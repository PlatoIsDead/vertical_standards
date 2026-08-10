"""№9 v2: HR-команды реестра руководителей (TestClient, без сети)."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.db as db
import app.roles as roles


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _post_hr(client, message, from_user="9", dialog="d9"):
    return client.post("/hr", data={
        "data[PARAMS][MESSAGE]": message,
        "data[PARAMS][DIALOG_ID]": dialog,
        "data[PARAMS][FROM_USER_ID]": from_user,
    })


def _last_reply(sent, dialog="d9"):
    return [m[1] for m in sent if m[0] == dialog][-1]


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
    bot._pending_edits.clear()

    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id=""):
        sent.append((dialog_id, text, bot_id))

    monkeypatch.setattr(bot, "_send", fake_send)
    return TestClient(bot.app), sent


def test_list_shows_seed(env):
    client, sent = env
    _post_hr(client, "Руководители")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "n.sharapov@proptech.digital" in _last_reply(sent)


def test_add_remove_and_senior(env):
    client, sent = env
    _post_hr(client, "Руководитель добавить boss@x.ru")
    assert _wait_for(lambda: any("boss@x.ru добавлен" in m[1] for m in sent))
    assert db.get_managers()[-1]["level"] == 1

    bot._recent_msgs.clear()
    _post_hr(client, "Руководитель добавить chief@x.ru старший")
    assert _wait_for(lambda: any("(старший)" in m[1] for m in sent))
    assert [m for m in db.get_managers() if m["email"] == "chief@x.ru"][0]["level"] == 2

    bot._recent_msgs.clear()
    _post_hr(client, "Руководители")
    assert _wait_for(lambda: "chief@x.ru (старший)" in _last_reply(sent))

    bot._recent_msgs.clear()
    _post_hr(client, "Руководитель добавить boss@x.ru")        # дубль
    assert _wait_for(lambda: any("уже в списке" in m[1] for m in sent))

    bot._recent_msgs.clear()
    _post_hr(client, "Руководитель удалить boss@x.ru")
    assert _wait_for(lambda: any("boss@x.ru удалён" in m[1] for m in sent))
    bot._recent_msgs.clear()
    _post_hr(client, "Руководитель удалить ghost@x.ru")
    assert _wait_for(lambda: any("не найден в списке" in m[1] for m in sent))


def test_bad_format(env):
    client, sent = env
    _post_hr(client, "Руководитель добавить")                  # без email
    assert _wait_for(lambda: any("❌ Формат" in m[1] for m in sent))


def test_non_hr_rejected(env):
    client, sent = env
    _post_hr(client, "Руководители", from_user="777", dialog="d777")
    assert _wait_for(lambda: any(m[0] == "d777" for m in sent))
    assert "только HR" in _last_reply(sent, "d777")


def test_pending_edit_not_hijacked(env):
    """Живая правка вопроса (№3) не съедает команду реестра —
    префикс в _HR_COMMAND_PREFIXES."""
    client, sent = env
    bot._pending_edits["9"] = {"course_id": 1, "q_num": 1,
                               "expires": time.monotonic() + 600}
    _post_hr(client, "Руководители")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "n.sharapov@proptech.digital" in _last_reply(sent)
    assert "9" not in bot._pending_edits                       # правка сброшена
