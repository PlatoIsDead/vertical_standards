"""
app/keyboards.py — инлайн-кнопки обоих ботов (за флагом BUTTONS_ENABLED).

Клавиатура — функция СОСТОЯНИЯ сессии, FSM не трогается: нажатие кнопки шлёт
generic-команду с COMMAND_PARAMS = ровно тем текстом, который текстовые
хендлеры уже понимают («A», «Готов», «Подтвердить 5», «Допустить 500»).
Employee-бот — команда «say», HR-бот — «hrsay»: /command роутит по имени
команды (BOT_ID в событии ONIMCOMMANDADD не гарантирован).
Событие нажатия приходит ONIMCOMMANDADD на POST /command.

Все билдеры чистые (без БД) — данные передаются параметрами из bitrix_bot.
"""

_BG = "#29619b"
_NEWLINE = {"TYPE": "NEWLINE"}

# Лимит клавиатуры Bitrix — 30 КБ (KEYBOARD_OVERSIZE): список курсов капаем,
# хвост доступен текстом «Вопросы N» как раньше.
MAX_COURSE_ROWS = 20


def _btn(text: str, payload: str = None, display: str = "LINE",
         command: str = "say") -> dict:
    return {"TEXT": text, "COMMAND": command, "COMMAND_PARAMS": payload or text,
            "DISPLAY": display, "BG_COLOR": _BG, "TEXT_COLOR": "#ffffff"}


def _hr_btn(text: str, payload: str = None, display: str = "LINE") -> dict:
    return _btn(text, payload, display, command="hrsay")


def for_session(session: dict | None, fork: bool = False,
                role_options: list | None = None) -> list | None:
    """session — СВЕЖАЯ сессия (после process_message); fork — активна
    развилка «Пересдать/Далее» (№9); role_options — selectable_roles()."""
    if session is None:
        if fork:
            return [_btn("Пересдать"), _btn("Далее")]
        return None
    state = session["state"]
    if state == "ROLE_SELECT":
        return ([_btn(str(i)) for i in range(1, len(role_options or []) + 1)]
                or None)
    if state == "READING":
        return [_btn("Готов", display="BLOCK"), _btn("Мои курсы"), _btn("Роль")]
    if state in ("BASIC_TEST", "EXAM"):
        return [_btn(x) for x in ("A", "B", "C", "D")]
    if state == "WAITING_HR":
        return [_btn("Мои курсы")]
    return None


# ── Employee-бот: добор ──────────────────────────────────────────────────────

def start_button(label: str, payload: str = "Начать") -> list:
    """Проактивные «Напиши что-нибудь…»: любой текст двигает FSM — кнопка
    шлёт нейтральное «Начать» (не A–D-лукалайк, ответом не считается)."""
    return [_btn(label, payload, display="BLOCK")]


def with_switch(base: list | None, n: int) -> list:
    """Ответ на «Мои курсы»: [Выбрать 1..n] поверх обычного ряда состояния."""
    switch = [_btn(f"Выбрать {i}") for i in range(1, n + 1)]
    if not base:
        return switch
    return switch + [dict(_NEWLINE)] + list(base)


# ── HR-бот ───────────────────────────────────────────────────────────────────

def hr_main_menu() -> list:
    """Команды без параметров — параметризованные остаются текстом."""
    return [_hr_btn("Курсы"), _hr_btn("Отчёт"), _hr_btn("Руководители")]


def hr_course_actions(course_id: int) -> list:
    return [_hr_btn(f"Вопросы {course_id}"), _hr_btn(f"Подтвердить {course_id}")]


def hr_course_list(course_ids: list) -> list:
    rows: list = []
    for cid in course_ids[:MAX_COURSE_ROWS]:
        if rows:
            rows.append(dict(_NEWLINE))
        rows += hr_course_actions(cid)
    return rows


def hr_question_card(course_id: int, q_num: int) -> list:
    """Клавиатура карточки «Вопросы N.q» (сквозная нумерация 1–15)."""
    nav = []
    if q_num > 1:
        nav.append(_hr_btn("⬅️ Назад", f"Вопросы {course_id}.{q_num - 1}"))
    if q_num < 15:
        nav.append(_hr_btn("▶️ Далее", f"Вопросы {course_id}.{q_num + 1}"))
    else:
        nav.append(_hr_btn("✅ Подтвердить", f"Подтвердить {course_id}"))
    return nav + [
        dict(_NEWLINE),
        _hr_btn("✏️ Изменить", f"Изменить {course_id}.{q_num}"),
        _hr_btn("🔄 Заново", f"Перегенерировать {course_id}.{q_num}"),
        dict(_NEWLINE),
        _hr_btn("📄 Все вопросы", f"Вопросы {course_id} все"),
    ]


def hr_wizard_step() -> list:
    """Шаг визарда правки: «.» = оставить текущее значение."""
    return [_hr_btn("• Оставить", "."), _hr_btn("Отмена")]


def hr_wizard_confirm() -> list:
    return [_hr_btn("💾 Сохранить", "Сохранить"),
            _hr_btn("🔄 Заново", "Заново"), _hr_btn("Отмена")]


def hr_invite(email: str) -> list:
    return [_hr_btn(f"Пригласить {email}", display="BLOCK")]


def hr_cancel_edit() -> list:
    return [_hr_btn("Отмена")]


def hr_admit(user_id: str) -> list:
    return [_hr_btn(f"Допустить {user_id}", display="BLOCK")]
