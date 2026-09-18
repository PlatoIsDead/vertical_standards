"""hr_handler: «Пригласить», HR-гейт, «Допустить {email}» — TestClient, без сети.

ВАЖНО: import app.bitrix_bot исполняет init_db() и load_index() на живых файлах
(идемпотентно; живую БД тесты НЕ мутируют — DB_PATH подменяется до любых записей).
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import app.bitrix_bot as bot
import app.db as db
import app.roles as roles

QUESTIONS = {"course_summary": "О курсе.", "basic_questions": [], "exam_questions": []}


def _wait_for(predicate, timeout=2.0):
    """Ответы hr_handler уходят через asyncio.create_task — дожидаемся тика лупа."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _post_hr(client, message, from_user="9", dialog="d9"):
    return client.post("/hr", data={
        "data[PARAMS][MESSAGE]": message,
        "data[PARAMS][DIALOG_ID]": dialog,
        "data[PARAMS][FROM_USER_ID]": from_user,
    })


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()

    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"housekeeper": "Горничная / Уборщица", "all_staff": "Все сотрудники"},
        "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles, "CONFIG_PATH", str(cfg))

    course_id = db.save_draft_course(
        "Стандарты.docx", "1", json.dumps(QUESTIONS, ensure_ascii=False)
    )
    db.activate_course_by_id(course_id, "hr9")

    monkeypatch.setenv("HR_USER_IDS", "9")
    bot._recent_msgs.clear()  # дедуп _is_duplicate переживает тесты — чистим
    bot._pending_edits.clear()  # 01.09: pending выбора роли тоже переживает

    sent = []

    async def fake_send(dialog_id, text, bot_id=None, client_id=""):
        sent.append((dialog_id, text, bot_id))

    monkeypatch.setattr(bot, "_send", fake_send)

    async def fake_lookup(email):
        if email == "ivan@x.ru":
            return {"ID": "500", "NAME": "Иван", "LAST_NAME": "Иванов",
                    "EMAIL": "ivan@x.ru"}
        if email == "petr@x.ru":
            return {"ID": "600", "NAME": "Пётр", "LAST_NAME": "Петров",
                    "EMAIL": "petr@x.ru"}
        return None

    monkeypatch.setattr(bot, "_bitrix_user_by_email", fake_lookup)
    return TestClient(bot.app), sent, course_id


def test_invite_flow(env):
    """01.09: роль выбирает HR цифрой ПОСЛЕ «Пригласить» — сотрудник меню
    ролей не видит, до выбора он даже не в whitelist («Отмена» = не приглашён)."""
    client, sent, _ = env
    r = _post_hr(client, "Пригласить ivan@x.ru")
    assert r.status_code == 200

    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    prompt = next(m for m in sent if m[0] == "d9")
    assert "Выбери роль" in prompt[1] and "1. Горничная" in prompt[1]
    assert not db.is_employee_allowed("500")
    assert db.get_session("500") is None

    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_session("500") is not None)

    assert db.is_employee_allowed("500")
    emp = db.get_employee_by_email("ivan@x.ru")
    assert emp["full_name"] == "Иван Иванов" and emp["added_by"] == "9"
    assert emp["role"] == "housekeeper"

    session = db.get_session("500")
    assert session["state"] == "READING"
    assert session["role"] == "housekeeper"
    assert session["dialog_id"] == "u500"

    # Первое сообщение сотруднику — от EMPLOYEE-бота (не HR-бота)
    assert _wait_for(lambda: any(m[0] == "u500" for m in sent))
    emp_msg = next(m for m in sent if m[0] == "u500")
    assert emp_msg[2] == bot.BOT_ID
    assert "Горничная" in emp_msg[1] and "назначил HR" in emp_msg[1]

    assert _wait_for(lambda: any("приглашён" in m[1] for m in sent
                                 if m[0] == "d9"))
    hr_msg = next(m for m in sent if m[0] == "d9" and "приглашён" in m[1])
    assert "Горничная" in hr_msg[1]          # подтверждение называет роль


def test_invite_unknown_email(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить ghost@x.ru")

    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "не найден" in next(m for m in sent if m[0] == "d9")[1]
    assert db.get_employee_by_email("ghost@x.ru") is None
    assert db.get_session("ghost") is None


def test_invite_requires_email(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "Напиши email" in next(m for m in sent if m[0] == "d9")[1]


def test_invite_already_learning(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_session("500") is not None)
    bot._recent_msgs.clear()

    _post_hr(client, "Пригласить ivan@x.ru", dialog="d9b")
    assert _wait_for(lambda: any(m[0] == "d9b" for m in sent))
    assert "уже проходит обучение" in next(m for m in sent if m[0] == "d9b")[1]


def test_invite_role_pick_starts_course_without_file(env):
    """01.09: роль выбрал HR → курс назначен сразу (без ROLE_SELECT у
    сотрудника). 18.09: файл документа больше не вкладывается — только
    сообщение от employee-бота со ссылкой на документ."""
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "1")
    assert _wait_for(lambda: any(m[0] == "u500" for m in sent))
    session = db.get_session("500")
    assert session["state"] == "READING" and session["role"] == "housekeeper"
    first = next(m for m in sent if m[0] == "u500")
    assert first[2] == bot.BOT_ID and "Документ" in first[1]
    assert bot._get_pending("9") is None


def test_invite_cancel(env):
    """«Отмена» на шаге выбора роли: сотрудник НЕ приглашён и не в whitelist."""
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Отмена")
    assert _wait_for(lambda: any(m[0] == "d9" and "не выдан" in m[1]
                                 for m in sent))
    assert bot._get_pending("9") is None
    assert not db.is_employee_allowed("500")
    assert db.get_session("500") is None


def test_invite_bad_digit_reprompts(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "7")                    # ролей в фикстуре — одна
    assert _wait_for(lambda: any("цифрой от 1 до 1" in m[1] for m in sent))
    assert bot._get_pending("9") is not None  # ждём корректный ответ
    assert db.get_session("500") is None


def test_invite_webhook_retry_deduped(env):
    """01.09 (ревью, CONFIRMED): ретрай вебхука «Пригласить» при живом
    pending раньше получал ключ «#step» и двоил промпт — теперь эскейпы
    дедупятся по голому ключу."""
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Пригласить ivan@x.ru")     # ретрай Bitrix, БЕЗ clear
    time.sleep(0.1)
    prompts = [m for m in sent if "Выбери роль" in m[1]]
    assert len(prompts) == 1


def test_two_invites_same_digit_not_swallowed(env):
    """01.09 (ревью, CONFIRMED): «1» для двух приглашений подряд в окне
    дедупа раньше глоталась — ключ различает pending'и по uid."""
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_session("500") is not None)
    _post_hr(client, "Пригласить petr@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "1")                         # та же цифра, БЕЗ clear
    assert _wait_for(lambda: db.get_session("600") is not None)
    assert db.get_employee_by_email("petr@x.ru")["role"] == "housekeeper"


def test_finish_invite_failure_keeps_pending(env, monkeypatch):
    """01.09 (ревью): сбой посреди завершения не теряет приглашение —
    pending жив, HR повторяет цифру (attempt растёт, дедуп не гасит)."""
    client, sent, _ = env
    calls = {"n": 0}
    real = bot.start_onboarding

    def flaky(uid, dialog):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(uid, dialog)

    monkeypatch.setattr(bot, "start_onboarding", flaky)
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "1")
    assert _wait_for(lambda: any("Не получилось завершить" in m[1]
                                 for m in sent))
    assert bot._get_pending("9") is not None      # pending пережил сбой
    _post_hr(client, "1")                         # повтор — БЕЗ clear
    assert _wait_for(lambda: db.get_session("500") is not None)
    assert bot._get_pending("9") is None


def test_digit_resolved_by_snapshot(env, monkeypatch, tmp_path):
    """01.09 (ревью): правка roles.json в окне pending не перемапливает
    цифру — резолв по снапшоту списка, который HR видел."""
    import app.roles as roles_mod
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    # руками «подменили» конфиг: первой ролью стала другая
    cfg = tmp_path / "roles.json"
    cfg.write_text(json.dumps({
        "roles": {"spy": "Шпион", "all_staff": "Все"}, "folders": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(roles_mod, "CONFIG_PATH", str(cfg))
    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_employee_by_email("ivan@x.ru") is not None)
    assert db.get_employee_by_email("ivan@x.ru")["role"] == "housekeeper"


def test_second_hr_race_does_not_overwrite_role(env, monkeypatch):
    """01.09 (ревью): второй HR в гонке получает «уже проходит обучение»,
    а не молча перезаписывает роль."""
    client, sent, _ = env
    monkeypatch.setenv("HR_USER_IDS", "9,10")
    _post_hr(client, "Пригласить ivan@x.ru")                 # HR-9
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Пригласить ivan@x.ru", from_user="10", dialog="d10")
    assert _wait_for(lambda: bot._get_pending("10") is not None)
    _post_hr(client, "1")                                    # HR-9 завершает
    assert _wait_for(lambda: db.get_session("500") is not None)
    _post_hr(client, "1", from_user="10", dialog="d10")      # HR-10 следом
    assert _wait_for(lambda: any(m[0] == "d10"
                                 and "уже проходит обучение" in m[1]
                                 for m in sent))
    assert db.get_employee_by_email("ivan@x.ru")["role"] == "housekeeper"


def test_reinvite_cancel_keeps_access_text(env):
    """01.09 (ревью, CONFIRMED): «Отмена» повторного приглашения не отзывает
    доступ — текст говорит об этом честно."""
    client, sent, course_id = env
    db.add_employee("500", "ivan@x.ru", "Иван Иванов", "9")
    db.set_employee_role("500", "housekeeper")
    s = db.create_session("500", "u500", course_id, state="DONE")
    db.update_session(s["id"], role="housekeeper")

    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Отмена")
    assert _wait_for(lambda: any("сохраняются" in m[1] for m in sent))
    assert db.is_employee_allowed("500")
    assert db.get_employee_by_email("ivan@x.ru")["role"] == "housekeeper"


def test_menu_button_drops_pending_with_notice(env):
    """01.09: «Меню» — эскейп из pending c явным «не завершено» (раньше —
    цикл: «меню» уходило в обработчик цифры)."""
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Меню")
    assert _wait_for(lambda: any("не завершено" in m[1] for m in sent))
    assert bot._get_pending("9") is None
    assert not db.is_employee_allowed("500")


def test_role_change_command(env):
    """01.09: «Роль {email}» — смена роли в любой момент, сессии целы."""
    client, sent, _ = env
    db.add_employee("500", "ivan@x.ru", "Иван Иванов", "9")
    db.set_employee_role("500", "housekeeper")

    _post_hr(client, "Роль ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    assert bot._get_pending("9")["kind"] == "role_change"
    prompt = next(m for m in sent if "Кем назначить" in m[1])
    assert "Горничная" in prompt[1]              # текущая роль показана

    _post_hr(client, "1")
    assert _wait_for(lambda: any("применится со следующего курса" in m[1]
                                 for m in sent))
    assert db.get_employee_by_email("ivan@x.ru")["role"] == "housekeeper"
    assert _wait_for(lambda: any(m[0] == "u500" for m in sent))
    emp_msg = next(m for m in sent if m[0] == "u500")
    assert "HR назначил тебе роль" in emp_msg[1] and emp_msg[2] == bot.BOT_ID
    assert db.get_session("500") is None          # сессии не трогали
    assert bot._get_pending("9") is None


def test_role_change_unknown_target(env):
    client, sent, _ = env
    _post_hr(client, "Роль ghost@x.ru")
    assert _wait_for(lambda: any("не найден среди приглашённых" in m[1]
                                 for m in sent))
    assert bot._get_pending("9") is None


def test_invite_no_courses_for_role(env, monkeypatch):
    """01.09 (ревью): курсы есть, но не для выбранной роли — HR получает
    честный текст, ложный notify_hr «запросил обучение» не шлётся."""
    import app.state_machine as sm
    client, sent, course_id = env
    notes = []
    monkeypatch.setattr(sm, "notify_hr", lambda text, keyboard=None:
                        notes.append(text))
    # конфиг с префиксом FO и второй ролью; единственный курс — только FO
    db.set_course_archived(course_id, True)
    import app.roles as roles_mod
    with open(roles_mod.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"roles": {"housekeeper": "Горничная / Уборщица",
                             "admin_reception": "СПиР",
                             "all_staff": "Все сотрудники"},
                   "prefixes": {"FO": "admin_reception"},
                   "folders": {}}, f, ensure_ascii=False)
    fo = db.save_draft_course("FO Ресепшн.docx", "9",
                              json.dumps(QUESTIONS, ensure_ascii=False))
    db.activate_course_by_id(fo, "hr9")

    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "1")                                    # housekeeper
    assert _wait_for(lambda: any("Курсов для этой роли пока нет" in m[1]
                                 for m in sent))
    assert db.get_employee_by_email("ivan@x.ru")["role"] == "housekeeper"
    assert db.get_session("500") is None                     # только маркер
    assert _wait_for(lambda: any(m[0] == "u500" for m in sent))
    emp_msg = next(m for m in sent if m[0] == "u500")
    assert "Подходящих курсов пока нет" in emp_msg[1]
    assert notes == []                                       # без ложного 👀


def test_invite_no_active_courses_still_asks_role(env):
    """Выбор роли — обязательный шаг «Пригласить» даже без курсов."""
    client, sent, course_id = env
    db.set_course_archived(course_id, True)
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    assert not db.is_employee_allowed("500")
    _post_hr(client, "1")
    assert _wait_for(lambda: any("Активных курсов нет" in m[1] for m in sent))
    assert bot._get_pending("9") is None
    assert db.is_employee_allowed("500")
    assert db.get_employee("500")["role"] == "housekeeper"


def test_questions_show_correct_option_text(env):
    """«Буковки» (протокол 05.08): «Вопросы N» показывает текст правильного
    варианта, а не голую букву."""
    client, sent, course_id = env
    qjson = json.dumps({"course_summary": "s", "basic_questions": [
        {"id": 0, "text": "Что делать при пожаре?",
         "options": ["A. Бежать", "B. Звонить 112", "C. Прятаться", "D. Кричать"],
         "correct": "B", "explanation": ""}], "exam_questions": []},
        ensure_ascii=False)
    db.update_course_questions(course_id, qjson)
    _post_hr(client, f"Вопросы {course_id} все")   # 17.08: простыня = «все»
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    text = next(m for m in sent if m[0] == "d9")[1]
    assert "→ B. Звонить 112" in text


def test_non_hr_rejected(env):
    client, sent, _ = env
    _post_hr(client, "Курсы", from_user="777", dialog="d777")
    assert _wait_for(lambda: any(m[0] == "d777" for m in sent))
    assert "только HR" in next(m for m in sent if m[0] == "d777")[1]


def test_allow_by_email(env):
    client, sent, course_id = env
    db.add_employee("600", "petr@x.ru", "Пётр Петров", "9")
    db.create_session("600", "u600", course_id)  # READING

    _post_hr(client, "Допустить petr@x.ru")
    # 31.08: допуск ведёт в EXAM_READY — экзамен стартует кнопкой «Начать»
    assert _wait_for(lambda: db.get_session("600")["state"] == "EXAM_READY")

    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "допущен к экзамену" in next(m for m in sent if m[0] == "d9")[1]


def test_allow_by_unknown_email(env):
    client, sent, _ = env
    _post_hr(client, "Допустить nobody@x.ru")
    assert _wait_for(lambda: any(m[0] == "d9" for m in sent))
    assert "не найден среди приглашённых" in next(m for m in sent if m[0] == "d9")[1]


# ── 18.09: кнопка → ввод (режим await_arg) ───────────────────────────────────

def _reply(sent, dialog="d9"):
    return [m[1] for m in sent if m[0] == dialog]


def test_invite_button_then_email(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    assert bot._get_pending("9")["kind"] == "await_arg"
    assert "Напиши email" in _reply(sent)[-1]
    _post_hr(client, "ivan@x.ru")                      # без слова «Пригласить»
    assert _wait_for(lambda: (bot._get_pending("9") or {}).get("kind") == "invite")
    assert "Иван Иванов" in _reply(sent)[-1]


def test_await_arg_cancel(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Отмена")
    assert _wait_for(lambda: any("Ввод отменён" in t for t in _reply(sent)))
    assert bot._get_pending("9") is None


def test_await_arg_menu_resets(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Меню")
    assert _wait_for(lambda: any("Доступные команды" in t for t in _reply(sent)))
    assert bot._get_pending("9") is None


def test_await_arg_command_is_command_not_argument(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Руководители")
    assert _wait_for(lambda: any("уководител" in t for t in _reply(sent)[1:]))
    assert bot._get_pending("9") is None
    assert not any("не найден" in t for t in _reply(sent))


def test_await_arg_ttl(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    bot._pending_edits["9"]["expires"] = time.monotonic() - 1
    _post_hr(client, "ivan@x.ru")
    assert _wait_for(lambda: any("Доступные команды" in t for t in _reply(sent)))


def test_surname_with_command_prefix_not_hijacked(env):
    """«Курсанов» начинается с «курс» — раньше startswith сбросил бы режим."""
    client, sent, _ = env
    _post_hr(client, "Пригласить")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Курсанов")
    assert _wait_for(lambda: len(_reply(sent)) >= 2)
    assert "Нет курсов" not in _reply(sent)[-1]      # не ушло в «Курсы»


def test_old_form_still_works(env):
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru")
    assert _wait_for(lambda: (bot._get_pending("9") or {}).get("kind") == "invite")


# ── 18.09: приглашение по ID / фамилии / списком / отделом ──────────────────

USERS = {
    "500": {"ID": "500", "NAME": "Иван", "LAST_NAME": "Иванов",
            "EMAIL": "ivan@x.ru", "WORK_POSITION": "Техник"},
    "600": {"ID": "600", "NAME": "Пётр", "LAST_NAME": "Петров",
            "EMAIL": "petr@x.ru"},
    "700": {"ID": "700", "NAME": "Игорь", "LAST_NAME": "Иванов",
            "EMAIL": "", "WORK_POSITION": "Администратор"},   # без почты
    "701": {"ID": "701", "NAME": "", "LAST_NAME": "БС 74", "EMAIL": ""},
}


@pytest.fixture
def lookup(monkeypatch):
    calls = {"departments": 0}

    async def by_id(uid):
        return USERS.get(uid)

    async def by_name(name):
        n = name.casefold()
        return [u for u in USERS.values()
                if n in f"{u['NAME']} {u['LAST_NAME']}".casefold()]

    async def departments():
        calls["departments"] += 1
        return [{"ID": 2439, "NAME": "Ресепшн"}, {"ID": 7, "NAME": "Ресепшн Дубай"}]

    async def dept_users(client, dep_id):
        return list(USERS.values()) if dep_id == "2439" else []

    monkeypatch.setattr(bot, "_bitrix_user_by_id", by_id)
    monkeypatch.setattr(bot, "_bitrix_users_by_name", by_name)
    monkeypatch.setattr(bot, "_bitrix_departments", departments)
    monkeypatch.setattr(bot, "_department_users", dept_users)
    return calls


def test_invite_by_id(env, lookup):
    client, sent, _ = env
    _post_hr(client, "Пригласить 600")
    assert _wait_for(lambda: (bot._get_pending("9") or {}).get("kind") == "invite")
    assert "Пётр Петров (petr@x.ru) найден" in _reply(sent)[-1]
    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_session("600") is not None)


def test_invite_by_surname_unique_without_email(env, lookup):
    client, sent, _ = env
    _post_hr(client, "Пригласить Петров")
    assert _wait_for(lambda: (bot._get_pending("9") or {}).get("kind") == "invite")
    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_session("600") is not None)
    # сотрудник без почты: контакт = ID
    _post_hr(client, "Пригласить Игорь")
    assert _wait_for(lambda: "ID 700" in _reply(sent)[-1])
    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_session("700") is not None)
    assert _wait_for(lambda: any("(ID 700) приглашён" in t for t in _reply(sent)))


def test_invite_by_surname_ambiguous_shows_candidates(env, lookup):
    client, sent, _ = env
    _post_hr(client, "Пригласить Иванов")
    assert _wait_for(lambda: any("несколько сотрудников" in t for t in _reply(sent)))
    assert bot._get_pending("9") is None
    text = _reply(sent)[-1]
    assert "Иван Иванов (Техник)" in text and "Игорь Иванов" in text


def test_invite_list_one_role_for_all(env, lookup):
    client, sent, _ = env
    _post_hr(client, "Пригласить ivan@x.ru, 600\nСидоров")
    assert _wait_for(lambda: (bot._get_pending("9") or {}).get("kind") == "invite")
    text = _reply(sent)[-1]
    assert "Найдено 2" in text and "«Сидоров» — не найден" in text
    assert "Выбери роль для всех" in text
    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_session("500") and db.get_session("600"))
    summary = _reply(sent)[-1]
    assert "Иван Иванов" in summary and "Пётр Петров" in summary
    assert "не найден" in summary                    # заметка дожила до итога
    assert db.get_employee_by_email("petr@x.ru")["role"] == "housekeeper"
    assert bot._get_pending("9") is None


def test_invite_list_partial_failure_retries_only_rest(env, lookup, monkeypatch):
    client, sent, _ = env
    calls = []
    real = bot.start_onboarding

    def flaky(uid, dialog):
        calls.append(uid)
        if uid == "600" and calls.count("600") == 1:
            raise RuntimeError("boom")
        return real(uid, dialog)

    monkeypatch.setattr(bot, "start_onboarding", flaky)
    _post_hr(client, "Пригласить 500, 600")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "1")
    assert _wait_for(lambda: any("Не получилось завершить" in t for t in _reply(sent)))
    assert db.get_session("500") is not None          # первый уже приглашён
    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_session("600") is not None)
    assert calls == ["500", "600", "600"]              # 500 повторно не трогали
    assert "Иван Иванов" in _reply(sent)[-1] and "Пётр Петров" in _reply(sent)[-1]


def test_invite_department_preview_then_confirm(env, lookup):
    client, sent, _ = env
    db.add_employee("500", "ivan@x.ru", "Иван Иванов", "9")   # уже в доступе
    _post_hr(client, "Пригласить отдел ресепшн")
    assert _wait_for(lambda: any("Отдел «Ресепшн»" in t for t in _reply(sent)))
    text = _reply(sent)[-1]
    assert "ещё не приглашены — 2" in text            # 600 и 700; 500 и БС 74 нет
    assert "Пётр Петров" in text and "Игорь Иванов" in text
    assert "БС 74" not in text and "Похожие отделы: Ресепшн Дубай" in text
    assert bot._get_pending("9") is None
    _post_hr(client, "Пригласить отдел 2439")           # кнопка подтверждения
    assert _wait_for(lambda: (bot._get_pending("9") or {}).get("kind") == "invite")
    assert "Найдено 2" in _reply(sent)[-1]
    _post_hr(client, "1")
    assert _wait_for(lambda: db.get_session("600") and db.get_session("700"))
    assert db.get_session("500") is None                # был в доступе, не трогали


def test_invite_department_name_without_word_otdel(env, lookup):
    """HR копирует название отдела из Битрикса как есть."""
    client, sent, _ = env
    _post_hr(client, "Пригласить")
    assert _wait_for(lambda: bot._get_pending("9") is not None)
    _post_hr(client, "Ресепшн")
    assert _wait_for(lambda: any("Отдел «Ресепшн»" in t for t in _reply(sent)))
    assert not any("не найден" in t for t in _reply(sent))


def test_invite_department_no_scope(env, lookup, monkeypatch):
    client, sent, _ = env

    async def no_scope():
        return None

    monkeypatch.setattr(bot, "_bitrix_departments", no_scope)
    _post_hr(client, "Пригласить отдел Ресепшн")
    assert _wait_for(lambda: any("права «department»" in t for t in _reply(sent)))
    sent.clear()
    _post_hr(client, "Пригласить Сидоров")             # ни фамилия, ни отдел
    assert _wait_for(lambda: any("права «department»" in t for t in _reply(sent)))


def test_split_invite_tokens():
    assert bot._split_invite_tokens("a@x.ru, 600\nИванов Иван; отдел Ресепшн") == \
        ["a@x.ru", "600", "Иванов Иван", "отдел Ресепшн"]
    assert bot._split_invite_tokens("a@x.ru 600 b@x.ru") == ["a@x.ru", "600", "b@x.ru"]
