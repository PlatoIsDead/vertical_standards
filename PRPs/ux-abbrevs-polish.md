name: "Протокол 05.08, пакет 1: «буковки»/BB-код, выбор курса сотрудником, группировка отчёта по отделам, 16 аббревиатур"
description: |
  Четыре доработки из протокола Дмитрия Челнокова (email 07-10.08): показ текста
  правильного ответа в «Вопросы N» + починка разметки (Bitrix не рендерит
  markdown-звёздочки), выбор курса сотрудником («Выбрать N» из «Мои курсы»),
  группировка «Отчёта» по отделам, полный реестр 16 аббревиатур департаментов.

## Purpose
Одно-проходная реализация. База — код после cdff728 (№11 + demo-feedback-pack:
инверсия роль→курс, parse_filename, «Мои курсы», рассылка при активации).

## Core Principles
1. **Context is King** — сниппеты и точки правок ниже
2. **Validation Loops** — ruff + pytest; рендер BB-кода — живой чеклист
3. **Information Dense** — имена из реального кода
4. **Progressive Success** — A (буковки) → D (аббревиатуры) → B (выбор) → C (отчёт)
5. **Global rules** — CLAUDE.md; UI-тексты на русском

## НЕ в скоупе
- №9 дедлайны/напоминания/эскалации — ПАРНЫЙ PRP `PRPs/deadlines-reminders-escalation.md`
- №7 кнопки KEYBOARD (живая API-неопределённость, после деплоя на сервер),
  №8 (роль из отдела — нет ID отделов и должностей), №10 Excel, №6 деплой

---

## Goal

HR в «Вопросы 12» видит не «→ A», а «→ A. Бежать» — может проверить
корректность. Сообщения ботов в чате Bitrix показывают **жирный** текст, а не
литеральные звёздочки. Сотрудник в «Мои курсы» видит нумерованный список и
переключается командой «Выбрать 2» (текущий курс НЕ помечается пройденным).
«Отчёт» сгруппирован секциями по отделам. Файл с любым из 16 префиксов
(включая «F&B», «S&M», «Rev», «Pur») получает правильную роль.

## Why

- **A** — прямой вопрос Дмитрия с демо: «ответ A на 1-й вопрос правильно —
  а что написано в этом ответе? Как HR должен проверить?» Плюс «артефакты»
  из протокола: чат Bitrix НЕ рендерит markdown — наши `*звёздочки*` торчат
  в тексте буквально (Bitrix понимает BB-код `[b]...[/b]`).
- **B** — протокол: «работа с несколькими курсами»; юзер: «employee can
  choose what test to do». Сейчас курс назначается строго очередью.
- **C** — протокол: «аналитика/группировка по отделам в отчёт». Роль в строке
  уже есть (demo-pack C), группировки нет.
- **D** — клиент прислал полный список аббревиатур («на перспективу
  закладываем») — письмо 07.08, 16 шт.

## What / Success Criteria
- [ ] «Вопросы N»: каждая строка `{i}. {текст} → {correct}. {текст варианта}`
- [ ] Все исходящие сообщения обоих ботов: `*x*` → `[b]x[/b]` в ЕДИНЫХ точках
      отправки (_send, notify_hr) — по коду ни один `*` не долетает до Bitrix
- [ ] «Мои курсы»: доступные непройденные курсы пронумерованы; «Выбрать 2» в
      READING переключает сессию (q_idx=0, READING), старый курс НЕ в done;
      «Выбрать» в тесте — вежливый отказ («сначала закончи тест»)
- [ ] «Отчёт»: секции «— Отдел бронирования —» по роли сессии, внутри строки
      как сейчас; сессии без роли — секция «Без роли»
- [ ] roles.json: 16 префиксов (вкл. `F&B`, `S&M`, `SAL`, `REV`, `PUR`),
      11 новых ролей; `parse_filename("F&B, CAT Банкеты.docx")` → 2 роли
- [ ] `python -m pytest tests/ -v` зелёный (сейчас 134), `ruff check` чистый

## All Needed Context

### Documentation & References
```yaml
- file: app/bitrix_bot.py
  why: |
    «Вопросы N» (~строка 672-681 в hr_handler): lines.append(f"{i}. {q['text']}
    → {q['correct']}") — сюда текст варианта. Формат options: "A. Бежать" —
    вариант ищется по startswith(correct + "."). _send (~431) — ЕДИНАЯ точка
    отправки бота: конвертация MESSAGE перед payload. «Пригласить»-ветка/
    рассылка/эскалации шлют через _send — покрываются автоматически.

- file: app/state_machine.py
  why: |
    notify_hr (~335) — ВТОРАЯ точка отправки (sync, httpx.post напрямую) —
    конвертировать и здесь. _my_courses_text — нумерация для «Выбрать N».
    _handle_reading — интерсепт «выбрать N» рядом с _MENU_COMMANDS.
    _handle_test — отказ на «выбрать» во время теста. pick_course_for_role /
    course_roles / _done_course_ids — переиспользовать. _start_reading —
    приветствие при переключении курса. bot_handler bitrix_bot.py:~486 шлёт
    файл курса при ПОЯВЛЕНИИ course_id (before/after) — при переключении
    курса before уже имеет course_id → файл НЕ уйдёт сам: слать из
    переключения нельзя (state_machine sync, без сети) — see Gotchas.

- file: app/hr_tools.py
  why: |
    build_report_text — группировка: rows уже содержат s.role; сортировка
    стабильная по (role, updated_at DESC). role_name уже импортирован.
    Лимит 30 строк сохранить (лимит длины сообщения Bitrix неизвестен).

- file: app/roles.py + data/roles.json
  why: |
    parse_filename: токены upper() → ключи prefixes UPPERCASE («F&B», «S&M» —
    `&` не разделитель, partition по пробелу/запятой, работает как есть).
    selectable_roles — меню вырастет до 15 пунктов (без all_staff) — ОСТАВИТЬ
    полный список (роль нужна и для RAG-фильтра, не только курсов; №8 заменит
    меню авто-ролью). display_name/role_name — без изменений.

- file: tests/test_prefix_parse.py, test_state_machine.py, test_hr_tools.py,
        test_hr_invite.py
  why: паттерны фикстур; фикстурные roles.json содержат СВОИ prefixes —
       новые аббревиатуры тестировать через реальный-подобный конфиг в tmp.

- url: https://apidocs.bitrix24.ru/api-reference/chat-bots/messages/imbot-message-add.html
  why: imbot.message.add — BB-коды в MESSAGE ([B]/[I]/[URL]); markdown НЕ
       поддержан. Проверка рендера — живой чеклист (task.md).
```

### Known Gotchas
```python
# CRITICAL (A): конвертация *x* → [b]x[/b] строго в ДВУХ точках выхода:
# bitrix_bot._send и state_machine.notify_hr. НЕ по всем строкам кода — их
# десятки, и тесты сверяют тексты со звёздочками (менять тесты не нужно:
# конвертация НА ВЫХОДЕ, внутренние тексты остаются с *).
# Регэксп: re.sub(r"\*([^*\n]+)\*", r"[b]\1[/b]", text) — нежадный, без
# переносов внутри; «A*B» без закрытия — не трогается.

# CRITICAL (A): в тестах _send мокается (fake_send) — конвертация не покрыта
# моками TestClient. Тестировать конвертер как ЧИСТУЮ функцию (md_to_bb) +
# один тест, что _send реально зовёт его (не мокать _send, мокать httpx).

# CRITICAL (B): «Выбрать N» НЕ должен помечать текущий курс пройденным —
# просто update_session(course_id=new, state="READING", q_idx=0). Прогресс
# чтения не теряется (в READING прогресса нет); в BASIC_TEST/EXAM/WAITING_HR
# переключение ЗАПРЕЩЕНО («сначала закончи тест / дождись HR»).

# CRITICAL (B): нумерация «Выбрать N» должна быть той же, что в «Мои курсы» —
# единый источник: функция возвращает и текст, и упорядоченный список курсов.
# Показывать номера ТОЛЬКО у доступных к выбору (непройденных, не текущего).

# GOTCHA (B): файл документа при переключении не уедет сам (before-сессия уже
# с course_id — хук в bot_handler не сработает). Честно: в тексте переключения
# дать ссылку doc_detail_url (как _start_reading уже делает) — этого хватает;
# отдельную отправку файла НЕ городить (state_machine — sync, без сети).

# GOTCHA (C): группировка по r["role"] сессии (роль на момент прохождения).
# Секции сортировать по русскому имени роли; «Без роли» — последней. Лимит
# 30 строк-сессий сохранить (не считая заголовков секций).

# GOTCHA (D): ключи prefixes — UPPERCASE ровно как токен после .upper():
# «Fin»→«FIN», «Rev»→«REV», «Pur»→«PUR», «F&B»→«F&B», «S&M»→«S&M».
# SAL оставить (клиент писал SAL в живых файлах) И добавить S&M — оба → sales.
# ADM → НОВАЯ роль administration «Администрация»; general_manager НЕ трогать
# (legacy-роль, живёт в старом индексе). HR-аббревиатура → роль hr_dept
# (id «hr» слишком общий? НЕТ — id "hr" допустим, конфликтов нет: role_id
# нигде не сравнивается с чем-то ещё; берём "hr").
# Решения зафиксированы юзером неявно («generate for all») — отметить в
# отчёте юзеру: SAL+S&M оба, ADM отдельной ролью.

# GOTCHA (D): русские имена ролей — из формулировок клиента (письмо 07.08).
```

## Implementation Blueprint

### data/roles.json (целевой вид блоков)
```json
"roles": {
  "housekeeper": "Горничная / Уборщица (HSKP)",
  "admin_reception": "Служба приёма и размещения (FO)",
  "engineer": "Инженерно-техническая служба (ENG)",
  "general_manager": "Администратор / Управляющий",
  "reservations": "Отдел бронирования (RES)",
  "sales": "Отдел продаж и маркетинга (S&M)",
  "finance": "Финансы и учёт (Fin)",
  "guest_relations": "Работа с гостями / GR",
  "fnb": "Питание (F&B)",
  "catering": "Банкеты и мероприятия (CAT)",
  "revenue": "Управление доходностью (Rev)",
  "pr": "Связи с медиа и PR",
  "administration": "Администрация (ADM)",
  "hr": "Отдел кадров (HR)",
  "purchasing": "Закупки и снабжение (Pur)",
  "it": "IT-поддержка",
  "security": "Охрана и безопасность (SEC)",
  "all_staff": "Все сотрудники"
},
"prefixes": {
  "FO": "admin_reception", "RES": "reservations", "GR": "guest_relations",
  "HSKP": "housekeeper", "ENG": "engineer", "F&B": "fnb",
  "S&M": "sales", "SAL": "sales", "CAT": "catering", "REV": "revenue",
  "PR": "pr", "ADM": "administration", "HR": "hr", "FIN": "finance",
  "PUR": "purchasing", "IT": "it", "SEC": "security", "ALL": "all_staff"
}
```
(Имена ролей — формулировки клиента, аббревиатура в скобках помогает HR
сопоставить меню с именами файлов. folders не трогать.)

### List of tasks (в порядке выполнения)

```yaml
Task 1 (A) — конвертер разметки:
  - app/roles.py НЕ трогать; НОВАЯ чистая функция в app/hr_tools.py?
    НЕТ — hr_tools про HR. Положить в app/bitrix_bot.py?
    Решение: app/state_machine.py — плоский helper md_to_bb(text) наверху
    (оба потребителя импортируют state_machine или соседи):
      _BOLD_RE = re.compile(r"\*([^*\n]+)\*")
      def md_to_bb(text): return _BOLD_RE.sub(r"[b]\1[/b]", text)
  - state_machine.notify_hr: httpx.post(... "MESSAGE": md_to_bb(message) ...)
  - bitrix_bot._send: payload["MESSAGE"] = md_to_bb(text)
    (импорт: from app.state_machine import md_to_bb — bitrix_bot уже
    импортирует state_machine)

Task 2 (A) — «Вопросы N» с текстом правильного варианта:
  - bitrix_bot hr_handler, ветка «вопросы» (двумя циклами basic/exam):
      корректный вариант: next((o for o in q["options"]
        if o.strip().upper().startswith(q["correct"] + ".")), q["correct"])
      lines.append(f"{i}. {q['text']}\n   → {opt}")
  - option без точки («A) …»/кривой) → фолбэк буква как раньше

Task 3 (D) — data/roles.json: блоки как выше; тест в test_prefix_parse.py
  с реальным конфигом-подобной фикстурой: «F&B, CAT Банкеты.docx» → [fnb,
  catering]; «S&M Продажи.docx» и «SAL Продажи.docx» → [sales]; «Rev
  Тарифы.docx» → [revenue]

Task 4 (B) — выбор курса:
  - state_machine: _my_courses_text ПЕРЕПИСАТЬ на пару:
      def my_courses(user_id, role_id) -> tuple[str, list[dict]]:
        # текст + упорядоченный список ДОСТУПНЫХ к выбору (не done, не текущий)
        # доступные строки: «{n}. ⏳ {name}» (n — индекс в списке выбора);
        # текущий: «▶️ {name} — проходишь сейчас»; пройденные: «✅ …»
        # хвост: «Переключиться: напиши *Выбрать {номер}*» (если есть доступные)
      _my_courses_text(user_id, role) = my_courses(...)[0]  # совместимость
  - _handle_reading: интерсепт re.match(r"^выбрать\s+(\d+)$", cmd) →
      _handle_course_switch(session, n):
        _, selectable = my_courses(...); 1<=n<=len → course = selectable[n-1]
        update_session(session["id"], course_id=course["id"],
                       state="READING", q_idx=0)
        return "🔄 Переключил курс.\n\n" + _start_reading(session, course)
      мимо диапазона → показать «Мои курсы» заново
  - _handle_test: cmd.startswith("выбрать") → «Сначала закончи текущий тест 🙂»
  - _handle_waiting_hr: «выбрать …» → «Дождись решения HR по текущему курсу»

Task 5 (C) — группировка отчёта:
  - hr_tools.build_report_text: rows[:30] сгруппировать по r.get("role"):
      порядок секций: по role_name(...), «Без роли» последняя
      заголовок секции: f"— {role_name(role)} —"
      внутри — существующий формат строк (label уже с ролью? УБРАТЬ
      « · Роль» из строки — роль теперь в заголовке секции, не дублировать)
  - тест test_report_label_with_position_and_role ОБНОВИТЬ: роль в заголовке
    секции, не в строке

Task 6 — тесты:
  - test_state_machine: md_to_bb чистая (жирный, два вхождения, незакрытая
    звёздочка не тронута, \n внутри не матчится); «выбрать 1» переключает
    (course_id сменился, старый НЕ в done, q_idx=0); «выбрать 99» →
    «Мои курсы»; «выбрать» в BASIC_TEST → отказ; нумерация в тексте
    соответствует selectable-списку
  - test_hr_invite/test_course_broadcast НЕ трогать (моки _send)
  - НОВЫЙ мини-тест конвертации на выходе: monkeypatch httpx в
    state_machine → notify_hr("*жирно*") → в payload "[b]жирно[/b]"
    (bitrix_bot._send аналогично через FakeAsyncClient из test_folder_sync)
  - test_hr_tools: секции отчёта (две роли + без роли, порядок, лимит 30)
  - «Вопросы N»: TestClient-тест в test_hr_edit_flow-стиле — в ответе
    «→ A. Бежать»

Task 7 — task.md: чеклист живого прогона (рендер [b] в чате обоих ботов,
  «Выбрать» с телефона, секции отчёта, файл «F&B, CAT ....docx» в папке)
```

### Integration Points
```yaml
DATABASE: нет изменений схемы
CONFIG:   data/roles.json (tracked с cdff728) — единственный конфиг
ROUTES:   без новых; меняются тексты /hr «вопросы», FSM-команды
```

## Validation Loop
```bash
ruff check app/ scripts/ tests/ --fix
python -m pytest tests/ -v          # 134 существующих зелёные + новые
```
Живое (task.md): рендер [b] в чате; если Bitrix покажет литеральный BB —
откатить md_to_bb на strip-звёздочек (`\1` вместо `[b]\1[/b]`) — одна строка.

## Anti-Patterns to Avoid
- ❌ Не менять *звёздочки* по всем строкам кода — только конвертер на выходе
- ❌ Не помечать курс DONE при переключении — done только через экзамен
- ❌ Не сокращать меню ролей до «ролей с курсами» — роль нужна и для RAG
- ❌ Не дублировать роль в строке И заголовке секции отчёта
- ❌ Не выкидывать SAL из prefixes — живые файлы клиента им размечены

## Score: 8/10
Всё на существующих паттернах; минус — рендер BB-кода подтверждается только
живьём (есть однострочный откат), и «Выбрать N» трогает горячий FSM-путь.
