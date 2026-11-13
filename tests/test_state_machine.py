"""FSM: ROLE_SELECT-флоу, команда «Роль», role_filter в RAG (без сети)."""
import json

import pytest

pytestmark = pytest.mark.usefixtures("legacy_sessions")

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
    # 02.09: тесты FSM ассертят конкретные буквы — порядок вариантов
    # фиксируем каноническим (перемешивание проверяет test_option_shuffle)
    monkeypatch.setenv("SHUFFLE_OPTIONS", "0")
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
        # непустой relevant: иначе _quiz_rag_reply справедливо решит, что по
        # оставшимся документам ничего не нашлось, и подменит текст
        return "MOCK_ANSWER", [{"doc_name": "Другой стандарт.docx"}]

    monkeypatch.setattr(sm, "rag_answer", fake_rag)
    return captured


def _ready(uid, dialog):
    """«Готов»; при нескольких курсах (18.09) бот сначала спрашивает, какой
    тест — легаси-тесты явно выбирают текущий курс."""
    reply = sm.process_message(uid, "Готов", dialog, [], None)
    if reply.startswith("❓ Какой тест начинаем"):
        cid = db.get_session(uid)["course_id"]
        reply = sm.process_message(uid, f"Тест {cid}", dialog, [], None)
    return reply


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
    assert reply.startswith("MOCK_ANSWER")   # + хвост «Что дальше»
    assert env["role_filter"] == "housekeeper"


def test_role_command_is_hr_only(env):
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    before = db.get_session("u1")
    reply = sm.process_message("u1", "Роль", "d1", [], None)
    after = db.get_session("u1")
    assert "🔒" in reply and "HR" in reply
    assert after["state"] == "READING"
    assert after["role"] == before["role"]
    assert after["course_id"] == before["course_id"]


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
    # 01.09 (ревью): выбор в меню НЕ пишет employees.role — роль легаси-
    # сотрудника приходит из памяти сессии («помню»), не из ветки HR
    assert "помню" in reply
    new = db.get_session("u1")
    assert new["role"] == "admin_reception"
    # следующий непройденный для роли — all_staff «Стандарты»
    assert new["course_id"] == db.get_course_by_doc_name("Стандарты.docx")["id"]


def test_role_command_cannot_override_hr_assignment(env):
    """Сотрудник не меняет ни HR-роль, ни текущую сессию."""
    db.set_employee_role("u1", "housekeeper")
    sm.process_message("u1", "привет", "d1", [], None)      # авто: HR-роль
    before = db.get_session("u1")
    reply = sm.process_message("u1", "Роль", "d1", [], None)
    after = db.get_session("u1")
    assert "🔒" in reply and "HR" in reply
    assert db.get_employee("u1")["role"] == "housekeeper"
    assert after["id"] == before["id"]
    assert after["role"] == before["role"] == "housekeeper"


def test_exit_outside_test_not_rag(env):
    """01.09: «Выйти» вне теста — вежливый ответ, не RAG."""
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)           # READING
    reply = sm.process_message("u1", "Выйти", "d1", [], None)
    assert "нет активного теста" in reply
    assert "MOCK_ANSWER" not in reply


def test_exam_pause_says_exam(env, monkeypatch):
    """01.09: пауза в экзамене называется экзаменом."""
    monkeypatch.setattr(sm, "notify_hr", lambda text: None)
    course = db.get_course_by_doc_name("Стандарты.docx")
    db.update_course_questions(course["id"],
                               json.dumps(_EXAM2, ensure_ascii=False))
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")
    sm.process_message("u1", "A", "d1", [], None)           # базовый сдан
    sm.process_message("u1", "Начать", "d1", [], None)      # EXAM
    assert db.get_session("u1")["state"] == "EXAM"
    reply = sm.process_message("u1", "Выйти", "d1", [], None)
    assert "Экзамен на паузе" in reply and "Тест на паузе" not in reply
    assert "пауза экзамена" in sm._STATE_HINTS["EXAM"]


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
    assert reply.startswith("MOCK_ANSWER")   # + хвост «Что дальше»                          # substring ≠ команда


def test_start_reading_shows_command_hints(env):
    sm.process_message("u1", "привет", "d1", [], None)
    reply = sm.process_message("u1", "1", "d1", [], None)
    assert "Мои курсы" in reply and "*Роль*" not in reply


def test_finish_phase_notify_has_name(env, monkeypatch):
    notes = []
    monkeypatch.setattr(sm, "notify_hr", lambda text: notes.append(text))
    db.add_employee("u1", full_name="Иван Иванов", work_position="Администратор")
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")      # BASIC_TEST, 1 вопрос
    sm.process_message("u1", "A", "d1", [], None)          # финиш базового
    assert notes and "Иван Иванов, Администратор (ID: u1)" in notes[0]
    # 31.08: авто-допуск — FYI вместо «Допустить»; экзамена в фикстуре нет
    assert "сдал базовый тест (1/1)" in notes[0]
    assert "Допустить" not in notes[0]


def test_exam_ready_menu(env, monkeypatch):
    """31.08: допуск автоматический; EXAM_READY не блокирует — меню и RAG
    свободны, экзамен стартует только командой «Начать»."""
    monkeypatch.setattr(sm, "notify_hr", lambda text: None)
    course = db.get_course_by_doc_name("Стандарты.docx")
    db.update_course_questions(course["id"],
                               json.dumps(_EXAM2, ensure_ascii=False))
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")
    reply = sm.process_message("u1", "A", "d1", [], None)  # базовый 1/1 сдан
    assert "допущен к экзамену" in reply
    assert db.get_session("u1")["state"] == "EXAM_READY"
    reply = sm.process_message("u1", "Мои курсы", "d1", [], None)
    assert "📚 Твои курсы" in reply
    reply2 = sm.process_message("u1", "что дальше?", "d1", [], None)
    assert reply2.startswith("MOCK_ANSWER")
    assert db.get_session("u1")["state"] == "EXAM_READY"   # RAG не стартует
    reply3 = sm.process_message("u1", "Начать", "d1", [], None)
    assert "Э1?" in reply3                                 # старт — командой
    assert db.get_session("u1")["state"] == "EXAM"


def test_basic_fail_retake(env, monkeypatch):
    """31.08: ниже порога (60%) — базовый пересдаётся с чистого счёта."""
    monkeypatch.setattr(sm, "notify_hr", lambda text: None)
    two_q = {"course_summary": "s",
             "basic_questions": [
                 {"text": "Б1?", "options": ["A. 1", "B. 2"], "correct": "A"},
                 {"text": "Б2?", "options": ["A. 1", "B. 2"], "correct": "A"}],
             "exam_questions": [
                 {"text": "Э?", "options": ["A. 1", "B. 2"], "correct": "A"}]}
    course = db.get_course_by_doc_name("Стандарты.docx")
    db.update_course_questions(course["id"],
                               json.dumps(two_q, ensure_ascii=False))
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")
    sm.process_message("u1", "B", "d1", [], None)          # 0/…
    reply = sm.process_message("u1", "B", "d1", [], None)  # 0/2 < порога 1
    assert "не сдан" in reply and "Готов" in reply
    session = db.get_session("u1")
    assert session["state"] == "READING"
    assert db.get_session_answers(session["id"], "basic") == []  # чисто
    _ready("u1", "d1")      # вторая попытка
    sm.process_message("u1", "A", "d1", [], None)
    reply = sm.process_message("u1", "A", "d1", [], None)  # 2/2 — сдан
    assert "Базовый тест сдан" in reply and "*2/2*" in reply
    assert db.get_session("u1")["state"] == "EXAM_READY"


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
    _ready("u1", "d1")
    sm.process_message("u1", "A", "d1", [], None)          # базовый → EXAM_READY
    a_row = db.get_session("u1")
    assert a_row["course_id"] == first_course and a_row["state"] == "EXAM_READY"

    # Переключение из допуска: строка первого курса паркуется, второму — новая
    reply = sm.process_message("u1", "Выбрать 1", "d1", [], None)
    assert "Переключил курс" in reply
    active = db.get_session("u1")
    assert active["course_id"] != first_course and active["state"] == "READING"
    assert active["id"] != a_row["id"]
    assert db.get_session_by_id(a_row["id"])["state"] == "EXAM_READY"  # жива

    menu2, sel = sm.my_courses("u1", active.get("role"))
    assert "допущен к экзамену" in menu2

    # Возврат через «Выбрать» — приглашение, экзамен стартует только «Начать»
    n = next(i + 1 for i, c in enumerate(sel) if c["id"] == first_course)
    reply = sm.process_message("u1", f"Выбрать {n}", "d1", [], None)
    assert "допущен к экзамену" in reply and "Начать" in reply
    reply = sm.process_message("u1", "Начать", "d1", [], None)
    assert "Э?" in reply
    sm.process_message("u1", "A", "d1", [], None)          # сдать (1 вопрос)
    assert db.get_session_by_id(a_row["id"])["state"] == "DONE"
    assert db.get_session("u1")["id"] == active["id"]      # активен снова второй


def test_legacy_waiting_hr_admit(env, monkeypatch):
    """Легаси-строка WAITING_HR (создана до 31.08) — «Допустить» переводит
    её в EXAM_READY, старт экзамена кнопкой."""
    monkeypatch.setattr(sm, "notify_hr", lambda *a, **kw: None)
    course = db.get_course_by_doc_name("Стандарты.docx")
    row = db.create_session("u2", "d2", course["id"])
    db.update_session(row["id"], state="WAITING_HR")
    assert db.get_waiting_session("u2")["id"] == row["id"]
    db.admit_session(row["id"])
    assert db.get_session_by_id(row["id"])["state"] == "EXAM_READY"


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


def test_switch_from_test_parks_progress(env):
    """19.08: переключение из теста больше не запрещено — тест паркуется."""
    _add_course("Правила2.docx", "d2")
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")       # BASIC_TEST
    reply = sm.process_message("u1", "Выбрать 1", "d1", [], None)
    assert "Переключил курс" in reply
    parked = [s for s in db.get_sessions_by_user("u1")
              if s["state"] == "BASIC_TEST"]
    assert len(parked) == 1                                 # прогресс жив
    assert db.get_session("u1")["state"] == "READING"       # активен новый курс


def test_test_pause_and_resume(env, monkeypatch):
    """19.08: тест — не клетка: меню/пауза/переключение с СОХРАНЕНИЕМ
    вопроса; возврат продолжает с того же места, ответы не задваиваются."""
    monkeypatch.setattr(sm, "notify_hr", lambda *a, **kw: None)
    two_q = {"course_summary": "s",
             "basic_questions": [
                 {"text": "Б1?", "options": ["A. 1", "B. 2"], "correct": "A"},
                 {"text": "Б2?", "options": ["A. 1", "B. 2"], "correct": "A"}],
             "exam_questions": []}
    course_a = db.get_course_by_doc_name("Стандарты.docx")
    db.update_course_questions(course_a["id"],
                               json.dumps(two_q, ensure_ascii=False))
    cid_b = db.save_draft_course("Правила.docx", "2",
                                 json.dumps(two_q, ensure_ascii=False))
    db.activate_course_by_id(cid_b, "hr1")

    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    first_course = db.get_session("u1")["course_id"]
    _ready("u1", "d1")      # вопрос 1
    sm.process_message("u1", "A", "d1", [], None)          # → вопрос 2
    assert db.get_session("u1")["current_q_idx"] == 1

    reply = sm.process_message("u1", "Мои курсы", "d1", [], None)
    assert "📚 Твои курсы" in reply
    assert db.get_session("u1")["state"] == "BASIC_TEST"   # состояние цело

    reply = sm.process_message("u1", "выйти", "d1", [], None)
    assert "паузе" in reply and "вопрос 2 из 2" in reply
    # 02.09: пауза — настоящее состояние, а не только текст
    assert db.get_session("u1")["state"] == "PAUSED_BASIC"

    reply = sm.process_message("u1", "Выбрать 1", "d1", [], None)
    assert "Переключил курс" in reply
    active = db.get_session("u1")
    assert active["course_id"] != first_course
    assert active["state"] == "READING"

    menu, sel = sm.my_courses("u1", active.get("role"))
    assert "базовый тест на вопросе 2" in menu             # метка паузы

    n = next(c["n"] for c in sel if c["id"] == first_course)
    reply = sm.process_message("u1", f"Выбрать {n}", "d1", [], None)
    assert "Продолжаем базовый тест" in reply and "Б2?" in reply
    reply = sm.process_message("u1", "A", "d1", [], None)  # дорешать
    assert "Базовый тест сдан" in reply                    # 2/2, экзамена нет
    row = next(x for x in db.get_sessions_by_user("u1")
               if x["course_id"] == first_course)
    assert row["state"] == "DONE"
    assert row["score_basic"] == 2                         # без задвоений


# ── 21.08: фиксы встречи — меню отовсюду, честные статусы, «Продолжить» ─────

_EXAM2 = {"course_summary": "s",
          "basic_questions": [{"text": "Б?", "options": ["A. 1", "B. 2"],
                               "correct": "A"}],
          "exam_questions": [
              {"text": "Э1?", "options": ["A. 1", "B. 2"], "correct": "A"},
              {"text": "Э2?", "options": ["A. 1", "B. 2"], "correct": "A"}]}


def test_menu_everywhere_no_side_effects(env, monkeypatch):
    """«Мои курсы»/«Документы» при session None: список, БЕЗ retake-форка и
    БЕЗ молчаливого назначения курса (живой тупик Дмитрия 21.08)."""
    monkeypatch.setattr(sm, "notify_hr", lambda *a, **kw: None)
    course = db.get_course_by_doc_name("Стандарты.docx")
    db.update_course_questions(course["id"],
                               json.dumps(_EXAM2, ensure_ascii=False))
    s = db.create_session("u1", "d1", course["id"], state="DONE")  # провал 0/2
    db.update_session(s["id"], role="housekeeper")

    reply = sm.process_message("u1", "Мои курсы", "d1", [], None)
    assert "📚 Твои курсы" in reply
    assert db.get_session("u1") is None            # курс НЕ назначился
    assert "Ты не сдал экзамен" not in reply       # форк НЕ перехватил

    doc_chunks = [{"doc_name": "ALL Правила.docx", "text": "т",
                   "roles": ["all_staff"], "audience": "staff"}]
    reply = sm.process_message("u1", "Документы", "d1", doc_chunks, None)
    assert "📖" in reply and "Правила" in reply
    assert db.get_session("u1") is None

    # обычное сообщение — форк работает как раньше
    reply = sm.process_message("u1", "привет", "d1", [], None)
    assert "Пересдать" in reply


def test_my_courses_honest_statuses(env, monkeypatch):
    """21.08: провал ≠ «пройден»; у последнего провала — подсказка Пересдать."""
    monkeypatch.setattr(sm, "notify_hr", lambda *a, **kw: None)
    course_a = db.get_course_by_doc_name("Стандарты.docx")
    db.update_course_questions(course_a["id"],
                               json.dumps(_EXAM2, ensure_ascii=False))
    cid_b = db.save_draft_course("Правила.docx", "2",
                                 json.dumps(_EXAM2, ensure_ascii=False))
    db.activate_course_by_id(cid_b, "hr1")

    sb = db.create_session("u1", "d1", cid_b, state="DONE")       # B сдан 2/2
    db.update_session(sb["id"], score_exam=2)
    sa = db.create_session("u1", "d1", course_a["id"], state="DONE")  # A провал
    db.update_session(sa["id"], score_exam=0)

    menu, sel = sm.my_courses("u1", None)
    assert "✅ Правила — пройден" in menu
    assert "❌ Стандарты — тест не сдан (0/2)" in menu
    assert "напиши *Пересдать*" in menu            # A = последний провал
    assert sel == []                               # завершённые не выбираемы


def test_continue_returns_to_question(env, monkeypatch):
    monkeypatch.setattr(sm, "notify_hr", lambda *a, **kw: None)
    course = db.get_course_by_doc_name("Стандарты.docx")
    db.update_course_questions(course["id"],
                               json.dumps(_EXAM2, ensure_ascii=False))
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")        # вопрос Б?
    reply = sm.process_message("u1", "выйти", "d1", [], None)
    assert "Что дальше" in reply and "Продолжить" in reply
    reply = sm.process_message("u1", "Продолжить", "d1", [], None)
    assert "Б?" in reply                                     # тот же вопрос
    assert db.get_session("u1")["current_q_idx"] == 0        # прогресс цел
    reply = sm.process_message("u1", "A", "d1", [], None)    # отвечает дальше
    assert "Базовый тест сдан" in reply                      # 31.08: авто-допуск
    assert "Что дальше" in reply                             # хвост-подсказка


def test_documents_sections(env):
    chunks = [
        {"doc_name": "FO Пожар.docx", "text": "т",
         "roles": ["admin_reception"], "audience": "staff"},
        {"doc_name": "ALL Общее.docx", "text": "т",
         "roles": ["all_staff"], "audience": "staff"},
    ]
    out = sm._my_documents_text("admin_reception", chunks)
    assert out.index("📌") < out.index("Пожар") < out.index("📖") \
        < out.index("Общее")


# ── 25.08: «после каждой команды — пояснение» (обещание со встречи 21.08) ────

def test_help_command_and_first_contact_greeting(env):
    reply = sm.process_message("u1", "привет", "d1", [], None)
    assert "Что я умею" in reply          # первый контакт = приветствие
    reply2 = sm.process_message("u1", "Помощь", "d1", [], None)
    assert reply2 == sm._HELP_TEXT        # команда работает из любого состояния


def test_hint_follows_state(env):
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    rag = sm.process_message("u1", "как дела?", "d1", [], None)
    assert "Что дальше: *Готов*" in rag            # READING-хвост на RAG-ответе
    q = _ready("u1", "d1")
    assert "ответь буквой A–D" in q                # хвост по НОВОМУ состоянию
    pause = sm.process_message("u1", "выйти", "d1", [], None)
    assert pause.count("Что дальше") == 1          # свой текст, без дубля хвоста
    assert "*Помощь*" in pause
    # хвост паузы приходит из _STATE_HINTS только для НЕ-своих текстов
    ans = sm.process_message("u1", "как дела?", "d1", [], None)
    assert "*Продолжить*" in ans


# ── 02.09: роль «Менеджер» (all_access) — все курсы и документы ──────────────

def _manager_config(tmp_path, monkeypatch):
    cfg = tmp_path / "roles_mgr.json"
    cfg.write_text(json.dumps({
        "roles": {"manager": "Менеджер", "housekeeper": "Горничная / Уборщица",
                  "admin_reception": "СПиР", "all_staff": "Все сотрудники"},
        "all_access": ["manager"],
        "prefixes": {"HSKP": "housekeeper", "FO": "admin_reception",
                     "ALL": "all_staff"},
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))


def test_manager_gets_any_department_course(env, tmp_path, monkeypatch):
    """Менеджеру назначается ведомственный курс чужого отдела и виден в меню."""
    monkeypatch.setattr(sm, "notify_hr", lambda text: None)
    _manager_config(tmp_path, monkeypatch)
    fo_id = _add_course("FO Ресепшн.docx", "d-fo")
    db.set_employee_role("u1", "manager")

    reply = sm.process_message("u1", "привет", "d1", [], None)
    assert "Менеджер" in reply and "назначил HR" in reply
    assert db.get_session("u1")["course_id"] in (fo_id, db.get_course_by_doc_name(
        "Стандарты.docx")["id"])

    menu, selectable = sm.my_courses("u1", "manager")
    assert "Ресепшн" in menu                       # чужой отдел — в списке


def test_manager_documents_include_all_departments(env, tmp_path, monkeypatch):
    _manager_config(tmp_path, monkeypatch)
    chunks = [
        {"doc_name": "FO Ресепшн.docx", "roles": ["admin_reception"],
         "audience": "staff"},
        {"doc_name": "HSKP Уборка.docx", "roles": ["housekeeper"],
         "audience": "staff"},
        {"doc_name": "ALL Общий.docx", "roles": ["all_staff"],
         "audience": "staff"},
        {"doc_name": "Гостям.docx", "roles": ["all_staff"], "audience": "guest"},
    ]
    text = sm._my_documents_text("manager", chunks)
    assert "По отделам" in text                    # не «Для твоего отдела»
    assert "Ресепшн" in text and "Уборка" in text and "Общий" in text
    assert "Гостям" not in text


# ── 02.09: вопрос по стандартам во время теста ──────────────────────────────

def test_question_during_basic_test_gets_rag_answer(env):
    """Живой прогон 02.09: в тесте бот отбивался «ответь одной буквой» —
    теперь отвечает по стандартам и возвращает к тому же вопросу."""
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")
    before = db.get_session("u1")["current_q_idx"]

    reply = sm.process_message("u1", "В каком случае номер ставят Out of Order?",
                               "d1", [{"text": "чанк"}], "emb")
    assert "MOCK_ANSWER" in reply
    assert "Возвращаемся к тесту" in reply and "Вопрос 1/" in reply
    assert "ответь одной буквой" not in reply
    assert db.get_session("u1")["current_q_idx"] == before   # прогресс цел
    # документ курса из поиска исключён — по нему и генерились вопросы
    assert env["exclude_doc"] == "Стандарты.docx"


def test_question_during_exam_answers_excluding_course_doc(env, monkeypatch):
    """02.09 (решение юзера): в экзамене бот тоже отвечает — честность держит
    исключение документа курса, из которого сделаны вопросы."""
    monkeypatch.setattr(sm, "notify_hr", lambda text: None)
    course = db.get_course_by_doc_name("Стандарты.docx")
    db.update_course_questions(course["id"],
                               json.dumps(_EXAM2, ensure_ascii=False))
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")
    sm.process_message("u1", "A", "d1", [], None)            # базовый сдан
    sm.process_message("u1", "Начать", "d1", [], None)       # EXAM

    reply = sm.process_message("u1", "а что делать при пожаре?", "d1",
                               [{"text": "чанк"}], "emb")
    assert "MOCK_ANSWER" in reply and "Идёт экзамен" not in reply
    assert "Возвращаемся к тесту" in reply and "Вопрос 1/" in reply
    assert env["exclude_doc"] == "Стандарты.docx"


# ── 02.09: пауза — настоящее состояние ──────────────────────────────────────

def _to_pause(env_unused=None):
    """u1 → базовый тест на вопросе 1 → пауза."""
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")
    sm.process_message("u1", "выйти", "d1", [], None)
    return db.get_session("u1")


def test_pause_is_a_state_and_resume_returns(env):
    paused = _to_pause()
    assert paused["state"] == "PAUSED_BASIC"
    reply = sm.process_message("u1", "Продолжить", "d1", [], None)
    assert "Продолжаем" in reply and "Вопрос 1/" in reply
    assert db.get_session("u1")["state"] == "BASIC_TEST"


def test_letter_in_pause_not_counted(env):
    """Кнопки Bitrix живут под старыми сообщениями: буква в паузе — почти
    всегда промах по кнопке прошлого вопроса."""
    paused = _to_pause()
    reply = sm.process_message("u1", "A", "d1", [], None)
    assert "не засчитан" in reply and "Продолжить" in reply
    assert "— Вопрос" not in reply          # иначе Bitrix вернёт кнопки A–D
    assert db.get_session_answers(paused["id"], "basic") == []
    assert db.get_session("u1")["state"] == "PAUSED_BASIC"


def _two_question_course(course_id=None):
    """Курс с ДВУМЯ базовыми вопросами: с одним ответ сразу закрывает тест."""
    two = {"course_summary": "s", "basic_questions": [
        {"text": "Б1?", "options": ["A. 1", "B. 2"], "correct": "A"},
        {"text": "Б2?", "options": ["A. 1", "B. 2"], "correct": "A"}],
        "exam_questions": []}
    if course_id is None:
        course_id = db.get_course_by_doc_name("Стандарты.docx")["id"]
    db.update_course_questions(course_id, json.dumps(two, ensure_ascii=False))
    return course_id


def test_ready_in_pause_does_not_restart_test(env):
    """«Готов» в паузе = «Продолжить»: без обнуления прогресса."""
    _two_question_course()
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")
    sm.process_message("u1", "A", "d1", [], None)       # ответ на вопрос 1
    sm.process_message("u1", "выйти", "d1", [], None)
    before = db.get_session("u1")
    assert before["current_q_idx"] == 1

    reply = _ready("u1", "d1")
    after = db.get_session("u1")
    assert after["current_q_idx"] == 1 and after["state"] == "BASIC_TEST"
    assert after["attempt"] == before["attempt"]        # попытка не менялась
    assert len(db.get_session_answers(after["id"], "basic")) == 1
    assert "Вопрос 2/" in reply


def test_question_in_pause_excludes_course_doc(env):
    _to_pause()
    reply = sm.process_message("u1", "что по пожару?", "d1",
                               [{"text": "чанк"}], "emb")
    assert env["exclude_doc"] == "Стандарты.docx"
    assert "MOCK_ANSWER" in reply
    assert reply.count("Продолжить") == 1              # хвост не задвоился
    assert db.get_session("u1")["state"] == "PAUSED_BASIC"


def test_pause_notice_when_only_course_doc_matches(env, monkeypatch):
    """Нашлось только в закрытом документе → честный текст, а не сухое
    «Не найдено релевантных фрагментов»."""
    monkeypatch.setattr(sm, "rag_answer",
                        lambda **kw: ("Не найдено релевантных фрагментов.", []))
    _to_pause()
    reply = sm.process_message("u1", "что там в стандарте?", "d1",
                               [{"text": "чанк"}], "emb")
    assert "во время теста не подсказываю" in reply
    assert "Не найдено релевантных фрагментов" not in reply


def test_switch_from_pause_keeps_progress(env):
    """Гейт PROGRESS_STATES: паузную строку нельзя репойнтить на другой курс,
    иначе ответы уедут чужому курсу."""
    # назначится САМЫЙ НОВЫЙ курс — вопросы подменяем именно у него
    _two_question_course(_add_course("HSKP Уборка.docx", "d-h"))
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")
    sm.process_message("u1", "A", "d1", [], None)
    paused_row = db.get_session("u1")
    sm.process_message("u1", "выйти", "d1", [], None)

    menu, sel = sm.my_courses("u1", "housekeeper")
    assert "на паузе" in menu
    n = sel[0]["n"]
    sm.process_message("u1", f"Выбрать {n}", "d1", [], None)
    parked = db.get_session_by_id(paused_row["id"])
    assert parked["state"] == "PAUSED_BASIC"           # строка цела
    assert parked["course_id"] == paused_row["course_id"]
    assert parked["current_q_idx"] == 1
    assert db.get_session("u1")["id"] != paused_row["id"]


def test_return_to_paused_course_resumes_question(env):
    _two_question_course(_add_course("HSKP Уборка.docx", "d-h"))
    sm.process_message("u1", "привет", "d1", [], None)
    sm.process_message("u1", "1", "d1", [], None)
    _ready("u1", "d1")
    first = db.get_session("u1")["course_id"]
    sm.process_message("u1", "выйти", "d1", [], None)
    _, sel = sm.my_courses("u1", "housekeeper")
    sm.process_message("u1", f"Выбрать {sel[0]['n']}", "d1", [], None)

    _, sel2 = sm.my_courses("u1", "housekeeper")
    back = next(c["n"] for c in sel2 if c["id"] == first)
    reply = sm.process_message("u1", f"Выбрать {back}", "d1", [], None)
    assert "Вопрос 1/" in reply                        # вопрос показан
    row = db.get_session("u1")
    assert row["course_id"] == first
    assert row["state"] == "BASIC_TEST"                # пауза снята


# ── 18.09: выбор теста при нескольких курсах («Готов» ≠ последний тронутый) ──

def _to_reading(uid="u1", dialog="d1"):
    sm.process_message(uid, "привет", dialog, [], None)
    sm.process_message(uid, "2", dialog, [], None)          # admin_reception
    return db.get_session(uid)


def test_ready_single_course_starts_test(env):
    _to_reading()
    reply = sm.process_message("u1", "Готов", "d1", [], None)
    assert "Начинаем базовый тест" in reply
    assert db.get_session("u1")["state"] == "BASIC_TEST"


def test_ready_with_other_courses_asks(env):
    other = _add_course("ALL Второй.docx", "d-2")
    current = _to_reading()["course_id"]
    reply = sm.process_message("u1", "Готов", "d1", [], None)
    assert reply.startswith("❓ Какой тест начинаем")
    assert f"Тест {current}" in reply and f"Тест {other}" in reply
    assert "текущий курс" in reply
    assert "Что дальше" not in reply                    # хинт не дублируется
    assert db.get_session("u1")["state"] == "READING"   # ничего не стартовало


def test_test_pick_current_starts_basic(env):
    _add_course("ALL Второй.docx", "d-2")
    current = _to_reading()["course_id"]
    reply = sm.process_message("u1", f"Тест {current}", "d1", [], None)
    assert "Начинаем базовый тест" in reply
    session = db.get_session("u1")
    assert session["course_id"] == current and session["state"] == "BASIC_TEST"


def _two_courses():
    """Второй ALL-курс + сотрудник в чтении; возвращает (текущий, другой) —
    порядок назначения = порядок активации, поэтому вычисляем, не угадываем."""
    second = _add_course("ALL Второй.docx", "d-2")
    first = db.get_course_by_doc_name("Стандарты.docx")["id"]
    current = _to_reading()["course_id"]
    return current, (second if current == first else first)


def test_test_pick_other_switches_and_starts(env):
    current, other = _two_courses()
    reply = sm.process_message("u1", f"Тест {other}", "d1", [], None)
    assert reply.startswith("🔄 Курс") and "Начинаем базовый тест" in reply
    session = db.get_session("u1")
    assert session["course_id"] == other and session["state"] == "BASIC_TEST"


def test_test_pick_paused_course_resumes(env):
    current, other = _two_courses()
    sm.process_message("u1", f"Тест {current}", "d1", [], None)   # BASIC_TEST
    sm.process_message("u1", "Выйти", "d1", [], None)              # пауза
    assert db.get_session("u1")["state"] == "PAUSED_BASIC"
    sm.process_message("u1", f"Тест {other}", "d1", [], None)     # другой курс
    assert db.get_session("u1")["course_id"] == other
    # обратно к запаркованному: продолжаем с того же вопроса, не с нуля
    reply = sm.process_message("u1", f"Тест {current}", "d1", [], None)
    assert "Продолжаем базовый тест" in reply
    session = db.get_session("u1")
    assert session["course_id"] == current and session["state"] == "BASIC_TEST"
    # текущий курс на паузе + «Тест текущий» = продолжить
    sm.process_message("u1", "Выйти", "d1", [], None)
    reply = sm.process_message("u1", f"Тест {current}", "d1", [], None)
    assert "Продолжаем" in reply
    assert db.get_session("u1")["state"] == "BASIC_TEST"


def test_test_pick_unknown_id(env):
    _to_reading()
    reply = sm.process_message("u1", "Тест 999", "d1", [], None)
    assert "нет в твоём списке" in reply
    assert db.get_session("u1")["state"] == "READING"


def test_ready_substring_no_longer_starts(env):
    _to_reading()
    reply = sm.process_message("u1", "я пока не готов", "d1", [], None)
    assert "MOCK_ANSWER" in reply
    assert db.get_session("u1")["state"] == "READING"


def test_pick_without_session_opens_chosen_course(env):
    """Кнопка «Выбрать 2»/«Тест N» под старым сообщением при закрытой
    сессии раньше молча назначала первый курс."""
    second = _add_course("ALL Второй.docx", "d-2")
    _, selectable = sm.my_courses("u2", None)
    n = next(c["n"] for c in selectable if c["id"] == second)
    reply = sm.process_message("u2", f"Выбрать {n}", "d2", [], None)
    assert "Второй" in reply
    session = db.get_session("u2")
    assert session["course_id"] == second and session["state"] == "READING"

    db.update_session(session["id"], state="DONE")
    first = db.get_course_by_doc_name("Стандарты.docx")["id"]
    reply = sm.process_message("u2", f"Тест {first}", "d2", [], None)
    assert "Начинаем базовый тест" in reply
    session = db.get_session("u2")
    assert session["course_id"] == first and session["state"] == "BASIC_TEST"
    # пройденный курс кнопкой не открывается
    reply = sm.process_message("u2", f"Тест {second}", "d2", [], None)
    assert "нет в твоём списке" in reply
