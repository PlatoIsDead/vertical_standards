"""№10: доставка xlsx по команде «Отчёт» (TestClient + фейковый httpx)."""
import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.db as db
import app.roles as roles

QUESTIONS = {"course_summary": "s", "basic_questions": [], "exam_questions": []}


class FakeResponse:
    def __init__(self, json_data=None):
        self._json = json_data or {}
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._json

    def raise_for_status(self):
        pass


class FakeAsyncClient:
    routes: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        for suffix, handler in FakeAsyncClient.routes.items():
            if url.endswith(suffix):
                return FakeResponse(handler(json) if callable(handler) else handler)
        raise AssertionError(f"unexpected POST {url}")


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _post_hr(client, message):
    return client.post("/hr", data={
        "data[PARAMS][MESSAGE]": message,
        "data[PARAMS][DIALOG_ID]": "d9",
        "data[PARAMS][FROM_USER_ID]": "9",
    })


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("HR_USER_IDS", "9")
    db.init_db()
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({"roles": {}, "prefixes": {}, "folders": {}}),
                   encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))
    bot._recent_msgs.clear()

    cid = db.save_draft_course("Курс.docx", "1",
                               json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid, "hr")
    db.add_employee("600", "e@x.ru", "Ева", "9")
    db.create_session("600", "u600", cid)

    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        sent.append((dialog_id, text))

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.routes = {}
    return TestClient(bot.app), sent


def test_report_uploads_and_sends_link(env, monkeypatch):
    """Корутина напрямую (asyncio.run): фоновая задача с to_thread не
    доживает в пер-запросном лупе TestClient — прод-путь тот же.
    18.09: вместо вложения (im.disk.file.commit от владельца вебхука) —
    ссылка на файл и папку от HR-бота; старые отчёты не удаляются."""
    import asyncio

    _client, sent = env
    monkeypatch.setattr(bot, "REPORTS_FOLDER_ID", "777")
    monkeypatch.setattr(bot, "BITRIX_WEBHOOK_URL", "https://portal.test/rest/1/x/")
    bot._REPORT_FOLDER.clear()
    db.set_meta("department_names", json.dumps(
        {"ts": time.time(), "names": {"2439": "Ресепшн"}}))
    db.mark_user_seen("600", "[2439]")

    uploaded = {}
    FakeAsyncClient.routes = {
        "disk.folder.uploadfile": lambda p: uploaded.update(p) or {"result": {
            "ID": 555, "NAME": "Отчёт по обучению 2026-09-18.xlsx",
            "DETAIL_URL": "https://old.host/company/personal/user/1/disk/"
                          "file/Отчёты/Отчёт по обучению 2026-09-18.xlsx"}},
        "disk.folder.get": {"result": {
            "ID": 777, "NAME": "Отчёты",
            "DETAIL_URL": "https://old.host/company/personal/user/1/disk/path/Отчёты"}},
    }

    asyncio.run(bot._build_and_send_report("d9", "cid"))

    assert uploaded["id"] == "777"                         # приватная папка
    name, b64 = uploaded["fileContent"]
    assert name.startswith("Отчёт по обучению 20") and name.endswith(".xlsx")
    assert base64.b64decode(b64)[:2] == b"PK"              # валидный xlsx (zip)
    assert len(sent) == 1 and sent[0][0] == "d9"
    text = sent[0][1]
    assert "Отчёт сохранён" in text
    # хост подменён на домен портала, пробелы percent-кодированы
    assert ("[URL=https://portal.test/company/personal/user/1/disk/file/"
            "%D0%9E%D1%82%D1%87%D1%91%D1%82%D1%8B/") in text
    assert "]Отчёт по обучению 2026-09-18.xlsx[/URL]" in text
    assert "]Отчёты[/URL]" in text
    # в файле — имя отдела из кэша department_names
    from io import BytesIO

    from openpyxl import load_workbook
    ws = load_workbook(BytesIO(base64.b64decode(b64)))["Матрица"]
    rows_by_name = {ws.cell(i, 1).value: [ws.cell(i, j).value for j in (2, 3)]
                    for i in range(2, ws.max_row + 1)}
    assert rows_by_name["Ева"] == [None, "Ресепшн"]


def test_report_reply_promises_file(env, monkeypatch):
    client, sent = env
    monkeypatch.setattr(bot, "REPORTS_FOLDER_ID", "777")
    # Фоновая задача упрётся в пустой uploadfile-ответ → лог, не падение
    FakeAsyncClient.routes = {
        "department.get": {"error": "insufficient_scope"},
        "user.get": {"result": []},
        "disk.folder.uploadfile": {"result": {}},
    }
    _post_hr(client, "Отчёт")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "пришлю ссылку на Excel" in next(m[1] for m in sent if m[0] == "d9")


def test_report_without_folder_only_text(env, monkeypatch):
    client, sent = env
    monkeypatch.setattr(bot, "REPORTS_FOLDER_ID", "")
    _post_hr(client, "Отчёт")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    text = next(m[1] for m in sent if m[0] == "d9")
    assert "REPORTS_FOLDER_ID" in text                     # подсказка HR
    assert FakeAsyncClient.routes == {}                    # сеть не дёргалась
