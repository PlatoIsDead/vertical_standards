"""
app/state_machine.py — Dialog FSM for employee onboarding.

States: ROLE_SELECT → READING → BASIC_TEST → WAITING_HR → EXAM → DONE

ROLE_SELECT: сотрудник выбирает роль цифрой; RAG-ответы фильтруются по роли.
Команда «Роль» в READING направляет сотрудника к HR.

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
    bump_attempt,
    create_session,
    delete_session_answers,
    get_active_courses,
    get_course_by_doc_name,
    get_course_by_id,
    get_course_questions,
    get_employee,
    get_last_done_session,
    get_open_session_for_course,
    get_session,
    get_session_answers,
    get_session_by_id,
    get_sessions_by_user,
    get_user_departments,
    is_employee_allowed,
    log_answer,
    update_session,
)
from app import kb_tracker, keyboards, quiz
from app.links import doc_url
from app.gamification import post_exam_congratulation
from app.rag import answer as rag_answer
from app.roles import (
    ALL_STAFF,
    display_name,
    load_roles_config,
    parse_filename,
    role_for_departments,
    role_matches,
    role_name,
    role_sees_all,
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


_HELP_TEXT = (
    "🤖 Я — бот стандартов «Вертикали». Что я умею:\n"
    "• Отвечаю на вопросы по стандартам твоего отдела — просто напиши вопрос\n"
    "• *Мои курсы* — твоё обучение и переключение между курсами\n"
    "• *Документы* — стандарты твоего отдела со ссылками\n"
    "• *Готов* — начать тест по текущему курсу · *Выйти* — пауза · "
    "*Продолжить* — вернуться к тесту\n"
    "• *Помощь* — показать эту подсказку ещё раз"
)

_HELP_COMMANDS = ("помощь", "help", "старт", "инструкция")

# 21.08 (Евгения: «инструкция уровня „нажми эту кнопку“», обещание «после
# каждой команды — пояснение»): хвост-навигатор по ТЕКУЩЕМУ состоянию
_STATE_HINTS = {
    "READING": ("Что дальше: *Готов* — начать тест · *Мои курсы* · "
                "*Документы* · или просто задай вопрос по стандарту"),
    "BASIC_TEST": ("Что дальше: ответь буквой A–D · можно задать вопрос по "
                   "стандартам · *Выйти* — пауза теста"),
    "EXAM": ("Что дальше: ответь буквой A–D · можно задать вопрос по "
             "стандартам · *Выйти* — пауза экзамена"),
    "PAUSED_BASIC": ("Что дальше: *Продолжить* — вернуться к тесту · "
                     "*Мои курсы* · *Документы*"),
    "PAUSED_EXAM": ("Что дальше: *Продолжить* — вернуться к экзамену · "
                    "*Мои курсы* · *Документы*"),
    "WAITING_HR": ("Что дальше: жди допуска HR · *Мои курсы* · *Документы* · "
                   "задавай вопросы по стандартам"),
    "EXAM_READY": ("Что дальше: *Начать* — начать экзамен · *Мои курсы* · "
                   "*Документы* · задавай вопросы по стандартам"),
    None: "Что дальше: *Мои курсы* · *Документы* · *Помощь*",
}

# Тексты, которые уже сами всё объясняют — хвост не дублируем
_HINT_SKIP_MARKERS = ("Что дальше", "Команды:", "Что я умею",
                      "ответь одной буквой", "Какой тест начинаем")


def _with_hint(text: str, user_id: str) -> str:
    if any(m in text for m in _HINT_SKIP_MARKERS):
        return text
    session = get_session(user_id)          # СВЕЖЕЕ состояние (после команды)
    state = session["state"] if session else None
    hint = _STATE_HINTS.get(state)
    return f"{text}\n\n{hint}" if hint else text


def process_message(user_id: str, message: str, dialog_id: str,
                    chunks: list, embeddings) -> str:
    """
    Main entry point. Returns the text to send back to the user.
    All DB calls are sync; run this in asyncio.to_thread() from FastAPI.
    """
    cmd = message.strip().lower()
    if cmd in _HELP_COMMANDS and is_employee_allowed(user_id):
        return _HELP_TEXT
    # 21.08: первый контакт — приветствие-инструкция перед обычным ответом
    first_contact = (is_employee_allowed(user_id)
                     and not get_sessions_by_user(user_id))
    text = _dispatch(user_id, message, dialog_id, chunks, embeddings)
    if first_contact:
        return _HELP_TEXT + "\n\n" + text
    return _with_hint(text, user_id)


def _dispatch(user_id: str, message: str, dialog_id: str,
              chunks: list, embeddings) -> str:
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
        # 21.08: меню/документы работают ВЕЗДЕ и БЕЗ побочных эффектов —
        # раньше их съедал retake-форк или они молча назначали новый курс
        # (живой тупик Дмитрия на встрече 21.08)
        if cmd in _MENU_COMMANDS:
            return my_courses(user_id, resolve_role_cached(user_id))[0]
        if cmd in _DOC_COMMANDS:
            return _my_documents_text(resolve_role_cached(user_id), chunks)
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
        # 18.09: кнопки «Выбрать N»/«Тест N» под старым сообщением при
        # закрытой сессии раньше молча назначали ПЕРВЫЙ курс — теперь
        # открываем именно выбранный
        picked = _pick_without_session(user_id, dialog_id, cmd)
        if picked is not None:
            return picked
        # №11/№8/01.09: курс — после роли. Приоритет resolve_role: выбор HR
        # → отдел Битрикса → память прошлой сессии → меню (легаси);
        # до выбора сессия с сентинелом course_id=0.
        if selectable_roles():
            role, source = resolve_role(user_id)
            if role:
                return _assign_course(user_id, dialog_id, role, None,
                                      remembered=source == "memory",
                                      by_department=source == "department",
                                      by_hr=source == "hr")
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
        return _handle_test(session, message, "basic", chunks, embeddings)
    elif state == "WAITING_HR":
        return _handle_waiting_hr(session, message, chunks, embeddings)
    elif state == "EXAM_READY":
        return _handle_exam_ready(session, message, chunks, embeddings)
    elif state == "EXAM":
        return _handle_test(session, message, "exam", chunks, embeddings)
    elif state in quiz.PAUSED_STATES:
        return _handle_paused(session, message, quiz.phase_of(state),
                              chunks, embeddings)
    else:
        # DONE сюда не попадает: get_session отдаёт только незакрытые строки
        # (state != 'DONE') — завершённые идут веткой session is None выше.
        # Остаётся откат кода при уже записанных в БД новых состояниях —
        # не запираем сотрудника «Обратитесь к HR», а мягко возвращаем в чтение
        print(f"[fsm] неизвестное состояние {state!r} у {session['user_id']} "
              "— восстанавливаю READING")
        update_session(session["id"], state="READING")
        course = get_course_by_id(session["course_id"]) if session["course_id"] else None
        if course:
            return _start_reading(get_session_by_id(session["id"]), course)
        return ("Продолжаем обучение. Напиши *Мои курсы*, чтобы выбрать курс.")


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
    # Зеркало ветки session is None в process_message (№11/№8/01.09)
    if selectable_roles():
        role, source = resolve_role(user_id)
        if role:
            return _assign_course(user_id, dialog_id, role, None,
                                  remembered=source == "memory",
                                  by_department=source == "department",
                                  by_hr=source == "hr")
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


def _basic_pass_threshold(questions: dict) -> int:
    """31.08 (решение юзера): базовый сдан при 60% верных — «3 из 5»;
    масштабируется с адаптивным числом вопросов."""
    return max(1, round(len(questions.get("basic_questions", [])) * 0.6))


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
    bump_attempt(last["id"])          # новый порядок вариантов на попытку
    update_session(last["id"], state="EXAM", q_idx=0, score_exam=0)
    fresh = get_session_by_id(last["id"])   # ОБЯЗАТЕЛЬНО: в last старый attempt
    return ("🔁 Пересдача экзамена — вперёд!\n\n"
            + format_question(exam_q[0], 0, len(exam_q), "exam", fresh))


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
        if role_id is None or role_matches(role_id, course_roles(c)):
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


def _hr_assigned_role(user_id: str) -> str | None:
    """01.09: роль, назначенная HR («Пригласить» / «Роль {email}») —
    employees.role, пишет ТОЛЬКО HR. Явный выбор HR — главнее авто-роли по
    отделу и памяти сессии. Роль, пропавшая из реестра, игнорируется."""
    emp = get_employee(user_id)
    role = (emp or {}).get("role")
    valid = load_roles_config().get("roles", {})
    return role if role and role in valid else None


def resolve_role(user_id: str) -> tuple[str | None, str | None]:
    """Единая резолюция роли для НАЗНАЧЕНИЯ курса: (role_id, источник).
    Источник: "hr" → "department" (№8) → "memory" (прошлая сессия);
    (None, None) — роль неизвестна, сотруднику показывается меню."""
    role = _hr_assigned_role(user_id)
    if role:
        return role, "hr"
    role = _role_from_profile(user_id)
    if role:
        return role, "department"
    role = _last_known_role(user_id)
    if role:
        return role, "memory"
    return None, None


def resolve_role_cached(user_id: str) -> str | None:
    """Роль для потребителей-циклов (рассылки, дедлайны, меню) — БЕЗ сети:
    HR-назначение → память сессии. Авто-роль по отделу сюда не входит —
    _role_from_profile может звать user.get (httpx), а эти вызовы идут в
    цикле по всем сотрудникам (флап-риск; см. PRP invite-hardening)."""
    return _hr_assigned_role(user_id) or _last_known_role(user_id)


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
                   by_department: bool = False, by_hr: bool = False) -> str:
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
        if not already_flagged and not by_hr:
            # by_hr: роль только что назначил сам HR («Пригласить» /
            # «Роль {email}») и он уже получил «курсов для роли нет» —
            # «сотрудник запросил обучение» было бы ложью (ревью 01.09)
            notify_hr(
                f"👀 Сотрудник {_employee_label(user_id)} с ролью "
                f"«{role_name(role_id)}» запросил обучение — подходящих "
                "активных курсов нет."
            )
        if by_hr:
            return (f"📚 Твоя роль: *{role_name(role_id)}* (назначил HR). "
                    "Подходящих курсов пока нет — напишу, когда появятся.")
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
    if by_hr:
        role_line += " (назначил HR; сменить её может HR)"
    elif remembered:
        role_line += " (помню с прошлого обучения)"
    elif by_department:
        role_line += " (по отделу; если это ошибка — напиши HR)"
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
    # 01.09 (ревью): employees.role пишет ТОЛЬКО HR («Пригласить» /
    # «Роль {email}») — выбор сотрудника в меню живёт в session.role:
    # «посмотреть другую роль» не должен затирать назначение HR.
    if session["course_id"]:
        # Смена роли командой «Роль»: курс уже назначен — НЕ переназначаем
        # (сессия привязана; тестовый инструмент для просмотра других ролей)
        course = get_course_by_id(session["course_id"])
        if course:
            update_session(session["id"], state="READING", role=role_id)
            note = ""
            if _hr_assigned_role(session["user_id"]):
                note = ("\n_(временно, до следующего курса — постоянную "
                        "роль назначает HR)_")
            return (f"✅ Твоя роль: *{role_label}*{note}\n\n"
                    + _start_reading(session, course))
    return _assign_course(session["user_id"], session["dialog_id"],
                          role_id, session)


def doc_link(course: dict) -> str:
    """Ссылка на документ курса BB-кодом. Один хелпер на все сообщения —
    02.09: анонс нового курса тоже обязан нести ссылку, а не только первое
    сообщение об обучении (фидбек юзера)."""
    detail_url = doc_url(course.get("doc_detail_url"))
    name = display_name(course["doc_name"])
    return (f"[URL={detail_url}]{name}[/URL]" if detail_url
            else "(ссылка не найдена — спросите HR)")


def _start_reading(session: dict, course: dict) -> str:
    questions = get_course_questions(course["id"])
    summary = questions.get("course_summary", "")

    lines = [f"👋 Добро пожаловать! Начинаем обучение: "
             f"*{display_name(course['doc_name'])}*", ""]
    if summary:
        lines += [summary, ""]
    lines += [
        f"📄 Документ: {doc_link(course)}",
        "",
        "Прочитай документ и напиши *Готов*, когда будешь готов к тесту.",
        "Если есть вопросы по материалу — задавай, отвечу на основе стандартов.",
        "",
        "Команды: *Готов* — начать тест · "
        "*Мои курсы* — список курсов.",
    ]
    return "\n".join(lines)


def my_courses(user_id: str, role_id: str | None) -> tuple[str, list[dict]]:
    """«Мои курсы»: текст списка + курсы, ДОСТУПНЫЕ к выбору (не текущий,
    не пройденные). Нумерация в тексте = индексы списка — единый источник
    для «Выбрать N» (протокол 05.08: выбор курса сотрудником).
    Висящие строки других курсов (17.08) получают статус: «ждёт допуска HR» /
    «допущен к экзамену» — такие курсы тоже выбираемы."""
    courses = get_active_courses()
    done = _done_course_ids(user_id)
    session = get_session(user_id)
    current_id = session["course_id"] if session else 0
    # Свежайшее состояние незакрытой строки по каждому курсу (список отсортирован
    # новые сверху — берём первое вхождение); q_idx — для меток пауз тестов.
    # 21.08: тем же списком считаем ЛУЧШИЙ экзамен по DONE-строкам — статусы
    # «пройден/не сдан» должны быть честными (маркеры course_id=0 не в счёт)
    open_states: dict[int, tuple] = {}
    best_exam: dict[int, int] = {}
    all_sessions = get_sessions_by_user(user_id)
    for s in all_sessions:
        if s["state"] == "DONE":
            if s["course_id"]:
                best_exam[s["course_id"]] = max(
                    best_exam.get(s["course_id"], 0), s["score_exam"] or 0)
        elif s["course_id"] not in open_states:
            open_states[s["course_id"]] = (s["state"],
                                           s.get("current_q_idx", 0))
    mine = [c for c in courses
            if role_id is None or role_matches(role_id, course_roles(c))]
    if not mine:
        return "Для твоей роли пока нет активных курсов.", []
    selectable = [c for c in mine
                  if c["id"] != current_id and c["id"] not in done]
    lines = ["📚 Твои курсы:", ""]
    for c in mine:
        name = display_name(c["doc_name"])
        if c["id"] == current_id:
            if session and session["state"] == "WAITING_HR":
                lines.append(f"▶️ {name} — базовый сдан, ждёт допуска HR")
            elif session and session["state"] == "EXAM_READY":
                lines.append(f"▶️ {name} — допущен к экзамену, "
                             "напиши *Начать*")
            elif session and quiz.phase_of(session["state"]):
                what = ("экзамен" if quiz.phase_of(session["state"]) == "exam"
                        else "базовый тест")
                pause = " (на паузе)" if quiz.is_paused(session["state"]) else ""
                lines.append(f"▶️ {name} — {what} на вопросе "
                             f"{session['current_q_idx'] + 1}{pause}")
            else:
                lines.append(f"▶️ {name} — проходишь сейчас")
        elif c["id"] in done:
            questions = get_course_questions(c["id"])
            total_exam = len(questions.get("exam_questions", []))
            best = best_exam.get(c["id"], 0)
            if best >= _exam_pass_threshold(questions):
                lines.append(f"✅ {name} — пройден")
            else:
                line = f"❌ {name} — экзамен не сдан ({best}/{total_exam})"
                last = get_last_done_session(user_id)
                if last and last["course_id"] == c["id"]:
                    line += " · напиши *Пересдать*"
                lines.append(line)
        else:
            n = selectable.index(c) + 1
            state, q_idx = open_states.get(c["id"], (None, 0))
            # Обогащение для BLOCK-кнопок (дизайн 17.08): номер «Выбрать N»
            # и статус едут вместе с курсом, контракт (text, selectable) прежний
            c["n"] = n
            if quiz.phase_of(state):
                # 01.09: EXAM q_idx=0 — экзамен УЖЕ начат (вопрос 1 показан),
                # «допущен» было бы враньём; «допущен» — только EXAM_READY
                c["status"] = "paused"       # 19.08: тест/экзамен отложен
            elif state == "EXAM_READY":
                c["status"] = "admitted"
            elif state == "WAITING_HR":
                c["status"] = "waiting"
            else:
                c["status"] = "todo"
            if state == "WAITING_HR":
                lines.append(f"{n}. ⏳ {name} — базовый сдан, ждёт допуска HR")
            elif quiz.phase_of(state):
                what = ("экзамен" if quiz.phase_of(state) == "exam"
                        else "базовый тест")
                lines.append(f"{n}. ⏸ {name} — {what} на вопросе {q_idx + 1}")
            elif state == "EXAM_READY":
                lines.append(f"{n}. 🎓 {name} — допущен к экзамену")
            else:
                lines.append(f"{n}. ⏳ {name}")
    if selectable:
        lines += ["", "Переключиться на другой курс: напиши *Выбрать {номер}*."]
    return "\n".join(lines), selectable


def _my_courses_text(user_id: str, role_id: str | None) -> str:
    return my_courses(user_id, role_id)[0]


_MENU_COMMANDS = ("мои курсы", "курсы", "меню")
_SWITCH_RE = re.compile(r"^выбрать\s+(\d+)$")


def _handle_course_switch(session: dict, n: int) -> str:
    """«Выбрать N»: переключение на другой курс. Старый курс НЕ помечается
    пройденным (done — только через экзамен).

    17.08: у целевого курса может висеть своя незакрытая строка (ждёт допуска /
    допущен к экзамену) — возвращаемся К НЕЙ (touch → она активна), вторую не
    плодим. Из WAITING_HR текущую строку НЕ репоинтим (при ней ответы базового
    её курса) — паркуем и создаём новую; READING-строка ответов не несёт,
    её репоинт безопасен (как раньше)."""
    user_id = session["user_id"]
    text, selectable = my_courses(user_id, session.get("role"))
    if not selectable:
        return text
    if not (1 <= n <= len(selectable)):
        return f"Такого номера нет.\n\n{text}"
    course = selectable[n - 1]
    name = display_name(course["doc_name"])

    existing = get_open_session_for_course(user_id, course["id"])
    if existing:
        update_session(existing["id"])          # touch → активная
        phase = quiz.phase_of(existing["state"])
        if phase:
            # 02.09: паузная строка при возврате СНИМАЕТ паузу — инвариант
            # «показали вопрос → состояние активное», иначе клавиатура придёт
            # без A–D и сотрудник смотрит на вопрос, на который нечем ответить
            questions = get_course_questions(course["id"])
            q_list = questions.get(f"{phase}_questions", [])
            idx = existing["current_q_idx"]
            if q_list and idx < len(q_list):
                update_session(existing["id"], state=quiz.active_state(phase))
                fresh = get_session_by_id(existing["id"])
                head = (f"🎓 Экзамен по курсу *{name}* — поехали!"
                        if phase == "exam" else
                        f"📝 Продолжаем базовый тест курса *{name}*!")
                return (head + "\n\n"
                        + format_question(q_list[idx], idx, len(q_list),
                                          phase, fresh))
            what = "экзамена" if phase == "exam" else "теста"
            return f"Курс *{name}*: вопросы {what} не найдены. Обратись к HR."
        if existing["state"] == "EXAM_READY":
            questions = get_course_questions(course["id"])
            n_exam = len(questions.get("exam_questions", []))
            return (f"🎓 Курс *{name}*: базовый сдан, ты допущен к экзамену "
                    f"({n_exam} вопросов). Нажми кнопку или напиши *Начать*, "
                    "когда будешь готов.")
        if existing["state"] == "WAITING_HR":
            return (f"⏳ Курс *{name}*: базовый тест сдан, ждём допуска HR "
                    "к экзамену. Пока можешь задавать вопросы по документам "
                    "или выбрать другой курс: *Мои курсы*.")
        return "🔄 Вернулся к курсу.\n\n" + _start_reading(existing, course)

    # Строки с ответами/прогрессом (ожидание, паузы тестов) НЕ репоинтим —
    # паркуем и создаём новую (19.08: из теста тоже можно уйти).
    # quiz.PROGRESS_STATES вместо литерала: забыть тут PAUSED_* = переиспользовать
    # строку с ответами под другой курс и потерять прогресс
    if session["state"] in quiz.PROGRESS_STATES:
        new = create_session(user_id, session["dialog_id"], course["id"])
        if session.get("role"):
            update_session(new["id"], role=session["role"])
        return "🔄 Переключил курс.\n\n" + _start_reading(new, course)

    update_session(session["id"], course_id=course["id"],
                   state="READING", q_idx=0)
    return "🔄 Переключил курс.\n\n" + _start_reading(session, course)


_TEST_RE = re.compile(r"^тест\s+(\d+)$")   # payload по course_id (18.09)


def _start_basic_test(session: dict) -> str:
    """«Готов» из READING: базовый тест с первого вопроса (пересдача базового
    после провала — прошлые ответы фазы стереть, иначе _finish_phase
    сосчитает обе попытки — ловушка _handle_retake)."""
    questions = get_course_questions(session["course_id"])
    basic_q = questions.get("basic_questions", [])
    if not basic_q:
        return "Ошибка: вопросы курса не найдены. Обратитесь к HR."
    delete_session_answers(session["id"], "basic")
    bump_attempt(session["id"])   # пересдача базового — тоже новый порядок
    update_session(session["id"], state="BASIC_TEST", q_idx=0,
                   score_basic=0)
    fresh = get_session_by_id(session["id"])   # см. bump_attempt
    return (f"✅ Отлично! Начинаем базовый тест — "
            f"{len(basic_q)} вопрос(ов).\n\n"
            + format_question(basic_q[0], 0, len(basic_q), "basic", fresh))


def test_choices(user_id: str, session: dict | None) -> list[dict]:
    """Курсы, по которым сотрудник может начать/продолжить тест: текущий
    (первым) + выбираемые из «Мои курсы». Для текста и кнопок «Тест {id}»."""
    role = session.get("role") if session else resolve_role_cached(user_id)
    _, selectable = my_courses(user_id, role)
    choices: list[dict] = []
    if session and session.get("course_id"):
        course = get_course_by_id(session["course_id"])
        if course:
            choices.append({"id": course["id"],
                            "name": display_name(course["doc_name"]),
                            "current": True, "status": "current"})
    for c in selectable:
        choices.append({"id": c["id"], "name": display_name(c["doc_name"]),
                        "current": False, "status": c.get("status", "todo")})
    return choices


_CHOICE_EMOJI = {"current": "▶️", "paused": "⏸", "admitted": "🎓",
                 "waiting": "⏳", "todo": "⏳"}


def _test_pick_text(choices: list[dict]) -> str:
    """18.09 (фидбек Дмитрия: «Готов» запускал не тот тест): при нескольких
    курсах спрашиваем, какой тест начинать. Кнопки несут course_id."""
    lines = ["❓ Какой тест начинаем?", ""]
    for c in choices:
        tail = " — текущий курс" if c["current"] else ""
        lines.append(f"{_CHOICE_EMOJI.get(c['status'], '⏳')} {c['name']}{tail}"
                     f" · *Тест {c['id']}*")
    lines += ["", "Нажми кнопку или напиши *Тест {номер}*."]
    return "\n".join(lines)


def _handle_test_pick(session: dict, course_id: int) -> str:
    """«Тест {course_id}»: стартовать/продолжить тест КОНКРЕТНОГО курса.
    Чужой курс — через механику «Выбрать» (возврат к висящей строке,
    парковка прогресса), затем базовый тест, если курс в чтении."""
    user_id = session["user_id"]
    if course_id == session["course_id"]:
        state = session["state"]
        if state == "READING":
            return _start_basic_test(session)
        phase = quiz.phase_of(state)
        if phase:
            questions = get_course_questions(course_id)
            q_list = questions.get(f"{phase}_questions", [])
            idx = session["current_q_idx"]
            if not q_list or idx >= len(q_list):
                return "Ошибка: вопросы курса не найдены. Обратитесь к HR."
            if quiz.is_paused(state):
                update_session(session["id"], state=quiz.active_state(phase))
            fresh = get_session_by_id(session["id"])
            return ("▶ Продолжаем!\n\n"
                    + format_question(q_list[idx], idx, len(q_list), phase, fresh))
        if state == "EXAM_READY":
            return ("🎓 Ты допущен к экзамену по этому курсу — нажми кнопку "
                    "или напиши *Начать*.")
        if state == "WAITING_HR":
            return ("⏳ Экзамен по этому курсу откроется после допуска HR — "
                    "придёт уведомление.")
        return _my_courses_text(user_id, session.get("role"))

    text, selectable = my_courses(user_id, session.get("role"))
    idx = next((i for i, c in enumerate(selectable) if c["id"] == course_id),
               None)
    if idx is None:
        return f"Этого курса нет в твоём списке.\n\n{text}"
    switched = _handle_course_switch(session, idx + 1)
    fresh = get_session(user_id)
    if fresh and fresh["course_id"] == course_id and fresh["state"] == "READING":
        name = display_name(selectable[idx]["doc_name"])
        return f"🔄 Курс *{name}*.\n\n" + _start_basic_test(fresh)
    return switched   # пауза/допуск/ожидание — переключение уже всё сказало


def _pick_without_session(user_id: str, dialog_id: str, cmd: str) -> str | None:
    """«Выбрать N» / «Тест {id}» без открытой сессии: открыть выбранный курс
    (не первый по списку). None — сообщение не про выбор курса."""
    m_switch, m_test = _SWITCH_RE.match(cmd), _TEST_RE.match(cmd)
    if not (m_switch or m_test):
        return None
    role = resolve_role_cached(user_id)
    text, selectable = my_courses(user_id, role)
    course = None
    if m_switch:
        n = int(m_switch.group(1))
        if 1 <= n <= len(selectable):
            course = selectable[n - 1]
    else:
        cid = int(m_test.group(1))
        course = next((c for c in selectable if c["id"] == cid), None)
    if course is None:
        return f"Этого курса нет в твоём списке.\n\n{text}"
    session = create_session(user_id, dialog_id, course["id"])
    if role:
        update_session(session["id"], role=role)
        session = get_session_by_id(session["id"])
    reply = _start_reading(session, course)
    if m_test:
        return reply + "\n\n" + _start_basic_test(session)
    return reply


def _handle_reading(session: dict, message: str, chunks: list, embeddings) -> str:
    cmd = message.strip().lower()
    if cmd in _MENU_COMMANDS:
        return _my_courses_text(session["user_id"], session.get("role"))
    if cmd in _DOC_COMMANDS:
        return _my_documents_text(session.get("role"), chunks)
    m_switch = _SWITCH_RE.match(cmd)
    if m_switch:
        return _handle_course_switch(session, int(m_switch.group(1)))
    m_test = _TEST_RE.match(cmd)
    if m_test:
        return _handle_test_pick(session, int(m_test.group(1)))
    if cmd in ("выйти", "выход"):
        # 01.09: раньше уходило в RAG — бот отвечал «по стандартам» на
        # служебную команду; хинт состояния допишет _with_hint
        return "Сейчас нет активного теста — откладывать нечего."
    if cmd in ("пересдать", "пересдача"):
        # Реактивация DONE-сессии при живой текущей дала бы ДВЕ активные —
        # get_session вернул бы не ту. Пересдача — только между курсами.
        return ("Сначала закончи текущий курс — пересдать прошлый экзамен "
                "можно будет после него.")
    if cmd == "роль":
        # Сотрудник не меняет организационную роль сам.
        return "🔒 Роль сотрудника назначает и меняет HR."

    if cmd in ("готов", "готова"):
        # 18.09: точное сравнение — «я пока не готов» шло в тест; при
        # нескольких курсах спрашиваем, какой тест (а не «последний тронутый»)
        choices = test_choices(session["user_id"], session)
        if len(choices) > 1:
            return _test_pick_text(choices)
        return _start_basic_test(session)

    return _rag_reply(session, message, chunks, embeddings)


_QUIZ_DOC_NOTICE = ("ℹ️ По документу текущего курса во время теста не "
                    "подсказываю — вернёмся к нему после сдачи.")


def _current_doc_name(session: dict) -> str | None:
    """doc_name курса сессии. course_id может быть 0 (сентинел ROLE_SELECT)
    или отсутствовать (словари-моки в тестах) — тогда исключать нечего."""
    cid = session.get("course_id")
    course = get_course_by_id(cid) if cid else None
    return course["doc_name"] if course else None


def _quiz_rag_reply(session: dict, message: str, chunks: list,
                    embeddings) -> str:
    """ЕДИНСТВЕННАЯ точка RAG во время теста/экзамена/паузы.

    Документ текущего курса исключён из поиска: вопросы генерируются из него
    же, поэтому исключение — и есть гарантия честности (а не запрет отвечать).
    Предупреждение про закрытый документ формируется ЗДЕСЬ и только здесь;
    вызывающие свои хвосты не дописывают, иначе в паузе получится два
    «Продолжить» подряд (навигацию добавляет _with_hint).
    """
    text, relevant = _rag_reply_ex(session, message, chunks, embeddings,
                                   exclude_doc=_current_doc_name(session))
    if not relevant:
        # «Не найдено» после исключения почти всегда = спрашивали ровно про
        # закрытый документ; сухая строка из rag.py тут дезинформирует
        return (_QUIZ_DOC_NOTICE + "\n\nЕсли вопрос про другой стандарт — "
                "переформулируй, поищу по остальным документам.")
    return text


def _rag_reply(session: dict, message: str, chunks: list, embeddings,
               exclude_doc: str | None = None) -> str:
    return _rag_reply_ex(session, message, chunks, embeddings, exclude_doc)[0]


def _rag_reply_ex(session: dict, message: str, chunks: list, embeddings,
                  exclude_doc: str | None = None) -> tuple[str, list]:
    """Ответ RAG + найденные чанки: relevant нужен вызывающему, чтобы отличить
    «нашли в другом документе» от «не нашли ничего»."""
    text, relevant = rag_answer(
        query=message,
        chunks=chunks,
        embeddings=embeddings,
        section_filter=None,
        answer_length="Стандартно",
        role_filter=session.get("role"),
        exclude_doc=exclude_doc,
    )
    # 19.08: источник под ответом — доверие и путь к первоисточнику.
    # relevant пуст («не найдено» или мок в тестах) → без строки источника.
    if relevant:
        doc = relevant[0].get("doc_name")
        if doc:
            url = doc_url((get_course_by_doc_name(doc) or {}).get("doc_detail_url"))
            name = display_name(doc)
            text += ("\n\n📄 Источник: " +
                     (f"[URL={url}]{name}[/URL]" if url else name))
    return text, relevant


_DOC_COMMANDS = ("мои документы", "документы")


def _my_documents_text(role_id: str | None, chunks: list) -> str:
    """«Мои документы»: документы индекса для роли сотрудника (19.08).
    21.08 (Евгения: «136 простынёй — люди уйдут к руководителю»): две секции —
    «Для твоего отдела» и «Общие». Роль неизвестна → только общие."""
    dept_docs: list[str] = []
    common_docs: list[str] = []
    for c in chunks:
        if c.get("audience") == "guest":
            continue
        doc = c.get("doc_name")
        if not doc:
            continue
        c_roles = set(c.get("roles") or [])
        # 02.09: all_access-роль («Менеджер») видит ведомственные стандарты
        # всех отделов — они идут в первую секцию, общие остаются общими
        if role_id is not None and (role_id in c_roles
                                    or (role_sees_all(role_id)
                                        and c_roles - {ALL_STAFF})):
            if doc not in dept_docs:
                dept_docs.append(doc)
        elif ALL_STAFF in c_roles:
            if doc not in common_docs and doc not in dept_docs:
                common_docs.append(doc)
    if not dept_docs and not common_docs:
        return "Для твоей роли пока нет документов."

    def _section(docs: list[str]) -> list[str]:
        out = []
        for doc in docs:
            url = doc_url((get_course_by_doc_name(doc) or {}).get("doc_detail_url"))
            name = display_name(doc)
            out.append("• " + (f"[URL={url}]{name}[/URL]" if url else name))
        return out

    lines = ["📄 Твои документы:"]
    if dept_docs:
        header = ("📌 *По отделам:*" if role_sees_all(role_id)
                  else "📌 *Для твоего отдела:*")
        lines += ["", header] + _section(dept_docs)
    if common_docs:
        lines += ["", "📖 *Общие:*"] + _section(common_docs)
    lines += ["", "Задавай вопросы по любому из них — отвечу по тексту стандарта."]
    if role_id is None:
        lines += ["", "Показаны только общие документы. "
                  "Попроси HR назначить тебе роль."]
    return "\n".join(lines)


def _handle_test(session: dict, message: str, phase: str,
                 chunks: list | None = None, embeddings=None) -> str:
    questions = get_course_questions(session["course_id"])
    q_list = questions.get(f"{phase}_questions", [])
    total = len(q_list)
    q_idx = session["current_q_idx"]

    if q_idx >= total:
        # Guard: shouldn't happen in normal flow
        return _finish_phase(session, phase, questions)

    current_q = q_list[q_idx]

    # 19.08: тест — не клетка. Меню, пауза и переключение доступны; прогресс
    # НЕ сбрасывается: строка паркуется со своим current_q_idx, возврат через
    # «Выбрать» продолжает С ЭТОГО ЖЕ вопроса (multi-row механика 17.08).
    cmd = message.strip().lower()
    if cmd in _MENU_COMMANDS:
        return _my_courses_text(session["user_id"], session.get("role"))
    if cmd in ("выйти", "выход"):
        # 02.09: пауза — НАСТОЯЩЕЕ состояние. Раньше здесь печатался текст, а
        # сессия оставалась в BASIC_TEST/EXAM, из-за чего «Помощь» и вопросы
        # вели себя как в активном тесте (живой прогон).
        update_session(session["id"], state=quiz.paused_state(phase))
        return _pause_text(session, phase, q_idx, total)
    if cmd == "продолжить":
        return "▶ Продолжаем!\n\n" + format_question(current_q, q_idx,
                                                     total, phase, session)
    if cmd in _DOC_COMMANDS:
        return _my_documents_text(session.get("role"), chunks or [])
    m_switch = _SWITCH_RE.match(cmd)
    if m_switch:
        return _handle_course_switch(session, int(m_switch.group(1)))
    m_test = _TEST_RE.match(cmd)
    if m_test:
        return _handle_test_pick(session, int(m_test.group(1)))

    letter = parse_answer(message)
    view = quiz.question_view(session, current_q, phase, q_idx)

    if letter is None:
        # 02.09 (живой прогон): вопрос по стандартам во время теста упирался
        # в «ответь одной буквой» — бот замолкал именно там, где сотрудник
        # чаще всего и спрашивает. Отвечаем в ОБЕИХ фазах: честность экзамена
        # держится не запретом, а исключением документа курса из поиска
        # (_quiz_rag_reply) — вопросы генерируются из него же.
        tail = format_question(current_q, q_idx, total, phase, session)
        if chunks and embeddings is not None:
            return (_quiz_rag_reply(session, message, chunks, embeddings)
                    + "\n\n— — —\nВозвращаемся к тесту:\n\n" + tail)
        return "Пожалуйста, ответь одной буквой: A, B, C или D.\n\n" + tail

    # Сотрудник прислал букву в СВОИХ координатах; в answers и на сверку идёт
    # каноническая — тогда «История» HR и отчёты остаются корректными.
    canon = quiz.to_canonical(letter, view.perm)
    is_correct = 1 if canon == current_q["correct"] else 0
    log_answer(session["id"], q_idx, phase, canon, is_correct)

    # Буква в фидбеке — в координатах сотрудника (канон в чат не утекает);
    # тело правильного варианта делает строку понятной даже без букв
    if is_correct:
        feedback = "✅ Верно!"
    else:
        feedback = (f"❌ Неверно. Правильный ответ: "
                    f"*{view.correct_display}. {view.correct_body}*"
                    if view.correct_body else
                    f"❌ Неверно. Правильный ответ: *{view.correct_display}*")
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
        return feedback + "\n\n" + format_question(next_q, new_idx, total,
                                                   phase, session)

    # Last question answered — finish the phase
    return _finish_phase(session, phase, questions, last_feedback=feedback)


def _pause_text(session: dict, phase: str, q_idx: int, total: int) -> str:
    label = quiz.phase_label(phase)
    return (f"⏸ {label} на паузе (вопрос {q_idx + 1} из {total}, прогресс "
            "сохранён).\n\n"
            f"Что дальше — пока {label.lower()} на паузе, можно:\n"
            "• ▶ *Продолжить* — вернуться к тому же вопросу\n"
            "• *Выбрать {номер}* — перейти к другому курсу\n"
            "• *Документы* — открыть список стандартов · *Помощь*\n"
            "• задать вопрос по стандартам (кроме документа этого курса)\n\n"
            + _my_courses_text(session["user_id"], session.get("role")))


def _handle_paused(session: dict, message: str, phase: str,
                   chunks: list, embeddings) -> str:
    """Пауза теста/экзамена (02.09). Навигация и вопросы свободны; вернуться
    к вопросу — только явной командой, поэтому буква A–D здесь НЕ засчитывается
    (см. ниже)."""
    cmd = message.strip().lower()
    questions = get_course_questions(session["course_id"])
    q_list = questions.get(f"{phase}_questions", [])
    total = len(q_list)
    q_idx = session["current_q_idx"]

    if cmd in _MENU_COMMANDS:
        return _my_courses_text(session["user_id"], session.get("role"))
    if cmd in _DOC_COMMANDS:
        return _my_documents_text(session.get("role"), chunks or [])
    m_switch = _SWITCH_RE.match(cmd)
    if m_switch:
        return _handle_course_switch(session, int(m_switch.group(1)))
    m_test = _TEST_RE.match(cmd)
    if m_test:
        return _handle_test_pick(session, int(m_test.group(1)))
    if cmd in ("выйти", "выход"):
        return _pause_text(session, phase, q_idx, total)     # идемпотентно
    if cmd in ("пересдать", "пересдача"):
        return ("Сначала закончи текущий курс — пересдать прошлый экзамен "
                "можно будет после него.")
    # «Готов»/«Начать» в паузе = «Продолжить»: НЕ обнуляем прогресс (иначе
    # ветка «Готов» из READING молча стёрла бы ответы и начала тест заново)
    if cmd in ("продолжить", "готов", "начать", "экзамен"):
        if not q_list or q_idx >= total:
            return "Ошибка: вопросы курса не найдены. Обратитесь к HR."
        update_session(session["id"], state=quiz.active_state(phase))
        fresh = get_session_by_id(session["id"])
        return ("▶ Продолжаем!\n\n"
                + format_question(q_list[q_idx], q_idx, total, phase, fresh))
    if parse_answer(message) is not None:
        # Кнопки Bitrix живут под старыми сообщениями вечно: буква в паузе
        # почти всегда — промах по кнопке ПРОШЛОГО вопроса, а с пер-сессионной
        # перестановкой она размапится чужим порядком и даст тихо неверную
        # оценку. Формулировка «вопрос N из T», а не «— Вопрос N/M»: второе
        # матчит _QUESTION_RE в bitrix_bot и вернуло бы клавиатуру A–D.
        return (f"⏸ {quiz.phase_label(phase)} на паузе — ответ не засчитан.\n"
                f"Нажми *Продолжить*, чтобы вернуться к вопросу "
                f"{q_idx + 1} из {total}.")
    return _quiz_rag_reply(session, message, chunks, embeddings)


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
        # 31.08 (решение юзера): допуск к экзамену АВТОМАТИЧЕСКИЙ по порогу
        # 60% («3 из 5»), HR получает FYI; провал = пересдача базового.
        total = len(questions.get("basic_questions", []))
        threshold = _basic_pass_threshold(questions)
        label = _employee_label(session["user_id"])
        if correct_count < threshold:
            # Сброс ответов фазы: _finish_phase считает ВСЕ строки фазы
            # (ловушка _handle_retake) — без чистки попытки схлопнутся
            update_session(session["id"], state="READING", q_idx=0,
                           score_basic=0)
            delete_session_answers(session["id"], "basic")
            notify_hr(f"📋 Сотрудник {label} не сдал базовый тест "
                      f"({correct_count}/{total}, нужно {threshold}) — "
                      "перечитывает документ и пересдаёт.")
            return prefix + (
                f"❌ Базовый тест не сдан: *{correct_count}/{total}* "
                f"(нужно {threshold}).\n\n"
                "Перечитай документ и напиши *Готов* — попробуем ещё раз."
            )
        exam_q = questions.get("exam_questions", [])
        if not exam_q:
            update_session(session["id"], state="DONE", q_idx=0,
                           score_basic=correct_count)
            notify_hr(f"📋 Сотрудник {label} сдал базовый тест "
                      f"({correct_count}/{total}); экзамена у курса нет — "
                      "обучение завершено.")
            return prefix + (
                f"🏁 Базовый тест сдан: *{correct_count}/{total}*!\n"
                "Экзаменационных вопросов у этого курса нет — "
                "обучение завершено. 🎉"
            )
        update_session(session["id"], state="EXAM_READY", q_idx=0,
                       score_basic=correct_count)
        notify_hr(f"📋 Сотрудник {label} сдал базовый тест "
                  f"({correct_count}/{total}) — допущен к экзамену "
                  "автоматически.")
        return prefix + (
            f"🏁 Базовый тест сдан: *{correct_count}/{total}*!\n\n"
            f"🎓 Ты допущен к экзамену: {len(exam_q)} вопросов, "
            f"проходной балл {_exam_pass_threshold(questions)}.\n"
            "Нажми кнопку или напиши *Начать*, когда будешь готов."
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


def _handle_waiting_hr(session: dict, message: str,
                       chunks: list, embeddings) -> str:
    """Ожидание допуска НЕ блокирует сотрудника (17.08): RAG-вопросы,
    «Мои курсы» и переключение на другие курсы доступны — заперт только
    экзамен этого курса до «Допустить»."""
    cmd = message.strip().lower()
    if cmd in _MENU_COMMANDS:
        return _my_courses_text(session["user_id"], session.get("role"))
    if cmd in _DOC_COMMANDS:
        return _my_documents_text(session.get("role"), chunks)
    m_switch = _SWITCH_RE.match(cmd)
    if m_switch:
        return _handle_course_switch(session, int(m_switch.group(1)))
    m_test = _TEST_RE.match(cmd)
    if m_test:
        return _handle_test_pick(session, int(m_test.group(1)))
    if cmd in ("выйти", "выход"):
        return "Сейчас нет активного теста — откладывать нечего."
    if cmd in ("пересдать", "пересдача"):
        return ("Пересдать прошлый экзамен можно будет после завершения "
                "текущего курса.")
    if cmd in ("готов", "экзамен", "начать"):
        return ("⏳ Экзамен по этому курсу откроется после допуска HR — "
                "придёт уведомление.\nПока можешь задавать вопросы по "
                "документам или пройти другой курс: *Мои курсы*.")
    return _rag_reply(session, message, chunks, embeddings)


def _handle_exam_ready(session: dict, message: str,
                       chunks: list, embeddings) -> str:
    """Допущен к экзамену (31.08): экзамен стартует ТОЛЬКО явной командой —
    до этого свободны RAG, меню, документы и другие курсы."""
    cmd = message.strip().lower()
    if cmd in _MENU_COMMANDS:
        return _my_courses_text(session["user_id"], session.get("role"))
    if cmd in _DOC_COMMANDS:
        return _my_documents_text(session.get("role"), chunks)
    m_switch = _SWITCH_RE.match(cmd)
    if m_switch:
        return _handle_course_switch(session, int(m_switch.group(1)))
    m_test = _TEST_RE.match(cmd)
    if m_test:
        return _handle_test_pick(session, int(m_test.group(1)))
    if cmd in ("выйти", "выход"):
        return ("Сейчас нет активного теста — откладывать нечего. "
                "Экзамен ещё не начат.")
    if cmd in ("пересдать", "пересдача"):
        return ("Пересдать прошлый экзамен можно будет после завершения "
                "текущего курса.")
    if cmd in ("начать", "готов", "экзамен"):
        questions = get_course_questions(session["course_id"])
        q_list = questions.get("exam_questions", [])
        if not q_list:
            return "Ошибка: вопросы экзамена не найдены. Обратись к HR."
        update_session(session["id"], state="EXAM", q_idx=0)
        return ("🎓 Экзамен — поехали!\n\n"
                + format_question(q_list[0], 0, len(q_list), "exam"))
    return _rag_reply(session, message, chunks, embeddings)


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_question(q: dict, idx: int, total: int, phase: str,
                    session: dict | None = None) -> str:
    """Вопрос глазами сотрудника. session=None — канонический порядок
    (HR-инструменты, легаси-вызовы); с сессией порядок вариантов её
    собственный (quiz.question_view — тот же вызов делает и клавиатура)."""
    label = "Базовый тест" if phase == "basic" else "Экзамен"
    view = quiz.question_view(session, q, phase, idx)
    lines = [f"📝 {label} — Вопрос {idx + 1}/{total}", "", q["text"], ""]
    return "\n".join(lines + view.options)


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


def bare_dialog(dialog_id: str) -> str:
    """Bitrix imbot принимает DIALOG_ID = числовой id юзера или chatNN.
    Исторический префикс «u» (u28528) портал отвергает 400 DIALOG_ID_EMPTY
    (вскрыто живьём 17.08) — срезаем в точках ОТПРАВКИ: это лечит и
    легаси-«uNNN», уже записанные в dialog_id сессий БД."""
    s = str(dialog_id)
    return s[1:] if s.startswith("u") and s[1:].isdigit() else s


def _track_keyboard_sync(webhook_url: str, bot_id, dialog_id, resp,
                         client_id: str = "") -> None:
    """Синхронный близнец bitrix_bot._track_keyboard для notify_hr (он идёт
    мимо _send и работает в потоке). 11.09: кнопки только у последнего
    сообщения HR-бота в диалоге; сбой снятия — старые кнопки остаются, и
    только."""
    try:
        message_id = resp.json().get("result")
    except Exception:
        return
    loser = kb_tracker.register(bot_id, dialog_id, message_id)
    if loser is None:
        return
    try:
        r = httpx.post(webhook_url + "imbot.message.update",
                       json=kb_tracker.strip_payload(bot_id, loser, client_id),
                       timeout=10.0)
        if r.status_code != 200:
            print(f"[notify_hr] KEYBOARD strip {r.status_code} (msg={loser}): "
                  f"{r.text[:200]}")
    except Exception as exc:
        print(f"[notify_hr] KEYBOARD strip failed (msg={loser}): {exc!r}")


def notify_hr(message: str, keyboard: list | None = None) -> None:
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

    # notify_hr bypasses bitrix_bot._send, so it needs the same universal
    # navigation fallback for HR notifications sent by the state machine.
    if os.getenv("BUTTONS_ENABLED", "0") == "1":
        if keyboard is None:
            keyboard = keyboards.hr_main_menu()
        keyboard = keyboards.with_main_menu(keyboard, hr=True)
    hr_ids = [x.strip() for x in hr_ids_str.split(",") if x.strip()]
    for hr_id in hr_ids:
        payload = {"BOT_ID": bot_id, "DIALOG_ID": bare_dialog(hr_id),
                   "MESSAGE": md_to_bb(message), "CLIENT_ID": client_id}
        if keyboard:  # инвариант №7: ключ отсутствует вовсе при None
            payload["KEYBOARD"] = keyboard
        # Same flapping network as _send — retry with backoff so HR notifications
        # aren't silently lost on a transient ConnectTimeout.
        for attempt in range(1, 6):
            try:
                resp = httpx.post(
                    webhook_url + "imbot.message.add",
                    json=payload,
                    timeout=15.0,
                )
                err = "" if resp.status_code == 200 else f" {resp.text[:200]}"
                print(f"[notify_hr] → HR {hr_id}: {resp.status_code}"
                      f" (attempt {attempt}){err}")
                if keyboard and resp.status_code == 200:
                    _track_keyboard_sync(webhook_url, bot_id, hr_id, resp,
                                         client_id)
                break
            except httpx.RequestError as exc:
                print(f"[notify_hr] retry {attempt}/5 for HR {hr_id}: {exc!r}")
                if attempt < 5:
                    time.sleep(2 * attempt)
