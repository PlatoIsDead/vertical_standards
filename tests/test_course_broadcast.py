"""Демо-фидбек A: рассылка «доступен новый курс» при «Подтвердить N» —
TestClient /hr, без сети (паттерн test_hr_invite)."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.db as db
import app.roles as roles

QUESTIONS = {"course_summary": "s", "basic_questions": [], "exam_questions": []}


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


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("HR_USER_IDS", "9")   # ДО init_db: сид только «9»
    db.init_db()

    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"reservations": "Отдел бронирования",
                  "housekeeper": "Горничная / Уборщица",
                  "all_staff": "Все сотрудники"},
        "prefixes": {"RES": "reservations", "HSKP": "housekeeper",
                     "ALL": "all_staff"},
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))
    bot._recent_msgs.clear()

    # База: старый курс для сессий сотрудников (активируем напрямую — без рассылки)
    base_cid = db.save_draft_course("Старый.docx", "0",
                                    json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(base_cid, "hr9")

    # 600: свободен, роль reservations (из DONE-сессии)
    db.add_employee("600", "res@x.ru", "Роман Брони", "9")
    s = db.create_session("600", "u600", base_cid, state="DONE")
    db.update_session(s["id"], role="reservations")
    # 700: busy — активная сессия
    db.add_employee("700", "busy@x.ru", "Борис Занятой", "9")
    db.create_session("700", "u700", base_cid)          # READING
    # 800: в whitelist, ни одной сессии → роль неизвестна
    db.add_employee("800", "new@x.ru", "Новый Ноунейм", "9")

    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id=""):
        sent.append((dialog_id, text, bot_id))

    monkeypatch.setattr(bot, "_send", fake_send)
    return TestClient(bot.app), sent


def _draft(doc_name, doc_id):
    return db.save_draft_course(doc_name, doc_id,
                                json.dumps(QUESTIONS, ensure_ascii=False))


def test_role_course_broadcast_to_matching_free_employee(env):
    client, sent = env
    cid = _draft("RES Брони.docx", "10")
    _post_hr(client, f"Подтвердить {cid}")

    assert _wait_for(lambda: any(m[0] == "u600" for m in sent))
    msg = next(m for m in sent if m[0] == "u600")
    assert msg[2] == bot.BOT_ID                       # от EMPLOYEE-бота
    assert "доступен новый курс" in msg[1]
    assert "Брони" in msg[1] and "RES" not in msg[1]  # display_name без префикса

    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    hr_msg = next(m for m in sent if m[0] == "d9")
    assert "активирован" in hr_msg[1] and "1 чел" in hr_msg[1]

    # busy и без-роли — НЕ получили ролевой курс
    assert not any(m[0] in ("u700", "u800") for m in sent)


def test_all_course_reaches_unknown_role_but_not_busy(env):
    client, sent = env
    cid = _draft("ALL Конфиденциальность.docx", "11")
    _post_hr(client, f"Подтвердить {cid}")

    assert _wait_for(lambda: any(m[0] == "u800" for m in sent))
    assert any(m[0] == "u600" for m in sent)          # роль ∩ all_staff — тоже да
    assert not any(m[0] == "u700" for m in sent)      # busy — нет


def test_completed_course_not_rebroadcast_to_finisher(env):
    client, sent = env
    cid = _draft("RES Повтор.docx", "12")
    s = db.create_session("600", "u600", cid, state="DONE")   # 600 уже прошёл его
    db.update_session(s["id"], role="reservations")
    _post_hr(client, f"Подтвердить {cid}")

    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "Подходящих сотрудников" in next(m for m in sent if m[0] == "d9")[1]
    assert not any(m[0] == "u600" for m in sent)


def test_reconfirm_rebroadcasts(env):
    """Повторное «Подтвердить» — рассылка снова (осознанно: HR может
    переанонсировать курс; спам ограничен ручным действием HR)."""
    client, sent = env
    cid = _draft("RES Дубль.docx", "13")
    _post_hr(client, f"Подтвердить {cid}")
    assert _wait_for(lambda: any(m[0] == "u600" for m in sent))
    bot._recent_msgs.clear()

    _post_hr(client, f"Подтвердить {cid}", dialog="d9b")
    assert _wait_for(
        lambda: sum(1 for m in sent if m[0] == "u600") >= 2)
