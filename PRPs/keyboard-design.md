name: "Дизайн клавиатур по спеке claude design: 5 цветовых ролей, BLOCK/WIDTH/NEWLINE-раскладки, новые тексты кнопок, пагинация «Курсы», курсы-кнопками"
description: |
  Реализация дизайн-спеки (юзер получил её от claude design 17.08 по нашему
  ТЗ со скриншотами): 5 ролей кнопок с парами BG/TEXT, раскладки для 12
  клавиатур двух ботов, тексты-глаголы («✅ Готов к тесту»), курсы BLOCK-
  кнопками в «Мои курсы», постраничные «Курсы на проверке» (по 8), тёмная
  плашка «Подтвердить и запустить» для необратимого действия.

## Purpose
Одно-проходный рестайл поверх кода после question-quality (238 тестов).
95% работы — app/keyboards.py + точки, где билдеры получают новые данные
(варианты теста, имена ролей, флаг pending, имя сотрудника). ЖЕЛЕЗНЫЙ
инвариант: меняются ТОЛЬКО TEXT/цвета/раскладка — все COMMAND_PARAMS
остаются прежними текстами, которые FSM и HR-диспетчер уже понимают.

## Core Principles
1-5 как всегда. Иерархию несёт раскладка, не цвет (цвета в теме портала
могут не отрисоваться — проверяется смоуком из спеки, деплой-хвост).

## Осознанные отклонения от спеки (зафиксировать в комментариях)
- «Мои курсы», BLOCK-список: кнопки ТОЛЬКО для выбираемых курсов (⏳ не
  начат / ⏳ ждёт HR / 🎓 допущен), номера = индексы «Выбрать N» из
  my_courses (FSM-совместимость). Текущий и пройденные курсы кнопками НЕ
  делаются (спека сама: «нажимать их незачем») — прогресс виден в тексте.
- Визард правки: кнопка [• Оставить] («.») СОХРАНЯЕТСЯ (дизайнер про шаг
  «точка» не знал, функционально важна) — оба в служебном цвете; спека
  «только Отмена» применяется к цвету/ширине, не к составу.
- «Проверить статус» в WAITING_HR НЕ делаем: уведомление о допуске приходит
  всегда (ветка «Допустить» шлёт сообщение) — по спеке тогда одна кнопка.
- Карточка вопроса «Вопросы N.q» в спеке не описана — стилизуется по её же
  ролям и правилу 06 (порядок): навигация secondary, действия secondary,
  «Все вопросы» service, на q=15 — [Подтвердить и запустить] danger BLOCK
  последним рядом.

## Goal (спека, сжатая до кода)

**Роли (пары BG_COLOR/TEXT_COLOR):**
| роль      | BG      | TEXT    | применение |
|-----------|---------|---------|------------|
| primary   | #BEDC3C | #004664 | главное действие, ≤1 на клавиатуру, всегда BLOCK, первый ряд |
| secondary | #DDEFF8 | #004664 | навигация, равнозначные варианты, A–D |
| service   | #EBF6FB | #778592 | Отмена, Роль, «Показать ещё», пройденное |
| review    | #D6EFEC | #007D70 | только HR: «Вопросы N» перед подтверждением |
| danger    | #004664 | #FFFFFF | «Подтвердить и запустить»: BLOCK, отдельный ПОСЛЕДНИЙ ряд |

**Механика:** WIDTH у каждой LINE-кнопки (52/70/96/110/120/130/150/160/170/
180); NEWLINE-разделители держат ряды; ряд ≤300px на мобильном; ≤2 LINE в
ряду (3+ только фикс-короткие: цифры 5×52, A–D 4×70, «Выбрать N» 3×96);
≤1 эмодзи на кнопку; ≤10 кнопок на сообщение (списки — постранично).

**Employee:** роль ≤8 → BLOCK «N · Имя роли» (secondary), >8 → сетка цифр
5×52; READING → [✅ Готов к тесту] primary BLOCK + [📚 Мои курсы 150]
[Роль 110 service]; тесты → все тела вариантов ≤24 симв. → 4 BLOCK
«A · текст», иначе ряд [A][B][C][D] 4×70; WAITING_HR → [📚 Мои курсы]
secondary BLOCK; «Мои курсы» → ≤6 выбираемых: BLOCK-кнопки
«{статус-эмодзи} N · Название(≤38…)» (🎓 допущен/⏳) + при READING сверху
[✅ Готов к тесту] primary + внизу [Роль 110 service]; >6 → сетка
[Выбрать N] 3×96; провал → [🔁 Пересдать экзамен] primary BLOCK +
[Далее, к следующему 180 secondary]; проактивные → [▶ Начать обучение] /
[🎓 Начать экзамен] primary BLOCK, [📚 Мои курсы] secondary BLOCK.

**HR:** меню → стопка BLOCK: [📚 Курсы на проверке] (primary при непустой
очереди, иначе secondary), [📊 Отчёт по обучению], [👥 Руководители]
(secondary); список на проверке → страницы по 8: ряд [Вопросы N 130 review]
[Подтвердить N 160 danger], внизу [Показать ещё {остаток}] service BLOCK
(payload «Курсы {page+1}»); уведомление о новом документе → [Вопросы N 160
review] + [Подтвердить и запустить N] danger BLOCK последним рядом, в
ТЕКСТЕ — «{K} вопросов · {M} получателей»; визард → [• Оставить][Отмена]
service (confirm: [💾 Сохранить] primary BLOCK + [🔄 Заново 130 secondary]
[Отмена 120 service]); уведомления → [Пригласить {email}] / [Допустить
{Имя Фамилия}] primary BLOCK, TEXT обрезан до 34 симв. (payload — полный).

## What / Success Criteria
- [ ] ЖЕЛЕЗНО: COMMAND_PARAMS каждой кнопки байт-в-байт прежний («Готов»,
      «Мои курсы», «Роль», «A»–«D», «Выбрать N», «Пересдать», «Далее»,
      «Начать», «Курсы», «Отчёт», «Руководители», «Вопросы N[.q][ все]»,
      «Подтвердить N», «Изменить N.q», «Перегенерировать N[.q]», «.»,
      «Сохранить», «Заново», «Отмена», «Пригласить {email}»,
      «Допустить {uid}») — unit-тест-инвариант по всем билдерам
- [ ] Каждая LINE-кнопка несёт WIDTH; ни один ряд не превышает 300px
      (сумма WIDTH + 8px зазор × (n−1)) — unit-хелпер проверяет билдеры
- [ ] ≤1 primary на клавиатуру — unit-инвариант
- [ ] «Курсы» постранично по 8 c «Показать ещё» (текст сообщения — та же
      страница), «Курсы 2» — вторая страница
- [ ] Тесты: обновлены ТОЛЬКО перечисленные в Tasks файлы-ассерты текстов;
      `python -m pytest tests/ -v` зелёный, `ruff check .` чистый
- [ ] task.md: смоук цветов из спеки (4 BLOCK-кнопки #BEDC3C/#DDEFF8/
      #004664/#D6EFEC в тестовый чат, десктоп+тёмная+мобильный; контраст
      белого на лайме НЕ использовать) + живой рендер WIDTH/NEWLINE

## All Needed Context

### Documentation & References
```yaml
- file: app/keyboards.py
  why: |
    ВСЕ билдеры здесь (после 17.08: for_session, start_button, with_switch,
    hr_main_menu, hr_course_actions, hr_course_list, hr_question_card,
    hr_wizard_step, hr_wizard_confirm, hr_invite, hr_cancel_edit, hr_admit).
    _btn(text, payload, display, command) → добавить role="secondary",
    width=None; _BG/#29619b УДАЛИТЬ, ввести словарь _ROLES из таблицы Goal.
    Чистота сохраняется: все данные параметрами.

- file: app/bitrix_bot.py
  why: |
    Точки, где билдеры получают НОВЫЕ данные:
    - _handle_employee_message / _session_keyboard: для BASIC_TEST/EXAM
      достать тела вариантов текущего вопроса (get_course_questions по
      session["course_id"], phase из state, session["current_q_idx"]) и
      передать в for_session(test_options=...); для «Мои курсы» после
      menu-команды selectable уже достаётся (with_switch) — передавать
      [(n, name, status)] для BLOCK-списка (статус из my_courses, см. ниже).
    - «курсы»-ветка _handle_hr_message: парс ^курсы( \d+)?$ → page (деф. 1);
      текст = ТОЛЬКО страница из 8 курсов; kb = hr_course_list(page_ids,
      page, remaining).
    - hr_main_menu(has_pending) — в else-ветке дешёвый
      len(get_pending_courses()) > 0 через to_thread.
    - process_new_document, notify: get_course_by_id после save →
      recipients = len(_course_recipients(course)) (sync, to_thread) →
      строка «{15} вопросов · {M} получателей» в текст; kb =
      hr_new_course_actions(course_id) (review + danger).
    - «Допустить»-уведомления HR (notify_hr из state_machine): передать
      label сотрудника (см. state_machine).
  критично: инвариант _kb_kwargs и BUTTONS_ENABLED-гейт НЕ трогать.

- file: app/state_machine.py
  why: |
    my_courses уже вычисляет open_states по курсам (WAITING_HR/EXAM) и
    selectable — добавить selectable-элементам ключи "n", "status"
    ("todo"|"waiting"|"admitted") И display-name, НЕ меняя (text, selectable)
    контракт (обогащаются сами dict-ы курсов). _finish_phase → notify_hr:
    keyboards.hr_admit(uid, label=_employee_label(uid)) — label в TEXT,
    uid в payload. selectable_roles() → list[(rid, name)] — имена ролей
    для BLOCK-кнопок выбора роли (roles.py:34).

- file: tests/test_buttons.py, tests/test_buttons_everywhere.py,
        tests/test_question_ux.py
  why: |
    Ассерты текстов кнопок (7/19/1 мест) ломаются ДИЗАЙНОМ — обновить под
    новые TEXT, проверяя ПАРУ (TEXT, COMMAND_PARAMS): например
    («✅ Готов к тесту», «Готов»). test_buttons.py строка ~41 инвариант
    «COMMAND_PARAMS == TEXT» заменить на явную карту payload'ов.
    ДРУГИЕ тестовые файлы клавиатур не касаются — не трогать.

- url: https://apidocs.bitrix24.ru/api-reference/chat-bots/keyboards/index.html
  why: поля BG_COLOR/TEXT_COLOR (hex), DISPLAY LINE|BLOCK, WIDTH (px),
       {"TYPE":"NEWLINE"} — всё, что использует спека, штатные поля v1.

- дизайн-спека в этом PRP (раздел Goal) — первоисточник от claude design;
  расхождения решать в пользу раздела «Осознанные отклонения».
```

### Known Gotchas
```python
# CRITICAL: TEXT ≠ payload теперь ВЕЗДЕ. Любая новая кнопка обязана явно
# передавать payload= прежней командой. Тест-инвариант по карте payload'ов —
# первый в новом test-файле, чтобы регресс ловился до живого бота.

# CRITICAL: «Далее, к следующему» — payload строго «Далее» (state_machine
# понимает «далее»); «✅ Готов к тесту» → payload «Готов» (FSM ловит
# «готов» in message.lower()); «Подтвердить и запустить N» → payload
# «Подтвердить N»; «📚 Курсы на проверке» → «Курсы».

# GOTCHA: BLOCK-вариант A–D показывает ТЕЛО варианта («A · До заезда»),
# но payload — буква «A»: parse_answer понимает только букву. Тела брать
# стрипнутыми от префикса «A. »; условие BLOCK — ВСЕ 4 тела ≤24 символов.

# GOTCHA: обрезка названий: BLOCK-кнопки курсов ≤38 симв. + «…», email/имя
# в уведомлениях ≤34 симв. — хелпер _trim(text, n) в keyboards.py.

# GOTCHA: пагинация «Курсы»: parse ^курсы(?:\s+(\d+))?$ — НЕ конфликтует с
# «Вопросы N» и не ловит «курс 5» (префикс «курс» в msg_lower in-проверке
# «курсы/курс/список» — ветку объединить с новым regex аккуратно:
# «курс»/«список» без номера = страница 1).

# GOTCHA: hr_main_menu(has_pending): вызов get_pending_courses в else-ветке
# — через asyncio.to_thread (sync БД), результат только булев.

# GOTCHA: правило «≤1 primary»: в «Мои курсы» при state=READING primary =
# [✅ Готов к тесту], сами курсы secondary; в WAITING_HR primary НЕТ вовсе
# (спека: «действовать нечему»).

# GOTCHA: WIDTH — int (px). NEWLINE-элементы наши прежние
# {"TYPE": "NEWLINE"}. Существующий рендер LINE-кнопок без WIDTH скакал —
# теперь WIDTH обязателен для каждой LINE (unit-инвариант).

# GOTCHA: старые тесты ловят keyboard=None-инвариант и _kb_kwargs — их не
# трогать; правки только в перечисленных файлах-ассертах текстов.
```

## Implementation Blueprint

### Список задач (в порядке выполнения)

```yaml
Task 1 — keyboards.py: роли, WIDTH, хелперы:
  - _ROLES (таблица Goal), _btn(text, payload=None, display="LINE",
    command="say", role="secondary", width=None) → BG/TEXT из роли,
    WIDTH при LINE; _hr_btn то же с command="hrsay"; _trim(text, n);
    удалить _BG. NEWLINE-константа остаётся.

Task 2 — keyboards.py: employee-билдеры по спеке:
  - for_session(session, fork, role_options, test_options=None):
      ROLE_SELECT: ≤8 ролей → BLOCK «{i} · {name}» (payload str(i),
        secondary); >8 → цифры LINE 52 рядами по 5 (NEWLINE после 5-й);
      READING: [✅ Готов к тесту→«Готов»] primary BLOCK; NEWLINE;
        [📚 Мои курсы 150 secondary][Роль 110 service];
      BASIC_TEST/EXAM: test_options (4 тела) и все ≤24 → BLOCK
        «{L} · {тело}»→payload L; иначе [A][B][C][D] LINE 70 secondary;
      WAITING_HR: [📚 Мои курсы] secondary BLOCK;
      fork: [🔁 Пересдать экзамен→«Пересдать»] primary BLOCK; NEWLINE;
        [Далее, к следующему→«Далее» 180 secondary]
  - courses_menu(items, reading, role_row=True): items=[(n, name, status)];
    ≤6 → BLOCK-кнопки «{⏳|🎓} {n} · {name≤38…}»→«Выбрать {n}» (secondary),
    сверху [✅ Готов к тесту] primary если reading; >6 → [Выбрать n] LINE 96
    по 3 в ряд; внизу [Роль 110 service] (только при reading).
    with_switch УДАЛИТЬ (заменяется courses_menu) — единственный вызов
    в _handle_employee_message.
  - start_button(label, payload): роль по метке — «Начать…» primary,
    «Мои курсы» secondary (или явный параметр role) — BLOCK как сейчас.

Task 3 — keyboards.py: HR-билдеры по спеке:
  - hr_main_menu(has_pending=True): [📚 Курсы на проверке→«Курсы»]
    (primary если has_pending, иначе secondary) BLOCK; [📊 Отчёт по
    обучению→«Отчёт»] BLOCK secondary; [👥 Руководители] BLOCK secondary
  - hr_course_list(ids, page=1, remaining=0): ряды [Вопросы N 130 review]
    [Подтвердить N 160 danger]; remaining>0 → NEWLINE + [Показать ещё
    {remaining}→«Курсы {page+1}»] service BLOCK
  - hr_new_course_actions(course_id): [Вопросы N 160 review]; NEWLINE;
    [Подтвердить и запустить N→«Подтвердить N»] danger BLOCK
  - hr_question_card: nav [⬅️ Назад 96][▶️ Далее 96] secondary; NEWLINE;
    [✏️ Изменить 130 secondary][🔄 Заново 130 secondary]; NEWLINE;
    [📄 Все вопросы 160 service]; q=15: вместо «Далее» ничего в nav-ряду,
    последним рядом [Подтвердить и запустить→«Подтвердить N»] danger BLOCK
  - hr_wizard_step: [• Оставить→«.» 130 service][Отмена 120 service]
  - hr_wizard_confirm: [💾 Сохранить] primary BLOCK; NEWLINE;
    [🔄 Заново 130 secondary][Отмена 120 service]
  - hr_invite(email): TEXT «Пригласить {email}»≤34…, payload полный, primary
  - hr_admit(uid, label=None): TEXT «Допустить {label or uid}»≤34…,
    payload «Допустить {uid}», primary
  - hr_course_actions оставить (флэт-простыня «N все») — review+danger пара
  - hr_cancel_edit: [Отмена 120 service]

Task 4 — bitrix_bot: данные для билдеров:
  - _session_keyboard + keyboard-блок _handle_employee_message: для
    состояний BASIC_TEST/EXAM достать через to_thread тела вариантов
    текущего вопроса (get_course_questions, phase по state, current_q_idx;
    промах индексов → None) → for_session(..., test_options=bodies)
  - menu-ветка («Мои курсы» в READING/WAITING_HR): selectable из my_courses
    уже обогащён (Task 6) → keyboards.courses_menu(
    [(c["n"], display_name(c["doc_name"]), c["status"]) for c in selectable],
    reading=(after["state"] == "READING"))
  - «курсы»-ветка HR: regex ^(курсы|курс|список)(?:\s+(\d+))?$ → page;
    страница = pending[8*(page-1):8*page]; текст строит ТОЛЬКО страницу
    («N · имя — строкой над парой кнопок» уже так); kb = hr_course_list(
    [c["id"] for c in page_items], page, remaining=len(pending)-8*page)
  - else-ветка: has_pending через to_thread → hr_main_menu(has_pending)
  - process_new_document notify: recipients=len(_course_recipients(course))
    (to_thread, course = get_course_by_id(course_id)); текст += строка
    «15 вопросов · {M} подходящих получателей»; kb = hr_new_course_actions
  - «Допустить»-ветка + notify_hr: без изменений payload'ов

Task 5 — state_machine:
  - my_courses: selectable-элементам добавить c["n"] (индекс+1) и
    c["status"] ("admitted" если open_state EXAM, "waiting" если
    WAITING_HR, иначе "todo") — на месте существующего цикла разметки
  - _finish_phase: keyboards.hr_admit(uid, label=_employee_label(uid))

Task 6 — тесты:
  - NEW tests/test_keyboard_design.py:
      * карта payload-инвариантов: для каждого билдера собрать все кнопки,
        assert COMMAND_PARAMS ∈ ожидаемая карта / формат (regex для
        параметризованных)
      * ≤1 primary (#BEDC3C) на клавиатуру — по всем билдерам
      * каждая LINE несёт WIDTH; суммы рядов ≤300 (хелпер режет по NEWLINE)
      * ролевые пары цветов из таблицы
      * ROLE_SELECT ≤8 → BLOCK с именами; 17 → сетка 5/5/5/2
      * тест-вариант BLOCK при телах ≤24 и буквы при длинных
      * courses_menu ≤6 BLOCK / 7+ сетка; обрезка 38
      * hr_course_list пагинация: 20 id, page 1 → 8 пар + «Показать ещё 12»
        (payload «Курсы 2»)
      * hr_admit label обрезан, payload с uid
  - ОБНОВИТЬ (только тексты/пары): tests/test_buttons.py (карта payload
    вместо «PARAMS==TEXT», новые TEXT), tests/test_buttons_everywhere.py
    (19 ассертов), tests/test_question_ux.py (1)
  - HR «курсы» пагинация: тест в test_buttons_everywhere-стиле: 10 pending
    → страница 8 + кнопка; «Курсы 2» → остальные 2

Task 7 — task.md (живой чеклист):
  - смоук цветов из спеки: сообщение с 4 BLOCK (#BEDC3C/#DDEFF8/#004664/
    #D6EFEC) в тестовый чат — десктоп, тёмная тема, мобильный; если фон
    не применяется — иерархия уже на раскладке (ничего не делать), белый
    на лайме запрещён
  - рендер WIDTH/NEWLINE на мобильном (ряды 292–304px), BLOCK-обрезки 38/34
  - прокликать все 12 клавиатур по списку спеки
```

### Integration Points
```yaml
CONFIG: нет новых env; всё под существующим BUTTONS_ENABLED
DB: нет изменений
ROUTES: нет — только парс «курсы {page}» внутри _handle_hr_message
```

## Validation Loop
```bash
ruff check . --fix
python -m pytest tests/ -v      # менять только 3 перечисленных файла + новый
python - <<'EOF'
from fastapi.testclient import TestClient
import app.bitrix_bot as bot
assert TestClient(bot.app).post("/command", data={"event": "X"}).json()["status"] == "ignored"
print("ok")
EOF
```

## Final validation Checklist
- [ ] pytest зелёный; ruff чистый
- [ ] Тест payload-инвариантов покрывает ВСЕ билдеры (grep def в keyboards)
- [ ] git diff bitrix_bot: ни одного изменённого COMMAND_PARAMS
- [ ] task.md: смоук цветов + мобильный рендер + 12 клавиатур

## Anti-Patterns to Avoid
- ❌ Не менять payload'ы — дизайн живёт только в TEXT/цвете/раскладке
- ❌ Не добавлять вторую primary-кнопку «для красоты»
- ❌ Не вешать BLOCK на «Отмена» в визарде (приглашение печатать, не жать)
- ❌ Не тащить имя курса в кнопки HR-списка (298px бюджет ряда)
- ❌ Белый текст на лайме — запрещён спекой (нечитаем)
- ❌ Не трогать _kb_kwargs/BUTTONS_ENABLED-гейты и неперечисленные тесты

## Confidence Score: 8/10
Спека самодостаточна (все WIDTH/цвета/тексты заданы, бюджеты рядов
просчитаны), payload-инварианты перечислены поимённо, билдеры
централизованы и чистые. Минус два балла: (1) живой рендер BG_COLOR/WIDTH
в теме портала непроверяем офлайн — смоук из спеки в деплой-хвосте, при
неотрисовке цветов иерархия по замыслу спеки держится раскладкой;
(2) объём правок тестов текстов (27 ассертов в 3 файлах) — механический,
но широкий; митигация — новый тест payload-инвариантов пишется ПЕРВЫМ.
