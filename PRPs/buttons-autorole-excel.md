name: "Добор второго пакета: №8 авто-роль из отдела, №10 Excel-матрица, №7 кнопки (за флагом)"
description: |
  Три оставшиеся кодовые фичи пакета №7-№10 (№9 v2 и №11 сданы). Деплой (№6)
  ЯВНО вне скоупа по команде юзера. №7 реализуется ЗА ФЛАГОМ BUTTONS_ENABLED=0:
  вся живая неопределённость (формат ONIMCOMMANDADD, рендер) проверяется на
  сервере включением флага, код и тесты готовятся сейчас.

## Purpose
Одно-проходная реализация. База — код после 02c56a2 (№11 + демо-пак + пакет 1 +
№9 v2, 175 тестов). Порядок: №8 → №10 → №7 (по возрастанию неопределённости).

## Core Principles
1-5 как всегда (CLAUDE.md, русские UI-тексты, ни одной второй формулы сдачи).

## НЕ в скоупе
- №6 деплой (исключён юзером), SMTP-канал эскалаций (нет кредов),
  HR-кнопки в «Допустить» (только employee-бот — скоуп-рез, см. C-гочи),
  бэклог-идеи (микротесты, метрики, сверка).

---

## Goal

**№8:** сотрудник из отдела, замапленного в roles.json `departments`, получает
роль автоматически («Твоя роль: X (по отделу)») — меню не показывается; маппинг
пуст (сегодня) → поведение как сейчас. **№10:** HR-команда «Отчёт» присылает
текстовую сводку + файл «Отчёт по обучению.xlsx» — матрица сотрудник×курс
(✅/❌/🕐/пусто) + лист «Детали»; файл один, прошлый удаляется. **№7:** при
`BUTTONS_ENABLED=1` сообщения employee-бота несут инлайн-кнопки (A-D в тестах,
Готов/Мои курсы/Роль в чтении, цифры ролей, Пересдать/Далее), нажатие приходит
`ONIMCOMMANDADD` на новый `/command` и прокидывается в тот же FSM.

## Why
- №8 — протокол клиента («минимизация необходимости знать команды»); механизм
  строим сейчас с ПУСТЫМ маппингом (как №1 строил folders): клиент пришлёт
  ID отделов → заполнение конфига без кода.
- №10 — запрос Дмитрия «матрица как в Marriott»; текстовый отчёт усечён 30
  строками и нечитаем при росте.
- №7 — просьба про кнопки с первой встречи; исследование ЗАВЕРШЕНО
  (INITIAL FEATURE 7, 10.08): нажатие = ONIMCOMMANDADD + imbot.command.register,
  варианта «кнопка печатает за юзера» в Bitrix НЕТ. Флаг позволяет писать код
  до сервера, не рискуя живыми сотрудниками.

## What / Success Criteria
- [ ] №8: `role_for_departments([1307])` по конфигу → роль; юзер в двух отделах
      с РАЗНЫМИ ролями → None (меню); пустой блок departments → None
- [ ] №8: онбординг с автоопределением: seen_portal_users → живой user.get
      (sync, timeout, ошибки → None) → «(по отделу)» в приветствии; команда
      «Роль» продолжает работать как ручная страховка
- [ ] №10: `build_report_xlsx(...)` -> bytes; матрица: лучший результат пары
      (сдал хоть раз → ✅), иначе последняя сессия (❌/🕐), нет сессий → пусто;
      лист «Детали» со всеми сессиями; открывается openpyxl из bytes в тесте
- [ ] №10: «Отчёт» = текстовая сводка (как сейчас) + файл; REPORTS_FOLDER_ID
      пуст → только текст + одна строка-подсказка HR; прошлый файл удаляется
      (meta last_report_file_id → markdeleted)
- [ ] №7: BUTTONS_ENABLED=0 (дефолт) — payload'ы БЕЗ ключа KEYBOARD, ни один
      существующий тест не изменился; =1 — клавиатура по СОСТОЯНИЮ сессии
- [ ] №7: POST /command (лог всех полей) → process_message тем же путём, что
      «/» (dedup, файл курса при назначении); scripts/register_commands.py
- [ ] `python -m pytest tests/ -v` зелёный (сейчас 175), `ruff check` чистый

## All Needed Context

### Documentation & References
```yaml
- file: INITIAL.md (FEATURE 7 и FEATURE 8 — полные спеки; FEATURE 10 — формат
  матрицы и решение про uploadfile)
  why: утверждённый скоуп + ловушки; №7 п.2 обновлён 10.08 (ONIMCOMMANDADD)

- file: app/state_machine.py
  why: |
    №8: ветка session is None (после развилки №9!) и start_onboarding —
    role = АВТО(профиль) or _last_known_role or меню. АВТО ГЛАВНЕЕ памяти:
    перевод в другой отдел должен сменить роль (сама суть №8).
    _assign_course(..., remembered=...) — добавить источник 'department'
    для текста «(по отделу — сменить: Роль)». _last_known_role — образец.
    №7: FSM НЕ трогать — кнопки строятся по state СНАРУЖИ (bot_handler).

- file: app/bitrix_bot.py
  why: |
    №8: _departments_json (сортированный json int-ов) — формат
    seen_portal_users.departments; _bitrix_user_by_email — образец user.get.
    №10: ветка «отчёт» (~756); _send_course_file — КАСКАД commit в чат
    (im.dialog.get → im.disk.file.commit UPLOAD_ID/DISK_ID) — вынести общий
    _commit_disk_file(dialog_id, disk_file_id), _send_course_file станет
    обёрткой. №7: bot_handler — после process_message уже читает СВЕЖУЮ
    сессию (after) для файла курса — ТУДА же клавиатуру по состоянию;
    /user-webhook — образец «неизвестный формат события → лог всех полей».
    _send(dialog_id, text, bot_id, client_id) — добавить keyboard=None.

- file: app/db.py
  why: get_user_departments (json-строка или None), meta get/set (№10 file_id),
       get_report_rows (строки для xlsx — s.* + doc_name + questions_json),
       get_all_employees, get_active_courses.

- file: app/hr_tools.py
  why: _session_status / формула сдачи — ЕДИНСТВЕННЫЙ источник статусов для
       ячеек xlsx (сдан = score_exam >= round(total*0.7)). Не дублировать —
       вынести хелпер passed(session, questions) если нужно.

- file: data/referance/bitrix24_docs.md:34572-34603, 954
  why: |
    ФОРМАТ ЗАГРУЗКИ ПОДТВЕРЖДЁН: disk.* принимают fileContent =
    ["имя.xlsx", "<base64>"] одним вызовом (пример uploadversion:34593;
    «имя файла и содержимое в Base64»:954). disk.folder.uploadfile:
    {"id": folder_id, "data": {"NAME": name}, "fileContent": [...],
    "generateUniqueName": true}. Скоуп disk есть (поллер работает).

- url: https://apidocs.bitrix24.ru/api-reference/disk/folder/disk-folder-upload-file.html
  why: параметры uploadfile (перепроверить при живом прогоне)

- url: https://apidocs.bitrix24.ru/api-reference/chat-bots/commands/index.html
  why: imbot.command.register (BOT_ID, COMMAND, COMMON=N, HIDDEN=Y,
       EVENT_COMMAND_ADD=url, LANG=[{LANGUAGE_ID:ru,TITLE:...}]) и событие
       ONIMCOMMANDADD — старое API imbot.*, наше.

- file: tests/test_state_machine.py, test_folder_sync.py (FakeAsyncClient),
        test_hr_invite.py (TestClient), test_hr_tools.py (чистые)
  why: все четыре паттерна тестов уже отработаны.
```

### Known Gotchas
```python
# ─── №8 ───
# CRITICAL: порядок источников роли: авто-из-отдела > память сессии > меню.
# Проверка авто — В ОБЕИХ точках (process_message session-is-None И
# start_onboarding), ПОСЛЕ развилки пересдачи №9 (fork первее всего).

# CRITICAL: UF_DEPARTMENT — массив; все отделы юзера мапятся в ОДНУ роль →
# берём её; в разные → None (меню). dep_id в конфиге — СТРОКИ (JSON-ключи),
# в seen_portal_users.departments — int'ы ([1307]); нормализовать str() в
# одном месте (role_for_departments).

# CRITICAL: живой user.get из sync-FSM — httpx.post(timeout=15.0) в
# try/except Exception → None (флап сети НЕ должен ломать онбординг: нет
# ответа = нет авто-роли = меню). Звать ТОЛЬКО если в seen_portal_users
# пусто (сотрудники вне WATCH_DEPARTMENT_IDS там отсутствуют).

# GOTCHA: «Роль» (ручная смена) сильнее авто до конца сессии — не
# перезатирать роль сессии при каждом сообщении, авто-детект только при
# СОЗДАНИИ сессии/назначении курса.

# ─── №10 ───
# CRITICAL: правило ячейки зафиксировано: сдал ХОТЬ ОДНУ сессию пары → ✅;
# иначе судим ПОСЛЕДНЮЮ (max id): DONE → ❌, не-DONE → 🕐; сессий нет → "".
# Формула сдачи — та же round(total*0.7).

# CRITICAL: колонки = курсы, у которых есть сессии, ∪ активные курсы
# (архивный курс с историей сдач НЕ выпадает из матрицы).

# CRITICAL: xlsx содержит ФИО и результаты — грузить ТОЛЬКО в
# REPORTS_FOLDER_ID (env, приватная папка HR). Пуст → файл НЕ грузим:
# текст + «⚙️ Укажи REPORTS_FOLDER_ID в .env — пришлю отчёт файлом.»
# НЕ фолбэкать в MONITOR_FOLDER_ID — её видят сотрудники.

# CRITICAL: перезапись: meta 'last_report_file_id' → disk.file.markdeleted
# старого ПЕРЕД загрузкой нового (ошибка удаления — глотать: файл в корзине
# или удалён руками). generateUniqueName=true как страховка от коллизии.

# GOTCHA: ✅/❌/🕐 — обычные строки в ячейках (openpyxl юникод ок);
# freeze_panes="B2", ширина по max(len)+2, максимум ~40.

# GOTCHA: openpyxl добавить в requirements.txt (сейчас НЕТ).
# save в BytesIO → .getvalue() (bytes для base64).

# ─── №7 ───
# CRITICAL: ВСЁ за флагом BUTTONS_ENABLED (env, дефолт "0"): выключено →
# _send не кладёт ключ KEYBOARD вообще (не пустой список!), поведение
# байт-в-байт сегодняшнее. Включается на сервере при живой проверке.

# CRITICAL: клавиатура — ф-я СОСТОЯНИЯ, не текста: bot_handler после
# process_message берёт СВЕЖУЮ сессию (переменная after уже есть!) →
# keyboards.for_session(after, user_id). FSM/state_machine НЕ меняются.
# Спец-случай «сессии нет»: если _retake_fork_text(user_id) не None →
# кнопки Пересдать/Далее (импорт из state_machine).

# CRITICAL: один generic COMMAND «say» + COMMAND_PARAMS=payload («A»,
# «Готов», «2», «Выбрать 1») — нажатие эквивалентно набору текста.
# /command-хендлер достаёт USER_ID/DIALOG_ID/COMMAND_PARAMS и вызывает
# ОБЩУЮ корутину обработки — вынести из bot_handler
# _handle_employee_message(user_id, text, dialog_id, client_id) и звать
# из обоих. Dedup _is_duplicate внутри общей корутины (двойной клик).

# CRITICAL: формат формы ONIMCOMMANDADD в выжимке доков НЕ описан —
# /command ОБЯЗАН логировать dict(form) целиком (паттерн /user-webhook) и
# доставать поля defensively: data[COMMAND][0][COMMAND_PARAMS] ИЛИ
# data[PARAMS][COMMAND_PARAMS] — пробовать оба, тесты фиксируют оба.

# GOTCHA: ROLE_SELECT-кнопки — цифры 1..N (17 ролей = 17 кнопок,
# DISPLAY: LINE — Bitrix сам переносит; лимит 30КБ не грозит).
# BLOCK для «Готов», LINE для A-D.

# GOTCHA: scripts/register_commands.py — одноразовый, параметр --url
# (адрес сервера для EVENT_COMMAND_ADD), НЕ запускается автоматически
# (стартап не должен зависеть от успеха регистрации).

# GOTCHA: HR-бот кнопок НЕ получает в этом PRP (обработка hr_handler
# завязана на форму запроса — рефакторинг не окупается до живой проверки
# employee-кнопок). Записано в скоуп-резах.
```

## Implementation Blueprint

### Список задач (в порядке выполнения)

```yaml
Task 1 (№8) — data/roles.json + app/roles.py:
  - roles.json: блок "departments": {} с _comment («ID отдела Битрикса →
    role_id; заполняется по списку клиента; пусто = только меню/память»)
  - roles.py: role_for_departments(dep_ids) -> str | None:
      cfg departments (str-ключи); roles = {cfg.get(str(d)) for d in dep_ids
      if cfg.get(str(d))}; len(roles)==1 → её, иначе None

Task 2 (№8) — state_machine:
  - NEW _role_from_profile(user_id) -> str | None:
      deps_json = get_user_departments(user_id)  # поллер отделов
      if deps_json is None: deps = _live_departments(user_id)  # user.get sync
      else: deps = json.loads(deps_json)
      return role_for_departments(deps or [])
  - NEW _live_departments(user_id) -> list | None: httpx.post user.get
      {"ID": uid}, timeout 15, except Exception → None; UF_DEPARTMENT
  - обе точки (process_message / start_onboarding), вместо
      role = _last_known_role(...):
      role = _role_from_profile(user_id) or _last_known_role(user_id)
      источник профиль → _assign_course(..., remembered=False,
      by_department=True) — текст «✅ Твоя роль: X (по отделу; сменить —
      команда «Роль»)»
  - _assign_course: параметр by_department=False → строка-суффикс

Task 3 (№10) — app/report_excel.py (НОВЫЙ, чистый):
  - build_report_xlsx(rows, employees_by_uid, courses) -> bytes
    (rows = get_report_rows(); courses = колонки: см. гочу об объединении)
  - лист «Матрица»: A=ФИО (или ID …), B=должность, C=роль (последней сессии),
    дальше по курсу на колонку (display_name); freeze B2
  - лист «Детали»: сотрудник, курс, статус (_session_status), базовый,
    экзамен, дата (updated_at[:10]) — по строке на сессию
  - build_report_summary(rows, ...) -> str — короткая сводка «сдали X из Y
    назначенных…» (можно переиспользовать группировку build_report_text —
    ТЕКСТ отчёта не менять, сводка = первые строки текущего текста)

Task 4 (№10) — bitrix_bot доставка:
  - env REPORTS_FOLDER_ID (пустой дефолт); requirements.txt + openpyxl>=3.1
  - РЕФАКТОР: _commit_disk_file(dialog_id, disk_file_id) — тело каскада из
    _send_course_file; _send_course_file → обёртка (тесты №4 зелёные)
  - NEW _send_report_file(dialog_id, xlsx_bytes):
      old_id = get_meta("last_report_file_id") → markdeleted (глотать)
      b64 = base64.b64encode(xlsx_bytes).decode()
      r = POST disk.folder.uploadfile {"id": REPORTS_FOLDER_ID,
          "data": {"NAME": "Отчёт по обучению.xlsx"},
          "fileContent": ["Отчёт по обучению.xlsx", b64],
          "generateUniqueName": True}
      file_id = r.result.ID → set_meta; _commit_disk_file(dialog_id, file_id)
      всё в try/except: фейл → лог + HR получает текст (файл best-effort)
  - ветка «отчёт»: текст как сейчас; if REPORTS_FOLDER_ID:
      asyncio.create_task(_build_and_send_report(dialog_id)) (генерация в
      to_thread — openpyxl не быстрый); else: text += "\n⚙️ Укажи
      REPORTS_FOLDER_ID..."

Task 5 (№7) — app/keyboards.py (НОВЫЙ, чистый) + флаг:
  - BUTTONS_ENABLED в bitrix_bot: os.getenv("BUTTONS_ENABLED", "0") == "1"
  - keyboards.py: _btn(text, payload, display="LINE") →
      {"TEXT": text, "COMMAND": "say", "COMMAND_PARAMS": payload,
       "DISPLAY": display, "BG_COLOR": "#29619b", "TEXT_COLOR": "#fff"}
    for_session(session, fork_text_present: bool, role_options) -> list|None:
      session None + fork → [Пересдать, Далее]
      session None → None
      state ROLE_SELECT → цифры 1..len(role_options)
      state READING → [Готов (BLOCK), Мои курсы, Роль]
      state BASIC_TEST/EXAM → [A, B, C, D]
      state WAITING_HR → [Мои курсы]
  - bitrix_bot._send(..., keyboard=None): if keyboard: payload["KEYBOARD"] =
      keyboard (ключа нет вовсе при None)

Task 6 (№7) — общий хендлер + /command + регистрация:
  - вынести из bot_handler корутину _handle_employee_message(user_id,
    question, dialog_id, client_id): dedup → before → process_message →
    файл курса → клавиатура (if BUTTONS_ENABLED: after=get_session,
    fork=_retake_fork_text(user_id) is not None if after is None else False,
    kb=keyboards.for_session(...)) → _send(..., keyboard=kb)
  - bot_handler: разбор формы + вызов корутины (поведение 1:1)
  - NEW POST /command: print(dict(form)); event=="ONIMCOMMANDADD" (upper);
    params = form.get("data[COMMAND][0][COMMAND_PARAMS]") or
             form.get("data[PARAMS][COMMAND_PARAMS]") or ""
    user_id/dialog_id — аналогично двумя путями; пусто → {"status":"ignored"}
    → _handle_employee_message(...)
  - NEW scripts/register_commands.py: argparse --url; POST
    imbot.command.register {BOT_ID, CLIENT_ID: BOT_CLIENT_ID, COMMAND:"say",
    COMMON:"N", HIDDEN:"Y", EXTRANET_SUPPORT:"N",
    EVENT_COMMAND_ADD: url+"/command",
    LANG:[{"LANGUAGE_ID":"ru","TITLE":"Ответ кнопкой"}]}; печать результата

Task 7 — тесты:
  - test_prefix_parse.py или НОВЫЙ test_autorole.py: role_for_departments
    (один отдел; два→одна роль; два→разные→None; пусто/незнакомый→None;
    int vs str ключи); FSM: seen-отделы → сессия сразу READING с ролью,
    «(по отделу)» в ответе; конфиг пуст → меню как раньше; live user.get
    замокан (sm.httpx.post) — сбой → меню
  - НОВЫЙ test_report_excel.py: чистый build_report_xlsx →
    load_workbook(BytesIO(...)): ✅ у сдавшего, ❌ последняя провалена,
    ✅ при пересдаче после провала (лучший результат), 🕐 в процессе,
    "" не начинал; колонка архивного курса с сессией есть; лист «Детали»
  - test_hr_invite.py-стиль или НОВЫЙ test_report_delivery.py (TestClient +
    FakeAsyncClient): «Отчёт» с REPORTS_FOLDER_ID → uploadfile вызван с
    base64, meta записан, старый file_id markdeleted, commit вызван;
    без REPORTS_FOLDER_ID → только текст с подсказкой
  - НОВЫЙ test_buttons.py: for_session по всем состояниям (чистый);
    флаг выключен → в payload _send НЕТ KEYBOARD (FakeAsyncClient);
    включён (monkeypatch bot.BUTTONS_ENABLED, True) → KEYBOARD с A-D в
    BASIC_TEST; /command оба формата полей → ответ ушёл, dedup двойного
    клика (второй POST тем же params — «DEDUP skip»)
  - существующие тесты НЕ трогать (флаг выключен по умолчанию)

Task 8 — .env.example (REPORTS_FOLDER_ID, BUTTONS_ENABLED с комментами),
  task.md: чеклист живого прогона (см. Validation L3)
```

### Integration Points
```yaml
CONFIG: .env REPORTS_FOLDER_ID="", BUTTONS_ENABLED="0"; roles.json departments
DB:     meta 'last_report_file_id'; схема НЕ меняется
ROUTES: POST /command (новый); "/" рефакторится без изменения поведения
DEPS:   requirements.txt + openpyxl>=3.1
```

## Validation Loop
```bash
# L1/L2
ruff check app/ scripts/ tests/ --fix
python -m pytest tests/ -v     # 175 существующих + новые
```
```bash
# L2.5 офлайн-смоук xlsx на живых данных (БД не мутируется):
python - <<'EOF'
from app.db import get_report_rows, get_all_employees, get_active_courses
from app.report_excel import build_report_xlsx
data = build_report_xlsx(get_report_rows(),
                         {e["bitrix_uid"]: e for e in get_all_employees()},
                         get_active_courses())
open("/tmp/report_smoke.xlsx", "wb").write(data)   # открыть глазами
EOF
```
L3 (сервер, task.md): №8 — сотрудник отдела из departments получает роль без
меню; №10 — «Отчёт» приносит файл, второй запуск заменяет файл; №7 —
register_commands.py → BUTTONS_ENABLED=1 → нажатия A-D/Готов работают, лог
/command показывает реальный формат события (поправить разбор при
расхождении), мобильный рендер.

## Anti-Patterns to Avoid
- ❌ Авто-роль НЕ перезатирает ручную «Роль» внутри сессии
- ❌ Никаких вторых формул сдачи/назначенности — hr_tools/state_machine
- ❌ xlsx НЕ грузить в MONITOR_FOLDER_ID (ФИО+результаты видны сотрудникам)
- ❌ KEYBOARD-ключ не появляется при выключенном флаге (даже пустым)
- ❌ /command не падает на неожиданном формате — лог + ignored
- ❌ Регистрацию команд НЕ звать на старте приложения

## Открытые допущения
1. №10: формат матрицы — MVP (✅/❌/🕐 + «Детали»); вопрос клиенту «нужны ли
   колонки дата/балл в матрице» из INITIAL остаётся открытым.
2. №8: приоритет «отдел > память сессии» — считаю верным (перевод меняет
   роль); если Дмитрий захочет иначе — один swap строк.
3. №7: точный формат полей ONIMCOMMANDADD подтверждается живым логом; разбор
   написан на два известных варианта.
4. Кнопки HR-боту — следующий шаг после живого подтверждения employee-кнопок.

## Score: 7/10
№8 и №10 — низкий риск (формат uploadfile подтверждён локальной докой, все
паттерны отработаны; минус — commit свежезагруженного файла в чат проверен
только для лежащих на Диске). №7 — код за флагом с защитным разбором, но
живое поведение ONIMCOMMANDADD принципиально непроверяемо до сервера — потому
и флаг. Рефакторинг bot_handler в общую корутину — главное место, где можно
сломать существующее: гейт = 175 зелёных тестов.
