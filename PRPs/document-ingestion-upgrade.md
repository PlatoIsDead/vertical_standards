name: "Расширение загрузки документов (доработка №4, 22ч/44 000₽)"
description: |
  Рекурсивный обход подпапок Bitrix Disk, форматы PPTX/TXT/PDF, отправка файла
  документа в чат сотруднику, замена версии одноимённого файла, удаление чанков
  при удалении файла. Самая рискованная доработка: мутации живого индекса.

## Purpose
Одно-проходная реализация доработки №4. Требует выполненных №1–№3 (они в кодовой
базе). Главные новые модули: app/doc_parsers.py (форматы) и app/index_store.py
(атомарные мутации индекса под локом).

## Core Principles
1. **Context is King** — схемы, сниппеты и ловушки ниже проверены по живому коду
2. **Validation Loops** — ruff + pytest (парсеры/индекс/синк офлайн) + живой прогон
3. **Information Dense** — имена из реального кода
4. **Progressive Success** — парсеры → хранилище индекса → рекурсия/синк → файл в чат
5. **Global rules** — CLAUDE.md; тексты бота русские

---

## Goal

HR кладёт документы любого формата (docx/md/pptx/txt/pdf) в любую подпапку ролевой
папки — бот их находит. Перезаливка одноимённого файла заменяет старые чанки, не
задевая остальные. Удалённый из папки файл исчезает из поиска, его курс архивируется.
Сотрудник при старте курса получает сам файл документа в чат (не только ссылку).

## Why

- Смета №4: «документы любых форматов из любых подпапок, без устаревших версий в поиске».
- Сейчас: только docx/md, только корень ролевой папки; повторная заливка = дубли
  чанков навсегда; удалённый документ продолжает отвечать в RAG (мусор в топ-16).
- Фундамент №6 (Qdrant): метадата doc_name/folder_id в чанках нужна для миграции.

## What

1. **Метадата чанка**: + `doc_name`, `folder_id` (только для документов пайплайна;
   legacy 842 чанка не имеют — замена/удаление на них НЕ действует, зафиксировано).
2. **app/doc_parsers.py**: диспетчер parse_file по расширению; docx — существующий
   `scripts/parse_standards.parse_docx`; md — перенос `_parse_md` из bitrix_bot;
   txt — чанки по 400–500 слов; pptx — python-pptx, слайд = чанк; pdf — pypdf,
   страница = чанк (PDF есть в planning.md и task.md-бэклоге; в тексте сметы клиенту
   его нет — дёшево, делаем, отметить клиенту как бонус).
3. **app/index_store.py**: все мутации chunks_cache.json + embeddings_cache.npy
   через threading.Lock и атомарную запись (tmp + os.replace). Закрывает и
   СУЩЕСТВУЮЩУЮ гонку: два одновременных ингеста делают read-modify-write без лока
   (lost update) — сейчас это живой баг, №4 его чинит попутно.
4. **Рекурсия**: обход подпапок до глубины 5; роли наследуются от корневой ролевой
   папки; подпапка, сама замапленная в roles.json, при рекурсии пропускается
   (её обходит главный цикл со своими ролями).
5. **Замена версии** — ДВА триггера (второй найден на ресёрче, в смете неявен):
   (a) новый file_id + то же doc_name + та же папка (удалить старую → залить новую);
   (b) тот же file_id, но изменился UPDATE_TIME — перезапись через веб-UI Bitrix
       создаёт новую ВЕРСИЮ с ТЕМ ЖЕ file_id, гейт по file_id это НЕ видит.
   Оба → удалить старые чанки (doc_name, folder_id) → влить новые. Курс и правки
   HR (№3) сохраняются; архивный курс разархивируется. То же имя в ДРУГОЙ папке =
   копия для другой роли (поведение №1, не трогать).
6. **Удаление**: после обхода корневой папки — processed_files по посещённым папкам
   минус увиденные file_id = кандидаты; удаление только со ВТОРОГО подряд промаха
   (two-strike, in-memory — защита от транзиентного глюка листинга); удалить чанки,
   снять processed_files; если копий doc_name в других папках не осталось —
   `courses.archived_at` (активные сессии не трогаем, новые не стартуют).
7. **Файл в чат**: при старте курса (новая сессия) бот прикладывает сам документ
   через `im.disk.file.commit` (или `im.v2.File.upload`) — методы БЕЗ детальных
   секций в выжимке, параметры проверяются живьём; при любой ошибке — молча
   остаёмся на ссылке (detail_url уже в тексте _start_reading).

### Success Criteria
- [ ] Файл в подпапке ролевой папки ингестится с ролями корня; глубина ≤5
- [ ] .pptx/.txt/.pdf режутся на чанки и попадают в индекс с doc_name/folder_id
- [ ] Повторная заливка одноимённого файла (новый file_id) в ту же папку: старых
      чанков нет, новые есть, курс один, правки вопросов HR сохранены
- [ ] Перезапись через веб-UI (тот же file_id, новый UPDATE_TIME) тоже реингестится
- [ ] Копия в другой ролевой папке: оба набора чанков живут (поведение №1)
- [ ] Удалённый файл: чанки исчезли со 2-го полла, курс архивирован, «активных
      курсов нет» для новых сотрудников; один транзиентный пустой листинг НЕ удаляет
- [ ] `len(chunks) == embeddings.shape[0]` после ЛЮБОЙ операции
- [ ] Все существующие 60 тестов зелёные; ruff чистый

## All Needed Context

### Documentation & References
```yaml
- file: app/bitrix_bot.py
  why: |
    Ядро изменений. _check_folder: getchildren с {"filter": {"TYPE": "file"}},
    гейт is_file_processed, ext-фильтр ("docx","md") — ДВА места фильтра (тут и в
    process_new_document). process_new_document: скачивание (disk.file.get →
    DOWNLOAD_URL → GET), парсинг, _embed по одному тексту, чтение-дописывание
    chunks_cache/embeddings ВРУЧНУЮ (шаг 6 — переехать в index_store), normalised
    (сюда doc_name/folder_id), шаг 6.5 дедуп по doc_name, генерация курса, шаг 8-10.
    _monitored_folders: folder_id→roles из roles.json + легаси MONITOR_FOLDER_ID,
    читается каждый цикл. _processing set — гейт параллельной обработки одного файла.
    ВАЖНО: global chunks, embeddings перечитываются load_index() после ингеста —
    после ЛЮБОЙ мутации индекса делать то же.

- file: app/rag.py
  why: |
    load_index() — источник глобалов. answer() использует c['section'], c['heading'],
    c['text'] и role_mask (лишние ключи чанка игнорируются — doc_name/folder_id
    безопасны). CHUNKS_PATH/EMBEDDINGS_PATH — те же пути использовать в index_store
    (импортировать оттуда, не дублировать строки).

- file: scripts/parse_standards.py
  why: |
    parse_docx(filepath) -> list[dict] с {id, section, code, heading, text, roles,
    status} — нормализация в process_new_document оставляет text/heading/section
    (+roles из ПАПКИ, не из parse_docx). Диспетчер doc_parsers должен вернуть
    «сырые» чанки с хотя бы {text, heading, section} — нормализация как сейчас.
    Импорт через sys.path-хак (scripts не пакет) — перенести хак в doc_parsers.

- file: app/bitrix_bot.py::_parse_md
  why: перенести в doc_parsers как parse_md БЕЗ изменений (split по #-заголовкам,
    чанк = секция, body > 50 симв.). Из bitrix_bot удалить.

- file: scripts/dedup_index.py
  why: |
    Паттерн выравнивания: assert len(chunks)==emb.shape[0]; keep_idx → chunks[i],
    emb[keep_idx]; ensure_ascii=False. index_store.remove_document — та же механика
    (маска по doc_name+folder_id), но атомарная запись tmp+os.replace вместо .bak
    (рантайм-мутации каждый полл — .bak-черновики не копить; бэкап делает деплой).

- file: app/db.py
  why: |
    processed_files(file_id PK, doc_name, folder_id, processed_at) — гейт поллера;
    сидирован из courses.doc_id (у legacy-строк folder_id NULL!). courses:
    approved_at/questions_json; get_active_courses = WHERE approved_at IS NOT NULL
    (+ добавить archived_at IS NULL). _ensure_column — паттерн миграции.
    get_course_by_doc_name — дедуп копий (№1).

- file: tests/test_hr_edit_flow.py + tests/test_hr_invite.py
  why: паттерны тестов (tmp DB, monkeypatch, TestClient). Для №4 интеграционный
    тест process_new_document гоняется asyncio.run(...) в sync-тесте (pytest-asyncio
    НЕТ в зависимостях — не добавлять), с monkeypatch httpx.AsyncClient и _embed_texts.

- docfile: data/referance/bitrix24_docs.md
  why: |
    ПРОВЕРЕНО в выжимке: im.disk.file.commit («Добавляет файл в чат»),
    im.disk.file.save, im.v2.File.upload («Загружает файл в чат»), im.dialog.get
    (базовые данные диалога, ~строки 1460–1532) — ТОЛЬКО каталожные строки,
    детальных секций с параметрами НЕТ. Параметры брать из онлайн-доки, проверять живьём.

- url: https://apidocs.bitrix24.com/api-reference/chats/files/im-disk-file-commit.html
  why: параметры im.disk.file.commit (CHAT_ID + идентификатор файла диска). Живой тест.

- url: https://apidocs.bitrix24.com/api-reference/chats/im-dialog-get.html
  why: im.dialog.get — получить CHAT_ID по DIALOG_ID вида "u123" (im.disk.* хотят CHAT_ID).

- url: https://apidocs.bitrix24.com/api-reference/disk/folder/disk-folder-get-children.html
  why: |
    getchildren: у объектов есть TYPE ("file"/"folder"), ID, NAME, UPDATE_TIME.
    Для рекурсии — ВТОРОЙ вызов с {"filter": {"TYPE": "folder"}} (существующий
    file-вызов не трогаем — меньше риска). Точное имя поля времени (UPDATE_TIME)
    подтвердить живьём по логу первого полла.

- url: https://python-pptx.readthedocs.io/en/latest/
  why: python-pptx — Presentation(path).slides; shape.has_text_frame → .text_frame.text;
    slide.shapes.title (может быть None).

- url: https://pypdf.readthedocs.io/en/stable/user/extract-text.html
  why: pypdf — PdfReader(path).pages[i].extract_text() (может вернуть "" для сканов).
```

### Current Codebase tree (релевантная часть)
```bash
vertical_standards/
├── app/
│   ├── bitrix_bot.py        # поллер, ингест, HR-команды (№2/№3), _parse_md
│   ├── rag.py               # load_index, CHUNKS_PATH/EMBEDDINGS_PATH, answer
│   ├── hr_tools.py          # №3 (не трогать)
│   ├── state_machine.py     # _start_reading шлёт detail_url-ссылку (текст)
│   └── db.py                # courses/sessions/answers/employees/processed_files/seen
├── scripts/parse_standards.py  # parse_docx (docx → чанки)
├── data/chunks_cache.json      # ЖИВОЙ индекс: 842 legacy + пайплайновые чанки
├── data/embeddings_cache.npy   # выровнен по индексу
├── requirements.txt            # НЕТ python-pptx/pypdf — добавить
└── tests/                      # 60 тестов
```

### Desired Codebase tree
```bash
├── app/
│   ├── doc_parsers.py       # NEW: SUPPORTED_EXTS, parse_file(path, ext, file_name);
│   │                        #      md перенесён, + txt/pptx/pdf, docx через scripts/
│   ├── index_store.py       # NEW: Lock + атомарные append_document/remove_document/
│   │                        #      has_document; пути импортирует из app.rag
│   ├── bitrix_bot.py        # MOD: рекурсия+синк удалений, замена версии (2 триггера),
│   │                        #      _embed_texts на уровень модуля, файл в чат, хуки
│   └── db.py                # MOD: courses.archived_at, processed_files.update_time,
│                            #      archive/unarchive, выборки по папкам
├── requirements.txt         # MOD: + python-pptx, pypdf
└── tests/
    ├── test_doc_parsers.py  # NEW: txt/pptx/pdf/md
    ├── test_index_store.py  # NEW: append/remove/выравнивание/legacy нетронуты
    └── test_folder_sync.py  # NEW: рекурсия, two-strike удаление, замена версии
                             #      (process_new_document через asyncio.run + моки)
```

### Known Gotchas of our codebase & Library Quirks
```python
# CRITICAL (находка ресёрча): перезалив одноимённого файла через веб-UI Bitrix Disk
# создаёт новую ВЕРСИЮ с ТЕМ ЖЕ file_id → гейт is_file_processed слеп. Хранить
# update_time (из getchildren/disk.file.get) в processed_files; при полле
# update_time изменился → снять гейт и реингестить как замену. Имя поля
# (UPDATE_TIME) подтвердить живьём — на первом полле логировать ключи объекта.

# CRITICAL: existing гонка — два параллельных ингеста читают-пишут chunks_cache без
# лока (lost update). ВСЕ мутации индекса → index_store под threading.Lock (функции
# sync, зовутся через asyncio.to_thread — threading.Lock корректен).

# CRITICAL: embeddings выровнены по индексу списка чанков. Любая операция:
# assert len(chunks) == emb.shape[0] ДО и ПОСЛЕ. Удаление — по маске одним проходом.

# CRITICAL: атомарная запись: json → tmp-файл в ТОЙ ЖЕ папке → os.replace;
# npy так же (np.save в tmp, os.replace). Иначе рестарт посреди записи убьёт индекс.
# Порядок: сначала npy, потом json?? НЕТ — оба через tmp+replace, окно рассинхрона
# сужается до мгновения между двумя replace; после краха между ними load_index
# упадёт на assert — при старте бота проверять выравнивание и, если беда, честно
# падать с понятным сообщением (лучше, чем молча отвечать мусором).

# CRITICAL: удаление — two-strike: первый промах только помечает (_missing_strikes
# dict file_id→счётчик in-memory), удаление со второго ПОДРЯД. Успешное появление
# файла сбрасывает счётчик. Транзиентный сбой листинга (exception) — папка
# пропускается целиком, счётчики НЕ инкрементятся.

# CRITICAL: legacy 842 чанка БЕЗ doc_name/folder_id — remove_document их никогда
# не матчит (только равенство обоих полей). Отдельно НЕ трогать.

# CRITICAL: после любой мутации индекса — chunks, embeddings = load_index() в
# bitrix_bot (globals), как в существующем ингесте.

# GOTCHA: рекурсия — подпапка, которая САМА есть в roles.json.folders, пропускается
# (её обойдёт главный цикл _monitored_folders со СВОИМИ ролями). Иначе двойной обход.

# GOTCHA: processed_files сидирован из courses.doc_id с folder_id NULL (legacy).
# Выборка «processed по папкам» (для удаления) обязана игнорировать NULL folder_id —
# иначе первый же синк посчитает legacy-доки «удалёнными».

# GOTCHA: дедуп курсов по doc_name (№1, шаг 6.5) остаётся для КОПИЙ в других папках.
# Замена в ТОЙ ЖЕ папке — другой путь: чанки заменить, курс сохранить (правки HR
# из №3 живут в questions_json — НЕ перегенерировать вопросы), archived_at → NULL.

# GOTCHA: архивировать курс при удалении файла ТОЛЬКО если не осталось других
# processed_files-строк с тем же doc_name (копия в другой папке держит курс живым).

# GOTCHA: pptx: слайд без текста/титула — heading = f"Слайд {n}"; пустые слайды
# пропускать. pdf: extract_text() может вернуть "" (скан) — пропуск страницы;
# весь документ пустой → лог + return (как сейчас «No chunks»).

# GOTCHA: txt-чанкер: 400–500 слов (конвенция MVP из сметы), heading = имя файла,
# section = имя файла (как _parse_md ставит section="Общее" — для txt лучше имя файла,
# однородно с normalised-фоллбэком).

# GOTCHA: _embed в process_new_document — вложенная функция; поднять на уровень
# модуля (_embed_texts) БЕЗ изменения логики (по одному тексту, t[:2000]) — иначе
# не замокать в тестах.

# GOTCHA: файл в чат — методы без детальной доки в выжимке. Порядок попыток:
# im.dialog.get(DIALOG_ID="u123") → chat_id; im.disk.file.commit(CHAT_ID=chat_id,
# UPLOAD_ID=<disk file id>). Не взлетело → попробовать DISK_ID вместо UPLOAD_ID →
# im.v2.File.upload. ВСЁ в try/except с print — ссылка в _start_reading уже есть,
# фейл отправки файла НЕ должен ломать старт курса. Может понадобиться CLIENT_ID
# бота (как в _send) — проверить живьём.

# GOTCHA: pytest-asyncio НЕТ — async-код в тестах через asyncio.run(...).
# Моки httpx: monkeypatch.setattr(bot.httpx, "AsyncClient", FakeAsyncClient).

# GOTCHA: requirements.txt: python-pptx>=1.0.0, pypdf>=4.0.0. Ставить в venv
# проекта (~/.pyenv/versions/vertical_standards_env). pypdf там УЖЕ стоит (6.14.2,
# ставился для чтения референс-PDF) — в requirements всё равно зафиксировать.
```

## Implementation Blueprint

### Data models and structure

```python
# Чанк пайплайна (normalised) ПОСЛЕ №4:
{"text": "...", "heading": "...", "section": "...",
 "roles": ["housekeeper"], "audience": "staff",
 "doc_name": "Стандарт уборки.docx", "folder_id": "111"}   # НОВОЕ

# SQLite миграции (init_db, _ensure_column):
# courses        + archived_at TEXT          (архив при удалении файла)
# processed_files + update_time TEXT         (детект новой версии при том же file_id)

# app/index_store.py API (все sync, под threading.Lock):
load() -> (chunks, emb)                        # с assert выравнивания
append_document(new_chunks, new_emb) -> int    # всего чанков после
remove_document(doc_name, folder_id) -> int    # сколько удалено (0 = не было)
has_document(doc_name, folder_id) -> bool
# приватно: _atomic_save(chunks, emb) — tmp + os.replace для ОБОИХ файлов

# In-memory синка удалений (bitrix_bot):
_missing_strikes: dict[str, int] = {}          # file_id → подряд-промахи
```

### List of tasks (в порядке выполнения)

```yaml
Task 1 — MODIFY requirements.txt:
  - + python-pptx>=1.0.0, pypdf>=4.0.0; pip install в venv проекта

Task 2 — CREATE app/doc_parsers.py:
  - SUPPORTED_EXTS = ("docx", "md", "pptx", "txt", "pdf")
  - parse_md(path) — ПЕРЕНОС _parse_md из bitrix_bot (тело без изменений)
  - parse_txt(path, file_name): слова → чанки по ~450 слов (границы 400–500),
      heading = section = file_name
  - parse_pptx(path, file_name): чанк на слайд с текстом; heading = титул слайда
      или f"Слайд {n}"; section = file_name; пропуск пустых
  - parse_pdf(path, file_name): чанк на страницу с текстом (extract_text() или "");
      heading = f"Стр. {n}"; section = file_name; пропуск пустых
  - parse_file(path, ext, file_name) -> list[dict]: диспетчер; docx — sys.path-хак
      (перенос из process_new_document) + from parse_standards import parse_docx
  - bitrix_bot: удалить _parse_md, импортировать doc_parsers

Task 3 — CREATE app/index_store.py:
  - пути: from app.rag import CHUNKS_PATH, EMBEDDINGS_PATH
  - _lock = threading.Lock(); _atomic_save: json→tmp→os.replace, np.save(tmp)→os.replace
  - load(): открытая читалка с assert len==shape[0] (для стартовой проверки тоже)
  - append_document / remove_document / has_document по маске
      c.get("doc_name") == doc_name and str(c.get("folder_id")) == str(folder_id)
  - remove: keep_idx-механика из dedup_index.py

Task 4 — MODIFY app/db.py:
  - init_db: _ensure_column(courses, archived_at TEXT);
             _ensure_column(processed_files, update_time TEXT)
  - get_active_courses: WHERE approved_at IS NOT NULL AND archived_at IS NULL
  - NEW: set_course_archived(course_id, archived: bool)
         get_processed_by_folders(folder_ids: list[str]) -> list[dict]
             # WHERE folder_id IN (...) — NULL folder_id (legacy) в выборку НЕ попадает
         get_processed_file(file_id) -> dict | None      # для сравнения update_time
         remove_processed_file(file_id)
         count_processed_by_doc_name(doc_name) -> int    # живые копии дока
  - mark_file_processed(+ update_time: str = None) — расширить сигнатуру (deфолт None,
      существующие вызовы/тесты не ломать)

Task 5 — MODIFY app/bitrix_bot.py — рекурсия и синк:
  - _list_children(client, folder_id, type_) -> list[dict]:
      POST getchildren {"id": folder_id, "filter": {"TYPE": type_}}  # file|folder
  - _walk_folder(client, folder_id, roles, depth, seen_files, visited_folders):
      visited_folders.add(str(folder_id))
      files = await _list_children(client, folder_id, "file")
      на первом полле: print(files[0].keys()) — подтвердить имя поля UPDATE_TIME
      for f: seen_files[str(f["ID"])] = f   # весь объект (нужен UPDATE_TIME)
             → гейт/обработка как в _check_folder СЕЙЧАС + триггер (b):
               row = get_processed_file(file_id)
               if row and row.get("update_time") and f.get("UPDATE_TIME") and
                  row["update_time"] != f["UPDATE_TIME"]: → обработать как замену
                  (снять гейт: не skip, звать process_new_document — он сам удалит старые)
      if depth > 1:
          for sub in await _list_children(client, folder_id, "folder"):
              if str(sub["ID"]) in _monitored_folders(): continue   # своя роль — свой обход
              await _walk_folder(..., depth-1, ...)
  - _sync_folder(root_id, roles):   # заменяет _check_folder в полл-цикле
      try: walk → seen_files, visited_folders
      except → print, return   # счётчики промахов НЕ трогаем
      processed = await asyncio.to_thread(get_processed_by_folders, sorted(visited_folders))
      for row in processed:
          fid = row["file_id"]
          if fid in seen_files: _missing_strikes.pop(fid, None); continue
          _missing_strikes[fid] = _missing_strikes.get(fid, 0) + 1
          if _missing_strikes[fid] < 2: continue          # two-strike
          → await _delete_document(row)
  - _delete_document(row):
      removed = await asyncio.to_thread(index_store.remove_document,
                                        row["doc_name"], row["folder_id"])
      await asyncio.to_thread(remove_processed_file, row["file_id"])
      _missing_strikes.pop(row["file_id"], None)
      if await asyncio.to_thread(count_processed_by_doc_name, row["doc_name"]) == 0:
          course = await asyncio.to_thread(get_course_by_doc_name, row["doc_name"])
          if course: await asyncio.to_thread(set_course_archived, course["id"], True)
      global chunks, embeddings; chunks, embeddings = load_index()
      print(f"[sync] deleted {row['doc_name']} (folder {row['folder_id']}): -{removed} chunks")
      уведомить HR: "🗑 Документ «{doc_name}» удалён из папки — {removed} фрагментов
      убрано из поиска" (+ ", курс архивирован" если архивировали)

Task 6 — MODIFY app/bitrix_bot.py — process_new_document:
  - ext-фильтр → doc_parsers.SUPPORTED_EXTS (оба места: walk и process_new_document)
  - парсинг → doc_parsers.parse_file(tmp_path, ext, file_name)
  - _embed → модульная _embed_texts(texts) (логика 1-в-1)
  - normalised: + "doc_name": file_name, "folder_id": str(folder_id or "")
  - шаг 6 (ручное чтение-дописывание файлов) → index_store:
      is_replacement = index_store.has_document(file_name, folder_id)
      if is_replacement:
          removed = index_store.remove_document(file_name, folder_id)
          print(f"[process_new_document] replacing: -{removed} old chunks")
      index_store.append_document(normalised, new_emb)
      chunks, embeddings = load_index()
  - mark_file_processed(..., update_time=meta.get("UPDATE_TIME")) — meta уже есть
      из disk.file.get; при замене (a) старую строку processed_files этого
      doc_name+folder (другой file_id) удалить (get_processed_by_folders + match)
  - шаг 6.5 дедуп: duplicate = get_course_by_doc_name(file_name)
      if duplicate:
          if duplicate.get("archived_at"): set_course_archived(duplicate["id"], False)
          if is_replacement: notify HR "🔄 Документ «X» обновлён: {n} фрагментов
              (было {removed}). Вопросы курса сохранены."
          mark_file_processed(...); return   # как сейчас — без нового курса
  - дальше (новый документ) — без изменений

Task 7 — MODIFY app/bitrix_bot.py — файл в чат при старте курса:
  - async _send_course_file(dialog_id: str, course_id: int):
      course = get_course_by_id; doc_id = course.get("doc_id"); if not doc_id: return
      try:
          im.dialog.get {"DIALOG_ID": dialog_id} → chat_id = result["ID"]? (проверить ключ)
          im.disk.file.commit {"CHAT_ID": chat_id, "UPLOAD_ID": int(doc_id)}
          не 200/ошибка в теле → повтор с {"DISK_ID": ...} → im.v2.File.upload
      except Exception as exc: print(f"[course-file] fallback to link: {exc!r}")
  - хук 1 ("/"): had_session = bool(await asyncio.to_thread(get_session, user_id))
      ДО process_message; после: if not had_session and (s := get_session(user_id)):
      asyncio.create_task(_send_course_file(dialog_id, s["course_id"]))
  - хук 2 («Пригласить», после успешного start_onboarding):
      s = await asyncio.to_thread(get_session, uid)
      if s: asyncio.create_task(_send_course_file(f"u{uid}", s["course_id"]))

Task 8 — TESTS:
  - tests/test_doc_parsers.py:
      txt: 1000 слов → чанки 400–500 слов, heading=имя файла
      pptx: собрать Presentation в tmp_path (python-pptx), 2 слайда с текстом +
            1 пустой → 2 чанка, heading из титула
      pdf: pypdf.PdfWriter().add_blank_page() → parse_pdf → [] и не падает
      md: перенесённый parse_md — split по заголовкам (закрепить поведение)
      parse_file: неизвестное расширение → [] или ValueError (выбрать: [] + лог)
  - tests/test_index_store.py (tmp-пути через monkeypatch app.rag.CHUNKS_PATH/
    EMBEDDINGS_PATH И app.index_store-ссылок — импортировать пути в index_store
    как module-attrs, monkeypatch там):
      append → длины сходятся; remove по (doc_name, folder_id) убирает только своё;
      legacy-чанки без полей не тронуты; remove несуществующего → 0;
      после операций файлы валидны (перечитать)
  - tests/test_folder_sync.py:
      _walk_folder с fake _list_children (дерево: root→sub→subsub, файлы на каждом
      уровне; role-mapped подпапка) — роли корня, скип замапленной, depth-лимит
      _sync_folder: файл пропал → 1-й полл strike (чанки живы) → 2-й полл удалены,
        processed снят, курс архивирован; копия в другой папке → курс ЖИВ
      исключение листинга → strikes не растут
      замена (a): process_new_document (asyncio.run) с моками httpx/_embed_texts/
        generate_questions: v1 (2 чанка) → v2 тем же именем+папкой, новый file_id
        (3 чанка) → в индексе только v2; курс один; questions_json НЕ перегенерён
      триггер (b): mark_file_processed с update_time=T1; в fake-листинге
        UPDATE_TIME=T2 → файл реингестится
      get_active_courses не отдаёт архивный курс; set_course_archived(False) вернул

Task 9 — MODIFY planning.md, task.md, .env.example:
  - planning.md: №4 ✅ код готов + заметки (UPDATE_TIME-триггер, two-strike,
    legacy-ограничение, файл-в-чат под живую проверку)
  - task.md: закрыть бэклог «Форматы файлов»/«Структура папок»/«RAG per-doc tracking»;
    добавить живые чеки №4
  - .env.example: без новых ключей (глубина 5 — константа MAX_FOLDER_DEPTH в коде)
```

### Per task pseudocode (ключевые места)

```python
# Task 3 — index_store: атомарная запись и удаление
def _atomic_save(chunks: list[dict], emb: "np.ndarray") -> None:
    assert len(chunks) == emb.shape[0], (len(chunks), emb.shape)
    tmp_json = CHUNKS_PATH + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False)
    tmp_npy = EMBEDDINGS_PATH + ".tmp.npy"     # np.save сам добавляет .npy к имени без него
    np.save(tmp_npy, emb)
    os.replace(tmp_npy, EMBEDDINGS_PATH)       # сначала npy,
    os.replace(tmp_json, CHUNKS_PATH)          # потом json — оба атомарны

def remove_document(doc_name: str, folder_id: str) -> int:
    with _lock:
        chunks, emb = load()
        keep = [i for i, c in enumerate(chunks)
                if not (c.get("doc_name") == doc_name
                        and str(c.get("folder_id")) == str(folder_id))]
        removed = len(chunks) - len(keep)
        if removed:
            _atomic_save([chunks[i] for i in keep], emb[keep])
        return removed

# Task 2 — txt-чанкер (400–500 слов)
def parse_txt(path: str, file_name: str) -> list[dict]:
    words = open(path, encoding="utf-8").read().split()
    out = []
    for i in range(0, len(words), 450):
        body = " ".join(words[i:i + 450])
        if len(body) > 50:
            out.append({"text": body, "heading": file_name, "section": file_name})
    return out
```

### Integration Points
```yaml
DATABASE:
  - миграции в init_db: courses.archived_at, processed_files.update_time (обе _ensure_column)
INDEX:
  - все мутации только через app/index_store.py (Lock + атомарная запись);
    ПЕРЕД живым деплоем — ручной бэкап chunks_cache.json/embeddings_cache.npy
ROUTES:
  - без новых; меняется поллер и process_new_document
DEPLOY:
  - pip install python-pptx pypdf; рестарт uvicorn; смотреть первый полл в логах
    (ключи файловых объектов — подтверждить UPDATE_TIME)
```

## Validation Loop

### Level 1: Syntax & Style
```bash
~/.pyenv/versions/vertical_standards_env/bin/pip install python-pptx pypdf -q
~/.pyenv/versions/vertical_standards_env/bin/python -m ruff check app/ scripts/ tests/ --fix
```

### Level 2: Unit Tests
```bash
~/.pyenv/versions/vertical_standards_env/bin/python -m pytest tests/ -v
# Существующие 60 зелёные + новые (парсеры, index_store, sync/replacement)
```

### Level 3: Офлайн-смоук index_store на КОПИИ живого индекса
```bash
~/.pyenv/versions/vertical_standards_env/bin/python - <<'EOF'
import shutil, json, numpy as np, os, tempfile
tmp = tempfile.mkdtemp()
shutil.copy("data/chunks_cache.json", f"{tmp}/chunks_cache.json")
shutil.copy("data/embeddings_cache.npy", f"{tmp}/embeddings_cache.npy")
import app.rag as rag, app.index_store as store
for m in (rag, store):
    m.CHUNKS_PATH = f"{tmp}/chunks_cache.json"; m.EMBEDDINGS_PATH = f"{tmp}/embeddings_cache.npy"
chunks, emb = store.load()
n0 = len(chunks)
store.append_document([{"text":"тест","heading":"т","section":"т",
    "roles":["all_staff"],"audience":"staff","doc_name":"смоук.txt","folder_id":"999"}],
    np.zeros((1, emb.shape[1]), dtype=emb.dtype))
assert store.has_document("смоук.txt", "999")
assert store.remove_document("смоук.txt", "999") == 1
chunks2, emb2 = store.load()
assert len(chunks2) == n0 == emb2.shape[0]
print("index_store smoke OK, legacy intact:", n0)
EOF
```

### Level 4: Живой прогон (сервер/ngrok, вместе со сдачей №2/№3)
```bash
# 1. Подпапка в ролевой папке + docx внутри → чанки с ролью корня (лог поллера)
# 2. Заливка .pptx и .txt → курсы сгенерированы; .pdf → чанки есть
# 3. Повторная заливка одноимённого docx (удалить+залить) → лог "replacing: -N",
#    вопросы курса прежние (проверить «Вопросы {N}» после правки №3)
# 4. Перезапись через веб-UI (новая версия, тот же file_id) → реингест по UPDATE_TIME
# 5. Удаление файла → на ВТОРОМ полле чанки исчезли, HR получил 🗑, курс архивный,
#    новый сотрудник: «Активных курсов пока нет»
# 6. Новый сотрудник стартует курс → в чат пришёл ФАЙЛ документа (или, при фейле
#    метода, — только ссылка, и в логе [course-file] fallback)
```

## Final validation Checklist
- [ ] pytest зелёный (60 старых + новые), ruff чистый
- [ ] Смоук index_store на копии живого индекса прошёл, legacy-число не изменилось
- [ ] Замена версии: оба триггера покрыты тестами (новый file_id; UPDATE_TIME)
- [ ] Удаление: two-strike, копия в другой папке держит курс, legacy NULL-folder не задет
- [ ] Файл в чат: фейл метода не ломает старт курса (try/except + лог)
- [ ] requirements.txt дополнен; тексты русские; planning/task обновлены

---

## Anti-Patterns to Avoid
- ❌ НЕ мутировать индекс мимо index_store (в т.ч. существующий append-код — перевести)
- ❌ НЕ удалять по одному промаху листинга — только two-strike
- ❌ НЕ перегенерировать вопросы при замене версии — правки HR (№3) дороже
- ❌ НЕ трогать legacy-чанки и processed_files с NULL folder_id
- ❌ НЕ делать .bak на каждый полл — атомарная запись + бэкап перед деплоем
- ❌ НЕ ронять старт курса из-за фейла отправки файла — ссылка уже есть
- ❌ НЕ добавлять pytest-asyncio — asyncio.run в sync-тестах

## Открытые допущения (проверить живьём)
1. Параметры im.disk.file.commit (CHAT_ID + UPLOAD_ID/DISK_ID) и ключ chat_id в
   ответе im.dialog.get — детальных секций в выжимке НЕТ; каскад фолбэков + ссылка.
2. Имя поля времени файла в getchildren (UPDATE_TIME) — лог первого полла.
3. Пустой результат getchildren при живой папке (транзиент с 200 OK) — two-strike
   защищает; если живьём встретится, поднять порог до 3.
4. PDF в тексте сметы клиенту отсутствует (есть в planning) — сделан как бонус,
   упомянуть при сдаче.

## Score: 7/10
Самая рискованная доработка пакета: мутации живого индекса (смягчено index_store +
Lock + атомарная запись + смоук на копии), два живых неизвестных (параметры файл-в-чат,
имя поля UPDATE_TIME) и рекурсия против реального Disk. Дизайн-риски закрыты
тестами и фолбэками, но два пункта подтверждаются только на сервере — потому 7.
