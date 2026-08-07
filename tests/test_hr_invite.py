"""hr_handler: «Пригласить», HR-гейт, «Допустить {email}» — TestClient, без сети.

ВАЖНО: import app.bitrix_bot исполняет init_db() и load_index() на живых файлах
(идемпотентно; живую БД тесты НЕ мутируют — DB_PATH подменяется до любых записей).
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.db as db
import app.roles as roles

QUESTIONS = {"course_summary": "О курсе.", "basic_questions": [], "exam_questions": []}


def _wait_for(predicate, timeout=2.0):
    """Ответы hr_handler уходят через asyncio.create_task — дожидаемся тика лупа."""
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


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()

    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"housekeeper": "Горничная / Уборщица", "all_staff": "Все сотрудники"},
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))

    course_id = db.save_draft_course(
        "Стандарты.docx", "1", json.dumps(QUESTIONS, ensure_ascii=False)
    )
    db.activate_course_by_id(course_id, "hr9")

    monkeypatch.setenv("HR_USER_IDS", "9")
    bot._recent_msgs.clear()  # дедуп _is_duplicate переживает тесты — чистим

    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id=""):
        sent.append((dialog_id, text, bot_id))

    monkeypatch.setattr(bot, "_send", fake_send)

    async def fake_lookup(email):
        if email == "ivan@x.ru":
            return {"ID": "500", "NAME": "Иван", "LAST_NAME": "Иванов",
                    "EMAIL": "ivan@x.ru"}
        return None

    monkeypatch.setattr(bot, "_bitrix_user_by_email", fake_lookup)
    return TestClient(bot.app), sent, course_id


def test_invite_flow(env):
    client, sent, _ = env
    r = _post_hr(client, "Пригласить ivan@x.ru")
    assert r.status_code == 200

    assert db.is_employee_allowed("500")
    emp = db.get_employee_by_email("ivan@x.ru")
    assert emp["full_name"] == "Иван Иванов" and emp["added_by"] == "9"

    session = db.get_session("500")
    assert session and session["state"] == "ROLE_SELECT"
    assert session["dialog_id"] == "u500"

    # Первое сообщение сотруднику — от EMPLOYEE-бота (не HR-бота)
    assert _wait_for(lambda: any(m[0] == "u500" for m in sent))
    emp_msg = next(m for m in sent if m[0] == "u500")
    assert emp_msg[2] == bot.BOT_ID
    assert "Горничная" in emp_msg[1]

    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    hr_msg = next(m for m in sent if m[0] == "d9")
    assert "приглашён" in hr_msg[1]


def test_invite_unknown_email(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить ghost@x.ru")

    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "не найден" in next(m for m in sent if m[0] == "d9")[1]
    assert db.get_employee_by_email("ghost@x.ru") is None
    assert db.get_session("ghost") is None


def test_invite_requires_email(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "Укажи email" in next(m for m in sent if m[0] == "d9")[1]


def test_invite_already_learning(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: db.get_session("500") is not None)
    bot._recent_msgs.clear()

    _post_hr(client, "Пригласить ivan@x.ru", dialog="d9b")
    assert _wait_for(lambda: any(m[0] == "d9b" for m in sent))
    assert "уже проходит обучение" in next(m for m in sent if m[0] == "d9b")[1]


def test_invite_role_select_defers_course_file(env, monkeypatch):
    """№11: при старте с выбором роли курс ещё не назначен (course_id=0) —
    файл документа НЕ шлётся, уйдёт после выбора роли через bot_handler."""
    client, sent, _ = env
    called = []

    async def fake_course_file(dialog_id, course_id):
        called.append((dialog_id, course_id))

    monkeypatch.setattr(bot, "_send_course_file", fake_course_file)
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: db.get_session("500") is not None)
    assert db.get_session("500")["course_id"] == 0
    time.sleep(0.05)
    assert not called


def test_invite_remembered_role_sends_course_file(env, monkeypatch):
    """№11: роль известна из прошлой сессии → курс назначен сразу, файл уходит."""
    client, sent, course_id = env
    cid2 = db.save_draft_course(
        "Правила.docx", "2", json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid2, "hr9")
    s = db.create_session("500", "u500", course_id, state="DONE")
    db.update_session(s["id"], role="housekeeper")
    called = []

    async def fake_course_file(dialog_id, course_id):
        called.append((dialog_id, course_id))

    monkeypatch.setattr(bot, "_send_course_file", fake_course_file)
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bool(called))
    session = db.get_session("500")
    assert session["state"] == "READING" and session["role"] == "housekeeper"
    assert called[0] == ("u500", session["course_id"])


def test_non_hr_rejected(env):
    client, sent, _ = env
    _post_hr(client, "Курсы", from_user="777", dialog="d777")
    assert _wait_for(lambda: any(m[0] == "d777" for m in sent))
    assert "только HR" in next(m for m in sent if m[0] == "d777")[1]


def test_allow_by_email(env):
    client, sent, course_id = env
    db.add_employee("600", "petr@x.ru", "Пётр Петров", "9")
    db.create_session("600", "u600", course_id)  # READING

    _post_hr(client, "Допустить petr@x.ru")
    assert _wait_for(lambda: db.get_session("600")["state"] == "EXAM")

    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "допущен к экзамену" in next(m for m in sent if m[0] == "d9")[1]


def test_allow_by_unknown_email(env):
    client, sent, _ = env
    _post_hr(client, "Допустить nobody@x.ru")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "не найден среди приглашённых" in next(m for m in sent if m[0] == "d9")[1]
