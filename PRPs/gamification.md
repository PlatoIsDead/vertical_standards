name: "Геймификация (доработка №5, 16ч/32 000₽)"
description: |
  «Благодарность» в Ленту новостей после сдачи экзамена + еженедельный рейтинг
  лучших по обучению. Последняя доработка пакета №2–№5; самая маленькая.

## Purpose
Одно-проходная реализация доработки №5. Требует выполненных №2 (employees — ФИО)
и №3 (get_report_rows — агрегат сессий). Один новый модуль app/gamification.py.

## Core Principles
1. **Context is King** — параметры и ограничения Ленты проверены по выжимке
2. **Validation Loops** — ruff + pytest (расписание/тексты/выборка — чистые функции)
3. **Information Dense** — имена из реального кода
4. **Progressive Success** — чистые функции → БД → хук экзамена → недельный луп
5. **Global rules** — CLAUDE.md; тексты русские; НИКОГДА не позорить отстающих

---

## Goal

Сотрудник сдал экзамен → в Ленте новостей портала появляется поздравление с его
ФИО и результатом. Раз в неделю (понедельник 10:00 МСК) бот публикует в Ленту
топ-5 «Лучшие по обучению за неделю». Рестарт сервера посты не задваивает.

## Why

- Смета №5: «сотрудники получают признание и видят себя в рейтинге».
- Публичное признание — нефинансовая мотивация (кейс «Миран» из data/referance/,
  реакция CEO) — единственная часть пакета, видимая ВСЕМ сотрудникам портала.

## What

1. **«Благодарность»**: хук в `_finish_phase` (экзамен, `passed` по существующей
   формуле ≥70%) → пост в Ленту через `log.blogpost.add`. ФИО из `employees`,
   фолбэк — «Сотрудник (ID N)». Фейл поста НЕ ломает завершение экзамена.
2. **Еженедельный рейтинг**: фоновый луп (по образцу поллеров) раз в час проверяет
   «пора ли»: понедельник ≥10:00 МСК и эта ISO-неделя ещё не публиковалась
   (метка в новой key-value таблице `meta`). Топ-5 СДАВШИХ за последние 7 дней
   по сумме баллов (базовый+экзамен). Никаких антирейтингов. Неделя без
   завершивших — пост не публикуется, но неделя помечается обработанной.
3. Идемпотентность: `meta["last_rating_week"] = "2026-W28"` — рестарты и
   ежечасные проверки не задваивают пост.

### Success Criteria
- [ ] Экзамен сдан → в Ленту ушёл пост с ФИО и баллами; экзамен НЕ сдан → поста нет
- [ ] Фейл log.blogpost.add (сеть/права) → экзамен завершается штатно, ошибка в логе
- [ ] Рейтинг: только сдавшие за 7 дней, топ-5 по сумме баллов, ФИО из employees
- [ ] Понедельник 09:59 МСК → не постим; 10:01 → постим; вторник после поста → не постим
- [ ] Рестарт после публикации → пост не дублируется (meta)
- [ ] Все существующие 84 теста зелёные; ruff чистый

## All Needed Context

### Documentation & References
```yaml
- docfile: data/referance/bitrix24_docs.md
  why: |
    ПРОВЕРЕНО (раздел «Лента новостей», ~строки 39440–39560):
    • log.blogpost.add — «добавляет сообщение в Ленту от имени ТЕКУЩЕГО
      пользователя», scope `log`, выполнять может любой пользователь. Для
      вебхука «текущий» = ВЛАДЕЛЕЦ вебхука — пост будет от имени Никиты/админа,
      не от бота. Это UX-факт для клиента, зафиксировать в planning.md.
    • Адресация: DEST по ID юзеров/групп/подразделений; «всем» = UA.
    • Детальной секции параметров log.blogpost.add в выжимке НЕТ (только
      log.blogpost.update: POST_ID/POST_TITLE/FILES) — параметры add по онлайн-доке.
    • НАХОДКА ВПРОК (не в скоупе): «важное сообщение» + log.blogpost.getusers.important
      = массовое информирование со списком прочитавших — готовый инструмент под
      претензию CEO о контроле ознакомления. Записать в planning.md как идею.
    • Спец-тип «Благодарность с медалью» в REST-выжимке НЕ упомянут — публикуем
      обычный пост с 🏆 (допущение №1).

- url: https://apidocs.bitrix24.com/api-reference/log/log-blogpost-add.html
  why: |
    Параметры log.blogpost.add: POST_MESSAGE (обязателен), POST_TITLE,
    DEST (массив, например ["UA"] — все сотрудники). Слать DEST явно.

- file: app/state_machine.py
  why: |
    _finish_phase — точка хука: ветка экзамена уже считает
    passed = correct_count >= round(total * 0.7) и зовёт notify_hr (sync httpx
    с retry 5×backoff, ошибки не роняют FSM) — тот же стиль для поста в Ленту.
    Вызов хука — ПОСЛЕ update_session(state="DONE") и notify_hr.
    session["user_id"] — uid для ФИО из employees.

- file: app/db.py
  why: |
    Паттерны: CREATE TABLE IF NOT EXISTS в init_db (новая таблица meta),
    get_report_rows() (№3) — sessions JOIN courses (doc_name, questions_json),
    свежие сверху — ПЕРЕИСПОЛЬЗОВАТЬ для рейтинга (фильтрация в чистой функции).
    get_all_employees() — map uid→ФИО. НЕТ get_employee(uid) — добавить.
    Даты в БД: datetime.utcnow().isoformat() (naive UTC) и DEFAULT datetime('now')
    (тоже UTC) — сравнение окна «7 дней» вести в naive UTC.

- file: app/hr_tools.py
  why: |
    _session_status — формула сдан/не сдан (score_exam >= round(exam_total*0.7));
    рейтинг обязан использовать ТУ ЖЕ (импортировать хелпер или продублировать
    формулу 1-в-1 с комментарием-ссылкой). Стиль чистых функций + тестов — образец.

- file: app/bitrix_bot.py
  why: |
    Образец фонового лупа: _user_poll_loop (startup-task, sleep, try/except внутри
    итерации). Новый _weekly_rating_loop — так же. BITRIX_WEBHOOK_URL — env.
    _hr_ids НЕ нужен — пост в Ленту, не в чат.

- file: tests/test_state_machine.py + tests/test_hr_tools.py
  why: образцы тестов FSM (fixture env, monkeypatch импортированных имён) и
    чистых функций. Хук тестировать через monkeypatch(sm, "post_exam_congratulation").
```

### Current Codebase tree (релевантная часть)
```bash
vertical_standards/
├── app/
│   ├── state_machine.py     # _finish_phase (passed-формула, notify_hr) ← хук
│   ├── bitrix_bot.py        # startup-таски поллеров ← + _weekly_rating_loop
│   ├── hr_tools.py          # _session_status (формула 70%)
│   └── db.py                # get_report_rows, get_all_employees, паттерны миграций
└── tests/                   # 84 теста
```

### Desired Codebase tree
```bash
├── app/
│   ├── gamification.py      # NEW: расписание (week_key/should_post), тексты
│   │                        #      (congrats/rating), post в Ленту, maybe_post_weekly
│   ├── state_machine.py     # MOD: вызов post_exam_congratulation при passed
│   ├── bitrix_bot.py        # MOD: _weekly_rating_loop + startup-task
│   └── db.py                # MOD: таблица meta (get_meta/set_meta), get_employee
└── tests/
    └── test_gamification.py # NEW: расписание/тексты/выборка/идемпотентность/хук
```

### Known Gotchas of our codebase & Library Quirks
```python
# CRITICAL: пост уходит от имени ВЛАДЕЛЬЦА вебхука (log.blogpost.add = «от имени
# текущего пользователя»). Не выдавать за пост «от бота»; сказать клиенту.
# Скоуп вебхука должен включать log — проверить живьём (как user для №2).

# CRITICAL: фейл поста НЕ должен ломать завершение экзамена. Вся отправка в
# try/except с print (паттерн notify_hr); retry 3×backoff достаточно.

# CRITICAL: идемпотентность рейтинга — meta["last_rating_week"] выставлять И когда
# постить нечего (неделя без завершивших): иначе луп будет пытаться каждый час.

# CRITICAL: два времени НЕ смешивать: расписание поста — МСК (UTC+3, БЕЗ DST:
# timezone(timedelta(hours=3))); окно «7 дней» по sessions.updated_at — naive UTC
# (так пишет БД: datetime.utcnow().isoformat() и DEFAULT datetime('now')).

# CRITICAL: формула «сдан» — score_exam >= round(exam_total * 0.7), КАК в
# _finish_phase/_session_status. exam_total = len(exam_questions) из questions_json
# (get_report_rows его отдаёт). В рейтинг — ТОЛЬКО сдавшие; не позорить.

# GOTCHA: чистые функции расписания принимают now параметром (datetime) —
# Date.now в тестах не мокается иначе. week_key по isocalendar():
# f"{y}-W{w:02d}". Понедельник 10:00 МСК: monday = now - timedelta(days=now.weekday()),
# replace(hour=10, minute=0, second=0, microsecond=0).

# GOTCHA: сортировка рейтинга по (score_basic + score_exam) убыв., при равенстве —
# по updated_at (кто раньше завершил, тот выше). Топ-5, «no silent caps» не нужен —
# рейтинг и есть топ.

# GOTCHA: state_machine._finish_phase — sync (в to_thread), поэтому
# post_exam_congratulation тоже sync (httpx.post, как notify_hr). Импорт
# в state_machine по имени → в тестах monkeypatch(sm, "post_exam_congratulation").

# GOTCHA: gamification НЕ импортирует state_machine/bitrix_bot (иначе цикл) —
# только db, httpx, os, datetime.

# GOTCHA: updated_at DONE-сессии = момент завершения экзамена (update_session
# ставит updated_at при каждом апдейте; после DONE сессию никто не трогает) —
# годится как «когда завершил».

# GOTCHA: ФИО: get_employee(uid) → full_name; NULL/нет записи (legacy) →
# «Сотрудник (ID {uid})» — рейтинг не роняем.

# GOTCHA: tests — clear meta не нужен (tmp DB per test); в тестах лупа не гонять
# asyncio-цикл, тестировать maybe_post_weekly_rating(now=...) напрямую.
```

## Implementation Blueprint

### Data models and structure

```python
# SQLite: key-value для служебных меток (init_db, CREATE TABLE IF NOT EXISTS)
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
# meta["last_rating_week"] = "2026-W28"

# app/gamification.py API:
MSK = timezone(timedelta(hours=3))
week_key(now: datetime) -> str                     # "2026-W28"
should_post_rating(now_msk, last_week: str | None) -> bool
build_congrats(fio, course_name, exam_score, exam_total,
               basic_score, basic_total) -> tuple[str, str]      # (title, message)
build_weekly_rating(rows, employees_by_uid, now_utc) -> tuple[str, str] | None
post_exam_congratulation(session: dict, questions: dict) -> None  # sync, глотает ошибки
maybe_post_weekly_rating(now_msk=None) -> bool     # True если запостили
_post_to_feed(title, message) -> bool              # log.blogpost.add, retry 3×
```

### List of tasks (в порядке выполнения)

```yaml
Task 1 — MODIFY app/db.py:
  - init_db: + CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)
  - NEW: get_meta(key) -> str | None; set_meta(key, value) (INSERT OR REPLACE);
         get_employee(bitrix_uid) -> dict | None

Task 2 — CREATE app/gamification.py:
  - MSK, week_key, monday_10 (приватная), should_post_rating:
      def should_post_rating(now_msk, last_week):
          return week_key(now_msk) != (last_week or "") and now_msk >= _monday_10(now_msk)
  - build_congrats(...) -> (title, message):
      title = "Благодарность за обучение"
      message = (f"🏆 {fio} успешно сдал(а) экзамен по курсу «{course_name}»!\n"
                 f"Результат: экзамен {exam_score}/{exam_total}, "
                 f"базовый тест {basic_score}/{basic_total}.\nПоздравляем! 🎉")
  - build_weekly_rating(rows, employees_by_uid, now_utc) -> (title, message) | None:
      cutoff = (now_utc - timedelta(days=7)).isoformat()
      done = [r for r in rows if r["state"] == "DONE" and (r.get("updated_at") or "") >= cutoff]
      # questions_json → exam_total; passed по формуле 70% (см. гочу); только сдавшие
      top = sorted(passed_rows, key=lambda r: (-(r["score_basic"] + r["score_exam"]),
                                               r["updated_at"]))[:5]
      if not top: return None
      строки: "{медаль 🥇🥈🥉/буллет} {ФИО} — «{doc_name}»: {баллы} баллов
               (экзамен {n}/{total})"
      title = "🏆 Лучшие по обучению за неделю"
  - _post_to_feed(title, message) -> bool:
      httpx.post(BITRIX_WEBHOOK_URL + "log.blogpost.add",
                 json={"POST_TITLE": title, "POST_MESSAGE": message, "DEST": ["UA"]},
                 timeout=15.0)  # retry 3× backoff 2,4с; True при 200 и result в теле
  - post_exam_congratulation(session, questions) -> None:
      try: emp = get_employee(session["user_id"])
           fio = (emp or {}).get("full_name") or f"Сотрудник (ID {session['user_id']})"
           exam_total = len(questions.get("exam_questions", []))
           basic_total = len(questions.get("basic_questions", []))
           _post_to_feed(*build_congrats(fio, questions.get("doc_name", "обучение"),
                         session["score_exam"], exam_total,
                         session["score_basic"], basic_total))
      except Exception as exc: print(f"[gamification] congrats failed: {exc!r}")
      # ВНИМАНИЕ: session на входе — со СТАРЫМИ score_exam (до update_session).
      # Передавать посчитанный correct_count из _finish_phase, не session["score_exam"]!
      # → сигнатура: post_exam_congratulation(user_id, questions, exam_score, basic_score)
  - maybe_post_weekly_rating(now_msk=None) -> bool:
      now_msk = now_msk or datetime.now(MSK)
      if not should_post_rating(now_msk, get_meta("last_rating_week")): return False
      rows = get_report_rows(); emps = {e["bitrix_uid"]: e for e in get_all_employees()}
      result = build_weekly_rating(rows, emps, datetime.utcnow())
      set_meta("last_rating_week", week_key(now_msk))   # и при пустой неделе тоже
      if result is None:
          print("[gamification] рейтинг: за неделю никто не завершил — пропуск")
          return False
      return _post_to_feed(*result)

Task 3 — MODIFY app/state_machine.py (хук):
  - import: from app.gamification import post_exam_congratulation
  - _finish_phase, ветка экзамена, ПОСЛЕ notify_hr(...):
      if passed:
          post_exam_congratulation(session["user_id"], questions,
                                   correct_count, basic_score)
      # passed уже посчитан; correct_count/basic_score — локальные переменные ветки

Task 4 — MODIFY app/bitrix_bot.py (недельный луп):
  - import gamification (module import: from app import gamification — для
    monkeypatch в тестах через bot.gamification)
  - RATING_CHECK_INTERVAL = 3600
  - async def _weekly_rating_loop():
      print("[rating] Started — weekly, Monday 10:00 MSK")
      while True:
          await asyncio.sleep(RATING_CHECK_INTERVAL)
          try:
              posted = await asyncio.to_thread(gamification.maybe_post_weekly_rating)
              if posted: print("[rating] weekly rating posted")
          except Exception as exc:
              print(f"[rating] ERROR: {exc!r}")
  - startup: _rating_task = asyncio.create_task(_weekly_rating_loop())
    (+ global _rating_task = None рядом с остальными)

Task 5 — CREATE tests/test_gamification.py:
  - week_key: границы года (datetime(2026,1,1) → ISO-неделя 2026-W01?
    проверить фактическое isocalendar-значение, зафиксировать поведение)
  - should_post_rating: вс 23:59 → False; пн 09:59 → False; пн 10:00 → True;
    пн 10:00 но last_week == текущей → False; вт 12:00, неделя не пощена → True
    (догоняем пропущенный понедельник — рестарт сервера в пн не теряет пост)
  - build_congrats: ФИО и баллы в тексте
  - build_weekly_rating: топ-5 из 7 кандидатов (сортировка по сумме);
    несдавший (6/10) исключён; DONE 8 дней назад исключён; пустая неделя → None;
    legacy-uid без employee → «Сотрудник (ID …)»; медали 🥇🥈🥉 на местах 1–3
  - maybe_post_weekly_rating (tmp DB): monkeypatch gamification._post_to_feed
    recorder → True; пн 10:01 → posted=True, meta выставлена; повторный вызов
    → False (идемпотентность); пустая неделя → False, но meta выставлена
  - хук: fixture env из test_state_machine-стиля; собрать сессию в EXAM с 1
    exam-вопросом; monkeypatch(sm, "post_exam_congratulation") recorder;
    ответить правильно → recorder вызван с exam_score=1; второй кейс: ответить
    неправильно (passed=False при total=1: round(0.7)=1 > 0) → recorder НЕ вызван

Task 6 — MODIFY planning.md, task.md:
  - planning.md: №5 ✅ код готов; заметки (пост от имени владельца вебхука!,
    скоуп log, «Благодарность с медалью» недоступна по REST → обычный пост);
    ИДЕЯ ВПРОК для ответа CEO: log.blogpost.getusers.important — «важное
    сообщение + кто прочитал» (контроль ознакомления со стандартами)
  - task.md: №5 в Готово; живые чеки: скоуп log, внешний вид поста, время
    публикации рейтинга в реальный понедельник
```

### Per task pseudocode (ключевые места)

```python
# gamification.py — расписание (чистое, tz-aware)
MSK = timezone(timedelta(hours=3))

def week_key(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"

def _monday_10(now: datetime) -> datetime:
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=10, minute=0, second=0, microsecond=0)
    return monday

def should_post_rating(now_msk: datetime, last_week: str | None) -> bool:
    return week_key(now_msk) != (last_week or "") and now_msk >= _monday_10(now_msk)

# state_machine.py — хук (внутри _finish_phase, ветка экзамена, после notify_hr)
    if passed:
        # Пост в Ленту; функция сама глотает ошибки — экзамен не роняем
        post_exam_congratulation(session["user_id"], questions,
                                 correct_count, basic_score)
```

### Integration Points
```yaml
DATABASE:
  - init_db: + таблица meta (CREATE IF NOT EXISTS — миграция не нужна)
CONFIG:
  - без новых .env-ключей (МСК и интервал — константы)
ROUTES:
  - без новых; + startup-task _weekly_rating_loop
BITRIX (живьём):
  - скоуп вебхука должен включать log; пост появится от имени владельца вебхука
```

## Validation Loop

### Level 1: Syntax & Style
```bash
~/.pyenv/versions/vertical_standards_env/bin/python -m ruff check app/ scripts/ tests/ --fix
```

### Level 2: Unit Tests
```bash
~/.pyenv/versions/vertical_standards_env/bin/python -m pytest tests/ -v
# Существующие 84 зелёные + test_gamification.py
```

### Level 3: Офлайн-смоук расписания и текстов
```bash
~/.pyenv/versions/vertical_standards_env/bin/python - <<'EOF'
from datetime import datetime
from app.gamification import MSK, should_post_rating, week_key, build_congrats
mon = datetime(2026, 7, 13, 10, 1, tzinfo=MSK)   # понедельник
sun = datetime(2026, 7, 12, 23, 0, tzinfo=MSK)
print(week_key(mon), should_post_rating(mon, None), should_post_rating(sun, None))
print(build_congrats("Иван Иванов", "Стандарты.docx", 8, 10, 5, 5)[1])
# ждём: 2026-W29 True False + русский текст с 8/10
EOF
```

### Level 4: Живой прогон (сервер/ngrok, вместе со сдачей пакета)
```bash
# 1. Пройти экзамен тестовым сотрудником → пост «Благодарность» в Ленте
#    (проверить: от чьего имени, виден ли всем, скоуп log у вебхука)
# 2. Временный тест рейтинга: set_meta('last_rating_week','') + подождать час
#    ИЛИ python -c "from app.gamification import maybe_post_weekly_rating;
#    print(maybe_post_weekly_rating())" в понедельник после 10:00 МСК
```

## Final validation Checklist
- [ ] pytest зелёный (84 старых + новые), ruff чистый
- [ ] Хук: passed → пост, не-passed → нет; фейл сети не роняет экзамен
- [ ] Рейтинг: топ-5 сдавших за 7 дней, медали, legacy-uid не роняет
- [ ] Идемпотентность: meta-гейт, пустая неделя тоже помечается
- [ ] Времена: расписание МСК, окно 7 дней — naive UTC (как в БД)
- [ ] Тексты русские; planning/task обновлены (вкл. идею getusers.important для CEO)

---

## Anti-Patterns to Avoid
- ❌ НЕ публиковать антирейтинг/отстающих — только топ сдавших
- ❌ НЕ ронять экзамен из-за Ленты — все посты в try/except
- ❌ НЕ брать session["score_exam"] в хуке — там старое значение (см. гочу),
     передавать correct_count из _finish_phase
- ❌ НЕ городить умный sleep-до-понедельника — ежечасная проверка + meta-гейт
- ❌ НЕ смешивать МСК (расписание) и UTC (updated_at в БД)
- ❌ НЕ добавлять .env-ключи и pytest-asyncio

## Открытые допущения (проверить живьём)
1. Спец-тип «Благодарность с медалью» через REST недоступен (в выжимке не упомянут)
   → обычный пост с 🏆. Если клиенту важна именно медаль — отдельная досмета.
2. Параметры log.blogpost.add (DEST=["UA"]) и скоуп log у вебхука — живой тест.
3. Пост от имени владельца вебхука — приемлемо ли клиенту (альтернатива —
   отдельный вебхук от служебного юзера «Бот Обучения»).

## Score: 9/10
Меньшая доработка пакета: один Bitrix-метод, вся логика — чистые функции
(расписание, тексты, выборка) с полным офлайн-покрытием; паттерны лупа/хука/тестов
отработаны в №2–№4. Минус балл: параметры log.blogpost.add и «от чьего имени пост»
подтверждаются только живьём (фолбэк — лог, ничего не ломается).
