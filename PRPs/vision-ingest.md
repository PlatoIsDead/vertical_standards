name: "Vision-ингест: SmartArt-флоучарты + скриншоты с аннотациями, с автономной петлёй «реализация → самотест → оценка → следующий спринт»"
description: |
  Закрыть слепоту бота на визуальный контент двух боевых документов. Тревога №1
  юзера — СТРЕЛКИ, задающие алгоритм действий: просто «текст из картинки»
  недостаточно. Исполнитель обязан пройти петлю: написать фичу → сам прогнать
  eval с судьёй-по-оригиналу → оценить → при неудовлетворительных метриках сам
  спланировать и выполнить следующий спринт (≤3 спринтов).

## Purpose
Реализация поверх кода 20.08 (259 тестов). Race-research 21.08 ВЫПОЛНЕН —
исполнитель опирается на его факты, не перепроверяя с нуля.

## Research findings (живые, 21.08 — основа всей архитектуры)
- «FO, RES АЛГОРИТМ БРОНИРОВАНИЯ ПО ТЕЛЕФОНУ.docx» — НЕ картинка:
  **SmartArt-диаграмма Word** (`word/diagrams/data1.xml`, 70КБ): 108 dgm:pt
  (27 с текстом: «Входящий звонок в отель» → «Приветствие гостя» → … +
  ветвления «Если гостя интересует ваш отель» / «другой отель сети»),
  102 dgm:cxn связи со srcId/destId/srcOrd. СТРЕЛКИ ЛЕЖАТ В XML ЯВНО —
  восстанавливаются детерминированно, vision для этого файла НЕ НУЖЕН.
- «FO, RES, SAL Негарантированные бронирования.docx» — 9 PNG (61–790КБ),
  два типажа (просмотрены глазами): (а) скриншоты OPERA/«Вертикаль»-ПМС с
  РУКОПИСНЫМИ красными аннотациями — стрелки к колонкам («1»→«Код гарантии»,
  «2»→BALANCE), обводки значений, метки («ПС»→«Постоплата»), нумерация
  порядка шагов, путь по меню к «Отменить бронь»; (б) чистые скриншоты
  навигации («Отчёты → Не доставленные в АСУ брони»).
- Копии обоих docx: на сервере `/state/data/*.docx` (скачаны с Диска) и
  локально в scratchpad; для фикстур тестов SmartArt `data1.xml` взять из
  первого файла.
- Текущий docx-парсер: doc_parsers.parse_file → scripts/parse_standards.
  parse_docx (только параграфы; медиа игнорируется). Чанк = {text, heading,
  section(, code, roles, status)} — роли/doc_name навешивает
  process_new_document.

## НЕ в скоупе
- Интерливинг медиа-чанков точно по позиции в документе — кандидат Sprint 2,
  ЕСЛИ eval покажет проблемы контекста (документы короткие, MVP — медиа-чанки
  после текстовых).
- PDF/pptx-медиа — только docx (боевые файлы — docx).
- EMF/WMF: vision их не ест — скип с логом (в наших файлах только PNG).

---

## Goal

**A. app/media_ingest.py (новый модуль):**
- `docx_media_chunks(path, file_name) -> list[dict]` — медиа-чанки docx:
  SmartArt-схемы + изображения; формат чанка как у parse_docx
  ({text, heading, section}); пустой список при любом системном сбое (лог).
- `smartart_outline(data_xml: bytes) -> str` — детерминированная
  линеаризация: dgm:pt (только с текстом) + dgm:cxn parOf-связи,
  сортировка детей по srcOrd, DFS-обход с отступами «— »; корень — узел без
  родителя. Это ГАРАНТИРОВАННО точный порядок стрелок.
- `smartart_to_text(data_xml) -> str` — outline → gpt-5.5 переписывает в
  связный пошаговый алгоритм с ветвлениями («1. … 2. … Если …, то …»);
  сбой LLM → вернуть сырой outline (лучше сырой порядок, чем ничего).
- `describe_image(image_bytes, context: str) -> str` — gpt-5.5 vision
  (chat.completions, content-части text + image_url data:base64;
  max_completion_tokens=2000, БЕЗ temperature — конвенция gpt-5.5).
  Промпт (русский): «изображение — часть рабочей инструкции отеля
  ({context} = название документа); транскрибируй как фрагмент инструкции:
  какой экран/система, ключевые поля и значения; ОСОБО ВАЖНО рукописные и
  цветные аннотации — стрелки, рамки, обводки, цифры: к какому элементу
  указывает каждая, какой ПОРЯДОК ДЕЙСТВИЙ задают; изложи как нумерованные
  шаги. Не выдумывай того, чего нет.»
- Кэш: data/vision_cache.json {sha256(bytes): text} — реингест/копии не
  платят повторно; атомарная запись (tmp+replace, паттерн save_roles_config).
- Извлечение: zipfile — `word/media/*` (embed-картинки; PNG/JPEG only) и
  `word/diagrams/data*.xml`; ≥3МБ на картинку — скип с логом.

**B. Интеграция:** doc_parsers.parse_file (ветка docx): parse_docx(path) +
media_ingest.docx_media_chunks(path, file_name). Тексты медиа-чанков:
префикс «[Схема] …» / «[Скриншот N] …», heading = «Схема: {display_name}» /
«Скриншот {N}». Никаких изменений в process_new_document (роли/эмбеддинг
навешиваются как обычным чанкам).

**C. Петля оценки (scripts/eval_vision.py) — сердце PRP:**
- Вход: --files "путь1;путь2" (по умолчанию оба боевых из /state/data),
  --questions K (деф. 4), --date.
- Шаг 1: свежий parse_file обоих файлов (текст+медиа) → эмбеддинг чанков
  (bitrix_bot._embed_texts / OpenAI embeddings напрямую) → мини-индекс
  в памяти.
- Шаг 2 (генератор, НЕЗАВИСИМ от ингеста): для КАЖДОГО медиа-объекта модель
  видит ОРИГИНАЛ (картинку / SmartArt-outline) и генерирует K вопросов
  строго о ПОРЯДКЕ и НАПРАВЛЕНИИ: «что идёт после …», «в каком случае …»,
  «на какую колонку указывает стрелка с цифрой 2», «какой пункт меню выбрать
  чтобы отменить бронь».
- Шаг 3: каждый вопрос → rag.answer по мини-индексу (role_filter роли файла).
- Шаг 4 (судья, видит ОРИГИНАЛ + вопрос + ответ бота): вердикт
  correct/partial/wrong + причина одним предложением (JSON).
- Отчёт markdown: по медиа-объектам, метрики: %correct, число wrong;
  сводка. Файл data/eval_vision_{date}.md.
- **Порог приёмки: ≥80% correct И 0 wrong.** Ниже — спринт-цикл (ниже).

**D. Протокол спринтов (исполнитель ОБЯЗАН выполнить, это часть PRP):**
1. Sprint 1 = A+B+C + юнит-тесты + живой прогон eval_vision НА СЕРВЕРЕ
   (docx уже в /state/data, ключ и канал там).
2. Метрики ниже порога → исполнитель сам: категоризирует фейлы по отчёту
   ((а) описание неполно/неточно → промпт/двухпроходное описание;
   (б) ретривер не достаёт нужный чанк → заголовки/разбиение длинных
   описаний/интерливинг; (в) судья несправедлив → калибровка судьи с
   примерами) → пишет план в `PRPs/vision-ingest-sprints.md` (спринт N:
   диагноз, гипотеза, изменения) → реализует → зелёные тесты → повторный
   живой eval. МАКСИМУМ 3 спринта; после третьего — честный отчёт юзеру
   с достигнутым % и остаточными пробелами.
3. Каждый спринт — отдельный коммит с номером спринта в сообщении.
4. Финал при достижении порога: деплой; реингест двух боевых файлов
   (scripts/reingest.py: снять их строки processed_files + index_store.
   remove_document по (doc_name, folder) → поллер переингестит за цикл);
   финальный контрольный вопрос живому боту про стрелку «2» → BALANCE.

## What / Success Criteria
- [ ] smartart_outline на РЕАЛЬНОМ data1.xml (фикстура tests/fixtures/
      smartart_data1.xml из боевого файла): «Входящий звонок в отель» раньше
      «Приветствие гостя»; ветка «другой отель сети» присутствует; все 27
      текстов узлов в выводе
- [ ] describe_image мокается; кэш: два вызова с теми же байтами → один
      LLM-вызов; кэш переживает процесс (файл)
- [ ] parse_file для docx с медиа возвращает текст+медиа-чанки; docx без
      медиа — как раньше (регресс test_folder_sync/test_doc_parsers)
- [ ] eval_vision на моках выдаёт корректный markdown и метрики; порог-логика
      (ниже 80% / есть wrong) отражена в exit-сводке
- [ ] ЖИВОЙ eval: отчёт с метриками в data/, вердикт против порога, спринты
      по протоколу D до порога или 3-го спринта
- [ ] `python -m pytest tests/ -v` (259+новые), `ruff check .` чистые
- [ ] task.md: хвост (реингест боевых, контрольный вопрос, синхронизация
      vision_cache в volume)

## All Needed Context

### Documentation & References
```yaml
- файлы исследования 21.08 (у исполнителя в scratchpad/docs/):
  m1/word/diagrams/data1.xml — боевой SmartArt (108 pt / 102 cxn / 27 текстов);
  m2/word/media/image1..9.png — боевые скриншоты. Фикстуры копировать отсюда.

- file: app/course_generator.py
  why: _llm_json (ретраи, json_object, max_completion_tokens, БЕЗ temperature)
       — переиспользовать для smartart_to_text/eval-судьи; конвенции промптов
       (запрет выдумывания). Vision-вызов ОТЛИЧАЕТСЯ: content-ЧАСТИ
       [{"type":"text","text":...},{"type":"image_url","image_url":
       {"url":"data:image/png;base64,..."}}] — response_format json_object
       для судьи/генератора, для describe_image — свободный текст (без
       response_format).

- file: app/doc_parsers.py
  why: диспетчер parse_file (ленивая загрузка), точка интеграции B; паттерн
       «сбой формата → [] и лог, бот не падает».

- file: scripts/parse_standards.py::parse_docx
  why: схема чанка {text, heading, section(, code, roles, status)} — медиа-
       чанки минимально {text, heading, section}; process_new_document
       нормализует остальное (bitrix_bot _ingest_document, normalised-блок).

- file: app/roles.py::save_roles_config
  why: паттерн атомарной записи json — vision_cache так же.

- file: scripts/eval_qa.py
  why: скелет eval-скрипта (argparse --date, markdown-отчёт, docker exec
       запуск, «слепые» документы). eval_vision — родственник, но с судьёй
       по ОРИГИНАЛУ и своим мини-индексом (не боевым).

- file: app/rag.py::answer + app/bitrix_bot.py::_embed_texts
  why: мини-индекс петли: chunks+np.array эмбеддингов; role_mask требует
       roles/audience у чанков — навесить как _ingest_document (roles из
       parse_filename, audience="staff").

- file: tests/test_folder_sync.py, tests/test_course_generator.py
  why: харнессы (fake httpx, очередь LLM-моков); фикстуры docx НЕ собирать
       живыми Document() в тестах медиа — использовать zipfile-фикстуру
       data1.xml и байтовые PNG-заглушки (describe_image мокается).

- url: https://platform.openai.com/docs/guides/images-vision
  why: формат image_url/data-URI в chat.completions; лимиты размеров.

- url: https://learn.microsoft.com/en-us/openspecs/office_standards/ms-odrawxml/
  why: (справочно) DrawingML диаграммы: dgm:ptLst/dgm:cxnLst; типы связей —
       парсить cxn БЕЗ type или type="parOf" как иерархию, узлы типа
       parTrans/sibTrans (без текста) — транзитные, пропускать.
```

### Known Gotchas
```python
# CRITICAL: SmartArt cxn ссылаются и на ТРАНЗИТНЫЕ узлы (parTrans/sibTrans
# без текста) — 108 pt при 27 текстах. Иерархию строить по cxn type="parOf"
# (в data.xml обычно ЕСТЬ явный type) с сортировкой по srcOrd; узлы без
# текста в выводе пропускать, детей поднимать к ближайшему текстовому предку.
# Валидация НА РЕАЛЬНОЙ фикстуре — единственный надёжный тест.

# CRITICAL: судья и генератор eval видят ОРИГИНАЛ (картинку/outline), а НЕ
# продукт ингеста — иначе оценка циклическая и всегда «отлично».

# CRITICAL: describe_image БЕЗ response_format (свободный текст); судья и
# генератор — С json_object. Всё на gpt-5.5-конвенциях (max_completion_tokens,
# без temperature) — «max_tokens» уже дважды ронял прод.

# GOTCHA: сеть в парсере: parse_file зовётся в to_thread — sync httpx/openai
# ок; но тесты parse-стека обязаны мокать describe_image/smartart_to_text
# (test_folder_sync гоняет parse_file с фейковым httpx БЕЗ OpenAI!) —
# ленивые импорты и módуль-уровневые функции, monkeypatch-able из тестов:
# в тестах folder_sync докинуть monkeypatch app.media_ingest.describe_image?
# НЕТ — тестовые .txt файлы медиа не имеют; docx-фикстур в folder_sync нет
# (V1_TEXT — txt). Регресс не затронут. Новые тесты — свои фикстуры.

# GOTCHA: vision_cache.json живёт в data/ → на сервере это volume state/data
# (переживает деплой), в тестах — monkeypatch пути кэша (модульная константа
# CACHE_PATH, паттерн roles.CONFIG_PATH).

# GOTCHA: PNG >3МБ и не-PNG/JPEG — скип с логом (EMF/WMF vision не ест).
# base64 раздувает ×1.33 — наши ≤790КБ ок.

# GOTCHA: SmartArt-текст режется на чанки, если длиннее ~500 слов? НЕТ:
# алгоритм целостен — ОДИН чанк на схему (порядок шагов нельзя рвать);
# наш «АЛГОРИТМ» ~27 узлов — заведомо влезает в чанк и в контекст RAG.

# GOTCHA: eval_vision мини-индекс НЕ трогает боевые data/ файлы — всё в
# памяти; на сервере запускать в контейнере (файлы в /state/data =
# /app/data, ключ в .env, канал стабилен).

# GOTCHA: sha256-кэш описаний: ключ — байты картинки; для SmartArt — байты
# data.xml. Инвалидация не нужна (другая картинка = другой хеш); смена
# ПРОМПТА в новом спринте требует ручного сброса кэша — отметить в
# PRPs/vision-ingest-sprints.md при каждом изменении промпта!
```

## Implementation Blueprint

### Список задач (Sprint 1)

```yaml
Task 1 — фикстуры:
  CREATE tests/fixtures/smartart_data1.xml  (копия боевого data1.xml)
  CREATE tests/fixtures/dot.png             (валидный 1×1 PNG, байты в тесте)

Task 2 — app/media_ingest.py:
  - CACHE_PATH (data/vision_cache.json), _load_cache/_save_cache (атомарно)
  - _extract_docx_media(path) -> (list[bytes] images, list[bytes] diagrams):
    zipfile: word/media/* (расширение .png/.jpg/.jpeg, ≤3МБ), сортировка по
    имени (image1..N = порядок вставки); word/diagrams/data*.xml
  - smartart_outline(xml_bytes) -> str: ElementTree + namespaces
    {'dgm': 'http://schemas.openxmlformats.org/drawingml/2006/diagram',
     'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'};
    pt: modelId → собранный текст (join a:t), пропуск пустых; cxn parOf →
    children[src].append((srcOrd, dest)); корни = текстовые узлы без
    текстового предка; DFS: «— » * depth + text
  - smartart_to_text(xml_bytes, doc_context) -> str: outline → _llm_json-
    подобный вызов (СВОЙ, без response_format — текст) «перепиши схему
    связным алгоритмом, сохрани порядок и ветвления, ничего не добавляй»;
    Exception → outline as-is (print)
  - describe_image(image_bytes, doc_context) -> str: кэш-гейт → vision-вызов
    (data:URI, промпт из Goal A) → кэш-запись
  - docx_media_chunks(path, file_name) -> list[dict]: схемы → чанки
    «[Схема] {text}» (heading «Схема: {display_name(file_name)}»),
    картинки → «[Скриншот {i}] {text}»; любой сбой на элементе — лог и
    продолжение (частичный результат лучше пустого)

Task 3 — интеграция doc_parsers:
  parse_file/docx: chunks = parse_docx(path); chunks += docx_media_chunks(
  path, file_name) (ленивый импорт app.media_ingest)

Task 4 — scripts/eval_vision.py (по Goal C):
  - сборка мини-индекса: parse_file → навесить roles (parse_filename)/
    audience → эмбеддинг (openai embeddings, паттерн _embed_texts) →
    rag.answer(chunks, np.array)
  - генератор/судья: vision-вызовы с json_object; судья возвращает
    {"verdict": "correct|partial|wrong", "reason": "..."}
  - отчёт + сводка метрик + строка «ПОРОГ ПРОЙДЕН/НЕ ПРОЙДЕН (≥80% и 0 wrong)»

Task 5 — тесты (tests/test_media_ingest.py):
  - smartart_outline на реальной фикстуре: порядок, ветка, все 27 текстов
  - describe_image: кэш-поведение (мок vision-вызова со счётчиком; второй
    вызов не дёргает LLM; кэш-файл в tmp_path через monkeypatch CACHE_PATH)
  - docx_media_chunks: собрать МИНИ-docx zipfile-ом в tmp (word/media/x.png
    + word/diagrams/data1.xml + минимальный [Content_Types]/document.xml не
    нужен — экстрактор читает только media/diagrams) с мокнутыми
    smartart_to_text/describe_image → чанки с префиксами; EMF-файл — скип
  - eval_vision: моки LLM/rag → markdown с метриками и порогом
  - parse_file регресс: docx-ветка с мокнутым docx_media_chunks

Task 6 — scripts/reingest.py:
  - argparse file_id... или --doc "имя": снять processed_files строки,
    index_store.remove_document(doc_name, folder) для всех папок строк;
    печать «поллер переингестит в течение POLL_INTERVAL»

Task 7 — живой Sprint-цикл (протокол D): прогон на сервере, метрики,
  при необходимости спринты 2–3 с планом в PRPs/vision-ingest-sprints.md

Task 8 — task.md + деплой + реингест двух боевых + контрольный вопрос боту
```

## Validation Loop
```bash
ruff check . --fix
python -m pytest tests/ -v
python scripts/eval_vision.py --help          # локальный smoke CLI
# живой цикл (сервер):
#   docker exec vertical-standards-bot python scripts/eval_vision.py --date YYYYMMDD
```

## Final validation Checklist
- [ ] pytest/ruff зелёные; фикстура data1.xml в репо
- [ ] Живой eval-отчёт в data/, метрики против порога зафиксированы
- [ ] Спринты (если были) задокументированы в PRPs/vision-ingest-sprints.md
- [ ] Боевые файлы переингещены, «что показывает стрелка 2?» отвечает боту
- [ ] vision_cache.json на сервере в volume

## Anti-Patterns to Avoid
- ❌ Не решать SmartArt vision-ом — стрелки в XML точнее любой модели
- ❌ Не давать судье/генератору продукт ингеста — только оригинал
- ❌ Не рвать алгоритм схемы на несколько чанков
- ❌ Не оставлять max_tokens/temperature в новых LLM-вызовах
- ❌ Не гонять живой eval из WSL — сервер (файлы, ключ, канал)
- ❌ Не объявлять фичу готовой без прохождения порога или честного отчёта
  после 3-го спринта

## Confidence Score: 7/10
Код-часть хорошо обоснована (реальные фикстуры, известные конвенции LLM-
вызовов, готовые харнессы) — по ней уверенность 8+. Балл снят за
принципиально итеративную часть: качество vision-описаний аннотированных
скриншотов заранее не гарантируется — но именно для этого в PRP встроена
петля с независимым судьёй и протоколом спринтов; второй снятый балл —
эвристика parOf/srcOrd на нестандартных SmartArt (митигация: тест на
единственной реально существующей боевой схеме).
