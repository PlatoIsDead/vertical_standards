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


def test_report_uploads_and_commits(env, monkeypatch):
    """Корутина напрямую (asyncio.run): фоновая задача с to_thread не
    доживает в пер-запросном лупе TestClient — прод-путь тот же."""
    import asyncio

    _client, _sent = env
    monkeypatch.setattr(bot, "REPORTS_FOLDER_ID", "777")
    db.set_meta("last_report_file_id", "111")

    deleted, uploaded, committed = [], {}, []
    FakeAsyncClient.routes = {
        "disk.file.markdeleted": lambda p: deleted.append(p["id"]) or {"result": True},
        "disk.folder.uploadfile": lambda p: uploaded.update(p) or {"result": {"ID": 555}},
        "im.dialog.get": {"result": {"chat_id": 5}},
        "im.disk.file.commit": lambda p: committed.append(p) or {"result": True},
    }

    asyncio.run(bot._build_and_send_report("d9"))

    assert deleted == ["111"]                              # старый файл убран
    assert uploaded["id"] == "777"                         # приватная папка
    name, b64 = uploaded["fileContent"]
    assert name == "Отчёт по обучению.xlsx"
    assert base64.b64decode(b64)[:2] == b"PK"              # валидный xlsx (zip)
    assert db.get_meta("last_report_file_id") == "555"
    assert committed and committed[0]["CHAT_ID"] == 5


def test_report_reply_promises_file(env, monkeypatch):
    client, sent = env
    monkeypatch.setattr(bot, "REPORTS_FOLDER_ID", "777")
    # Фоновая задача упрётся в пустой uploadfile-ответ → лог, не падение
    FakeAsyncClient.routes = {
        "disk.file.markdeleted": {"result": True},
        "disk.folder.uploadfile": {"result": {}},
    }
    _post_hr(client, "Отчёт")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "пришлю файлом" in next(m[1] for m in sent if m[0] == "d9")


def test_report_without_folder_only_text(env, monkeypatch):
    client, sent = env
    monkeypatch.setattr(bot, "REPORTS_FOLDER_ID", "")
    _post_hr(client, "Отчёт")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    text = next(m[1] for m in sent if m[0] == "d9")
    assert "REPORTS_FOLDER_ID" in text                     # подсказка HR
    assert FakeAsyncClient.routes == {}                    # сеть не дёргалась
