"""
app/state_machine.py — Dialog FSM for employee onboarding.

States: ROLE_SELECT → READING → BASIC_TEST → WAITING_HR → EXAM → DONE

ROLE_SELECT: сотрудник выбирает роль цифрой; RAG-ответы фильтруются по роли.
Команда «Роль» в READING — сменить роль (тестирование в разных ролях).

Entry point: process_message() — synchronous, designed to be called via asyncio.to_thread().
"""
import os
import re
import time

import httpx
from dotenv import load_dotenv

from app.db import (
    create_session,
    get_active_courses,
    get_course_by_id,
    get_course_questions,
    get_session,
    get_session_answers,
    is_employee_allowed,
    log_answer,
    update_session,
)
from app.gamification import post_exam_congratulation
from app.rag import answer as rag_answer
from app.roles import selectable_roles

load_dotenv()


def process_message(user_id: str, message: str, dialog_id: str,
                     chunks: list, embeddings) -> str:
    """
    Main entry point. Returns the text to send back to the user.
    All DB calls are sync; run this in asyncio.to_thread() from FastAPI.
    """
    session = get_session(user_id)

    if session is None:
        # Whitelist: доступ к обучению только у приглашённых HR (таблица employees;
        # legacy-юзеры с сессиями засеяны в init_db).
        if not is_employee_allowed(user_id):
            return (
                "🔒 Доступ к обучению открывает HR-менеджер.\n"
                "Обратись к нему, чтобы начать обучение."
            )
        active_courses = get_active_courses()
        if not active_courses:
            return (
                "Добро пожаловать! Активных курсов обучения пока нет.\n"
                "Обратитесь к HR-менеджеру для назначения обучения."
            )
        course = active_courses[0]
        if selectable_roles():
            session = create_session(user_id, dialog_id, course["id"],
                                     state="ROLE_SELECT")
            return _start_role_select()
        session = create_session(user_id, dialog_id, course["id"])
        return _start_reading(session, course)

    state = session["state"]

    if state == "ROLE_SELECT":
        return _handle_role_select(session, message)
    elif state == "READING":
        return _handle_reading(session, message, chunks, embeddings)
    elif state == "BASIC_TEST":
        return _handle_test(session, message, "basic")
    elif state == "WAITING_HR":
        return _handle_waiting_hr()
    elif state == "EXAM":
        return _handle_test(session, message, "exam")
    elif state == "DONE":
        return _handle_done(session)
    else:
        return f"Неизвестное состояние сессии ({state!r}). Обратитесь к HR."


def start_onboarding(user_id: str, dialog_id: str) -> str | None:
    """Автостарт после «Пригласить»: создать сессию и вернуть первое сообщение
    сотруднику (бот пишет первым). Sync — звать через asyncio.to_thread.

    None — сотрудник уже проходит обучение или активных курсов нет.
    """
    if get_session(user_id):
        return None
    courses = get_active_courses()
    if not courses:
        return None
    course = courses[0]
    # Зеркало ветки session is None в process_message
    if selectable_roles():
        create_session(user_id, dialog_id, course["id"], state="ROLE_SELECT")
        return _start_role_select()
    session = create_session(user_id, dialog_id, course["id"])
    return _start_reading(session, course)


# ── State handlers ────────────────────────────────────────────────────────────

def _format_role_list(options: list[tuple[str, str]]) -> str:
    return "\n".join(f"{i}. {name}" for i, (_rid, name) in enumerate(options, 1))


def _start_role_select() -> str:
    options = selectable_roles()
    return (
        "👋 Привет! Сначала выбери свою роль — буду отвечать только тем, "
        "что относится к твоей работе.\n\n"
        f"{_format_role_list(options)}\n\n"
        "Ответь цифрой (например: 1)."
    )


def _handle_role_select(session: dict, message: str) -> str:
    options = selectable_roles()
    course = get_course_by_id(session["course_id"])

    if not options:
        # Конфиг ролей опустел между сообщениями — не блокируем обучение
        update_session(session["id"], state="READING")
        return _start_reading(session, course)

    m = re.match(r"^\s*(\d+)\s*[.)]?\s*$", message)
    if not m or not (1 <= int(m.group(1)) <= len(options)):
        return (
            f"Пожалуйста, ответь цифрой от 1 до {len(options)}:\n\n"
            + _format_role_list(options)
        )

    role_id, role_label = options[int(m.group(1)) - 1]
    update_session(session["id"], state="READING", role=role_id)
    return f"✅ Твоя роль: *{role_label}*\n\n" + _start_reading(session, course)


def _start_reading(session: dict, course: dict) -> str:
    questions = get_course_questions(course["id"])
    summary = questions.get("course_summary", "")
    detail_url = course.get("doc_detail_url") or "(ссылка не найдена — спросите HR)"

    lines = [f"👋 Добро пожаловать! Начинаем обучение: *{course['doc_name']}*", ""]
    if summary:
        lines += [summary, ""]
    lines += [
        f"📄 Ссылка на документ: {detail_url}",
        "",
        "Прочитай документ и напиши *Готов*, когда будешь готов к тесту.",
        "Если есть вопросы по материалу — задавай, отвечу на основе стандартов.",
    ]
    return "\n".join(lines)


def _handle_reading(session: dict, message: str, chunks: list, embeddings) -> str:
    if message.strip().lower() == "роль":
        update_session(session["id"], state="ROLE_SELECT")
        return _start_role_select()

    if "готов" in message.lower():
        questions = get_course_questions(session["course_id"])
        if not questions.get("basic_questions"):
            return "Ошибка: вопросы курса не найдены. Обратитесь к HR."

        update_session(session["id"], state="BASIC_TEST", q_idx=0)

        first_q = questions["basic_questions"][0]
        return "✅ Отлично! Начинаем базовый тест — 5 вопросов.\n\n" + \
               format_question(first_q, 0, 5, "basic")

    text, _ = rag_answer(
        query=message,
        chunks=chunks,
        embeddings=embeddings,
        section_filter=None,
        answer_length="Стандартно",
        role_filter=session.get("role"),
    )
    return text


def _handle_test(session: dict, message: str, phase: str) -> str:
    questions = get_course_questions(session["course_id"])
    q_list = questions.get(f"{phase}_questions", [])
    total = len(q_list)
    q_idx = session["current_q_idx"]

    if q_idx >= total:
        # Guard: shouldn't happen in normal flow
        return _finish_phase(session, phase, questions)

    current_q = q_list[q_idx]
    letter = parse_answer(message)

    if letter is None:
        return (
            "Пожалуйста, ответь одной буквой: A, B, C или D.\n\n"
            + format_question(current_q, q_idx, total, phase)
        )

    is_correct = 1 if letter == current_q["correct"] else 0
    log_answer(session["id"], q_idx, phase, letter, is_correct)

    feedback = "✅ Верно!" if is_correct else f"❌ Неверно. Правильный ответ: *{current_q['correct']}*"
    explanation = current_q.get("explanation", "")
    if explanation:
        feedback += f"\n_{explanation}_"

    new_idx = q_idx + 1

    if new_idx < total:
        # Update score incrementally
        if phase == "basic":
            update_session(session["id"], state="BASIC_TEST", q_idx=new_idx,
                           score_basic=session["score_basic"] + is_correct)
        else:
            update_session(session["id"], state="EXAM", q_idx=new_idx,
                           score_exam=session["score_exam"] + is_correct)

        next_q = q_list[new_idx]
        return feedback + "\n\n" + format_question(next_q, new_idx, total, phase)

    # Last question answered — finish the phase
    return _finish_phase(session, phase, questions, last_feedback=feedback)


def _finish_phase(session: dict, phase: str, questions: dict,
                   last_feedback: str = "") -> str:
    answers = get_session_answers(session["id"], phase)
    correct_count = sum(1 for a in answers if a["is_correct"] == 1)

    prefix = (last_feedback + "\n\n") if last_feedback else ""

    if phase == "basic":
        total = len(questions.get("basic_questions", []))
        update_session(session["id"], state="WAITING_HR", q_idx=0,
                       score_basic=correct_count)
        notify_hr(
            f"📋 Сотрудник (ID: {session['user_id']}) завершил базовый тест.\n"
            f"Результат: {correct_count}/{total}\n\n"
            f"Чтобы допустить к экзамену, напиши:\n"
            f"Допустить {session['user_id']}"
        )
        return prefix + (
            f"🏁 Базовый тест завершён! Результат: *{correct_count}/{total}*\n\n"
            "Ожидаем решения HR о допуске к экзамену. Скоро получишь уведомление."
        )

    # Exam phase finished
    total = len(questions.get("exam_questions", []))
    update_session(session["id"], state="DONE", q_idx=0, score_exam=correct_count)

    basic_score = session["score_basic"]
    basic_total = len(questions.get("basic_questions", []))
    passed = correct_count >= round(total * 0.7)
    grade = "✅ Сдан" if passed else "❌ Не сдан"

    notify_hr(
        f"🎓 Сотрудник (ID: {session['user_id']}) завершил экзамен.\n"
        f"Базовый тест: {basic_score}/{basic_total}\n"
        f"Экзамен: {correct_count}/{total} — {grade}\n"
        f"Курс: {questions.get('doc_name', '')}"
    )
    if passed:
        # «Благодарность» в Ленту (№5). session["score_exam"] здесь ещё старый —
        # передаём свежий correct_count. Функция глотает ошибки: экзамен не роняем.
        post_exam_congratulation(session["user_id"], questions,
                                 correct_count, basic_score)
    return prefix + (
        f"🎓 Экзамен завершён!\n\n"
        f"Экзамен: *{correct_count}/{total}* — {grade}\n"
        f"Базовый тест: {basic_score}/{basic_total}\n\n"
        "Результаты отправлены HR. Спасибо!"
    )


def _handle_waiting_hr() -> str:
    return "⏳ Ожидаем решения HR о допуске к экзамену. Скоро получишь уведомление."


def _handle_done(session: dict) -> str:
    basic_answers = get_session_answers(session["id"], "basic")
    exam_answers = get_session_answers(session["id"], "exam")
    basic_score = sum(1 for a in basic_answers if a["is_correct"] == 1)
    exam_score = sum(1 for a in exam_answers if a["is_correct"] == 1)
    return (
        f"📊 Твои результаты обучения:\n"
        f"• Базовый тест: {basic_score}/{len(basic_answers)}\n"
        f"• Экзамен: {exam_score}/{len(exam_answers)}\n\n"
        "Обучение завершено. Для нового курса обратись к HR."
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_question(q: dict, idx: int, total: int, phase: str) -> str:
    label = "Базовый тест" if phase == "basic" else "Экзамен"
    lines = [f"📝 {label} — Вопрос {idx + 1}/{total}", "", q["text"], ""] + q["options"]
    return "\n".join(lines)


def parse_answer(message: str) -> str | None:
    """
    Extract A/B/C/D from a message. Handles:
    - Single Latin letter: "A", "b"
    - Single Cyrillic lookalike: "А", "В", "С", "Д"
    - Letter with separator: "A.", "A)", "A. текст", "А. текст"
    Returns uppercase Latin letter or None.
    """
    cyrillic_to_latin = {"А": "A", "В": "B", "С": "C", "Д": "D"}
    msg = message.strip()
    first = msg[0].upper() if msg else ""

    # Translate Cyrillic lookalike first
    first = cyrillic_to_latin.get(first, first)

    if first not in ("A", "B", "C", "D"):
        return None

    # Accept if it's just the letter, or followed by a separator
    rest = msg[1:].lstrip()
    if rest == "" or rest[0] in (".", ")", ":", " "):
        return first

    # Reject if letter is the start of a longer word (e.g. "Администратор")
    if rest and rest[0].isalpha():
        return None

    return first


def notify_hr(message: str) -> None:
    """Send a message to all HR users via the HR bot."""
    webhook_url = os.getenv("BITRIX_WEBHOOK_URL", "")
    hr_ids_str = os.getenv("HR_USER_IDS", "")
    # Use HR bot if configured, fall back to employee bot
    bot_id = os.getenv("HR_BOT_ID") or os.getenv("BOT_ID", "54849")
    # Proactive send (no inbound webhook) needs the bot's CLIENT_ID or Bitrix → 403.
    client_id = os.getenv("HR_CLIENT_ID", "")

    if not webhook_url or not hr_ids_str:
        print(f"[notify_hr] Config missing — message not sent: {message[:80]}")
        return

    hr_ids = [x.strip() for x in hr_ids_str.split(",") if x.strip()]
    for hr_id in hr_ids:
        # Same flapping network as _send — retry with backoff so HR notifications
        # aren't silently lost on a transient ConnectTimeout.
        for attempt in range(1, 6):
            try:
                resp = httpx.post(
                    webhook_url + "imbot.message.add",
                    json={"BOT_ID": bot_id, "DIALOG_ID": f"u{hr_id}",
                          "MESSAGE": message, "CLIENT_ID": client_id},
                    timeout=15.0,
                )
                print(f"[notify_hr] → HR {hr_id}: {resp.status_code} (attempt {attempt})")
                break
            except httpx.RequestError as exc:
                print(f"[notify_hr] retry {attempt}/5 for HR {hr_id}: {exc!r}")
                if attempt < 5:
                    time.sleep(2 * attempt)
