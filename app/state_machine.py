"""
app/state_machine.py — Dialog FSM for employee onboarding.

States: ROLE_SELECT → READING → BASIC_TEST → WAITING_HR → EXAM → DONE

ROLE_SELECT: сотрудник выбирает роль цифрой; RAG-ответы фильтруются по роли.
Команда «Роль» в READING — сменить роль (тестирование в разных ролях).

№11: курс назначается ПОСЛЕ определения роли (память прошлой сессии или меню) —
первый активный непройденный курс, чьи роли (префиксы в doc_name) пересекаются
с {роль, all_staff}. До выбора роли сессия живёт с сентинелом course_id=0.

Entry point: process_message() — synchronous, designed to be called via asyncio.to_thread().
"""
import json
import os
import re
import time

import httpx
from dotenv import load_dotenv

from app.db import (
    create_session,
    delete_session_answers,
    get_active_courses,
    get_course_by_id,
    get_course_questions,
    get_employee,
    get_last_done_session,
    get_session,
    get_session_answers,
    get_session_by_id,
    get_sessions_by_user,
    get_user_departments,
    is_employee_allowed,
    log_answer,
    update_session,
)
from app.gamification import post_exam_congratulation
from app.rag import answer as rag_answer
from app.roles import (
    ALL_STAFF,
    display_name,
    load_roles_config,
    parse_filename,
    role_for_departments,
    role_name,
    selectable_roles,
)

load_dotenv()

# Чат Bitrix НЕ рендерит markdown (протокол 05.08, «артефакты-буковки») —
# конвертация *x* → [b]x[/b] СТРОГО на выходе (_send / notify_hr), внутренние
# тексты остаются со звёздочками. Незакрытая * и переносы внутри не трогаются.
_BOLD_RE = re.compile(r"\*([^*\n]+)\*")


def md_to_bb(text: str) -> str:
    """*жирный* → [b]жирный[/b] для imbot.message.add (BB-код Bitrix)."""
    return _BOLD_RE.sub(r"[b]\1[/b]", text)


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
        # №9: пересдача и развилка после провала — ДО назначения нового курса
        # (иначе любое сообщение молча назначит следующий курс, и «Пересдать»
        # уйдёт в RAG).
        cmd = message.strip().lower()
        if cmd in ("пересдать", "пересдача"):
            return _handle_retake(user_id)
        if cmd != "далее":
            fork = _retake_fork_text(user_id)
            if fork:
                return fork
        active_courses = get_active_courses()
        if not active_courses:
            return (
                "Добро пожаловать! Активных курсов обучения пока нет.\n"
                "Обратитесь к HR-менеджеру для назначения обучения."
            )
        # №11/№8: курс — после роли. Приоритет: отдел Битрикса → память
        # прошлой сессии → меню; до выбора сессия с сентинелом course_id=0.
        if selectable_roles():
            auto_role = _role_from_profile(user_id)
            if auto_role:
                return _assign_course(user_id, dialog_id, auto_role, None,
                                      by_department=True)
            role = _last_known_role(user_id)
            if role:
                return _assign_course(user_id, dialog_id, role, None,
                                      remembered=True)
            create_session(user_id, dialog_id, 0, state="ROLE_SELECT")
            return _start_role_select()
        course = pick_course_for_role(active_courses, None,
                                      _done_course_ids(user_id))
        if course is None:
            return ("🎓 Все активные курсы уже пройдены. "
                    "За новым обучением обратись к HR.")
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
        return _handle_waiting_hr(session, message)
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
    # Зеркало ветки session is None в process_message (№11/№8)
    if selectable_roles():
        auto_role = _role_from_profile(user_id)
        if auto_role:
            return _assign_course(user_id, dialog_id, auto_role, None,
                                  by_department=True)
        role = _last_known_role(user_id)
        if role:
            return _assign_course(user_id, dialog_id, role, None, remembered=True)
        create_session(user_id, dialog_id, 0, state="ROLE_SELECT")
        return _start_role_select()
    course = pick_course_for_role(courses, None, _done_course_ids(user_id))
    if course is None:
        return ("🎓 Все активные курсы уже пройдены. "
                "За новым обучением обратись к HR.")
    session = create_session(user_id, dialog_id, course["id"])
    return _start_reading(session, course)


# ── Пересдача экзамена (№9) ──────────────────────────────────────────────────

def _exam_pass_threshold(questions: dict) -> int:
    return round(len(questions.get("exam_questions", [])) * 0.7)


def _retake_fork_text(user_id: str) -> str | None:
    """Развилка после провала: последняя завершённая сессия не сдана →
    предложить «Пересдать / Далее» вместо молчаливого назначения следующего
    курса (№11 назначал бы его любым сообщением)."""
    last = get_last_done_session(user_id)
    if last is None:
        return None
    questions = get_course_questions(last["course_id"])
    exam_q = questions.get("exam_questions", [])
    if not exam_q or last["score_exam"] >= _exam_pass_threshold(questions):
        return None
    course = get_course_by_id(last["course_id"])
    name = display_name(course["doc_name"]) if course else "прошлый курс"
    return (f"📝 Ты не сдал экзамен «{name}» "
            f"({last['score_exam']}/{len(exam_q)}).\n"
            "Напиши *Пересдать* — пройти экзамен ещё раз, "
            "или *Далее* — перейти к следующему курсу.")


def _handle_retake(user_id: str) -> str:
    last = get_last_done_session(user_id)
    if last is None:
        return ("У тебя нет завершённого экзамена. Напиши любое сообщение, "
                "чтобы начать обучение.")
    questions = get_course_questions(last["course_id"])
    exam_q = questions.get("exam_questions", [])
    if not exam_q:
        return "Ошибка: вопросы курса не найдены. Обратитесь к HR."
    if last["score_exam"] >= _exam_pass_threshold(questions):
        return (f"🎉 Экзамен уже сдан ({last['score_exam']}/{len(exam_q)}). "
                "Пересдача не нужна.")
    # ЛОВУШКА answers: _finish_phase считает ВСЕ строки фазы — без удаления
    # старых ответов две попытки схлопнулись бы в один счёт.
    delete_session_answers(last["id"], "exam")
    update_session(last["id"], state="EXAM", q_idx=0, score_exam=0)
    return ("🔁 Пересдача экзамена — вперёд!\n\n"
            + format_question(exam_q[0], 0, len(exam_q), "exam"))


# ── Назначение курса по роли (№11: префиксы в имени файла) ────────────────────

def course_roles(course: dict) -> list[str]:
    """Роли курса — на лету из имени документа; без префиксов = для всех."""
    return parse_filename(course["doc_name"])["roles"] or [ALL_STAFF]


def pick_course_for_role(courses: list[dict], role_id: str | None,
                         done_ids: set) -> dict | None:
    """Первый непройденный курс для роли (порядок = порядок активации).

    role_id None — легаси-режим без конфига ролей: любой непройденный."""
    for c in courses:
        if c["id"] in done_ids:
            continue
        if role_id is None or {role_id, ALL_STAFF} & set(course_roles(c)):
            return c
    return None


def _done_course_ids(user_id: str) -> set:
    return {s["course_id"] for s in get_sessions_by_user(user_id)
            if s["state"] == "DONE"}


def _last_known_role(user_id: str) -> str | None:
    """Роль из последней сессии, если она ещё существует в реестре ролей."""
    valid = load_roles_config().get("roles", {})
    for s in get_sessions_by_user(user_id):
        if s.get("role") and s["role"] in valid:
            return s["role"]
    return None


def _live_departments(user_id: str) -> list | None:
    """user.get по ID (сотрудник вне WATCH_DEPARTMENT_IDS не попадает в
    seen_portal_users). Любой сбой → None: флап сети не ломает онбординг."""
    webhook_url = os.getenv("BITRIX_WEBHOOK_URL", "")
    if not webhook_url:
        return None
    try:
        resp = httpx.post(webhook_url + "user.get",
                          json={"ID": str(user_id)}, timeout=15.0)
        result = resp.json().get("result", [])
        if result:
            return list(result[0].get("UF_DEPARTMENT") or [])
    except Exception as exc:
        print(f"[autorole] user.get failed for {user_id}: {exc!r}")
    return None


def _role_from_profile(user_id: str) -> str | None:
    """№8: авто-роль из отдела Битрикса. ГЛАВНЕЕ памяти сессии (перевод в
    другой отдел должен сменить роль). Пустой маппинг departments → None
    без сетевых вызовов."""
    if not load_roles_config().get("departments"):
        return None
    deps_json = get_user_departments(user_id)
    if deps_json is not None:
        try:
            deps = json.loads(deps_json)
        except (ValueError, TypeError):
            deps = []
    else:
        deps = _live_departments(user_id) or []
    return role_for_departments(deps)


def _assign_course(user_id: str, dialog_id: str, role_id: str,
                   session: dict | None, remembered: bool = False,
                   by_department: bool = False) -> str:
    """Назначить первый подходящий курс (роль/all_staff, непройденный).

    session — живая ROLE_SELECT-сессия (course_id=0) либо None (роль вспомнили
    из прошлой сессии, сессии ещё нет)."""
    course = pick_course_for_role(get_active_courses(), role_id,
                                  _done_course_ids(user_id))
    if course is None:
        # Маркер «курсов нет» (course_id=0, DONE, role) гасит повторный спам HR
        # на каждое сообщение сотрудника.
        last = get_sessions_by_user(user_id)
        already_flagged = (bool(last) and last[0]["course_id"] == 0
                           and last[0]["state"] == "DONE"
                           and last[0].get("role") == role_id)
        if session:
            update_session(session["id"], state="DONE", role=role_id)
        elif not already_flagged:
            marker = create_session(user_id, dialog_id, 0, state="DONE")
            update_session(marker["id"], role=role_id)
        if not already_flagged:
            notify_hr(
                f"👀 Сотрудник (ID: {user_id}) с ролью «{role_name(role_id)}» "
                "запросил обучение — подходящих активных курсов нет."
            )
        return ("📚 Для твоей роли пока нет назначенных курсов. "
                "HR уже в курсе — получишь сообщение, когда курс появится.")
    if session:
        update_session(session["id"], state="READING", role=role_id,
                       course_id=course["id"])
        session = get_session_by_id(session["id"])
    else:
        session = create_session(user_id, dialog_id, course["id"])
        update_session(session["id"], role=role_id)
    role_line = f"✅ Твоя роль: *{role_name(role_id)}*"
    if remembered:
        role_line += " (помню с прошлого обучения; сменить — команда «Роль»)"
    elif by_department:
        role_line += " (по отделу; сменить — команда «Роль»)"
    return role_line + "\n\n" + _start_reading(session, course)


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

    if not options:
        # Конфиг ролей опустел между сообщениями — не блокируем обучение
        course = (get_course_by_id(session["course_id"])
                  if session["course_id"] else None)
        if course is None:
            course = pick_course_for_role(
                get_active_courses(), None, _done_course_ids(session["user_id"]))
        if course is None:
            update_session(session["id"], state="DONE")
            return ("Активных курсов обучения пока нет.\n"
                    "Обратитесь к HR-менеджеру для назначения обучения.")
        update_session(session["id"], state="READING", course_id=course["id"])
        return _start_reading(session, course)

    m = re.match(r"^\s*(\d+)\s*[.)]?\s*$", message)
    if not m or not (1 <= int(m.group(1)) <= len(options)):
        return (
            f"Пожалуйста, ответь цифрой от 1 до {len(options)}:\n\n"
            + _format_role_list(options)
        )

    role_id, role_label = options[int(m.group(1)) - 1]
    if session["course_id"]:
        # Смена роли командой «Роль»: курс уже назначен — НЕ переназначаем
        # (сессия привязана; тестовый инструмент для просмотра других ролей)
        course = get_course_by_id(session["course_id"])
        if course:
            update_session(session["id"], state="READING", role=role_id)
            return f"✅ Твоя роль: *{role_label}*\n\n" + _start_reading(session, course)
    return _assign_course(session["user_id"], session["dialog_id"],
                          role_id, session)


def _start_reading(session: dict, course: dict) -> str:
    questions = get_course_questions(course["id"])
    summary = questions.get("course_summary", "")
    detail_url = course.get("doc_detail_url") or "(ссылка не найдена — спросите HR)"

    lines = [f"👋 Добро пожаловать! Начинаем обучение: "
             f"*{display_name(course['doc_name'])}*", ""]
    if summary:
        lines += [summary, ""]
    lines += [
        f"📄 Ссылка на документ: {detail_url}",
        "",
        "Прочитай документ и напиши *Готов*, когда будешь готов к тесту.",
        "Если есть вопросы по материалу — задавай, отвечу на основе стандартов.",
        "",
        "Команды: *Готов* — начать тест · *Мои курсы* — список курсов · "
        "*Роль* — сменить роль.",
    ]
    return "\n".join(lines)


def my_courses(user_id: str, role_id: str | None) -> tuple[str, list[dict]]:
    """«Мои курсы»: текст списка + курсы, ДОСТУПНЫЕ к выбору (не текущий,
    не пройденные). Нумерация в тексте = индексы списка — единый источник
    для «Выбрать N» (протокол 05.08: выбор курса сотрудником)."""
    courses = get_active_courses()
    done = _done_course_ids(user_id)
    session = get_session(user_id)
    current_id = session["course_id"] if session else 0
    mine = [c for c in courses
            if role_id is None or {role_id, ALL_STAFF} & set(course_roles(c))]
    if not mine:
        return "Для твоей роли пока нет активных курсов.", []
    selectable = [c for c in mine
                  if c["id"] != current_id and c["id"] not in done]
    lines = ["📚 Твои курсы:", ""]
    for c in mine:
        name = display_name(c["doc_name"])
        if c["id"] == current_id:
            lines.append(f"▶️ {name} — проходишь сейчас")
        elif c["id"] in done:
            lines.append(f"✅ {name} — пройден")
        else:
            lines.append(f"{selectable.index(c) + 1}. ⏳ {name}")
    if selectable:
        lines += ["", "Переключиться на другой курс: напиши *Выбрать {номер}*."]
    return "\n".join(lines), selectable


def _my_courses_text(user_id: str, role_id: str | None) -> str:
    return my_courses(user_id, role_id)[0]


_MENU_COMMANDS = ("мои курсы", "курсы", "меню")
_SWITCH_RE = re.compile(r"^выбрать\s+(\d+)$")


def _handle_course_switch(session: dict, n: int) -> str:
    """«Выбрать N»: переключение текущей READING-сессии на другой курс.
    Старый курс НЕ помечается пройденным (done — только через экзамен)."""
    text, selectable = my_courses(session["user_id"], session.get("role"))
    if not selectable:
        return text
    if not (1 <= n <= len(selectable)):
        return f"Такого номера нет.\n\n{text}"
    course = selectable[n - 1]
    update_session(session["id"], course_id=course["id"],
                   state="READING", q_idx=0)
    return "🔄 Переключил курс.\n\n" + _start_reading(session, course)


def _handle_reading(session: dict, message: str, chunks: list, embeddings) -> str:
    cmd = message.strip().lower()
    if cmd in _MENU_COMMANDS:
        return _my_courses_text(session["user_id"], session.get("role"))
    m_switch = _SWITCH_RE.match(cmd)
    if m_switch:
        return _handle_course_switch(session, int(m_switch.group(1)))
    if cmd in ("пересдать", "пересдача"):
        # Реактивация DONE-сессии при живой текущей дала бы ДВЕ активные —
        # get_session вернул бы не ту. Пересдача — только между курсами.
        return ("Сначала закончи текущий курс — пересдать прошлый экзамен "
                "можно будет после него.")
    if cmd == "роль":
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

    if message.strip().lower().startswith("выбрать"):
        return ("⏳ Сначала закончи текущий тест — ответь на вопрос буквой A–D.\n\n"
                + format_question(current_q, q_idx, total, phase))

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


def _employee_label(user_id: str) -> str:
    """«Иван Иванов, Администратор (ID: 500)» для уведомлений HR; ID остаётся
    всегда — команды «Допустить/История» работают по нему."""
    emp = get_employee(user_id)
    if emp and emp.get("full_name"):
        label = emp["full_name"]
        if emp.get("work_position"):
            label += f", {emp['work_position']}"
        return f"{label} (ID: {user_id})"
    return f"(ID: {user_id})"


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
            f"📋 Сотрудник {_employee_label(session['user_id'])} "
            f"завершил базовый тест.\n"
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
        f"🎓 Сотрудник {_employee_label(session['user_id'])} завершил экзамен.\n"
        f"Базовый тест: {basic_score}/{basic_total}\n"
        f"Экзамен: {correct_count}/{total} — {grade}\n"
        f"Курс: {questions.get('doc_name', '')}"
    )
    if passed:
        # «Благодарность» в Ленту (№5). session["score_exam"] здесь ещё старый —
        # передаём свежий correct_count. Функция глотает ошибки: экзамен не роняем.
        post_exam_congratulation(session["user_id"], questions,
                                 correct_count, basic_score)
    retake_hint = ("" if passed else
                   "\nНапиши *Пересдать*, чтобы пройти экзамен ещё раз.")
    return prefix + (
        f"🎓 Экзамен завершён!\n\n"
        f"Экзамен: *{correct_count}/{total}* — {grade}\n"
        f"Базовый тест: {basic_score}/{basic_total}\n\n"
        "Результаты отправлены HR. Спасибо!" + retake_hint
    )


def _handle_waiting_hr(session: dict, message: str) -> str:
    cmd = message.strip().lower()
    if cmd in _MENU_COMMANDS:
        return _my_courses_text(session["user_id"], session.get("role"))
    if cmd.startswith("выбрать"):
        return ("⏳ Дождись решения HR по текущему курсу — "
                "потом можно будет переключиться.")
    return ("⏳ Ожидаем решения HR о допуске к экзамену. Скоро получишь уведомление.\n"
            "Посмотреть свои курсы: *Мои курсы*")


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
                          "MESSAGE": md_to_bb(message), "CLIENT_ID": client_id},
                    timeout=15.0,
                )
                print(f"[notify_hr] → HR {hr_id}: {resp.status_code} (attempt {attempt})")
                break
            except httpx.RequestError as exc:
                print(f"[notify_hr] retry {attempt}/5 for HR {hr_id}: {exc!r}")
                if attempt < 5:
                    time.sleep(2 * attempt)
