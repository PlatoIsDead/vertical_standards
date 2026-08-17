"""
app/hr_tools.py — чистые функции инструментов HR (доработка №3): правка вопросов
через чат, «История», «Отчёт». Без сети и БД (role_name читает только локальный
data/roles.json) — всё тестируется напрямую.

Нумерация вопросов в UI сквозная (смета): 1–5 = базовый тест, 6–15 = экзамен.
В answers.question_id хранится индекс ВНУТРИ фазы (0-based) — не путать.
"""
import json
import re

from app.course_generator import validate_question
from app.roles import role_name

# Кириллические двойники букв ответов — HR печатает по-русски
_CYR_TO_LAT = {"А": "A", "В": "B", "С": "C", "Д": "D"}

_OPTION_RE = re.compile(r"^([a-dавсд])[.)]\s*(.+)$", re.IGNORECASE)
_ANSWER_RE = re.compile(r"^ответ\s*[:\-—]\s*(.+)$", re.IGNORECASE)
_EXPLANATION_RE = re.compile(r"^пояснение\s*[:\-—]\s*(.+)$", re.IGNORECASE)

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
    "EXAM": "сдаёт экзамен",
}


def _norm_letter(raw: str) -> str:
    letter = raw.strip()[:1].upper()
    return _CYR_TO_LAT.get(letter, letter)


def resolve_question_ref(q_num: int) -> tuple[str, int]:
    """UI-номер 1–15 → (фаза, индекс внутри фазы). 1–5 базовые, 6–15 экзамен."""
    if 1 <= q_num <= 5:
        return "basic", q_num - 1
    if 6 <= q_num <= 15:
        return "exam", q_num - 6
    raise ValueError(f"Номер вопроса — от 1 до 15, получил {q_num}")


def ui_number(phase: str, question_id: int) -> int:
    """Обратный маппинг: (фаза, индекс) → сквозной номер 1–15."""
    return question_id + (1 if phase == "basic" else 6)


def question_by_ref(questions: dict, q_num: int) -> dict | None:
    try:
        phase, idx = resolve_question_ref(q_num)
    except ValueError:
        return None
    q_list = questions.get(f"{phase}_questions", [])
    return q_list[idx] if idx < len(q_list) else None


def correct_option(q: dict) -> str:
    """«A» → «A. Бежать» из options — HR видит текст правильного ответа
    («буковки», протокол 05.08). Кривой формат варианта → буква как есть."""
    prefix = q.get("correct", "") + "."
    return next((o.strip() for o in q.get("options", [])
                 if o.strip().upper().startswith(prefix)), q.get("correct", "?"))


def format_question_full(q: dict, q_num: int) -> str:
    phase, _ = resolve_question_ref(q_num)
    label = "базовый тест" if phase == "basic" else "экзамен"
    lines = [f"*Вопрос {q_num}* ({label}):", q["text"], ""] + list(q["options"])
    lines.append(f"Ответ: {q['correct']}")
    if q.get("explanation"):
        lines.append(f"Пояснение: {q['explanation']}")
    return "\n".join(lines)


def format_question_card(doc_name: str, q: dict, q_num: int) -> str:
    """Карточка вопроса «как видит сотрудник» + ✅ на правильном (17.08:
    «Вопросы N» листает карточки вместо простыни-ответника)."""
    phase, idx = resolve_question_ref(q_num)
    sub = (f"Базовый блок — вопрос {idx + 1}/5" if phase == "basic"
           else f"Экзамен — вопрос {idx + 1}/10")
    lines = [f"📋 *{doc_name}*", sub, "", f"{q_num}. {q['text']}", ""]
    correct_prefix = (q.get("correct") or "") + "."
    for o in q.get("options", []):
        o = str(o).strip()
        mark = " ✅" if o.upper().startswith(correct_prefix) else ""
        lines.append(o + mark)
    if q.get("explanation"):
        lines += ["", f"Пояснение: {q['explanation']}"]
    return "\n".join(lines)


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
    phase, idx = resolve_question_ref(q_num)
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
    """Этап сессии по-русски. Порог сдачи — round(total*0.7), как в _finish_phase."""
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
        questions = questions_by_course.get(s["course_id"], {})
        name = course_names.get(s["course_id"], f"курс №{s['course_id']}")
        lines.append(f"*«{name}»* — {_session_status(s, questions)}")
        answers = answers_by_session.get(s["id"], [])
        if not answers:
            lines.append("(ответов пока нет)")
        for a in answers:
            q_list = questions.get(f"{a['phase']}_questions", [])
            q = q_list[a["question_id"]] if a["question_id"] < len(q_list) else {}
            mark = "✓" if a["is_correct"] == 1 else "✗"
            line = (f"{ui_number(a['phase'], a['question_id'])}. {mark} "
                    f"{(q.get('text') or '')[:60]} — ответ {a['user_answer']}")
            if a["is_correct"] != 1:
                line += f" (верно: {q.get('correct', '?')})"
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
