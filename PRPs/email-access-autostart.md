name: "Доступ по email и автостарт (доработка №2, 22ч/44 000₽)"
description: |
  HR приглашает сотрудника по email («Пригласить …»), бот сам пишет сотруднику и
  начинает диалог; whitelist «доступ только у одобренных»; ловля новых сотрудников:
  опрос отделов (основной механизм) + endpoint под событие OnUserAdd (живая проверка).

## Purpose
Одно-проходная реализация доработки №2 из сметы. Контекст самодостаточный: реальные
сниппеты, проверенные факты из выжимки Bitrix-доков, ловушки репо, порядок задач, гейты.

## Core Principles
1. **Context is King** — весь нужный код и данные перечислены ниже
2. **Validation Loops** — ruff + pytest + FSM-smoke офлайн + живой прогон через ngrok/сервер
3. **Information Dense** — имена функций/полей из реального кода
4. **Progressive Success** — сначала таблицы и whitelist, потом «Пригласить», потом поллер отделов
5. **Global rules** — CLAUDE.md: простейшее решение, чужой код не трогать, все тексты бота на русском

---

## Goal

HR пишет HR-боту «Пригласить ivanov@vertical.ru» — бот находит сотрудника в Битриксе
по email (`user.get`), заносит в whitelist, **сам пишет сотруднику первым** и начинает
онбординг (меню выбора роли из №1). Сотрудник НЕ из whitelist получает вежливый отказ.
Новые люди на портале ловятся опросом отделов (+опционально событием OnUserAdd) —
HR получает уведомление с готовой командой «Пригласить {email}».

## Why

- HR сейчас работает по Bitrix-ID («Допустить 123») — ID никто не знает, email знают все.
- Сейчас курс может начать ЛЮБОЙ пользователь портала (открытый вопрос №2 в planning.md) —
  смета продана как «доступ только у одобренных сотрудников».
- Сотрудник сейчас должен сам первым написать боту — о боте он не знает; автостарт
  замыкает цикл «принят на работу → приглашён → обучается» без ручных объяснений.

## What

1. Таблица `employees` (whitelist) + сидинг из существующих `sessions` (legacy-юзеры
   не должны отвалиться).
2. HR-команда «Пригласить {email}»: `user.get` по EMAIL → `employees` → создание сессии
   → проактивное сообщение сотруднику (меню ролей) от ЕМPLOYEE-бота.
3. «Допустить …» принимает и email, и ID (обратная совместимость).
4. Whitelist-гейт в `process_message` (только для НОВЫХ сессий).
5. Поллер отделов: `user.get` по `UF_DEPARTMENT` из `WATCH_DEPARTMENT_IDS`, diff с
   таблицей `seen_portal_users` → HR-уведомление о новичке/переводе. Первый прогон —
   тихий засев (без спама по всему отделу).
6. Endpoint `/user-webhook` под OnUserAdd (по образцу `/disk-webhook`) — код + инструкция;
   доступность события простому исходящему вебхуку проверяется живьём.
7. Гейт `/hr` по HR_USER_IDS (сейчас HR-боту может командовать кто угодно — дыра,
   по духу пункта «доступ только у одобренных»; малая задача, включена).

### Success Criteria
- [ ] «Пригласить {email}» → сотрудник получает первое сообщение от бота (меню ролей),
      в `sessions` появилась запись ROLE_SELECT, в `employees` — запись с email/ФИО
- [ ] Неизвестный email → «⚠️ … не найден», ничего не создано
- [ ] Пользователь НЕ из employees пишет боту → отказ, сессия НЕ создана
- [ ] Legacy-юзер (имел сессию до миграции) пишет боту → работает как раньше
- [ ] «Допустить ivanov@x.ru» работает наравне с «Допустить 123»
- [ ] Поллер отделов: первый прогон тихий; новый uid → одно HR-уведомление, не каждые N минут
- [ ] `python -m pytest tests/ -v` зелёный, `ruff check app/ scripts/ tests/` чистый

## All Needed Context

### Documentation & References
```yaml
- docfile: data/referance/bitrix24_docs.md   # ВНИМАНИЕ: папка referAnce, выжимка 51k строк
  why: |
    ПЕРВИЧНЫЙ источник. Проверено в этой выжимке:
    • user.get (~строки 46030–46200): FILTER по любым полям user.add (в т.ч. EMAIL);
      дополнительно UF_DEPARTMENT, ACTIVE (true = без уволенных), USER_TYPE=employee,
      NAME_SEARCH. Пагинация: страница всегда 50, параметр start=(N-1)*50.
      КРИТИЧНО: официальный cURL-пример передаёт фильтр ПЛОСКИМИ полями:
        {"UF_DEPARTMENT": 1, "SORT": "ID", "ORDER": "asc"}
      (НЕ вложенным {"FILTER": {...}}). Начинать с плоской формы; если живьём фильтр
      игнорируется (вернулись все юзеры) — попробовать вложенную. Это допущение №2.
    • Скоупы user (~строка 45990): user_brief БЕЗ контактных данных, user_basic — с
      контактами, user — полный. Вебхук должен иметь скоуп user (или user_basic),
      иначе EMAIL не вернётся и фильтр по нему не сработает. Проверить права вебхука!
    • События (~строки 35850–35980): подписка возможна БЕЗ OAuth-приложения через
      «Разработчикам → Другое → Исходящий вебхук» (URL + выбор события из списка).
      Есть ли OnUserAdd в этом списке — в выжимке НЕ сказано (проверить в портале).
      Имя события приходит UPPERCASE: 'ONAPPINSTALL', 'ONCRMDEALADD'.
      Content-type: application/x-www-form-urlencoded. Повторных отправок НЕТ.
      В запросе исходящего вебхука НЕТ пользовательских OAuth-токенов (только
      application_token) — для ответных вызовов используем свой BITRIX_WEBHOOK_URL.

- url: https://apidocs.bitrix24.com/api-reference/user/user-get.html
  why: онлайн-версия user.get — только если выжимки не хватит

- url: https://apidocs.bitrix24.com/api-reference/chat-bots/messages/imbot-message-add.html
  why: |
    imbot.message.add — метода НЕТ в выжимке, но он уже живёт в проде (_send/notify_hr).
    Нужен для проактивного первого сообщения: DIALOG_ID="u{uid}", CLIENT_ID обязателен
    (без него Bitrix → 403 — уже решено в _send).

- file: app/bitrix_bot.py
  why: |
    hr_handler — точка вставки «Пригласить» (stateless if/elif по msg_lower.startswith);
    ветка «допустить» — сюда email-резолв; ветка else — help-текст, дополнить.
    _send(dialog_id, text, bot_id, client_id) — проактивная отправка с retry 5×backoff;
    при client_id="" сама подставляет BOT_CLIENT_ID/HR_CLIENT_ID из .env по bot_id.
    КРИТИЧНО для автостарта: сообщение сотруднику шлём от EMPLOYEE-бота →
    _send(f"u{uid}", text, BOT_ID) БЕЗ client_id из формы (форма пришла HR-боту!).
    _disk_poll_loop/_monitored_folders — образец фонового поллера (startup-task).
    /disk-webhook — образец endpoint'а под Bitrix-событие. ЛОВУШКА: он сравнивает
    event != "OnDiskFileAdd", а события приходят UPPERCASE — в /user-webhook сравнивать
    event.upper() != "ONUSERADD". (/disk-webhook НЕ чинить — не наш скоуп, всплыло на ресёрче.)
    МОДУЛЬ ИМЕЕТ side effects при импорте: init_db() + load_index() на живых файлах —
    см. гочу про тесты.

- file: app/state_machine.py
  why: |
    process_message: ветка session is None → создание сессии (ROLE_SELECT если
    selectable_roles() else READING) — СЮДА whitelist-гейт. _start_role_select() и
    _start_reading(session, course) — переиспользовать для автостарта (новая функция
    start_onboarding). notify_hr — образец синхронной отправки с retry.

- file: app/db.py
  why: |
    _ensure_column (PRAGMA+ALTER) и сидинг processed_files из courses в init_db —
    ПАТТЕРН миграции живой onboarding.db; employees сидировать из sessions так же.
    get_session(user_id) — «активная сессия» = state != 'DONE'. DB_PATH читается при
    каждом _conn() → monkeypatch(db, "DB_PATH", tmp) в тестах работает.

- file: tests/test_state_machine.py
  why: |
    Образец FSM-тестов: fixture env = tmp DB + tmp roles.json (monkeypatch CONFIG_PATH)
    + mock rag_answer через monkeypatch(sm, "rag_answer", ...). Новые тесты — в этом стиле.

- file: tests/test_db.py
  why: образец миграционного теста (_old_schema_db → init_db → колонка появилась).

- file: PRPs/role-based-search.md
  why: предыдущий PRP — конвенции этого репо (валидация, анти-паттерны, живой прогон).
```

### Current Codebase tree (релевантная часть)
```bash
vertical_standards/
├── app/
│   ├── bitrix_bot.py        # FastAPI "/", "/hr", "/disk-webhook"; _send; поллер папок; ингест
│   ├── state_machine.py     # FSM ROLE_SELECT→READING→BASIC_TEST→WAITING_HR→EXAM→DONE
│   ├── roles.py             # data/roles.json: selectable_roles(), role_mask()
│   ├── rag.py               # load_index, answer(role_filter)
│   └── db.py                # courses, sessions, answers, processed_files; _ensure_column
├── data/roles.json          # роли + folder→roles (№1)
├── tests/                   # 22 теста: conftest (sys.path), test_db, test_state_machine, ...
├── onboarding.db            # ЖИВАЯ БД — только аддитивные миграции
├── ruff.toml                # ignore E702/E402 — стиль репо
└── .env / .env.example      # BOT_ID, HR_BOT_ID, BOT_CLIENT_ID, HR_CLIENT_ID, HR_USER_IDS, ...
```

### Desired Codebase tree
```bash
├── app/
│   ├── bitrix_bot.py        # MOD: «Пригласить», «Допустить email», HR-гейт, поллер отделов,
│   │                        #      /user-webhook, help-текст
│   ├── state_machine.py     # MOD: whitelist-гейт; NEW start_onboarding(user_id, dialog_id)
│   └── db.py                # MOD: employees + seen_portal_users + миграция/сидинг + CRUD
├── tests/
│   ├── test_db.py           # MOD: + employees seed/CRUD, seen_users roundtrip
│   ├── test_state_machine.py# MOD: + whitelist-кейсы, start_onboarding
│   └── test_hr_invite.py    # NEW: hr_handler «Пригласить» через fastapi TestClient (моки)
└── .env.example             # MOD: WATCH_DEPARTMENT_IDS, USER_POLL_INTERVAL_SEC
```

### Known Gotchas of our codebase & Library Quirks
```python
# CRITICAL: user.get — фильтр ПЛОСКИМИ полями ({"EMAIL": ..., "ACTIVE": True}), как в
# официальном примере. Если живьём вернутся ВСЕ юзеры (фильтр проигнорирован) — форма
# {"FILTER": {"EMAIL": ...}}. Зашить плоскую, отметить в коде комментарием.

# CRITICAL: email нормализовать (strip().lower()) и при записи в employees, и при
# поиске. В user.get слать как ввёл HR (Bitrix сам нечувствителен к регистру email).

# CRITICAL: автостарт шлётся ЧЕРЕЗ EMPLOYEE-бота: _send(f"u{uid}", text, BOT_ID)
# без client_id — _send сам возьмёт BOT_CLIENT_ID из .env. client_id из формы hr_handler
# принадлежит HR-БОТУ и для BOT_ID не подходит (Bitrix 403/чужой бот).

# CRITICAL: имена событий Bitrix приходят UPPERCASE (пример из доков: 'ONCRMDEALADD').
# В /user-webhook: if (form.get("event") or "").upper() != "ONUSERADD". Формат data[...]
# для OnUserAdd НЕизвестен (события нет в выжимке) — логировать dict(form) целиком
# (паттерн /disk-webhook) и доставать ID по вариантам: data[ID], data[FIELDS][ID],
# data[FIELDS_AFTER][ID].

# CRITICAL: сидинг whitelist из legacy: INSERT OR IGNORE INTO employees(bitrix_uid, added_by)
# SELECT DISTINCT user_id, 'seed' FROM sessions — иначе после деплоя все текущие
# пользователи (включая тестовые uid Никиты) получат отказ.

# CRITICAL: первый прогон поллера отделов = ТИХИЙ засев (seen_portal_users пуста →
# INSERT всех без уведомлений). Иначе HR получит N сообщений по всему отделу разом.

# CRITICAL: пагинация user.get — страница ЖЁСТКО 50; если в ответе есть "next",
# повторить с start=next. Отель > 50 сотрудников в отделе — реальный случай.

# GOTCHA: скоуп вебхука. user_brief НЕ отдаёт email → «Пригласить» не найдёт никого.
# Нужен скоуп user (или user_basic) у BITRIX_WEBHOOK_URL. Проверка живьём; если пусто
# при существующем юзере — первым делом смотреть скоуп.

# GOTCHA: _send — fire-and-forget (не возвращает успех). Автостарт: HR-ответ
# формулировать «отправил приглашение», не «сотрудник получил». Если бот не может
# писать первым (юзер ни разу не открывал диалог с ботом — допущение №1), сессия УЖЕ
# создана: первое же сообщение сотрудника боту попадёт в ROLE_SELECT и покажет меню
# (реprompt) — деградация мягкая, ничего не ломается.

# GOTCHA: import app.bitrix_bot ИСПОЛНЯЕТ init_db() (живая onboarding.db) и load_index()
# (живой индекс). В test_hr_invite: импортировать модуль, ПОТОМ monkeypatch
# db.DB_PATH → db.init_db() заново — все вызовы уйдут во временную БД. init_db на живой
# БД идемпотентна (те же миграции прогонит деплой) — приемлемо, но в тестах живую БД
# НЕ мутировать (никаких insert'ов до подмены DB_PATH).

# GOTCHA: _is_duplicate глушит повторы «Пригласить x@y.ru» в окне 15с — повторная
# отправка той же команды после ответа бота проходит (окно короткое), спец-обработки не надо.

# GOTCHA: httpx-вызов user.get — паттерн _check_folder: async with
# httpx.AsyncClient(timeout=30.0) → post(BITRIX_WEBHOOK_URL + "user.get", json=...).
# Сеть WSL2 флапает — для разовых HR-команд достаточно try/except с человеческим
# текстом ошибки (⚠️ Битрикс не ответил, попробуй ещё раз), retry-цикл не городить
# (HR повторит команду сам); в поллере отделов — try/except + print, как _check_folder.

# GOTCHA: WATCH_DEPARTMENT_IDS пуст → поллер отделов НЕ стартовать вовсе
# (лог «[user-poller] disabled»), функция опциональна до настройки клиентом.

# GOTCHA: pytest/ruff НЕ в requirements.txt — venv проекта: pyenv vertical_standards_env.
# mypy в репо не настроен — НЕ вводить.
```

## Implementation Blueprint

### Data models and structure

```python
# SQLite (мигрируется в init_db, паттерн — существующие CREATE IF NOT EXISTS + _ensure_column):

# employees — whitelist допущенных к обучению
CREATE TABLE IF NOT EXISTS employees (
    bitrix_uid TEXT PRIMARY KEY,
    email      TEXT,              -- normalized lower; NULL у legacy-сидированных
    full_name  TEXT,              -- "NAME LAST_NAME" из user.get; NULL у legacy
    added_by   TEXT,              -- HR user_id | 'seed'
    added_at   TEXT DEFAULT (datetime('now'))
);
# + сидинг: INSERT OR IGNORE ... SELECT DISTINCT user_id, 'seed' FROM sessions

# seen_portal_users — память поллера отделов (НЕ whitelist! просто «кого видели»)
CREATE TABLE IF NOT EXISTS seen_portal_users (
    bitrix_uid  TEXT PRIMARY KEY,
    departments TEXT,             -- json-массив ID отделов, отсортированный (для детекта перевода)
    first_seen  TEXT DEFAULT (datetime('now'))
);

# .env (добавить в .env.example с комментариями):
# WATCH_DEPARTMENT_IDS=1,5      # отделы под наблюдением (пусто = поллер отделов выключен)
# USER_POLL_INTERVAL_SEC=600    # период опроса отделов
```

### List of tasks (в порядке выполнения)

```yaml
Task 1 — MODIFY app/db.py (таблицы + CRUD):
  - init_db(): CREATE TABLE IF NOT EXISTS employees, seen_portal_users (в executescript);
    после — сидинг employees из sessions (INSERT OR IGNORE ... SELECT DISTINCT ...)
  - NEW функции (все sync, стиль модуля):
      add_employee(bitrix_uid, email=None, full_name=None, added_by=None) -> None
        # INSERT ... ON CONFLICT(bitrix_uid) DO UPDATE SET email=излишне-не-null, full_name=...
        # (повторное «Пригласить» обновляет email/имя у seed-записи, added_at не трогает)
      is_employee_allowed(bitrix_uid) -> bool
      get_employee_by_email(email) -> dict | None      # WHERE email = ? (нормализованный)
      is_user_seen(bitrix_uid) -> bool
      mark_user_seen(bitrix_uid, departments_json) -> None      # INSERT OR IGNORE
      get_user_departments(bitrix_uid) -> str | None
      update_user_departments(bitrix_uid, departments_json) -> None
      seen_users_empty() -> bool                        # для тихого первого прогона

Task 2 — MODIFY app/state_machine.py (whitelist + автостарт):
  - import: from app.db import is_employee_allowed (+ существующие)
  - process_message, ветка session is None — ПЕРЕД get_active_courses():
      if not is_employee_allowed(user_id):
          return ("🔒 Доступ к обучению открывает HR-менеджер.\n"
                  "Обратись к нему, чтобы начать обучение.")
  - NEW start_onboarding(user_id, dialog_id) -> str | None:
      # для «Пригласить»: создать сессию и вернуть ПЕРВОЕ сообщение сотруднику
      if get_session(user_id): return None            # уже проходит обучение
      courses = get_active_courses()
      if not courses: return None                     # нет активных курсов
      # ЗЕРКАЛО ветки session is None из process_message:
      if selectable_roles():
          create_session(user_id, dialog_id, courses[0]["id"], state="ROLE_SELECT")
          return _start_role_select()
      session = create_session(user_id, dialog_id, courses[0]["id"])
      return _start_reading(session, courses[0])

Task 3 — MODIFY app/bitrix_bot.py (HR-команды):
  - NEW _extract_email(text) -> str | None:
      m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text); return m.group(0).lower() if m else None
  - NEW async _bitrix_user_by_email(email) -> dict | None:
      # user.get, ПЛОСКИЙ фильтр (см. гочу): json={"EMAIL": email, "ACTIVE": True}
      # result — список; [0] или None; поля: ID, NAME, LAST_NAME, EMAIL
  - hr_handler, НОВАЯ ветка (после "допустить", до "вопросы"):
      elif msg_lower.startswith("пригласить"):
          email = _extract_email(question)
          → нет email: "❌ Укажи email: Пригласить ivanov@company.ru"
          → user = await _bitrix_user_by_email(email)
          → user is None: "⚠️ Сотрудник с email {email} не найден в Битриксе."
          → uid = str(user["ID"]); fio = f"{NAME} {LAST_NAME}".strip()
            await asyncio.to_thread(add_employee, uid, email, fio, user_id)
            first_msg = await asyncio.to_thread(start_onboarding, uid, f"u{uid}")
            if first_msg is None and активная сессия: "ℹ️ {fio} уже проходит обучение."
            elif first_msg is None: "✅ {fio} добавлен в доступ. Активных курсов нет — "
                                    "после активации курса отправь Пригласить повторно."
            else: asyncio.create_task(_send(f"u{uid}", first_msg, BOT_ID))  # БЕЗ client_id формы!
                  "✅ {fio} ({email}) приглашён — отправил ему первое сообщение."
      # различить «уже учится» и «нет курсов»: get_session(uid) до вызова start_onboarding
  - ветка «допустить»: target = parts[-1]; if "@" in target →
      emp = await asyncio.to_thread(get_employee_by_email, target.lower())
      → None: "⚠️ {email} не найден среди приглашённых. Сначала: Пригласить {email}"
      → target_uid = emp["bitrix_uid"]; дальше существующий код без изменений
  - HR-гейт в НАЧАЛЕ hr_handler (после парсинга формы и dedup):
      if _hr_ids() and user_id not in _hr_ids():
          text = "⛔ Команды HR-бота доступны только HR-менеджерам."
          → отправить и вернуть ok  # ПУСТОЙ HR_USER_IDS = гейт выключен (dev)
  - help-текст (ветка else): добавить строки
      «• Пригласить {email} — дать доступ и начать обучение»
      «• Допустить {email или ID} — допустить к экзамену»

Task 4 — MODIFY app/bitrix_bot.py (поллер отделов + /user-webhook):
  - ENV: WATCH_DEPARTMENT_IDS (csv), USER_POLL_INTERVAL_SEC (default 600)
  - NEW async _user_poll_loop():   # MIRROR _disk_poll_loop
      deps = [x.strip() for x in os.getenv("WATCH_DEPARTMENT_IDS","").split(",") if x.strip()]
      if not deps: print("[user-poller] disabled (WATCH_DEPARTMENT_IDS empty)"); return
      while True:
          await asyncio.sleep(USER_POLL_INTERVAL)
          silent = await asyncio.to_thread(seen_users_empty)   # первый прогон — тихий
          for dep in deps: await _check_department(dep, silent)
  - NEW async _check_department(dep_id, silent):
      # user.get {"UF_DEPARTMENT": int(dep_id), "ACTIVE": True} + пагинация:
      #   payload["start"] = 0; loop: resp = post(...); users += result;
      #   nxt = resp.json().get("next"); if nxt is None: break; payload["start"] = nxt
      for u in users:
          uid = str(u["ID"]); deps_json = json.dumps(sorted(u.get("UF_DEPARTMENT") or []))
          if not is_user_seen(uid):
              mark_user_seen(uid, deps_json)
              if not silent and not is_employee_allowed(uid):
                  notify_hr_new_user(u)      # см. текст ниже
          elif get_user_departments(uid) != deps_json:
              update_user_departments(uid, deps_json)
              if not silent: notify_hr_transfer(u)
      # try/except вокруг всего тела с print — паттерн _check_folder
  - тексты уведомлений (через существующий notify_hr из state_machine ИЛИ локальный
    цикл _send по _hr_ids() с HR_BOT_ID — выбрать _send, он уже в модуле):
      new:      "👤 Новый сотрудник: {fio} ({email или 'email не указан'}).\n"
                "Пригласить в обучение: Пригласить {email}"
      transfer: "🔄 {fio} сменил отдел. Если поменялась роль — пусть напишет боту «Роль»."
  - startup: рядом с _poller_task → _user_poller_task = asyncio.create_task(_user_poll_loop())
  - NEW endpoint /user-webhook:      # MIRROR /disk-webhook
      event = (form.get("event") or "").upper()
      if event != "ONUSERADD": return {"status": "ignored"}
      print(f"[user-webhook] fields={dict(form)}")     # формат data неизвестен — логировать
      uid = form.get("data[ID]") or form.get("data[FIELDS][ID]") or form.get("data[FIELDS_AFTER][ID]")
      if not uid: return {"status": "no_user_id"}
      # дальше: user.get по ID (плоско {"ID": uid}), mark_user_seen (idempotent),
      # если не в employees → notify HR (тот же текст new). Всё в try/except.

Task 5 — MODIFY .env.example:
  - добавить с комментариями:
      WATCH_DEPARTMENT_IDS=        # ID отделов для авто-обнаружения новичков (пусто = выкл)
      USER_POLL_INTERVAL_SEC=600   # период опроса отделов
  - комментарий у BITRIX_WEBHOOK_URL: «скоуп вебхука должен включать user —
    иначе user.get не вернёт email (user_brief режет контакты)»

Task 6 — TESTS:
  - tests/test_db.py, добавить:
      test_employees_seeded_from_sessions: _old_schema_db + INSERT сессию user_id='77'
        → init_db() → is_employee_allowed('77') is True
      test_add_employee_upsert: add дважды (второй раз с email/fio) → email обновился,
        added_at первого сохранён (проверить хотя бы email)
      test_seen_users_roundtrip: is_user_seen/mark_user_seen/update_user_departments
  - tests/test_state_machine.py, добавить (fixture env создаёт tmp DB — в ней
    employees пуста; существующие тесты создают сессии через process_message,
    который теперь гейтится! → В fixture env добавить db.add_employee("u1", added_by="test")
    и "u2" — СУЩЕСТВУЮЩИЕ тесты остаются зелёными):
      test_unknown_user_rejected: process_message("stranger", ...) → "HR" в ответе,
        get_session("stranger") is None
      test_start_onboarding_creates_session: start_onboarding("u1","d1") → текст с
        "1. Горничная", сессия ROLE_SELECT; повторный вызов → None
      test_start_onboarding_no_courses: без активного курса → None, сессии нет
  - tests/test_hr_invite.py (NEW):
      # ПОРЯДОК ВАЖЕН (см. гочу про side effects импорта):
      import app.bitrix_bot as bot; from fastapi.testclient import TestClient
      fixture: monkeypatch db.DB_PATH → tmp; db.init_db(); monkeypatch
        bot._bitrix_user_by_email (async fake → {"ID": "500", "NAME": "Иван",
        "LAST_NAME": "Иванов", "EMAIL": "ivan@x.ru"}); monkeypatch bot._send (capture);
        monkeypatch bot.HR_USER_IDS?  # нет — гейт читает _hr_ids() из env:
        monkeypatch.setenv("HR_USER_IDS", "9")
      test_invite_flow: POST "/hr" form={"data[PARAMS][MESSAGE]": "Пригласить ivan@x.ru",
        "data[PARAMS][DIALOG_ID]": "d9", "data[PARAMS][FROM_USER_ID]": "9"}
        → 200; db.is_employee_allowed("500"); сессия ROLE_SELECT у "500";
        _send вызван с ("u500", <меню ролей>, BOT_ID)
      test_invite_unknown_email: fake → None → в ответе HR «не найден», employees пуст
      test_non_hr_rejected: FROM_USER_ID="777" → «только HR», employees пуст
      # ответ HR уходит через asyncio.create_task(_send(...)) — capture просмотреть
      # ПОСЛЕ выполнения запроса TestClient (тела тасков успевают: TestClient гоняет
      # event loop до завершения). Если флак — собрать текст через мок _send.

Task 7 — MODIFY planning.md + task.md:
  - planning.md: №2 → «в работе/сделано», открытый вопрос №2 (whitelist) → закрыт этой
    доработкой; зафиксировать находку «/disk-webhook сравнивает event без upper()» как
    отдельный пункт (починить при подключении события, доработка №6)
  - task.md: открытые пункты — проверить живьём допущения 1–4 (см. ниже)
```

### Per task pseudocode (ключевые места)

```python
# Task 3 — hr_handler: автостарт (внутри ветки «пригласить», после add_employee)
existing = await asyncio.to_thread(get_session, uid)          # активная сессия?
if existing:
    text = f"ℹ️ {fio} уже проходит обучение."
else:
    first_msg = await asyncio.to_thread(start_onboarding, uid, f"u{uid}")
    if first_msg is None:
        text = (f"✅ {fio} добавлен в доступ. Активных курсов нет — "
                f"после активации курса отправь: Пригласить {email}")
    else:
        # EMPLOYEE-бот, client_id НЕ из формы (форма — от HR-бота)!
        asyncio.create_task(_send(f"u{uid}", first_msg, BOT_ID))
        text = f"✅ {fio} ({email}) приглашён — отправил ему первое сообщение."

# Task 4 — пагинация user.get (страница жёстко 50)
async def _department_users(client, dep_id: str) -> list[dict]:
    users, start = [], 0
    while True:
        r = await client.post(BITRIX_WEBHOOK_URL + "user.get",
                              json={"UF_DEPARTMENT": int(dep_id), "ACTIVE": True,
                                    "start": start})
        r.raise_for_status()
        data = r.json()
        users += data.get("result", [])
        if data.get("next") is None: return users
        start = data["next"]
```

### Integration Points
```yaml
DATABASE:
  - migration: "CREATE IF NOT EXISTS employees/seen_portal_users + сидинг employees из sessions — всё внутри init_db(), деплой = рестарт uvicorn"
CONFIG:
  - .env: WATCH_DEPARTMENT_IDS, USER_POLL_INTERVAL_SEC (+ .env.example с комментариями)
ROUTES:
  - NEW POST /user-webhook; поведение "/" (whitelist) и "/hr" (команды, гейт) меняется
BITRIX (руками, при живом прогоне):
  - скоуп вебхука: user (проверить ДО отладки «не находит email»)
  - Разработчикам → Другое → Исходящий вебхук → событие OnUserAdd (если есть в списке) → URL /user-webhook
DEPLOY:
  - рестарт uvicorn (миграция сама); поллер отделов молчит, пока WATCH_DEPARTMENT_IDS пуст
```

## Validation Loop

### Level 1: Syntax & Style
```bash
# venv проекта (pyenv vertical_standards_env), НЕ системный python
ruff check app/ scripts/ tests/ --fix
# Expected: чисто (ruff.toml уже игнорит E702/E402 — стиль репо). mypy НЕ вводить.
```

### Level 2: Unit Tests
```bash
python -m pytest tests/ -v
# СУЩЕСТВУЮЩИЕ 22 теста обязаны остаться зелёными (fixture env дополнена add_employee —
# см. Task 6). Новые: whitelist, start_onboarding, employees/seen CRUD, invite-флоу.
```

### Level 3: FSM-smoke офлайн (без сети)
```bash
python - <<'EOF'
import app.db as db
db.init_db()
from app.state_machine import process_message, start_onboarding
print("-- чужак:", process_message("999999", "привет", "u999999", [], None))  # ждём отказ 🔒
db.add_employee("999999", "test@x.ru", "Тест Тестов", "manual")
print("-- приглашённый:", process_message("999999", "привет", "u999999", [], None))  # меню ролей
EOF
# ВНИМАНИЕ: это ЖИВАЯ onboarding.db — после проверки удалить следы:
# DELETE FROM employees WHERE bitrix_uid='999999'; DELETE FROM sessions WHERE user_id='999999';
```

### Level 4: Живой прогон (ngrok/сервер — локальная сеть WSL2 флапает, ей не верить)
```bash
uvicorn app.bitrix_bot:app --port 8000   # + ngrok, вебхуки Bitrix на "/", "/hr", "/user-webhook"
# 1. HR-бот: «Пригласить {реальный email Никиты}» → пришло первое сообщение от employee-бота
#    (допущение №1: бот пишет первым). Если 403/тишина — смотреть stdout BITRIX SEND.
# 2. «Пригласить nonexistent@x.ru» → «не найден» (если найден ВСЁ РАВНО — фильтр
#    проигнорирован: допущение №2, переключить на {"FILTER": {...}}).
# 3. Не-HR пользователь шлёт HR-боту «Курсы» → отказ ⛔.
# 4. WATCH_DEPARTMENT_IDS=<тестовый отдел> → рестарт → первый прогон тихий (лог),
#    затем создать/перевести юзера в отдел → HR-уведомление одно, не повторяется.
# 5. Исходящий вебхук OnUserAdd (если событие есть в списке портала) → регистрация
#    юзера → лог [user-webhook] fields=... → HR-уведомление. Нет в списке → допущение №3
#    подтверждено «недоступно», остаётся поллинг (задокументировать в planning.md).
```

## Final validation Checklist
- [ ] `python -m pytest tests/ -v` — зелёный (старые 22 + новые)
- [ ] `ruff check app/ scripts/ tests/` — чистый
- [ ] Смоук: чужак → отказ; приглашённый → меню ролей; сессия ROLE_SELECT
- [ ] Живьём: «Пригласить email» → сообщение сотруднику пришло БЕЗ его первого хода
- [ ] «Допустить {email}» работает, «Допустить {ID}» не сломан
- [ ] Legacy-юзеры (сидинг) не получили отказ
- [ ] Поллер отделов: тихий первый прогон, одно уведомление на новичка
- [ ] Тексты бота русские, UTF-8; help HR-бота обновлён
- [ ] planning.md/task.md обновлены (вкл. находку про /disk-webhook uppercase)

---

## Anti-Patterns to Avoid
- ❌ НЕ слать автостарт через HR-бота или с client_id из HR-формы — сотруднику пишет EMPLOYEE-бот
- ❌ НЕ городить retry-циклы вокруг разовых HR-команд — человеческая ошибка + повтор руками
- ❌ НЕ уведомлять HR на первом прогоне поллера отделов (тихий засев)
- ❌ НЕ пересоздавать onboarding.db и НЕ терять сидинг legacy-юзеров
- ❌ НЕ чинить /disk-webhook (uppercase) в этой доработке — зафиксировать, править в №6
- ❌ НЕ вводить mypy/новые зависимости; email-парсинг — re, без сторонних валидаторов
- ❌ НЕ доверять локальному живому прогону — финальная проверка через ngrok/сервер

## Открытые допущения (проверить живьём, отметить в task.md)
1. **Бот может писать юзеру первым** (без открытого диалога). notify_hr так работает
   в проде, но HR-юзеры могли открывать диалог сами. Деградация мягкая: сессия создана,
   первое сообщение сотрудника наткнётся на меню ролей.
2. **Формат фильтра user.get** — плоский (по официальному примеру). Признак ошибки:
   «не найден» для существующего email или найдены ВСЕ. Тогда {"FILTER": {...}}.
3. **OnUserAdd в списке исходящих вебхуков портала** — если нет, событие только через
   OAuth-прилу (event.bind), остаёмся на поллинге отделов (в смете он и есть основной).
4. **Скоуп вебхука включает user** — иначе email не приходит и «Пригласить» слеп.
5. HR-гейт «/hr» при ПУСТОМ HR_USER_IDS выключен (dev-режим) — на проде переменная
   обязана быть заполнена (уже так: notify_hr на неё завязан).

## Score: 8/10
Уверенность в one-pass: паттерны все в репо (поллер, endpoint под событие, миграции,
FSM-тесты), схема данных тривиальна, тексты и порядок задач заданы. Минус балл за
четыре живых допущения по Bitrix (пишет-первым, формат фильтра, OnUserAdd, скоуп) —
у всех есть фолбэки, но подтверждаются только на сервере/ngrok. Минус — TestClient
поверх модуля с import-side-effects: задокументировано, но может потребовать подгонки
фикстуры.
