name: "Качество вопросов и удобная правка: промпты+критик+shuffle, карточный просмотр, мастер правки, перегенерация, дедуп курсов"
description: |
  Фидбек юзера 17.08 по живому дампу боевой БД: (1) «Вопросы N» показывает
  простыню-ответник вместо теста, (2) правка = one-shot всей карточки,
  (3) 4 курса-дубля одного документа + полые вопросы («Что описывает документ?»,
  correct=A у всех). Диагнозы ПОДТВЕРЖДЕНЫ живыми данными (см. Research ниже).
  Решения юзера: модель gpt-5.5 (да), hash-дубли = авто-привязка без диалога (да).

## Purpose
Одно-проходная реализация поверх кода после «кнопки везде»+FSM-редизайна 17.08
(221 тест зелёный). Три блока: генерация, HR-UX (карточки/визард/перегенерация),
дедуп. Всё уже за флагом BUTTONS_ENABLED там, где клавиатуры; текстовые команды
работают и без кнопок.

## Core Principles
1-5 как всегда (CLAUDE.md): русские UI-тексты, простое решение, паттерны репо.

## Research findings (живые данные с боевого сервера, 17.08)
- Модель на сервере: `OPENAI_MODEL=gpt-4o-mini` (.env) — главный рычаг качества.
- Вопросы курса 17 («АЛГОРИТМ БРОНИРОВАНИЯ»): «Какой документ описывает…»,
  «Кто является целевой аудиторией…» — мета-вопросы, НЕ содержание; у всех
  проверенных вопросов correct=A (пример в промпте показывает "correct": "A",
  модель копирует) — экзамен сдаётся кнопкой A.
- Дубли: «ALL КОНФЕДЕНЦИАЛЬНОСТЬ» = курсы 20/21/22/24, созданы 14:08:36–40
  29.07 из 4 ФИЗИЧЕСКИХ копий в разных ролевых папках, обработанных поллером
  ПАРАЛЛЕЛЬНО — каждая проверила get_course_by_doc_name до того, как первая
  сохранилась. Та же гонка: 17/25, 19/23, 16/18. Дедуп по имени ЕСТЬ
  (bitrix_bot.process_new_document, шаг 6.5) — он проигрывает гонку.
  Второй пробел: тот же файл под другим именем создаст курс (нет content-hash).

## НЕ в скоупе (осознанные резы)
- UNIQUE-индекс по courses.doc_name: гонка происходит в ОДНОМ процессе —
  per-doc asyncio.Lock решает её полностью; индекс при живых архивных дублях
  в проде — лишние грабли.
- Диалог «обновить или отменить?» при hash-дубле — решение юзера: авто-привязка
  + уведомление HR, без нового диалогового состояния.
- Кнопка [▶️ Следующий вопрос] внутри confirm-шага визарда (мок юзера):
  после [💾 Сохранить] показывается КАРТОЧКА вопроса с её штатной [▶️ Далее] —
  композиция чище, отдельная кнопка в визарде дублировала бы её.
- Пересчёт/перегенерация существующих боевых курсов кодом — руками через
  новую команду «Перегенерировать N» (деплой-хвост).

---

## Goal

**Генерация (course_generator.py):**
- промпты запрещают мета-вопросы и требуют конкретики (шаг, срок, порог,
  роль, действие); дистракторы = правдоподобные ошибки исполнения из того же
  документа; explanation ссылается на содержание, не на букву
- буква правильного ответа перемешивается КОДОМ (модели велено класть верный
  в A — код перемешивает и переприсваивает correct)
- этап-критик: LLM бракует полые вопросы → один ремонтный раунд (best-effort)
- контекст = весь документ (кап 120к символов), а не первые 15 чанков
- facts_basic/facts_exam сохраняются в questions_json (топливо перегенерации)
- совместимость с gpt-5.5: `max_completion_tokens` вместо `max_tokens`,
  `temperature` убрать вовсе

**HR-UX:**
- «Вопросы N» → КАРТОЧКА вопроса 1 (как видит сотрудник + ✅ на верном);
  «Вопросы N.q» → карточка q; «Вопросы N все» → прежняя простыня.
  Кнопки карточки: [⬅️ Назад][▶️ Далее / ✅ Подтвердить N на последней],
  [✏️ Изменить N.q][🔄 Заново N.q], [📄 Все вопросы]
- «Изменить N.q» (и старый «N q») → пошаговый визард: текст → A → B → C → D →
  правильная буква; «.» = оставить текущее; превью + [💾 Сохранить][🔄 Заново
  с шага 1][Отмена]; на шаге текста присланный ЦЕЛЬНЫЙ блок по старому шаблону
  (parse_replacement распознал) → сразу превью (power-user путь)
- сохранение валидирует: 4 непустых варианта, нет дубликатов вариантов,
  correct ∈ A–D (validate_question расширяется — гейт общий с генератором)
- «Перегенерировать N.q» / «Перегенерировать N» — фоновая задача с «⏳ Генерирую…»
  и итоговым сообщением; чанки документа — из глобального индекса по doc_name

**Дедуп:**
- per-doc_name asyncio.Lock вокруг «проверка → генерация → сохранение» в
  process_new_document + повторная проверка get_course_by_doc_name под локом
- sha256(file_bytes) в processed_files.content_hash; совпадение хеша при другом
  имени → чанки ингестим (роли), курс НЕ создаём, HR-уведомление «то же
  содержимое, что у курса «X» (№N)»
- scripts/dedup_courses.py: dry-run план + --apply (архив дублей, keep = курс
  с сессиями, иначе min id)

## What / Success Criteria
- [ ] Генерация: во всех вопросах нет мета-паттернов; буквы correct
      распределены (unit: shuffle сохраняет текст верного ответа, меняет буквы)
- [ ] validate_question режет дубли вариантов и пустые тексты (и в правке HR)
- [ ] Критик: бракованный вопрос перегенерируется один раз; сбой критика не
      роняет генерацию (best-effort, лог)
- [ ] facts_* в questions_json нового курса; старый курс без facts —
      перегенерация одного вопроса работает через фолбэк «по документу»
- [ ] «Вопросы N» = карточка 1 с ✅ и кнопками; «N.q» листает; «N все» —
      простыня (прежний формат, тест «→ B. Звонить 112» переезжает сюда)
- [ ] Визард: happy-path, «.»-пропуски, отмена, перехват командой, one-shot
      блок, ошибка валидации возвращает в превью с текстом причины
- [ ] «Перегенерировать N.q»: вопрос заменён, «⏳» пришло раньше результата;
      «Перегенерировать N»: весь сет заменён + предупреждение о правках;
      чанков нет в индексе → честный отказ
- [ ] Гонка: два параллельных process_new_document одного doc_name (мок LLM с
      задержкой) → ОДИН курс; hash-дубль → курс не создан, HR уведомлён
- [ ] `python -m pytest tests/ -v` зелёный (221 + новые; обновлённые старые —
      только санкционированные, список в Tasks), `ruff check .` чистый

## All Needed Context

### Documentation & References
```yaml
- file: app/course_generator.py
  why: |
    ВЕСЬ генератор, 165 строк: FACTS_PROMPT/QUESTIONS_FROM_FACTS_PROMPT
    (переписать), _llm_json (2 попытки, response_format json_object — паттерн
    сохранить; max_tokens→max_completion_tokens, temperature УБРАТЬ),
    generate_questions (двухэтапность сохранить, добавить этап-критик и
    shuffle), validate_question (расширить гейтами — им пользуется и hr_tools).
    context строится из chunks[:15] — убрать срез, кап 120_000 символов.

- file: app/bitrix_bot.py
  why: |
    process_new_document (~1230): file_bytes уже в руках (шаг 2 download) —
    там sha256; дедуп-проверка шага 6.5 (get_course_by_doc_name) — обернуть
    вместе с генерацией и сохранением в per-doc lock. new_chunks[:20] в
    generate_questions — передавать целиком. _handle_hr_message: ветки
    «вопросы» (~1000) и «изменить» (~1030) переписываются; _pending_edits +
    _get_pending (TTL 600) — База визарда; _HR_COMMAND_PREFIXES — добавить
    «перегенерировать» (перехват важнее забытой правки). Фоновые LLM-задачи —
    паттерн _build_and_send_report (fire-and-forget, best-effort, лог).
    Глобальный chunks (RAG) — источник фрагментов для перегенерации:
    [c for c in chunks if c.get("doc_name") == course["doc_name"]].

- file: app/hr_tools.py
  why: |
    resolve_question_ref/ui_number/question_by_ref/correct_option — сквозная
    нумерация 1–15 (1–5 базовые). format_question_full — «полная карточка для
    правки», остаётся; НОВОЕ format_question_card (вид сотрудника + ✅).
    parse_replacement — one-shot разбор, переиспользуется визардом (шаг текста).
    apply_replacement — сохранение (вызывает validate_question — новые гейты
    подхватятся сами). _norm_letter — кириллические А/В/С/Д.

- file: app/keyboards.py
  why: |
    _hr_btn (command="hrsay"), hr_course_actions, MAX_COURSE_ROWS, NEWLINE —
    паттерн. НОВОЕ hr_question_card(course_id, q_num) и кнопки визарда.
    ВСЕ билдеры чистые (без БД).

- file: app/db.py
  why: |
    _ensure_column (строка 22) — миграция колонок; добавить
    processed_files.content_hash TEXT + get_processed_by_hash(hash).
    mark_file_processed (~427) — параметр content_hash.
    get_course_by_doc_name, update_course_questions, get_course_questions.

- file: tests/test_course_generator.py
  why: FakeClient/queue-паттерн мока OpenAI (очередь ответов, capture calls) —
       расширить на 3-4 вызова (facts, questions, critic, repair).

- file: tests/test_hr_edit_flow.py
  why: |
    TestClient-паттерн двухшаговой правки (env-фикстура с курсом на 15 вопросов,
    _wait_for). ЭТОТ ФАЙЛ ПЕРЕПИСЫВАЕТСЯ под визард — изменение поведения
    санкционировано юзером 17.08 (тексты шагов другие, механика pending та же).

- file: tests/test_buttons_everywhere.py, tests/test_hr_invite.py
  why: |
    hr_env/фикстуры и ловушка _kb_kwargs (двойники _send без keyboard).
    ОБНОВИТЬ два теста: test_hr_questions_keyboard (клавиатура «Вопросы 5»
    теперь карточная) и test_questions_show_correct_option_text («→ B. …»
    проверять на «Вопросы N все»). Остальные старые тесты НЕ трогать.

- url: https://platform.openai.com/docs/api-reference/chat
  why: max_completion_tokens (новые модели отвергают max_tokens; gpt-4o-mini
       принимает оба — миграция безопасна); temperature у gpt-5.5 в
       chat.completions может быть отвергнут → параметр убрать вовсе.

- file: PRPs/buttons-everywhere.md
  why: инвариант «KEYBOARD-ключ только при кнопках», роутинг hrsay, дедуп
       нажатий (текст+MESSAGE_ID) — кнопки карточек едут по готовым рельсам.
```

### Known Gotchas
```python
# CRITICAL: _kb_kwargs — новые вызовы _send передают keyboard ТОЛЬКО при
# реальной клавиатуре, иначе падают 5 файлов старых тестов (двойники _send
# без параметра). Все клавиатуры карточек/визарда — kb if BUTTONS_ENABLED.

# CRITICAL: options хранятся строками «A. текст» — shuffle обязан снять
# префикс, перемешать ТЕЛА, переприсвоить «A./B./C./D.» и пересчитать correct.
# Кривой вариант без префикса — оставить сет как есть (не терять данные), лог.

# CRITICAL: validate_question используется generate_questions, apply_replacement
# и визардом — расширяя гейтами (дубли вариантов case-insensitive по телу,
# пустые тексты), не менять сигнатуру.

# CRITICAL: перегенерация всего сета ЗАТИРАЕТ ручные правки HR — в ответе
# предупреждать; сохранять только после успешной валидации всего сета.

# GOTCHA: LLM-вызовы в _handle_hr_message НЕЛЬЗЯ await-ить в хендлере
# (Bitrix ждёт ответ вебхука) — asyncio.create_task + немедленное «⏳»
# (паттерн _build_and_send_report). Двойной клик по «🔄 Заново» в дедуп-окне
# уже гасится (текст+MESSAGE_ID).

# GOTCHA: у чанков старого индекса поле doc_name может отсутствовать
# (появилось в №1/№4) — перегенерация при пустой выборке отвечает честным
# отказом, НЕ падает.

# GOTCHA: gpt-5.5 через chat.completions: убрать temperature, max_tokens →
# max_completion_tokens, response_format json_object оставить. Лимиты поднять:
# facts 2000, questions 6000, critic 2000 (вопросы стали длиннее).

# GOTCHA: правильный ответ от модели ВСЕГДА в A (так велит промпт) — это
# фича: код перемешивает сам, а инструкция модели детерминирована. НЕ просить
# модель «рандомизировать буквы» — она не умеет, наблюдение 17.08.

# GOTCHA: per-doc lock: dict[str, asyncio.Lock] лениво; лок берётся по
# file_name ДО is_file_processed и держится до сохранения курса — генерация
# копий одного документа сериализуется (нужно!), разных документов — параллельна.

# GOTCHA: sha256 считать от СЫРЫХ file_bytes до записи tmp; в processed_files
# hash пишется всегда, а ПРОВЕРКА «другое имя, тот же hash» — только при
# отсутствии курса-тёзки (дедуп имени главнее и дешевле).

# GOTCHA: «Изменить» перехватывает live-pending (_HR_COMMAND_PREFIXES) —
# визард ДОЛЖЕН регистрировать pending на КАЖДОМ шаге (это уже так: один dict);
# «перегенерировать» добавить в префиксы, иначе команда провалится в шаг визарда.

# GOTCHA: в «вопросы»-ветке regex: «Вопросы 20», «Вопросы 20.7», «Вопросы 20 все»
# — msg_lower.startswith("вопросы") уже ловит все три; парсить хвост regex-ом
# r"^вопросы\s+(\d+)(?:\.(\d+))?(?:\s+(все))?$" (лишние формы → подсказка).
```

## Implementation Blueprint

### Список задач (в порядке выполнения)

```yaml
Task 1 — генератор, промпты + совместимость с gpt-5.5:
MODIFY app/course_generator.py:
  - FACTS_PROMPT: запрет положений о самом документе (название, назначение,
    аудитория); каждое положение обязано содержать ≥1 конкретный элемент:
    шаг процедуры / число-порог / срок / роль-ответственного / конкретное
    действие («кому звонить», «что нажать»); формулировка — проверяемое
    утверждение из текста
  - QUESTIONS_FROM_FACTS_PROMPT: запрет вопросов, отвечаемых без чтения
    документа; дистракторы = правдоподобные ошибки исполнения ИЗ ТОГО ЖЕ
    документа (соседний шаг, другой порог из текста, чужая зона
    ответственности); правильный вариант ВСЕГДА класть в A (код перемешает);
    explanation без ссылок на буквы вариантов
  - _llm_json: max_tokens → max_completion_tokens, temperature убрать;
    лимиты: facts 2000, questions 6000
  - контекст: убрать chunks[:15]; join всех чанков, кап 120_000 символов
    (обрезка с логом); process_new_document (Task 5) перестаёт резать [:20]

Task 2 — shuffle + гейты + сохранение facts:
MODIFY app/course_generator.py:
  - NEW _shuffle_options(q): распарсить options по "^[A-D]\.\s*", перемешать
    тела (random.shuffle), пересобрать с префиксами, correct = новая буква
    прежнего верного ТЕЛА; вариант без префикса → вопрос не трогать (лог)
  - generate_questions: после этапа 2 — shuffle каждого вопроса; в result
    добавить facts_basic/facts_exam (для перегенерации N.q)
  - validate_question: + непустой text/options-тела; + нет дубликатов тел
    (casefold); сообщения ошибок по-русски (их видит HR при сохранении)

Task 3 — этап-критик:
MODIFY app/course_generator.py:
  - NEW CRITIC_PROMPT: вход — документ + пронумерованные 1–15 вопросы; выход
    {"verdicts": [{"num": 1, "ok": true, "reason": ""}]}; браковать: вопрос
    отвечаем без документа, мета-вопрос, неразличимые/абсурдные дистракторы
  - generate_questions: этап 3 — критик (max_completion_tokens 2000);
    для ok=false — ОДИН ремонт: этап 2 по их положениям («вопрос забракован:
    {reason} — составь другой»), shuffle, validate; повторно плохие принять
    (не зацикливаться); ошибка критика (ValueError из _llm_json) → лог +
    вернуть как есть (best-effort)

Task 4 — перегенерация:
MODIFY app/course_generator.py:
  - NEW regenerate_one(doc_name, chunks, fact | None, existing_texts, q_num)
    -> dict: промпт «составь ОДИН вопрос по положению … (или: по документу),
    не повторяющий: {existing_texts}»; validate + shuffle
MODIFY app/bitrix_bot.py (_handle_hr_message):
  - NEW ветка msg_lower.startswith("перегенерировать"): парс «N» | «N.q»;
    курс есть → немедленный ответ «⏳ Генерирую…» + asyncio.create_task(
    _regenerate_and_notify(dialog_id, client_id, course, q_num|None));
    чанки: [c for c in chunks if c.get("doc_name") == course["doc_name"]],
    пусто → «фрагменты документа не найдены в индексе — перегенерация
    недоступна» (без задачи)
  - NEW _regenerate_and_notify: q_num → regenerate_one (fact из facts_* по
    resolve_question_ref, нет facts → None) → apply в questions_json →
    ответ = карточка нового вопроса + карточная клавиатура; без q_num →
    generate_questions целиком → «✅ 15 вопросов пересозданы (ручные правки
    затёрты). Вопросы N» + карточная клавиатура; любой сбой → «❌ …» (лог)
  - _HR_COMMAND_PREFIXES += "перегенерировать"

Task 5 — дедуп:
MODIFY app/db.py:
  - init_db: _ensure_column(processed_files, "content_hash", "TEXT")
  - mark_file_processed(..., content_hash=None) — писать всегда
  - NEW get_processed_by_hash(content_hash) -> dict | None (строка с doc_name,
    исключая пустые хеши)
MODIFY app/bitrix_bot.py (process_new_document):
  - NEW module-level _ingest_locks: dict[str, asyncio.Lock]; хелпер
    _doc_lock(file_name); ВСЁ тело process_new_document после гейта
    расширения — под async with _doc_lock(file_name)
  - после скачивания: content_hash = hashlib.sha256(file_bytes).hexdigest()
  - шаг 6.5 (duplicate по doc_name) остаётся первым; НОВОЕ: если тёзки нет —
    same = get_processed_by_hash(content_hash); same и same["doc_name"] !=
    file_name и курс same["doc_name"] существует → mark_file_processed(с
    hash) + HR-notify «📄 {file_name}: то же содержимое, что у курса
    «{doc_name}» (№{id}) — вопросы общие» + return (чанки УЖЕ ингестированы
    выше — роли новой папки работают)
  - mark_file_processed везде с content_hash; generate_questions —
    new_chunks целиком (без [:20])
CREATE scripts/dedup_courses.py:
  - sys.path-бутстрап как в других скриптах; sqlite по db.DB_PATH (уважает
    env DB_PATH); группы doc_name с >1 незаархивированным; keep: с сессиями
    (SELECT DISTINCT course_id FROM sessions), иначе min id; остальным
    archived_at=now; по умолчанию DRY-RUN (печать плана), --apply — применить

Task 6 — карточный просмотр «Вопросы N»:
MODIFY app/hr_tools.py:
  - NEW format_question_card(doc_name, q, q_num) -> str: заголовок
    «📋 {doc_name}», подзаголовок «Базовый блок — вопрос q/5» или «Экзамен —
    вопрос (q-5)/10», текст, варианты с ✅ у правильного (по букве correct),
    «Пояснение: …» если есть
MODIFY app/keyboards.py:
  - NEW hr_question_card(course_id, q_num): ряд1 [⬅️ Назад→«Вопросы N.{q-1}»]
    (нет на q=1) + [▶️ Далее→«Вопросы N.{q+1}»] | на q=15 вместо Далее
    [✅ Подтвердить {N}]; NEWLINE; ряд2 [✏️ Изменить→«Изменить N.q»]
    [🔄 Заново→«Перегенерировать N.q»]; NEWLINE; ряд3 [📄 Все вопросы→
    «Вопросы N все»]
MODIFY app/bitrix_bot.py («вопросы»-ветка):
  - regex r"^вопросы\s+(\d+)(?:\.(\d+))?(?:\s+(все))?$" на msg_lower;
    «все» → ПРЕЖНЯЯ простыня (код ветки не менять, kb=hr_course_actions);
    иначе q = группа2 или 1; вопрос вне 1–15/нет → подсказка; текст =
    format_question_card, kb = keyboards.hr_question_card

Task 7 — визард «Изменить N.q»:
MODIFY app/bitrix_bot.py:
  - парс «изменить»: принимать «N q» И «N.q» (r"^изменить\s+(\d+)[.\s]+(\d+)$")
  - _pending_edits[user] = {course_id, q_num, step, draft, expires};
    step ∈ (text, opt_a..opt_d, correct, confirm); draft стартует копией
    текущего вопроса
  - шаговые тексты: «Шаг 1/6 — текст вопроса.\nСейчас: {…}\nПришли новый или
    точку (.), чтобы оставить.»; opt_X показывают тело варианта; correct
    принимает A–D/кириллицу (_norm_letter из hr_tools); ".": draft не меняется
  - шаг text: если parse_replacement(raw) распознал ЦЕЛЬНЫЙ блок → draft =
    блок целиком, прыжок в confirm
  - confirm: превью format_question_card(draft) + «💾 Сохранить —
    подтвердить, 🔄 Заново — с первого шага, Отмена — выйти»
  - ввод на confirm: «сохранить» → собрать new_q (id прежний, паттерн
    apply_replacement), validate_question; ошибка → confirm с «❌ {текст}»;
    успех → update_course_questions + карточка вопроса с карточной
    клавиатурой; «заново» → step=text; прочее → повторить превью
  - клавиатуры шагов (BUTTONS_ENABLED): [• Оставить→"."][Отмена]; confirm:
    [💾 Сохранить][🔄 Заново][Отмена] — NEW keyboards.hr_wizard_step(),
    hr_wizard_confirm()
  - существующий pending-перехват/«Отмена»/TTL — без изменений

Task 8 — тесты:
MODIFY tests/test_course_generator.py:
  - очередь моков → +критик (все ok) как 3-й вызов; тест repair: критик
    бракует №2 → 4-й вызов чинит; тест сбоя критика (мусор×2) → результат
    этапа 2 возвращается; shuffle: тела сохранены, correct_option-текст тот
    же; гейт дубликатов тел → ValueError; facts_* в result; capture: контекст
    содержит чанк №25 из 30 (срез снят)
CREATE tests/test_question_ux.py (паттерны hr_env/test_buttons_everywhere):
  - format_question_card: ✅ на правильном, подзаголовки базовый/экзамен
  - «Вопросы N» → карточка 1 + hr_question_card-клавиатура; «N.7» → карточка
    7 (Экзамен 2/10); «N.15» → [✅ Подтвердить]; «N все» → простыня
  - визард: happy-path 6 шагов + сохранить; «.»-пропуски всё оставляют;
    дубль вариантов → «❌» и повторное превью; one-shot блок на шаге 1 →
    сразу превью; «Отчёт» посреди визарда — команда важнее (уже так)
  - «Перегенерировать N.q» (regenerate_one мокнут): «⏳» пришло, вопрос
    в БД заменён, карточка ушла; чанки без doc_name → отказ
  - гонка: два asyncio.gather(process_new_document) одного doc_name, LLM-мок
    с asyncio.sleep(0.05) (мокать generate_questions через
    monkeypatch + httpx-моки шагов скачивания — образец test_folder_sync) →
    в БД ОДИН курс; hash-дубль под другим именем → курс не создан, HR-notify
MODIFY (санкционировано, ТОЛЬКО эти):
  - tests/test_hr_edit_flow.py — переписать под визард (та же env-фикстура)
  - tests/test_hr_invite.py::test_questions_show_correct_option_text —
    команда «Вопросы {id} все»
  - tests/test_buttons_everywhere.py::test_hr_questions_keyboard — карточная
    клавиатура «Вопросы 5» (тексты кнопок)

Task 9 — task.md, деплой-хвост (чеклист):
  - .env сервера: OPENAI_MODEL=gpt-5.5 (+ рестарт)
  - deploy.sh (уже git-строгий), scripts/dedup_courses.py на сервере:
    dry-run → --apply (ожидаемо в архив: 18, 21, 22, 23, 24, 25)
  - «Перегенерировать 13…20» руками для боевых курсов (старые наборы полые)
  - живой прогон: карточки, визард, перегенерация одного вопроса, hash-дубль
    (залить копию документа под другим именем)
```

### Integration Points
```yaml
CONFIG: только серверный .env (OPENAI_MODEL=gpt-5.5) — новых env нет
DB: processed_files.content_hash (через _ensure_column, без миграций схемы)
ROUTES: без новых роутов — всё внутри _handle_hr_message
```

## Validation Loop

### Level 1
```bash
ruff check . --fix   # mypy в репо не настроен — не вводить
```

### Level 2
```bash
python -m pytest tests/ -v
# Старые тесты, кроме трёх перечисленных в Task 8, — БЕЗ правок.
# Красный не-перечисленный тест = сломан код, чинить код.
```

### Level 3 (локально, без сети)
```bash
python - <<'EOF'
from fastapi.testclient import TestClient
import app.bitrix_bot as bot
c = TestClient(bot.app)
assert c.post("/command", data={"event": "X"}).json()["status"] == "ignored"
print("ok")
EOF
```

## Final validation Checklist
- [ ] pytest зелёный; ruff чистый
- [ ] grep: каждый новый keyboard-вызов за флагом/через _kb_kwargs
- [ ] Перегенерация и визард не await-ят LLM в хендлере (create_task)
- [ ] dedup_courses.py в dry-run на ЛОКАЛЬНОЙ базе печатает план, ничего не меняя
- [ ] task.md обновлён (чеклист деплой-хвоста из Task 9)

## Anti-Patterns to Avoid
- ❌ Не просить модель «случайно распределять буквы» — перемешивает код
- ❌ Не блокировать вебхук ожиданием LLM — только create_task + «⏳»
- ❌ Не трогать не-перечисленные старые тесты и «Вопросы N все»-формат
- ❌ Не добавлять UNIQUE-индекс по doc_name (см. резы)
- ❌ Не городить диалог при hash-дубле — авто-привязка (решение юзера)
- ❌ Не терять id вопроса при правке/перегенерации (паттерн apply_replacement)

## Confidence Score: 8/10
Все паттерны (LLM-моки очередью, hr_env, pending-правка, фоновые задачи,
_kb_kwargs, карточные клавиатуры) в репо отработаны; диагнозы подтверждены
живыми данными. Минус два балла: (1) фактическое КАЧЕСТВО новых промптов и
критика проверяется только живой генерацией на gpt-5.5 (деплой-хвост —
«Перегенерировать N» по боевым курсам и глазами смотреть); (2) совместимость
параметров chat.completions с gpt-5.5 (митигация: max_completion_tokens, без
temperature) подтверждается первым живым вызовом.
