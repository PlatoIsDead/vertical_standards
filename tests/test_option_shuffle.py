"""02.09: у каждого сотрудника свой порядок вариантов A–D.

Живой повод: последовательность правильных букв была одна на курс, и её
достаточно было продиктовать коллеге («A, B, B, D, B»). Здесь шафл ВКЛЮЧЁН
(в FSM-тестах он глушится SHUFFLE_OPTIONS=0 — там ассертятся конкретные буквы).
"""
import json

import pytest

pytestmark = pytest.mark.usefixtures("legacy_sessions")

import app.db as db
import app.quiz as quiz
import app.roles as roles
import app.state_machine as sm

QUESTIONS = {
    "course_summary": "s",
    "basic_questions": [
        {"id": i, "text": f"Б{i}?",
         "options": [f"A. а{i}", f"B. б{i}", f"C. в{i}", f"D. г{i}"],
         "correct": "A", "explanation": ""} for i in range(5)
    ],
    "exam_questions": [
        {"id": i, "text": f"Э{i}?",
         "options": [f"A. а{i}", f"B. б{i}", f"C. в{i}", f"D. г{i}"],
         "correct": "A", "explanation": ""} for i in range(3)
    ],
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("SHUFFLE_OPTIONS", "1")
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"housekeeper": "Горничная", "all_staff": "Все"},
        "prefixes": {}, "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))
    cid = db.save_draft_course("Стандарты.docx", "1",
                               json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid, "hr")
    monkeypatch.setattr(sm, "rag_answer", lambda **kw: ("MOCK", []))
    monkeypatch.setattr(sm, "notify_hr", lambda *a, **kw: None)
    for uid in ("u1", "u2"):
        db.add_employee(uid, added_by="t")
        db.set_employee_role(uid, "housekeeper")
    return cid


def _start_basic(uid: str) -> dict:
    sm.process_message(uid, "привет", f"d{uid}", [], None)
    sm.process_message(uid, "Готов", f"d{uid}", [], None)
    return db.get_session(uid)


def _display_correct(session: dict, phase: str, idx: int) -> str:
    """Буква правильного ответа в координатах ЭТОГО сотрудника."""
    q = db.get_course_questions(session["course_id"])[f"{phase}_questions"][idx]
    return quiz.question_view(session, q, phase, idx).correct_display


def test_two_employees_get_different_letter_sequences(env):
    s1, s2 = _start_basic("u1"), _start_basic("u2")
    seq1 = [_display_correct(s1, "basic", i) for i in range(5)]
    seq2 = [_display_correct(s2, "basic", i) for i in range(5)]
    assert seq1 != seq2, "передать буквы коллеге по-прежнему можно"


def test_text_and_buttons_agree(env):
    """Текст вопроса и тела кнопок строит одна функция — иначе кнопка «B»
    пошлёт букву не того варианта, который сотрудник прочитал."""
    import asyncio

    import app.bitrix_bot as bot
    session = _start_basic("u1")
    q = db.get_course_questions(session["course_id"])["basic_questions"][0]
    text = sm.format_question(q, 0, 5, "basic", session)
    bodies = asyncio.run(bot._test_options(session))
    for letter, body in zip(quiz.LETTERS, bodies):
        assert f"{letter}. {body}" in text


def test_answer_logged_canonically_and_scored_right(env):
    """Сотрудник жмёт СВОЮ букву; в answers уходит каноническая — иначе
    «История» HR показала бы «ответ A ✗ (верно: A)»."""
    session = _start_basic("u1")
    display = _display_correct(session, "basic", 0)
    reply = sm.process_message("u1", display, "du1", [], None)
    assert "✅ Верно!" in reply
    answers = db.get_session_answers(session["id"], "basic")
    assert answers[0]["user_answer"] == "A"        # канон, не display
    assert answers[0]["is_correct"] == 1


def test_wrong_answer_feedback_uses_employee_letter(env):
    session = _start_basic("u1")
    display = _display_correct(session, "basic", 0)
    wrong = next(x for x in quiz.LETTERS if x != display)
    reply = sm.process_message("u1", wrong, "du1", [], None)
    assert f"Правильный ответ: *{display}." in reply     # его буква + тело


def test_order_survives_pause_and_resume(env):
    session = _start_basic("u1")
    before = sm.format_question(
        db.get_course_questions(session["course_id"])["basic_questions"][0],
        0, 5, "basic", session)
    sm.process_message("u1", "выйти", "du1", [], None)
    resumed = sm.process_message("u1", "Продолжить", "du1", [], None)
    for line in before.splitlines():
        if line.startswith(("A.", "B.", "C.", "D.")):
            assert line in resumed


def test_retake_reshuffles(env):
    """После провала сотрудник видел «Правильный ответ: X» по каждому вопросу —
    с прежним порядком пересдача сдавалась бы по записанным буквам."""
    _start_basic("u1")
    for i in range(5):                                  # сдать базовый
        sm.process_message("u1", _display_correct(db.get_session("u1"),
                                                  "basic", i), "du1", [], None)
    exam = db.get_session("u1")
    assert exam["state"] == "EXAM_READY"
    sm.process_message("u1", "Начать", "du1", [], None)
    exam = db.get_session("u1")
    before = [_display_correct(exam, "exam", i) for i in range(3)]
    for i in range(3):                                  # завалить экзамен
        cur = db.get_session("u1")
        wrong = next(x for x in quiz.LETTERS
                     if x != _display_correct(cur, "exam", i))
        sm.process_message("u1", wrong, "du1", [], None)

    sm.process_message("u1", "Пересдать", "du1", [], None)
    retry = db.get_session("u1")
    assert retry["attempt"] > exam["attempt"]
    after = [_display_correct(retry, "exam", i) for i in range(3)]
    assert after != before


def test_legacy_session_without_attempt_column_value(env):
    """Строки, созданные до миграции (attempt NULL), не падают."""
    session = _start_basic("u1")
    with db._conn() as conn:
        conn.execute("UPDATE sessions SET attempt = NULL WHERE id = ?",
                     (session["id"],))
    fresh = db.get_session("u1")
    q = db.get_course_questions(fresh["course_id"])["basic_questions"][0]
    view = quiz.question_view(fresh, q, "basic", 0)
    assert len(view.options) == 4 and view.correct_display in quiz.LETTERS
