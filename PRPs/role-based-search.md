name: "Поиск по ролям — role-based RAG filtering (доработка №1, 18ч/36 000₽)"
description: |
  Роль-адресат в метадате чанков, маппинг «папка Bitrix Disk → роль», выбор роли
  сотрудником в чате, фильтрация RAG-выдачи по роли + метка audience (staff/guest),
  закрывающая баг «пожар для гостей».

## Purpose
Одно-проходная реализация доработки №1 из сметы. Контекст ниже — самодостаточный:
структуры данных, реальные сниппеты, ловушки этого репо, порядок задач, исполняемые гейты.

## Core Principles
1. **Context is King** — весь нужный код и данные перечислены ниже
2. **Validation Loops** — ruff + pytest + офлайн-бэкфилл dry-run + живой eval
3. **Information Dense** — имена функций/полей из реального кода
4. **Progressive Success** — сначала метадата и фильтр, потом FSM, потом eval
5. **Global rules** — следовать CLAUDE.md (простейшее решение, не трогать чужой код, UI-тексты на русском)

---

## Goal

Бот «СтандартыВертикаль» отвечает сотруднику только фрагментами, адресованными его роли
(или всем сотрудникам), и никогда — гостевыми разделами. Роль сотрудник выбирает цифрой
при старте сессии и может сменить командой «Роль». Новые документы получают роли из
ролевой папки Bitrix Disk, существующие 842 чанка — из бэкфилла.

## Why

- Клиентский trust-баг: «Что делать при пожаре» вернул раздел для **гостей** (planning.md,
  «RAG-баг (пожар)», статус «приоритетная задача»). Роли + audience убирают посторонние ответы.
- Смета продана как «бот отвечает каждому релевантной для его роли информацией — без
  посторонней информации».
- Фундамент для доработки №6 (Qdrant «с фильтрацией по ролям» — метадата должна уже существовать).

## What

1. Чанк получает `roles: list[str]` и `audience: "staff"|"guest"` в метадате.
2. Конфиг `data/roles.json`: реестр ролей (id → русское имя) + маппинг folder_id → роли.
3. Поллер обходит все ролевые папки (плоско; рекурсия = доработка №4, НЕ делать).
4. Один документ в N папках = N file_id: чанки ингестятся с ролью каждой папки,
   курс генерируется только для первой копии (дедуп по doc_name).
5. Бэкфилл существующего индекса без переэмбеддинга и без изменения порядка чанков.
6. FSM: новое состояние ROLE_SELECT перед READING; `sessions.role` в SQLite; команда «Роль».
7. `rag.py::answer(..., role_filter)` — маска по ролям + безусловное исключение audience=guest.
8. Eval-харнес для валидационного цикла из сметы (вопросы Дмитрия с источником и ролью).

### Success Criteria
- [ ] Все чанки в `data/chunks_cache.json` имеют `roles` и `audience`; `len(chunks) == embeddings.shape[0]` сохранено
- [ ] Вопрос «что делать при пожаре» от роли housekeeper НЕ возвращает гостевой раздел (живой прогон)
- [ ] Новый сотрудник получает список ролей, отвечает цифрой, роль в `sessions.role`
- [ ] Документ, положенный в папку «Горничные», даёт чанки с `roles=["housekeeper"]`
- [ ] Тот же документ во второй папке НЕ порождает второй курс и второе HR-уведомление
- [ ] `pytest tests/ -v` зелёный, `ruff check app/ scripts/ tests/` чистый

## All Needed Context

### Documentation & References
```yaml
- file: app/rag.py
  why: |
    Точка фильтрации. Паттерн уже есть — section_filter умножает scores на 0/1-маску:
      mask = np.array([1.0 if c["section"] == section_filter else 0.0 for c in chunks])
      scores = scores * mask
    top = np.argsort(scores)[::-1][:16]; порог scores[i] > 0.01 отсеет занулённые.
    Ролевую маску делать ТОЧНО ТАК ЖЕ. OpenAI клиент: timeout=20.0, max_retries=3 —
    НЕ убирать (сеть WSL2 флапает).

- file: app/bitrix_bot.py
  why: |
    _disk_poll_loop/_check_folder — сейчас одна папка MONITOR_FOLDER_ID; станет цикл по
    ролевым папкам. process_new_document шаг 6: словарь `normalised` оставляет ТОЛЬКО
    text/heading/section — именно здесь чанки ТЕРЯЮТ roles (parse_docx их выдаёт!).
    Добавить roles (из папки) и audience. Гейт повторной обработки:
    `existing = get_course_by_doc_id(file_id)` — для второй копии документа курс не
    создаётся, значит нужен отдельный гейт processed_files, иначе поллер будет
    ингестить копию каждые 5 минут бесконечно.

- file: app/state_machine.py
  why: |
    FSM: process_message роутит по session["state"]. Добавить ветку ROLE_SELECT.
    _handle_reading вызывает rag_answer(..., section_filter=None) — сюда role_filter.
    parse_answer НЕ конфликтует с цифрами ролей (роутинг по состоянию раньше).

- file: app/db.py
  why: |
    init_db = CREATE TABLE IF NOT EXISTS → существующая onboarding.db НЕ получит новую
    колонку. Нужна миграция PRAGMA table_info + ALTER TABLE. DB_PATH читается при каждом
    _conn() → monkeypatch(app.db, "DB_PATH", tmp) в тестах РАБОТАЕТ (в отличие от
    паттерна default-аргумента — урок content_machine).

- file: scripts/parse_standards.py
  why: |
    parse_docx УЖЕ выдаёт roles через get_roles(section_code, heading_lower):
    SECTION_ROLES (префикс VA.XX → роли) + HEADING_ROLE_OVERRIDES (ключевые слова).
    Схема чанка: {id, section, code, heading, text, roles, status}.
    ПЕРЕИСПОЛЬЗОВАТЬ get_roles в бэкфилле, не переписывать.

- file: scripts/generate_role_docs.py
  why: |
    assign_roles(chunk) — вариант той же логики по РУССКИМ секциям («Хозяйственная
    служба» и т.д.) через SECTION_DEFAULT_ROLES. Живой индекс имеет ИМЕННО русские
    секции (Хозяйственная служба 240, СПиР 108, Общее 91, …) + хвост VA.* (GENERAL 38,
    VA.MD 25…) — бэкфиллу нужны ОБА маппинга. Реестр ROLES (12 ролей с русскими
    именами) — источник для data/roles.json.

- file: scripts/dedup_index.py
  why: |
    Образец скрипта-миграции индекса: assert len(chunks)==emb.shape[0], бэкап *.bak
    через shutil.copy2 ПЕРЕД записью, json.dump(..., ensure_ascii=False).

- file: data/chunks_cache.json
  why: |
    ФАКТ (проверено): 842 чанка, у ВСЕХ только {heading, section, text} — ни roles,
    ни code, ни id. Бэкфилл работает по section+heading, поле code недоступно.

- url: https://apidocs.bitrix24.com/api-reference/disk/folder/disk-folder-get-children.html
  why: disk.folder.getchildren — уже используется с filter TYPE=file; для ролевых папок то же самое.

- docfile: data/bitrix24_docs.md
  why: локальная выжимка Bitrix REST (51k строк) — искать здесь прежде чем в веб.

- file: ~/code/PlatoIsDead/agent_docs/rag_pipeline.md
  why: конвенции RAG (top-k, chunk size) — не ломать.
```

### Current Codebase tree (релевантная часть)
```bash
vertical_standards/
├── app/
│   ├── bitrix_bot.py        # FastAPI: "/", "/hr", "/disk-webhook", поллер, ингест
│   ├── state_machine.py     # FSM READING→BASIC_TEST→WAITING_HR→EXAM→DONE
│   ├── rag.py               # load_index, cosine_sim, answer (OpenAI)
│   ├── db.py                # SQLite: courses, sessions, answers
│   └── course_generator.py  # генерация 15 вопросов
├── scripts/
│   ├── parse_standards.py   # docx → чанки С ролями (не используется живым пайплайном!)
│   ├── generate_role_docs.py# реестр 12 ролей, assign_roles по русским секциям
│   ├── dedup_index.py       # образец миграции индекса с *.bak
│   └── build_index_openai.py
├── data/
│   ├── chunks_cache.json    # 842 чанка {heading, section, text} — БЕЗ ролей
│   ├── embeddings_cache.npy # выровнен с chunks по индексу
│   └── bitrix24_docs.md
├── onboarding.db            # ЖИВАЯ БД — мигрировать, не пересоздавать
└── requirements.txt         # НЕТ pytest/ruff — поставить в venv (pyenv env vertical_standards_env)
```

### Desired Codebase tree
```bash
├── app/
│   ├── roles.py             # NEW: загрузка data/roles.json, реестр ролей, folder→roles, role_mask()
│   ├── rag.py               # MOD: answer(..., role_filter=None) + audience-фильтр
│   ├── bitrix_bot.py        # MOD: поллер по ролевым папкам, roles/audience в normalised, дедуп курсов
│   ├── state_machine.py     # MOD: ROLE_SELECT, команда «Роль», role_filter в rag_answer
│   └── db.py                # MOD: sessions.role, processed_files, миграция колонок
├── data/
│   ├── roles.json           # NEW: конфиг ролей и папок (заполняет Никита реальными folder_id)
│   └── eval_roles.json      # NEW: вопросы Дмитрия {question, role, expect, forbid_guest}
├── scripts/
│   ├── backfill_roles.py    # NEW: одноразовая миграция 842 чанков (roles+audience), *.bak
│   └── eval_roles.py        # NEW: retrieval-only проверка по eval_roles.json (живая, нужен ключ)
└── tests/
    ├── test_role_mask.py    # NEW: чистая маска без сети
    ├── test_db.py           # NEW: миграция колонки, processed_files
    ├── test_state_machine.py# NEW: ROLE_SELECT-флоу (mock rag_answer, tmp DB)
    └── test_backfill.py     # NEW: назначение ролей/audience, выравнивание сохранено
```

### Known Gotchas of our codebase & Library Quirks
```python
# CRITICAL: process_new_document шаг 6 нормализует чанки в {text, heading, section} —
# это МЕСТО, где сейчас теряются roles из parse_docx. Роль для живого ингеста берём
# ИЗ ПАПКИ (смета), а не из parse_docx — содержимое может противоречить раскладке HR.

# CRITICAL: гейт поллера — get_course_by_doc_id(file_id). Вторая копия документа
# (другой file_id) без курса будет ре-ингеститься каждые POLL_INTERVAL=300с.
# Нужна таблица processed_files, гейтящая НЕЗАВИСИМО от курсов.

# CRITICAL: бэкфилл НЕ меняет текст → переэмбеддинг НЕ нужен; НЕ менять порядок/число
# чанков — embeddings_cache.npy выровнен по индексу (см. assert в dedup_index.py).

# CRITICAL: init_db() — CREATE TABLE IF NOT EXISTS не добавляет колонки в существующую
# onboarding.db. Миграция: PRAGMA table_info(sessions) → ALTER TABLE ADD COLUMN.
# SQLite ALTER TABLE ADD COLUMN дёшев и безопасен; DEFAULT NULL.

# CRITICAL: сеть WSL2 флапает (ConnectTimeout → 200 через секунды). НЕ трогать:
# OpenAI(timeout=20.0, max_retries=3) в rag.py, retry-циклы в _send/notify_hr,
# _is_duplicate (дедуп 15с). Локальный «живой» тест недостоверен — финальная проверка
# через ngrok/сервер, как раньше.

# GOTCHA: чанки без ключа "roles" (старые/сторонние) считать all_staff — обратная
# совместимость, бот не должен молчать, если бэкфилл ещё не прогнан.

# GOTCHA: audience=guest исключать ВСЕГДА (бот только для сотрудников — task.md,
# «Системный промпт RAG»), не только при заданном role_filter.

# GOTCHA: global chunks, embeddings в bitrix_bot.py перечитываются только в
# process_new_document. После бэкфилла нужен РЕСТАРТ uvicorn.

# GOTCHA: create_session жёстко пишет state='READING'. Добавить параметр state,
# по умолчанию 'READING' — существующие вызовы не ломать.

# GOTCHA: тесты — monkeypatch(app.db, "DB_PATH", str(tmp_path/"test.db")) работает,
# т.к. DB_PATH читается в _conn() при вызове. rag_answer мокать в app.state_machine
# (импортирован туда по имени: from app.rag import answer as rag_answer).

# GOTCHA: pytest/ruff НЕ в requirements.txt. Ставить в venv проекта
# (pyenv activate vertical_standards_env), НЕ системный python.
```

## Implementation Blueprint

### Data models and structure

```python
# data/roles.json — единственный источник правды о ролях (UTF-8, русские имена)
{
  "roles": {
    "housekeeper":     "Горничная / Уборщица",
    "admin_reception": "Администратор ресепшн (СПиР)",
    "engineer":        "Техник / Инженер",
    "general_manager": "Администратор / Управляющий",
    "all_staff":       "Все сотрудники"        # служебная, в меню выбора НЕ показывать
  },
  "folders": {
    # Bitrix folder_id → роли документа. Заполняется реальными ID при настройке.
    "111": ["housekeeper"],
    "112": ["admin_reception"],
    "113": ["engineer"],
    "114": ["general_manager"],
    "115": ["all_staff"]          # общая папка (бывший MONITOR_FOLDER_ID)
  }
}
# Смета называет 4 роли — стартуем с 4 + all_staff; реестр расширяем без кода.
# 12-ролевый реестр из generate_role_docs.py — источник имён, если клиент захочет больше.

# Чанк после доработки:
{"text": "...", "heading": "...", "section": "...",
 "roles": ["housekeeper"], "audience": "staff"}   # audience: "staff" | "guest"

# SQLite:
# sessions  + role TEXT DEFAULT NULL          (id роли из roles.json)
# processed_files(file_id TEXT PRIMARY KEY, doc_name TEXT, folder_id TEXT,
#                 processed_at TEXT)          (гейт поллера, независим от courses)
```

### List of tasks (в порядке выполнения)

```yaml
Task 1 — CREATE data/roles.json + app/roles.py:
  - roles.json по схеме выше (folder_id — плейсхолдеры, Никита заполнит реальными)
  - app/roles.py:
      load_roles_config() -> dict           # читает data/roles.json, кэш на модуле
      selectable_roles() -> list[(id, name)] # без all_staff, стабильный порядок
      roles_for_folder(folder_id) -> list[str]  # [] если папка не ролевит
      role_mask(chunks, role_id) -> np.ndarray  # ЧИСТАЯ функция: 1.0 если
        # (role_id in c.get("roles") or "all_staff" in roles or roles отсутствует/пуст)
        # and c.get("audience") != "guest"
  - PATTERN: пути через os.path.join(os.path.dirname(__file__), "..", "data") как в rag.py

Task 2 — MODIFY app/db.py (миграция + новые функции):
  - в init_db() после executescript:
      _ensure_column(conn, "sessions", "role", "TEXT")   # PRAGMA table_info → ALTER TABLE
      CREATE TABLE IF NOT EXISTS processed_files(...)
      # backfill гейта: INSERT OR IGNORE INTO processed_files(file_id, doc_name)
      #   SELECT doc_id, doc_name FROM courses WHERE doc_id IS NOT NULL
  - update_session(...): добавить параметр role (тот же паттерн non-None полей)
  - NEW: set_session_role(session_id, role), is_file_processed(file_id) -> bool,
         mark_file_processed(file_id, doc_name, folder_id),
         get_course_by_doc_name(doc_name) -> dict | None

Task 3 — MODIFY app/rag.py:
  - answer(query, chunks, embeddings, section_filter, answer_length, role_filter=None)
  - после section-маски:
      from app.roles import role_mask
      if role_filter: scores = scores * role_mask(chunks, role_filter)
      else: занулить только audience=guest (гостевые НЕ отдавать никогда)
  - SYSTEM_PROMPT: добавить строку «Ты отвечаешь СОТРУДНИКУ отеля, не гостю.
    Инструкции для гостей не пересказывай как инструкции для сотрудника.»
  - существующие вызовы (state_machine) обновляются в Task 6; сигнатура с default —
    ничего не ломается

Task 4 — CREATE scripts/backfill_roles.py:
  - MIRROR: scripts/dedup_index.py (assert выравнивания, *.bak, ensure_ascii=False)
  - для каждого чанка БЕЗ "roles":
      section русская («Хозяйственная служба»…) → SECTION_DEFAULT_ROLES из
        generate_role_docs.py (импортировать, не копировать)
      section VA.* / GENERAL → get_roles() из parse_standards.py
      + HEADING-уточнения уже внутри этих функций
  - audience: "guest" если re.search(r"гост(ь|я|ям|ей|ин)", heading.lower())
      и НЕ содержит маркеров персонала («действия персонала», «сотрудник»);
      иначе "staff". Спорные случаи выводить в stdout для ручного скана
  - флаг --dry-run: печатает статистику ролей/audience, файл не пишет
  - НЕ менять порядок чанков, НЕ трогать embeddings_cache.npy

Task 5 — MODIFY app/bitrix_bot.py:
  - _disk_poll_loop: папки = ключи roles_config["folders"] + MONITOR_FOLDER_ID
      (если задан и не в конфиге → роль all_staff). Цикл: for fid in folders:
      await _check_folder(fid)
  - _check_folder(folder_id): гейт → is_file_processed(file_id) вместо/в дополнение
      к get_course_by_doc_id; передавать roles=roles_for_folder(folder_id) дальше
  - process_new_document(file_id, file_name, roles): 
      normalised: {..., "roles": roles or ["all_staff"], "audience": "staff"}
      # HR кладёт в ролевые папки ТОЛЬКО стандарты для персонала; гостевые тексты
      # в старом индексе ловит бэкфилл
      перед генерацией вопросов: if get_course_by_doc_name(file_name):
        mark_file_processed(...); print("[process_new_document] duplicate doc, chunks
        ingested for role, course skipped"); return
      после успешного save_draft_course: mark_file_processed(...)
  - /disk-webhook: roles_for_folder(folder_id) при передаче в process_new_document

Task 6 — MODIFY app/state_machine.py:
  - create_session(..., state="READING") в db.py; новая сессия:
      state = "ROLE_SELECT" if selectable_roles() else "READING"
  - _start_role_select(): "Привет! Выбери свою роль — отвечу только тем, что
      относится к твоей работе:\n1. Горничная / Уборщица\n2. ..." (нумерация по
      selectable_roles())
  - _handle_role_select(session, message): цифра N в диапазоне → 
      update_session(role=..., state="READING") → вернуть подтверждение роли +
      _start_reading(...); иначе — повторить список («Ответь цифрой от 1 до N»)
  - _handle_reading: if message.strip().lower() == "роль": состояние ROLE_SELECT,
      вернуть список (нужно Никите для теста в разных ролях — смета)
  - rag_answer(..., role_filter=session.get("role"))
  - process_message: ветка "ROLE_SELECT"

Task 7 — CREATE tests/ (pytest):
  - test_role_mask.py: роль совпала=1; all_staff=1; нет ключа roles=1; чужая роль=0;
      audience=guest=0 даже для all_staff
  - test_db.py: старая схема sessions без role → init_db() добавляет колонку;
      processed_files insert/check; get_course_by_doc_name
  - test_state_machine.py: tmp DB (monkeypatch DB_PATH) + monkeypatch
      app.state_machine.rag_answer → фиксированный ответ; сценарий: первое сообщение →
      список ролей; "2" → роль сохранена, READING; вопрос → rag_answer вызван с
      role_filter="admin_reception"; "Роль" → снова список; "99" → реprompt
  - test_backfill.py: образцы чанков (Хозяйственная служба → housekeeper+hsk_supervisor;
      «ДЕЙСТВИЯ ГОСТЯ ПРИ ПОЖАРЕ» → audience=guest; «ДЕЙСТВИЯ ПЕРСОНАЛА ПРИ ПОЖАРЕ» →
      staff+all_staff); порядок не изменён

Task 8 — CREATE scripts/eval_roles.py + data/eval_roles.json:
  - eval_roles.json (стартовые 5-6 строк, дальше пополняет Дмитрий):
      [{"question": "Что делать при пожаре?", "role": "housekeeper",
        "expect_substring": "персонал", "forbid_substring": "гост"}]
  - скрипт: load_index → для каждой строки embed вопрос (OpenAI, timeout/retries как
      в rag.py) → маска role_mask → топ-16 → PASS если expect_substring встречается в
      heading/text топа И forbid_substring НЕ встречается; вывод таблицей, exit 1 при
      фейлах. ЖИВОЙ гейт (ключ + сеть) — не для CI, для ежедневного созвона

Task 9 — MODIFY .env.example, README.md, task.md:
  - .env.example: комментарий, что ролевые папки теперь в data/roles.json,
      MONITOR_FOLDER_ID = общая папка (all_staff)
  - task.md: отметить выполненное (audience-фикс, роли), добавить «заполнить реальные
      folder_id в roles.json» в открытые
```

### Per task pseudocode (ключевые места)

```python
# Task 1 — app/roles.py::role_mask (ЧИСТАЯ, тестируется без сети)
def role_mask(chunks: list[dict], role_id: str | None) -> "np.ndarray":
    vals = []
    for c in chunks:
        if c.get("audience") == "guest":          # гостевое — никогда
            vals.append(0.0); continue
        roles = c.get("roles") or []              # нет ключа/пусто = all_staff (b/c)
        ok = (not roles) or ("all_staff" in roles) or (role_id and role_id in roles)
        vals.append(1.0 if ok else 0.0)
    return np.array(vals)

# Task 2 — app/db.py::_ensure_column
def _ensure_column(conn, table: str, col: str, ddl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

# Task 5 — bitrix_bot.py поллер (папки читаются каждый цикл — конфиг можно менять без рестарта)
async def _disk_poll_loop():
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        folders = dict(load_roles_config().get("folders", {}))
        legacy = os.getenv("MONITOR_FOLDER_ID")
        if legacy and legacy not in folders:
            folders[legacy] = ["all_staff"]
        for folder_id, roles in folders.items():
            await _check_folder(folder_id, roles)   # внутри: is_file_processed-гейт

# Task 6 — state_machine.py роутинг
if state == "ROLE_SELECT":
    return _handle_role_select(session, message)
...
def _handle_role_select(session, message):
    options = selectable_roles()                    # [(id, name), ...]
    m = re.match(r"^\s*(\d+)\s*$", message)
    if not m or not (1 <= int(m.group(1)) <= len(options)):
        return "Ответь цифрой:\n" + _format_role_list(options)
    role_id, role_name = options[int(m.group(1)) - 1]
    update_session(session["id"], state="READING", role=role_id)
    course = get_course_by_id(session["course_id"])
    return f"✅ Роль: *{role_name}*\n\n" + _start_reading(session, course)
```

### Integration Points
```yaml
DATABASE:
  - migration: "_ensure_column(sessions, role TEXT) внутри init_db(); processed_files CREATE IF NOT EXISTS + seed из courses.doc_id"
CONFIG:
  - data/roles.json — новый конфиг; .env НЕ расширять (folder→role живёт в json)
INDEX:
  - scripts/backfill_roles.py прогнать ОДИН раз перед рестартом uvicorn; *.bak обязательны
ROUTES:
  - без новых эндпоинтов; меняется поведение "/" (FSM) и поллера
DEPLOY:
  - после мержа: pyenv activate vertical_standards_env → backfill → рестарт uvicorn
```

## Validation Loop

### Level 1: Syntax & Style
```bash
# venv проекта (pyenv), НЕ системный python; поставить dev-инструменты один раз:
pip install pytest ruff
ruff check app/ scripts/ tests/ --fix
# Expected: чисто. mypy в репо не настроен — не вводить в рамках этой задачи.
```

### Level 2: Unit Tests
```bash
python -m pytest tests/ -v
# Ключевые кейсы (см. Task 7): маска ролей, guest-исключение, миграция колонки,
# ROLE_SELECT-флоу с mock rag_answer, backfill-теги, выравнивание индекса.
```

### Level 3: Offline-миграция
```bash
python scripts/backfill_roles.py --dry-run    # статистика ролей/audience, спорные заголовки
python scripts/backfill_roles.py              # пишет с *.bak
python - <<'EOF'
import json, numpy as np
ch = json.load(open("data/chunks_cache.json", encoding="utf-8"))
emb = np.load("data/embeddings_cache.npy")
assert len(ch) == emb.shape[0] == 842, (len(ch), emb.shape)
assert all("roles" in c and "audience" in c for c in ch)
print("guest chunks:", sum(c["audience"] == "guest" for c in ch))
EOF
```

### Level 4: Integration (локально, сеть флапает — финально на сервере/ngrok)
```bash
uvicorn app.bitrix_bot:app --port 8000 &
# новый пользователь → должен прийти список ролей (смотреть stdout: BITRIX SEND упадёт
# без реального портала — проверяем текст ответа в логе process_message через тестовый хук
# ЛИБО прямой вызов FSM:
python - <<'EOF'
from app.state_machine import process_message
from app.rag import load_index
ch, emb = load_index()
print(process_message("999001", "привет", "u999001", ch, emb))   # ждём список ролей
print(process_message("999001", "1", "u999001", ch, emb))        # ждём подтверждение роли
EOF

# Живой eval (нужен OPENAI_API_KEY, сеть):
python scripts/eval_roles.py
# Expected: PASS по стартовым строкам, в т.ч. «пожар» для housekeeper без гостевого раздела
```

## Final validation Checklist
- [ ] `python -m pytest tests/ -v` — зелёный
- [ ] `ruff check app/ scripts/ tests/` — чистый
- [ ] backfill: 842/842 с roles+audience, выравнивание с npy, *.bak на месте
- [ ] FSM-прогон: список ролей → цифра → READING → вопрос идёт с role_filter
- [ ] «пожар» от housekeeper: гостевой раздел отсутствует в выдаче (eval_roles.py)
- [ ] вторая копия документа: чанки с новой ролью есть, второго курса/уведомления нет
- [ ] тексты бота на русском, UTF-8, ensure_ascii=False
- [ ] planning.md/task.md обновлены

---

## Anti-Patterns to Avoid
- ❌ НЕ делать рекурсию по подпапкам — это доработка №4 (22ч), не смешивать смету
- ❌ НЕ переэмбеддировать индекс при бэкфилле — текст не менялся
- ❌ НЕ переписывать get_roles/assign_roles — импортировать существующие
- ❌ НЕ убирать retry/timeout-паттерны (флапающая сеть — свойство окружения)
- ❌ НЕ пересоздавать onboarding.db — только ALTER TABLE миграция
- ❌ НЕ хардкодить роли в коде — только data/roles.json
- ❌ НЕ доверять локальному «живому» прогону — финальная проверка через ngrok/сервер

## Открытые допущения (проговорить с клиентом на созвоне)
1. «Папки с картинками»: изображения в ролевых папках игнорируются ингестом
   (фильтр docx/md). Если имелось в виду «читать картинки» — это НЕ в смете №1.
2. Роль хранится per-сессия: новый курс = снова выбор роли (обычно совпадает — можно
   позже предзаполнять последней ролью).
3. Гостевые разделы исключаются для ВСЕХ ролей — бот только для сотрудников.

## Score: 8/10
Уверенность в one-pass: контекст полный (реальная схема данных проверена, ловушки
нормализации/гейта поллера/миграции БД описаны, переиспользуемые функции найдены).
Минус балл за живые интеграции (Bitrix-папки с реальными folder_id, флапающая сеть) и
минус за эвристику audience по заголовкам — может потребовать ручной правки спорных
чанков после dry-run.
