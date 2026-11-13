"""
app/hr_tools.py — чистые функции инструментов HR (доработка №3): правка вопросов
через чат, «История», «Отчёт». Без сети и БД (role_name читает только локальный
data/roles.json) — всё тестируется напрямую.

Нумерация вопросов в UI сквозная: 1–B = базовый тест, B+1–B+E = экзамен,
где B/E — фактические длины (адаптивная генерация 31.08; дефолты 5/10 —
совместимость со старыми курсами). В answers.question_id хранится индекс
ВНУТРИ фазы (0-based) — не путать.
"""
import json
import re

from app import quiz

from app.course_generator import validate_question
from app.roles import role_name

# Кириллические двойники букв ответов — HR печатает по-русски
_CYR_TO_LAT = {"А": "A", "В": "B", "С": "C", "Д": "D"}

_OPTION_RE = re.compile(r"^([a-dавсд])[.)]\s*(.+)$", re.IGNORECASE)
_ANSWER_RE = re.compile(r"^ответ\s*[:\-—]\s*(.+)$", re.IGNORECASE)
_EXPLANATION_RE = re.compile(r"^пояснение\s*[:\-—]\s*(.+)$", re.IGNORECASE)
# «1. Текст вопроса» из карточки — HR копирует вопрос вместе с номером
_LEAD_NUM_RE = re.compile(r"^\d{1,2}[.)]\s*")

REPLACEMENT_TEMPLATE = (
    "Текст вопроса\n"
    "A. первый вариант\n"
    "B. второй вариант\n"
    "C. третий вариант\n"
    "D. четвёртый вариант\n"
    "Ответ: A\n"
    "Пояснение: почему верен (необязательно)"
)

_STATE_LABELS = {
    "ROLE_SELECT": "изучает материал",
    "READING": "изучает материал",
    "BASIC_TEST": "проходит базовый тест",
    "WAITING_HR": "ждёт допуска к экзамену",
    "EXAM_READY": "допущен к экзамену",
    "EXAM": "сдаёт экзамен",
    "PAUSED_BASIC": "базовый тест на паузе",
    "PAUSED_EXAM": "экзамен на паузе",
}


def _norm_letter(raw: str) -> str:
    letter = raw.strip()[:1].upper()
    return _CYR_TO_LAT.get(letter, letter)


def resolve_question_ref(q_num: int, basic_len: int = 5,
                         exam_len: int = 10) -> tuple[str, int]:
    """UI-номер → (фаза, индекс внутри фазы). 1–B базовые, B+1–B+E экзамен."""
    if 1 <= q_num <= basic_len:
        return "basic", q_num - 1
    if basic_len < q_num <= basic_len + exam_len:
        return "exam", q_num - basic_len - 1
    raise ValueError(f"Номер вопроса — от 1 до {basic_len + exam_len}, "
                     f"получил {q_num}")


def ui_number(phase: str, question_id: int, basic_len: int = 5) -> int:
    """Обратный маппинг: (фаза, индекс) → сквозной номер."""
    return question_id + (1 if phase == "basic" else basic_len + 1)


def phase_lens(questions: dict) -> tuple[int, int]:
    """(len basic, len exam) курса; пустой dict → легаси-дефолт 5/10."""
    basic = questions.get("basic_questions")
    exam = questions.get("exam_questions")
    if basic is None and exam is None:
        return 5, 10
    return len(basic or []), len(exam or [])


def question_by_ref(questions: dict, q_num: int) -> dict | None:
    try:
        phase, idx = resolve_question_ref(q_num, *phase_lens(questions))
    except ValueError:
        return None
    q_list = questions.get(f"{phase}_questions", [])
    return q_list[idx] if idx < len(q_list) else None


def correct_option(q: dict) -> str:
    """«A» → «A. Бежать» из options — HR видит текст правильного ответа
    («буковки», протокол 05.08). Кривой формат варианта → буква как есть."""
    view = quiz.question_view(None, q, "exam", 0)
    if view.valid:
        return f"{view.correct_display}. {view.correct_body}"
    # HR may inspect a damaged legacy question to repair it.
    correct = str(q.get("correct", "?"))
    options = q.get("options")
    if isinstance(options, list):
        prefix = correct.upper() + "."
        return next((o.strip() for o in options if isinstance(o, str)
                     and o.strip().upper().startswith(prefix)), correct)
    return correct


def format_question_full(q: dict, q_num: int, basic_len: int = 5,
                         exam_len: int = 10) -> str:
    resolve_question_ref(q_num, basic_len, exam_len)
    label = "тест"
    lines = [f"*Вопрос {q_num}* ({label}):", q["text"], ""] + list(q["options"])
    lines.append(f"Ответ: {q['correct']}")
    if q.get("explanation"):
        lines.append(f"Пояснение: {q['explanation']}")
    return "\n".join(lines)


def format_question_card(doc_name: str, q: dict, q_num: int,
                         basic_len: int = 5, exam_len: int = 10) -> str:
    """Карточка вопроса «как видит сотрудник» + ✅ на правильном (17.08:
    «Вопросы N» листает карточки вместо простыни-ответника)."""
    phase, idx = resolve_question_ref(q_num, basic_len, exam_len)
    sub = f"Тест — вопрос {q_num}/{basic_len + exam_len}"
    lines = [f"📋 *{doc_name}*", sub, "", f"{q_num}. {q['text']}", ""]
    view = quiz.question_view(None, q, phase, idx)
    for option in view.options:
        mark = " ✅" if view.valid and option.startswith(view.correct_display + ".") else ""
        lines.append(option + mark)
    if q.get("explanation"):
        lines += ["", f"Пояснение: {q['explanation']}"]
    return "\n".join(lines)


def format_question_editable(q: dict) -> str:
    """Текущий вопрос в формате шаблона — HR копирует, правит строку и шлёт
    обратно одним сообщением (02.09: 6-шаговый визард был слишком долгим)."""
    lines = [str(q.get("text", "")).strip()]
    lines += [str(o).strip() for o in q.get("options", [])]
    lines.append(f"Ответ: {q.get('correct', '')}")
    if q.get("explanation"):
        lines.append(f"Пояснение: {q['explanation']}")
    return "\n".join(lines)


def parse_partial_replacement(raw: str) -> dict:
    """Разбор ЛЮБОГО подмножества полей вопроса из сообщения HR (02.09).

    Понимает и целый блок, и точечную правку:
      «B. новый вариант» — только вариант B;
      «Ответ: C» — только правильный ответ;
      «Пояснение: …» — только пояснение;
      строка без префикса — новый текст вопроса (ведущая нумерация «1. »
      из карточки срезается — HR копирует вопрос вместе с номером).

    Возвращает {} — не распознано ничего. Недостающие поля вызывающий берёт
    из текущего вопроса: раньше блок без строки «Ответ:» не разбирался вовсе
    и молча уезжал в текст вопроса (живой прогон 02.09).
    """
    text_lines: list[str] = []
    options: dict[str, str] = {}
    correct, explanation = None, None
    seen_option = False

    for line in (ln.strip() for ln in raw.splitlines()):
        if not line:
            continue
        m = _OPTION_RE.match(line)
        if m:
            options[_norm_letter(m.group(1))] = m.group(2).strip()
            seen_option = True
            continue
        m = _ANSWER_RE.match(line)
        if m:
            correct = _norm_letter(m.group(1))
            continue
        m = _EXPLANATION_RE.match(line)
        if m:
            explanation = m.group(1).strip()
            continue
        if not seen_option:      # текст вопроса — только до первого варианта
            text_lines.append(line)

    out: dict = {}
    if text_lines:
        head = _LEAD_NUM_RE.sub("", text_lines[0], count=1).strip()
        out["text"] = " ".join([head] + text_lines[1:]).strip()
    if options:
        out["options"] = options
    if correct in ("A", "B", "C", "D"):
        out["correct"] = correct
    if explanation is not None:
        out["explanation"] = explanation
    return out


def merge_replacement(draft: dict, patch: dict) -> dict:
    """Наложить распознанную правку на текущий вопрос (не мутируя draft)."""
    out = {"text": draft["text"], "options": list(draft["options"]),
           "correct": draft["correct"],
           "explanation": draft.get("explanation", "")}
    if "text" in patch:
        out["text"] = patch["text"]
    for letter, body in (patch.get("options") or {}).items():
        out["options"]["ABCD".index(letter)] = f"{letter}. {body}"
    if "correct" in patch:
        out["correct"] = patch["correct"]
    if "explanation" in patch:
        out["explanation"] = patch["explanation"]
    return out


def parse_replacement(raw: str) -> dict | None:
    """Разбор замены вопроса из одного сообщения HR. None = формат не распознан.

    Формат: текст (можно в несколько строк) → 4 варианта «A. …»/«A) …»
    (кириллические А/В/С/Д допустимы) → «Ответ: X» → опционально «Пояснение: …».
    Текст вопроса собирается только ДО первого варианта.
    """
    text_lines: list[str] = []
    options: dict[str, str] = {}
    correct, explanation = None, ""

    for line in (ln.strip() for ln in raw.splitlines()):
        if not line:
            continue
        m = _OPTION_RE.match(line)
        if m:
            options[_norm_letter(m.group(1))] = m.group(2).strip()
            continue
        m = _ANSWER_RE.match(line)
        if m:
            correct = _norm_letter(m.group(1))
            continue
        m = _EXPLANATION_RE.match(line)
        if m:
            explanation = m.group(1).strip()
            continue
        if not options:  # текст вопроса — только до первого варианта
            text_lines.append(line)

    if not text_lines or sorted(options) != ["A", "B", "C", "D"]:
        return None
    if correct not in ("A", "B", "C", "D"):
        return None
    return {
        "text": " ".join(text_lines),
        "options": [f"{letter}. {options[letter]}" for letter in ("A", "B", "C", "D")],
        "correct": correct,
        "explanation": explanation,
    }


def apply_replacement(questions: dict, q_num: int, repl: dict) -> dict:
    """Замена 1:1 вопроса q_num (id исходного вопроса сохраняется). ValueError
    при невалидном номере/вопросе. Количество вопросов не меняется никогда."""
    phase, idx = resolve_question_ref(q_num, *phase_lens(questions))
    q_list = questions.get(f"{phase}_questions", [])
    if idx >= len(q_list):
        raise ValueError(f"В курсе нет вопроса №{q_num}")
    new_q = {
        "id": q_list[idx].get("id", idx),
        "text": repl["text"],
        "options": repl["options"],
        "correct": repl["correct"],
        "explanation": repl["explanation"],
    }
    validate_question(new_q)
    q_list[idx] = new_q
    return questions


# ── История и Отчёт ──────────────────────────────────────────────────────────

def _session_status(session: dict, questions: dict) -> str:
    """Этап и результат с учётом версии теста и сохранённого снимка."""
    if quiz.unified(session):
        if session["state"] == "DONE":
            score, total, passed = quiz.result(session, questions)
            verdict = "✅ сдан" if passed else "❌ не сдан"
            return f"завершил — {verdict} (тест {score}/{total})"
        if session["state"] in quiz.QUIZ_STATES:
            return "тест на паузе" if quiz.is_paused(session["state"]) else "проходит тест"
    if session["state"] == "DONE":
        basic_total = len(questions.get("basic_questions", []))
        exam_total = len(questions.get("exam_questions", []))
        passed = exam_total and session["score_exam"] >= round(exam_total * 0.7)
        verdict = "✅ сдан" if passed else "❌ не сдан"
        return (f"завершил — {verdict} (базовый {session['score_basic']}/{basic_total}, "
                f"экзамен {session['score_exam']}/{exam_total})")
    return _STATE_LABELS.get(session["state"], session["state"])


def build_history_text(employee_label: str, sessions: list[dict],
                       answers_by_session: dict[int, list[dict]],
                       questions_by_course: dict[int, dict],
                       course_names: dict[int, str]) -> str:
    """Последние 3 сессии сотрудника с ответами: сквозной номер, ✓/✗, верный ответ."""
    lines = [f"📖 История обучения: {employee_label}", ""]
    for s in sessions[:3]:
        questions = quiz.session_questions(s, questions_by_course.get(s["course_id"], {}))
        name = course_names.get(s["course_id"], f"курс №{s['course_id']}")
        lines.append(f"*«{name}»* — {_session_status(s, questions)}")
        answers = answers_by_session.get(s["id"], [])
        if not answers:
            lines.append("(ответов пока нет)")
        for a in answers:
            q_list = quiz.phase_questions(s, questions, a["phase"])
            q = q_list[a["question_id"]] if a["question_id"] < len(q_list) else {}
            mark = "✓" if a["is_correct"] == 1 else "✗"
            b_len = len(questions.get("basic_questions", [])) or 5
            line = (f"{a['question_id'] + 1 if quiz.unified(s) else ui_number(a['phase'], a['question_id'], b_len)}. {mark} "
                    f"{(q.get('text') or '')[:60]} — ответ {a['user_answer']}")
            if a["is_correct"] != 1:
                view = quiz.question_view(None, q, a["phase"], a["question_id"])
                correct = view.correct_canonical if view.valid else "?"
                line += f" (верно: {correct})"
            lines.append(line)
        lines.append("")
    if len(sessions) > 3:
        lines.append(f"…показаны последние 3 сессии из {len(sessions)}")
    return "\n".join(lines).strip()


def _report_line(r: dict, employees_by_uid: dict[str, dict]) -> str:
    emp = employees_by_uid.get(str(r["user_id"]))
    if emp:
        inner = ", ".join(x for x in (emp.get("work_position"),
                                      emp.get("email")) if x)
        label = f"{emp.get('full_name') or r['user_id']} ({inner or '—'})"
    else:
        label = f"ID {r['user_id']}"  # legacy-сессия без записи в employees
    try:
        questions = json.loads(r.get("questions_json") or "{}")
    except json.JSONDecodeError:
        questions = {}
    date = (r.get("updated_at") or "")[:10]
    line = f"• {label} — «{r['doc_name']}»: {_session_status(r, questions)}"
    if date:
        line += f", {date}"
    return line


def build_report_text(rows: list[dict], employees_by_uid: dict[str, dict]) -> str:
    """Сводка по последним 30 сессиям, сгруппированная СЕКЦИЯМИ по отделам
    (роль сессии; роль = «отдел» до №8 — протокол 05.08 «аналитика по
    отделам»). «Без роли» — последней. Роль в строке не дублируется."""
    if not rows:
        return "Сессий обучения пока нет."
    shown = rows[:30]
    groups: dict[str, list[dict]] = {}
    for r in shown:
        groups.setdefault(r.get("role") or "", []).append(r)
    ordered = sorted((k for k in groups if k), key=role_name)
    if "" in groups:
        ordered.append("")
    lines = ["📊 Отчёт по обучению:", ""]
    for role in ordered:
        lines.append(f"— {role_name(role) if role else 'Без роли'} —")
        lines += [_report_line(r, employees_by_uid) for r in groups[role]]
        lines.append("")
    if len(rows) > 30:
        lines.append(f"…показаны последние 30 сессий из {len(rows)}")
    return "\n".join(lines).strip()
