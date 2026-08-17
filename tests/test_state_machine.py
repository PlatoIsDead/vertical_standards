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
    # 17.08: ожидание допуска не блокирует — вопросы уходят в RAG
    reply2 = sm.process_message("u1", "что дальше?", "d1", [], None)
    assert reply2 == "MOCK_ANSWER"


def test_waiting_park_switch_admit_exam(env, monkeypatch):
    """17.08: «ждёт допуска» — свойство курса, не клетка: сотрудник уходит во
    второй курс, допуск не выдёргивает, экзамен доступен через «Выбрать»."""
    monkeypatch.setattr(sm, "notify_hr", lambda *a, **kw: None)
    exam_q = {
        "course_summary": "s",
        "basic_questions": [{"text": "Б?", "options": ["A. 1", "B. 2"],
                             "correct": "A"}],
        "exam_questions": [{"text": "Э?", "options": ["A. 1", "B. 2"],
                            "correct": "A"}],
    }
    course_a = db.get_course_by_doc_name("Стандарты.docx")
    db.update_course_questions(course_a["id"],
                               json.dumps(exam_q, ensure_ascii=False))
    cid_b = db.save_draft_course("Правила.docx", "2",
                                 json.dumps(exam_q, ensure_ascii=False))
    db.activate_course_by_id(cid_b, "hr1")

    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)          # роль → первый курс
    first_course = db.get_session("u1")["course_id"]       # порядок не важен
    sm.process_message("u1", "Готов", "d1", [], None)
    sm.process_message("u1", "A", "d1", [], None)          # базовый → WAITING_HR
    a_row = db.get_waiting_session("u1")
    assert a_row and a_row["course_id"] == first_course

    # Переключение из ожидания: строка первого курса паркуется, второму — новая
    reply = sm.process_message("u1", "Выбрать 1", "d1", [], None)
    assert "Переключил курс" in reply
    active = db.get_session("u1")
    assert active["course_id"] != first_course and active["state"] == "READING"
    assert active["id"] != a_row["id"]
    assert db.get_waiting_session("u1")["id"] == a_row["id"]   # ожидание живо

    menu, _ = sm.my_courses("u1", active.get("role"))
    assert "ждёт допуска HR" in menu

    # Допуск НЕ выдёргивает из текущего курса
    db.admit_session(a_row["id"])
    assert db.get_session("u1")["id"] == active["id"]
    menu2, sel = sm.my_courses("u1", active.get("role"))
    assert "допущен к экзамену" in menu2

    # Вход в допущенный экзамен через «Выбрать»
    n = next(i + 1 for i, c in enumerate(sel) if c["id"] == first_course)
    reply = sm.process_message("u1", f"Выбрать {n}", "d1", [], None)
    assert "Экзамен" in reply and "Э?" in reply
    sm.process_message("u1", "A", "d1", [], None)          # сдать (1 вопрос)
    assert db.get_session_by_id(a_row["id"])["state"] == "DONE"
    assert db.get_session("u1")["id"] == active["id"]      # активен снова второй


# ── Протокол 05.08: BB-код, выбор курса ──────────────────────────────────────

def test_md_to_bb():
    assert sm.md_to_bb("Напиши *Готов* или *Роль*.") == \
        "Напиши [b]Готов[/b] или [b]Роль[/b]."
    assert sm.md_to_bb("2 * 3 = 6") == "2 * 3 = 6"          # незакрытая — не трогаем
    assert sm.md_to_bb("*а\nб*") == "*а\nб*"                # перенос внутри — нет


def test_notify_hr_converts_markdown(env, monkeypatch):
    sent = {}

    class _Resp:
        status_code = 200

    def fake_post(url, json=None, timeout=None):
        sent.update(json)
        return _Resp()

    monkeypatch.setattr(sm.httpx, "post", fake_post)
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://x/")
    monkeypatch.setenv("HR_USER_IDS", "9")
    sm.notify_hr("Напиши *Допустить 5*")
    assert sent["MESSAGE"] == "Напиши [b]Допустить 5[/b]"


def test_switch_course(env):
    new_id = _add_course("Правила2.docx", "d2")             # all_staff, новейший
    std_id = db.get_course_by_doc_name("Стандарты.docx")["id"]
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)           # housekeeper → Правила2
    assert db.get_session("u1")["course_id"] == new_id

    reply = sm.process_message("u1", "Мои курсы", "d1", [], None)
    assert "1. ⏳ Стандарты" in reply and "Выбрать" in reply

    reply = sm.process_message("u1", "Выбрать 1", "d1", [], None)
    assert "🔄 Переключил курс" in reply and "Стандарты" in reply
    session = db.get_session("u1")
    assert session["course_id"] == std_id
    assert session["state"] == "READING" and session["current_q_idx"] == 0
    # Старый курс НЕ помечен пройденным — доступен к выбору обратно
    reply = sm.process_message("u1", "Мои курсы", "d1", [], None)
    assert "1. ⏳ Правила2" in reply and "✅" not in reply


def test_switch_out_of_range_shows_list(env):
    _add_course("Правила2.docx", "d2")
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    reply = sm.process_message("u1", "Выбрать 99", "d1", [], None)
    assert "Такого номера нет" in reply and "📚 Твои курсы" in reply


def test_switch_forbidden_during_test(env):
    _add_course("Правила2.docx", "d2")
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    sm.process_message("u1", "Готов", "d1", [], None)       # BASIC_TEST
    reply = sm.process_message("u1", "Выбрать 1", "d1", [], None)
    assert "закончи текущий тест" in reply
    assert db.get_session("u1")["state"] == "BASIC_TEST"    # состояние не тронуто
