"""№9 v2: ежедневные напоминания и двухступенчатая эскалация (лупы, без сети)."""
import asyncio
import json
from datetime import datetime, timedelta

import pytest

import app.bitrix_bot as bot
import app.db as db
import app.roles as roles

NOW_10MSK = datetime(2026, 8, 20, 7, 0, 0)     # 10:00 МСК
NOW_8MSK = datetime(2026, 8, 20, 5, 0, 0)      # 08:00 МСК (до часа напоминаний)
OLD = (NOW_10MSK - timedelta(days=10)).isoformat()
VERY_OLD = (NOW_10MSK - timedelta(days=20)).isoformat()

QUESTIONS = json.dumps({"course_summary": "s", "basic_questions": [],
                        "exam_questions": [
    {"id": i, "text": "?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
     "correct": "A"} for i in range(10)]}, ensure_ascii=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"all_staff": "Все"}, "prefixes": {"ALL": "all_staff"},
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))

    db.add_employee("600", "e@x.ru", "Ева Тест", "9")
    cid = db.save_draft_course("Общий.docx", "1", QUESTIONS)
    db.activate_course_by_id(cid, "hr")
    # Состарить все базы дедлайна (курс, сотрудник, baseline)
    with db._conn() as conn:
        conn.execute("UPDATE courses SET approved_at = ?", (VERY_OLD,))
        conn.execute("UPDATE employees SET added_at = ?", (VERY_OLD,))
    db.set_meta("deadlines_baseline", VERY_OLD)

    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id=""):
        sent.append((dialog_id, text, bot_id))

    monkeypatch.setattr(bot, "_send", fake_send)
    return sent, cid


def test_reminder_daily_idempotent(env):
    sent, _ = env
    asyncio.run(bot._maybe_send_reminders(NOW_10MSK))
    reminders = [m for m in sent if m[0] == "u600"]
    assert len(reminders) == 1
    assert "Твои курсы и сроки" in reminders[0][1]
    assert "просрочен" in reminders[0][1]
    assert reminders[0][2] == bot.BOT_ID                    # от employee-бота

    asyncio.run(bot._maybe_send_reminders(NOW_10MSK))       # тот же день
    assert len([m for m in sent if m[0] == "u600"]) == 1

    next_day = NOW_10MSK + timedelta(days=1)
    asyncio.run(bot._maybe_send_reminders(next_day))
    assert len([m for m in sent if m[0] == "u600"]) == 2


def test_reminder_waits_for_hour(env):
    sent, _ = env
    asyncio.run(bot._maybe_send_reminders(NOW_8MSK))
    assert sent == []
    assert db.get_meta("reminder_last_date") is None


def test_reminder_skips_passed(env):
    sent, cid = env
    s = db.create_session("600", "u600", cid, state="DONE")
    db.update_session(s["id"], score_exam=9)                # сдан
    asyncio.run(bot._maybe_send_reminders(NOW_10MSK))
    assert sent == []                                       # напоминать нечего


def test_escalation_two_stages_once(env, monkeypatch):
    sent, cid = env

    async def fake_lookup(email):
        return {"ID": "77"}                                 # руководитель найден

    monkeypatch.setattr(bot, "_bitrix_user_by_email", fake_lookup)

    asyncio.run(bot._check_escalations(NOW_10MSK))
    boss_msgs = [m for m in sent if m[0] == "u77"]
    # 20 дней просрочки → обе ступени сразу; level-2 пуст → фолбэк на level-1
    assert len(boss_msgs) == 2
    assert any("ступень 1" in m[1] for m in boss_msgs)
    assert any("ступень 2" in m[1] for m in boss_msgs)
    assert all(m[2] == bot.HR_BOT_ID for m in boss_msgs)
    assert "не начинал обучение" in boss_msgs[0][1]

    asyncio.run(bot._check_escalations(NOW_10MSK))          # повтор — тишина
    assert len([m for m in sent if m[0] == "u77"]) == 2


def test_escalation_transient_failure_not_marked(env, monkeypatch):
    sent, _ = env

    async def broken_lookup(email):
        raise bot.httpx.ConnectError("флап")

    monkeypatch.setattr(bot, "_bitrix_user_by_email", broken_lookup)
    with pytest.raises(bot.httpx.ConnectError):
        asyncio.run(bot._check_escalations(NOW_10MSK))
    assert db.get_escalated(1) == set()                     # НЕ помечено — доставит позже
