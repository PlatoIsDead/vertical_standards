"""Проводка kb_tracker: _send, нажатие кнопки (/command), notify_hr.

Сеть — только фейки; BITRIX_WEBHOOK_URL подменяется, иначе снятие кнопок
по дизайну молчит (conftest обнуляет боевые ключи).
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.kb_tracker as kt
import app.state_machine as sm


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.text = ""
        self._payload = payload

    def json(self):
        return self._payload


class NoJsonResponse:
    """Как двойник в test_buttons_everywhere: у ответа нет json() вовсе."""
    status_code = 200
    text = ""


class Portal:
    """Фейковый портал: add выдаёт растущие id, update отвечает true."""

    def __init__(self, add_status=200, has_json=True):
        self.posts: list[tuple[str, dict]] = []
        self.next_id = 1000
        self.add_status = add_status
        self.has_json = has_json

    def handle(self, url, payload):
        self.posts.append((url, payload))
        if url.endswith("imbot.message.add"):
            self.next_id += 1
            if not self.has_json:
                return NoJsonResponse()
            return FakeResponse(self.add_status, {"result": self.next_id})
        return FakeResponse(200, {"result": True})

    def updates(self):
        return [p for u, p in self.posts if u.endswith("imbot.message.update")]


@pytest.fixture
def portal(monkeypatch):
    kt.clear()
    p = Portal()

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return p.handle(url, json)

    monkeypatch.setattr(bot.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(bot, "BITRIX_WEBHOOK_URL", "https://portal/rest/1/x/")
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", False)   # без похода в БД
    yield p
    kt.clear()


async def _drain():
    """Дать отработать fire-and-forget задачам снятия кнопок."""
    for _ in range(5):
        pending = [t for t in asyncio.all_tasks()
                   if t is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


KB = [{"TEXT": "A", "COMMAND": "say", "COMMAND_PARAMS": "A"}]


def test_new_keyboard_message_strips_previous(portal):
    async def run():
        await bot._send("28528", "вопрос 1", "54849", "cid", keyboard=KB)
        await bot._send("28528", "вопрос 2", "54849", "cid", keyboard=KB)
        await _drain()

    asyncio.run(run())
    assert portal.updates() == [{"BOT_ID": "54849", "MESSAGE_ID": 1001,
                                 "KEYBOARD": "N", "IS_EDITED": "N",
                                 "CLIENT_ID": "cid"}]
    assert "MESSAGE" not in portal.updates()[0]     # пустой MESSAGE удалил бы
    assert kt.latest("54849", "28528") == 1002


def test_message_without_keyboard_not_tracked(portal):
    async def run():
        await bot._send("28528", "с кнопками", "54849", "cid", keyboard=KB)
        await bot._send("28528", "без кнопок", "54849", "cid")
        await _drain()

    asyncio.run(run())
    assert portal.updates() == []                   # кнопки остались у первого
    assert kt.latest("54849", "28528") == 1001


def test_bots_do_not_strip_each_other(portal):
    async def run():
        await bot._send("28528", "HR", bot.HR_BOT_ID, "cid", keyboard=KB)
        await bot._send("28528", "сотрудник", "54849-emp", "cid", keyboard=KB)
        await _drain()

    asyncio.run(run())
    assert portal.updates() == []


def test_failed_send_not_tracked(portal):
    portal.add_status = 500

    async def run():
        await bot._send("28528", "раз", "54849", "cid", keyboard=KB)
        await bot._send("28528", "два", "54849", "cid", keyboard=KB)
        await _drain()

    asyncio.run(run())
    assert portal.updates() == []
    assert kt.latest("54849", "28528") is None


def test_unparseable_response_still_delivers(portal):
    """Ответ без json() — сообщение ушло, трекинг просто пропущен."""
    portal.has_json = False

    async def run():
        await bot._send("28528", "раз", "54849", "cid", keyboard=KB)
        await bot._send("28528", "два", "54849", "cid", keyboard=KB)
        await _drain()

    asyncio.run(run())
    adds = [u for u, _ in portal.posts if u.endswith("imbot.message.add")]
    assert len(adds) == 2
    assert portal.updates() == []


def test_strip_is_silent_without_webhook(portal, monkeypatch):
    monkeypatch.setattr(bot, "BITRIX_WEBHOOK_URL", "")
    asyncio.run(bot._strip_keyboard("54849", 1001, "cid"))
    assert portal.posts == []


def test_strip_network_error_swallowed(portal, monkeypatch):
    """Флап сети при снятии кнопок не роняет обработку — только лог."""
    calls = []

    class Boom:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            calls.append(url)
            raise bot.httpx.ConnectTimeout("flap")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(bot.httpx, "AsyncClient", Boom)
    monkeypatch.setattr(bot.asyncio, "sleep", no_sleep)
    asyncio.run(bot._strip_keyboard("54849", 1001, "cid"))
    assert len(calls) == 3


# ── Нажатие кнопки ───────────────────────────────────────────────────────────

def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def press(portal, monkeypatch):
    async def fake_emp(*a, **kw):
        pass

    async def fake_hr(*a, **kw):
        pass

    monkeypatch.setattr(bot, "_handle_employee_message", fake_emp)
    monkeypatch.setattr(bot, "_handle_hr_message", fake_hr)
    client = TestClient(bot.app)

    def do(message_id, bot_id="54858", command="hrsay"):
        return client.post("/command", data={
            "event": "ONIMCOMMANDADD",
            "data[COMMAND][71][BOT_ID]": bot_id,
            "data[COMMAND][71][COMMAND]": command,
            "data[COMMAND][71][COMMAND_PARAMS]": "Курсы",
            "data[COMMAND][71][MESSAGE_ID]": str(message_id),
            "data[PARAMS][DIALOG_ID]": "28528",
            "data[USER][ID]": "28528",
            "auth[application_token]": "tok",
        })

    return do


def test_press_on_stale_message_strips_it(portal, press):
    kt.register("54858", "28528", 5000)          # актуальное сообщение
    press(4990)                                  # нажали старую кнопку
    assert _wait_for(lambda: portal.updates())
    assert portal.updates()[0]["MESSAGE_ID"] == 4990
    assert kt.latest("54858", "28528") == 5000


def test_press_on_latest_message_is_noop(portal, press):
    kt.register("54858", "28528", 5000)
    press(5000)
    time.sleep(0.1)
    assert portal.updates() == []


def test_press_after_restart_seeds_registry(portal, press):
    """Реестр пуст (рестарт): нажатое сообщение запоминается, ответ с новыми
    кнопками снимет их у него обычным правилом."""
    press(5000)
    time.sleep(0.1)
    assert portal.updates() == []
    assert kt.latest("54858", "28528") == 5000


def test_press_without_message_id_ignored(portal, monkeypatch):
    async def fake_emp(*a, **kw):
        pass

    monkeypatch.setattr(bot, "_handle_employee_message", fake_emp)
    TestClient(bot.app).post("/command", data={
        "event": "ONIMCOMMANDADD",
        "data[PARAMS][COMMAND_PARAMS]": "A",
        "data[PARAMS][DIALOG_ID]": "d2",
        "data[USER][ID]": "u2",
    })
    assert kt.latest(bot.BOT_ID, "d2") is None


# ── notify_hr (синхронный путь мимо _send) ────────────────────────────────────

def test_notify_hr_strips_previous_hr_keyboard(monkeypatch):
    kt.clear()
    posts = []
    ids = iter(range(7001, 7100))

    def fake_post(url, json=None, timeout=None):
        posts.append((url, json))
        if url.endswith("imbot.message.add"):
            return FakeResponse(200, {"result": next(ids)})
        return FakeResponse(200, {"result": True})

    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal/rest/1/x/")
    monkeypatch.setenv("HR_USER_IDS", "28528")
    monkeypatch.setenv("HR_BOT_ID", "54858")
    monkeypatch.setenv("HR_CLIENT_ID", "hrcid")
    monkeypatch.setenv("BUTTONS_ENABLED", "1")
    monkeypatch.setattr(sm.httpx, "post", fake_post)

    sm.notify_hr("первое")
    sm.notify_hr("второе")

    updates = [p for u, p in posts if u.endswith("imbot.message.update")]
    assert updates == [{"BOT_ID": "54858", "MESSAGE_ID": 7001, "KEYBOARD": "N",
                        "IS_EDITED": "N", "CLIENT_ID": "hrcid"}]
    assert kt.latest("54858", "28528") == 7002
    kt.clear()


def test_notify_hr_without_buttons_not_tracked(monkeypatch):
    kt.clear()
    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append((url, json))
        return FakeResponse(200, {"result": 7001})

    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal/rest/1/x/")
    monkeypatch.setenv("HR_USER_IDS", "28528")
    monkeypatch.setenv("HR_BOT_ID", "54858")
    monkeypatch.setenv("BUTTONS_ENABLED", "0")
    monkeypatch.setattr(sm.httpx, "post", fake_post)

    sm.notify_hr("первое")
    sm.notify_hr("второе")
    assert all(u.endswith("imbot.message.add") for u, _ in posts)
    assert kt.latest("54858", "28528") is None
