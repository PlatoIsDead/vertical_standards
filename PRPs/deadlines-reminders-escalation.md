name: "№9 v2 — Дедлайны, ежедневные напоминания сотруднику, двухступенчатая эскалация, пересдача"
description: |
  ПЕРЕГЕНЕРАЦИЯ PRPs/retake-escalation.md под скоуп, ИЗМЕНЁННЫЙ клиентом
  (протокол Дмитрия Челнокова, 05-07.08): неделя на прохождение с момента
  НАЗНАЧЕНИЯ (не старта сессии), сотруднику — ежедневные напоминания с
  остатком дней (старое решение «напоминаний НЕТ» отменено клиентом),
  эскалация ДВУХступенчатая (7д → руководители, 14д → старшие руководители).
  Пересдача и реестр руководителей — из старого PRP, почти без изменений.

## Purpose
Одно-проходная реализация №9 v2. СТАРЫЙ PRP `PRPs/retake-escalation.md`
остаётся справочником (паттерны, ловушки FSM/лупов там разобраны и
действительны) — здесь только дельта скоупа и новая механика сроков.
База — код после cdff728: инверсия роль→курс, course_roles/pick_course_for_role,
_course_recipients (рассылка), «Мои курсы».

## Core Principles
1-5 как в старом PRP. Плюс: **ни одной второй формулы** — сдача везде
`score_exam >= round(exam_total * 0.7)`, назначенность везде через
`course_roles`-пересечение (та же логика, что _course_recipients).

## Что ИЗМЕНИЛОСЬ против старого PRP (дельта скоупа)
```
БЫЛО (решения 24.07)                 СТАЛО (протокол клиента 05-07.08)
─────────────────────────────────────────────────────────────────────────
напоминаний сотруднику НЕТ         → ЕЖЕДНЕВНО ~09:00 МСК: список
                                     непройденных курсов + остаток дней
триггер = 7д от started_at сессии  → 7д от НАЗНАЧЕНИЯ (активация курса /
  (только начатые)                   попадание в whitelist) — «не начал
                                     вообще» тоже эскалируется
одна ступень (managers)            → ступень 1 (7д) → level-1 руководители;
                                     ступень 2 (14д) → level-2 («выше»);
                                     level 2 пуст → level 1 + HR
канал чат Bitrix (SMTP позже)      → БЕЗ ИЗМЕНЕНИЙ: чат Bitrix; протокол
                                     говорит email, но SMTP-кредов нет —
                                     известное ограничение, проговорить
пересдача «Пересдать»              → БЕЗ ИЗМЕНЕНИЙ + развилка после провала
                                     (см. Gotcha «окно пересдачи»)
```

## Goal

Сотрудник каждое утро (~09:00 МСК) получает от employee-бота одно сообщение:
«📅 Твои курсы: „Алгоритм бронирования" — осталось 3 дн.; „Конфиденциальность"
— просрочен на 2 дн.» На 8-й день непройденный курс уходит сводкой
level-1 руководителям, на 15-й — level-2. Провативший экзамен пишет
«Пересдать» и проходит экзамен заново.

## What / Success Criteria
- [ ] `deadline = max(course.approved_at, employee.added_at, baseline) + 7д`
      (baseline — meta-метка первого запуска после деплоя: старые курсы не
      просрочены пачкой в день деплоя)
- [ ] Напоминание: раз в день, идемпотентно (meta `reminder_last_date`, дата
      МСК), только сотрудникам с непройденными назначенными курсами; учтены
      и не начатые курсы (сессии нет), и текущий
- [ ] Эскалация stage 1 (>7д) и stage 2 (>14д) — по одному разу на
      (user_id, course_id, stage); сводка одним сообщением на руководителя
- [ ] «Пересдать»: exam-ответы стёрты, basic цел, state=EXAM/q0; после
      провала следующее сообщение предлагает выбор «Пересдать / Далее»
- [ ] HR-команды: «Руководители», «Руководитель добавить {email} [старший]»,
      «Руководитель удалить {email}»; сид n.sharapov@proptech.digital (level 1)
- [ ] 134 теста зелёные + новые; ruff чистый

## All Needed Context

### Documentation & References
```yaml
- file: PRPs/retake-escalation.md
  why: |
    ОСНОВА. Оттуда действительны целиком: Gotchas про FSM (get_session
    фильтрует DONE; перехват ДО создания сессии), update_session и score=0,
    _HR_COMMAND_PREFIXES (добавить «руководител…» — иначе pending-правка №3
    съест команду), naive-UTC-ISO сравнение строк, «последняя сессия пары —
    max id», exam_total==0 → пропуск, archived → пропуск, транзиентный сбой
    Bitrix → НЕ помечать, паттерны тестов (test_hr_invite каркас, эмодзи-
    ловушка). Псевдокод _handle_retake и ветки «руководител…» — брать оттуда.
    УСТАРЕЛО там: триггер от started_at, «напоминаний НЕТ», одноступенчатость,
    сид эскалаций из sessions (заменён baseline-меткой).

- file: app/state_machine.py
  why: |
    course_roles / pick_course_for_role / _done_course_ids / _last_known_role —
    ЕДИНСТВЕННЫЙ источник «назначенности» и «пройденности». Ветка
    session is None (~44): whitelist → [СЮДА перехват пересдать/далее] →
    назначение курса. _finish_phase — строка про пересдачу при not passed.
    md_to_bb — если PRP ux-abbrevs-polish исполнен раньше, конвертер уже есть.

- file: app/bitrix_bot.py
  why: |
    _course_recipients — та же логика назначенности (роль ∩ курс, без роли →
    только ALL): «назначенные курсы сотрудника» = обратная проекция.
    _weekly_rating_loop + gamification.maybe_post_weekly_rating — ОБРАЗЕЦ
    ежедневного лупа с МСК-гейтом через meta. _send / _hr_ids /
    _bitrix_user_by_email / стартовая регистрация лупов.

- file: app/gamification.py
  why: МСК-математика (UTC+3) и идемпотентность через meta — копировать приём.

- file: app/db.py
  why: |
    meta get/set; employees.added_at (база дедлайна для поздно приглашённых);
    courses.approved_at; get_report_rows (+archived_at добавить, как в старом
    PRP); сид-паттерны init_db.
```

### Known Gotchas (дельта к старому PRP)
```python
# КРИТИЧНО — «окно пересдачи» (следствие №11, в старом PRP этого не было):
# после DONE любое сообщение АВТО-НАЗНАЧАЕТ следующий курс (ветка session is
# None). Если сотрудник провалил экзамен и написал «привет» — он уже в новом
# курсе, и «Пересдать» ушло бы в RAG. РЕШЕНИЕ: в ветке session is None, если
# ПОСЛЕДНЯЯ DONE-сессия провалена (score_exam < round(total*0.7)) — НЕ
# назначать курс молча, а ответить развилкой: «Ты не сдал „X" (3/10). Напиши
# *Пересдать* — пересдача, *Далее* — следующий курс.» Перехваты:
# «пересдать»/«пересдача» → _handle_retake; «далее» → обычное назначение.
# Развилка детерминированная (без состояния): любое другое сообщение — снова
# развилка. При активной сессии команда «пересдать» в READING → подсказка
# «сначала закончи текущий курс» (реактивация старой DONE-сессии при живой
# новой создала бы ДВЕ активные — get_session вернёт не ту).

# КРИТИЧНО: базу дедлайна считать БЕЗ сессий: назначение существует до
# первого сообщения сотрудника. deadline_base(course, employee, baseline) =
# max(approved_at, added_at, baseline) — все три naive-UTC-ISO строки,
# max() по строкам корректен (ISO сортируется лексикографически).

# КРИТИЧНО: baseline — meta 'deadlines_baseline', ставится ОДИН раз в
# init_db() (гейт по отсутствию ключа), значение utcnow().isoformat().
# Это замена старому «сиду эскалаций из sessions»: на дату деплоя ничего
# не просрочено, отсчёт с нуля. Таблицы escalations в живой БД НЕТ (старый
# PRP не исполнялся) — схему сразу с колонкой stage.

# КРИТИЧНО: напоминания шлёт EMPLOYEE-бот (BOT_ID), эскалации — HR-бот
# (HR_BOT_ID): _send сам подставит нужный CLIENT_ID (см. №2/демо-пак A).

# Напоминание считает те же назначения, что эскалация — ОДНА чистая функция
# assignments_with_deadlines(employees, courses, sessions, baseline, now):
# список (uid, course, deadline, days_left, started: bool). Напоминание
# фильтрует «не пройден», эскалация — «не пройден и days_left <= -0/-7».

# Роль сотрудника для назначенности: _last_known_role. Сотрудник ролью так и
# не обзавёлся (не писал боту) → назначены только ALL-курсы (та же логика,
# что рассылка) — НЕ эскалировать RES-курс тому, кто роли не выбирал.

# stage 2 показывает и то, что уже эскалировано stage 1 (это не дубль:
# другой получатель); (uid, course_id, stage) — PK, повторов внутри ступени нет.

# HR-команда «Руководитель добавить {email} старший» → level 2; без слова —
# level 1. В списке «Руководители» уровень показывать («• x@y (старший)»).

# ESCALATION_DAYS=7 (env, дефолт), ступень 2 = 2*ESCALATION_DAYS. Часовой
# чек обоих лупов; напоминание — гейт «сейчас >= 9:00 МСК и meta-дата != сегодня-МСК».
```

## Implementation Blueprint

### Data model
```sql
CREATE TABLE IF NOT EXISTS managers (
    email    TEXT PRIMARY KEY,          -- strip().lower()
    level    INTEGER NOT NULL DEFAULT 1, -- 1 = руководитель, 2 = «выше»
    added_by TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS escalations (
    user_id     TEXT NOT NULL,
    course_id   INTEGER NOT NULL,
    stage       INTEGER NOT NULL,        -- 1 | 2
    notified_at TEXT,
    PRIMARY KEY (user_id, course_id, stage)
);
-- meta: 'deadlines_baseline' (один раз), 'reminder_last_date' (дата МСК)
```

### List of tasks
```yaml
Task 1 — app/db.py: таблицы + сид менеджера (level 1) + baseline-гейт;
  функции add_manager(email, added_by, level)/remove_manager/get_managers
  (ORDER BY level, added_at), get_escalated(stage)->set, mark_escalated(uid,
  course_id, stage), delete_session_answers, get_last_done_session (из
  старого PRP), get_report_rows +archived_at

Task 2 — app/deadlines.py (НОВЫЙ, чистый — ни сети, ни БД):
  assignments_with_deadlines(employees, courses, sessions_by_uid, roles_by_uid,
    baseline, now, days) -> list[Assignment-dict]
    # назначенность: роль ∩ course_roles (без роли → только ALL); пройден =
    # есть DONE-сессия пары С УЧЁТОМ сдачи? НЕТ: «пройден» = DONE-сессия есть
    # (как _done_course_ids №11) — провал закрывает НАЗНАЧЕНИЕ, но открывает
    # ПЕРЕСДАЧУ (эскалация судит отдельным правилом ниже)
  build_reminder_text(items) -> str | None      # «осталось N дн./просрочен»
  find_due_escalations(items, sessions, already, stage, days) -> list
    # не пройдено ИЛИ провалено (последняя сессия пары); deadline+ (stage-1)*days
  build_escalation_message(due, employees_by_uid, stage) -> str | None
    # статусы — hr_tools._session_status; «не начинал» для пар без сессии

Task 3 — state_machine: развилка после провала + «пересдать»/«далее» +
  _handle_retake (из старого PRP: delete_session_answers, score_exam=0) +
  строка про пересдачу в _finish_phase; «пересдать» в READING → подсказка

Task 4 — bitrix_bot: ветка «руководител…» (+_HR_COMMAND_PREFIXES, help),
  _reminder_loop (час; гейт 9:00 МСК + meta-дата; шлёт BOT_ID),
  _escalation_loop (час; stage 1 → level 1, stage 2 → level 2, пустой
  level 2 → level 1 + HR; резолв email → user.get; сбой → не помечать),
  регистрация обоих лупов в startup

Task 5 — тесты: test_deadlines.py (чистые: назначенность/сроки/ступени/
  провал-vs-пройден/без роли), test_retake.py (+развилка «Пересдать/Далее»,
  «далее» назначает курс, retake при активной сессии — отказ),
  test_hr_managers.py (уровни), test_reminders.py (гейт даты, тексты,
  идемпотентность), test_db.py (baseline один раз)

Task 6 — task.md: чеклист живого прогона (ESCALATION_DAYS=0-трюк из старого
  PRP, напоминание себе, ступень 2, «Пересдать» живьём)
```

### Integration Points
```yaml
CONFIG: ESCALATION_DAYS=7 (env, дефолт в коде); REMINDER_HOUR_MSK=9
STARTUP: два новых лупа рядом с _rating_task
DEPLOY:  после рестарта meta получает baseline — до него сроки не тикают
```

## Validation Loop
```bash
ruff check app/ scripts/ tests/ --fix
python -m pytest tests/ -v    # все существующие + новые
```
Живое — чеклист task.md (первый контакт с руководителем = проактив, CLIENT_ID).

## Anti-Patterns to Avoid
- ❌ Вторая формула сдачи / назначенности — только round(total*0.7) и course_roles
- ❌ Эскалация по UF_HEAD/department.get — путь отменён клиентом ещё 24.07
- ❌ Реактивация DONE-сессии при живой активной (две активные — FSM слепнет)
- ❌ Пометка эскалации при транзиентном сбое; сид эскалаций без baseline
- ❌ Напоминания чаще раза в день / без meta-гейта (рестарт = спам)
- ❌ SMTP/email в этом PRP — только чат Bitrix, email после кредов клиента

## Score: 7/10
Механика сроков вся на чистых функциях с полным тест-покрытием, паттерны
лупов/реестра проверены. Минус: «окно пересдачи» пересекается с №11-веткой
назначения (самый горячий путь FSM, три новых перехвата — пересдать/далее/
развилка); минус: две доставки (напоминание BOT_ID, эскалация HR_BOT_ID)
проверяются живьём; минус: понятие «пройден vs провален» двоится между
назначенностью и эскалацией — соблюдать формулировки Task 2 дословно.
