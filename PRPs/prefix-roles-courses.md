name: "Префиксы департаментов в имени файла → роли → назначение курсов (FEATURE 11) + тестировщик 36523"
description: |
  Пивот клиента 29.07: одна плоская папка, департаменты — латинскими аббревиатурами
  в начале имени файла (FO, HSKP, ENG, RES, SAL, ALL). Полная цепочка: префикс →
  роли документа → какие курсы назначаются сотруднику его роли и по каким стандартам
  бот отвечает. Плюс: юзер 36523 — тестировщик обоих ботов с правами HR (как 28528).

## Purpose
Одно-проходная реализация FEATURE 11 из INITIAL.md. Контекст самодостаточный:
реальные имена файлов из живой папки клиента, сниппеты кода, ловушки, порядок задач,
исполняемые гейты.

## Core Principles
1. **Context is King** — весь нужный код и данные перечислены ниже
2. **Validation Loops** — ruff + pytest + офлайн FSM-прогон + backfill dry-run
3. **Information Dense** — имена функций/полей из реального кода
4. **Progressive Success** — сначала чистый парсер, потом ингест, потом FSM
5. **Global rules** — CLAUDE.md: простейшее решение, UI-тексты на русском, не трогать чужой код

---

## Goal

Документ `FO, RES АЛГОРИТМ БРОНИРОВАНИЯ ПО ТЕЛЕФОНУ.docx` из отслеживаемой папки
даёт чанки с `roles=["admin_reception","reservations"]`; сотрудник с ролью
«Отдел бронирования» получает при онбординге ИМЕННО этот курс (и ALL-курсы), а не
первый попавшийся; бот отвечает ему только фрагментами его роли/ALL. Файлы
`.DS_Store` / `._*.docx` никогда не попадают в пайплайн. Юзеры из `HR_USER_IDS`
(28528, 36523) автоматически допущены к employee-боту как тестировщики.

## Why

- Клиент 29.07 де-факто сменил структуру: вместо ролевых папок (№1, блок `folders`
  в roles.json ТАК И ОСТАЛСЯ пустым — маппинг не заполнен) — префиксы в имени.
  Его мотив = наша же ловушка №1: копии по папкам расходятся при правке.
- Сейчас курс назначается ВСЕМ одинаково: `get_active_courses()[0]`
  (`app/state_machine.py:58,95`) — сотрудник бронирования получит курс горничных,
  если тот активирован последним. Роль фильтрует только RAG-ответы.
- В живой папке «Регламент_тест2» лежит macOS-мусор (`._*.docx` = бинарь
  AppleDouble с расширением docx) — поллер №4 рекурсивно ходит в подпапки
  MONITOR_FOLDER_ID и будет вечно ретраить его парсинг (ошибка → файл не помечен
  processed → повтор каждые POLL_INTERVAL=300с).
- 36523 (контакт клиента) писал обоим ботам — нужен полный доступ тестировщика.

## What

1. `data/roles.json`: блок `prefixes` (аббревиатура → role_id) + 3 новые роли
   (reservations, sales, finance).
2. `app/roles.py::parse_filename()` — чистая функция: имя файла → roles/title/
   suspicious. `None` ролей ≠ ALL (различать «нет префикса» и «для всех»).
3. Ингест: роли документа из имени файла (приоритет над ролями папки), фильтр
   скрытых файлов, предупреждение HR о подозрительном неизвестном префиксе.
4. FSM-инверсия: сначала роль (меню или память прошлой сессии), потом курс =
   первый активный, чьи роли пересекаются с {роль, all_staff} и который сотрудник
   ещё не проходил. Нет подходящих → сообщение + уведомление HR.
5. Роли курса НЕ хранятся в схеме — считаются на лету из `courses.doc_name`
   (старые курсы без префикса → all_staff → видны всем = сегодняшнее поведение).
6. `scripts/backfill_prefix_roles.py` — перетегировать уже заингещенные чанки по
   префиксу doc_name + вычистить мусорные чанки `._*` (если успели попасть).
7. `init_db()`: сид employees из `HR_USER_IDS` — HR/тестировщики допущены к
   employee-боту автоматически.

### Success Criteria
- [ ] `parse_filename("FO, HSKP, ENG, RES Вывод номера из обращения (ремонт, OOS, ООО).docx")`
      → roles `[admin_reception, housekeeper, engineer, reservations]` (запятые в
      скобках названия НЕ ломают разбор)
- [ ] Ингест `ALL КОНФЕДЕНЦИАЛЬНОСТЬ.docx` → чанки `roles=["all_staff"]`;
      `Договор.docx` без префикса из all_staff-папки → `["all_staff"]` (фолбэк)
- [ ] `.DS_Store`, `._FO Внешний вид.docx` — скип и в поллере, и в /disk-webhook
- [ ] Сотрудник с ролью reservations: назначен первый непройденный курс с RES или
      ALL в имени; курс `FO Внешний вид…` ему НЕ назначается, но `role_mask`
      по-прежнему отдаёт ему ALL-чанки в RAG
- [ ] Нет курсов для роли → русское сообщение сотруднику + notify_hr
- [ ] 36523 и 28528: `is_employee_allowed` → True после `init_db()` (сид из env)
- [ ] `python -m pytest tests/ -v` зелёный, `ruff check app/ scripts/ tests/` чистый

## All Needed Context

### Реальные имена файлов клиента (папка «Регламент_тест2», id 4760370, подпапка MONITOR_FOLDER_ID=4698010)
```
FO, HSKP, ENG, RES Вывод номера из обращения (ремонт, OOS, ООО).docx
FO Внешний вид сотрудника.docx
ENG ДЕЙСТВИЯ ДЕЖУРНОГО СМЕНЫ ИНЖЕНЕРНОЙ СЛУЖБЫ ПРИ СРАБОТКЕ ОПОВЕЩЕНИЯ О ПОЖАРЕ И ЭВАКУАЦИИ ОТЕЛЯ.docx
ALL КОНФЕДЕНЦИАЛЬНОСТЬ.docx
FO ДЕЙСТВИЯ ДЕЖУРНОГО СМЕНЫ СЛУЖБЫ ПРИЕМА И РАЗМЕЩЕНИЯ И ХОЗЯЙСТВЕННОЙ СЛУЖБЫ ПРИ ПОЖАРЕ И ЭВАКУАЦИИ ОТЕЛЯ.docx
FO, RES, SAL Негарантированные бронирования.docx
FO, RES АЛГОРИТМ БРОНИРОВАНИЯ ПО ТЕЛЕФОНУ.docx
.DS_Store
._FO Внешний вид сотрудника.docx        ← AppleDouble-мусор на КАЖДЫЙ файл
```
Факты формата: аббревиатуры ЛАТИНИЦЕЙ, через «, » до названия; названия бывают
целиком КАПСОМ КИРИЛЛИЦЕЙ (не путать с префиксами); в названии бывают запятые
внутри скобок; клиент делает опечатки («КОНФЕДЕНЦИАЛЬНОСТЬ») — будут и в префиксах.
Дмитрий обещал ещё FIN «и так далее» — реестр расширяемый, БЕЗ правок кода.

### Documentation & References
```yaml
- file: app/roles.py
  why: |
    Точка расширения. load_roles_config() читает data/roles.json С ДИСКА при каждом
    вызове (менять без рестарта); role_mask(chunks, role_id) — фильтр RAG, НЕ ТРОГАТЬ
    (чанк без roles = all_staff, audience=guest режется всегда). parse_filename
    добавить сюда же — конфиг prefixes уже под рукой.

- file: app/state_machine.py
  why: |
    ГЛАВНАЯ правка. Строки 44-64 (process_message, ветка session is None) и 84-101
    (start_onboarding) — зеркальные: courses[0] + create_session(...) + ROLE_SELECT.
    _handle_role_select:120-138 берёт course = get_course_by_id(session["course_id"])
    ДО выбора — при инверсии курс на этом этапе ещё не выбран. _handle_reading:158-182
    и команда «Роль» не меняются. notify_hr:335 — образец уведомления HR (retry 5).

- file: app/db.py
  why: |
    sessions.course_id INTEGER NOT NULL (строка 48) — NULL нельзя без пересборки
    таблицы. РЕШЕНИЕ: сентинел course_id=0 на этапе ROLE_SELECT (PRAGMA foreign_keys
    в проекте НЕ включается — FK не enforced). update_session:256 — паттерн non-None
    полей, добавить course_id. init_db:29 — образцы идемпотентных сидов
    (INSERT OR IGNORE из sessions:107-110) — так же сидить HR_USER_IDS.
    get_sessions_by_user:294 — ВСЕ сессии юзера, новые сверху (для памяти роли и
    «уже пройденных» курсов). get_active_courses:182 — approved_at DESC, не менять.

- file: app/bitrix_bot.py
  why: |
    _walk_folder:197-247 — обход файлов: сюда фильтр скрытых (ДО ext-гейта, чтобы
    ._*.docx не прошёл). process_new_document:845-1009 — единая точка ингеста
    (поллер + /disk-webhook): здесь переопределить roles из имени файла (шаг 6,
    normalised:910-921 — roles уходит в чанки) и добавить suspicious-предупреждение
    в HR-уведомление (шаг 10, notify_text:991-996). bot_handler:462-508 — отправка
    файла курса при появлении сессии (had_session:486,500-505) — при инверсии
    сессия появляется с course_id=0, файл слать при ПОЯВЛЕНИИ course_id.
    /hr invite:589-627 — start_onboarding + _send_course_file: тот же гард.
    _monitored_folders:147-158 — MONITOR_FOLDER_ID → ["all_staff"], не трогать.

- file: app/index_store.py
  why: |
    _matches по doc_name+folder_id — вот почему doc_name ДОЛЖЕН остаться полным
    именем файла с префиксом (идентичность индекса/курсов/удаления №4).
    Для backfill-скрипта: паттерн assert len(chunks)==emb.shape[0] и атомарная
    запись; НО скрипт офлайновый — писать напрямую с *.bak, как dedup_index.py.

- file: scripts/backfill_roles.py
  why: |
    Образец офлайн-миграции индекса №1: бэкап shutil.copy2 → *.bak ПЕРЕД записью,
    --dry-run со статистикой, ensure_ascii=False, порядок чанков не менять.
    Новый скрипт зеркалит его, но ВНИМАНИЕ: удаление мусорных чанков МЕНЯЕТ число
    строк — фильтровать chunks и embeddings СИНХРОННО по одному списку индексов.

- file: tests/conftest.py + tests/test_state_machine.py:26-52 (fixture env)
  why: |
    Паттерн изоляции: monkeypatch(db, "DB_PATH", tmp), monkeypatch(roles,
    "CONFIG_PATH", tmp roles.json), db.add_employee("u1"), мок sm.rag_answer через
    monkeypatch.setattr(sm, "rag_answer", ...) — rag_answer импортирован в
    state_machine ПО ИМЕНИ. Расширить фикстурный roles.json блоком prefixes.

- file: tests/test_folder_sync.py
  why: |
    Паттерн тестов поллера: фейковый httpx-клиент, _walk_folder/_sync_folder живьём
    без сети. Сюда тесты скрытых файлов и префиксного переопределения ролей.

- file: INITIAL.md (FEATURE 11)
  why: полный дизайн-контекст фичи, открытые вопросы клиенту.
```

### Current Codebase tree (релевантная часть)
```bash
app/
├── bitrix_bot.py     # FastAPI "/", "/hr", "/disk-webhook"; поллеры; process_new_document
├── state_machine.py  # FSM ROLE_SELECT→READING→BASIC_TEST→WAITING_HR→EXAM→DONE
├── roles.py          # load_roles_config, selectable_roles, role_mask
├── db.py             # SQLite: courses, sessions, employees, processed_files, meta
├── index_store.py    # единая точка мутаций индекса (lock + atomic)
data/roles.json       # {roles: 5 ролей, folders: {}} — folders ПУСТ
tests/                # 95 тестов, fixture env, без сети
```

### Desired Codebase tree
```bash
app/roles.py                     # MOD: parse_filename(), display_name(), prefixes
data/roles.json                  # MOD: +prefixes, +reservations/sales/finance
app/db.py                        # MOD: update_session(course_id=), init_db сид HR
app/state_machine.py             # MOD: инверсия роль→курс, pick_course_for_role
app/bitrix_bot.py                # MOD: фильтр скрытых, roles из имени, гард файла курса
scripts/backfill_prefix_roles.py # NEW: перетег чанков по префиксу + чистка ._* (--dry-run, *.bak)
tests/test_prefix_parse.py       # NEW: parse_filename (чистая, без сети)
tests/test_state_machine.py      # MOD: инверсия, память роли, «нет курсов для роли»
tests/test_folder_sync.py        # MOD: скрытые файлы, префиксные роли в чанках
tests/test_db.py                 # MOD: сид HR_USER_IDS, update_session course_id
```

### Known Gotchas
```python
# CRITICAL: doc_name — ключ идентичности (index_store._matches, courses.doc_name,
# дедуп копий, удаление two-strike №4). Префикс из doc_name НЕ вырезать НИГДЕ в
# данных. Только отображение сотруднику: display_name() в _start_reading.

# CRITICAL: sessions.course_id NOT NULL → на этапе ROLE_SELECT класть сентинел 0.
# get_course_by_id(0) → None; _send_course_file уже безопасен ((course or {}).get).
# get_report_rows (№3 «Отчёт») — INNER JOIN courses: сессии с course_id=0 в отчёт
# не попадают (это pre-course состояние, ок). Не «чинить» LEFT JOIN'ом.

# CRITICAL: split имени по запятой РЕЖЕТ название («...(ремонт, OOS, ООО)») —
# разбор обязан ОСТАНОВИТЬСЯ на первом токене, который не чистый префикс
# (взяв его первое слово, если оно префикс). Иначе OOS/ООО попадут в разбор.

# CRITICAL: названия у клиента бывают КАПСОМ КИРИЛЛИЦЕЙ («ДЕЙСТВИЯ ДЕЖУРНОГО...»)
# — suspicious-детект неизвестного префикса ТОЛЬКО по латинице ^[A-Z]{2,6}$,
# иначе каждое капс-название даст ложную тревогу HR.

# CRITICAL: ext-гейт (_walk_folder:215, process_new_document:861) ПРОПУСКАЕТ
# ._FO....docx (расширение .docx). Фильтр скрытых — ОТДЕЛЬНОЙ проверкой
# file_name.startswith(".") ДО ext-гейта и в обеих точках входа.

# CRITICAL: файлы «Регламент_тест2» могли УЖЕ заингеститься на сервере с
# roles=["all_staff"] (рекурсия №4 ходит в подпапки MONITOR_FOLDER_ID). Гейт
# processed_files по file_id их больше не тронет → нужен offline-backfill чанков
# по префиксу doc_name (текст не менялся — БЕЗ переэмбеддинга).

# GOTCHA: get_active_courses → approved_at DESC (новые первыми). Порядок
# назначения = порядок активации; ALL-vs-ролевые приоритеты — открытый вопрос
# клиенту, НЕ изобретать.

# GOTCHA: роли курса считать НА ЛЕТУ из doc_name — НЕ добавлять колонку в courses.
# Старые курсы («Стандарты.docx») без префикса → None → all_staff → видны всем.

# GOTCHA: сеть WSL2 флапает: retry-циклы _send/notify_hr, дедуп 15с, таймауты —
# НЕ трогать. Живой прогон — только сервер/ngrok.

# GOTCHA: тесты №1-№5 полагаются на «новая сессия = courses[0]» — инверсия их
# СЛОМАЕТ (test_state_machine, test_hr_invite, test_folder_sync:289). Обновить
# ожидания (courses с префиксными doc_name), НЕ удалять тесты.

# GOTCHA: db.py не вызывает load_dotenv — env читает тот, кто импортирует
# (bitrix_bot делает load_dotenv() ДО init_db()). В сиде HR_USER_IDS читать
# os.getenv на месте: в тестах без env — no-op, monkeypatch.setenv работает.

# GOTCHA: ruff.toml игнорит E702/E402 — стиль репо компактный, следовать ему.
# mypy в репо НЕ настроен — не вводить.
```

## Implementation Blueprint

### Data model — data/roles.json (полный целевой вид)
```json
{
  "_comment": "roles: реестр ролей. prefixes: аббревиатура в имени файла → role_id (формат клиента: 'FO, RES Название.docx'). folders: legacy №1, оставить. Всё редактируется без рестарта.",
  "roles": {
    "housekeeper":     "Горничная / Уборщица",
    "admin_reception": "Администратор ресепшн (СПиР)",
    "engineer":        "Техник / Инженер",
    "general_manager": "Администратор / Управляющий",
    "reservations":    "Отдел бронирования",
    "sales":           "Отдел продаж",
    "finance":         "Финансовый отдел",
    "all_staff":       "Все сотрудники"
  },
  "prefixes": {
    "FO": "admin_reception", "HSKP": "housekeeper", "ENG": "engineer",
    "RES": "reservations",   "SAL":  "sales",       "FIN": "finance",
    "ALL": "all_staff"
  },
  "folders": {}
}
```
`general_manager` префикса не имеет (управляющий видит роль через меню/№8) — ключи
`prefixes` ДОЛЖНЫ быть UPPERCASE, парсер нормализует токены к upper.

### List of tasks (в порядке выполнения)

```yaml
Task 1 — MODIFY data/roles.json + app/roles.py (парсер, чистый):
  - roles.json по схеме выше
  - app/roles.py NEW функции:
      parse_filename(file_name) -> dict   # {"roles": list[str]|None, "title": str, "suspicious": str|None}
      display_name(file_name) -> str      # title если roles найдены, иначе имя как есть
  - roles: None если НИ ОДНОГО известного префикса (≠ [] и ≠ ["all_staff"])
  - дедуп ролей с сохранением порядка (dict.fromkeys)

Task 2 — MODIFY app/db.py:
  - update_session(..., course_id: int = None) — тот же non-None паттерн (строка 256)
  - init_db(): после существующих сидов —
      for uid in (os.getenv("HR_USER_IDS", "").split(",")):
          uid.strip() → INSERT OR IGNORE INTO employees (bitrix_uid, added_by)
          VALUES (?, 'hr-seed')
    # «тестировщики»: HR (28528, 36523) автоматически допущены к employee-боту.
    # INSERT OR IGNORE = идемпотентно, живые записи «Пригласить» не затираются.

Task 3 — MODIFY app/bitrix_bot.py (ингест):
  - _walk_folder: ПЕРЕД ext-гейтом (строка 215): if file_name.startswith("."): continue
    # покрывает .DS_Store и ._*.docx; seen_files тоже НЕ пополнять этим мусором
  - process_new_document: в начале (после лога START):
      if file_name.startswith("."): print(...skip hidden...); return   # путь вебхука
    после ext-гейта:
      parsed = parse_filename(file_name)
      if parsed["roles"]: roles = parsed["roles"]          # префикс главнее папки
      roles = roles or ["all_staff"]
  - шаг 10 (notify_text): if parsed["suspicious"]:
      notify_text += f"\n⚠️ Возможная опечатка в префиксе: «{parsed['suspicious']}» — " \
                     f"такого департамента нет в реестре. Файл виден всем сотрудникам своей разметки."
  - _start_reading показывает doc_name — правится в Task 4 (display_name)

Task 4 — MODIFY app/state_machine.py (инверсия роль → курс):
  - NEW course_roles(course) -> list[str]:
      parse_filename(course["doc_name"])["roles"] or ["all_staff"]
  - NEW pick_course_for_role(courses, role_id, done_course_ids) -> dict | None:
      role_id None → первый курс не из done (легаси-режим без конфига ролей);
      иначе первый, где {role_id, "all_staff"} ∩ course_roles(c) и id не в done
  - NEW _done_course_ids(user_id): {s["course_id"] for s in get_sessions_by_user
      if s["state"] == "DONE"}
  - NEW _last_known_role(user_id) -> str | None: первая сессия из
      get_sessions_by_user с role не None И role в load_roles_config()["roles"]
  - process_message ветка session is None (44-64) И start_onboarding (84-101) —
      зеркально:
      1) whitelist-гейт как есть; активных курсов нет вообще → текст как сегодня
      2) roles сконфигурированы:
         role = _last_known_role(user_id)
         if role: → сразу _assign_course(user_id, dialog_id, role)   # без меню
         else: create_session(user_id, dialog_id, course_id=0, state="ROLE_SELECT")
               → _start_role_select()
      3) roles НЕ сконфигурированы: как сегодня — pick_course_for_role(courses,
         None, done) → create_session(...) READING
  - NEW _assign_course(user_id|session, dialog_id, role_id) -> str:
      course = pick_course_for_role(get_active_courses(), role_id, done)
      if course is None:
          notify_hr(f"👀 Сотрудник (ID {user_id}) с ролью {role_name(role_id)} "
                    f"запросил обучение — подходящих активных курсов нет.")
          закрыть/пометить сессию state="DONE" (если была ROLE_SELECT-сессия)
          return "📚 Для твоей роли пока нет назначенных курсов. HR уже в курсе — " \
                 "получишь сообщение, когда курс появится."
      # сессия есть (ROLE_SELECT) → update_session(course_id=course["id"],
      #   state="READING", role=role_id); нет → create_session с course["id"]
      return f"✅ Твоя роль: *{role_name(role_id)}*\n\n" + _start_reading(session, course)
  - _handle_role_select: убрать get_course_by_id(session["course_id"]) сверху;
      валидная цифра → _assign_course(...); фолбэк «конфиг опустел» →
      pick_course_for_role(..., None, done)
  - _start_reading: заголовок через display_name(course["doc_name"])
      («Начинаем обучение: *АЛГОРИТМ БРОНИРОВАНИЯ ПО ТЕЛЕФОНУ*» без «FO, RES»)
  - Команда «Роль» в READING остаётся; смена роли НЕ меняет уже назначенный курс
    (сессия привязана) — это осознанно, тестовый инструмент

Task 5 — MODIFY app/bitrix_bot.py (файл курса при инверсии):
  - bot_handler: заменить had_session-логику (486, 500-505):
      before = await asyncio.to_thread(get_session, user_id)   # вместо bool
      ... process_message ...
      after = await asyncio.to_thread(get_session, user_id)
      if after and after["course_id"] and not (before and before["course_id"]):
          asyncio.create_task(_send_course_file(dialog_id, after["course_id"]))
      # course_id=0 falsy → файл уходит РОВНО при назначении курса
      # (и при старом флоу тоже: before None → after с курсом)
  - /hr «Пригласить» (622-625): гард if new_session and new_session["course_id"]
      (при ROLE_SELECT-старте файл уйдёт позже, когда сотрудник выберет роль)

Task 6 — CREATE scripts/backfill_prefix_roles.py:
  - MIRROR scripts/backfill_roles.py (--dry-run, *.bak через shutil.copy2,
    ensure_ascii=False, финальный assert len(chunks)==emb.shape[0])
  - для каждого чанка: doc_name = c.get("doc_name")
      нет doc_name (legacy 842) → не трогать
      doc_name.startswith(".") → В УДАЛЕНИЕ (мусор ._*, если успел заингеститься)
      parse_filename(doc_name)["roles"] → перезаписать c["roles"]
      roles None → не трогать
  - удаление: keep = [i for i, c ...]; chunks = [chunks[i]...]; emb = emb[keep]
    (СИНХРОННО, как index_store.remove_document); печать что удалено/перетегано
  - также: DELETE строк processed_files и архив курсов для удалённых ._* doc_name
    (переиспользовать get_processed_by_folders/remove_processed_file/
    get_course_by_doc_name/set_course_archived или прямой SQL — скрипту доступен
    app.db); dry-run НИЧЕГО не пишет

Task 7 — MODIFY тесты (обновить ожидания) + NEW tests/test_prefix_parse.py:
  - test_prefix_parse.py (чистая функция, фикстурный roles.json с prefixes):
      * "FO Внешний вид сотрудника.docx" → ["admin_reception"], title "Внешний вид сотрудника"
      * "FO, HSKP, ENG, RES Вывод номера из обращения (ремонт, OOS, ООО).docx"
        → 4 роли, запятые в скобках не ломают, OOS/ООО НЕ роли
      * "ALL КОНФЕДЕНЦИАЛЬНОСТЬ.docx" → ["all_staff"]
      * "fo,res Алгоритм.docx" → нижний регистр и без пробела работают
      * "Договор.docx" → roles None
      * "ENG ДЕЙСТВИЯ ДЕЖУРНОГО СМЕНЫ.docx" → suspicious None (кириллица не тревожит)
      * "FO, XYZ Документ.docx" → roles ["admin_reception"], suspicious "XYZ"
      * "FO, FO Дубль.docx" → ["admin_reception"] (дедуп)
  - fixture env (test_state_machine.py): в roles.json добавить prefixes; второй
    курс с doc_name "RES Брони.docx"; тесты:
      * новый юзер → ROLE_SELECT, сессия course_id=0
      * цифра роли admin_reception → назначен курс с FO/ALL, НЕ RES-курс
      * роль reservations, все RES/ALL-курсы DONE → «нет курсов» + notify_hr мокнут
      * память роли: DONE-сессия с ролью → новое сообщение БЕЗ меню, курс по роли
      * конфиг без ролей → сразу READING, первый непройденный курс (легаси)
  - test_folder_sync.py: листинг с ".DS_Store"/"._FO X.docx" → не обработаны и не
    в seen_files; файл "FO, RES Алгоритм.docx" в all_staff-папке → чанки с
    ["admin_reception","reservations"]
  - test_hr_invite.py: инвайт при сконфигурированных ролях → ответ-меню ролей,
    файл курса НЕ отправлен (гард course_id=0)
  - test_db.py: monkeypatch.setenv("HR_USER_IDS", "28528,36523") → init_db() →
    оба allowed; повторный init_db() не падает; add_employee поверх сида обновляет

Task 8 — MODIFY .env.example, INITIAL.md, task.md:
  - .env.example: комментарий у HR_USER_IDS «также автодопуск к employee-боту (тестировщики)»
  - task.md: чеклист сдачи — сервер: HR_USER_IDS=28528,36523 в серверном .env;
    прогнать backfill_prefix_roles.py на сервере + рестарт uvicorn; вручную в
    Битриксе сделать 36523 администратором портала (просьба Дмитрия — вне кода)
```

### Per task pseudocode (ключевые места)

```python
# Task 1 — app/roles.py::parse_filename (ЧИСТАЯ; конфиг с диска как load_roles_config)
_SUSPICIOUS_RE = re.compile(r"^[A-Z]{2,6}$")   # ТОЛЬКО латиница — капс-кириллица легальна

def parse_filename(file_name: str) -> dict:
    prefixes = load_roles_config().get("prefixes", {})
    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    found, title, suspicious = [], stem.strip(), None
    if prefixes:
        parts = stem.split(",")
        for i, part in enumerate(parts):
            token = part.strip()
            if token.upper() in prefixes:            # чистый токен: "FO"
                found.append(prefixes[token.upper()]); continue
            head, _, rest = token.partition(" ")      # хвост: "RES Вывод номера..."
            if head.upper() in prefixes:
                found.append(prefixes[head.upper()])
                title = rest.strip() or token
            else:
                # разбор ОСТАНОВЛЕН: всё от этого part — название (запятые в скобках)
                title = ",".join(parts[i:]).strip()
                if found and _SUSPICIOUS_RE.match(head):
                    suspicious = head                 # "FO, XYZ Документ" → XYZ
            break
        else:                                         # имя = только префиксы
            title = stem.strip()
    roles = list(dict.fromkeys(found)) or None
    return {"roles": roles, "title": title if roles else stem.strip(),
            "suspicious": suspicious}
# ВНИМАНИЕ: continue/break-структура — break после первого НЕчистого токена
# обязателен, иначе "(ремонт, OOS, ООО)" продолжит разбор.

# Task 4 — state_machine: выбор курса
def pick_course_for_role(courses, role_id, done_ids):
    for c in courses:                                  # порядок = approved_at DESC
        if c["id"] in done_ids: continue
        if role_id is None: return c                   # легаси без конфига ролей
        if {role_id, ALL_STAFF} & set(course_roles(c)): return c
    return None
```

### Integration Points
```yaml
DATABASE: миграций схемы НЕТ (сентинел 0 в существующей course_id NOT NULL);
          новый сид employees из HR_USER_IDS в init_db()
CONFIG:   data/roles.json +prefixes +3 роли; .env НЕ меняется (36523 уже в HR_USER_IDS
          локально — проверить СЕРВЕРНЫЙ .env при деплое)
INDEX:    scripts/backfill_prefix_roles.py один раз на сервере, затем рестарт uvicorn
          (global chunks/embeddings читаются на старте)
ROUTES:   без новых эндпоинтов; меняется поведение "/" (FSM) и ингеста
```

## Validation Loop

### Level 1: Syntax & Style
```bash
# venv проекта (pyenv vertical_standards_env), НЕ системный python
ruff check app/ scripts/ tests/ --fix
# mypy в репо не настроен — не вводить
```

### Level 2: Unit Tests
```bash
python -m pytest tests/ -v
# Ключевые: test_prefix_parse (8 кейсов), инверсия FSM, память роли,
# «нет курсов для роли», скрытые файлы, префикс главнее папки, сид HR_USER_IDS.
# Существующие тесты №1-№5 ДОЛЖНЫ остаться зелёными после обновления ожиданий.
```

### Level 3: Offline FSM-прогон (без сети, мок не нужен — notify_hr без env молчит)
```bash
python - <<'EOF'
import os; os.environ.setdefault("HR_USER_IDS", "")
from app.db import init_db, save_draft_course, activate_course_by_id, add_employee
import json, app.db as db
# ... tmp DB не нужна: использовать ЖИВУЮ onboarding.db НЕЛЬЗЯ — сделать копию:
EOF
# Проще: целевой прогон через pytest-кейс инверсии (Level 2 покрывает).
# Финальная живая проверка — на сервере: сотрудник reservations получает RES-курс.
```

### Level 4: Backfill dry-run (локально; на сервере — боевой прогон при деплое)
```bash
python scripts/backfill_prefix_roles.py --dry-run
# Ожидаемо локально: legacy 842 не тронуты, retag 0 (префиксных доков в локальном
# индексе нет), junk 0. Скрипт не падает, статистика печатается.
```

## Final validation Checklist
- [ ] `python -m pytest tests/ -v` зелёный (старые тесты обновлены, не удалены)
- [ ] `ruff check app/ scripts/ tests/` чистый
- [ ] parse_filename: все 8 кейсов из Task 7, включая запятые в скобках
- [ ] Инверсия: роль → курс по пересечению ролей; course_id=0 нигде не шлёт файл
- [ ] Память роли: после DONE новое сообщение не показывает меню повторно
- [ ] Сид: init_db() с HR_USER_IDS=28528,36523 → оба is_employee_allowed
- [ ] Тексты бота на русском, ensure_ascii=False
- [ ] task.md: серверные шаги (backfill, серверный .env, админка 36523 в портале)

## Anti-Patterns to Avoid
- ❌ НЕ вырезать префикс из doc_name в данных — только display_name для показа
- ❌ НЕ добавлять колонку roles в courses — считать на лету из doc_name
- ❌ НЕ изобретать приоритет ALL-vs-ролевых курсов — порядок активации, вопрос клиенту открыт
- ❌ НЕ включать PRAGMA foreign_keys (сломает сентинел 0)
- ❌ НЕ трогать role_mask/audience-логику №1 — фильтр RAG уже корректен
- ❌ НЕ переэмбеддировать индекс в backfill — текст чанков не менялся
- ❌ НЕ детектить suspicious по кириллице — капс-названия у клиента норма
- ❌ НЕ доверять локальному живому прогону (WSL-флап) — финал на сервере

## Открытые допущения (проговорить с Дмитрием/юзером)
1. Очерёдность курсов при нескольких подходящих = порядок активации (новые первыми,
   как сегодня). ALL-курсы первыми? — спросить.
2. «Пройденность» курса = сессия в DONE независимо от результата экзамена; пересдача
   несданного — скоуп №9 (не смешивать).
3. Смена роли командой «Роль» не переназначает уже начатый курс — осознанно.
4. Молчание ботов для 36523 на «Привет» (встреча 29.07) может иметь ВТОРУЮ причину
   кроме прав: по коду оба бота отвечают хоть что-то («🔒 Доступ…»/«⛔ Команды…»).
   Если после сида молчание останется — смотреть серверные логи (MSG…/BITRIX SEND,
   403 по client_id, доставку событий). Сид решает права, не доставку.
5. Права 36523 «как у меня» = членство в HR_USER_IDS (все команды HR-бота) +
   whitelist employee-бота. Админство в САМОМ Битриксе — ручной шаг в портале.

## Score: 8/10
Контекст полный: реальные имена файлов клиента проверены через API, точки правок
с номерами строк, сентинел-решение для NOT NULL без миграции, ловушка запятых в
названии и капс-кириллицы отражена в парсере и тестах. Минус балл — инверсия FSM
трогает самый связанный узел (state_machine + оба хендлера + инвайт): риск
пропущенного пути отправки файла курса; минус — живое поведение (порядок курсов,
серверный .env, уже-заингещенные файлы) проверяется только на сервере.
