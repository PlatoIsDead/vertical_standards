name: "Доработка №9 — Пересдача экзамена + эскалация руководителю (реестр email)"
description: |

## Purpose
Пересдача несданного экзамена по команде сотрудника + эскалация руководителям
через 7 дней после назначения курса, если экзамен не сдан. Руководители —
ручной реестр email-ов, управляемый HR. Канал доставки — чат Bitrix
(email-рассылка отложена: SMTP-кредов нет).

## Core Principles
1. **Context is King**: всё нужное — в этом файле + указанные файлы репо
2. **Validation Loops**: ruff + pytest, гоняются после каждой задачи
3. **Information Dense**: имена функций/таблиц — ровно как в кодовой базе
4. **Progressive Success**: сначала чистые функции, потом БД, потом обвязка
5. **Global rules**: CLAUDE.md — спрашивать, не выдумывать, не трогать чужой код

---

## Goal

1. Сотрудник, не сдавший экзамен (<70%), может написать «Пересдать» и пройти
   экзамен заново (базовый тест не пересдаётся, попытки не ограничены).
2. Реестр руководителей: таблица `managers` (email), сид
   `n.sharapov@proptech.digital`; HR-команды «Руководители» /
   «Руководитель добавить {email}» / «Руководитель удалить {email}».
3. Ежечасный луп: сессии старше `ESCALATION_DAYS` (дефолт 7) дней, где экзамен
   не сдан (не начал / застрял / провалил — неважно), → ОДНО сводное сообщение
   каждому руководителю в чат Bitrix. Одна эскалация на (user_id, course_id),
   повторов нет.

## Why

- Фидбек Дмитрия (клиент): контроль прохождения — руководители должны узнавать
  о «зависших» сотрудниках; сейчас при «❌ Не сдан» уведомляется только HR.
- Пересдача — без неё эскалация «не сдал» повисает: руководитель узнал,
  а сотрудник сделать ничего не может (DONE финален).
- Решения Никиты 2026-07-24 (зафиксированы в INITIAL.md FEATURE 9): порог 70%
  остаётся; руководители вручную email-ами (НЕ `department.get`/`UF_HEAD` —
  отменено); единственный триггер = 7 дней + не сдан; напоминаний сотруднику НЕТ.

## What

### Поведение сотрудника
- Провалил экзамен → в финальном сообщении появляется строка:
  «Напиши *Пересдать*, чтобы пройти экзамен ещё раз.»
- Пишет «Пересдать» (регистронезависимо, допускаем «Пересдача») →
  экзамен стартует заново с вопроса 1; старые exam-ответы стёрты; базовый
  балл сохранён.
- Пишет «Пересдать», когда экзамен сдан → «Экзамен уже сдан (X/Y)».
- Пишет «Пересдать» без завершённого курса → подсказка начать обучение.

### Поведение HR
- «Руководители» → список email-ов реестра.
- «Руководитель добавить ivanov@x.ru» → в реестр (дубль → сообщение).
- «Руководитель удалить ivanov@x.ru» → из реестра (нет такого → сообщение).
- Help-текст (ветка `else`) пополнен.

### Поведение системы
- Ежечасно: непройденные курсы старше 7 дней → одно сводное сообщение всем
  руководителям от HR-бота. Руководитель не найден на портале → та же сводка
  уходит HR с пометкой. Эскалация помечается отправленной один раз.
- При деплое существующие старые сессии НЕ эскалируются пачкой
  (одноразовый сид, meta-флаг).

### Success Criteria
- [ ] Ретейк: DONE-сессия с проваленным экзаменом → «Пересдать» → state=EXAM,
      q_idx=0, score_exam=0, exam-ответы удалены, первый вопрос показан
- [ ] Эскалация: сессия старше 7 дней + не сдан → одно сообщение руководителю;
      второй прогон лупа НЕ шлёт повторно
- [ ] HR-команды реестра работают, гейт /hr действует, help обновлён
- [ ] Все существующие тесты зелёные + новые; `ruff check app tests` чисто

## All Needed Context

### Documentation & References
```yaml
- file: INITIAL.md
  why: FEATURE 9 — утверждённый скоуп и решения Никиты 24-07 (вкл. ловушки)

- file: app/state_machine.py
  why: |
    ВЕСЬ FSM. Критично: get_session (db.py) возвращает только state != 'DONE' —
    после экзамена следующее сообщение попадает в ветку `session is None`
    (строка ~44), которая СОЗДАЁТ НОВУЮ СЕССИЮ любому whitelisted-юзеру.
    Перехват «Пересдать» — строго в этой ветке, ДО create_session.
    _finish_phase (строка ~230): порог passed = correct_count >= round(total*0.7);
    сюда добавляется строка про пересдачу при not passed.
    notify_hr (строка ~335) — sync-паттерн отправки с retry (НЕ трогать).
    format_question (строка ~299) — переиспользовать для первого вопроса ретейка.

- file: app/bitrix_bot.py
  why: |
    hr_handler (строка ~512): паттерн elif-веток команд; _HR_COMMAND_PREFIXES
    (строка ~95) — ОБЯЗАТЕЛЬНО добавить новые префиксы, иначе живая
    pending-правка (№3) перехватит «Руководитель …» как текст замены вопроса.
    _bitrix_user_by_email (строка ~337) — резолв email→user, переиспользовать
    для руководителей. _send (строка ~431) — async-отправка: при пустом
    client_id сама подставляет HR_CLIENT_ID/BOT_CLIENT_ID (проактив).
    _weekly_rating_loop (строка ~133) + start_disk_poller (строка ~125) —
    образец нового лупа и его регистрации.
    _extract_email (строка ~309), _hr_ids (строка ~305).

- file: app/gamification.py
  why: |
    Образец «чистые функции + луп»: build_weekly_rating сравнивает naive-UTC
    ISO-строки лексикографически (cutoff = (now-7d).isoformat()) — ровно так же
    сравнивать started_at в эскалации. Формула сдачи там же:
    score_exam < round(exam_total * 0.7). maybe_post_weekly_rating —
    идемпотентность через meta.

- file: app/hr_tools.py
  why: |
    _session_status (строка ~139) — русские статусы этапов («изучает материал»,
    «ждёт допуска…», «❌ не сдан») — импортировать для текста эскалации,
    НЕ дублировать. build_report_text — паттерн label сотрудника
    (full_name/email/ID). Импорт hr_tools из нового модуля цикла не создаёт.

- file: app/db.py
  why: |
    init_db + _ensure_column + сид-паттерны (processed_files из courses,
    employees из sessions — INSERT OR IGNORE). get_report_rows (строка ~304) —
    основной источник строк для эскалации (s.* + doc_name + questions_json);
    добавить c.archived_at в SELECT. update_session (строка ~256) — проверки
    `is not None`, поэтому score_exam=0 корректно записывается.
    get_session — фильтр state != 'DONE' (причина перехвата ретейка).
    meta: get_meta/set_meta — для одноразового сида эскалаций.

- file: tests/test_hr_invite.py
  why: |
    ЭТАЛОН теста hr_handler: TestClient, fixture env (DB_PATH через
    monkeypatch ДО init_db, roles.json во tmp_path, HR_USER_IDS=9,
    bot._recent_msgs.clear() — дедуп переживает тесты!), fake _send со сбором
    sent, _wait_for-поллинг create_task-ответов, _post_hr helper.

- file: tests/test_gamification.py
  why: паттерн тестов чистых функций расписания/текстов (без сети)

- file: tests/test_state_machine.py
  why: паттерн тестов FSM (monkeypatch DB_PATH, notify_hr; sync-вызовы)

- url: https://apidocs.bitrix24.ru/api-reference/chat-bots/messages/imbot-message-add.html
  why: imbot.message.add — параметры BOT_ID/DIALOG_ID/MESSAGE/CLIENT_ID (уже используется, ничего нового не нужно)

- url: https://apidocs.bitrix24.ru/api-reference/user/user-get.html
  why: user.get фильтр по EMAIL — уже реализован в _bitrix_user_by_email
```

### Current Codebase tree (релевантная часть)
```bash
app/
├── bitrix_bot.py        # FastAPI: /handler, /hr, поллеры, лупы, _send
├── state_machine.py     # FSM сотрудника, notify_hr
├── db.py                # SQLite: courses/sessions/answers/employees/meta…
├── hr_tools.py          # чистые функции HR (№3): _session_status и др.
├── gamification.py      # №5: чистые функции + недельный рейтинг
├── course_generator.py, doc_parsers.py, index_store.py, rag.py, roles.py
tests/
├── test_hr_invite.py    # эталон TestClient-тестов /hr
├── test_gamification.py # эталон тестов чистых функций
├── test_state_machine.py, test_hr_tools.py, test_hr_edit_flow.py, …
onboarding.db            # ЖИВАЯ БД — тесты обязаны подменять DB_PATH
```

### Desired Codebase tree
```bash
app/
├── escalation.py        # НОВЫЙ: чистые функции — find_due_escalations,
│                        #   build_escalation_message (без сети и БД)
├── db.py                # + managers, escalations, delete_session_answers,
│                        #   get_last_done_session, сиды
├── state_machine.py     # + перехват «Пересдать», + строка в _finish_phase
├── bitrix_bot.py        # + ветка «руководител…», + _escalation_loop
tests/
├── test_escalation.py   # НОВЫЙ: чистые функции эскалации
├── test_retake.py       # НОВЫЙ: FSM-ретейк
├── test_hr_managers.py  # НОВЫЙ: HR-команды реестра (TestClient)
```

### Known Gotchas
```python
# КРИТИЧНО: get_session() отдаёт только state != 'DONE'. После экзамена
# следующее сообщение сотрудника идёт в ветку `session is None`, которая
# СОЗДАЁТ НОВУЮ СЕССИЮ (get_active_courses()[0]). Перехват «Пересдать» —
# ДО этого создания, иначе сотрудник молча начнёт курс с нуля.
# (Попутная находка: _handle_done фактически недостижим — get_session никогда
# не вернёт DONE. НЕ чинить в этом PRP, только знать.)

# КРИТИЧНО: update_session() пишет только поля `is not None` → score_exam=0
# проходит (0 is not None). Не «чинить» под falsy.

# КРИТИЧНО: _HR_COMMAND_PREFIXES — добавить "руководители", "руководитель",
# иначе живой pending правки вопроса (№3) съест команду как текст замены.

# КРИТИЧНО: сид эскалаций для легаси-сессий — ОДНОРАЗОВЫЙ через meta-флаг
# ('escalations_seeded'). Голый INSERT..SELECT в init_db() выполнялся бы при
# КАЖДОМ старте: сессия, пересёкшая 7-дневную границу между рестартами,
# получила бы 'seed' и никогда не эскалировалась.

# Время: started_at/updated_at = naive UTC ISO (datetime.utcnow().isoformat()).
# Сравнение — лексикографически со строкой cutoff, как в build_weekly_rating.
# НЕ мешать с МСК-логикой рейтинга (тут расписания по часам нет вообще).

# (user_id, course_id) может иметь НЕСКОЛЬКО сессий (старый DONE-провал +
# новая активная после «Добро пожаловать»-перезапуска). Судить только
# ПОСЛЕДНЮЮ (max id) сессию пары.

# exam_total == 0 (курс без экзаменационных вопросов — бывает в фикстурах,
# бывает у битого JSON) → сдачу оценить нельзя → пару ПРОПУСКАТЬ, не эскалировать.

# Курс archived (archived_at != NULL) → не эскалировать: документ удалён,
# доучиться нельзя. get_report_rows нужно расширить полем c.archived_at.

# Транзиентный сбой Bitrix при резолве руководителя (httpx.HTTPError) →
# ПРОПУСТИТЬ ВЕСЬ ЦИКЛ без mark_escalated (следующий час доставит).
# Руководитель НЕ НАЙДЕН (user.get вернул пусто — это НЕ сбой) → сводку
# слать HR с пометкой, эскалации ПОМЕТИТЬ (доставлено «куда-то» ровно раз).

# import app.bitrix_bot исполняет init_db() + load_index() на живых файлах —
# в тестах monkeypatch db.DB_PATH ДО первой записи (паттерн test_hr_invite.py).
# bot._recent_msgs.clear() в каждой фикстуре — дедуп переживает тесты.

# _send() сама подставляет HR_CLIENT_ID при bot_id == HR_BOT_ID (проактив без
# входящего вебхука; без CLIENT_ID Bitrix → 403). Слать руководителям от
# HR_BOT_ID. Живой прогон first-contact — в чеклист сдачи, не в тесты.

# Эмодзи-ловушка из №5: `"" in "🥇🥈🥉•"` == True — в assert'ах не проверять
# принадлежность подстроки к строке эмодзи без гарантии непустоты.
```

## Implementation Blueprint

### Data models and structure

```sql
-- app/db.py :: init_db() executescript, после meta:
CREATE TABLE IF NOT EXISTS managers (
    email    TEXT PRIMARY KEY,          -- нормализован: strip().lower()
    added_by TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS escalations (
    user_id     TEXT NOT NULL,
    course_id   INTEGER NOT NULL,
    notified_at TEXT,
    PRIMARY KEY (user_id, course_id)
);
```

```python
# Сиды в init_db() (после существующих):
# 1) Реестр руководителей — временный email Никиты (клиент заменит через HR-команды):
conn.execute("INSERT OR IGNORE INTO managers (email, added_by) VALUES (?, 'seed')",
             ("n.sharapov@proptech.digital",))
# 2) ОДНОРАЗОВЫЙ тихий сид эскалаций (легаси-сессии не эскалировать пачкой).
#    НЕ через executescript — нужен meta-гейт:
if conn.execute("SELECT value FROM meta WHERE key='escalations_seeded'").fetchone() is None:
    conn.execute("""INSERT OR IGNORE INTO escalations (user_id, course_id, notified_at)
                    SELECT user_id, course_id, 'seed' FROM sessions""")
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('escalations_seeded','1')")
# Сидим ВСЕ существующие пары (не только старше 7 дней): на момент деплоя
# история доверия к срокам нулевая, эскалируем только НОВОЕ. Просто и предсказуемо.
```

### Список задач (по порядку)

```yaml
Task 1 — app/db.py (фундамент):
  MODIFY init_db():
    - две таблицы + два сида (см. выше; сид эскалаций с meta-гейтом)
  CREATE функции (секции по образцу «── Employees…»):
    - add_manager(email, added_by) -> bool      # False = уже был (INSERT OR IGNORE, rowcount)
    - remove_manager(email) -> bool             # rowcount > 0
    - get_managers() -> list[dict]              # ORDER BY added_at
    - get_escalated_pairs() -> set[tuple[str, int]]
    - mark_escalated(user_id, course_id)        # INSERT OR IGNORE + notified_at=utcnow
    - delete_session_answers(session_id, phase) # DELETE FROM answers WHERE …
    - get_last_done_session(user_id) -> dict | None
        # SELECT * FROM sessions WHERE user_id=? AND state='DONE' ORDER BY id DESC LIMIT 1
  MODIFY get_report_rows():
    - в SELECT добавить c.archived_at (обратная совместимость: dict-строки)

Task 2 — app/escalation.py (НОВЫЙ, чистый — ни сети, ни БД):
  - ESCALATION_DAYS читает bitrix_bot, сюда передаётся параметром
  - find_due_escalations(rows, already, now_utc, days) -> list[dict]
  - build_escalation_message(due, employees_by_uid) -> str | None
  - Формула сдачи — round(exam_total * 0.7), КАК в _finish_phase; статус —
    импорт _session_status из app.hr_tools (не дублировать)

Task 3 — app/state_machine.py (пересдача):
  MODIFY process_message(), ветка session is None:
    - ПОСЛЕ whitelist-гейта, ДО get_active_courses(): перехват
      message.strip().lower() in ("пересдать", "пересдача") → _handle_retake(user_id)
  CREATE _handle_retake(user_id) -> str
  MODIFY _finish_phase(), exam-ветка, при not passed:
    - к финальному сообщению добавить строку про «Пересдать»

Task 4 — app/bitrix_bot.py (HR-команды + луп):
  MODIFY _HR_COMMAND_PREFIXES: + "руководители", "руководитель"
  ADD elif msg_lower.startswith("руководител") в hr_handler
      (ПЕРЕД веткой else, после «отчёт»)
  MODIFY help-текст (else): + 2 строки команд
  ADD ESCALATION_CHECK_INTERVAL = 3600, ESCALATION_DAYS = int(os.getenv("ESCALATION_DAYS", "7"))
  ADD _escalation_loop() + _check_escalations(); регистрация в start_disk_poller()
      (глобальная _escalation_task — как _rating_task)

Task 5 — тесты:
  CREATE tests/test_escalation.py   # чистые функции (образец test_gamification.py)
  CREATE tests/test_retake.py       # FSM (образец test_state_machine.py)
  CREATE tests/test_hr_managers.py  # TestClient (образец test_hr_invite.py)
  + в test_db.py при желании: managers/escalations/сиды (или покрыть в трёх выше)

Task 6 — task.md:
  ADD чеклист живого прогона №9 (см. Final checklist)
```

### Per-task pseudocode

```python
# ── Task 2: app/escalation.py ────────────────────────────────────────────────
"""Чистые функции эскалации (№9): кто «завис» дольше N дней — тем руководителям.
Времена naive UTC ISO-строками, как пишет БД (см. gamification: тот же приём)."""

def find_due_escalations(rows: list[dict], already: set[tuple[str, int]],
                         now_utc: datetime, days: int = 7) -> list[dict]:
    cutoff = (now_utc - timedelta(days=days)).isoformat()
    # 1) последняя сессия каждой пары (user_id, course_id) — max id
    latest: dict[tuple[str, int], dict] = {}
    for r in rows:  # rows = get_report_rows(): s.* + doc_name + questions_json + archived_at
        key = (str(r["user_id"]), r["course_id"])
        if key not in latest or r["id"] > latest[key]["id"]:
            latest[key] = r
    due = []
    for key, r in latest.items():
        if key in already or r.get("archived_at"):
            continue
        if (r.get("started_at") or "") >= cutoff:   # моложе N дней
            continue
        questions = _safe_json(r.get("questions_json"))     # как в build_weekly_rating
        exam_total = len(questions.get("exam_questions", []))
        if not exam_total:                                   # судить нечем — пропуск
            continue
        passed = (r["state"] == "DONE"
                  and r["score_exam"] >= round(exam_total * 0.7))  # ЕДИНСТВЕННАЯ формула
        if not passed:
            due.append(r)
    due.sort(key=lambda r: r.get("started_at") or "")
    return due

def build_escalation_message(due, employees_by_uid) -> str | None:
    if not due: return None
    # label — паттерн build_report_text; статус — hr_tools._session_status(r, questions)
    # «⚠️ Обучение не пройдено за 7 дней:\n• {label} — «{doc_name}»: {status}\n…»
    # + хвост: «Сотрудник может пересдать экзамен командой Пересдать.»


# ── Task 3: state_machine._handle_retake ────────────────────────────────────
def _handle_retake(user_id: str) -> str:
    last = get_last_done_session(user_id)           # None → нечего пересдавать
    if last is None:
        return "У тебя нет завершённого экзамена. Напиши любое сообщение, чтобы начать обучение."
    questions = get_course_questions(last["course_id"])
    exam_q = questions.get("exam_questions", [])
    if not exam_q:
        return "Ошибка: вопросы курса не найдены. Обратитесь к HR."
    if last["score_exam"] >= round(len(exam_q) * 0.7):       # та же формула
        return f"🎉 Экзамен уже сдан ({last['score_exam']}/{len(exam_q)}). Пересдача не нужна."
    # ЛОВУШКА answers: get_session_answers в _finish_phase считает ВСЕ строки
    # фазы — без удаления старых ответов correct_count схлопнул бы две попытки.
    delete_session_answers(last["id"], "exam")
    update_session(last["id"], state="EXAM", q_idx=0, score_exam=0)  # 0 проходит: is not None
    return ("🔁 Пересдача экзамена — вперёд!\n\n"
            + format_question(exam_q[0], 0, len(exam_q), "exam"))
# В process_message ветка session is None:
#   whitelist-гейт (как есть) → if msg in ("пересдать","пересдача"): return _handle_retake(...)
#   → дальше существующий код создания сессии.
# score_basic НЕ трогать. dialog_id сессии сохраняется старый — ок, это тот же u{id}.


# ── Task 4: ветка «руководител…» в hr_handler ───────────────────────────────
elif msg_lower.startswith("руководител"):
    parts = question.split()
    email = _extract_email(question)
    if len(parts) == 1:                              # «Руководители»
        managers = await asyncio.to_thread(get_managers)
        text = ("👥 Руководители (получают эскалации):\n"
                + "\n".join(f"• {m['email']}" for m in managers)
                if managers else "Реестр руководителей пуст.")
    elif len(parts) >= 2 and parts[1].lower() == "добавить" and email:
        added = await asyncio.to_thread(add_manager, email, user_id)
        text = f"✅ {email} добавлен." if added else f"ℹ️ {email} уже в списке."
    elif len(parts) >= 2 and parts[1].lower() == "удалить" and email:
        removed = await asyncio.to_thread(remove_manager, email)
        text = f"✅ {email} удалён." if removed else f"⚠️ {email} не найден в списке."
    else:
        text = ("❌ Формат: Руководители | Руководитель добавить {email} | "
                "Руководитель удалить {email}")


# ── Task 4: луп ──────────────────────────────────────────────────────────────
async def _escalation_loop():
    print(f"[escalation] Started — {ESCALATION_DAYS}d, check hourly")
    while True:
        await asyncio.sleep(ESCALATION_CHECK_INTERVAL)
        try:
            await _check_escalations()
        except Exception as exc:
            print(f"[escalation] ERROR: {exc!r}")

async def _check_escalations():
    rows = await asyncio.to_thread(get_report_rows)
    already = await asyncio.to_thread(get_escalated_pairs)
    due = escalation.find_due_escalations(rows, already, datetime.utcnow(),
                                          ESCALATION_DAYS)
    if not due:
        return
    employees = {e["bitrix_uid"]: e for e in await asyncio.to_thread(get_all_employees)}
    text = escalation.build_escalation_message(due, employees)
    managers = await asyncio.to_thread(get_managers)
    resolved, missing = [], []
    for m in managers:
        # httpx.HTTPError здесь НЕ ловить точечно: транзиентный сбой должен
        # прервать ВЕСЬ цикл (исключение уйдёт в _escalation_loop) — эскалации
        # не помечены, следующий час доставит.
        user = await _bitrix_user_by_email(m["email"])
        (resolved if user else missing).append((m["email"], user))
    for _email, user in resolved:
        await _send(f"u{user['ID']}", text, HR_BOT_ID)   # проактив: HR_CLIENT_ID из _send
    if missing or not resolved:                          # фолбэк — HR не слепнет
        note = ("⚠️ Не найдены на портале: "
                + ", ".join(e for e, _ in missing) + "\n\n" if missing else "")
        for hr_id in _hr_ids():
            await _send(str(hr_id), note + text, HR_BOT_ID)
    for r in due:                                        # помечаем ПОСЛЕ доставки
        await asyncio.to_thread(mark_escalated, str(r["user_id"]), r["course_id"])
```

### Integration Points
```yaml
DATABASE:
  - init_db(): managers + escalations + сид менеджера + одноразовый сид эскалаций
  - get_report_rows(): + c.archived_at в SELECT
CONFIG (.env, дефолты в коде — ничего обязательного):
  - ESCALATION_DAYS=7  # срок до эскалации, дней
STARTUP:
  - start_disk_poller(): _escalation_task = asyncio.create_task(_escalation_loop())
HELP:
  - hr_handler else-ветка: «Руководители», «Руководитель добавить/удалить {email}»
TASK.MD:
  - чеклист живого прогона №9
```

## Validation Loop

### Level 1: Syntax & Style
```bash
source .venv/bin/activate 2>/dev/null || true   # venv проекта, как обычно
ruff check app tests
# Ожидание: чисто. mypy в проекте не гоняется — не вводить.
```

### Level 2: Unit Tests
```python
# tests/test_escalation.py — чистые (образец test_gamification.py):
#  - not_started_old_session_due: state=READING, started 8 дней назад → due
#  - failed_done_due: DONE, score_exam=2/10 → due
#  - passed_not_due: DONE, 8/10 → нет
#  - fresh_not_due: started 2 дня назад → нет
#  - already_escalated_skipped: пара в already → нет
#  - latest_session_wins: старый DONE-провал + свежая активная той же пары → нет
#  - archived_skipped, zero_exam_total_skipped
#  - build_message: label из employees, статус из _session_status, None при пустом due

# tests/test_retake.py — FSM (образец test_state_machine.py: DB_PATH → tmp,
# monkeypatch notify_hr; сессия DONE с exam-ответами в фикстуре):
#  - retake_resets: «Пересдать» → EXAM/q0/score_exam=0, exam-answers пусто,
#    basic-answers целы, score_basic цел, в ответе «Вопрос 1»
#  - retake_after_pass_refused; retake_without_done_session — подсказка,
#    сессия НЕ создана; «пересдача» (синоним) работает
#  - fail_message_offers_retake: провал экзамена → в тексте «Пересдать»
#  - second_attempt_scores_clean: после ретейка ответить все вопросы → 
#    correct_count только новой попытки (старые ответы не схлопнулись)

# tests/test_hr_managers.py — TestClient (СКОПИРОВАТЬ каркас test_hr_invite.py:
# fixture env с DB_PATH/roles.json/HR_USER_IDS/_recent_msgs.clear()/fake _send):
#  - list_shows_seed: «Руководители» → n.sharapov@proptech.digital
#  - add/remove/duplicate/unknown; non_hr_rejected; формат-ошибка
#  - pending_edit_not_hijacked: живой pending правки + «Руководители» →
#    команда исполняется (префикс в _HR_COMMAND_PREFIXES)
```
```bash
python -m pytest tests/ -q
# Все существующие (95) + новые зелёные. Красный → читать, чинить код, не тест.
```

### Level 3: Integration (живой прогон — чеклист сдачи, НЕ гейт PRP)
```bash
# Сервер/ngrok (см. task.md «живой прогон» №2/№5 — тот же стенд):
# 1. Сотрудник проваливает экзамен → сообщение содержит «Пересдать»;
#    «Пересдать» → экзамен заново; сдача → поздравление в Ленту (№5 не сломан)
# 2. ESCALATION_DAYS=0 временно → луп шлёт сводку на u{uid Никиты}
#    (n.sharapov@proptech.digital резолвится user.get'ом) РОВНО ОДИН РАЗ
# 3. Первое сообщение руководителю доставилось (CLIENT_ID! — как автостарт №2)
# 4. HR: Руководители / добавить / удалить — живьём
```

## Final validation Checklist
- [ ] `python -m pytest tests/ -q` — все зелёные
- [ ] `ruff check app tests` — чисто
- [ ] Смоук на КОПИИ живой onboarding.db: init_db() создал managers/escalations,
      сид эскалаций покрыл существующие пары, meta escalations_seeded=1,
      повторный init_db() ничего не добавил
- [ ] Существующее поведение не тронуто: «Отчёт»/«История»/рейтинг №5 работают
      (get_report_rows расширен, не изменён)
- [ ] task.md пополнен чеклистом живого прогона №9
- [ ] В коде нет выдуманных порогов: везде round(total*0.7) и ESCALATION_DAYS

## Anti-Patterns to Avoid
- ❌ Не изобретать вторую формулу сдачи — только `round(total * 0.7)`
- ❌ Не эскалировать по `department.get`/`UF_HEAD` — путь ОТМЕНЁН клиентом
- ❌ Не слать напоминания сотруднику — Никита явно отказался
- ❌ Не помечать эскалацию при транзиентном сбое Bitrix — потеря навсегда
- ❌ Не сидировать эскалации без meta-гейта — сид при каждом старте глотает новые
- ❌ Не удалять basic-ответы при ретейке — пересдаётся только экзамен
- ❌ Не трогать _handle_done / «Добро пожаловать»-перезапуск — отдельная тема
- ❌ Не мокать живую БД в тестах «по месту» — только DB_PATH → tmp_path

---

## Score: 8/10

Уверенность в one-pass: высокая. Вся механика — на проверенных паттернах
репо (лупы №5, TestClient №2/№3, сиды init_db, _send с CLIENT_ID), ни одного
нового Bitrix-метода. Минус два балла: (1) перехват «Пересдать» сидит в самой
горячей ветке FSM (session is None) — легко нарушить порядок гейтов
(whitelist → retake → создание сессии); (2) первый проактивный контакт
с руководителем проверяется только живьём (CLIENT_ID/403 — как в №2).
