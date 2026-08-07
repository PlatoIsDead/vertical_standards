name: "Пакет фидбека демо 05.08: рассылка при активации курса, «Мои курсы», ФИО/должность в отчётах, 2-этапная генерация вопросов, чистка macOS-мусора с Диска"
description: |
  Пять доработок по фидбеку Дмитрия с демо 05.08. Критическая — A: рассылка
  «доступен новый курс» при «Подтвердить N» ОБЕЩАНА клиенту как уже работающая,
  в коде её нет. Остальные: навигация сотрудника (B), человеческие подписи в
  отчётах (C), двухэтапная генерация вопросов (D), удаление ._* с Диска (E).

## Purpose
Одно-проходная реализация пакета. Контекст самодостаточный: реальные сниппеты,
точки правок, ловушки, порядок задач, исполняемые гейты. База — код после №11
(PRPs/prefix-roles-courses.md): инверсия роль→курс, parse_filename, сид HR.

## Core Principles
1. **Context is King** — весь нужный код перечислен ниже
2. **Validation Loops** — ruff + pytest, LLM-часть через мок клиента
3. **Information Dense** — имена функций/полей из реального кода
4. **Progressive Success** — A (критично) → B → C → D → E
5. **Global rules** — CLAUDE.md: простейшее решение, UI-тексты на русском

## НЕ в скоупе (не трогать)
- №9 с новым скоупом (ежедневные напоминания сотруднику, двухступенчатая
  email-эскалация) — ОТДЕЛЬНЫЙ PRP: скоуп изменён клиентом 05.08,
  PRPs/retake-escalation.md устарел, нужно решение юзера.
- №8 (роль из UF_DEPARTMENT) — блокирован: ждём от клиента список отделов.
- №7 (кнопки KEYBOARD), №10 (Excel-отчёт) — свои PRP позже.

---

## Goal

HR пишет «Подтвердить 12» → курс активирован И все подходящие по роли свободные
сотрудники получают от employee-бота «📚 Тебе доступен новый курс…». Сотрудник
в READING пишет «Мои курсы» → список своих курсов (✅ пройден / ▶️ текущий /
⏳ ожидает). «Отчёт»/«История»/уведомления HR показывают ФИО, должность и роль,
а не голый ID. Вопросы курса генерируются в 2 этапа (факты → вопросы).
`._*`/`.DS_Store` в отслеживаемых папках уезжают в корзину Диска.

## Why

- **A обещано клиенту как работающее** (транскрипт 05.08: «после подтверждения
  курс уходит всем сотрудникам, им приходит уведомление») — в `hr_handler`
  ветка «подтвердить» только отвечает HR. Дмитрий пойдёт проверять именно это.
- **B — главная претензия Дмитрия:** «я сотрудник, открыл пустой чат — не знаю,
  что мне проходить, нет старта, нет меню».
- **C — просьба Дмитрия:** «рядом с номером фамилия-имя… должность — шикарно…
  градация по отделу — для аналитики». `build_report_text` уже показывает
  ФИО+email для приглашённых; не хватает должности и роли/отдела, а
  `notify_hr` в `_finish_phase` шлёт голый ID.
- **D — Никита сам признал на демо:** «этап составления вопросов не самый
  лучший… сделаем в 2 этапа, чтобы было поумнее».
- **E — Никита пообещал:** «добавлю, чтобы при входе он их удалял»; Дмитрий
  подтвердил, что файлы не нужны («это временные файлы мака»).

## What / Success Criteria
- [ ] «Подтвердить N» → HR-ответ «активирован + уведомляю K сотрудников»;
      каждому подходящему уходит сообщение от EMPLOYEE-бота (BOT_ID, не HR)
- [ ] Рассылка только: в whitelist, БЕЗ активной сессии, курс не пройден,
      роль ∩ роли курса (роль неизвестна → только ALL-курсы)
- [ ] «Мои курсы» в READING → список по роли с статусами, RAG НЕ вызывается
- [ ] `_start_reading` подсказывает команды (Готов / Мои курсы / Роль)
- [ ] «Отчёт»: `ФИО (должность) · роль — «курс»: статус`; «История» — то же в
      шапке; `_finish_phase` notify_hr — ФИО вместо голого ID (ID остаётся в
      команде «Допустить {id}»)
- [ ] `generate_questions` делает 2 LLM-вызова (факты → вопросы), итоговый JSON
      той же схемы, `_validate_questions` проходит; тесты с мок-клиентом
- [ ] `.DS_Store`/`._*` из листинга → `disk.file.markdeleted` (корзина,
      восстановимо), best-effort, ошибка не роняет поллер
- [ ] `python -m pytest tests/ -v` зелёный (сейчас 118), `ruff check` чистый

## All Needed Context

### Documentation & References
```yaml
- file: app/bitrix_bot.py
  why: |
    hr_handler «подтвердить»: строки ~570-587 — сюда рассылку (A). Паттерн
    отправки: _send(dialog_id, text, bot_id, client_id); автостарт от
    EMPLOYEE-бота = _send(f"u{uid}", msg, BOT_ID) (ветка «пригласить», ~621).
    _walk_folder: скип скрытых файлов уже стоит (№11) — в эту ветку E-удаление.
    _delete_document — образец фоновой операции с httpx-клиентом + уведомление.
    ЛОВУШКА: _send создаётся клиентом httpx.AsyncClient внутри — в тестах
    httpx подменён FakeAsyncClient (test_folder_sync) либо _send замокан
    (test_hr_invite). Для E создавать клиент так же (bot.httpx.AsyncClient).

- file: app/state_machine.py
  why: |
    После №11: pick_course_for_role/course_roles/_done_course_ids/
    _last_known_role — ПЕРЕИСПОЛЬЗОВАТЬ для рассылки (A) и «Мои курсы» (B).
    _handle_reading:158 — интерсепт «роль» уже есть, «мои курсы» добавить ДО
    RAG тем же паттерном (exact match lower). _start_reading — сюда строку
    подсказки команд. _finish_phase:230-277 — два notify_hr с голым ID (C).
    _handle_waiting_hr — фикс-текст, добавить подсказку «Мои курсы».

- file: app/hr_tools.py
  why: |
    build_report_text:179 — label уже `ФИО (email)`; расширить должностью и
    ролью. Чистые функции без БД — сигнатуру менять аккуратно: rows из
    get_report_rows содержат s.* (включая role!) + doc_name + questions_json.
    build_history_text:151 — label приходит готовым из hr_handler («история»),
    обогащение делать в hr_handler при сборке label.

- file: app/course_generator.py
  why: |
    generate_questions: 1 вызов chat.completions, response_format json_object,
    retry 2 попытки, _validate_questions (5+10, validate_question). Сигнатуру
    НЕ менять — мокается в test_folder_sync (cg.generate_questions).
    Клиент создаётся внутри функции: в тестах monkeypatch
    course_generator.OpenAI. OPENAI_MODEL default gpt-4o-mini.

- file: app/db.py
  why: |
    employees: bitrix_uid/email/full_name/added_by (+_ensure_column-паттерн для
    новой колонки work_position). add_employee — upsert COALESCE, расширить.
    get_report_rows — JOIN sessions×courses, отдаёт s.role. get_employee(uid).

- file: tests/test_hr_invite.py
  why: |
    Паттерн теста /hr через TestClient: fixture env (tmp DB, roles.json,
    fake _send собирает sent, fake _bitrix_user_by_email, HR_USER_IDS=9,
    bot._recent_msgs.clear()), _wait_for для asyncio.create_task. Рассылку (A)
    тестировать так же — новый файл test_course_broadcast.py.

- file: tests/test_folder_sync.py
  why: |
    FakeAsyncClient.routes (suffix→handler) — для E добавить роут
    disk.file.markdeleted и расширить test_hidden_files_skipped_in_walk.

- file: data/referance/bitrix24_docs.md
  why: локальная выжимка Bitrix REST — искать «markdeleted» здесь прежде веба.

- url: https://apidocs.bitrix24.com/api-reference/disk/file/disk-file-mark-deleted.html
  why: disk.file.markdeleted {id} — в корзину (восстановимо), скоуп disk УЖЕ
       есть у вебхука (проверено: disk, im, imbot, user).
```

### Known Gotchas
```python
# CRITICAL (A): рассылка от EMPLOYEE-бота — _send(f"u{uid}", text, BOT_ID);
# client_id НЕ передавать (форма /hr несёт токен HR-бота) — _send сам возьмёт
# BOT_CLIENT_ID из .env. Без него Bitrix → 403 на проактивную отправку.

# CRITICAL (A): count считать ДО ответа HR (asyncio.to_thread), сами отправки —
# fire-and-forget asyncio.create_task: HR не должен ждать K×retry-циклов
# (сеть флапает, до 5 ретраев на сообщение).

# CRITICAL (A): _last_known_role/course_roles импортировать из state_machine,
# НЕ дублировать. Роль неизвестна (сотрудник ни разу не выбирал) → слать только
# курсы с all_staff в ролях: «RES-курс всем без роли» = спам не тем людям.

# CRITICAL (B): интерсепт «мои курсы»/«курсы»/«меню» — exact match после
# .strip().lower(), ДО rag_answer, по образцу «роль» (_handle_reading:159).
# НЕ substring: «какие курсы по уборке?» должен уйти в RAG.

# CRITICAL (C): get_report_rows УЖЕ отдаёт s.role — для «Отчёта» ничего в БД
# не менять; должность = employees.work_position (новая колонка). role_name()
# из app.roles для русского имени роли. hr_tools остаётся чистым: должность
# передавать через employees_by_uid (записи уже содержат все колонки employees).

# CRITICAL (D): сигнатура generate_questions(doc_name, chunks) НЕ меняется —
# мокается в test_folder_sync. Итоговый dict — ТА ЖЕ схема (doc_name,
# course_summary, basic_questions[5], exam_questions[10]) — совместимость с
# questions_json, нумерацией 1-15 (hr_tools), format_question.

# GOTCHA (D): у обоих этапов response_format json_object + retry 2 (паттерн
# текущего кода). Фейл двух этапов → ValueError, как сейчас: process_new_document
# ловит generic except, файл НЕ помечен processed → ретрай следующим поллом.

# GOTCHA (E): markdeleted, НЕ disk.file.delete — корзина восстановима, Дмитрий
# сказал «файлы появятся снова» (мак пересоздаёт). Best-effort: try/except всё,
# лог, поллер не ронять. Файл уже скипнут ДО этого — удаление не влияет на
# ингест. В тестах FakeAsyncClient кидает AssertionError на неизвестный POST —
# добавить роут в тест скрытых файлов.

# GOTCHA: сеть WSL2 флапает — retry в _send не трогать; живой прогон на сервере.

# GOTCHA: тексты сотруднику/HR на русском, «ты» сотруднику, «вы» не смешивать.
```

## Implementation Blueprint

### List of tasks (в порядке выполнения)

```yaml
Task 1 (A) — MODIFY app/bitrix_bot.py — рассылка при активации:
  - импорт: from app.state_machine import (..., _last_known_role) и
    from app.state_machine import course_roles; from app.roles import ALL_STAFF
    (у bitrix_bot уже есть import app.roles-функций и process_message)
  - NEW _course_recipients(course) -> list[str]  (sync, для to_thread):
      done-гейт: get_sessions_by_user(uid) state DONE c course["id"] → скип
      busy-гейт: get_session(uid) is not None → скип (текущий курс/выбор роли)
      роль: _last_known_role(uid); croles = set(course_roles(course))
      match: (role and ({role, ALL_STAFF} & croles)) or (role is None and ALL_STAFF in croles)
  - NEW async _broadcast_course(course, uids):
      text = (f"📚 Тебе доступен новый курс: *{display_name(course['doc_name'])}*\n"
              "Напиши мне любое сообщение, чтобы начать обучение.")
      for uid in uids: await _send(f"u{uid}", text, BOT_ID)   # последовательно
  - hr_handler «подтвердить», ветка успеха (ok=True):
      uids = await asyncio.to_thread(_course_recipients, course)
      asyncio.create_task(_broadcast_course(course, uids))
      text = (f"✅ Курс «{course['doc_name']}» активирован. "
              f"Уведомляю {len(uids)} сотрудников.")
      (0 получателей → «...активирован. Подходящих сотрудников для уведомления
      нет.» — честно, не пугает)

Task 2 (B) — MODIFY app/state_machine.py — «Мои курсы» и подсказки:
  - NEW _my_courses_text(user_id, role_id) -> str:
      courses = get_active_courses(); done = _done_course_ids(user_id)
      current = get_session(user_id) — course_id текущей сессии
      mine = [c for c in courses if role_id is None or
              {role_id, ALL_STAFF} & set(course_roles(c))]
      пусто → "Для твоей роли пока нет курсов."
      строки: ✅ display_name (пройден) / ▶️ display_name (текущий —
      напиши «Готов» для теста) / ⏳ display_name (будет предложен после
      текущего)
  - _handle_reading: ПЕРЕД веткой «роль»:
      if message.strip().lower() in ("мои курсы", "курсы", "меню"):
          return _my_courses_text(session["user_id"], session.get("role"))
  - _start_reading: последней строкой:
      "Команды: *Готов* — начать тест · *Мои курсы* — список курсов · "
      "*Роль* — сменить роль."
  - _handle_waiting_hr: добавить "…Посмотреть остальные курсы: *Мои курсы*."
      (WAITING_HR: интерсепт «мои курсы» добавить и сюда — состояние ждёт HR,
      любой текст сейчас отвечает заглушкой)

Task 3 (C) — должность и человеческие подписи:
  - app/db.py: init_db → _ensure_column(conn, "employees", "work_position", "TEXT");
      add_employee(..., work_position: str = None) — в INSERT и COALESCE-upsert
  - app/bitrix_bot.py ветка «пригласить»: add_employee(..., work_position=
      (user.get("WORK_POSITION") or "").strip() or None)
  - app/hr_tools.py build_report_text: label =
      "ФИО (должность) · РольРус (email)" — должность/роль опускать, если пусто;
      роль: словарь role_names передать параметром ИЛИ вызвать role_name из
      app.roles (импорт уже есть цепочкой; hr_tools чистый — role_name читает
      конфиг с диска, сети нет — допустимо, зафиксировать в докстринге)
      role берётся из r["role"] (get_report_rows отдаёт s.*)
  - app/bitrix_bot.py «история»: label = f"{full_name} ({email})" расширить
      должностью из emp["work_position"]
  - app/state_machine.py _finish_phase: оба notify_hr —
      emp = get_employee(session["user_id"]);
      who = f"{emp['full_name']} (ID: {uid})" если есть имя, иначе как сейчас;
      команда «Допустить {user_id}» НЕ меняется (парсер по ID/email)

Task 4 (D) — MODIFY app/course_generator.py — 2 этапа:
  - NEW FACTS_PROMPT: «извлеки РОВНО 15 ключевых положений документа:
      facts_basic (5 простых фактов/правил), facts_exam (10 — процессы,
      алгоритмы, исключения), course_summary (2-3 предложения). JSON.»
  - NEW QUESTIONS_FROM_FACTS_PROMPT: «для КАЖДОГО факта составь вопрос с 4
      вариантами A-D, correct, explanation со ссылкой на стандарт. Неверные
      варианты — правдоподобные, из той же области. Схема как сейчас.»
  - generate_questions (сигнатура та же):
      _llm_json(client, system, user, max_tokens) — общий хелпер с retry 2
      facts = _llm_json(FACTS_PROMPT, контекст чанков, 1200);
        валидация: 5/10 строк, иначе retry внутри хелпера по ValueError
      result = _llm_json(QUESTIONS_FROM_FACTS_PROMPT,
        f"Документ: {doc_name}\nФакты: {json.dumps(facts)}\n\nКонтекст: {context}", 3000)
      result["doc_name"]=doc_name; result["course_summary"]=facts["course_summary"]
      _validate_questions(result); return result
  - temperature 0.3, response_format json_object — как сейчас

Task 5 (E) — MODIFY app/bitrix_bot.py — корзина для мусора:
  - NEW async _trash_junk_file(file_id, file_name):
      try: async with httpx.AsyncClient(timeout=30.0) as client:
          await client.post(BITRIX_WEBHOOK_URL + "disk.file.markdeleted",
                            json={"id": file_id})
          print(f"[poller] junk → корзина: {file_name!r}")
      except Exception as exc: print(f"[poller] junk delete failed ...")
  - _walk_folder, ветка скипа скрытых: asyncio.create_task(
      _trash_junk_file(file_id, file_name)) — только если file_id

Task 6 — тесты:
  - NEW tests/test_course_broadcast.py (паттерн test_hr_invite):
      env: 3 employees — «600» с DONE-сессией роли reservations (свободен),
      «700» с активной сессией (busy), «800» без сессий (роль неизвестна);
      курс «RES Брони.docx» черновик → «Подтвердить {id}»:
      * sent содержит u600 от BOT_ID с «доступен новый курс», БЕЗ u700/u800
      * HR-ответ содержит «Уведомляю 1»
      * курс «ALL Общий.docx» → u600 И u800 (роль неизвестна, ALL — да), не u700
      * повторное «Подтвердить» того же — рассылка снова (осознанно: HR
        может переактивировать; зафиксировать поведением теста)
  - tests/test_state_machine.py:
      * READING «Мои курсы» → в ответе ✅/▶️/⏳-строки, rag_answer НЕ вызван
        (captured пуст); «какие курсы по уборке?» → уходит в RAG (MOCK_ANSWER)
      * _finish_phase: notify_hr замокать, добавить employee с full_name →
        в тексте ФИО
  - NEW tests/test_course_generator.py:
      * fake OpenAI (monkeypatch course_generator.OpenAI): очередь из 2
        ответов — факты, потом вопросы; проверить 2 вызова, итоговая схема
        валидна, doc_name/course_summary на месте
      * первый ответ битый JSON → ретрай (3 вызова всего), успех
  - tests/test_folder_sync.py:
      * test_hidden_files_skipped_in_walk: добавить роут
        "disk.file.markdeleted" (собирать вызовы) → вызван для «9» и «10»,
        НЕ для «11»; ингест-поведение не изменилось
  - tests/test_db.py: work_position колонка мигрируется, add_employee upsert
      не затирает должность None-ом
  - tests/test_hr_tools.py: build_report_text с work_position/role в строке

Task 7 — task.md: блок «Демо-фидбек 05.08» — чеклист живого прогона
  (рассылка реальному сотруднику, «Мои курсы» с телефона, вопросы нового
  генератора показать Дмитрию, ._* исчезли из папки в корзину)
```

### Per task pseudocode (ключевые места)

```python
# Task 1 — bitrix_bot.py::_course_recipients (sync, звать через to_thread)
def _course_recipients(course: dict) -> list[str]:
    croles = set(course_roles(course))
    uids = []
    for e in get_all_employees():
        uid = e["bitrix_uid"]
        if get_session(uid):                       # busy: текущий курс/выбор роли
            continue
        done = {s["course_id"] for s in get_sessions_by_user(uid)
                if s["state"] == "DONE"}
        if course["id"] in done:
            continue
        role = _last_known_role(uid)
        if role and ({role, ALL_STAFF} & croles):
            uids.append(uid)
        elif role is None and ALL_STAFF in croles:  # без роли — только ALL-курсы
            uids.append(uid)
    return uids

# Task 4 — course_generator.py::_llm_json (общий retry-хелпер)
def _llm_json(client, system: str, user: str, max_tokens: int,
              validate=None) -> dict:
    for attempt in range(2):
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0.3, max_tokens=max_tokens,
        )
        try:
            result = json.loads(response.choices[0].message.content)
            if validate:
                validate(result)
            return result
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[course_generator] attempt {attempt + 1} failed: {e}")
            if attempt == 1:
                raise ValueError(f"LLM invalid JSON after 2 attempts: {e}") from e
```

### Integration Points
```yaml
DATABASE: _ensure_column(employees, work_position TEXT) — миграция на старте
CONFIG:   ничего нового в .env; OPENAI_MODEL уже есть
ROUTES:   без новых эндпоинтов; меняется «/hr подтвердить», FSM, поллер
DEPLOY:   после мержа — обычный рестарт; отдельных миграций не нужно
```

## Validation Loop

### Level 1: Syntax & Style
```bash
ruff check app/ scripts/ tests/ --fix     # mypy в репо не настроен — не вводить
```

### Level 2: Unit Tests
```bash
python -m pytest tests/ -v
# Новое: broadcast (3 сценария получателей), «Мои курсы» vs RAG, 2-этапный
# генератор с мок-клиентом, markdeleted-роут, work_position, ФИО в notify_hr.
# Все 118 существующих остаются зелёными.
```

### Level 3: Живая проверка генератора (нужен OPENAI_API_KEY, по желанию)
```bash
python - <<'EOF'
from app.rag import load_index
from app.course_generator import generate_questions
ch, _ = load_index()
doc = [c for c in ch if c.get("doc_name", "").startswith("FO, RES, SAL")][:15]
q = generate_questions("FO, RES, SAL Негарантированные бронирования.docx", doc)
print(q["course_summary"]); print(q["basic_questions"][0])
EOF
# Глазами: вопросы осмысленнее одноэтапных? Это же показать Дмитрию (обещано).
```

### Level 4: Сервер (чеклист task.md)
- «Подтвердить» тестового курса → уведомление реальному сотруднику от employee-бота
- «Мои курсы» с телефона; ._* в папке → корзина Диска (лог поллера)

## Final validation Checklist
- [ ] pytest зелёный (118 + новые), ruff чистый
- [ ] Рассылка: только подходящие/свободные/непрошедшие; от BOT_ID; HR видит счётчик
- [ ] «Мои курсы» не перехватывает свободные вопросы (только exact match)
- [ ] Отчёт/История/notify_hr: ФИО (должность) · роль; «Допустить {id}» с ID
- [ ] generate_questions: 2 вызова, схема прежняя, ретраи работают
- [ ] Мусор в корзину best-effort, поллер не падает при ошибке
- [ ] Все тексты на русском, ensure_ascii=False

## Anti-Patterns to Avoid
- ❌ НЕ слать рассылку от HR-бота и не передавать client_id формы /hr
- ❌ НЕ дублировать pick/roles-логику №11 — импортировать из state_machine
- ❌ НЕ менять сигнатуру generate_questions и схему questions_json
- ❌ НЕ делать substring-матч команд — только exact после strip().lower()
- ❌ НЕ использовать disk.file.delete — только markdeleted (корзина)
- ❌ НЕ блокировать ответ HR ожиданием K отправок с ретраями
- ❌ НЕ трогать №9/№8/№7/№10 — отдельные PRP

## Открытые допущения (проговорить с юзером/клиентом)
1. Повторное «Подтвердить» уже активного курса шлёт рассылку повторно —
   считаем фичей (HR может «переанонсировать»); дешёвого гейта нет, спам
   ограничен ручным действием HR.
2. Сотрудник без роли получает только ALL-курсы — до №8 (роль из отдела)
   это осознанное ограничение, иначе спам не тем людям.
3. «Отдел» в отчёте = роль сотрудника (аббревиатуры клиента и есть отделы);
   настоящий отдел из UF_DEPARTMENT появится с №8.
4. Качество 2-этапного генератора проверяется глазами на живом документе
   (Level 3) — промпт, вероятно, потребует 1-2 итерации после показа Дмитрию.

## Score: 8/10
Контекст полный: точки правок с номерами строк, переиспользование №11-хелперов,
тест-паттерны /hr и FakeAsyncClient описаны, схема генератора зафиксирована.
Минус балл: LLM-качество (D) не проверяется офлайн — мок гарантирует только
пайплайн; минус: живое «бот пишет первым» для рассылки massively зависит от
серверного окружения (BOT_CLIENT_ID) — как и весь №2, проверяется на сервере.
