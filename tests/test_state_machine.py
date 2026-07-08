"""FSM: ROLE_SELECT-флоу, команда «Роль», role_filter в RAG (без сети)."""
import json

import pytest

import app.db as db
import app.roles as roles
import app.state_machine as sm

QUESTIONS = {
    "course_summary": "Краткое описание курса.",
    "basic_questions": [
        {"text": "Вопрос 1?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
         "correct": "A"},
    ],
    "exam_questions": [],
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Изолированные БД, конфиг ролей и мок rag_answer."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    # Whitelist (№2): тестовые юзеры должны быть допущены
    db.add_employee("u1", added_by="test")
    db.add_employee("u2", added_by="test")

    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {
            "housekeeper": "Горничная / Уборщица",
            "admin_reception": "Администратор ресепшн (СПиР)",
            "all_staff": "Все сотрудники",
        },
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))

    course_id = db.save_draft_course(
        "Стандарты.docx", "1", json.dumps(QUESTIONS, ensure_ascii=False)
    )
    db.activate_course_by_id(course_id, "hr1")

    captured = {}

    def fake_rag(**kwargs):
        captured.update(kwargs)
        return "MOCK_ANSWER", []

    monkeypatch.setattr(sm, "rag_answer", fake_rag)
    return captured


def test_new_session_starts_with_role_select(env):
    reply = sm.process_message("u1", "привет", "d1", [], None)
    assert "1. Горничная" in reply
    assert "2. Администратор ресепшн" in reply
    assert db.get_session("u1")["state"] == "ROLE_SELECT"


def test_invalid_digit_reprompts(env):
    sm.process_message("u1", "привет", "d1", [], None)
    reply = sm.process_message("u1", "99", "d1", [], None)
    assert "цифрой от 1 до 2" in reply
    assert db.get_session("u1")["state"] == "ROLE_SELECT"


def test_valid_digit_sets_role_and_starts_reading(env):
    sm.process_message("u1", "привет", "d1", [], None)
    reply = sm.process_message("u1", "2", "d1", [], None)
    assert "Администратор ресепшн" in reply
    assert "Добро пожаловать" in reply
    session = db.get_session("u1")
    assert session["state"] == "READING"
    assert session["role"] == "admin_reception"


def test_rag_called_with_role_filter(env):
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    reply = sm.process_message("u1", "Как убирать номер?", "d1", [], None)
    assert reply == "MOCK_ANSWER"
    assert env["role_filter"] == "housekeeper"


def test_role_command_switches_role(env):
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)

    reply = sm.process_message("u1", "Роль", "d1", [], None)
    assert "1. Горничная" in reply
    assert db.get_session("u1")["state"] == "ROLE_SELECT"

    sm.process_message("u1", "2", "d1", [], None)
    session = db.get_session("u1")
    assert session["role"] == "admin_reception"
    assert session["state"] == "READING"


def test_no_roles_config_falls_back_to_reading(env, monkeypatch):
    monkeypatch.setattr(sm, "selectable_roles", lambda: [])
    reply = sm.process_message("u2", "привет", "d2", [], None)
    assert "Добро пожаловать" in reply
    assert db.get_session("u2")["state"] == "READING"


# ── Доработка №2: whitelist + автостарт ──────────────────────────────────────

def test_unknown_user_rejected(env):
    reply = sm.process_message("stranger", "привет", "d9", [], None)
    assert "HR" in reply
    assert db.get_session("stranger") is None


def test_start_onboarding_creates_session(env):
    reply = sm.start_onboarding("u1", "d1")
    assert "1. Горничная" in reply
    session = db.get_session("u1")
    assert session["state"] == "ROLE_SELECT"
    assert session["dialog_id"] == "d1"
    # Повторный вызов при активной сессии — None (сессия не дублируется)
    assert sm.start_onboarding("u1", "d1") is None


def test_start_onboarding_without_courses(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "empty.db"))
    db.init_db()
    db.add_employee("u5", added_by="test")
    assert sm.start_onboarding("u5", "d5") is None
    assert db.get_session("u5") is None
