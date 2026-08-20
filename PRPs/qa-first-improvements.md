name: "Q&A-first улучшения: свобода в тесте, источник в ответах, «Мои документы», анонс при загрузке, HR-аббревиатуры, per-user смена роли, eval-прогон, фикс disk-вебхука"
description: |
  Пакет по голосовому юзера 19.08 (после разворота «тесты ОСТАЮТСЯ, но Q&A
  должен быть хорош»): снять клетку теста (выход/пауза с сохранением
  прогресса), источник-ссылка под RAG-ответами, список «Мои документы»,
  уведомление «стандарт доступен» сразу после загрузки (тест — после
  подтверждения, с явными 7 днями), HR-команда добавления аббревиатур,
  выдаваемое HR-ом право смены роли, скрипт валидации Q&A (5 вопросов/документ
  → ответы → отчёт для Дмитрия), регистр события ONDISKFILEADD.

## Purpose
Одно-проходная реализация поверх кода 19.08 (248 тестов; чистка индекса
сделана: боевая база = 7 документов Регламент_тест2, мониторинг 4760370).

## Core Principles
1-5 как всегда. Дизайн кнопок 17.08 обязателен: payload'ы = прежние команды,
роли цветов из keyboards._ROLES, WIDTH у LINE, ≤1 primary (инвариант-тест
test_keyboard_design ловит нарушения автоматически).

## НЕ в скоупе (зафиксировать комментариями)
- Vision-ингест картинок (АЛГОРИТМ БРОНИРОВАНИЯ = 5 слов + 1 картинка, бот
  по нему слеп; Негарантированные — 9 скриншотов) — отдельная смета.
- Гибридный поиск/реранк — после результатов eval-прогона.
- RAG-ответы ВНУТРИ теста — свободный текст в тесте остаётся ответом на
  вопрос; путь сотрудника: выйти из теста (прогресс сохранится) → спросить →
  вернуться через «Выбрать».
- Qdrant (решение 19.08: не нужен на этом масштабе).
- Скрытие кнопки «Роль» при запрете смены — кнопка остаётся, бот вежливо
  отказывает (клавиатура не знает per-user прав, усложнение не окупается).

---

## Goal

**A. Свобода в тесте (BASIC_TEST/EXAM), «застрял в бесконечном тесте»:**
- В _handle_test ДО parse_answer обрабатываются: «Мои курсы»/«курсы»/«меню»
  (_MENU_COMMANDS) → список курсов; «Выбрать N» (_SWITCH_RE) → переключение
  (текущая тест-строка ПАРКУЕТСЯ со своим state и current_q_idx — механика
  multi-row 17.08 уже умеет: get_open_session_for_course вернёт к тому же
  вопросу); «выйти»/«выход» → тот же список курсов + подсказка «тест
  сохранён на вопросе {q_idx+1}/{total} — вернись через Выбрать».
- _handle_course_switch: existing-строка в BASIC_TEST → touch + показать
  ТЕКУЩИЙ вопрос базового (симметрично EXAM-ветке).
- Клавиатура теста: + ряд [⏸ Отложить тест → «Выйти»] (LINE 170, service).
- «Мои курсы»: строка курса с открытой тест-строкой помечается
  «— тест на вопросе {n}» (open_states уже собираются).

**B. Источник под RAG-ответами:**
- _rag_reply дополняет ответ строкой «📄 Источник: {display_name} — {url}»
  (top-1 doc_name из relevant; ссылка = get_course_by_doc_name → doc_detail_url,
  нет курса/ссылки → без URL). «Не найдено релевантных фрагментов» — без
  источника.

**C. «Мои документы»:**
- Команда «Мои документы»/«документы» в READING и WAITING_HR: список
  уникальных doc_name из ГЛОБАЛЬНОГО индекса, чьи roles пересекаются с
  {роль, all_staff} и audience != guest, с display_name и ссылками
  (doc_detail_url по doc_name). Роль неизвестна → только ALL-документы +
  подсказка про «Роль».
- Кнопки: READING ряд навигации → [📚 Мои курсы 150][📄 Документы 130],
  NEWLINE, [Роль 110 service]; WAITING_HR → [📚 Мои курсы] BLOCK +
  [📄 Документы 130 LINE].

**D. Доступность сразу после загрузки:**
- process_new_document, НОВЫЙ курс (после сохранения драфта): рассылка
  сотрудникам ролей документа (роли из префиксов; ALL = все в whitelist):
  «📄 Новый стандарт доступен: *{display_name}* — {ссылка}. Уже можешь
  читать и задавать мне вопросы по нему. Тест будет назначен позже.»
  Без busy-гейта (это знание, не курс); fire-and-forget как _broadcast_course.
- Анонс при «Подтвердить» (существующий _broadcast_course): в текст добавить
  «⏰ На прохождение — {ESCALATION_DAYS} дней.» (константу пробросить).

**E. HR-аббревиатуры:**
- «Аббревиатуры» — список prefixes из data/roles.json («FO → Служба приёма…»).
- «Аббревиатура добавить {ABBR} {Название роли}» → roles.json: prefixes[ABBR]
  = slug (транслит/abbr.lower()), roles[slug] = название (если slug нового);
  атомарная запись (tmp+replace) через новый roles.save_roles_config.
  Валидация: ABBR = A-Z/0-9 ≤6; конфликт существующей → «уже занята …».
- «Аббревиатура удалить {ABBR}» → удалить prefix (роль остаётся).

**F. Право смены роли (per-user):**
- db: _ensure_column(employees, "can_switch_role", "INTEGER DEFAULT 1") +
  set_can_switch_role(uid, flag), get_employee уже отдаёт строку.
- HR: «Роль разрешить {email|ID}» / «Роль запретить {email|ID}» (резолв
  email по образцу «Допустить»).
- state_machine: команда «Роль» в READING/WAITING_HR — если у сотрудника
  авто-роль по отделу ВОЗМОЖНА (отдел замаплен) И can_switch_role=0 →
  «🔒 Твоя роль определяется отделом. Право выбора ролей выдаёт HR.»;
  иначе как сейчас. (Пока departments пуст — поведение не меняется ни для
  кого: гейт срабатывает только при живом маппинге.)

**G. eval-прогон Q&A (scripts/eval_qa.py):**
- Для каждого doc_name индекса (пропуск документов < 50 слов текста чанков —
  «АЛГОРИТМ» честно попадает в отчёт строкой «текста нет, бот слеп»):
  gpt-5.5 генерирует 5 конкретных вопросов сотрудника ПО СОДЕРЖИМОМУ
  (промпт запрещает мета-вопросы — переиспользовать формулировки из
  course_generator); каждый вопрос → rag.answer(role_filter=первая роль
  документа, ALL → None); markdown-отчёт: документ → [вопрос / ответ /
  источник top-1] → data/eval_qa_{дата-аргумент}.md + stdout.
- Запуск в контейнере (данные и ключ там):
  docker exec vertical-standards-bot python scripts/eval_qa.py --date 20260819

**H. Фикс disk-вебхука:** event сравнивать регистронезависимо
  (UPPER == "ONDISKFILEADD") — Bitrix шлёт UPPERCASE (см. /user-webhook).

## What / Success Criteria
- [ ] В тесте: «Мои курсы» показывает список, «Выбрать N» переключает с
      сохранением q_idx, возврат «Выбрать» продолжает С ТОГО ЖЕ вопроса,
      «выйти» — список + подсказка; A–D продолжают отвечать как раньше
- [ ] Ответ RAG оканчивается источником со ссылкой (есть курс) / без (нет)
- [ ] «Мои документы» фильтрует по роли; ALL видны всем; кнопки по спеке
- [ ] Новый документ → немедленная рассылка ролям (тест не назначен);
      «Подтвердить» → прежний анонс + «{N} дней»
- [ ] «Аббревиатура добавить/удалить», «Аббревиатуры» работают, roles.json
      пишется атомарно, невалидные входы отбиты
- [ ] «Роль разрешить/запретить» меняет employees.can_switch_role; гейт
      «Роль» активен ТОЛЬКО при замапленном отделе
- [ ] scripts/eval_qa.py на мокнутом OpenAI/rag выдаёт корректный markdown;
      payload-инварианты клавиатур (test_keyboard_design) зелёные с новыми
      кнопками
- [ ] `python -m pytest tests/ -v` (248 + новые), `ruff check .` чистые

## All Needed Context

### Documentation & References
```yaml
- file: app/state_machine.py
  why: |
    _handle_test (~500): вставить блок меню/выбора/выхода ДО «выбрать»-заглушки
    и parse_answer; СЕГОДНЯ там «выбрать» отвечает отказом — заменить на
    _handle_course_switch. _MENU_COMMANDS, _SWITCH_RE (~430). _handle_course_switch
    (~445): ветки existing EXAM/WAITING_HR — добавить BASIC_TEST (образец —
    EXAM-ветка: format_question(current_q, q_idx, total, "basic")).
    my_courses (~400): open_states уже считаются — статус «тест на вопросе N»
    для строк BASIC_TEST (state и current_q_idx есть в строке сессии).
    _rag_reply (~530): точка источника (B). «роль» в _handle_reading (~470)
    и WAITING_HR — гейт can_switch_role (F): нужен get_employee (импортирован)
    и _role_from_profile/_live_departments+role_for_departments (~245-278) —
    «авто-роль возможна» = role_for_departments(_live_departments(uid)) не None;
    ЖИВОЙ user.get дорог на каждый вызов «Роль» — допустимо (команда редкая).
    _broadcast_course-текст НЕ здесь — в bitrix_bot (D).

- file: app/bitrix_bot.py
  why: |
    _handle_employee_message: «Мои документы» — добавить в условие menu-веток?
    НЕТ: команда идёт в process_message (FSM) — обработка в state_machine
    (_handle_reading/_handle_waiting_hr, кортеж _DOC_COMMANDS), а СПИСОК
    строится из глобального chunks bitrix_bot… ГОЧА: у state_machine НЕТ
    глобального индекса — chunks передаются в process_message! _handle_reading
    уже получает chunks — фильтровать по ним (roles/audience/doc_name у чанков
    есть). Ссылки: get_course_by_doc_name в state_machine уже импортирован?
    НЕТ — добавить в импорт из db.
    process_new_document (~1490 после правок): точка рассылки D — после
    save_draft_course, до notify HR; получатели: whitelist-сотрудники, чья
    роль (или роль неизвестна → только ALL) пересекается с ролями документа —
    ОБРАЗЕЦ _course_recipients (~880), но БЕЗ busy-гейта и БЕЗ done-гейта:
    новый хелпер _standard_recipients(roles) (sync, to_thread).
    _broadcast_course (~920): + строка про ESCALATION_DAYS.
    HR-ветки: «аббревиатур»-префикс и «роль разрешить/запретить» — паттерн
    «руководител»-ветки (~1230): parts, _extract_email, add/remove. ВАЖНО:
    «роль разрешить» стартует с «роль» — HR-бот не знает команды «роль»,
    конфликтов нет; в _HR_COMMAND_PREFIXES добавить «аббревиатура»,
    «аббревиатуры», «роль».
    /disk-webhook (~1470): регистр события (H).

- file: app/roles.py
  why: |
    load_roles_config/CONFIG_PATH — образец чтения; НОВОЕ save_roles_config
    (json.dump в tmp + os.replace, ensure_ascii=False, indent=2 — файл правят
    и руками). parse_filename/display_name — имена; ALL_STAFF. slug для новой
    роли: abbr.lower() (просто и предсказуемо; коллизия slug при живом
    prefixes → та же роль переиспользуется).

- file: app/db.py
  why: _ensure_column (22) — can_switch_role; employees-хелперы (add_employee,
       get_employee ~570); НОВОЕ set_can_switch_role. get_course_by_doc_name.

- file: app/keyboards.py + tests/test_keyboard_design.py
  why: |
    Дизайн-инварианты. Новые кнопки: for_session READING ряд → [📚 Мои курсы
    150][📄 Документы 130] / NEWLINE / [Роль 110 service]; WAITING_HR →
    BLOCK[📚 Мои курсы] + [📄 Документы 130]; тест-клавиатуры (буквы И
    block-вариант) + NEWLINE + [⏸ Отложить тест→«Выйти» 170 service].
    Payload'ы «Документы», «Выйти» — добавить в ALLOWED_PAYLOADS и SAMPLES.

- file: app/course_generator.py
  why: формулировки запрета мета-вопросов для промпта eval_qa (G) —
       скопировать требования из QUESTIONS_FROM_FACTS_PROMPT; _llm_json —
       образец вызова (max_completion_tokens, без temperature, json_object).

- file: scripts/register_commands.py, scripts/dedup_courses.py
  why: паттерн скриптов (sys.path-бутстрап, argparse, docstring с командой
       запуска в docker exec).

- file: tests/test_state_machine.py (env-фикстура), tests/test_buttons_everywhere.py
        (hr_env, send_trap), tests/test_question_ux.py
  why: все нужные паттерны; env мокает rag_answer → источник тестировать
       через relevant: fake_rag должен вернуть ("MOCK", [{"doc_name": ...}])
       — ГОЧА: существующий fake_rag в env возвращает ("MOCK_ANSWER", []) —
       пустой relevant = без источника, старые тесты НЕ ломаются.
```

### Known Gotchas
```python
# CRITICAL: _rag_reply сейчас выбрасывает relevant — источник строить из
# rag_answer()[1]; пустой список (мок в тестах, «не найдено») → БЕЗ строки
# источника, иначе 20+ старых тестов с fake_rag посыпались бы.

# CRITICAL: «Мои документы» строится из ПЕРЕДАВАЕМЫХ chunks (сигнатуры
# _handle_reading/_handle_waiting_hr уже несут chunks) — НЕ тянуть индекс
# в state_machine отдельно. Фильтр: audience != "guest" и
# {role, all_staff} & set(chunk roles) (образец role_mask в roles.py).

# CRITICAL: парковка теста через «Выбрать» опирается на active=freshest
# (17.08): НИЧЕГО не сбрасывать в тест-строке — возврат продолжает с q_idx.
# Ответы не задваиваются (продолжение, не рестарт).

# CRITICAL: в _handle_test блок меню ставить ПОСЛЕ гейта q_idx>=total, но
# ДО parse_answer; строку «выбрать» с отказом УДАЛИТЬ (заменяется реальным
# переключением) — тест test_state_machine, проверяющий отказ, обновить
# (санкционировано: поведение меняется по голосовому 19.08).

# GOTCHA: рассылка D — БЕЗ гейтов busy/done (_course_recipients НЕ
# переиспользовать as-is); роль сотрудника = _last_known_role, None → только
# ALL-документы (не спамить ролевыми).

# GOTCHA: «роль разрешить» в HR — префикс «роль» ловит и «руководител…»?
# НЕТ: startswith-кортеж, «руководител» отдельная ветка ВЫШЕ по порядку —
# новую ветку «роль » ставить ПОСЛЕ «руководител»-ветки и матчить
# r"^роль\s+(разрешить|запретить)\s+(\S+)$" — иначе провал в help.

# GOTCHA: can_switch_role дефолт 1 (разрешено) — сегодняшнее поведение не
# меняется; гейт активен только когда И отдел замаплен, И флаг снят.

# GOTCHA: save_roles_config пишет data/roles.json В КОНТЕЙНЕРЕ = volume
# state/data — переживает деплой ✓; локальная копия репо разъедется —
# отметить в task.md (перенести правки в репо при случае).

# GOTCHA: eval_qa: rag.answer при пустых чанках роли вернёт «Не найдено…» —
# это ВАЛИДНАЯ строка отчёта (показывает пробел), не ошибка скрипта.
# Сеть/LLM только в живом запуске; тесты — с мокнутым OpenAI (очередь) и
# мокнутым rag.answer.

# GOTCHA: ESCALATION_DAYS живёт в bitrix_bot (env) — текст «N дней» в
# _broadcast_course, НЕ в state_machine.
```

## Implementation Blueprint

### Список задач (в порядке выполнения)

```yaml
Task 1 — свобода в тесте (state_machine):
  - _handle_test: после guard q_idx>=total —
      cmd = message.strip().lower()
      if cmd in _MENU_COMMANDS or cmd in ("выйти", "выход"):
          text = _my_courses_text(...)
          if cmd in ("выйти", "выход"): text = подсказка(q_idx, total) + text
          return text
      m = _SWITCH_RE.match(cmd) → return _handle_course_switch(session, n)
    (строку-отказ «выбрать» удалить)
  - _handle_course_switch: ветка existing["state"] == "BASIC_TEST" →
    touch + «📝 Продолжаем базовый тест курса *{name}*» + format_question
  - my_courses: строка с open_state BASIC_TEST → «— базовый тест на
    вопросе {q+1}» (нужен current_q_idx: open_states хранит state —
    расширить до (state, current_q_idx))

Task 2 — источник (state_machine._rag_reply):
  - text, relevant = rag_answer(...); top = relevant[0]["doc_name"] если есть
  - course = get_course_by_doc_name(top) (импорт в db-блок) → url
  - return text + f"\n\n📄 Источник: {display_name(top)}" (+ f" — {url}")

Task 3 — «Мои документы» (state_machine + keyboards):
  - _DOC_COMMANDS = ("мои документы", "документы")
  - _my_documents_text(role, chunks): уникальные doc_name прошедших фильтр
    чанков; на каждый display_name + ссылка (get_course_by_doc_name);
    пусто → «Для твоей роли пока нет документов.»; role None → только ALL +
    подсказка «выбери роль: Роль»
  - обработка в _handle_reading и _handle_waiting_hr (до RAG)
  - keyboards.for_session: READING → [Готов BLOCK] / [📚 Мои курсы 150]
    [📄 Документы 130→«Документы»] / NEWLINE [Роль 110 service];
    WAITING_HR → [📚 Мои курсы BLOCK] / [📄 Документы 130];
    тест-клавиатуры + NEWLINE + [⏸ Отложить тест→«Выйти» 170 service]

Task 4 — анонс при загрузке (bitrix_bot):
  - NEW _standard_recipients(doc_roles) (sync): whitelist-сотрудники;
    role=_last_known_role; role None → включать только если ALL_STAFF в
    doc_roles; иначе {role, ALL_STAFF} & doc_roles
  - process_new_document после save_draft_course: asyncio.create_task(
    _broadcast_new_standard(course_id, file_name, detail_url, roles))
    → текст из Goal D, последовательные _send (образец _broadcast_course)
  - _broadcast_course: + f"\n⏰ На прохождение — {ESCALATION_DAYS} дней."

Task 5 — HR-аббревиатуры (roles + bitrix_bot):
  - roles.save_roles_config(cfg) — tmp+os.replace
  - ветки: «аббревиатуры» (список), «аббревиатура добавить/удалить» —
    regex, валидация ABBR ([A-Z0-9]{1,6}, upper()), название обяз. при
    новом slug; ответы с текущим списком; _HR_COMMAND_PREFIXES +
    ("аббревиатура", "аббревиатуры")

Task 6 — право смены роли (db + bitrix_bot + state_machine):
  - db: _ensure_column employees.can_switch_role INTEGER DEFAULT 1;
    set_can_switch_role(uid, flag)
  - HR-ветка «роль разрешить|запретить {email|ID}» (после «руководител»);
    email → get_employee_by_email; ответ «✅ … может/не может выбирать роль»
  - state_machine: в обработке «роль» (READING) — гейт из Goal F
    (авто-роль возможна = role_for_departments(_live_departments(uid)))

Task 7 — eval_qa (scripts/eval_qa.py):
  - argparse --date (обяз., в имя файла — Date.now в скриптах есть, это не
    workflow, datetime можно; всё же дата аргументом для воспроизводимости),
    --questions N (деф. 5), --out (деф. /app/data)
  - load_index() → группировка чанков по doc_name; текст дока = join;
    < 50 слов → строка-отчёт «⚠️ текста нет — бот слеп (картинки?)»
  - _llm_json-подобный вызов (СКОПИРОВАТЬ приватный паттерн или импорт
    course_generator._llm_json — импорт приватного из scripts допустим в
    этом репо): промпт «5 конкретных вопросов сотрудника по содержимому,
    без мета-вопросов» → {"questions": [...]}
  - роль = первый префикс parse_filename; ALL → None
  - rag.answer(...) на каждый вопрос; отчёт markdown; печать + файл
  - докстринг: docker exec … python scripts/eval_qa.py --date YYYYMMDD

Task 8 — вебхук (bitrix_bot /disk-webhook):
  - event = (form.get("event") or "").upper(); != "ONDISKFILEADD" → ignored
    (лог-строка прежняя)

Task 9 — тесты:
  - test_state_machine: НОВЫЕ — меню из теста (env-флоу: дойти до BASIC_TEST,
    «Мои курсы» → «📚 Твои курсы», состояние НЕ изменилось, q_idx цел);
    «Выбрать» из теста паркует и возвращает к тому же вопросу (2 курса);
    «выйти» — подсказка с номером вопроса; ОБНОВИТЬ тест отказа «выбрать»
    в тесте (если есть — grep «Сначала закончи текущий тест»)
  - test_question_ux/новый test_qa_first.py: источник (fake_rag с relevant
    → строка «📄 Источник», пустой relevant → нет строки); «Мои документы»
    (чанки с ролями: фильтрация, ALL, role None); анонс при загрузке
    (test_folder_sync-харнесс: _hr_ids пуст, capture _send → «стандарт
    доступен» ролям); аббревиатуры (tmp roles.json через monkeypatch
    roles.CONFIG_PATH — образец env-фикстур; добавить/удалить/список/
    невалидные); «роль разрешить/запретить» (hr_env + tmp DB); гейт «Роль»
    (monkeypatch _live_departments/role_for_departments + can_switch_role=0);
    eval_qa (мок OpenAI очередью + мок rag.answer → md-структура)
  - test_keyboard_design: SAMPLES/ALLOWED_PAYLOADS += «Документы», «Выйти»,
    новые раскладки READING/WAITING/тест
  - test_buttons_everywhere: reminders/READING-ассерты текстов кнопок
    обновить под новый ряд (санкционировано — только затронутые)

Task 10 — task.md:
  - деплой: живой прогон eval_qa на сервере → отчёт юзеру → Дмитрию
  - юзеру: настроить исходящий вебхук ONDISKFILEADD →
    http://195.63.168.145:8000/disk-webhook (после — поллер можно замедлить
    POLL_INTERVAL_SEC=3600, не выключая: страховка)
  - синхронизировать data/roles.json репо ↔ сервер после первых
    «Аббревиатура добавить»
  - живо: выход из теста, возврат к вопросу, источник-ссылки, «Мои
    документы», анонс при заливке нового файла
```

## Validation Loop
```bash
ruff check . --fix
python -m pytest tests/ -v      # старые менять только перечисленные в Task 9
python - <<'EOF'
from fastapi.testclient import TestClient
import app.bitrix_bot as bot
c = TestClient(bot.app)
assert c.post("/disk-webhook", data={"event": "ONDISKFILEADD"}).json()["status"] == "no_file_id"
print("ok")
EOF
```

## Final validation Checklist
- [ ] pytest/ruff зелёные; test_keyboard_design проходит с новыми кнопками
- [ ] grep: новые клавиатуры за BUTTONS_ENABLED/_kb_kwargs
- [ ] В тесте A–D работают как раньше (регресс test_state_machine)
- [ ] eval_qa: `python scripts/eval_qa.py --help` работает локально
- [ ] task.md обновлён

## Anti-Patterns to Avoid
- ❌ Не сбрасывать q_idx/ответы при выходе из теста — пауза, не рестарт
- ❌ Не разрешать RAG внутри теста (буква = ответ, текст = переспрос)
- ❌ Не тянуть индекс в state_machine — chunks приходят параметром
- ❌ Не переиспользовать _course_recipients для анонса стандарта (лишние гейты)
- ❌ Не менять payload'ы кнопок и не добавлять вторую primary
- ❌ Не писать roles.json без tmp+replace (файл читают на каждый запрос)

## Confidence Score: 8/10
Все механики опираются на уже отработанные конструкции (multi-row парковка,
menu-команды, hr-ветки, конфиг ролей, харнесс тестов), санкционированные
правки старых тестов перечислены. Минус два балла: (1) взаимодействие
«пауза теста ↔ дедлайны №9/Мои курсы» имеет краевые случаи (строка теста
видна как незакрытая — дедлайн продолжает тикать: поведение ПРАВИЛЬНОЕ, но
проверить глазами в интеграционном тесте); (2) качество eval-вопросов и
живого прогона проверяется только на сервере с gpt-5.5.
