name: "Пакет целостности 01.09: укрепление инвайт-флоу (по ultra-ревью), единая семантика ролей, UX-постоянство (фидбек юзера: «Помощь» в паузе, экзамен≠тест, «Править руководителей», A–D невпопад)"
description: |
  Два источника болей. (1) Живой фидбек юзера 01.09: «Помощь» в отложенном
  тесте возвращает тестовую клавиатуру A–D; экзамен «менее постоянен» —
  все тексты/кнопки паузы говорят «тест»; кнопка «Править руководителей»
  показывает список вместо подсказки правки; буквы A–D видны, когда уже
  нерелевантны (напоминание о дедлайне с A–D-кнопками). (2) Ultra-ревью
  (локальный max-обзор, 15 находок, 3 вердикта CONFIRMED) незакоммиченного
  диффа «HR выбирает роль при „Пригласить“»: приглашение живёт только в
  in-memory pending и молча гибнет; дедуп-ключ асимметричен; «Роль»
  сотрудника затирает HR-назначение; pop pending до _finish_invite без
  try/except; второй HR молча перезаписывает роль; HR не может сменить роль
  обучающегося; потребители роли (рассылки/дедлайны/меню) не переведены на
  новый приоритет.

## Purpose
Одно-проходная реализация ПОВЕРХ незакоммиченного рабочего дерева 01.09
(HR-выбор роли при «Пригласить», employees.role, 285 тестов зелёные).
Всё — правки существующих механик, без новых подсистем и без миграций
(колонка employees.role уже в дереве).

## Core Principles
1-5 как всегда. Два железных правила пакета:
- **Клавиатура соответствует сообщению**: A–D прикладываются ТОЛЬКО к
  сообщению, в котором есть вопрос теста; всё остальное — навигация.
- **employees.role пишет только HR** (инвайт или новая команда «Роль
  {email}»); «Роль» сотрудника — временный просмотр, сессионный.

## НЕ в скоупе
- Гашение A–D на СТАРЫХ сообщениях через imbot.message.update (кнопки
  Bitrix живут на сообщении вечно) — отдельная работа после живой проверки,
  что портал позволяет update чужих клавиатур; отметить в task.md.
- Персистентный pending (переживание рестарта): pending остаётся in-memory,
  риск гасим уведомлениями о дропе + честными текстами. БД-таблица pending —
  отдельная работа, если клиент попросит.
- BB-инъекция через fio в HR-сообщениях (low, нужен контроль профиля
  портала) — отметить в task.md.

---

## Goal

### A. Укрепление инвайт-флоу (bitrix_bot.py)

**A1. Снапшот ролей в pending.** «Пригласить» кладёт в pending снимок
`options: [(rid, name), ...]` (список, который HR реально видел);
`_invite_role_input` резолвит цифру и строит reprompt-клавиатуру ИЗ
СНАПШОТА, а не из живого `selectable_roles()`. Ветка «конфиг опустел между
шагами» (строки ~667-672) удаляется — снапшот делает её невозможной.

**A2. Pop после успеха + try/except.** В `_invite_role_input` при валидной
цифре: pending НЕ снимать до завершения; `await _finish_invite(...)`
обернуть `try/except Exception` → лог + возврат HR «⚠️ Не получилось
завершить приглашение {fio} (сбой связи) — ответь цифрой ещё раз или
«Отмена»» с продлённым `expires` (pending жив). Успех → pop. Паттерн —
визард: pop только после успешного сохранения (`_wizard_input`, ветка
«сохранить»). `_finish_invite` идемпотентен при ретрае: add_employee —
upsert, set_employee_role — UPDATE, start_onboarding не создаёт вторую
сессию при живой.

**A3. Симметричный дедуп-ключ.** Сейчас (`_handle_hr_message`, блок
`wiz = _get_pending(...)`) суффикс `#step{...}` вешается на ЛЮБОЕ сообщение
при живом pending — CONFIRMED-инверсии: ретрай «Пригласить» не дедупится
(двойной user.get + двойной промпт), одинаковая цифра двух приглашений
подряд глотается. Правило: суффикс — ТОЛЬКО для сообщений, которые реально
уйдут в pending-обработчик (не «отмена»/«cancel», не «меню», не
`startswith(_HR_COMMAND_PREFIXES)`); суффикс различает pending'и:
визард → `#step{step}` (как сейчас), invite → `#inv{uid}`,
role_change (A5) → `#rc{uid}`. Итог: ретрай «Пригласить» дедупится по
bare-ключу; «1» для a@x и «1» для b@x — разные ключи.

**A4. «Уже проходит обучение» — без записи роли.** В `_finish_invite`
проверку `get_session(uid)` поднять ДО `set_employee_role` (add_employee-
refresh email/ФИО оставить): второй HR в гонке получает
«ℹ️ {fio} уже проходит обучение (роль: {role_name}). Сменить роль: Роль
{email}» — и НИЧЕГО не перезаписывает. Ту же подсказку — в pre-pending
гейт invite-ветки.

**A5. Новая HR-команда «Роль {email|ID}» — смена роли в любой момент.**
Закрывает «HR ошибся цифрой и не может исправить». Ветку вставить ПОСЛЕ
`^роль\s+(разрешить|запретить)` (regex нового: `^роль\s+(?!разрешить|запретить)(\S+)$`
по msg_lower; «роль» уже в `_HR_COMMAND_PREFIXES`). Флоу: резолв сотрудника
(email → get_employee_by_email, иначе get_employee по ID; не найден →
«Сначала: Пригласить {email}») → pending `{kind: "role_change", uid, fio,
options: снапшот}` → ТОТ ЖЕ нумерованный промпт/клавиатура → цифра →
`set_employee_role` + подтверждение HR «✅ Роль {fio} теперь {name} —
применится со следующего курса» + сообщение сотруднику от employee-бота
(«ℹ️ HR назначил тебе роль {name}. Текущий курс не прерывается; следующие
курсы — по новой роли»). Сессии НЕ трогаем. Обработчик выбора цифры
обобщить: общий парсинг/reprompt, разный finisher по `kind`.

**A6. Честные тексты «Отмена» и дропа pending.** «Отмена» invite-pending:
если `is_employee_allowed(uid)` (повторное приглашение DONE-сотрудника,
CONFIRMED) → «Приглашение {fio} отменено; ранее выданный доступ и роль
сохраняются.», иначе «Приглашение {fio} отменено — доступ не выдан.»;
role_change → «Смена роли отменена.». Дроп pending командой («команда
важнее») или кнопкой «🏠 Меню»: если kind invite/role_change — ПЕРЕД
обработкой команды отправить отдельное «⚠️ Выбор роли для {fio} отменён —
приглашение не завершено. Повтори: Пригласить {email}» (для role_change —
соответствующее). «меню» (точное совпадение, не префикс!) добавить в
эскейпы pending-перехвата: дроп с уведомлением, дальше сообщение идёт
обычным путём (упадёт в help с hr_main_menu — рабочий выход из цикла;
чинит и старый цикл визарда).

**A7. Гейт «курсов нет» — по РОЛИ, честные исходы.** Находка ревью:
активны только курсы горничных, HR приглашает СПиР → HR видит «✅ приглашён —
отправил первое сообщение», сотрудник видит «курсов для роли нет», плюс
ложный notify_hr «запросил обучение» с голым ID. Правки:
- `_assign_course` (state_machine): при `by_hr=True` и course None —
  НЕ звать notify_hr (HR сам это только что сделал), текст сотруднику:
  «👋 HR добавил тебя в обучение (роль: {name}). Подходящих курсов пока
  нет — напишу, когда появятся.» Механика маркера (course_id=0, DONE,
  already_flagged) не трогать.
- `_finish_invite`: после start_onboarding проверить `get_session(uid)` —
  открытой сессии нет (маркер) → HR-текст «✅ {fio} добавлен (роль:
  {name}). Курсов для этой роли пока нет — сотрудник получит анонс при
  активации», НЕ «отправил первое сообщение».

### B. Единая семантика ролей (state_machine.py + bitrix_bot.py)

**B1. «Роль» сотрудника НЕ пишет employees.role.** Убрать
`set_employee_role` из `_handle_role_select` (внесён 01.09 — ревью
подтвердило: «посмотреть другую роль» превращается в постоянную смену и
затирает выбор HR). By design: employees.role — только HR; смена в меню
живёт в session.role до следующего курса. В ответ `_handle_role_select`
при живой `_hr_assigned_role` добавить «(временно, до следующего курса —
постоянную роль назначает HR)». Побочный эффект: ветка remembered
(«помню с прошлого обучения») снова достижима для легаси-сотрудников —
вернуть тест (B4).

**B2. Общий резолвер роли.** В state_machine:
```python
def resolve_role(user_id) -> tuple[str | None, str | None]:
    # («роль», источник "hr"|"department"|"memory"|None)
    # hr → отдел → память сессии; сеть — только внутри _role_from_profile
```
Назначение курса (`_dispatch` session-None и `start_onboarding`) — через
него, источник определяет текст (`by_hr`/`by_department`/`remembered`).
Потребители-циклы БЕЗ сети: `resolve_role_cached(uid)` =
`_hr_assigned_role(uid) or _last_known_role(uid)` (dept-часть может ходить
в user.get — в цикле по всем сотрудникам это флап-риск; dept-маппинг в
проде пуст). Перевести на cached: `_course_recipients`,
`_standard_recipients`, `_deadline_items` (bitrix_bot: словарь
roles_by_uid), menu/docs-ветки `_dispatch` (строки ~146-148) и
menu-клавиатурную ветку `_handle_employee_message` (роль при after None).

**B3. Честные тексты про источник роли.** С B1 «(назначил HR)» становится
правдой всегда — оставить и добавить хвост «; вопросы по роли — к HR».
HR-текст «Роль запретить» (bitrix_bot, ~1512): «она определяется отделом»
→ «роль назначает HR (или отдел Битрикса)».

### C. UX-постоянство (фидбек юзера)

**C1. Клавиатура по содержимому сообщения.** В `_handle_employee_message`
(блок №7): если `after` в BASIC_TEST/EXAM, но в ответе НЕТ вопроса
(`re.search(r"— Вопрос \d+/\d+", text)` пуст — маркер format_question) →
вместо A–D прикладывать `courses_menu(..., resume_test=True)` (та же
клавиатура, что сейчас на «Мои курсы»/«Выйти» из теста). Покрывает
«Помощь», «Документы», RAG-ответы — и в паузе, и посреди теста. Tradeoff
(зафиксировать в комментарии): RAG-вопрос посреди активного теста теряет
A–D под ответом — возврат одним тапом [▶ Продолжить] показывает вопрос
заново; интуитивность > скорость. Существующее условие is_menu/выйти
станет частным случаем — объединить, не дублировать построение menu_kb.

**C2. Экзамен называется экзаменом.** Фазовые тексты:
- `_handle_test`, ветка «выйти»: label = «Экзамен» if phase=="exam" else
  «Тест» → «⏸ {label} на паузе (вопрос …)».
- `keyboards.for_session` BASIC_TEST/EXAM: TEXT кнопки «⏸ Отложить тест» /
  «⏸ Отложить экзамен» по state (payload «Выйти» НЕ менять).
- `keyboards.courses_menu`: параметр `resume_exam=False` → TEXT
  «▶ Продолжить экзамен» (payload «Продолжить» не менять); прокинуть из
  `_handle_employee_message` по `after["state"] == "EXAM"`.
- `_STATE_HINTS["EXAM"]`: «*Выйти* — пауза экзамена».

**C3. «Выйти» вне теста — не RAG.** В `_handle_reading`,
`_handle_exam_ready`, `_handle_waiting_hr`: `cmd in ("выйти", "выход")` →
короткое «Сейчас нет активного теста — откладывать нечего.» (БЕЗ своего
«Что дальше» — авто-hint `_with_hint` допишет правильный по состоянию).

**C4. «Руководитель» → подсказка правки.** В ветке
`startswith("руководител")`: `len(parts) == 1` разделить —
`msg_lower == "руководители"` → список (как сейчас); `== "руководитель"`
(payload кнопки «⚙️ Править руководителей») → существующий else-текст
«⚙️ Напиши действие и email…».

**C5. Напоминания без A–D.** `_maybe_send_reminders`: вместо
`_session_keyboard(uid)` — хелпер `_reminder_keyboard(uid)`: состояние
тестовое → `[▶ Продолжить тест/экзамен → «Продолжить»]` (start_button,
label по фазе) + придёт универсальная «🏠 Меню»; иначе — прежний
`_session_keyboard(uid)`.

**C6. Статус начатого экзамена.** `my_courses`: убрать условие `q_idx > 0`
— любая EXAM-строка = «⏸ {name} — экзамен на вопросе {q_idx+1}» (status
"paused"); «🎓 допущен к экзамену» остаётся только для EXAM_READY.

**C7. Мёртвый код `_handle_done`.** `get_session` фильтрует DONE
(db.py ~283) — ветка `state == "DONE"` в `_dispatch` и функция
`_handle_done` недостижимы; удалить (≈20 строк) с комментарием почему.

## What / Success Criteria
- [ ] Ретрай вебхука «Пригласить» дедупится (один промпт, один user.get);
      «1»+«1» для двух приглашений подряд — оба сработали
- [ ] Сбой внутри `_finish_invite` → pending жив, HR получает «попробуй ещё
      раз», повторная цифра завершает инвайт (без дублей employee/сессии)
- [ ] Цифра резолвится по снапшоту options из pending
- [ ] Второй HR при гонке: «уже проходит обучение (роль: X)», employees.role
      НЕ изменился
- [ ] «Роль {email}» → промпт → цифра → employees.role обновлён, сотрудник
      уведомлён, сессии не тронуты; «Роль разрешить/запретить» работают
      как раньше
- [ ] «Отмена»: с доступом — «доступ сохраняется», без — «доступ не выдан»;
      дроп pending командой/«Меню» шлёт «⚠️ … не завершено»
- [ ] Инвайт с ролью без курсов ДЛЯ НЕЁ: HR-текст «курсов для роли нет»,
      notify_hr «запросил обучение» не шлётся
- [ ] «Роль» сотрудника не пишет employees.role; на следующем курсе
      возвращается HR-роль; легаси без employees.role — «помню» (тест)
- [ ] Рассылки/дедлайны/меню считают роль hr→память (resolve_role_cached)
- [ ] «Помощь»/«Документы»/RAG в тесте и паузе → клавиатура
      [▶ Продолжить …]+курсы, НЕ A–D; ответ с вопросом → A–D как раньше
- [ ] В экзамене: «⏸ Экзамен на паузе», «⏸ Отложить экзамен»,
      «▶ Продолжить экзамен», hint «пауза экзамена»
- [ ] «Выйти» в READING/EXAM_READY/WAITING_HR → «нет активного теста»
      (не RAG)
- [ ] Кнопка «Руководитель» → подсказка формата; «Руководители» → список
- [ ] Напоминание сотруднику в тесте — без A–D, с [▶ Продолжить …]
- [ ] `python -m pytest tests/ -q` (285 + новые), `ruff check app tests`

## All Needed Context

### Documentation & References
```yaml
- file: app/bitrix_bot.py
  why: |
    ВСЁ инвайт-ядро (незакоммиченный дифф 01.09): pending-перехват в
    _handle_hr_message (дедуп-ключ с wiz.get('step'), «отмена», «команда
    важнее»), _finish_invite / _invite_role_input (~625-695), invite-ветка
    «пригласить» (~1215-1255, гейт get_active_courses + _pending_edits с
    kind:"invite"), ветка «руководител» (~1484), «роль
    разрешить/запретить» (~1493), _maybe_send_reminders (~218,
    _session_keyboard → A–D в тесте), _deadline_items (~200, roles_by_uid
    на _last_known_role), _course_recipients/_standard_recipients
    (~1027-1074), _handle_employee_message блок №7 (клавиатура по СВЕЖЕМУ
    состоянию + is_menu-ветка с resume_test) — точка C1.

- file: app/state_machine.py
  why: |
    _dispatch session-None (резолюция hr→dept→память→меню, ~161-180) и
    start_onboarding (зеркало, ~208-230) — врезать resolve_role;
    _hr_assigned_role (~326), _last_known_role, _role_from_profile (сеть!);
    _assign_course (by_hr/by_department/remembered + маркер «курсов нет»,
    ~370-410) — точка A7/B3; _handle_role_select (~440-460, УБРАТЬ
    set_employee_role — B1); _handle_test («выйти»-пауза ~785, «продолжить»
    ~794) — C2; _handle_reading/_handle_exam_ready/_handle_waiting_hr —
    C3 («выйти»); my_courses статусы (~555-578) — C6; _STATE_HINTS (~82),
    _HINT_SKIP_MARKERS — C2/C3 (авто-hint допишет «Что дальше»);
    format_question («— Вопрос {i+1}/{total}») — маркер C1; _handle_done
    (~979) — C7.

- file: app/keyboards.py
  why: |
    for_session BASIC_TEST/EXAM (кнопка «⏸ Отложить тест», A–D) — C2;
    courses_menu(resume_test) «▶ Продолжить тест» — добавить resume_exam;
    hr_role_select (новый билдер 01.09) — параметризовать не надо, но
    промпт role_change использует его же; with_main_menu — источник
    «🏠 Меню» (payload «Меню» у HR) — A6-эскейп; ЖЕЛЕЗНЫЙ ИНВАРИАНТ:
    payload'ы не менять, меняются только TEXT.

- file: tests/test_hr_invite.py
  why: |
    Фикстура env (DB_PATH-подмена, fake_send БЕЗ kwargs keyboard, стаб
    _send_course_file, _recent_msgs/_pending_edits.clear()); паттерн
    _wait_for/_post_hr. Сюда — тесты A1-A7 (ретраи, сбой _finish_invite,
    гонка двух HR, «Роль {email}», отмена-тексты, курсов-нет-для-роли).
    ГОЧА: fake_send не принимает keyboard → передавать
    **_kb_kwargs(None-путь) как сейчас (BUTTONS_ENABLED выключен в тестах).

- file: tests/test_state_machine.py + tests/test_autorole.py
  why: |
    env-фикстуры и тест-паттерны FSM; test_role_remembered_after_done —
    вернуть ассерт «помню» (B1/B4); test_hr_assigned_role_beats_department
    (01.09) — остаётся; новые: «Роль» не пишет employees.role, «выйти» вне
    теста, статус EXAM q_idx=0, фазовые тексты паузы.

- file: tests/test_keyboard_design.py
  why: |
    SAMPLES + ALLOWED_PAYLOADS (цифры уже разрешены — сетка ролей;
    «Продолжить»/«Выйти»/«Меню» разрешены). Добавить SAMPLES:
    hr_role_select (2 и 17 ролей), courses_menu(resume_exam=True),
    for_session EXAM с «Отложить экзамен». Инварианты: ≤1 primary, WIDTH
    у LINE, бюджет ряда ≤304px — новые TEXT длиннее («⏸ Отложить экзамен»
    = 17 симв — проверить width 160→180? бюджет ряда с «❓ Помощь» 130:
    180+130+8=318 > 304 — СНАЧАЛА посчитать, при переполнении width
    оставить 160 (текст влезает: у кнопок TEXT не режется бюджетом, бюджет
    считается по WIDTH) — см. как считает тест.

- file: tests/test_buttons_everywhere.py
  why: |
    hr_env-фикстура (captured с keyboard), test_invite_role_prompt_keyboard
    (01.09) — паттерн для теста «Меню»-эскейпа и role_change-промпта;
    test_reminder-* если есть — C5.

- file: tests/test_reminders.py
  why: паттерн тестов _maybe_send_reminders (meta reminder_last_date,
       now_utc-инъекция) — сюда C5-тест «в тесте → без A–D».

- file: PRPs/meeting-2108-fixes.md
  why: прецедент «меню отовсюду»/resume_test — стиль решений и гочи
       (≤1 primary, payload-инварианты); C1 расширяет именно эту механику.

- доклад ultra-ревью: в этой сессии (15 находок, 3 CONFIRMED) — контекст
  зафиксирован в description и задачах; отдельного файла нет.
```

### Known Gotchas
```python
# CRITICAL: дедуп-ключ считается В НАЧАЛЕ _handle_hr_message, ДО HR-гейта.
# Суффикс — только если сообщение реально уйдёт в pending-обработчик
# (не «отмена»/«cancel»/«меню», не startswith(_HR_COMMAND_PREFIXES)).
# Кнопочный dedup_key уже «text#msgID» — суффикс вешается ПОВЕРХ, это ок.

# CRITICAL: pop pending — ТОЛЬКО после успешного _finish_invite /
# set_employee_role (паттерн визарда: pop после сохранения). except —
# широкий Exception с логом (сеть/БД), но НЕ глотать KeyboardInterrupt-
# семейство (except Exception достаточно).

# CRITICAL: payload'ы кнопок НЕ менять («Выйти», «Продолжить», «Меню»,
# цифры) — меняются только TEXT. ALLOWED_PAYLOADS в test_keyboard_design
# не расширять без нужды.

# CRITICAL: resolve_role в циклах (_course_recipients/_standard_recipients/
# _deadline_items) — БЕЗ сети: hr → _last_known_role. _role_from_profile
# может звать httpx user.get (сессии нет в seen) — в цикле по всем
# сотрудникам это флап-риск и секунды на юзера.

# CRITICAL: B1 (убрать set_employee_role из _handle_role_select) ломает
# test_role_remembered_after_done в текущем виде (я 01.09 ослабил его до
# «Твоя роль») — вернуть ассерт «помню» и НЕ забыть, что сообщение снова
# идёт веткой remembered, а не by_hr.

# GOTCHA: маркер вопроса — r"— Вопрос \d+/\d+" (формат format_question
# «📝 {label} — Вопрос {i+1}/{total}»). Фидбек на ПОСЛЕДНИЙ ответ фазы
# вопроса не содержит, но after-состояние уже не тестовое — C1 не заденет.

# GOTCHA: «меню»-эскейп pending — ТОЧНОЕ совпадение msg_lower == "меню"
# (кнопка «🏠 Меню» шлёт «Меню»), НЕ префикс — иначе съест цифры/тексты
# с «меню» внутри. У employee-бота «🏠 Меню» шлёт «Мои курсы» — не трогать.

# GOTCHA: _assign_course маркер «курсов нет» (course_id=0, DONE,
# already_flagged) гасит повторный notify_hr — при by_hr=True notify_hr
# просто НЕ звать, маркер оставить (гасит и последующие «привет»).

# GOTCHA: _finish_invite «уже проходит» — get_session ДО set_employee_role,
# но ПОСЛЕ add_employee (refresh email/ФИО — поведение старого кода).

# GOTCHA: тесты hr_invite: fake_send без параметра keyboard — новые ветки
# шлют клавиатуры только при BUTTONS_ENABLED (в тестах выключен), _kb_kwargs
# вернёт {} — сигнатуры двойников не трогать. В test_buttons_everywhere
# наоборот BUTTONS_ENABLED=True и captured ловит keyboard.

# GOTCHA: role_change для сотрудника БЕЗ открытой сессии — просто UPDATE
# employees.role; уведомление сотруднику шлём на f"u{uid}" (bare_dialog
# срежет «u»), от BOT_ID (employee-бот), client_id пустой → BOT_CLIENT_ID.

# GOTCHA: C3 «выйти» в WAITING_HR/EXAM_READY: короткий текст БЕЗ «Что
# дальше» — _with_hint сам допишет hint состояния; если добавить своё
# «Что дальше», hint подавится (_HINT_SKIP_MARKERS).

# GOTCHA: C6 меняет status EXAM q_idx=0 c "admitted" на "paused" — эмодзи
# в courses_menu (⏸ вместо 🎓) и ассерты test_state_machine/test_buttons
# на «допущен к экзамену» для EXAM-строк — проверить grep'ом.

# GOTCHA: дедуп-тесты: _recent_msgs/_pending_edits чистятся в фикстурах;
# для теста ретрая НЕ чистить между двумя _post_hr (в этом суть).

# GOTCHA: «Роль {email}» — msg_lower для regex, но email вытаскивать
# _extract_email(question) (регистронезависимо, lower внутри).
```

## Implementation Blueprint

### Список задач (в порядке выполнения)

```yaml
Task 1 — дедуп-ключ + эскейпы pending (bitrix_bot._handle_hr_message):
  - суффикс ключа: вычислять consumed = wiz and msg_lower not in
    ("отмена","cancel","меню") and not msg_lower.startswith(_HR_COMMAND_PREFIXES);
    суффикс по kind: invite→f"#inv{wiz['uid']}", role_change→f"#rc{wiz['uid']}",
    визард→f"#step{wiz['step']}"
  - pending-перехват: «меню» → дроп как команду; дроп invite/role_change
    (командой ИЛИ «меню») → asyncio.create_task(_send("⚠️ Выбор роли для
    {fio} отменён — {приглашение не завершено. Повтори: Пригласить {email}
    | смена роли не выполнена}")) и продолжить обработку сообщения
  - «отмена»: тексты по kind + is_employee_allowed (A6)

Task 2 — снапшот + pop-после-успеха (bitrix_bot):
  - invite-ветка: в pending добавить options=selectable_roles() (снапшот);
    промпт строить из него же
  - _invite_role_input → переименовать/обобщить в _role_pick_input:
    options = pending["options"]; невалидная цифра → reprompt из снапшота
    (+продлить expires); валидная → finisher по kind:
      invite: try: text = await _finish_invite(...); except Exception:
        лог, expires+=TTL, return «⚠️ … ответь цифрой ещё раз»; else:
        _pending_edits.pop
      role_change: try/except аналогично вокруг set_employee_role +
        уведомления сотрудника
  - удалить ветку «конфиг опустел» (options всегда в pending)

Task 3 — _finish_invite честность (bitrix_bot + state_machine._assign_course):
  - порядок: add_employee → get_session-гейт («уже проходит (роль: X).
    Сменить роль: Роль {email}», РОЛЬ НЕ ПИСАТЬ) → set_employee_role →
    start_onboarding
  - после start_onboarding: get_session(uid) is None (маркер «курсов нет»)
    → HR-текст «добавлен (роль X), курсов для роли пока нет»; иначе как
    сейчас «приглашён — отправил первое сообщение. Роль: X»
  - _assign_course(by_hr=True, course None): не notify_hr, текст сотруднику
    «HR добавил тебя в обучение (роль: X). Курсов пока нет…»
  - pre-pending гейт invite-ветки: «уже проходит» — тоже с ролью и
    подсказкой «Роль {email}»

Task 4 — HR-команда «Роль {email|ID}» (bitrix_bot):
  - ветка после «роль разрешить/запретить»: резолв сотрудника → pending
    kind="role_change" (uid, fio, email, options-снапшот) → промпт
    «Кем назначить {fio}? …» + keyboards.hr_role_select(options)
  - finisher: set_employee_role + _send сотруднику (BOT_ID) + HR-подтверждение
  - help-текст HR-бота: строка «*Роль {email}* — сменить роль сотрудника
    (бот спросит цифру)»; обновить текст «Роль запретить» (B3)

Task 5 — семантика ролей (state_machine + bitrix_bot):
  - B1: убрать set_employee_role из _handle_role_select; при живой
    _hr_assigned_role — приписка «(временно, до следующего курса …)»
  - B2: resolve_role(user_id)->(role, source) для назначения (обе точки —
    _dispatch и start_onboarding, тексты по source); resolve_role_cached
    для _course_recipients, _standard_recipients, _deadline_items,
    menu/docs-веток _dispatch и menu-клавиатуры _handle_employee_message
  - B3: «(назначил HR; вопросы по роли — к HR)»; текст «Роль запретить»

Task 6 — клавиатура по содержимому + фазовые тексты (C1, C2, C5, C6):
  - bitrix_bot._handle_employee_message: in_test and not re.search(
    r"— Вопрос \d+/\d+", text) → menu_kb-ветка (resume_test=True,
    resume_exam=(after.state=="EXAM")); объединить с is_menu-условием
  - keyboards: for_session BASIC_TEST/EXAM — TEXT «⏸ Отложить
    тест|экзамен» по state; courses_menu(resume_exam=False) — TEXT
    «▶ Продолжить экзамен»; payload'ы прежние
  - state_machine: пауза «⏸ {Тест|Экзамен} на паузе», _STATE_HINTS EXAM;
    my_courses: EXAM всегда «экзамен на вопросе {q_idx+1}»/paused (C6)
  - bitrix_bot._reminder_keyboard(uid): тестовое состояние →
    start_button(«▶ Продолжить тест|экзамен», «Продолжить»); иначе
    _session_keyboard; применить в _maybe_send_reminders (C5)

Task 7 — мелкие UX (C3, C4, C7):
  - «выйти»/«выход» в _handle_reading/_handle_exam_ready/_handle_waiting_hr
    → «Сейчас нет активного теста — откладывать нечего.»
  - «руководител»-ветка: «руководители» → список, «руководитель» →
    подсказка правки
  - удалить _handle_done + ветку DONE в _dispatch (комментарий: get_session
    фильтрует DONE)

Task 8 — тесты (по файлам):
  - test_hr_invite: ретрай «Пригласить» (без clear между постами) → один
    промпт; две цифры двух приглашений подряд → оба инвайта; сбой
    _finish_invite (monkeypatch start_onboarding → raise) → pending жив,
    повторная «1» доводит; гонка двух HR → роль не перезаписана; «Роль
    {email}» полный флоу + «разрешить/запретить» не сломаны; «Отмена» два
    текста; курсов-нет-для-роли (активен только чужой ролевой курс);
    «Меню» дропает pending с «⚠️»
  - test_state_machine/test_autorole: «Роль» не пишет employees.role +
    возврат HR-роли на следующем курсе; «помню» для легаси (B4); «выйти»
    вне теста; статус EXAM q_idx=0; фазовый текст паузы экзамена
  - test_buttons_everywhere: help в тесте → клавиатура resume (не A–D);
    RAG-ответ в тесте → resume; ответ с вопросом → A–D; напоминание в
    тесте → «Продолжить» без A–D (или в test_reminders)
  - test_keyboard_design: SAMPLES += hr_role_select(2/17),
    courses_menu(resume_exam), for_session EXAM (текст «Отложить экзамен»);
    бюджеты рядов пересчитать
  - test_hr_managers (если ассертит «руководител»-ветку) — обновить

Task 9 — хвосты:
  - help-тексты обоих ботов (упомянуть «Роль {email}», «Продолжить»)
  - task.md: отложено — message.update для старых A–D, персистентный
    pending, BB-эскейп fio
  - деплой + живой чек-лист (см. Final validation)
```

### Integration Points
```yaml
DB: без миграций (employees.role уже в дереве 01.09; pending — in-memory)
ENV: без изменений (BUTTONS_ENABLED-гейты как есть)
```

## Validation Loop
```bash
ruff check app tests
python -m pytest tests/ -q          # 285 + новые, ноль красных
# точечные прогоны при итерации:
python -m pytest tests/test_hr_invite.py tests/test_state_machine.py \
  tests/test_buttons_everywhere.py tests/test_keyboard_design.py -q
```

## Final validation Checklist
- [ ] pytest/ruff зелёные; payload-инварианты не изменились
- [ ] Живой сценарий юзера: тест → [⏸ Отложить] → «Помощь» → ответ БЕЗ
      A–D, с [▶ Продолжить тест]; в экзамене все надписи «экзамен»
- [ ] Живой инвайт: «Пригласить» → цифра; «Пригласить» → «Меню» → «⚠️ не
      завершено»; «Роль {email}» меняет роль без перезапуска курса
- [ ] Кнопка «⚙️ Править руководителей» показывает подсказку формата
- [ ] Напоминание (можно форс-тиком) сотруднику в тесте — без A–D
- [ ] task.md обновлён (отложенные пункты)

## Anti-Patterns to Avoid
- ❌ Не менять payload'ы кнопок — только TEXT (FSM/дедуп завязаны)
- ❌ Не давать «Мои курсы»/«Помощь» побочных эффектов (прецедент 21.08)
- ❌ Не писать employees.role ниоткуда, кроме HR-флоу (инвайт, «Роль
  {email}») — это инвариант всего пакета
- ❌ Не звать сеть в цикле по сотрудникам (resolve_role_cached)
- ❌ Не «чинить» вечные A–D на старых сообщениях в этом пакете — отдельная
  работа с message.update
- ❌ Не глотать исключения _finish_invite молча — HR всегда получает ответ

## Confidence Score: 8/10
Все точки врезки — код, прочитанный и частично написанный в этой сессии;
боли воспроизведены ревью с вердиктами CONFIRMED и живым фидбеком юзера;
тестовые харнессы готовы. Минус два балла: (1) дедуп-ключ — тонкая логика
с тремя видами pending и кнопочными msgID-ключами, легко получить
регресс дедупа кнопок (покрыть ретрай-тестами ДО рефакторинга);
(2) C1 меняет клавиатуру для всех не-вопросных ответов в тестах — широкая
поверхность по test_buttons/test_buttons_everywhere, ассерты старых
раскладок могут дёрнуться в неожиданных местах.
