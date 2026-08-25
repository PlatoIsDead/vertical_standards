name: "Фиксы встречи 21.08 к демо во вторник: выход из тупиков (меню отовсюду + «Продолжить тест»), честные статусы «Мои курсы», группировка «Документов», починка битых доменов ссылок"
description: |
  Живой прогон Дмитрия/Евгении 21.08 вскрыл 4 боли: (0) «ссылки не работают» —
  ВСЕ активные курсы несут doc_detail_url на СТАРЫЙ домен propertyhunters.pro
  (портал живёт на portal.becar.ru — проверено в БД 21.08); (1) «Отложить
  тест» = тупик: текст паузы предлагает «Выбрать», а клавиатура остаётся
  тестовой (A–D + Отложить) — замкнутый круг; (2) «Мои курсы» врёт статусами:
  провал показан «✅ пройден», завершённый курс выглядит текущим, а при
  session None меню вовсе не работает (перехват retake-форка / автоназначение);
  (3) «Документы» — простыня без иерархии (Евгения: «будет 136 — люди уйдут
  к руководителю»). Демо: вторник 15:00, до него — промежуточное письмо.

## Purpose
Одно-проходная реализация поверх кода 21.08 (266 тестов). Всё — правки
существующих механик, без новых подсистем.

## Core Principles
1-5 как всегда. Правило Евгении — «инструкция уровня „нажми эту кнопку“»:
каждый тупиковый ответ заканчивается строкой «Что дальше: …».

## НЕ в скоупе
- Пересдача ПРОИЗВОЛЬНОГО провального курса: механика №9 пересдаёт только
  ПОСЛЕДНИЙ завершённый (get_last_done_session) — в списке курсов подсказка
  «Пересдать» ставится только когда она реально сработает; расширение
  пересдачи на любой курс — отдельная работа (отметить в task.md).
- Красота по-Евгениному (она обещала прислать пожелания и не прислала) —
  делаем группировку и подсказки, «визуал» ждёт её вводных.
- Роли/отделы — ждут структуру нового Bitrix (не наш ход).

---

## Goal

**0. Домены ссылок:**
- Разовая правка БД на сервере: `UPDATE courses SET doc_detail_url =
  replace(doc_detail_url, 'propertyhunters.pro', 'portal.becar.ru')`
  (деплой-хвост, docker exec, показать до/после).
- Нормализация НАВСЕГДА: в process_new_document при сохранении detail_url
  подменять его хост на хост из BITRIX_WEBHOOK_URL (канонический домен
  портала известен из env) — новый хелпер `_canonical_url(url)` в
  bitrix_bot; пустой/кривой url — вернуть как есть.

**1. Выход из тупиков — «меню работает отовсюду»:**
- «Мои курсы»/«Документы» обрабатываются В ЛЮБОМ состоянии и при
  session is None:
  - process_message, ветка session None: ДО retake-форка и автоназначения
    курса — cmd в _MENU_COMMANDS → my_courses (роль из _last_known_role);
    cmd в _DOC_COMMANDS → _my_documents_text (роль оттуда же). «Мои курсы»
    больше НИКОГДА не назначает курс и не подменяется форком.
  - _handle_done: те же две команды до показа результатов.
  (В READING/WAITING_HR/тестах — уже работает.)
- НОВАЯ команда «Продолжить» в BASIC_TEST/EXAM: показать текущий вопрос
  (format_question по q_idx) — возврат в тест без «Выбрать».
- Клавиатура ПОСЛЕ паузы/меню в тесте — не тестовая, а навигационная:
  в _handle_employee_message условие menu-клавиатуры расширить:
  question в _MENU_COMMANDS | {"выйти", "выход"} И state в (READING,
  WAITING_HR, BASIC_TEST, EXAM) → keyboards.courses_menu(...,
  resume_test=(state in тестах)); в courses_menu параметр resume_test
  добавляет ПЕРВОЙ кнопкой [▶ Продолжить тест → «Продолжить»] (primary
  BLOCK; тогда «Готов»-primary не ставится — ≤1 primary).
- Строки «Что дальше»: тупиковые тексты завершаются подсказкой команд:
  - пауза теста: «Что дальше: ▶ Продолжить · Выбрать {номер} · Документы»
  - «Базовый тест завершён» (WAITING_HR): «Что дальше: жди допуска HR;
    пока — Мои курсы · Документы, задавай вопросы по стандартам»
  - _handle_done: «Что дальше: Мои курсы · Документы» (+ «Пересдать» если
    _retake_fork_text не None)
  - «Тебе доступен новый курс» и назначение курса — уже несут команды.

**2. Честные статусы «Мои курсы» (my_courses):**
- done-курсы разделить: СДАН (лучшая DONE-сессия курса прошла порог
  _exam_pass_threshold; курс без exam_questions = сдан по завершении) →
  «✅ {name} — пройден»; НЕ СДАН (все DONE ниже порога) → «❌ {name} —
  экзамен не сдан ({X}/{Y})», и если этот курс = get_last_done_session
  (пересдача сработает) → приписка « · напиши *Пересдать*» и кнопка
  [🔁 Пересдать] в клавиатуре меню (payload «Пересдать», service-роль? НЕТ:
  secondary; primary остаётся у Продолжить/Готов).
- Не сданные и сданные НЕ selectable (как сейчас), не выглядят текущими.
- Механика: _done_course_ids оставить (гейт назначения), в my_courses
  добавить расчёт passed/failed по сессиям (get_sessions_by_user уже
  вызывается; questions курса — get_course_questions, дешёво при ≤10 курсов).

**3. Группировка «Документов» (_my_documents_text):**
- Две секции: «📌 Для твоего отдела» (документы, где роль сотрудника в
  ролях документа) и «📖 Общие» (ALL_STAFF); у роли None — только «Общие»
  + подсказка про роль. Внутри секций — как сейчас (display_name + ссылка).
- Хвост: «Задавай вопросы по любому из них» (уже есть) + «Что дальше:
  Мои курсы · Готов» при активном READING? НЕТ — не перегружать, оставить
  существующий хвост.

## What / Success Criteria
- [ ] _canonical_url: propertyhunters.pro → portal.becar.ru по
      BITRIX_WEBHOOK_URL; кривые url не падают; юнит
- [ ] «Мои курсы» при session None показывает список (не форк, не
      автоназначение); «Документы» так же; в DONE так же — flow-тесты
- [ ] «Продолжить» в тесте показывает текущий вопрос, q_idx цел
- [ ] Пауза/меню в тесте → клавиатура: [▶ Продолжить тест] + курсы;
      тестовая клавиатура (A–D) НЕ прикрепляется к ответу-меню
- [ ] Провальный курс: «❌ … не сдан (X/Y)», не «пройден»; последний
      провальный — подсказка и кнопка Пересдать; сданный — «✅ пройден»
- [ ] «Документы» — две секции; None-роль — только Общие
- [ ] Тупиковые тексты содержат «Что дальше:» (пауза, WAITING_HR-финиш,
      DONE)
- [ ] payload-инварианты клавиатур (test_keyboard_design) с новыми
      кнопками [▶ Продолжить тест], [🔁 Пересдать]
- [ ] `python -m pytest tests/ -v` (266+), `ruff check .`; деплой + разовый
      UPDATE доменов на сервере + живой контроль ссылки

## All Needed Context

### Documentation & References
```yaml
- file: app/state_machine.py
  why: |
    process_message ветка session None (строки ~76-119: whitelist → retake
    fork → активные курсы → назначение) — menu/doc-команды вставить ПОСЛЕ
    whitelist, ДО форка. _handle_done (~700) — то же. _handle_test —
    добавить «продолжить» рядом с блоком меню/выйти (вернуть
    format_question(current_q, q_idx, total, phase)). my_courses (~400) —
    статусы done; _exam_pass_threshold, get_last_done_session,
    get_course_questions импортированы. Тексты «Что дальше» — в
    _finish_phase (basic-ветка), паузе (_handle_test «выйти»), _handle_done.
    _my_documents_text (~600) — секции.

- file: app/bitrix_bot.py
  why: |
    menu-клавиатура в _handle_employee_message (блок «Ответ на „Мои курсы“»):
    расширить условие на тест-состояния и «выйти»/«выход»; session None —
    клавиатура ТОЖЕ courses_menu, когда question был menu-командой (сейчас
    for_session(None) → None!) — вычислять selectable и для session None
    (роль _last_known_role). process_new_document — _canonical_url при
    save_draft_course (detail_url). ГОЧА: _kb_kwargs/BUTTONS_ENABLED-гейты.

- file: app/keyboards.py + tests/test_keyboard_design.py
  why: courses_menu(items, reading, resume_test=False) — resume_test ставит
       [▶ Продолжить тест→«Продолжить»] primary BLOCK первым (и подавляет
       «Готов»-primary: ≤1 primary); retake-кнопка [🔁 Пересдать→«Пересдать»]
       — параметр retake=False. ALLOWED_PAYLOADS += «Продолжить» (Пересдать
       уже разрешён). Роли/бюджеты по дизайн-спеке.

- file: tests/test_state_machine.py
  why: env-фикстура; test_test_pause_and_resume — расширить «Продолжить»;
       test_no_course_for_role_notifies_hr_once и retake-тесты НЕ ломать:
       menu-ветка в session None стоит ДО форка, но ПОСЛЕ whitelist; форк
       перехватывает по-прежнему всё, КРОМЕ menu/doc-команд — проверить
       tests/test_retake.py (форк-тексты дергаются сообщениями «привет» —
       не задеваются).

- file: app/hr_tools.py::_session_status
  why: эталон формулы «сдан» (score_exam >= round(total*0.7)) — в
       state_machine использовать _exam_pass_threshold (та же формула),
       НЕ дублировать константу.

- Транскрипт data/meeting_notes/2026.08.21 — первоисточник болей (цитаты
  Дмитрия про тупик паузы и завершённый курс, Евгении про список).
```

### Known Gotchas
```python
# CRITICAL: menu-команды в session None — ПОСЛЕ whitelist-гейта (чужим
# список не показывать), ДО _retake_fork_text (иначе форк съест «Мои курсы» —
# сегодняшний баг Дмитрия) и ДО автоназначения курса (иначе «Мои курсы»
# молча назначает курс — второй сегодняшний баг).

# CRITICAL: клавиатура ответа-меню при session None: for_session(None)
# возвращает None/fork — для menu-вопроса строить courses_menu из
# my_courses(_last_known_role). При отсутствии selectable — клавиатуру
# не прикреплять (None), текст сам подскажет.

# CRITICAL: ≤1 primary: resume_test=True → primary у «Продолжить тест»,
# «Готов» в этом режиме не ставится вовсе (в тесте «Готов» бессмыслен).

# GOTCHA: «продолжить» — только в _handle_test; в других состояниях слово
# уйдёт в RAG (безвредно). В тесте ставить ПОСЛЕ «выйти», ДО parse_answer.

# GOTCHA: статус «сдан» для курса БЕЗ exam_questions (старые тестовые) =
# DONE достаточно (порог 0); формула через _exam_pass_threshold сама даёт 0.

# GOTCHA: my_courses теперь зовёт get_course_questions на done-курсы —
# только для DONE-курсов из mine (≤ считанные разы); НЕ на каждый чанк.

# GOTCHA: _canonical_url: хост канона = BITRIX_WEBHOOK_URL (например
# https://portal.becar.ru/rest/...) — взять scheme+netloc через urlsplit;
# подменять ТОЛЬКО netloc у detail_url, путь не трогать. env пустой →
# вернуть url как есть.

# GOTCHA: разовый UPDATE доменов — на сервере, через docker exec sqlite;
# в тестах не нужен (нормализация покрыта юнитом).

# GOTCHA: тексты «Что дальше:» ловятся старыми тестами? grep «Ожидаем
# решения HR» — test_state_machine ассертит вход строки, ДОПИСЫВАНИЕ хвоста
# не ломает (assert in). Пауза-текст ассертится «паузе» и «вопрос 2/2» —
# дополнение хвостом безопасно.
```

## Implementation Blueprint

### Список задач (в порядке выполнения)

```yaml
Task 1 — _canonical_url + нормализация (bitrix_bot):
  - def _canonical_url(url): urlsplit(BITRIX_WEBHOOK_URL) → scheme, netloc;
    url пуст/без netloc или env пуст → url; иначе urlunsplit с подменой
  - process_new_document: detail_url = _canonical_url(meta.get("DETAIL_URL"))

Task 2 — меню отовсюду (state_machine):
  - process_message session None: после is_employee_allowed:
      cmd = message.strip().lower()
      if cmd in _MENU_COMMANDS: return my_courses(user_id,
          _last_known_role(user_id))[0]
      if cmd in _DOC_COMMANDS: return _my_documents_text(
          _last_known_role(user_id), chunks)
  - _handle_done: то же (роль session.get("role")), chunks прокинуть
    (сигнатура _handle_done(session, message, chunks) + вызов в диспетчере)
  - _handle_test: cmd == "продолжить" → format_question(current_q, q_idx,
    total, phase) с префиксом «▶ Продолжаем!»

Task 3 — честные статусы my_courses:
  - для курсов из done: passed = максимум score_exam по DONE-сессиям курса
    >= _exam_pass_threshold(questions) (exam пуст → passed=True)
  - строка «✅ … — пройден» / «❌ … — экзамен не сдан ({best}/{total})»
  - last = get_last_done_session(user_id); если failed-курс == last["course_id"]
    → приписка « · напиши *Пересдать*», флаг retake_available для клавиатуры
  - selectable НЕ меняется; contract (text, selectable) прежний; НОВОЕ:
    third возврат нельзя — retake-флаг передать через... селектабл-объекты
    не подходят → вернуть тройку? НЕТ: bitrix_bot после my_courses сам
    зовёт _retake_fork_text(user)? Проще: keyboards.courses_menu(retake=
    bool) — bitrix_bot вычисляет retake отдельным дешёвым вызовом
    (_retake_fork_text is not None) в menu-ветке.

Task 4 — «Что дальше:» хвосты (state_machine):
  - пауза теста: заменить текущий хвост на «Что дальше: ▶ *Продолжить* ·
    *Выбрать {номер}* · *Документы*» (+ список курсов как сейчас)
  - _finish_phase basic: + «\nЧто дальше: жди допуска HR; пока можешь
    смотреть *Мои курсы*, *Документы* и задавать вопросы по стандартам.»
  - _handle_done: + «\nЧто дальше: *Мои курсы* · *Документы*» (+
    « · *Пересдать*» если _retake_fork_text не None)

Task 5 — клавиатуры (keyboards + bitrix_bot):
  - courses_menu(items, reading=False, resume_test=False, retake=False):
    resume_test → [▶ Продолжить тест→«Продолжить»] primary BLOCK первым
    (reading-primary «Готов» подавлен); retake → [🔁 Пересдать] BLOCK
    secondary после курсов
  - _handle_employee_message: расширить menu-блок:
      menu_words = _MENU_COMMANDS + ("выйти", "выход")
      if question.lower() in menu_words and (after is None or
          after["state"] in (READING, WAITING_HR, BASIC_TEST, EXAM, DONE)):
        selectable из my_courses (роль: after.get("role") или
        _last_known_role); keyboard = courses_menu(...,
        resume_test=(after и after.state in тестах),
        retake=(_retake_fork_text не None — только при after None или DONE))
      (session None: after None — работает же ветка)
  - ALLOWED_PAYLOADS += r"^Продолжить$"; SAMPLES += courses_menu с
    resume_test/retake

Task 6 — тесты:
  - test_state_machine: session None + «Мои курсы» → список (не форк: сид
    провальной DONE-сессии, «Мои курсы» НЕ отдаёт форк-текст, «привет» —
    отдаёт); «Продолжить» в тесте → тот же вопрос, q_idx цел; статусы:
    провал → «не сдан (X/Y)» + «Пересдать»-приписка, сдан → «пройден»;
    _handle_done «Что дальше»
  - test_qa_first/_my_documents: секции «Для твоего отдела»/«Общие»
  - test_keyboard_design: resume_test/retake раскладки, ≤1 primary
  - test_buttons_everywhere: menu-клавиатура из теста (get_session →
    BASIC_TEST, question «выйти» → courses_menu с [▶ Продолжить тест])
  - юнит _canonical_url
  - обновить затронутые ассерты паузы (хвост «Что дальше»)

Task 7 — task.md + деплой-хвост:
  - разовый UPDATE доменов на сервере + контроль: ссылка курса открывается
  - промежуточное письмо Дмитрию/Евгении (юзер шлёт): список фиксов
  - отметка: пересдача произвольного курса — отложено
```

## Validation Loop
```bash
ruff check . --fix
python -m pytest tests/ -v
python - <<'EOF'
from app.bitrix_bot import _canonical_url
import os
os.environ["BITRIX_WEBHOOK_URL"] = "https://portal.becar.ru/rest/1/x/"
print(_canonical_url("https://propertyhunters.pro/company/doc.docx"))
EOF
```

## Final validation Checklist
- [ ] pytest/ruff зелёные; payload-инварианты с новыми кнопками
- [ ] Живой сценарий Дмитрия: тест → [⏸ Отложить] → видны [▶ Продолжить
      тест] и курсы → [▶] возвращает тот же вопрос
- [ ] БД: доменов propertyhunters.pro в courses не осталось; ссылка из
      приветствия курса открывается на portal.becar.ru
- [ ] task.md обновлён; черновик письма отдан юзеру

## Anti-Patterns to Avoid
- ❌ «Мои курсы» не должна иметь побочных эффектов НИГДЕ (не назначать
  курс, не переключать состояния)
- ❌ Не чинить домены только в БД без нормализации при ингесте (следующая
  заливка вернёт битые)
- ❌ Не давать «Пересдать»-кнопку там, где механика её не выполнит
- ❌ Не добавлять вторую primary (Продолжить vs Готов)
- ❌ Не менять payload'ы существующих кнопок

## Confidence Score: 8/10
Каждая правка — в знакомую точку с готовыми тестовыми харнессами; боли
воспроизведены транскриптом, домены проверены живой БД. Минус два балла:
(1) ветка session None плотная (whitelist/форк/автоназначение) — порядок
вставки критичен, покрыт flow-тестами; (2) статусы «сдан/не сдан» тянут
вопросы к старым DONE-строкам с course_id=0 (маркеры «нет курсов» №11) —
фильтровать course_id!=0, проверить на фикстурах.
