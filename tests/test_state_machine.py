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
        "prefixes": {"HSKP": "housekeeper", "FO": "admin_reception",
                     "RES": "reservations", "ALL": "all_staff"},
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


# ── №11: курс назначается по роли (префиксы в имени файла) ───────────────────

def _add_course(doc_name, doc_id):
    cid = db.save_draft_course(doc_name, doc_id,
                               json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(cid, "hr1")
    return cid


def test_session_starts_with_sentinel_course(env):
    sm.process_message("u1", "привет", "d1", [], None)
    assert db.get_session("u1")["course_id"] == 0     # курс до выбора роли не назначен


def test_course_matched_by_role(env):
    _add_course("RES Брони.docx", "d-res")
    fo_id = _add_course("FO Ресепшн.docx", "d-fo")
    sm.process_message("u1", "привет", "d1", [], None)
    reply = sm.process_message("u1", "2", "d1", [], None)   # admin_reception
    session = db.get_session("u1")
    assert session["course_id"] == fo_id                    # не RES-курс
    assert session["state"] == "READING"
    assert "Ресепшн" in reply and "FO" not in reply         # display_name без префикса


def test_all_staff_course_matches_any_role(env):
    # Единственный курс «Стандарты.docx» без префикса = all_staff
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)           # housekeeper
    session = db.get_session("u1")
    assert session["state"] == "READING"
    assert session["course_id"] == db.get_course_by_doc_name("Стандарты.docx")["id"]


def test_no_course_for_role_notifies_hr_once(env, monkeypatch):
    notes = []
    monkeypatch.setattr(sm, "notify_hr", lambda text: notes.append(text))
    std = db.get_course_by_doc_name("Стандарты.docx")["id"]
    db.create_session("u1", "d1", std, state="DONE")        # all_staff-курс пройден
    _add_course("RES Брони.docx", "d-res")                  # курс чужой роли

    sm.process_message("u1", "привет", "d1", [], None)
    reply = sm.process_message("u1", "2", "d1", [], None)   # admin_reception
    assert "нет назначенных курсов" in reply
    assert len(notes) == 1 and "admin_reception" not in notes[0]  # русское имя роли
    assert db.get_session("u1") is None                     # сессия-маркер закрыта

    reply2 = sm.process_message("u1", "привет", "d1", [], None)
    assert "нет назначенных курсов" in reply2
    assert len(notes) == 1                                  # HR не спамится повторно


def test_role_remembered_after_done(env):
    fo_id = _add_course("FO Ресепшн.docx", "d-fo")
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "2", "d1", [], None)           # admin_reception → FO-курс
    session = db.get_session("u1")
    assert session["course_id"] == fo_id
    db.update_session(session["id"], state="DONE")          # курс пройден

    reply = sm.process_message("u1", "привет", "d1", [], None)
    assert "1. Горничная" not in reply                      # меню НЕ показано
    assert "помню" in reply
    new = db.get_session("u1")
    assert new["role"] == "admin_reception"
    # следующий непройденный для роли — all_staff «Стандарты»
    assert new["course_id"] == db.get_course_by_doc_name("Стандарты.docx")["id"]


def test_legacy_all_done_message(env, monkeypatch):
    monkeypatch.setattr(sm, "selectable_roles", lambda: [])
    std = db.get_course_by_doc_name("Стандарты.docx")["id"]
    db.create_session("u2", "d2", std, state="DONE")
    reply = sm.process_message("u2", "привет", "d2", [], None)
    assert "пройдены" in reply
    assert db.get_session("u2") is None


# ── Демо-фидбек 05.08: «Мои курсы», подсказки, подписи HR ────────────────────

def test_my_courses_command_skips_rag(env):
    _add_course("RES Брони.docx", "d-res")
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)          # housekeeper → Стандарты
    reply = sm.process_message("u1", "Мои курсы", "d1", [], None)
    assert "▶️ Стандарты — проходишь сейчас" in reply
    assert "Брони" not in reply                            # курс чужой роли скрыт
    assert env == {}                                       # RAG не вызывался


def test_free_question_still_goes_to_rag(env):
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    reply = sm.process_message("u1", "какие курсы по уборке?", "d1", [], None)
    assert reply == "MOCK_ANSWER"                          # substring ≠ команда


def test_start_reading_shows_command_hints(env):
    sm.process_message("u1", "привет", "d1", [], None)
    reply = sm.process_message("u1", "1", "d1", [], None)
    assert "Мои курсы" in reply and "Роль" in reply


def test_finish_phase_notify_has_name(env, monkeypatch):
    notes = []
    monkeypatch.setattr(sm, "notify_hr", lambda text: notes.append(text))
    db.add_employee("u1", full_name="Иван Иванов", work_position="Администратор")
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    sm.process_message("u1", "Готов", "d1", [], None)      # BASIC_TEST, 1 вопрос
    sm.process_message("u1", "A", "d1", [], None)          # финиш базового
    assert notes and "Иван Иванов, Администратор (ID: u1)" in notes[0]
    assert "Допустить u1" in notes[0]                      # команда — по ID


def test_waiting_hr_menu(env, monkeypatch):
    monkeypatch.setattr(sm, "notify_hr", lambda text: None)
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    sm.process_message("u1", "Готов", "d1", [], None)
    sm.process_message("u1", "A", "d1", [], None)          # → WAITING_HR
    reply = sm.process_message("u1", "Мои курсы", "d1", [], None)
    assert "📚 Твои курсы" in reply
    reply2 = sm.process_message("u1", "что дальше?", "d1", [], None)
    assert "Ожидаем решения HR" in reply2
