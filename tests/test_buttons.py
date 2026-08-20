"""№7: инлайн-кнопки за флагом BUTTONS_ENABLED — чистые клавиатуры,
/command-эндпоинт, наличие/отсутствие KEYBOARD в payload."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.keyboards as kb


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ── Чистые клавиатуры ────────────────────────────────────────────────────────

def _btns(keyboard):
    return [b for b in keyboard if "TEXT" in b]


def test_for_session_states():
    """Дизайн 17.08: TEXT — витрина, COMMAND_PARAMS — прежние команды FSM."""
    assert kb.for_session(None) is None
    fork = _btns(kb.for_session(None, fork=True))
    assert [(b["TEXT"], b["COMMAND_PARAMS"]) for b in fork] == \
        [("🔁 Пересдать экзамен", "Пересдать"), ("Далее, к следующему", "Далее")]
    roles = _btns(kb.for_session({"state": "ROLE_SELECT"},
                                 role_options=[("a", "А"), ("b", "Б"),
                                               ("c", "В")]))
    assert [b["COMMAND_PARAMS"] for b in roles] == ["1", "2", "3"]
    reading = _btns(kb.for_session({"state": "READING"}))
    assert [(b["TEXT"], b["COMMAND_PARAMS"]) for b in reading] == \
        [("✅ Готов к тесту", "Готов"), ("📚 Мои курсы", "Мои курсы"),
         ("📄 Документы", "Документы"), ("Роль", "Роль")]
    assert reading[0]["DISPLAY"] == "BLOCK"
    test_kb = _btns(kb.for_session({"state": "BASIC_TEST"}))
    assert [b["COMMAND_PARAMS"] for b in test_kb] == \
        ["A", "B", "C", "D", "Выйти"]        # 19.08: пауза теста
    assert kb.for_session({"state": "EXAM"}) == kb.for_session(
        {"state": "BASIC_TEST"})
    waiting = _btns(kb.for_session({"state": "WAITING_HR"}))
    assert [(b["TEXT"], b["COMMAND_PARAMS"]) for b in waiting] == \
        [("📚 Мои курсы", "Мои курсы"), ("📄 Документы", "Документы")]
    # Нажатие шлёт generic-команду say с ПРЕЖНИМ payload
    assert all(b["COMMAND"] == "say" for b in reading)


# ── KEYBOARD в payload _send ─────────────────────────────────────────────────

class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return {}


class FakeAsyncClient:
    captured: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        FakeAsyncClient.captured.append(json)
        return FakeResponse(json)


def test_send_keyboard_key_only_when_passed(monkeypatch):
    FakeAsyncClient.captured = []
    monkeypatch.setattr(bot.httpx, "AsyncClient", FakeAsyncClient)
    asyncio.run(bot._send("u1", "текст", "42", "cid"))
    assert "KEYBOARD" not in FakeAsyncClient.captured[0]    # ключа нет вовсе
    asyncio.run(bot._send("u1", "текст2", "42", "cid",
                          keyboard=[{"TEXT": "A"}]))
    assert FakeAsyncClient.captured[1]["KEYBOARD"] == [{"TEXT": "A"}]


def test_keyboard_attached_when_flag_on(monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    monkeypatch.setattr(bot, "get_session",
                        lambda uid: {"course_id": 5, "state": "BASIC_TEST"})
    monkeypatch.setattr(bot, "get_course_questions", lambda cid: {})
    monkeypatch.setattr(bot, "process_message", lambda *a, **kw: "ok")
    captured = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        captured.append(keyboard)

    monkeypatch.setattr(bot, "_send", fake_send)
    bot._recent_msgs.clear()

    async def run():
        await bot._handle_employee_message("u9", "A", "d1", "")
        await asyncio.sleep(0)

    asyncio.run(run())
    assert captured and \
        [b["COMMAND_PARAMS"] for b in captured[0] if "TEXT" in b] == \
        ["A", "B", "C", "D", "Выйти"]


# ── /command ─────────────────────────────────────────────────────────────────

@pytest.fixture
def cmd_env(monkeypatch):
    replies = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        replies.append((dialog_id, text))

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "process_message", lambda *a, **kw: "ОТВЕТ")
    monkeypatch.setattr(bot, "get_session",
                        lambda uid: {"course_id": 5, "state": "READING"})
    bot._recent_msgs.clear()
    return TestClient(bot.app), replies


def test_command_format_variant_command(cmd_env):
    client, replies = cmd_env
    client.post("/command", data={
        "event": "ONIMCOMMANDADD",
        "data[COMMAND][0][COMMAND_PARAMS]": "Готов",
        "data[COMMAND][0][DIALOG_ID]": "d1",
        "data[COMMAND][0][USER_ID]": "u9",
    })
    assert _wait_for(lambda: ("d1", "ОТВЕТ") in replies)


def test_command_format_variant_params(cmd_env):
    client, replies = cmd_env
    client.post("/command", data={
        "event": "ONIMCOMMANDADD",
        "data[PARAMS][COMMAND_PARAMS]": "A",
        "data[PARAMS][DIALOG_ID]": "d2",
        "data[USER][ID]": "u9",
    })
    assert _wait_for(lambda: ("d2", "ОТВЕТ") in replies)


def test_command_double_click_deduped(cmd_env):
    client, replies = cmd_env
    fields = {
        "event": "ONIMCOMMANDADD",
        "data[COMMAND][0][COMMAND_PARAMS]": "B",
        "data[COMMAND][0][DIALOG_ID]": "d3",
        "data[COMMAND][0][USER_ID]": "u9",
    }
    client.post("/command", data=fields)
    client.post("/command", data=fields)                    # двойной клик
    assert _wait_for(lambda: ("d3", "ОТВЕТ") in replies)
    time.sleep(0.05)
    assert len([r for r in replies if r[0] == "d3"]) == 1


def test_command_other_event_ignored(cmd_env):
    client, replies = cmd_env
    r = client.post("/command", data={"event": "ONIMBOTMESSAGEADD",
                                      "data[PARAMS][MESSAGE]": "x"})
    assert r.json()["status"] == "ignored"
    assert replies == []
