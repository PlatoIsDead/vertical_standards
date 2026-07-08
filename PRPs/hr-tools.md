name: "Инструменты HR (доработка №3, 20ч/40 000₽)"
description: |
  Правка вопросов курса через чат («Изменить {курс} {вопрос 1–15}», двухшаговый флоу),
  «История {email}» — вопросы и ответы сотрудника, «Отчёт» — кто прошёл обучение
  и с каким результатом. Полностью офлайновая доработка — ни одного нового Bitrix-вызова.

## Purpose
Одно-проходная реализация доработки №3. Требует ВЫПОЛНЕННОЙ №2 (таблица employees,
HR-гейт, «Пригласить») — она в кодовой базе с июля 2026 (PRPs/email-access-autostart.md).

## Core Principles
1. **Context is King** — схемы данных и сниппеты ниже проверены по живому коду
2. **Validation Loops** — ruff + pytest (чистые парсеры отдельно, флоу через TestClient)
3. **Information Dense** — имена функций/полей из реального кода
4. **Progressive Success** — сначала чистые хелперы, потом БД, потом чат-флоу
5. **Global rules** — CLAUDE.md; ВСЕ тексты бота русские, ты-форма для сотрудников,
   к HR тоже «ты» (стиль существующих команд)

---

## Goal

HR в чате: «Изменить 3 7» → бот показывает текущий вопрос №7 курса №3 и шаблон →
HR шлёт замену одним сообщением → вопрос обновлён. «История ivan@x.ru» → все ответы
сотрудника с ✓/✗. «Отчёт» → сводка по всем сотрудникам: кто на каком этапе, баллы,
сдан/не сдан. Всё без разработчика.

## Why

- Смета №3: «HR сам ведёт тесты и видит результаты, без разработчика».
- Сгенерированные GPT вопросы бывают кривыми — сейчас поправить их может только Никита
  руками в SQLite. Это блокер реального использования.
- «Отчёт» — первый ответ на претензию CEO про контроль/отчётность (см. planning.md).

## What

1. **«Изменить {N курса} {номер 1–15}»** — двухшаговый флоу:
   шаг 1: бот показывает текущий вопрос + шаблон замены; запоминает pending-правку
   (in-memory dict по hr_user_id, TTL 10 мин — потеря при рестарте ок, HR повторит);
   шаг 2: HR шлёт замену ОДНИМ сообщением (текст, 4 варианта A–D, «Ответ: X»,
   опционально «Пояснение: …») → валидация → замена 1:1 в questions_json.
   «Отмена» — выйти; другая команда — молча снять pending и выполнить команду.
   Нумерация из сметы: 1–5 = базовый тест, 6–15 = экзамен.
2. **«История {email или ID}»** — сессии сотрудника (включая DONE) с вопросами,
   его ответами и ✓/✗.
3. **«Отчёт»** — по всем сессиям: ФИО/email (из employees; legacy без записи — голый
   uid), курс, этап по-русски, баллы, сдан/не сдан (порог 70% как в _finish_phase).
4. «Вопросы {N}» — перейти на сквозную нумерацию 1–15 + подсказка про «Изменить».
5. Help-текст HR-бота — добавить три команды.

### Success Criteria
- [ ] «Изменить 1 7» показывает текущий 7-й вопрос (= exam_questions[1]) и шаблон
- [ ] Замена одним сообщением обновляет ровно этот вопрос (correct/options/text/explanation),
      остальные 14 нетронуты, approved_at курса не сброшен
- [ ] Кривая замена → понятная ошибка, pending жив; «Отмена» — снят; «Курсы» — снят + список
- [ ] «История ivan@x.ru» → вопросы с ответами сотрудника и ✓/✗; неизвестный email → подсказка
- [ ] «Отчёт» → строки с ФИО/email, этапом, баллами; legacy-uid без ФИО не роняет отчёт
- [ ] Все существующие 35 тестов зелёные; `ruff check app/ scripts/ tests/` чистый

## All Needed Context

### Documentation & References
```yaml
- file: app/bitrix_bot.py
  why: |
    hr_handler — точка вставки. Структура ПОСЛЕ №2: form-парсинг → _is_duplicate →
    HR-гейт (_hr_ids) → if/elif по msg_lower: ("курсы","курс","список") точным
    совпадением; startswith: "подтвердить", "пригласить", "допустить", "вопросы";
    else → help. Ответ уходит в конце: asyncio.create_task(_send(dialog_id, text,
    HR_BOT_ID, client_id)). Pending-перехват ставить ПОСЛЕ HR-гейта и ДО if/elif.
    Ветка "вопросы" — существующий вывод нумерует базовые 1..5 и экзамен 1..10
    РАЗДЕЛЬНО — переделать на сквозную 1–15 (единственное место).
    Дедуп _is_duplicate: одинаковое сообщение от юзера глушится 15с — на двухшаговый
    флоу не влияет (шаги разные тексты).

- file: app/db.py
  why: |
    Схемы (проверено):
    courses: id, doc_name, doc_id, doc_detail_url, questions_json (TEXT!), created_at,
             approved_at, approved_by. get_course_by_id, get_course_questions
             (json.loads(questions_json)).
    sessions: id, user_id, dialog_id, course_id, state, current_q_idx, score_basic,
              score_exam, role, started_at, updated_at. get_session = последняя НЕ-DONE!
              Для «Истории» нужны ВСЕ сессии юзера — новая get_sessions_by_user.
    answers: id, session_id, question_id, phase('basic'|'exam'), user_answer('A'..'D'),
             is_correct(0/1), answered_at. get_session_answers(session_id, phase=None)
             сортирует по question_id.
    employees (№2): bitrix_uid PK, email (lower!), full_name, added_by, added_at.
             get_employee_by_email есть; для «Отчёта» нужен разовый SELECT * (map uid→имя).
    Стиль: sync-функции, _conn() с row_factory=Row, вызовы через asyncio.to_thread.

- file: app/state_machine.py
  why: |
    КРИТИЧНО — как пишутся answers: _handle_test → log_answer(session["id"], q_idx,
    phase, letter, is_correct), где q_idx = ИНДЕКС ВНУТРИ ФАЗЫ (0-based). Глобальная
    нумерация 1–15 существует ТОЛЬКО в UI правки: 1–5 → basic[0..4], 6–15 → exam[0..9].
    parse_answer — маппинг кириллических двойников {"А":"A","В":"B","С":"C","Д":"D"} —
    тот же нужен парсеру замены (HR печатает по-русски!).
    Порог сдачи: passed = correct >= round(total * 0.7) (_finish_phase) — «Отчёт»
    использует ту же формулу, НЕ выдумывать другую.

- file: app/course_generator.py
  why: |
    _validate_questions(result) валидирует ВЕСЬ дикт (5+10); внутри цикла — поштучные
    проверки (missing fields, len(options)==4, correct in ABCD). Извлечь из цикла
    validate_question(q) и переиспользовать в парсере замены (рефактор без изменения
    поведения). Структура вопроса: {"id": int, "text": str,
    "options": ["A. …","B. …","C. …","D. …"], "correct": "A", "explanation": str}.

- file: tests/test_hr_invite.py
  why: |
    ОБРАЗЕЦ интеграционных тестов hr_handler: TestClient(bot.app), фикстура env =
    tmp DB (monkeypatch db.DB_PATH → init_db) + tmp roles.json + monkeypatch
    bot._send (capture list) + monkeypatch.setenv("HR_USER_IDS","9") +
    bot._recent_msgs.clear() (дедуп переживает тесты!) + _wait_for-поллинг
    (ответы уходят через asyncio.create_task). _post_hr(client, message, ...) хелпер.
    В новой фикстуре ДОПОЛНИТЕЛЬНО чистить bot._pending_edits.

- file: PRPs/email-access-autostart.md
  why: конвенции предыдущей доработки (гоча про import-side-effects bitrix_bot и т.д.)
```

### Current Codebase tree (релевантная часть)
```bash
vertical_standards/
├── app/
│   ├── bitrix_bot.py        # hr_handler (гейт, курсы/вопросы/подтвердить/пригласить/допустить)
│   ├── state_machine.py     # FSM; log_answer(q_idx per phase); parse_answer (кириллица)
│   ├── course_generator.py  # _validate_questions (извлечь validate_question)
│   └── db.py                # courses/sessions/answers/employees + CRUD
├── tests/                   # 35 тестов; test_hr_invite.py — образец TestClient-флоу
├── onboarding.db            # ЖИВАЯ БД (не мутировать из тестов до подмены DB_PATH)
└── ruff.toml
```

### Desired Codebase tree
```bash
├── app/
│   ├── hr_tools.py          # NEW: ЧИСТЫЕ функции — resolve_question_ref, parse_replacement,
│   │                        #      format_question_full, build_history_text, build_report_text
│   ├── bitrix_bot.py        # MOD: _pending_edits + ветки изменить/история/отчёт/отмена,
│   │                        #      перехват pending, сквозная нумерация в «Вопросы», help
│   ├── course_generator.py  # MOD: validate_question(q) извлечён из _validate_questions
│   └── db.py                # MOD: update_course_questions, get_sessions_by_user,
│                            #      get_report_rows, get_all_employees
└── tests/
    ├── test_hr_tools.py     # NEW: чистые парсеры/форматтеры без TestClient
    └── test_hr_edit_flow.py # NEW: двухшаговый флоу + история + отчёт через TestClient
```

### Known Gotchas of our codebase & Library Quirks
```python
# CRITICAL: answers.question_id = индекс ВНУТРИ фазы (0-based), phase отдельно.
# UI-номер N (1–15): N<=5 → ("basic", N-1); N>=6 → ("exam", N-6). Не перепутать —
# «Изменить 1 6» правит exam_questions[0], а не basic[5].

# CRITICAL: questions_json — ТЕКСТ в courses; правка = json.loads → мутация →
# json.dumps(ensure_ascii=False) → UPDATE courses SET questions_json. approved_at
# НЕ трогать (правка активного курса не должна его деактивировать).

# CRITICAL: HR печатает кириллицей: «Ответ: В» — это кириллическая В (=B latin).
# Нормализация букв {"А":"A","В":"B","С":"C","Д":"D"} и в строках вариантов,
# и в «Ответ:». Регистр — upper() до маппинга.

# CRITICAL: pending-правка НЕ должна съедать другие команды. Перехват: если у
# hr_user_id есть живой pending И msg_lower НЕ начинается с известной команды
# ("курсы","курс","список","подтвердить","допустить","пригласить","вопросы",
# "изменить","история","отчёт") → сообщение = замена. «Отмена» — отдельно, чистит
# pending. Другая команда — pending молча снимается, команда выполняется.

# CRITICAL: сотрудник в середине теста по правленому курсу: вопрос уже показан
# старым текстом, а проверка пойдёт по НОВОМУ correct. Допустимо (обычно правят
# до активации), зафиксировать в planning.md как заметку — НЕ городить версионирование.

# GOTCHA: replacement приходит ОДНИМ сообщением, Bitrix переносы строк сохраняет
# (form-data). Парсер по строкам: варианты = строки, начинающиеся с буквы A–D
# (или кириллического двойника) + '.'/')' ; текст вопроса = всё ДО первого варианта
# (может быть многострочным); «Ответ:» и «Пояснение:» — startswith после lower().
# Пояснение не прислали → explanation = "" (старое НЕ наследовать — может
# противоречить новому вопросу; поведение задокументировать в ответе бота).

# GOTCHA: сохранять поле id исходного вопроса (q["id"]) при замене — на него могут
# опираться будущие фичи; options хранить С префиксами "A. ..." как в генераторе
# (формат единый; если HR прислал вариант без префикса — добавить).

# GOTCHA: get_session возвращает последнюю НЕ-DONE сессию — для «Истории» НЕ годится
# (завершённые исчезнут). Новая get_sessions_by_user(user_id) — все, новые сверху.

# GOTCHA: «Отчёт»/«История» могут быть длинными. Лимиты сообщений Bitrix не
# документированы в выжимке — резать: История = последние 3 сессии, Отчёт =
# последние 30 строк, с честной припиской «показаны последние N» (no silent caps).

# GOTCHA: data/courses/{file_id}_draft.json — черновик при генерации; при правке
# НЕ обновлять (источник правды — questions_json в SQLite). Не трогать.

# GOTCHA: тесты — import app.bitrix_bot исполняет init_db()+load_index() на живых
# файлах (см. PRP №2): DB_PATH подменять ДО любых записей; в фикстуре чистить
# bot._recent_msgs И bot._pending_edits. Ответы hr_handler уходят через
# asyncio.create_task → _wait_for-поллинг из test_hr_invite.py.

# GOTCHA: pytest/ruff в venv проекта: ~/.pyenv/versions/vertical_standards_env/bin/python.
# mypy НЕ вводить.
```

## Implementation Blueprint

### Data models and structure

```python
# Никаких новых таблиц. Pending-правки — in-memory (потеря при рестарте некритична):
_pending_edits: dict[str, dict] = {}   # hr_user_id → {"course_id": int, "q_num": int,
                                       #               "expires": float}  # time.monotonic()+600

# resolve_question_ref(q_num) → ("basic", 0..4) | ("exam", 0..9), ValueError вне 1–15

# parse_replacement(text) → dict | None:
# {"text": str, "options": ["A. …", ..4], "correct": "A", "explanation": str}
# None = не распознано (бот отвечает шаблоном ещё раз)
```

### List of tasks (в порядке выполнения)

```yaml
Task 1 — MODIFY app/course_generator.py (рефактор без изменения поведения):
  - извлечь из цикла _validate_questions поштучную проверку:
      def validate_question(q: dict) -> None:   # raises ValueError
          missing = [k for k in ("text", "options", "correct") if k not in q]
          if missing: raise ValueError(...)
          if len(q["options"]) != 4: raise ValueError(...)
          if q["correct"] not in ("A","B","C","D"): raise ValueError(...)
  - _validate_questions вызывает validate_question(q) в цикле; счётчики 5/10 не трогать

Task 2 — MODIFY app/db.py (4 функции, стиль модуля):
  - update_course_questions(course_id: int, questions_json: str) -> bool:
      UPDATE courses SET questions_json = ? WHERE id = ?; rowcount > 0
      # approved_at/approved_by НЕ трогать
  - get_sessions_by_user(user_id: str) -> list[dict]:
      SELECT * FROM sessions WHERE user_id = ? ORDER BY id DESC   # ВКЛЮЧАЯ DONE
  - get_report_rows() -> list[dict]:
      SELECT s.*, c.doc_name, c.questions_json FROM sessions s
      JOIN courses c ON c.id = s.course_id ORDER BY s.updated_at DESC
  - get_all_employees() -> list[dict]:  SELECT * FROM employees

Task 3 — CREATE app/hr_tools.py (ЧИСТЫЕ функции, без сети и БД):
  - _CYR = {"А":"A","В":"B","С":"C","Д":"D"}; _norm_letter(s) → upper + маппинг
  - resolve_question_ref(q_num: int) -> tuple[str, int]:
      1..5 → ("basic", q_num-1); 6..15 → ("exam", q_num-6); иначе ValueError
  - question_by_ref(questions: dict, q_num: int) -> dict | None
  - format_question_full(q: dict, q_num: int) -> str:
      "*Вопрос {q_num}* ({базовый тест|экзамен}):\n{text}\n{options…}\nОтвет: {correct}"
      + "\nПояснение: {explanation}" если есть
  - parse_replacement(raw: str) -> dict | None:
      lines = [l.strip() for l in raw.splitlines() if l.strip()]
      options, text_lines, correct, explanation = [], [], None, ""
      for line:
          m = re.match(r"^([A-DАВСД])[.)]\s*(.+)", line, re.IGNORECASE) → вариант:
              letter = _norm_letter(m[1]); options.append(f"{letter}. {m[2]}")
          low = line.lower()
          low.startswith("ответ") → correct = _norm_letter(первая буква после ':')
          low.startswith("пояснение") → explanation = хвост после ':'
          иначе, если options пуст → text_lines.append(line)   # текст только ДО вариантов
      if not text_lines or len(options) != 4 or correct not in ABCD: return None
      # буквы вариантов должны быть ровно A,B,C,D по одному разу — иначе None
      return {"text": " ".join(text_lines), "options": options,
              "correct": correct, "explanation": explanation}
  - apply_replacement(questions: dict, q_num: int, repl: dict) -> dict:
      phase, idx = resolve_question_ref(q_num); q_list = questions[f"{phase}_questions"]
      new_q = {"id": q_list[idx].get("id", idx), **repl}
      course_generator.validate_question(new_q)   # единая валидация
      q_list[idx] = new_q; return questions
  - build_history_text(employee_label, sessions, answers_by_session, questions_by_course) -> str:
      последние 3 сессии; на сессию: курс, этап, баллы; ответы:
      "{№}. {✓|✗} {text[:60]} — твой ответ {letter}" (+ " (верно: {correct})" если ✗)
      # № — сквозной 1–15: basic q_id+1, exam q_id+6
  - build_report_text(rows, employees_by_uid) -> str:
      на строку-сессию: "{ФИО или uid} ({email или '—'}) — «{doc_name}»: {этап}, {баллы}"
      этапы RU: ROLE_SELECT/READING «изучает материал», BASIC_TEST «базовый тест»,
      WAITING_HR «ждёт допуска», EXAM «сдаёт экзамен»,
      DONE «завершил — {✅ сдан|❌ не сдан} (экзамен {n}/{total})» по формуле
      passed = score_exam >= round(total*0.7), total = len(exam_questions) из questions_json
      лимит 30 строк + "…показаны последние 30 из {N}"

Task 4 — MODIFY app/bitrix_bot.py:
  - import: from app.hr_tools import (...); from app.db import (+4 новые)
  - module-level: _pending_edits: dict[str, dict] = {}; PENDING_TTL = 600
  - хелперы: _get_pending(user_id) (проверка expires, чистка), _HR_COMMAND_PREFIXES tuple
  - в hr_handler ПОСЛЕ HR-гейта, ДО msg_lower-цепочки:
      pending = _get_pending(user_id)
      if pending:
          if msg_lower in ("отмена", "cancel"):
              _pending_edits.pop(user_id, None)
              text = "Правка отменена." → отправить и return
          if not msg_lower.startswith(_HR_COMMAND_PREFIXES):
              → это замена: parse_replacement(question)
                None → text = "❌ Не понял формат. Пришли вопрос ОДНИМ сообщением:\n" +
                        _REPLACEMENT_TEMPLATE + "\nИли напиши Отмена."   # pending ЖИВ
                ok → questions = get_course_questions(course_id);
                     apply_replacement (ValueError → текст ошибки);
                     update_course_questions(course_id, json.dumps(..., ensure_ascii=False));
                     _pending_edits.pop(user_id);
                     text = f"✅ Вопрос {q_num} курса «{doc_name}» обновлён.\n\n" +
                            format_question_full(new_q, q_num)
              → отправить и return
          # иначе — команда: молча снять pending и провалиться в цепочку
          _pending_edits.pop(user_id, None)
  - ветка elif msg_lower.startswith("изменить"):
      parts: "Изменить {course_id} {q_num}"; оба int, иначе подсказка формата
      course = get_course_by_id → нет: "❌ Курс №{N} не найден."
      q = question_by_ref(questions, q_num) → нет: "❌ Номер вопроса 1–15."
      _pending_edits[user_id] = {...}
      text = f"✏️ Курс «{doc_name}», сейчас:\n\n" + format_question_full(q, q_num) +
             "\n\nПришли новый вопрос ОДНИМ сообщением:\n" + _REPLACEMENT_TEMPLATE +
             "\n(Пояснение необязательно; без него старое пояснение удаляется.)\n" +
             "Отмена — выйти без изменений."
  - ветка elif msg_lower.startswith("история"):
      target = последнее слово; "@" → get_employee_by_email → uid (нет → подсказка
      "Сначала: Пригласить {email}"); иначе uid = target
      sessions = get_sessions_by_user(uid) → пусто: "У сотрудника нет сессий обучения."
      answers по каждой: get_session_answers(s["id"]); questions_by_course из
      get_course_questions; text = build_history_text(...)
  - ветка elif msg_lower == "отчёт" (+ "отчет" без ё!):
      rows = get_report_rows(); emps = {e["bitrix_uid"]: e for e in get_all_employees()}
      text = build_report_text(rows, emps); пусто → "Сессий обучения пока нет."
  - ветка "вопросы": сквозная нумерация — базовые enumerate(basic, 1),
      экзамен enumerate(exam, 6); в конце строка "Изменить вопрос: Изменить {N} {1–15}"
  - help (else): + "• *Изменить {курс} {вопрос 1–15}* — править вопрос",
      "• *История {email}* — ответы сотрудника", "• *Отчёт* — сводка по обучению"
  - ВСЕ db-вызовы через await asyncio.to_thread(...)

Task 5 — CREATE tests/test_hr_tools.py (чистые, без TestClient):
  - resolve_question_ref: 1→(basic,0), 5→(basic,4), 6→(exam,0), 15→(exam,9);
    0/16 → ValueError
  - parse_replacement happy: текст + 4 варианта + "Ответ: B" → дикт; префиксы "A)" и
    кириллические "А. …"/"Ответ: В" нормализуются; "Пояснение:" подхватывается
  - parse_replacement отказы: 3 варианта → None; без "Ответ:" → None; дубль буквы
    (два "A.") → None; пустой текст → None; многострочный текст вопроса склеивается
  - apply_replacement: заменяет exam[1] при q_num=7, сохраняет id, остальные нетронуты
  - build_report_text: DONE с 8/10 → "✅ сдан"; 6/10 → "❌ не сдан" (round(10*0.7)=7);
    uid без employee → голый uid в строке; >30 строк → приписка про последние 30
  - build_history_text: ✗-ответ содержит "(верно: X)"; сквозные номера (exam q_id 0 → №6)

Task 6 — CREATE tests/test_hr_edit_flow.py (TestClient, зеркало test_hr_invite.py):
  - фикстура: как в test_hr_invite (tmp DB, roles.json, HR_USER_IDS=9, mock _send,
    clear _recent_msgs) + bot._pending_edits.clear() + курс с 5+10 РАЗЛИЧИМЫМИ
    вопросами ("Базовый {i}?"/"Экзамен {i}?") + activate
  - test_edit_two_step: "Изменить 1 7" → ответ содержит "Экзамен 2?"; затем замена
    ("Новый вопрос?\nA. а\nB. б\nC. в\nD. г\nОтвет: Г"  ← кириллическая Г НЕ буква
    ответа — взять "Ответ: B") → db.get_course_questions: exam[1]["text"]=="Новый вопрос?",
    correct=="B", basic нетронуты, approved_at на месте
  - test_edit_bad_format_keeps_pending: кривая замена → "Не понял формат"; повторная
    правильная — применяется
  - test_edit_cancel: "Отмена" → "отменена"; следующее сообщение уже НЕ замена (help)
  - test_edit_pending_ttl: bot._pending_edits[uid]["expires"] = time.monotonic()-1 →
    сообщение-замена уходит в help (pending истёк)
  - test_command_cancels_pending: после шага 1 отправить "Курсы" → ответ со списком
    курсов, pending снят
  - test_history_by_email: employee + сессия + log_answer basic q0 ✓, exam q1 ✗ →
    "История ivan@x.ru" содержит "✓", "✗", "(верно:", сквозной номер "7"
  - test_history_unknown: "История ghost@x.ru" → "Пригласить"
  - test_report: две сессии (DONE сдан, WAITING_HR) → "✅ сдан" и "ждёт допуска";
    ФИО из employees в строке
  - test_voprosy_numbering: "Вопросы 1" → экзаменационные пронумерованы 6..15

Task 7 — MODIFY planning.md, task.md:
  - planning.md: №3 ✅ код готов; заметка «правка активного курса влияет на сотрудника
    в середине теста (старый текст показан, новый correct считается) — правим до
    активации»; открытый вопрос «Отчёт/История длинные — лимит сообщения Bitrix
    проверить живьём»
  - task.md: HR-команды из бэклога (Изменить/Ответ/Отчёт) → Готово (Ответ {…} покрыт
    заменой целиком); добавить живой чек-пункт про длину сообщений
```

### Per task pseudocode (ключевые места)

```python
# Task 4 — перехват pending (внутри hr_handler, после HR-гейта)
_HR_COMMAND_PREFIXES = ("курсы", "курс", "список", "подтвердить", "допустить",
                        "пригласить", "вопросы", "изменить", "история", "отчёт", "отчет")

pending = _get_pending(user_id)
if pending:
    if msg_lower in ("отмена", "cancel"):
        _pending_edits.pop(user_id, None)
        asyncio.create_task(_send(dialog_id, "Правка отменена.", HR_BOT_ID, client_id))
        return {"status": "ok"}
    if not msg_lower.startswith(_HR_COMMAND_PREFIXES):
        repl = parse_replacement(question)
        if repl is None:
            text = ("❌ Не понял формат. Пришли вопрос одним сообщением:\n\n"
                    + _REPLACEMENT_TEMPLATE + "\n\nИли напиши: Отмена")
        else:
            questions = await asyncio.to_thread(get_course_questions, pending["course_id"])
            try:
                questions = apply_replacement(questions, pending["q_num"], repl)
            except ValueError as exc:
                text = f"❌ {exc}"
            else:
                await asyncio.to_thread(
                    update_course_questions, pending["course_id"],
                    json.dumps(questions, ensure_ascii=False))
                _pending_edits.pop(user_id, None)
                course = await asyncio.to_thread(get_course_by_id, pending["course_id"])
                new_q = question_by_ref(questions, pending["q_num"])
                text = (f"✅ Вопрос {pending['q_num']} курса «{course['doc_name']}» обновлён.\n\n"
                        + format_question_full(new_q, pending["q_num"]))
        asyncio.create_task(_send(dialog_id, text, HR_BOT_ID, client_id))
        return {"status": "ok"}
    _pending_edits.pop(user_id, None)   # команда важнее забытой правки

# Task 3 — шаблон замены (константа в hr_tools, используется в двух ответах)
_REPLACEMENT_TEMPLATE = (
    "Текст вопроса\n"
    "A. первый вариант\n"
    "B. второй вариант\n"
    "C. третий вариант\n"
    "D. четвёртый вариант\n"
    "Ответ: A\n"
    "Пояснение: почему верен (необязательно)"
)
```

### Integration Points
```yaml
DATABASE:
  - без миграций: только UPDATE courses.questions_json + новые SELECT'ы
CONFIG:
  - без новых .env-ключей
ROUTES:
  - без новых эндпоинтов; расширяется только "/hr"
STATE:
  - _pending_edits — in-memory, per hr_user_id, TTL 600с; рестарт теряет (ок)
```

## Validation Loop

### Level 1: Syntax & Style
```bash
~/.pyenv/versions/vertical_standards_env/bin/python -m ruff check app/ scripts/ tests/ --fix
# Expected: чисто. mypy не настроен — не вводить.
```

### Level 2: Unit Tests
```bash
~/.pyenv/versions/vertical_standards_env/bin/python -m pytest tests/ -v
# Существующие 35 обязаны остаться зелёными; новые — test_hr_tools + test_hr_edit_flow.
```

### Level 3: Офлайн-смоук чистых функций
```bash
~/.pyenv/versions/vertical_standards_env/bin/python - <<'EOF'
from app.hr_tools import parse_replacement, resolve_question_ref
print(resolve_question_ref(6))    # ('exam', 0)
print(parse_replacement("Что делать при пожаре?\nА. Бежать\nB. Звонить 112\nC. Прятаться\nD. Кричать\nОтвет: В"))
# ждём options с латинскими префиксами и correct='B' (кириллица нормализована)
EOF
```

### Level 4: Живой прогон (ngrok/сервер, вместе со сдачей №2)
```bash
# HR-бот: «Изменить 1 7» → текущий вопрос; замена → «обновлён»; «Вопросы 1» —
# сквозная нумерация и новый текст. «История {email Никиты}» после прохождения
# теста. «Отчёт» — проверить, что длинное сообщение доходит целиком (лимит Bitrix).
```

## Final validation Checklist
- [ ] pytest зелёный (35 старых + новые), ruff чистый
- [ ] Замена вопроса: ровно один вопрос изменён, id сохранён, approved_at цел
- [ ] Кириллические буквы в замене нормализуются (А/В/С/Д)
- [ ] Pending: ошибка формата не сбрасывает, «Отмена» сбрасывает, команда сбрасывает, TTL работает
- [ ] «История»: сквозные номера 1–15, ✓/✗, «(верно: X)» на ошибках
- [ ] «Отчёт»: этапы по-русски, сдан/не сдан по формуле 70%, legacy-uid не роняет
- [ ] «отчет» без ё тоже работает
- [ ] Тексты русские; help обновлён; planning.md/task.md обновлены

---

## Anti-Patterns to Avoid
- ❌ НЕ добавлять/удалять вопросы — только замена 1:1 (current_q_idx сессий!)
- ❌ НЕ хранить pending в БД — in-memory достаточно, рестарт не страшен
- ❌ НЕ трогать approved_at при правке и НЕ обновлять data/courses/*_draft.json
- ❌ НЕ наследовать старое explanation при замене — только присланное
- ❌ НЕ вводить новую формулу «сдан» — round(total*0.7) как в _finish_phase
- ❌ НЕ парсить replacement регэкспом-монолитом — построчно, это тестируемо
- ❌ НЕ забыть "отчет" без ё и кириллические А/В/С/Д

## Открытые допущения
1. Лимит длины сообщения Bitrix неизвестен — лимиты 3 сессии/30 строк с припиской;
   если живьём обрежется — чанкование сообщений отдельной мелкой правкой.
2. Правка активного курса действует немедленно (сотрудник в середине теста получит
   новый correct при старом показанном тексте) — принято, правим до активации.
3. «История» по email работает только для приглашённых через №2 (email в employees);
   для legacy — по uid.

## Score: 9/10
Полностью офлайновая доработка: ни одного нового Bitrix-вызова, все данные уже в
SQLite, паттерны команд/тестов отработаны в №2, парсер замены — чистая функция с
таблицей кейсов. Минус балл: свободный ввод HR (форматы замены) и неизвестный лимит
длины сообщений — оба риска закрыты фолбэками (реprompt с шаблоном, усечение с припиской).
