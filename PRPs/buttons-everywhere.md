name: "Кнопки везде: HR-бот + проактивные отправки + добор employee-бота (за флагом BUTTONS_ENABLED)"
description: |
  Расширение №7: инлайн-кнопки во ВСЕХ местах UI, где нажатие может заменить
  набор текста. №7 дал механизм (keyboards.py, /command, флаг) только для
  реактивного пути employee-бота. Этот PRP добавляет: (1) кнопки HR-боту —
  главный пробел, требует второй команды и роутинга /command; (2) кнопки во
  всех проактивных отправках; (3) добор employee-бота («Выбрать N» после
  «Мои курсы»). Закрывает открытый пункт task.md «решить про кнопки HR-боту» —
  решение: да, делаем.

## Purpose
Одно-проходная реализация поверх кода после dc01165 (№7/№8/№10, 194 теста
зелёные). Всё — за тем же флагом BUTTONS_ENABLED (0 по умолчанию): при
выключенном флаге ни один payload не меняется, ни один существующий тест
не должен быть отредактирован.

## Core Principles
1-5 как всегда (CLAUDE.md): русские UI-тексты, простое решение, FSM не трогать,
существующие паттерны переиспользовать.

## НЕ в скоупе (осознанные скоуп-резы — зафиксировать в комментариях кода)
- Снятие кнопок со старых сообщений через imbot.message.update («кнопки жмутся
  вечно»): все наши COMMAND_PARAMS идут в те же текстовые хендлеры, которые
  уже идемпотентны (повторный «Подтвердить N» → «Не удалось активировать»,
  двойной клик режется dedup-окном 15с). Отдельная доработка, если клиент
  попросит.
- Кнопки в эскалациях руководителям (№9): руководители НЕ в HR_USER_IDS —
  их нажатие HR-кнопки упёрлось бы в гейт «⛔ только HR-менеджерам».
- Кнопка «Руководитель удалить {email}» в списке руководителей: удаление
  в один тап без подтверждения — опасно, оставляем текстом.
- Кнопка «Изменить N {q}»: требует номер вопроса — параметризованный ввод
  кнопкой не выразить (15 кнопок на курс = шум).
- Chatbots 2.0 (imbot.v2.*): весь бот на v1 imbot.*, миграция — отдельный
  разговор. v1 в доках помечен «Устаревшие методы», но работает (наш бот
  живёт на нём в проде).

---

## Goal

При `BUTTONS_ENABLED=1`:

**HR-бот** — под каждым ответом кнопки ближайших действий:
- help-меню (else-ветка) → [Курсы] [Отчёт] [Руководители]
- «Курсы» (список pending) → на каждый курс строка [Вопросы N] [Подтвердить N]
- «Вопросы N» → [Подтвердить N]
- уведомление «Новый документ» (process_new_document) → [Вопросы N] [Подтвердить N]
- уведомление «Новый сотрудник» (_notify_hr_about_user) → [Пригласить {email}]
- уведомление «завершил базовый тест» (notify_hr в state_machine) → [Допустить {uid}]
- шаг 1 правки вопроса («Изменить N q») и ответ «❌ Не понял формат» → [Отмена]

**Employee-бот, проактивные отправки** (сейчас все без клавиатуры):
- анонс курса `_broadcast_course` → [Начать обучение] (params «Начать»)
- автостарт после «Пригласить» → клавиатура по свежей сессии (цифры ролей
  или Готов/Мои курсы/Роль)
- «🎓 HR допустил тебя к экзамену!» → [Начать экзамен] (params «Начать»)
- ежедневные напоминания `_maybe_send_reminders` → клавиатура по текущему
  состоянию сессии сотрудника (state — источник истины, никаких спец-кнопок)

**Employee-бот, добор реактивного пути:**
- ответ на «Мои курсы»/«Курсы»/«Меню» в READING, когда есть selectable-курсы →
  кнопки [Выбрать 1] … [Выбрать N] + стандартный READING-ряд

**Инфраструктура:** нажатие HR-кнопки приходит тем же ONIMCOMMANDADD на
/command, но от ВТОРОЙ команды `hrsay`, зарегистрированной на HR-бота;
/command роутит по ИМЕНИ команды: `hrsay` → HR-путь, `say`/нет поля →
employee-путь (обратная совместимость с уже зарегистрированной кнопкой).

## Why
- Кнопки — просьба Дмитрия с первой встречи; №7 закрыл только треть UI.
  HR-сценарии («Вопросы N» → «Подтвердить N», «Допустить {uid}») — ровно те
  места, где сейчас надо КОПИРОВАТЬ номер/ID из текста руками.
- task.md: «№7: после подтверждения — решить про кнопки HR-боту („Допустить")»
  — этот PRP и есть решение.
- Проактивные сообщения («Напиши мне любое сообщение…», «Напиши что-нибудь…»)
  буквально просят кнопку.

## What / Success Criteria
- [ ] BUTTONS_ENABLED=0 (дефолт): payload'ы всех отправок БЕЗ ключа KEYBOARD;
      все 194 существующих теста проходят БЕЗ правок
- [ ] /hr отрефакторен: логика вынесена в `_handle_hr_message(user_id,
      question, dialog_id, client_id)` — тексты ответов и порядок веток
      побайтно те же (существующие HR-тесты зелёные без правок)
- [ ] /command роутит по имени команды: hrsay → _handle_hr_message (гейт HR
      внутри работает и для кнопок), say/пусто → _handle_employee_message
- [ ] При флаге=1 каждый пункт из Goal несёт свою клавиатуру (unit-тесты
      на чистые билдеры + on-flag тесты по образцу test_buttons.py)
- [ ] notify_hr(state_machine) умеет keyboard=None и шлёт [Допустить {uid}]
      при env-флаге (читается в момент вызова — monkeypatch.setenv в тесте)
- [ ] scripts/register_commands.py регистрирует ОБЕ команды: say (employee,
      как сейчас) + hrsay (HR_BOT_ID/HR_CLIENT_ID), флаг --bot both|employee|hr
- [ ] `python -m pytest tests/ -v` зелёный (194 + новые), `ruff check .` чистый
- [ ] task.md: пункт «решить про кнопки HR-боту» перечёркнут → новый чеклист
      живой проверки (регистрация hrsay, включение флага, рендер)

## All Needed Context

### Documentation & References
```yaml
- file: app/keyboards.py
  why: |
    ВЕСЬ существующий механизм №7 — 36 строк. _btn() хардкодит COMMAND="say" —
    обобщить параметром command (дефолт "say", HR-билдеры передают "hrsay").
    for_session() — чистая функция состояния, БЕЗ обращений к БД: новые
    билдеры держать такими же (все данные — параметрами из bitrix_bot).
    _BG = "#29619b" — единый цвет, не плодить.

- file: app/bitrix_bot.py
  why: |
    _send (строка ~604): keyboard уже поддержан («ключ отсутствует вовсе при
    None» — инвариант, тесты это проверяют). BUTTONS_ENABLED (строка ~107).
    _handle_employee_message (~638) — образец «клавиатура по СВЕЖЕЙ сессии»;
    сюда добор «Выбрать N». /command (~710) — парсер двух известных раскладок
    полей + лог всего; сюда роутинг по имени команды. hr_handler (~770-1085)
    — ~300 строк веток if/elif, ВЫНЕСТИ ЦЕЛИКОМ в _handle_hr_message,
    поведение не менять; в каждой ветке рядом с text появляется kb (дефолт
    None). Проактивные точки: _broadcast_course (~761), автостарт «Пригласить»
    (~890: _send(f"u{uid}", first_msg, BOT_ID)), «Допустить» (~918),
    _maybe_send_reminders (~201), _notify_hr_about_user (~590),
    process_new_document HR-notify (~1308).

- file: app/state_machine.py
  why: |
    notify_hr (~660) — sync httpx.post с ретраями, БЕЗ keyboard: добавить
    параметр keyboard=None (payload-ключ по тому же инварианту). Вызов из
    _finish_phase (basic, ~556) — единственный, где нужна кнопка
    [Допустить {uid}]. ВАЖНО: state_machine НЕ импортирует bitrix_bot
    (циклический импорт!) — флаг читать локально:
    os.getenv("BUTTONS_ENABLED", "0") == "1" в момент вызова.
    app.keyboards импортировать МОЖНО (keyboards ни от чего не зависит).
    my_courses (~395) возвращает (text, selectable) — источник числа кнопок
    «Выбрать N». _MENU_COMMANDS = ("мои курсы", "курсы", "меню") — детект
    «юзер запросил меню» в bitrix_bot делать через этот же кортеж (импорт).
    _handle_test: не-ответ (parse_answer → None) в EXAM/BASIC_TEST безопасен —
    переспрашивает вопрос, ответ не записывает; поэтому кнопка «Начать» в
    «допущен к экзамену» безвредна. ПРОВЕРИТЬ это чтением кода перед
    реализацией — если не так, кнопку для «Допустить»-уведомления не ставить.

- file: tests/test_buttons.py
  why: |
    ВСЕ четыре паттерна тестов этой фичи уже отработаны: чистые билдеры,
    FakeAsyncClient ловит payload _send, monkeypatch bot.BUTTONS_ENABLED +
    fake _send ловит keyboard, TestClient POST /command с form-полями +
    _wait_for (фоновые задачи). Новые тесты — в этот же стиль, новый файл
    tests/test_buttons_everywhere.py.

- file: tests/test_hr_invite.py, tests/test_hr_tools.py
  why: TestClient-паттерн для /hr; эти тесты — регресс-сетка рефакторинга
       hr_handler, менять их НЕЛЬЗЯ (зелёные без правок = рефакторинг честный).

- file: scripts/register_commands.py
  why: одноразовая регистрация say (50 строк). Расширить: --bot both|employee|hr;
       hr-ветка = HR_BOT_ID + HR_CLIENT_ID, COMMAND="hrsay", тот же
       EVENT_COMMAND_ADD {url}/command. COMMON=N — команда живёт НА боте,
       поэтому одноимённые say на двух ботах в принципе возможны, но разные
       имена надёжнее: роутинг в /command не зависит от того, придёт ли
       BOT_ID в событии (в выжимке доков формат не описан).

- url: https://apidocs.bitrix24.ru/api-reference/chat-bots/keyboards/index.html
  why: поля кнопки (TEXT, COMMAND, COMMAND_PARAMS, BG_COLOR, TEXT_COLOR,
       DISPLAY LINE/BLOCK, {"TYPE":"NEWLINE"} как разделитель строк).
       Лимит клавиатуры 30 КБ (ошибки KEYBOARD_ERROR/KEYBOARD_OVERSIZE).

- url: https://apidocs.bitrix24.ru/api-reference/chat-bots/commands/index.html
  why: imbot.command.register + событие ONIMCOMMANDADD. Какое ПОЛЕ несёт имя
       команды в форме события — в доках/выжимке не описано: доставать по
       кандидатам data[COMMAND][0][COMMAND] и data[PARAMS][COMMAND], лог всех
       полей в /command уже есть — живой прогон покажет истину.

- file: data/referance/bitrix24_docs.md:1383
  why: единственное упоминание chat-bots в выжимке — imbot.* объявлен
       устаревшим (outdated/). Деталей KEYBOARD/ONIMCOMMANDADD в выжимке НЕТ
       — не искать там, источник = URL выше + живой лог.

- file: PRPs/buttons-autorole-excel.md (раздел №7)
  why: исходное исследование №7: «варианта „кнопка печатает за юзера" в
       Bitrix НЕТ», формат ONIMCOMMANDADD непроверяем до сервера, скоуп-рез
       «HR-кнопки — потом» (= этот PRP).
```

### Current Codebase tree (фрагмент — только затрагиваемое)
```bash
app/
  bitrix_bot.py      # /  /hr  /command; _send(keyboard=); BUTTONS_ENABLED
  keyboards.py       # №7: _btn, for_session (employee-состояния)
  state_machine.py   # FSM; notify_hr (sync, без keyboard); my_courses
scripts/
  register_commands.py  # регистрация say для employee-бота
tests/
  test_buttons.py    # №7-тесты (не трогать)
  test_hr_invite.py, test_hr_tools.py, test_hr_edit_flow.py  # регресс /hr
```

### Desired Codebase tree
```bash
app/
  keyboards.py       # + command-параметр в _btn; + hr_main_menu(),
                     #   hr_course_actions(course_id), hr_course_list(ids),
                     #   hr_invite(email), hr_cancel_edit(), hr_admit(uid),
                     #   start_button(text_label), switch_courses(n, base_row)
  bitrix_bot.py      # + _handle_hr_message (вынос из hr_handler);
                     #   /command роутит по имени команды;
                     #   kb в HR-ветках; клавиатуры в проактивных _send
  state_machine.py   # notify_hr(message, keyboard=None); кнопка Допустить
scripts/
  register_commands.py  # --bot both|employee|hr, регистрация hrsay
tests/
  test_buttons_everywhere.py  # новый файл
task.md              # чеклист живой проверки
```

### Known Gotchas
```python
# CRITICAL: инвариант _send/notify_hr — при keyboard=None ключа KEYBOARD в
# payload НЕТ ВООБЩЕ (не пустой список). Тесты №7 это проверяют.

# CRITICAL: рефакторинг hr_handler — ЧИСТЫЙ вынос. Ветки, тексты, порядок
# early-return'ов (гейт, dedup, pending) не менять. Существующие HR-тесты
# зелёные БЕЗ правок — это и есть валидация выноса.

# CRITICAL: state_machine НЕ импортирует bitrix_bot (цикл). Флаг кнопок там —
# свежий os.getenv при каждом вызове (тест: monkeypatch.setenv). В bitrix_bot
# флаг остаётся модульной константой (тесты монкипатчат bot.BUTTONS_ENABLED)
# — расхождение осознанное, зафиксировать комментарием у notify_hr.

# CRITICAL: HR-кнопки идут через /command → _handle_hr_message, где стоит
# гейт HR_USER_IDS. Это фича: чужое нажатие отбивается тем же «⛔». Гейт
# из выноса не потерять.

# GOTCHA: имя команды в форме ONIMCOMMANDADD — кандидаты
# data[COMMAND][0][COMMAND] и data[PARAMS][COMMAND]; поле может не прийти
# вовсе → фолбэк на employee-путь (сегодняшнее поведение). Лог dict(form)
# в /command уже печатает всё — живой прогон уточнит.

# GOTCHA: dedup 15с общий для текста и кнопок — двойной клик по [Подтвердить N]
# режется _is_duplicate (уже так для employee; HR-путь получает то же через
# вынесенный _handle_hr_message).

# GOTCHA: лимит клавиатуры 30 КБ (KEYBOARD_OVERSIZE). hr_course_list — 2
# кнопки + NEWLINE на курс (~200 байт) → капнуть 20 курсами (len(ids[:20])),
# остальные доступны текстом «Вопросы N» как раньше.

# GOTCHA: «Выбрать N» показывать ТОЛЬКО когда ответ был на menu-команду:
# в _handle_employee_message question.strip().lower() in
# state_machine._MENU_COMMANDS И свежая сессия в READING. Число кнопок =
# len(my_courses(user_id, role)[1]) — звать через asyncio.to_thread (sync БД!).
# selectable может быть 0 → обычный READING-ряд.

# GOTCHA: «Начать экзамен» шлёт «Начать» в EXAM-состояние. Безопасно, только
# если parse_answer("Начать") → None ведёт к переспросу вопроса (проверить
# _handle_test). «Н» не входит в A/B/C/D-лукалайки — по коду так.

# GOTCHA: _maybe_send_reminders шлёт по uid БЕЗ dialog_id из сессии — f"u{uid}".
# Клавиатура = for_session(get_session(uid), fork=...) — тот же расчёт, что в
# _handle_employee_message; вынести в хелпер _session_keyboard(user_id),
# чтобы не дублировать (fork через _retake_fork_text).

# GOTCHA: кнопка [Пригласить {email}] — email внутри COMMAND_PARAMS строкой
# («Пригласить ivanov@x.ru»); _extract_email в HR-ветке его достанет как из
# обычного текста. Ничего не кодировать.
```

## Implementation Blueprint

### Data models and structure
Без новых моделей и таблиц. Клавиатура = list[dict] (формат Bitrix), билдеры —
чистые функции в app/keyboards.py.

### Список задач (в порядке выполнения)

```yaml
Task 1 — keyboards.py, обобщение и новые билдеры:
MODIFY app/keyboards.py:
  - _btn(text, payload=None, display="LINE", command="say") — новый параметр
  - NEW hr-билдеры (все шлют command="hrsay"):
      hr_main_menu() -> [[Курсы][Отчёт][Руководители]]
      hr_course_actions(course_id) -> [Вопросы {id}] [Подтвердить {id}]
      hr_course_list(course_ids) -> ряды hr_course_actions через
        {"TYPE": "NEWLINE"}, ids[:20]
      hr_invite(email) -> [Пригласить {email}] (BLOCK)
      hr_cancel_edit() -> [Отмена]
      hr_admit(user_id) -> [Допустить {uid}] (BLOCK)
  - NEW employee-добор (command="say"):
      start_button(label, payload="Начать") -> [{label}] (BLOCK)
      with_switch(base, n) -> [Выбрать 1..n] + NEWLINE + base (base может
        быть None → только Выбрать-ряд)
  - for_session НЕ менять (регресс №7)

Task 2 — notify_hr с клавиатурой + кнопка «Допустить»:
MODIFY app/state_machine.py:
  - notify_hr(message, keyboard=None): if keyboard: payload["KEYBOARD"] = ...
    (в json= обоих... там ОДИН httpx.post — собрать payload-словарь до цикла)
  - в _finish_phase (ветка basic): keyboard=keyboards.hr_admit(uid) if
    os.getenv("BUTTONS_ENABLED", "0") == "1" else None
  - import app.keyboards as keyboards (не из bitrix_bot!)

Task 3 — вынос _handle_hr_message:
MODIFY app/bitrix_bot.py:
  - NEW async def _handle_hr_message(user_id, question, dialog_id, client_id):
    тело hr_handler от print(f"HR MSG…") до финального _send — ВЕРБАТИМ
    (dedup, гейт, pending, все elif). return вместо return {"status": ...}.
  - в начале диспетчера: kb = None; ветки проставляют kb:
      else-ветка (help)            → kb = keyboards.hr_main_menu()
      «курсы» (есть pending)       → kb = keyboards.hr_course_list([c["id"]...])
      «вопросы N» (курс найден)    → kb = keyboards.hr_course_actions(course_id)
      «изменить N q» (шаг 1 ок)    → kb = keyboards.hr_cancel_edit()
      _apply_pending_edit вернул «❌ Не понял формат» → kb = hr_cancel_edit()
        (проще: в pending-ветке после text = await _apply_pending_edit(...)
         kb = hr_cancel_edit() if text.startswith("❌ Не понял") else None)
  - финальный send: asyncio.create_task(_send(dialog_id, text, HR_BOT_ID,
      client_id, keyboard=kb if BUTTONS_ENABLED else None))
  - hr_handler становится тонким: разбор формы + print + вызов
    _handle_hr_message + return {"status": "ok"} (ветки no-question/гейта/
    dedup возвращают ok как сейчас — статусы наружу не менялись и не меняются)

Task 4 — /command роутинг по имени команды:
MODIFY app/bitrix_bot.py (/command):
  - command_name = (form.get("data[COMMAND][0][COMMAND]")
                    or form.get("data[PARAMS][COMMAND]") or "").strip().lower()
  - if command_name == "hrsay": await _handle_hr_message(user_id, params,
      dialog_id, client_id) else: _handle_employee_message(...) — как сейчас

Task 5 — проактивные клавиатуры employee-бота:
MODIFY app/bitrix_bot.py:
  - NEW async def _session_keyboard(user_id) -> list | None:
      if not BUTTONS_ENABLED: return None
      session = await asyncio.to_thread(get_session, user_id)
      fork = session is None and (await asyncio.to_thread(
          _retake_fork_text, user_id)) is not None
      return keyboards.for_session(session, fork, selectable_roles())
    и ПЕРЕИСПОЛЬЗОВАТЬ его в _handle_employee_message (убрать дубль расчёта,
    поведение то же — after уже свежий, допустим один лишний get_session
    ИЛИ оставить как есть и хелпер только для проактивных — решить по месту,
    НЕ ломая test_keyboard_attached_when_flag_on)
  - _broadcast_course: keyboard=keyboards.start_button("Начать обучение")
      if BUTTONS_ENABLED else None
  - «Пригласить»-автостарт (~890): после new_session уже прочитан —
      _send(f"u{uid}", first_msg, BOT_ID, keyboard=keyboards.for_session(
      new_session, False, selectable_roles()) if BUTTONS_ENABLED else None)
      (fork невозможен: сессию только что создали)
  - «Допустить» (~918): keyboard=keyboards.start_button("Начать экзамен")
      if BUTTONS_ENABLED else None
  - _maybe_send_reminders: keyboard=await _session_keyboard(uid) на каждый uid

Task 6 — «Выбрать N» после «Мои курсы»:
MODIFY app/bitrix_bot.py (_handle_employee_message):
  - после расчёта keyboard: if BUTTONS_ENABLED and after и
    after["state"] == "READING" и question.strip().lower() in
    state_machine._MENU_COMMANDS:
      n = len((await asyncio.to_thread(my_courses, user_id,
               after.get("role")))[1])
      if n: keyboard = keyboards.with_switch(keyboard, n)
  - импорт my_courses и _MENU_COMMANDS из app.state_machine

Task 7 — HR-уведомления из фоновых задач bitrix_bot:
MODIFY app/bitrix_bot.py:
  - process_new_document, блок «10. Notify HR»: keyboard=
      keyboards.hr_course_actions(course_id) if BUTTONS_ENABLED else None
  - _notify_hr_about_user (не transfer, есть email): keyboard=
      keyboards.hr_invite(email) if BUTTONS_ENABLED else None

Task 8 — регистрация hrsay:
MODIFY scripts/register_commands.py:
  - argparse: --bot {both,employee,hr}, default both
  - вынести _register(webhook, bot_id, client_id, command, url); employee =
    ("say", BOT_ID, BOT_CLIENT_ID), hr = ("hrsay", HR_BOT_ID, HR_CLIENT_ID);
    HR_BOT_ID пуст → SystemExit с подсказкой
  - докстринг: обе команды, порядок включения флага

Task 9 — тесты (tests/test_buttons_everywhere.py):
  см. Validation Loop / Level 2

Task 10 — task.md:
  - пункт «№7: после подтверждения — решить про кнопки HR-боту» → [x] решено,
    реализовано (PRPs/buttons-everywhere.md)
  - NEW чеклист сдачи: register_commands.py --bot hr на сервере,
    BUTTONS_ENABLED=1, живой лог /command → подтвердить ПОЛЕ имени команды
    (поправить кандидатов при расхождении), нажать: Курсы → Вопросы N →
    Подтвердить N; Пригласить {email}; Допустить {uid}; Отмена в правке;
    Начать обучение из анонса; Выбрать N из «Мои курсы»; рендер NEWLINE-строк
    на мобильном; 21+ pending-курсов → клавиатура капнута, ошибки
    KEYBOARD_OVERSIZE нет
```

### Integration Points
```yaml
CONFIG:
  - флаг существующий: BUTTONS_ENABLED (никаких новых env)
  - scripts/register_commands.py требует HR_BOT_ID, HR_CLIENT_ID в .env
    (уже есть на сервере)
ROUTES:
  - /command: без нового роута, только роутинг по имени команды внутри
DB: нет изменений
```

## Validation Loop

### Level 1: Syntax & Style
```bash
ruff check . --fix
# Expected: чисто. mypy в репо не настроен — не вводить.
```

### Level 2: Unit Tests — tests/test_buttons_everywhere.py
```python
# Чистые билдеры (без моков):
# - hr_main_menu: 3 кнопки, у всех COMMAND == "hrsay", COMMAND_PARAMS == TEXT
# - hr_course_list([1, 2]): ["Вопросы 1","Подтвердить 1",NEWLINE,"Вопросы 2",
#   "Подтвердить 2"]; список из 25 id → только 20 курсов в клавиатуре
# - hr_invite("a@b.ru"): COMMAND_PARAMS == "Пригласить a@b.ru"
# - hr_admit("500"): COMMAND_PARAMS == "Допустить 500"
# - start_button("Начать обучение"): TEXT метка, COMMAND_PARAMS == "Начать",
#   COMMAND == "say"
# - with_switch(None, 3) и with_switch(reading_row, 2): состав и порядок

# Рефакторинг /hr (регресс): существующие test_hr_* зелёные БЕЗ правок —
# отдельного теста не надо, это и есть сетка.

# HR-клавиатуры за флагом (паттерн test_keyboard_attached_when_flag_on):
# - monkeypatch bot.BUTTONS_ENABLED=True, fake _send ловит keyboard,
#   HR_USER_IDS пуст (гейт выключен): POST /hr «непонятная команда» →
#   keyboard == hr_main_menu(); «Курсы» с замоканным get_pending_courses →
#   hr_course_list; флаг False → keyboard is None во всех ветках

# /command-роутинг:
# - form с data[COMMAND][0][COMMAND]="hrsay" + monkeypatch
#   bot._handle_hr_message (ловушка) → вызван HR-путь, employee-путь нет
# - имя "say" и форма БЕЗ поля имени → _handle_employee_message (как раньше)

# notify_hr:
# - monkeypatch httpx.post-ловушка; monkeypatch.setenv BUTTONS_ENABLED=1 →
#   в payload есть KEYBOARD с «Допустить {uid}» при вызове из _finish_phase
#   (или прямой вызов notify_hr(msg, keyboard=...) + отдельный тест ветки
#   _finish_phase через сценарий завершения basic — по образцу
#   test_state_machine); без env — ключа KEYBOARD нет

# Проактивные:
# - _broadcast_course при флаге → каждый _send с keyboard start_button
# - «Пригласить»-автостарт (паттерн test_hr_invite): keyboard == for_session
#   новой сессии
# - _maybe_send_reminders: fake _send, get_session → READING → keyboard ==
#   READING-ряд

# «Выбрать N»:
# - флаг on, get_session → READING, monkeypatch my_courses → (text, [c1, c2]):
#   сообщение «Мои курсы» → keyboard начинается с «Выбрать 1», «Выбрать 2»;
#   обычный вопрос → keyboard == чистый READING-ряд
```

```bash
python -m pytest tests/ -v
# Итерировать до зелёного. Существующие тесты НЕ редактировать — красный
# старый тест = сломан рефакторинг, чинить код, не тест.
```

### Level 3: Integration (локально, без Bitrix)
```bash
# smoke: приложение поднимается, /command игнорирует чужие события
python - <<'EOF'
from fastapi.testclient import TestClient
import app.bitrix_bot as bot
c = TestClient(bot.app)
r = c.post("/command", data={"event": "ONIMBOTMESSAGEADD"})
assert r.json()["status"] == "ignored"
print("ok")
EOF
```

Живая проверка (сервер, руками юзера) — чеклист в task.md (Task 10):
регистрация hrsay, BUTTONS_ENABLED=1, лог /command покажет реальное поле
имени команды — при расхождении поправить два кандидата в /command.

## Final validation Checklist
- [ ] `python -m pytest tests/ -v` — все зелёные, старые без правок
- [ ] `ruff check .` — чисто
- [ ] git diff hr_handler: вынос без изменения текстов ответов (просмотреть
      диff глазами: только отступы/return/kb)
- [ ] BUTTONS_ENABLED=0: grep-проверка, что каждый новый keyboard=... обёрнут
      флагом (или идёт через _session_keyboard/ветку с флагом)
- [ ] task.md обновлён (решение + чеклист живой сдачи)

---

## Anti-Patterns to Avoid
- ❌ Не менять тексты ответов HR-бота при выносе — вынос ДОЛЖЕН быть невидим
- ❌ Не давать keyboards.py доступ к БД — все данные параметрами
- ❌ Не регистрировать «say» на HR-бота — роутинг обязан работать по имени
- ❌ Не добавлять KEYBOARD пустым списком — ключ либо есть с кнопками, либо нет
- ❌ Не трогать for_session и test_buttons.py — регресс №7 священен
- ❌ Не изобретать второй флаг (HR_BUTTONS_ENABLED) — один BUTTONS_ENABLED,
  включение всё равно одномоментное после живой проверки

## Confidence Score: 8/10
Код и тесты полностью в отработанных паттернах №7 (билдеры, FakeAsyncClient,
TestClient, флаг), рефакторинг /hr большой, но механический и накрыт
регресс-сеткой существующих HR-тестов. Минус два балла за живую
неопределённость, невыясняемую до сервера: (1) какое поле ONIMCOMMANDADD несёт
имя команды (митигация: два кандидата + фолбэк на employee-путь + лог всех
полей); (2) регистрация hrsay на HR-бота и рендер NEWLINE-рядов — проверяется
только включением флага на сервере (чеклист в task.md).
