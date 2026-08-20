"""Дизайн клавиатур 17.08 (PRPs/keyboard-design.md): payload-инварианты
(TEXT — витрина, COMMAND_PARAMS — прежние команды FSM), ≤1 primary,
WIDTH у каждой LINE, бюджеты рядов ≤304px, ролевые пары цветов, раскладки."""
import re

import app.keyboards as kb

# Все клавиатуры всех билдеров — общий прогон инвариантов
SAMPLES = {
    "fork": kb.for_session(None, fork=True),
    "roles_2": kb.for_session({"state": "ROLE_SELECT"},
                              role_options=[("a", "Ресепшн"),
                                            ("b", "Хаускипинг")]),
    "roles_17": kb.for_session({"state": "ROLE_SELECT"},
                               role_options=[(f"r{i}", f"Роль {i}")
                                             for i in range(17)]),
    "reading": kb.for_session({"state": "READING"}),
    "test_letters": kb.for_session({"state": "BASIC_TEST"}),
    "test_block": kb.for_session(
        {"state": "EXAM"},
        test_options=["До заезда гостя", "В день заезда",
                      "После выезда", "В любой момент"]),
    "waiting": kb.for_session({"state": "WAITING_HR"}),
    "menu_small": kb.courses_menu([(1, "Курс о конфиденциальности", "todo"),
                                   (2, "Пожарная безопасность", "waiting"),
                                   (3, "Бронирование", "admitted")],
                                  reading=True),
    "menu_grid": kb.courses_menu([(i, f"Курс {i}", "todo")
                                  for i in range(1, 8)]),
    "start": kb.start_button("▶ Начать обучение"),
    "start_menu": kb.start_button("📚 Мои курсы", "Мои курсы",
                                  role="secondary"),
    "hr_menu": kb.hr_main_menu(True),
    "hr_menu_empty": kb.hr_main_menu(False),
    "hr_list": kb.hr_course_list([1, 2], 1, 0),
    "hr_list_page": kb.hr_course_list(list(range(1, 9)), 1, 12),
    "hr_new": kb.hr_new_course_actions(7),
    "hr_flat": kb.hr_course_actions(7),
    "card_1": kb.hr_question_card(5, 1),
    "card_7": kb.hr_question_card(5, 7),
    "card_15": kb.hr_question_card(5, 15),
    "wiz_step": kb.hr_wizard_step(),
    "wiz_confirm": kb.hr_wizard_confirm(),
    "cancel": kb.hr_cancel_edit(),
    "invite": kb.hr_invite("a.petrova@becar.ru"),
    "admit": kb.hr_admit("28528", "Анна Петрова"),
}

# ЖЕЛЕЗНЫЙ ИНВАРИАНТ: payload'ы — только команды, которые понимает FSM/HR
ALLOWED_PAYLOADS = [
    r"^(Готов|Мои курсы|Роль|Пересдать|Далее|Начать|Отмена|Сохранить|Заново|\.)$",
    r"^(Документы|Выйти)$",                 # 19.08: «Мои документы», пауза теста
    r"^[A-D]$",
    r"^\d{1,2}$",                       # выбор роли
    r"^Выбрать \d+$",
    r"^(Курсы|Отчёт|Руководители)$",
    r"^Курсы \d+$",                     # пагинация
    r"^Вопросы \d+(\.\d+)?( все)?$",
    r"^Подтвердить \d+$",
    r"^Изменить \d+\.\d+$",
    r"^Перегенерировать \d+(\.\d+)?$",
    r"^Пригласить \S+@\S+$",
    r"^Допустить \S+$",
]


def _buttons(keyboard):
    return [b for b in keyboard if "TEXT" in b]


def _rows(keyboard):
    rows, cur = [], []
    for el in keyboard:
        if el.get("TYPE") == "NEWLINE":
            if cur:
                rows.append(cur)
            cur = []
        elif "TEXT" in el:
            cur.append(el)
    if cur:
        rows.append(cur)
    return rows


def test_payload_invariants():
    for name, keyboard in SAMPLES.items():
        for b in _buttons(keyboard):
            p = b["COMMAND_PARAMS"]
            assert any(re.match(rx, p) for rx in ALLOWED_PAYLOADS), \
                f"{name}: неожиданный payload {p!r} (TEXT={b['TEXT']!r})"


def test_at_most_one_primary():
    for name, keyboard in SAMPLES.items():
        primary = [b for b in _buttons(keyboard)
                   if b["BG_COLOR"] == "#BEDC3C"]
        assert len(primary) <= 1, f"{name}: {len(primary)} primary-кнопок"
        for b in primary:
            assert b["DISPLAY"] == "BLOCK", f"{name}: primary не BLOCK"


def test_line_width_and_row_budget():
    for name, keyboard in SAMPLES.items():
        for b in _buttons(keyboard):
            if b["DISPLAY"] == "LINE":
                assert b.get("WIDTH"), f"{name}: LINE без WIDTH: {b['TEXT']!r}"
        for row in _rows(keyboard):
            lines = [b for b in row if b["DISPLAY"] == "LINE"]
            if len(lines) > 1:
                budget = sum(b["WIDTH"] for b in lines) + 8 * (len(lines) - 1)
                assert budget <= 304, f"{name}: ряд {budget}px > 304"


def test_role_color_pairs():
    pairs = set(kb._ROLES.values())
    for name, keyboard in SAMPLES.items():
        for b in _buttons(keyboard):
            assert (b["BG_COLOR"], b["TEXT_COLOR"]) in pairs, \
                f"{name}: чужая пара цветов у {b['TEXT']!r}"
    # белый на лайме запрещён спекой
    assert ("#BEDC3C", "#FFFFFF") not in pairs


def test_role_select_layouts():
    small = _buttons(SAMPLES["roles_2"])
    assert [(b["TEXT"], b["COMMAND_PARAMS"]) for b in small] == \
        [("1 · Ресепшн", "1"), ("2 · Хаускипинг", "2")]
    assert all(b["DISPLAY"] == "BLOCK" for b in small)
    grid_rows = _rows(SAMPLES["roles_17"])
    assert [len(r) for r in grid_rows] == [5, 5, 5, 2]
    assert all(b["WIDTH"] == 52 for r in grid_rows for b in r)


def test_test_options_block_vs_letters():
    block = _buttons(SAMPLES["test_block"])
    assert [(b["TEXT"], b["COMMAND_PARAMS"]) for b in block][0] == \
        ("A · До заезда гостя", "A")
    answers = [b for b in block if b["COMMAND_PARAMS"] != "Выйти"]
    assert all(b["DISPLAY"] == "BLOCK" for b in answers)
    assert block[-1]["COMMAND_PARAMS"] == "Выйти"          # 19.08: пауза теста
    # длинный вариант → буквы
    long = kb.for_session({"state": "EXAM"},
                          test_options=["х" * 30, "а", "б", "в"])
    letters = [b for b in _buttons(long) if b["COMMAND_PARAMS"] != "Выйти"]
    assert [b["COMMAND_PARAMS"] for b in letters] == ["A", "B", "C", "D"]
    assert all(b["DISPLAY"] == "LINE" and b["WIDTH"] == 70 for b in letters)


def test_courses_menu_layouts():
    small = _buttons(SAMPLES["menu_small"])
    assert small[0]["COMMAND_PARAMS"] == "Готов"           # primary сверху
    assert (small[1]["TEXT"], small[1]["COMMAND_PARAMS"]) == \
        ("⏳ 1 · Курс о конфиденциальности", "Выбрать 1")
    assert small[3]["TEXT"].startswith("🎓 3 · ")          # допущен
    assert small[-1]["COMMAND_PARAMS"] == "Роль"           # ряд состояния
    grid = _buttons(SAMPLES["menu_grid"])
    assert [b["COMMAND_PARAMS"] for b in grid] == \
        [f"Выбрать {i}" for i in range(1, 8)]
    assert all(b["WIDTH"] == 96 for b in grid)
    assert [len(r) for r in _rows(SAMPLES["menu_grid"])] == [3, 3, 1]
    # обрезка 38
    long = _buttons(kb.courses_menu([(1, "х" * 60, "todo")]))[0]
    assert long["TEXT"].endswith("…") and len(long["TEXT"]) <= 38 + 8


def test_hr_list_pagination():
    page = SAMPLES["hr_list_page"]
    pairs = [b for b in _buttons(page) if b["COMMAND_PARAMS"] != "Курсы 2"]
    assert len(pairs) == 16                                # 8 курсов × 2
    more = _buttons(page)[-1]
    assert (more["TEXT"], more["COMMAND_PARAMS"]) == \
        ("Показать ещё 12", "Курсы 2")
    assert more["DISPLAY"] == "BLOCK" and more["BG_COLOR"] == "#EBF6FB"
    # порядок в паре: проверка (review) ПЕРЕД необратимым (danger)
    first_row = _rows(page)[0]
    assert first_row[0]["BG_COLOR"] == "#D6EFEC"
    assert first_row[1]["BG_COLOR"] == "#004664"


def test_danger_is_last_row():
    for name in ("hr_new", "card_15"):
        rows = _rows(SAMPLES[name])
        last = rows[-1]
        assert len(last) == 1 and last[0]["BG_COLOR"] == "#004664"
        assert last[0]["DISPLAY"] == "BLOCK"
        assert last[0]["COMMAND_PARAMS"].startswith("Подтвердить ")
        assert "запустить" in last[0]["TEXT"]
    # на непоследней карточке danger-кнопки нет
    assert not any(b["BG_COLOR"] == "#004664"
                   for b in _buttons(SAMPLES["card_7"]))


def test_admit_and_invite_trim():
    admit = _buttons(SAMPLES["admit"])[0]
    assert admit["TEXT"] == "Допустить Анна Петрова"
    assert admit["COMMAND_PARAMS"] == "Допустить 28528"
    long = _buttons(kb.hr_admit("1", "Оченьдлинное Имяфамилия Отчество"))[0]
    assert long["TEXT"].endswith("…") and len(long["TEXT"]) == 34
    invite = _buttons(kb.hr_invite("very.long.address@subdomain.becar.ru"))[0]
    assert len(invite["TEXT"]) == 34
    assert invite["COMMAND_PARAMS"] == \
        "Пригласить very.long.address@subdomain.becar.ru"
