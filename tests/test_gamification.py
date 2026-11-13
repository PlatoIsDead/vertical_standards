"""Геймификация №5: расписание, тексты, выборка рейтинга, идемпотентность, хук."""
import json
from datetime import datetime, timedelta

import pytest

import app.db as db
import app.gamification as gm
import app.state_machine as sm

MON_10_01 = datetime(2026, 7, 13, 10, 1, tzinfo=gm.MSK)   # понедельник
MON_09_59 = datetime(2026, 7, 13, 9, 59, tzinfo=gm.MSK)
SUN_23_00 = datetime(2026, 7, 12, 23, 0, tzinfo=gm.MSK)
TUE_12_00 = datetime(2026, 7, 14, 12, 0, tzinfo=gm.MSK)


# ── Расписание ───────────────────────────────────────────────────────────────

def test_week_key():
    assert gm.week_key(MON_10_01) == "2026-W29"
    assert gm.week_key(datetime(2026, 1, 1, tzinfo=gm.MSK)) == "2026-W01"
    # стык лет: 1 января 2027 — пятница ISO-недели 2026-W53
    assert gm.week_key(datetime(2027, 1, 1, tzinfo=gm.MSK)) == "2026-W53"


def test_should_post_rating():
    assert gm.should_post_rating(MON_10_01, None) is True
    assert gm.should_post_rating(MON_09_59, None) is False
    # воскресенье W28: неделя уже публиковалась → False; НЕ публиковалась →
    # True (догон: сервер лежал всю неделю — рейтинг всё равно выйдет)
    assert gm.should_post_rating(SUN_23_00, "2026-W28") is False
    assert gm.should_post_rating(SUN_23_00, None) is True
    assert gm.should_post_rating(MON_10_01, "2026-W29") is False  # уже постили
    assert gm.should_post_rating(TUE_12_00, "2026-W28") is True   # догоняем пн
    assert gm.should_post_rating(TUE_12_00, "2026-W29") is False


# ── Тексты и выборка ─────────────────────────────────────────────────────────

def _row(uid, exam=8, basic=4, days_ago=0, state="DONE", name="Курс.docx"):
    questions = {"exam_questions": [{}] * 10, "basic_questions": [{}] * 5}
    return {"user_id": uid, "state": state, "score_exam": exam, "score_basic": basic,
            "doc_name": name, "questions_json": json.dumps(questions),
            "updated_at": (datetime.utcnow() - timedelta(days=days_ago)).isoformat()}


def test_build_congrats():
    title, message = gm.build_congrats("Иван Иванов", "Стандарты.docx", 8, 10, 5, 5)
    assert "Благодарность" in title
    assert "Иван Иванов" in message and "8/10" in message and "Стандарты.docx" in message


def test_rating_top5_sorted_with_medals():
    rows = [_row(str(i), exam=7 + (i % 3), basic=i % 5) for i in range(7)]
    emps = {"0": {"bitrix_uid": "0", "full_name": "Нулевой Топ"}}
    title, message = gm.build_weekly_rating(rows, emps, datetime.utcnow())
    assert title == gm.RATING_TITLE
    assert message.count("\n🥈") == 1 and "🥇" in message and "🥉" in message
    entries = [ln for ln in message.splitlines()
               if ln.startswith(("🥇", "🥈", "🥉", "•"))]
    assert len(entries) == 5
    assert "Сотрудник (ID" in message                 # без записи в employees
    # первый в топе — максимальная сумма баллов
    first = next(ln for ln in message.splitlines() if ln.startswith("🥇"))
    top = max(rows, key=lambda r: r["score_basic"] + r["score_exam"])
    assert f"{top['score_basic'] + top['score_exam']} баллов" in first


def test_rating_excludes_failed_old_and_non_done():
    rows = [
        _row("1", exam=6),                 # 6 < round(10*0.7)=7 — не сдал
        _row("2", days_ago=8),             # старше 7 дней
        _row("3", state="EXAM"),           # не завершил
    ]
    assert gm.build_weekly_rating(rows, {}, datetime.utcnow()) is None


def test_rating_dedupes_user_keeps_best():
    rows = [_row("5", exam=10, basic=5), _row("5", exam=7, basic=0)]
    _, message = gm.build_weekly_rating(rows, {}, datetime.utcnow())
    assert message.count("Сотрудник (ID 5)") == 1
    assert "15 баллов" in message                     # лучший результат


# ── Идемпотентность maybe_post_weekly_rating ─────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    # 02.09: тесты FSM ассертят конкретные буквы — порядок вариантов
    # фиксируем каноническим (перемешивание проверяет test_option_shuffle)
    monkeypatch.setenv("SHUFFLE_OPTIONS", "0")
    db.init_db()
    posted = []
    monkeypatch.setattr(gm, "_post_to_feed",
                        lambda title, msg: posted.append((title, msg)) or True)
    return posted


def _make_done_session(uid="500", exam=8):
    questions = {"exam_questions": [{}] * 10, "basic_questions": [{}] * 5}
    course_id = db.save_draft_course("Курс.docx", "1", json.dumps(questions))
    db.activate_course_by_id(course_id, "hr")
    session = db.create_session(uid, f"u{uid}", course_id, quiz_version=1)
    db.update_session(session["id"], state="DONE", score_basic=4, score_exam=exam)


def test_maybe_post_posts_once(env):
    posted = env
    db.add_employee("500", "ivan@x.ru", "Иван Иванов", "test")
    _make_done_session()

    assert gm.maybe_post_weekly_rating(MON_10_01) is True
    assert "Иван Иванов" in posted[0][1]
    assert db.get_meta(gm.RATING_META_KEY) == "2026-W29"
    # повторный вызов той же недели — идемпотентно
    assert gm.maybe_post_weekly_rating(MON_10_01) is False
    assert len(posted) == 1


def test_maybe_post_marks_empty_week(env):
    posted = env
    assert gm.maybe_post_weekly_rating(MON_10_01) is False  # сессий нет
    assert posted == []
    assert db.get_meta(gm.RATING_META_KEY) == "2026-W29"    # неделя помечена


def test_maybe_post_too_early(env):
    posted = env
    _make_done_session()
    assert gm.maybe_post_weekly_rating(MON_09_59) is False
    assert posted == [] and db.get_meta(gm.RATING_META_KEY) is None


# ── Хук в _finish_phase ──────────────────────────────────────────────────────

EXAM_QUESTIONS = {
    "doc_name": "Стандарты.docx",
    "course_summary": "s",
    "basic_questions": [],
    "exam_questions": [
        {"text": "Вопрос?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
         "correct": "A"},
    ],
}


@pytest.fixture
def exam_env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SHUFFLE_OPTIONS", "0")        # тест ассертит букву «A»
    monkeypatch.delenv("HR_USER_IDS", raising=False)  # notify_hr молчит офлайн
    db.init_db()
    db.add_employee("u1", "ivan@x.ru", "Иван Иванов", "test")
    course_id = db.save_draft_course(
        "Стандарты.docx", "1", json.dumps(EXAM_QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(course_id, "hr")
    session = db.create_session("u1", "d1", course_id, quiz_version=1)
    db.update_session(session["id"], state="EXAM", q_idx=0)

    calls = []
    monkeypatch.setattr(sm, "post_exam_congratulation",
                        lambda uid, q, exam, basic: calls.append((uid, exam, basic)))
    return calls


def test_passed_exam_posts_congrats(exam_env):
    reply = sm.process_message("u1", "A", "d1", [], None)  # 1/1 — сдан (round(0.7)=1)
    assert "Сдан" in reply
    assert exam_env == [("u1", 1, 0)]


def test_failed_exam_no_congrats(exam_env):
    reply = sm.process_message("u1", "B", "d1", [], None)  # 0/1 — не сдан
    assert "Не сдан" in reply
    assert exam_env == []


# ── Штатная благодарность (адресно, с медалью) ───────────────────────────────

class _Resp:
    def __init__(self, body, status=200):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


@pytest.fixture
def grat_http(monkeypatch):
    monkeypatch.setenv("GRATITUDE_ENABLED", "1")
    monkeypatch.setenv("GRATITUDE_MEDAL", "cup")
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal/rest/1/x/")
    calls, state = [], {"value": 2349373}

    def fake_post(url, json=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        calls.append((method, json))
        if method == "log.blogpost.add":
            return _Resp({"result": 6888})
        if method == "log.blogpost.get":
            return _Resp({"result": [{"UF_GRATITUDE": {"VALUE": state["value"]}}]})
        return _Resp({"result": True})

    monkeypatch.setattr(gm.httpx, "post", fake_post)
    return calls, state


def test_gratitude_payload_addressed_only_to_employee():
    payload = gm.build_gratitude_payload(36523, "T", "M", "cup")
    assert payload["DEST"] == ["U36523"]           # не "UA" — Лента чистая
    assert payload["GRATITUDE_EMPLOYEES"] == [36523]
    assert payload["GRATITUDE_MEDAL"] == "cup"
    assert "UF_GRATITUDE" not in payload           # внутри UF_ Битрикс игнорирует


def test_gratitude_created(grat_http):
    calls, _ = grat_http
    assert gm._post_gratitude("36523", "T", "M") is True
    assert [m for m, _ in calls] == ["log.blogpost.add", "log.blogpost.get"]
    assert calls[0][1]["DEST"] == ["U36523"]


def test_gratitude_not_created_post_deleted(grat_http):
    calls, state = grat_http
    state["value"] = False                          # неверный код медали
    assert gm._post_gratitude("36523", "T", "M") is False
    assert calls[-1] == ("log.blogpost.delete", {"POST_ID": 6888})


def test_gratitude_disabled_by_default(grat_http, monkeypatch):
    calls, _ = grat_http
    monkeypatch.delenv("GRATITUDE_ENABLED")
    assert gm._post_gratitude("36523", "T", "M") is False
    assert calls == []


def test_gratitude_non_numeric_uid_skipped(grat_http):
    calls, _ = grat_http
    assert gm._post_gratitude("chk_u1", "T", "M") is False
    assert calls == []
