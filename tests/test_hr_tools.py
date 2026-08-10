"""hr_tools: нумерация 1–15, парсер замены, история, отчёт — чистые функции."""
import pytest

from app.hr_tools import (
    apply_replacement,
    build_history_text,
    build_report_text,
    parse_replacement,
    question_by_ref,
    resolve_question_ref,
    ui_number,
)

VALID_REPLACEMENT = (
    "Что делать при пожаре?\n"
    "A. Бежать\n"
    "B. Звонить 112\n"
    "C. Прятаться\n"
    "D. Кричать\n"
    "Ответ: B"
)


def _questions():
    return {
        "doc_name": "Стандарты.docx",
        "basic_questions": [
            {"id": i, "text": f"Базовый {i + 1}?",
             "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
             "correct": "A", "explanation": ""} for i in range(5)
        ],
        "exam_questions": [
            {"id": i, "text": f"Экзамен {i + 1}?",
             "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
             "correct": "A", "explanation": ""} for i in range(10)
        ],
    }


# ── Нумерация ────────────────────────────────────────────────────────────────

def test_resolve_question_ref_bounds():
    assert resolve_question_ref(1) == ("basic", 0)
    assert resolve_question_ref(5) == ("basic", 4)
    assert resolve_question_ref(6) == ("exam", 0)
    assert resolve_question_ref(15) == ("exam", 9)
    for bad in (0, 16, -1):
        with pytest.raises(ValueError):
            resolve_question_ref(bad)


def test_ui_number_roundtrip():
    assert ui_number("basic", 0) == 1
    assert ui_number("exam", 0) == 6
    assert ui_number("exam", 9) == 15


def test_question_by_ref():
    q = question_by_ref(_questions(), 7)
    assert q["text"] == "Экзамен 2?"
    assert question_by_ref(_questions(), 16) is None


# ── Парсер замены ────────────────────────────────────────────────────────────

def test_parse_replacement_happy():
    repl = parse_replacement(VALID_REPLACEMENT)
    assert repl["text"] == "Что делать при пожаре?"
    assert repl["options"] == ["A. Бежать", "B. Звонить 112", "C. Прятаться", "D. Кричать"]
    assert repl["correct"] == "B"
    assert repl["explanation"] == ""


def test_parse_replacement_cyrillic_and_parens():
    raw = ("Вопрос?\n"
           "А. один\n"      # кириллическая А
           "B) два\n"       # скобка вместо точки
           "С. три\n"       # кириллическая С
           "D. четыре\n"
           "Ответ: В\n"     # кириллическая В = B
           "Пояснение: потому что")
    repl = parse_replacement(raw)
    assert repl["options"][0] == "A. один"
    assert repl["options"][2] == "C. три"
    assert repl["correct"] == "B"
    assert repl["explanation"] == "потому что"


def test_parse_replacement_multiline_text():
    raw = "Первая строка вопроса\nвторая строка\nA. а\nB. б\nC. в\nD. г\nОтвет: A"
    assert parse_replacement(raw)["text"] == "Первая строка вопроса вторая строка"


def test_parse_replacement_rejects():
    assert parse_replacement("Вопрос?\nA. а\nB. б\nC. в\nОтвет: A") is None  # 3 варианта
    assert parse_replacement("Вопрос?\nA. а\nB. б\nC. в\nD. г") is None      # без Ответ:
    assert parse_replacement("Вопрос?\nA. а\nA. а2\nB. б\nC. в\nОтвет: A") is None  # дубль A
    assert parse_replacement("A. а\nB. б\nC. в\nD. г\nОтвет: A") is None     # пустой текст
    assert parse_replacement("просто текст без вариантов") is None


def test_parse_replacement_text_starting_with_otvet_like_word():
    # «Ответственность…» не должна съедаться как строка «Ответ:»
    raw = "Ответственность за пожар?\nA. а\nB. б\nC. в\nD. г\nОтвет: D"
    repl = parse_replacement(raw)
    assert repl["text"] == "Ответственность за пожар?"
    assert repl["correct"] == "D"


# ── Применение замены ────────────────────────────────────────────────────────

def test_apply_replacement_targets_exact_question():
    questions = _questions()
    repl = parse_replacement(VALID_REPLACEMENT)
    updated = apply_replacement(questions, 7, repl)
    exam = updated["exam_questions"]
    assert exam[1]["text"] == "Что делать при пожаре?"
    assert exam[1]["correct"] == "B"
    assert exam[1]["id"] == 1                       # id исходного сохранён
    assert exam[0]["text"] == "Экзамен 1?"          # соседи нетронуты
    assert updated["basic_questions"][0]["text"] == "Базовый 1?"


# ── Отчёт и история ──────────────────────────────────────────────────────────

def _row(uid, state, exam=0, basic=0, name="Стандарты.docx"):
    import json
    return {"id": 1, "user_id": uid, "state": state, "score_basic": basic,
            "score_exam": exam, "doc_name": name, "course_id": 1,
            "questions_json": json.dumps(_questions()), "updated_at": "2026-07-08T10:00:00"}


def test_report_passed_threshold():
    text = build_report_text([_row("500", "DONE", exam=8)], {})
    assert "✅ сдан" in text and "8/10" in text
    text = build_report_text([_row("500", "DONE", exam=6)], {})
    assert "❌ не сдан" in text  # round(10*0.7)=7 > 6


def test_report_labels():
    emps = {"500": {"bitrix_uid": "500", "full_name": "Иван Иванов",
                    "email": "ivan@x.ru"}}
    text = build_report_text([_row("500", "WAITING_HR"), _row("42", "EXAM")], emps)
    assert "Иван Иванов (ivan@x.ru)" in text
    assert "ждёт допуска" in text
    assert "ID 42" in text and "сдаёт экзамен" in text


def test_report_label_with_position_and_role_section(tmp_path, monkeypatch):
    """Демо-фидбек C + протокол 05.08: ФИО (должность, email), роль —
    заголовком секции (не дублируется в строке)."""
    import json as _json

    import app.roles as roles
    cfg = tmp_path / "roles.json"
    cfg.write_text(_json.dumps({
        "roles": {"reservations": "Отдел бронирования"},
        "prefixes": {}, "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))

    emps = {"500": {"bitrix_uid": "500", "full_name": "Иван Иванов",
                    "email": "ivan@x.ru", "work_position": "Администратор"}}
    row = dict(_row("500", "READING"), role="reservations")
    text = build_report_text([row], emps)
    assert "— Отдел бронирования —" in text
    assert "Иван Иванов (Администратор, ivan@x.ru)" in text
    assert "· Отдел бронирования" not in text        # в строке не дублируется


def test_report_grouped_by_role(tmp_path, monkeypatch):
    import json as _json

    import app.roles as roles
    cfg = tmp_path / "roles.json"
    cfg.write_text(_json.dumps({
        "roles": {"reservations": "Отдел бронирования",
                  "housekeeper": "Горничные"},
        "prefixes": {}, "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))

    rows = [dict(_row("1", "READING"), role="reservations"),
            dict(_row("2", "READING"), role="housekeeper"),
            _row("3", "READING")]                     # без роли
    text = build_report_text(rows, {})
    assert "— Горничные —" in text and "— Отдел бронирования —" in text
    assert text.rstrip().split("\n")[-2] == "— Без роли —"  # секция последняя
    assert text.count("•") == 3


def test_correct_option():
    from app.hr_tools import correct_option
    q = {"correct": "B", "options": ["A. Бежать", "B. Звонить 112", "C. Ждать",
                                    "D. Кричать"]}
    assert correct_option(q) == "B. Звонить 112"
    assert correct_option({"correct": "A", "options": ["1) кривой формат"]}) == "A"
    assert correct_option({"correct": "C", "options": ["c. нижний регистр"]}) == \
        "c. нижний регистр"


def test_report_truncates_to_30():
    rows = [_row(str(i), "READING") for i in range(35)]
    text = build_report_text(rows, {})
    assert "последние 30 сессий из 35" in text
    assert text.count("•") == 30


def test_history_marks_and_numbering():
    questions = _questions()
    sessions = [{"id": 10, "course_id": 1, "state": "EXAM",
                 "score_basic": 1, "score_exam": 0}]
    answers = {10: [
        {"phase": "basic", "question_id": 0, "user_answer": "A", "is_correct": 1},
        {"phase": "exam", "question_id": 1, "user_answer": "C", "is_correct": 0},
    ]}
    text = build_history_text("Иван Иванов (ivan@x.ru)", sessions, answers,
                              {1: questions}, {1: "Стандарты.docx"})
    assert "1. ✓ Базовый 1?" in text
    assert "7. ✗ Экзамен 2?" in text        # exam q_id=1 → сквозной №7
    assert "(верно: A)" in text
    assert "Иван Иванов" in text


def test_history_truncates_sessions():
    sessions = [{"id": i, "course_id": 1, "state": "DONE",
                 "score_basic": 5, "score_exam": 8} for i in range(5)]
    text = build_history_text("x", sessions, {}, {1: _questions()}, {1: "Док"})
    assert "последние 3 сессии из 5" in text
