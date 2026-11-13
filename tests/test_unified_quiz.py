"""New sessions run one versioned test; old sessions keep their coordinates."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import bitrix_bot as bot, db, deadlines, gamification, hr_tools, keyboards, quiz
from app import state_machine as sm
from app.report_excel import _cell, _session_cell, build_report_xlsx


def question(i):
    return {"text": f"Question {i}?", "options": ["A. one", "B. two", "C. three", "D. four"],
            "correct": "B", "explanation": "standard"}


@pytest.fixture
def course(monkeypatch):
    data = {"doc_name": "Standard.docx", "basic_questions": [question(i) for i in range(3)],
            "exam_questions": [question(i) for i in range(3, 8)]}
    cid = db.save_draft_course("Standard.docx", "1", json.dumps(data))
    db.activate_course_by_id(cid, "hr")
    db.add_employee("u1", added_by="test")
    monkeypatch.setattr(sm, "selectable_roles", lambda: [])
    monkeypatch.setenv("SHUFFLE_OPTIONS", "1")
    monkeypatch.setattr(sm, "rag_answer", lambda **kw: ("mock", []))
    notices = []
    monkeypatch.setattr(sm, "notify_hr", lambda message: notices.append(message))
    monkeypatch.setattr(sm, "post_exam_congratulation", lambda *args: None)
    return cid, data, notices


def send(message):
    return sm.process_message("u1", message, "d1", [], None)


def current():
    session = db.get_session("u1")
    data = db.get_course_questions(session["course_id"])
    qs = quiz.phase_questions(session, data, "exam")
    idx = session["current_q_idx"]
    return session, quiz.question_view(session, qs[idx], "exam", idx)


def start(course):
    send("hello")
    return send("Готов")


@pytest.mark.parametrize("perm", quiz.PERMS)
def test_one_flow_all_permutations(course, monkeypatch, perm):
    monkeypatch.setattr(quiz, "option_perm", lambda *args: perm)
    reply = start(course)
    assert "70%" in reply and "Вопрос 1/8" in reply
    for idx in range(8):
        session, view = current()
        assert session["state"] == "EXAM" and session["current_q_idx"] == idx
        assert view.options == [line for line in reply.splitlines() if line[:3] in ("A. ", "B. ", "C. ", "D. ")]
        assert asyncio.run(bot._test_options(session)) == view.bodies
        kb = keyboards.for_session(session, test_options=view.bodies)
        payloads = [b["COMMAND_PARAMS"] for b in kb if b.get("COMMAND_PARAMS", "").startswith("answer:")]
        answer = view.correct_display if idx != 1 else next(x for x in quiz.LETTERS if x != view.correct_display)
        reply = send(next(p for p in payloads if p.endswith(":" + answer)))
        if idx == 1:
            assert f"{view.correct_display}. {view.correct_body}" in reply
        else:
            assert "✅ Верно!" in reply
    done = db.get_last_done_session("u1")
    assert done["quiz_version"] == 2 and done["score_exam"] == 7
    assert len(db.get_session_answers(done["id"])) == 8
    assert quiz.result(done, course[1]) == (7, 8, True)
    assert "Тест завершён" in reply and "✅ Сдан" in reply
    assert len(course[2]) == 1  # only the final result, no intermediate admission


def test_no_duplicates_and_snapshot_survives_hr_edits_pause_and_restart(course):
    cid, data, _ = course
    data["exam_questions"].insert(0, dict(data["basic_questions"][0]))
    db.update_course_questions(cid, json.dumps(data))
    first = start(course)
    session, view = current()
    assert len(quiz.phase_questions(session, data, "exam")) == 8
    send(view.correct_display)
    session, view = current()
    old_options = view.options
    db.update_course_questions(cid, json.dumps({"basic_questions": [question(99)], "exam_questions": []}))
    send("Выйти")
    assert db.get_session("u1")["state"] == "PAUSED_EXAM"
    assert "Вопрос 2/8" in send("Продолжить")
    assert current()[1].options == old_options
    send(current()[1].correct_display)
    assert db.get_session("u1")["current_q_idx"] == 2
    assert "Вопрос 1/8" in first


def test_stale_double_delivery_pause_wrong_session_and_attempt(course):
    start(course)
    session, view = current()
    payload = quiz.answer_payload(session, view.correct_display)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(send, [payload, payload]))
    assert sum("✅ Верно!" in r for r in replies) == 1
    assert db.get_session("u1")["current_q_idx"] == 1
    assert len(db.get_session_answers(session["id"])) == 1
    session, view = current()
    payload = quiz.answer_payload(session, view.correct_display)
    for bad in (payload.replace(f":{session['id']}:", ":999:"),
                payload.replace(f":{session['attempt']}:exam:", ":999:exam:")):
        assert "не засчитан" in send(bad)
    send("Выйти")
    assert "не засчитан" in send(payload)
    send("Продолжить")
    assert "✅ Верно!" in send(payload)


@pytest.mark.parametrize("right", [5, 6])
def test_ceil_threshold_retake_preserves_attempt_and_reports(course, right):
    start(course)
    for idx in range(8):
        session, view = current()
        stale = quiz.answer_payload(session, view.correct_display)
        send(view.correct_display if idx < right else next(x for x in quiz.LETTERS if x != view.correct_display))
    old = db.get_last_done_session("u1")
    assert "не засчитан" in send(stale)
    assert db.get_session("u1") is None
    assert quiz.result(old, course[1]) == (right, 8, right == 6)
    assert deadlines._passed(old, course[1]) == (right == 6)
    assert _cell([old], course[1])[0] == f"{'Сдан' if right == 6 else 'Не сдан'} {right}/8"
    assert f"тест {right}/8" in hr_tools._session_status(old, course[1])
    assert _session_cell(old, course[1])[1] == ("green" if right == 6 else "red")
    row = dict(old, doc_name="Standard.docx", questions_json=json.dumps(course[1]))
    wb = load_workbook(BytesIO(build_report_xlsx([row], {}, [db.get_course_by_id(course[0])])))
    assert wb["Детали"].cell(2, 6).value == f"{right}/8"
    rating = gamification.build_weekly_rating([row], {}, datetime.utcnow())
    assert bool(rating) == (right == 6)
    if right == 5:
        answers = db.get_session_answers(old["id"])
        assert "Пересдача теста" in send("Пересдать")
        fresh, _ = current()
        assert fresh["id"] != old["id"] and fresh["attempt"] > old["attempt"]
        assert db.get_session_answers(old["id"]) == answers
        assert "не засчитан" in send(stale)
    else:
        assert "Пересдача не нужна" in send("Пересдать")


def test_legacy_migration_preserves_progress_answers_and_seed(course):
    cid, data, _ = course
    session = db.create_session("u1", "d1", cid, state="BASIC_TEST", quiz_version=1)
    db.update_session(session["id"], q_idx=1, score_basic=1)
    db.log_answer(session["id"], 0, "basic", "B", 1)
    before = db.get_session("u1")
    answers = db.get_session_answers(session["id"])
    perm = quiz.option_perm(session["id"], 0, "basic", 1)
    db.init_db()
    assert db.get_session("u1") == before
    assert db.get_session_answers(session["id"]) == answers
    reply = send("Продолжить")
    assert "Вопрос 2/3" in reply
    assert quiz.question_view(before, data["basic_questions"][1], "basic", 1).perm == perm


@pytest.mark.parametrize("bad", [None, "E", 7])
def test_bad_correct_does_not_change_progress_or_answers(course, bad):
    start(course)
    before, _ = current()
    # Simulate corrupted persisted snapshot after first display.
    snapshot = quiz.session_questions(before, {})
    snapshot["basic_questions"][0]["correct"] = bad
    with db._conn() as conn:
        conn.execute("UPDATE sessions SET quiz_questions_json=? WHERE id=?", (json.dumps(snapshot), before["id"]))
    before = db.get_session("u1")
    assert "не засчитан" in send("A")
    assert db.get_session("u1") == before
    assert db.get_session_answers(before["id"]) == []
    assert course[2]


def test_two_options_reject_unshown_letter(course):
    cid, _, _ = course
    db.update_course_questions(cid, json.dumps({"basic_questions": [], "exam_questions": [
        {"text": "yes?", "options": ["A. yes", "B. no"], "correct": "A"}]}))
    start(course)
    session, _ = current()
    assert "не засчитан" in send("D")
    assert db.get_session_answers(session["id"]) == []
    assert len([b for b in keyboards.for_session(session, test_options=["yes", "no"]) if b.get("COMMAND_PARAMS", "").startswith("answer:")]) == 2
    assert "✅ Сдан" in send("A")


def test_old_command_buttons_are_rejected_but_typed_letters_work(course, monkeypatch):
    start(course)
    session, view = current()
    captured = []
    async def handle(uid, params, *args, **kwargs):
        captured.append(send(params))
    monkeypatch.setattr(bot, "_handle_employee_message", handle)
    client = TestClient(bot.app)
    response = client.post("/command", data={"event": "ONIMCOMMANDADD", "data[PARAMS][USER_ID]": "u1",
                           "data[PARAMS][DIALOG_ID]": "d1", "data[PARAMS][COMMAND_PARAMS]": view.correct_display})
    assert response.json()["status"] == "ok" and "не засчитан" in captured[0]
    assert db.get_session_answers(session["id"]) == []
    assert "✅ Верно!" in send(view.correct_display)


def test_invalid_course_can_be_fixed_before_snapshot(course):
    cid, data, notices = course
    data["basic_questions"][0].pop("correct")
    db.update_course_questions(cid, json.dumps(data))
    assert "не начат" in start(course)
    before = db.get_session("u1")
    assert before["state"] == "READING" and before["quiz_questions_json"] is None
    assert db.get_session_answers(before["id"]) == [] and notices
    data["basic_questions"][0]["correct"] = "B"
    db.update_course_questions(cid, json.dumps(data))
    assert "Вопрос 1/8" in send("Готов")


def test_switch_back_to_unified_course_keeps_snapshot_and_progress(course):
    start(course)
    session, view = current()
    send(view.correct_display)
    second = db.save_draft_course("Second.docx", "2", json.dumps(course[1]))
    db.activate_course_by_id(second, "hr")
    send("Выбрать 1")
    assert db.get_session("u1")["course_id"] == second
    reply = send("Выбрать 1")
    assert "Вопрос 2/8" in reply
    assert db.get_session("u1")["id"] == session["id"]
    assert "Вопрос 2/8" in send(f"Тест {course[0]}")


def test_migration_from_original_schema_preserves_old_rows(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY, user_id TEXT, dialog_id TEXT, course_id INTEGER,
                state TEXT, current_q_idx INTEGER, score_basic INTEGER, score_exam INTEGER,
                role TEXT, attempt INTEGER, started_at TEXT, updated_at TEXT);
            INSERT INTO sessions VALUES
                (4, 'u1', 'd1', 1, 'PAUSED_EXAM', 2, 3, 1, 'manager', 2, 'old', 'old');
            CREATE TABLE answers (
                id INTEGER PRIMARY KEY, session_id INTEGER, question_id INTEGER,
                phase TEXT, user_answer TEXT, is_correct INTEGER, answered_at TEXT);
            INSERT INTO answers VALUES (5, 4, 1, 'exam', 'B', 1, 'old');
        """)
    db.init_db()
    row = db.get_session_by_id(4)
    assert row["quiz_version"] == 1 and row["quiz_questions_json"] is None
    assert (row["state"], row["current_q_idx"], row["attempt"], row["updated_at"]) == ("PAUSED_EXAM", 2, 2, "old")
    assert db.get_session_answers(4)[0]["user_answer"] == "B"
    db.init_db()
    assert db.get_session_by_id(4) == row
    assert quiz.result(dict(row, score_exam=2), {"exam_questions": [question(i) for i in range(3)]}) == (2, 3, True)


@pytest.mark.parametrize("raw,correct,body", [
    (["B. two", "D. four", "A. one", "C. three"], "B", "two"),
    (["Г) four", "Б) two", "А) one", "В) three"], "В", "three"),
    (["Д: four", "В: two", "А: one", "С: three"], "В", "two"),
])
def test_prefix_binding_and_cyrillic_are_context_consistent(raw, correct, body):
    q = {"text": "test?", "options": raw, "correct": correct}
    view = quiz.question_view(None, q, "exam", 0)
    assert view.valid and view.correct_body == body
    assert hr_tools.correct_option(q) == f"{view.correct_display}. {body}"
    card = hr_tools.format_question_card("Standard", q, 1)
    assert f"{view.correct_display}. {body} ✅" in card


@pytest.mark.parametrize("options", [None, "ABCD", [None] * 4,
    ["Apple", "Banana", "Cat", "Dog"], ["A. x", "B. X", "C. y", "D. z"],
    ["A. one", "A. two", "C. three", "D. four"], ["A. one", "B.", "C. three", "D. four"]])
def test_invalid_option_shapes_are_fail_closed(options):
    assert not quiz.question_view(None, {"text": "test?", "options": options, "correct": "A"}, "exam", 0).valid


@pytest.mark.parametrize("total", range(1, 16))
def test_threshold_never_rounds_down(total):
    threshold = (7 * total + 9) // 10
    data = {"basic_questions": [], "exam_questions": [question(i) for i in range(total)]}
    assert quiz.pass_threshold(total) == threshold
    session = {"quiz_version": 2, "score_exam": threshold - 1}
    assert not quiz.result(session, data)[2]
    assert quiz.result(dict(session, score_exam=threshold), data)[2]


def test_two_of_three_is_failed_in_real_flow(course):
    cid, data, _ = course
    data["basic_questions"] = [question(0)]
    data["exam_questions"] = [question(1), question(2)]
    db.update_course_questions(cid, json.dumps(data))
    start(course)
    for idx in range(3):
        _, view = current()
        reply = send(view.correct_display if idx < 2 else next(x for x in quiz.LETTERS if x != view.correct_display))
    assert "❌ Не сдан" in reply
    done = db.get_last_done_session("u1")
    assert quiz.result(done, data) == (2, 3, False)
    assert _cell([done], data)[0] == "Не сдан 2/3"
    assert not deadlines._passed(done, data)


def test_legacy_seed_golden_value():
    assert quiz.option_perm(42, 0, "basic", 3) == (1, 2, 0, 3)
