"""Кнопки везде (PRPs/buttons-everywhere.md): HR-билдеры, роутинг /command
по имени команды, клавиатуры HR-веток и проактивных отправок — за флагом
BUTTONS_ENABLED. Паттерны — test_buttons.py."""
import asyncio
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.keyboards as kb
import app.state_machine as sm


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ── Чистые билдеры ───────────────────────────────────────────────────────────

def _btns(keyboard):
    return [b for b in keyboard if "TEXT" in b]


def test_hr_main_menu():
    menu = _btns(kb.hr_main_menu(True))
    assert [(b["TEXT"], b["COMMAND_PARAMS"]) for b in menu] == [
        ("📚 Курсы на проверке", "Курсы"),
        ("📊 Отчёт по обучению", "Отчёт"),
        ("👥 Руководители", "Руководители")]
    assert all(b["COMMAND"] == "hrsay" and b["DISPLAY"] == "BLOCK"
               for b in menu)
    assert menu[0]["BG_COLOR"] == "#BEDC3C"               # очередь непуста
    assert _btns(kb.hr_main_menu(False))[0]["BG_COLOR"] == "#DDEFF8"


def test_hr_course_list_pagination():
    rows = kb.hr_course_list([1, 2])
    assert [b.get("TEXT", "NL") for b in rows] == \
        ["Вопросы 1", "Подтвердить 1", "NL", "Вопросы 2", "Подтвердить 2"]
    page = kb.hr_course_list(list(range(1, 9)), page=1, remaining=12)
    assert sum(1 for b in page if "TEXT" in b) == 17      # 8 пар + «ещё»
    more = _btns(page)[-1]
    assert (more["TEXT"], more["COMMAND_PARAMS"]) == \
        ("Показать ещё 12", "Курсы 2")


def test_hr_param_buttons():
    assert kb.hr_invite("a@b.ru")[0]["COMMAND_PARAMS"] == "Пригласить a@b.ru"
    assert kb.hr_admit("500")[0]["COMMAND_PARAMS"] == "Допустить 500"
    assert kb.hr_cancel_edit()[0]["TEXT"] == "Отмена"
    assert all(x[0]["COMMAND"] == "hrsay" for x in
               (kb.hr_invite("a@b.ru"), kb.hr_admit("500"), kb.hr_cancel_edit()))


def test_start_button():
    btn = kb.start_button("Начать обучение")
    assert btn[0]["TEXT"] == "Начать обучение"
    assert btn[0]["COMMAND_PARAMS"] == "Начать"
    assert btn[0]["COMMAND"] == "say"
    assert btn[0]["DISPLAY"] == "BLOCK"


def test_courses_menu():
    """Дизайн 17.08: курсы BLOCK-кнопками (with_switch заменён)."""
    menu = _btns(kb.courses_menu([(1, "Курс А", "todo"),
                                  (2, "Курс Б", "admitted")], reading=True))
    assert [b["COMMAND_PARAMS"] for b in menu] == \
        ["Готов", "Выбрать 1", "Выбрать 2", "Роль"]
    assert menu[2]["TEXT"] == "🎓 2 · Курс Б"
    grid = _btns(kb.courses_menu([(i, f"К{i}", "todo") for i in range(1, 8)]))
    assert [b["COMMAND_PARAMS"] for b in grid] == \
        [f"Выбрать {i}" for i in range(1, 8)]


# ── HR-ветки за флагом ───────────────────────────────────────────────────────

@pytest.fixture
def hr_env(monkeypatch):
    captured = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        captured.append({"dialog": dialog_id, "text": text,
                         "keyboard": keyboard})

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setenv("HR_USER_IDS", "")      # гейт выключен (dev-режим)
    bot._recent_msgs.clear()
    bot._pending_edits.clear()
    return TestClient(bot.app), captured


def _post_hr(client, message, user="hr1", dialog="dhr"):
    return client.post("/hr", data={
        "data[PARAMS][MESSAGE]": message,
        "data[PARAMS][DIALOG_ID]": dialog,
        "data[PARAMS][FROM_USER_ID]": user,
    })


def test_hr_help_menu_keyboard(hr_env, monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    monkeypatch.setattr(bot, "get_pending_courses", lambda: [])
    client, captured = hr_env
    _post_hr(client, "непонятная команда")
    assert _wait_for(lambda: captured)
    menu = _btns(captured[0]["keyboard"])
    assert [b["COMMAND_PARAMS"] for b in menu] == \
        ["Курсы", "Отчёт", "Руководители"]
    assert menu[0]["BG_COLOR"] == "#DDEFF8"               # очередь пуста


def test_hr_no_keyboard_flag_off(hr_env):
    client, captured = hr_env
    _post_hr(client, "непонятная команда")
    assert _wait_for(lambda: captured)
    assert captured[0]["keyboard"] is None


def test_hr_courses_keyboard(hr_env, monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    monkeypatch.setattr(bot, "get_pending_courses",
                        lambda: [{"id": 3, "doc_name": "Док.docx"},
                                 {"id": 7, "doc_name": "Док2.docx"}])
    client, captured = hr_env
    _post_hr(client, "Курсы")
    assert _wait_for(lambda: captured)
    assert [b.get("TEXT", "NL") for b in captured[0]["keyboard"]] == \
        ["Вопросы 3", "Подтвердить 3", "NL", "Вопросы 7", "Подтвердить 7"]


def test_hr_questions_card_keyboard(hr_env, monkeypatch):
    """17.08: «Вопросы N» — карточка с ✅ и навигацией."""
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    q = {"text": "Что?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
         "correct": "B"}
    monkeypatch.setattr(bot, "get_course_by_id",
                        lambda cid: {"id": cid, "doc_name": "Д"})
    monkeypatch.setattr(bot, "get_course_questions",
                        lambda cid: {"basic_questions": [q],
                                     "exam_questions": []})
    client, captured = hr_env
    _post_hr(client, "Вопросы 5")
    assert _wait_for(lambda: captured)
    assert "B. 2 ✅" in captured[0]["text"]
    texts = [b["TEXT"] for b in captured[0]["keyboard"] if "TEXT" in b]
    assert texts == ["▶️ Далее", "✏️ Изменить", "🔄 Заново", "📄 Все вопросы"]
    # последняя карточка: «Далее» нет, необратимое — последним рядом
    last = [b for b in kb.hr_question_card(5, 15) if "TEXT" in b]
    assert "▶️ Далее" not in [b["TEXT"] for b in last]
    assert (last[-1]["TEXT"], last[-1]["COMMAND_PARAMS"]) == \
        ("Подтвердить и запустить", "Подтвердить 5")


def test_hr_edit_wizard_keyboards(hr_env, monkeypatch):
    """17.08: визард — [• Оставить][Отмена] на шагах, confirm-набор в конце."""
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    q = {"text": "?", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
         "correct": "A"}
    monkeypatch.setattr(bot, "get_course_by_id",
                        lambda cid: {"id": cid, "doc_name": "Д"})
    monkeypatch.setattr(bot, "get_course_questions",
                        lambda cid: {"basic_questions": [q],
                                     "exam_questions": []})
    client, captured = hr_env
    _post_hr(client, "Изменить 5.1")
    assert _wait_for(lambda: captured)
    assert [b["TEXT"] for b in _btns(captured[0]["keyboard"])] == \
        ["• Оставить", "Отмена"]
    # цельный блок по шаблону → сразу превью с confirm-клавиатурой
    _post_hr(client, "Новый вопрос?\nA. а\nB. б\nC. в\nD. г\nОтвет: A")
    assert _wait_for(lambda: len(captured) >= 2)
    assert "Проверь вопрос" in captured[1]["text"]
    assert [(b["TEXT"], b["COMMAND_PARAMS"])
            for b in _btns(captured[1]["keyboard"])] == \
        [("💾 Сохранить", "Сохранить"), ("🔄 Заново", "Заново"),
         ("Отмена", "Отмена")]


def test_hr_admit_waiting_in_place(hr_env, monkeypatch):
    """Сотрудник ждёт в курсе: «Начать экзамен» как раньше."""
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    waiting = {"id": 5, "course_id": 3, "dialog_id": "d_emp", "user_id": "500"}
    monkeypatch.setattr(bot, "get_waiting_session", lambda uid: waiting)
    admitted = []
    monkeypatch.setattr(bot, "admit_session", lambda sid: admitted.append(sid))
    monkeypatch.setattr(bot, "get_session", lambda uid: {"id": 5})
    monkeypatch.setattr(bot, "get_course_by_id",
                        lambda cid: {"doc_name": "Д.docx"})
    client, captured = hr_env
    _post_hr(client, "Допустить 500")
    assert _wait_for(lambda: len(captured) >= 2)
    assert admitted == [5]
    emp = next(c for c in captured if c["dialog"] == "d_emp")
    assert emp["keyboard"][0]["TEXT"] == "🎓 Начать экзамен"
    assert emp["keyboard"][0]["COMMAND_PARAMS"] == "Начать"


def test_hr_admit_parked_not_pulled(hr_env, monkeypatch):
    """17.08: сотрудник в другом курсе — допуск НЕ выдёргивает, зовёт в меню."""
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    waiting = {"id": 5, "course_id": 3, "dialog_id": "d_emp", "user_id": "500"}
    monkeypatch.setattr(bot, "get_waiting_session", lambda uid: waiting)
    monkeypatch.setattr(bot, "admit_session", lambda sid: None)
    monkeypatch.setattr(bot, "get_session", lambda uid: {"id": 9})
    monkeypatch.setattr(bot, "get_course_by_id",
                        lambda cid: {"doc_name": "Д.docx"})
    client, captured = hr_env
    _post_hr(client, "Допустить 500")
    assert _wait_for(lambda: len(captured) >= 2)
    emp = next(c for c in captured if c["dialog"] == "d_emp")
    assert "Мои курсы" in emp["text"]
    assert emp["keyboard"][0]["TEXT"] == "📚 Мои курсы"


# ── /command: роутинг по имени команды ───────────────────────────────────────

@pytest.fixture
def cmd_router(monkeypatch):
    calls = []

    async def fake_hr(user_id, question, dialog_id, client_id,
                      dedup_key=None):
        calls.append(("hr", user_id, question, dialog_id))

    async def fake_emp(user_id, question, dialog_id, client_id, bot_id=None,
                       dedup_key=None):
        calls.append(("emp", user_id, question, dialog_id))

    monkeypatch.setattr(bot, "_handle_hr_message", fake_hr)
    monkeypatch.setattr(bot, "_handle_employee_message", fake_emp)
    return TestClient(bot.app), calls


def test_command_hrsay_routes_to_hr(cmd_router):
    client, calls = cmd_router
    client.post("/command", data={
        "event": "ONIMCOMMANDADD",
        "data[COMMAND][0][COMMAND]": "hrsay",
        "data[COMMAND][0][COMMAND_PARAMS]": "Курсы",
        "data[COMMAND][0][DIALOG_ID]": "d9",
        "data[COMMAND][0][USER_ID]": "u5",
    })
    assert calls == [("hr", "u5", "Курсы", "d9")]


def test_command_live_format_command_id_index(cmd_router):
    """Живой формат портала (лог 17.08): индекс ключа = COMMAND_ID, не 0;
    DIALOG_ID/USER только в data[PARAMS]/data[USER]."""
    client, calls = cmd_router
    client.post("/command", data={
        "event": "ONIMCOMMANDADD",
        "data[COMMAND][71][BOT_ID]": "54858",
        "data[COMMAND][71][COMMAND]": "hrsay",
        "data[COMMAND][71][COMMAND_ID]": "71",
        "data[COMMAND][71][COMMAND_PARAMS]": "Курсы",
        "data[COMMAND][71][COMMAND_CONTEXT]": "KEYBOARD",
        "data[PARAMS][MESSAGE]": "/hrsay Курсы",
        "data[PARAMS][DIALOG_ID]": "28528",
        "data[USER][ID]": "28528",
    })
    assert calls == [("hr", "28528", "Курсы", "28528")]


def test_command_say_and_missing_name_route_to_employee(cmd_router):
    client, calls = cmd_router
    client.post("/command", data={
        "event": "ONIMCOMMANDADD",
        "data[COMMAND][0][COMMAND]": "say",
        "data[COMMAND][0][COMMAND_PARAMS]": "A",
        "data[COMMAND][0][DIALOG_ID]": "d1",
        "data[COMMAND][0][USER_ID]": "u1",
    })
    client.post("/command", data={           # поле имени не пришло вовсе
        "event": "ONIMCOMMANDADD",
        "data[PARAMS][COMMAND_PARAMS]": "B",
        "data[PARAMS][DIALOG_ID]": "d2",
        "data[USER][ID]": "u2",
    })
    assert calls == [("emp", "u1", "A", "d1"), ("emp", "u2", "B", "d2")]


def test_command_dedup_by_source_message(monkeypatch):
    """Та же надпись кнопки на РАЗНЫХ сообщениях → оба нажатия проходят;
    двойной клик той же кнопки (тот же MESSAGE_ID) → гасится (лог 17.08)."""
    replies = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        replies.append(text)

    monkeypatch.setattr(bot, "_send", fake_send)
    monkeypatch.setattr(bot, "process_message", lambda *a, **kw: "ОТВЕТ")
    monkeypatch.setattr(bot, "get_session", lambda uid: None)
    monkeypatch.setattr(bot, "_retake_fork_text", lambda uid: None)
    bot._recent_msgs.clear()
    client = TestClient(bot.app)

    def press(msg_id):
        client.post("/command", data={
            "event": "ONIMCOMMANDADD",
            "data[COMMAND][70][COMMAND]": "say",
            "data[COMMAND][70][COMMAND_PARAMS]": "Мои курсы",
            "data[COMMAND][70][MESSAGE_ID]": msg_id,
            "data[PARAMS][DIALOG_ID]": "d1",
            "data[USER][ID]": "u9",
        })

    press("100")
    press("101")            # та же надпись, другое сообщение → проходит
    press("101")            # двойной клик той же кнопки → dedup
    assert _wait_for(lambda: len(replies) >= 2)
    time.sleep(0.05)
    assert len(replies) == 2


# ── notify_hr + кнопка «Допустить» из _finish_phase ──────────────────────────

def test_notify_hr_keyboard_key(monkeypatch):
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append(json)

        class R:
            status_code = 200
        return R()

    monkeypatch.setattr(sm.httpx, "post", fake_post)
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "http://x/")
    monkeypatch.setenv("HR_USER_IDS", "77")
    sm.notify_hr("тест")
    assert "KEYBOARD" not in sent[0]          # ключа нет вовсе при None
    sm.notify_hr("тест2", keyboard=[{"TEXT": "Допустить 5"}])
    assert sent[1]["KEYBOARD"] == [{"TEXT": "Допустить 5"}]


def test_finish_basic_admit_button_env_gated(monkeypatch):
    monkeypatch.setattr(sm, "get_session_answers", lambda sid, phase: [])
    monkeypatch.setattr(sm, "update_session", lambda *a, **kw: None)
    monkeypatch.setattr(sm, "_employee_label", lambda uid: "X")
    captured = {}
    monkeypatch.setattr(
        sm, "notify_hr",
        lambda msg, keyboard=None: captured.update(kb=keyboard))
    session = {"id": 1, "user_id": "500"}

    monkeypatch.setenv("BUTTONS_ENABLED", "1")
    sm._finish_phase(session, "basic", {"basic_questions": []})
    assert captured["kb"] == kb.hr_admit("500")

    monkeypatch.setenv("BUTTONS_ENABLED", "0")
    sm._finish_phase(session, "basic", {"basic_questions": []})
    assert captured["kb"] is None


def test_my_courses_open_state_labels(monkeypatch):
    """17.08: висящие строки курсов подписаны — ждёт допуска / допущен."""
    courses = [{"id": 1, "doc_name": "A.docx"}, {"id": 2, "doc_name": "B.docx"},
               {"id": 3, "doc_name": "C.docx"}]
    monkeypatch.setattr(sm, "get_active_courses", lambda: courses)
    monkeypatch.setattr(sm, "_done_course_ids", lambda uid: set())
    monkeypatch.setattr(sm, "get_session",
                        lambda uid: {"course_id": 1, "state": "READING"})
    monkeypatch.setattr(sm, "get_sessions_by_user", lambda uid: [
        {"course_id": 2, "state": "WAITING_HR"},
        {"course_id": 3, "state": "EXAM"},
    ])
    text, sel = sm.my_courses("u1", None)
    assert "ждёт допуска HR" in text
    assert "допущен к экзамену" in text
    assert len(sel) == 2 and "напиши *Выбрать" in text


def test_bare_dialog():
    """«uNNN» → 400 DIALOG_ID_EMPTY на портале (живой прогон 17.08)."""
    assert sm.bare_dialog("u28528") == "28528"
    assert sm.bare_dialog("28528") == "28528"
    assert sm.bare_dialog("chat123") == "chat123"
    assert sm.bare_dialog("uchk_u1") == "uchk_u1"   # не-числовой хвост не трогаем


# ── Проактивные отправки employee-бота ───────────────────────────────────────

@pytest.fixture
def send_trap(monkeypatch):
    captured = []

    async def fake_send(dialog_id, text, bot_id=None, client_id="",
                        keyboard=None):
        captured.append({"dialog": dialog_id, "text": text,
                         "keyboard": keyboard})

    monkeypatch.setattr(bot, "_send", fake_send)
    return captured


def test_broadcast_course_start_button(send_trap, monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    asyncio.run(bot._broadcast_course({"doc_name": "Д.docx"}, ["1", "2"]))
    assert len(send_trap) == 2
    assert all(c["keyboard"][0]["COMMAND_PARAMS"] == "Начать"
               for c in send_trap)


def test_broadcast_course_no_keyboard_flag_off(send_trap, monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", False)
    asyncio.run(bot._broadcast_course({"doc_name": "Д.docx"}, ["1"]))
    assert send_trap[0]["keyboard"] is None


def test_reminders_carry_state_keyboard(send_trap, monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    monkeypatch.setattr(bot, "get_session",
                        lambda uid: {"course_id": 1, "state": "READING"})
    monkeypatch.setattr(bot, "_deadline_items", lambda: [{"uid": "9"}])
    monkeypatch.setattr(bot.deadlines, "build_reminder_text",
                        lambda items: "напоминание")
    monkeypatch.setattr(bot, "get_meta", lambda k: None)
    monkeypatch.setattr(bot, "set_meta", lambda k, v: None)
    asyncio.run(bot._maybe_send_reminders(datetime(2026, 8, 17, 12, 0)))
    texts = [b["TEXT"] for b in send_trap[0]["keyboard"] if "TEXT" in b]
    assert texts == ["✅ Готов к тесту", "📚 Мои курсы", "📄 Документы",
                     "Роль"]


def test_notify_hr_about_user_invite_button(send_trap, monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    monkeypatch.setenv("HR_USER_IDS", "77")
    asyncio.run(bot._notify_hr_about_user(
        {"ID": 5, "NAME": "Иван", "LAST_NAME": "Иванов", "EMAIL": "i@x.ru"},
        transfer=False))
    assert send_trap[0]["keyboard"][0]["COMMAND_PARAMS"] == "Пригласить i@x.ru"
    # перевод отдела — кнопки нет (сотруднику самому писать «Роль»)
    send_trap.clear()
    asyncio.run(bot._notify_hr_about_user({"ID": 5}, transfer=True))
    assert send_trap[0]["keyboard"] is None


def test_invite_autostart_keyboard(hr_env, monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    monkeypatch.setattr(bot, "selectable_roles", lambda: [("a", "A"), ("b", "B")])

    async def fake_lookup(email):
        return {"ID": "500", "NAME": "Иван", "LAST_NAME": "Иванов",
                "EMAIL": "ivan@x.ru"}

    monkeypatch.setattr(bot, "_bitrix_user_by_email", fake_lookup)
    monkeypatch.setattr(bot, "add_employee", lambda *a, **kw: None)
    started = {}
    monkeypatch.setattr(bot, "start_onboarding",
                        lambda uid, d: started.update(uid=uid) or "Выбери роль")
    monkeypatch.setattr(
        bot, "get_session",
        lambda uid: ({"course_id": 0, "state": "ROLE_SELECT"}
                     if started else None))
    client, captured = hr_env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: any(c["dialog"] == "u500" for c in captured))
    emp = next(c for c in captured if c["dialog"] == "u500")
    assert [(b["TEXT"], b["COMMAND_PARAMS"])
            for b in _btns(emp["keyboard"])] == [("1 · A", "1"), ("2 · B", "2")]


# ── «Выбрать N» после «Мои курсы» ────────────────────────────────────────────

def _run_employee(question):
    async def run():
        await bot._handle_employee_message("u3", question, "d5", "")
        await asyncio.sleep(0)
    asyncio.run(run())


def test_my_courses_switch_buttons(send_trap, monkeypatch):
    monkeypatch.setattr(bot, "BUTTONS_ENABLED", True)
    monkeypatch.setattr(bot, "get_session",
                        lambda uid: {"course_id": 1, "state": "READING",
                                     "role": "fo"})
    monkeypatch.setattr(bot, "process_message", lambda *a, **kw: "список")
    monkeypatch.setattr(
        bot, "my_courses",
        lambda uid, role: ("t", [
            {"id": 2, "n": 1, "status": "todo", "doc_name": "А.docx"},
            {"id": 3, "n": 2, "status": "admitted", "doc_name": "Б.docx"},
        ]))
    bot._recent_msgs.clear()
    _run_employee("Мои курсы")
    btns = [b for b in send_trap[0]["keyboard"] if "TEXT" in b]
    assert [b["COMMAND_PARAMS"] for b in btns] == \
        ["Готов", "Выбрать 1", "Выбрать 2", "Роль"]
    assert btns[2]["TEXT"] == "🎓 2 · Б"                   # допущенный курс

    # обычный вопрос в READING → чистый ряд без «Выбрать»
    send_trap.clear()
    _run_employee("что такое чек-ин?")
    texts = [b["TEXT"] for b in send_trap[0]["keyboard"] if "TEXT" in b]
    assert texts == ["✅ Готов к тесту", "📚 Мои курсы", "📄 Документы",
                     "Роль"]
