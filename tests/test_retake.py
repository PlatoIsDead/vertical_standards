"""№9 v2: пересдача экзамена + развилка «Пересдать / Далее» после провала."""
import json

import pytest

import app.db as db
import app.roles as roles
import app.state_machine as sm

QUESTIONS = {
    "course_summary": "s",
    "basic_questions": [
        {"id": 0, "text": "Б1?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
         "correct": "A", "explanation": ""},
    ],
    "exam_questions": [
        {"id": 0, "text": "Э1?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
         "correct": "A", "explanation": ""},
        {"id": 1, "text": "Э2?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
         "correct": "A", "explanation": ""},
    ],
}  # порог сдачи round(2*0.7) = 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("HR_USER_IDS", "")
    db.init_db()
    db.add_employee("u1", added_by="test")

    # Без конфига ролей: сразу READING (легаси-путь) — меню не мешает сценарию
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({"roles": {}, "prefixes": {}, "folders": {}}),
                   encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))

    cid = db.save_draft_course("Курс.docx", "1",
                               json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid, "hr")
    monkeypatch.setattr(sm, "notify_hr", lambda text: None)
    monkeypatch.setattr(sm, "post_exam_congratulation", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "rag_answer", lambda **kw: ("MOCK", []))
    return cid


def _fail_exam(cid):
    """Провалить курс: базовый ок, экзамен 0/2."""
    sm.process_message("u1", "привет", "d1", [], None)      # READING
    sm.process_message("u1", "Готов", "d1", [], None)       # BASIC_TEST
    sm.process_message("u1", "A", "d1", [], None)           # basic 1/1 → WAITING_HR
    db.update_session_by_user("u1", "EXAM", 0)              # «Допустить»
    sm.process_message("u1", "B", "d1", [], None)
    reply = sm.process_message("u1", "B", "d1", [], None)   # 0/2 → не сдан
    return reply


def test_fail_message_offers_retake(env):
    reply = _fail_exam(env)
    assert "❌ Не сдан" in reply and "Пересдать" in reply
    assert db.get_session("u1") is None                     # сессия DONE


def test_fork_after_fail_no_silent_assignment(env):
    _fail_exam(env)
    reply = sm.process_message("u1", "привет", "d1", [], None)
    assert "Не сдал" in reply or "не сдал" in reply
    assert "Пересдать" in reply and "Далее" in reply
    assert db.get_session("u1") is None                     # курс НЕ назначен молча


def test_retake_resets_exam_keeps_basic(env):
    _fail_exam(env)
    last = db.get_last_done_session("u1")
    reply = sm.process_message("u1", "Пересдать", "d1", [], None)
    assert "Пересдача экзамена" in reply and "Э1?" in reply
    session = db.get_session("u1")
    assert session["id"] == last["id"]                      # та же сессия
    assert session["state"] == "EXAM" and session["current_q_idx"] == 0
    assert session["score_exam"] == 0
    assert session["score_basic"] == 1                      # базовый цел
    assert db.get_session_answers(session["id"], "exam") == []
    assert len(db.get_session_answers(session["id"], "basic")) == 1


def test_second_attempt_scores_clean(env):
    _fail_exam(env)
    sm.process_message("u1", "пересдача", "d1", [], None)   # синоним
    sm.process_message("u1", "A", "d1", [], None)
    reply = sm.process_message("u1", "A", "d1", [], None)
    assert "2/2" in reply and "✅ Сдан" in reply             # только новая попытка


def test_dalee_assigns_next_course(env):
    _fail_exam(env)
    cid2 = db.save_draft_course("Второй.docx", "2",
                                json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid2, "hr")
    reply = sm.process_message("u1", "Далее", "d1", [], None)
    assert "Второй" in reply
    assert db.get_session("u1")["course_id"] == cid2


def test_retake_refused_during_active_session(env):
    _fail_exam(env)
    cid2 = db.save_draft_course("Второй.docx", "2",
                                json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid2, "hr")
    sm.process_message("u1", "Далее", "d1", [], None)       # активная сессия
    reply = sm.process_message("u1", "Пересдать", "d1", [], None)
    assert "закончи текущий курс" in reply
    assert db.get_session("u1")["course_id"] == cid2        # сессия не тронута


def test_retake_after_pass_refused(env):
    _fail_exam(env)
    sm.process_message("u1", "Пересдать", "d1", [], None)
    sm.process_message("u1", "A", "d1", [], None)
    sm.process_message("u1", "A", "d1", [], None)           # сдал 2/2
    reply = sm.process_message("u1", "Пересдать", "d1", [], None)
    assert "уже сдан" in reply


def test_retake_without_done_session(env):
    reply = sm.process_message("u1", "Пересдать", "d1", [], None)
    assert "нет завершённого экзамена" in reply
    assert db.get_session("u1") is None
