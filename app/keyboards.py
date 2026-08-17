"""
app/keyboards.py — инлайн-кнопки обоих ботов (за флагом BUTTONS_ENABLED).

Дизайн 17.08 (спека claude design): 5 цветовых ролей, BLOCK/WIDTH/NEWLINE-
раскладки, мобильный бюджет ряда ≤300px, ≤1 primary на клавиатуру.
ЖЕЛЕЗНЫЙ ИНВАРИАНТ: TEXT — витрина, COMMAND_PARAMS — прежние команды,
которые FSM/HR-диспетчер понимают («✅ Готов к тесту» шлёт «Готов»).

Employee-бот — команда «say», HR-бот — «hrsay»: /command роутит по имени
команды. Событие нажатия приходит ONIMCOMMANDADD на POST /command.
Все билдеры чистые (без БД) — данные передаются параметрами из bitrix_bot.
"""

# Роли кнопок: (BG_COLOR, TEXT_COLOR). Белый на лайме запрещён (нечитаем).
_ROLES = {
    "primary":   ("#BEDC3C", "#004664"),   # главное действие, ≤1, всегда BLOCK
    "secondary": ("#DDEFF8", "#004664"),   # навигация, равные варианты, A–D
    "service":   ("#EBF6FB", "#778592"),   # Отмена, Роль, «Показать ещё»
    "review":    ("#D6EFEC", "#007D70"),   # HR: «Вопросы N» перед подтверждением
    "danger":    ("#004664", "#FFFFFF"),   # необратимое: BLOCK, последний ряд
}
_NEWLINE = {"TYPE": "NEWLINE"}

HR_COURSES_PAGE = 8   # список «Курсы на проверке» — постранично


def _trim(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


def _btn(text: str, payload: str = None, display: str = "LINE",
         command: str = "say", role: str = "secondary",
         width: int = None) -> dict:
    bg, fg = _ROLES[role]
    btn = {"TEXT": text, "COMMAND": command, "COMMAND_PARAMS": payload or text,
           "DISPLAY": display, "BG_COLOR": bg, "TEXT_COLOR": fg}
    if display == "LINE" and width:
        btn["WIDTH"] = width   # без WIDTH ряды LINE скачут между темами
    return btn


def _hr_btn(text: str, payload: str = None, display: str = "LINE",
            role: str = "secondary", width: int = None) -> dict:
    return _btn(text, payload, display, command="hrsay", role=role, width=width)


# ── Employee-бот ─────────────────────────────────────────────────────────────

def for_session(session: dict | None, fork: bool = False,
                role_options: list | None = None,
                test_options: list | None = None) -> list | None:
    """session — СВЕЖАЯ сессия (после process_message); fork — развилка
    «Пересдать/Далее» (№9); role_options — selectable_roles();
    test_options — 4 ТЕЛА вариантов текущего вопроса (без «A. »)."""
    if session is None:
        if fork:
            return [_btn("🔁 Пересдать экзамен", "Пересдать",
                         display="BLOCK", role="primary"),
                    dict(_NEWLINE),
                    _btn("Далее, к следующему", "Далее", width=180)]
        return None
    state = session["state"]
    if state == "ROLE_SELECT":
        opts = role_options or []
        if not opts:
            return None
        if len(opts) <= 8:   # имена ролей на кнопках; payload — цифра (FSM)
            return [_btn(f"{i} · {_trim(name, 38)}", str(i), display="BLOCK")
                    for i, (_rid, name) in enumerate(opts, 1)]
        rows: list = []      # длинный список — сетка цифр 5×52 (292px)
        for i in range(1, len(opts) + 1):
            if i > 1 and (i - 1) % 5 == 0:
                rows.append(dict(_NEWLINE))
            rows.append(_btn(str(i), width=52))
        return rows
    if state == "READING":
        return [_btn("✅ Готов к тесту", "Готов", display="BLOCK",
                     role="primary"),
                dict(_NEWLINE),
                _btn("📚 Мои курсы", "Мои курсы", width=150),
                _btn("Роль", width=110, role="service")]
    if state in ("BASIC_TEST", "EXAM"):
        # Короткие варианты (все ≤24) — BLOCK с текстом: нажатие не требует
        # сверки с сообщением; payload — буква (parse_answer)
        if (test_options and len(test_options) == 4
                and all(len(b) <= 24 for b in test_options)):
            return [_btn(f"{letter} · {body}", letter, display="BLOCK")
                    for letter, body in zip("ABCD", test_options)]
        return [_btn(x, width=70) for x in ("A", "B", "C", "D")]
    if state == "WAITING_HR":
        # Действовать нечему — primary здесь быть не должно (спека)
        return [_btn("📚 Мои курсы", "Мои курсы", display="BLOCK")]
    return None


_STATUS_EMOJI = {"admitted": "🎓", "waiting": "⏳", "todo": "⏳"}


def courses_menu(items: list, reading: bool = False) -> list | None:
    """Ответ на «Мои курсы»: items = [(n, name, status)] ТОЛЬКО выбираемых
    курсов (n = номер «Выбрать N» из my_courses — FSM-совместимость).
    ≤6 — BLOCK с названием (≤38…), больше — сетка «Выбрать N» 3×96.
    Текущий и пройденные курсы кнопками не делаются (нажимать незачем)."""
    rows: list = []
    if reading:
        rows += [_btn("✅ Готов к тесту", "Готов", display="BLOCK",
                      role="primary"), dict(_NEWLINE)]
    if len(items) <= 6:
        for n, name, status in items:
            emoji = _STATUS_EMOJI.get(status, "⏳")
            rows.append(_btn(f"{emoji} {n} · {_trim(name, 38)}",
                             f"Выбрать {n}", display="BLOCK"))
            rows.append(dict(_NEWLINE))
    else:
        for idx, (n, _name, _status) in enumerate(items):
            if idx and idx % 3 == 0:
                rows.append(dict(_NEWLINE))
            rows.append(_btn(f"Выбрать {n}", width=96))
        rows.append(dict(_NEWLINE))
    if reading:
        rows.append(_btn("Роль", width=110, role="service"))
    elif rows and rows[-1].get("TYPE") == "NEWLINE":
        rows.pop()
    return rows or None


def start_button(label: str, payload: str = "Начать",
                 role: str = "primary") -> list:
    """Проактивные одиночные BLOCK-кнопки: «Начать…» — primary (смысл
    push-уведомления), навигация — secondary. Второй кнопки не добавлять."""
    return [_btn(label, payload, display="BLOCK", role=role)]


# ── HR-бот ───────────────────────────────────────────────────────────────────

def hr_main_menu(has_pending: bool = True) -> list:
    """Стопка BLOCK; «Курсы на проверке» — primary только при непустой
    очереди (лайм отмечает то, ради чего HR открывает бота)."""
    return [
        _hr_btn("📚 Курсы на проверке", "Курсы", display="BLOCK",
                role="primary" if has_pending else "secondary"),
        dict(_NEWLINE),
        _hr_btn("📊 Отчёт по обучению", "Отчёт", display="BLOCK"),
        dict(_NEWLINE),
        _hr_btn("👥 Руководители", "Руководители", display="BLOCK"),
    ]


def hr_course_list(course_ids: list, page: int = 1, remaining: int = 0) -> list:
    """Страница списка на проверке (по HR_COURSES_PAGE): ряд на курс —
    «Вопросы» (проверка) слева от необратимого «Подтвердить». 130+160+8=298px."""
    rows: list = []
    for cid in course_ids:
        if rows:
            rows.append(dict(_NEWLINE))
        rows.append(_hr_btn(f"Вопросы {cid}", width=130, role="review"))
        rows.append(_hr_btn(f"Подтвердить {cid}", width=160, role="danger"))
    if remaining > 0:
        rows.append(dict(_NEWLINE))
        rows.append(_hr_btn(f"Показать ещё {remaining}", f"Курсы {page + 1}",
                            display="BLOCK", role="service"))
    return rows


def hr_new_course_actions(course_id: int) -> list:
    """Уведомление «Новый документ»: проверка, затем необратимое действие
    отдельным последним рядом (текст кнопки называет последствие)."""
    return [_hr_btn(f"Вопросы {course_id}", width=160, role="review"),
            dict(_NEWLINE),
            _hr_btn(f"Подтвердить и запустить {course_id}",
                    f"Подтвердить {course_id}", display="BLOCK", role="danger")]


def hr_course_actions(course_id: int) -> list:
    """Пара под простынёй «Вопросы N все»."""
    return [_hr_btn(f"Вопросы {course_id}", width=130, role="review"),
            _hr_btn(f"Подтвердить {course_id}", width=160, role="danger")]


def hr_question_card(course_id: int, q_num: int) -> list:
    """Клавиатура карточки «Вопросы N.q» (сквозная нумерация 1–15)."""
    rows: list = []
    if q_num > 1:
        rows.append(_hr_btn("⬅️ Назад", f"Вопросы {course_id}.{q_num - 1}",
                            width=96))
    if q_num < 15:
        rows.append(_hr_btn("▶️ Далее", f"Вопросы {course_id}.{q_num + 1}",
                            width=96))
    rows += [
        dict(_NEWLINE),
        _hr_btn("✏️ Изменить", f"Изменить {course_id}.{q_num}", width=130),
        _hr_btn("🔄 Заново", f"Перегенерировать {course_id}.{q_num}", width=130),
        dict(_NEWLINE),
        _hr_btn("📄 Все вопросы", f"Вопросы {course_id} все", width=160,
                role="service"),
    ]
    if q_num == 15:   # необратимое — последним рядом
        rows += [dict(_NEWLINE),
                 _hr_btn("Подтвердить и запустить", f"Подтвердить {course_id}",
                         display="BLOCK", role="danger")]
    return rows


def hr_wizard_step() -> list:
    """Шаг визарда: ввод идёт текстом — кнопки тише всего вокруг."""
    return [_hr_btn("• Оставить", ".", width=130, role="service"),
            _hr_btn("Отмена", width=120, role="service")]


def hr_wizard_confirm() -> list:
    return [_hr_btn("💾 Сохранить", "Сохранить", display="BLOCK",
                    role="primary"),
            dict(_NEWLINE),
            _hr_btn("🔄 Заново", "Заново", width=130),
            _hr_btn("Отмена", width=120, role="service")]


def hr_cancel_edit() -> list:
    return [_hr_btn("Отмена", width=120, role="service")]


def hr_invite(email: str) -> list:
    """TEXT обрезан до 34 (длинный e-mail разворачивает BLOCK в 2 строки),
    payload — полный адрес."""
    return [_hr_btn(_trim(f"Пригласить {email}", 34), f"Пригласить {email}",
                    display="BLOCK", role="primary")]


def hr_admit(user_id: str, label: str = None) -> list:
    """TEXT — имя сотрудника (ID ничего не говорит нажимающему),
    payload — команда с uid."""
    return [_hr_btn(_trim(f"Допустить {label or user_id}", 34),
                    f"Допустить {user_id}", display="BLOCK", role="primary")]
