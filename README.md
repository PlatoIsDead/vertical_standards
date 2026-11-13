# Вертикаль — Онбординг-бот

Система автоматического обучения сотрудников апарт-отелей «Вертикаль».
FastAPI-бэкенд, два Bitrix24-бота (сотрудник + HR), SQLite, RAG на OpenAI.

---

## Архитектура

### Поток 1 — Загрузка документа и создание курса

```
Bitrix Disk (новый .docx / .md)
        │
        ├── Webhook  POST /disk-webhook (OnDiskFileAdd)
        └── Поллер   _disk_poll_loop() — каждые POLL_INTERVAL сек.
                │
                ▼
        bitrix_bot.py → process_new_document()
                │
                ├─ 1. Скачать файл через disk.file.get + DOWNLOAD_URL
                ├─ 2. Распарсить на чанки (parse_docx / _parse_md)
                ├─ 3. Embed чанки (text-embedding-3-small)
                ├─ 4. Добавить к chunks_cache.json + embeddings_cache.npy
                ├─ 5. Перезагрузить RAG-индекс (load_index)
                ├─ 6. Сгенерировать вопросы (course_generator.py → GPT)
                │       └─ Адаптивный список вопросов единого теста (JSON)
                ├─ 7. Сохранить черновик курса в SQLite (courses)
                └─ 8. Уведомить HR через HR-бота
```

### Поток 2 — Проверка и активация курса (HR-бот, POST /hr)

```
HR пишет HR-боту
        │
        ├─ "Курсы"           → список курсов на проверке (статус PENDING)
        ├─ "Вопросы {N}"     → предпросмотр вопросов курса №N
        ├─ "Подтвердить {N}" → activate_course_by_id() → курс становится ACTIVE
        └─ "Допустить {uid}" → допуск только для сохранённых legacy-сессий
                                + уведомить сотрудника через employee-бота
```

### Поток 3 — Обучение сотрудника (Employee-бот, POST /)

```
Сотрудник пишет боту
        │
        ▼
 state_machine.process_message()
        │
        ├─ Нет сессии?
        │       └─ Создать сессию (courses.active → sessions) → ROLE_SELECT
        │
        ├─ [ROLE_SELECT]
        │       └─ Цифра 1..N → sessions.role → READING
        │
        ├─ [READING]
        │       ├─ Любое сообщение → RAG-ответ по стандартам
        │       │       (фильтр по роли сотрудника + audience=guest исключён)
        │       ├─ "Роль" → сменить роль (обратно в ROLE_SELECT)
        │       └─ "Готов" → единый тест (EXAM), вопрос 1/N
        │
        ├─ [EXAM] (единый тест новых сессий)
        │       ├─ Ответ A/B/C/D → следующий вопрос
        │       ├─ "Выйти" / "Продолжить" → пауза с сохранением вопроса
        │       └─ Последний ответ → DONE, единый итог и уведомление HR
        │               └─ Сдан при >= ceil(N × 70%); без допуска между этапами
        │
        └─ [DONE]
                └─ Показать итоговые результаты
```

Новые сессии сохраняются с `quiz_version=2`: вопросы объединяются в порядке
`basic_questions` → `exam_questions`, повторяющиеся тексты (без учёта регистра
и пробелов) исключаются, сохраняется первое вхождение. Исходные группы остаются
для правок HR. Число вопросов зависит от документа, вопросы не дублируются ради
числа. При первом запуске снимок исходных вопросов сохраняется в
`quiz_questions_json` сессии: правки курса не меняют текущую попытку или историю.
Пересдача создаёт новую сессию с тем же снимком и сохраняет ответы прошлой.
Итог единого теста хранится в `score_exam`; `score_basic` у новых сессий равен 0.

`init_db()` добавляет две колонки идемпотентно; существующие строки получают
`quiz_version=1` и продолжаются с прежними фазами, индексами, seed перемешивания
и исторической формулой сдачи. Сохранённые legacy-сессии не объединяются на лету.
Одобрение курса HR перед активацией сохраняется.

Кнопки ответа содержат сессию, попытку, фазу и индекс вопроса. Повторное или
устаревшее нажатие не записывает ответ. Старые кнопки с одной буквой отклоняются
на `/command`; ручной ввод A–D остаётся доступен. Порядок вариантов в тексте,
кнопках, оценке и обратной связи задаёт `quiz.question_view`.

Локальные проверки: `python -m pytest -q`, `python -m ruff check` для изменённых
Python-файлов и `git diff --check`. `tests/conftest.py` до сбора тестов блокирует
сеть и перенаправляет БД, индекс и media-кэш во временные файлы.

---

## Структура проекта

```
vertical_standards/
├── app/
│   ├── bitrix_bot.py        # FastAPI: /  /hr  /disk-webhook + поллер ролевых папок
│   ├── state_machine.py     # FSM: ROLE_SELECT→READING→BASIC_TEST→WAITING_HR→EXAM→DONE
│   ├── course_generator.py  # Генерация вопросов через GPT
│   ├── db.py                # SQLite: courses / sessions / answers / processed_files
│   ├── rag.py               # Retrieval + generation (cosine similarity, ролевая маска)
│   ├── roles.py             # Реестр ролей, маппинг папка→роль, role_mask
│   ├── streamlit_app.py     # (dev) RAG-интерфейс для ручного тестирования
│   └── streamlit_review.py  # (dev) Просмотр курсов и результатов
├── scripts/
│   ├── parse_standards.py       # Парсинг .docx → чанки (с ролями по VA.*-кодам)
│   ├── build_index_openai.py    # Парсинг .md + embeddings → кэш-файлы
│   ├── backfill_roles.py        # Одноразовый бэкфилл roles+audience в индекс
│   ├── eval_roles.py            # Живая проверка ролевой фильтрации (вопросы Дмитрия)
│   └── generate_questions.py    # PoC: генерация вопросов напрямую из chunks_cache.json
├── tests/                       # pytest: маска ролей, миграция БД, FSM, бэкфилл
├── data/
│   ├── Vertical_franchise_standards.md
│   ├── Vertical_franchise_standards.docx
│   ├── roles.json               # Роли + маппинг «папка Bitrix Disk → роль»
│   ├── eval_roles.json          # Кейсы для scripts/eval_roles.py
│   ├── chunks_cache.json        # Генерируется скриптами индексации
│   ├── embeddings_cache.npy     # Генерируется скриптами индексации
│   └── courses/                 # Черновики сгенерированных курсов (JSON)
├── onboarding.db                # SQLite (создаётся при запуске)
├── .env
├── .env.example
└── requirements.txt
```

---

## База данных (SQLite)

```
courses
  id, doc_name, doc_id (Bitrix file ID), doc_detail_url,
  questions_json, created_at, approved_at, approved_by

sessions
  id, user_id, dialog_id, course_id → courses.id,
  state (ROLE_SELECT|READING|BASIC_TEST|WAITING_HR|EXAM|DONE),
  current_q_idx, score_basic, score_exam, role, started_at, updated_at

answers
  id, session_id → sessions.id, question_id, phase (basic|exam),
  user_answer, is_correct, answered_at

processed_files
  file_id (Bitrix file ID, PK), doc_name, folder_id, processed_at
  — гейт поллера: копия документа из второй ролевой папки ингестится
    в индекс (с ролью папки), но второй курс не создаётся
```

---

## Установка

```bash
cd ~/code/PlatoIsDead/vertical_standards
pyenv virtualenv 3.12.9 vertical_standards_env
pyenv local vertical_standards_env
pip install -r requirements.txt
cp .env.example .env
```

Переменные в `.env`:

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

BITRIX_WEBHOOK_URL=https://portal.becar.ru/rest/USER_ID/TOKEN/

BOT_ID=...            # ID employee-бота в Bitrix
HR_BOT_ID=...         # ID HR-бота (если отдельный, иначе = BOT_ID)
HR_USER_IDS=123,456   # Bitrix user ID HR-менеджеров через запятую

MONITOR_FOLDER_ID=... # ID папки на Bitrix Disk для автоимпорта
POLL_INTERVAL_SEC=300 # Интервал поллинга (по умолчанию 5 мин)
```

---

## Первичная индексация

```bash
# Из Markdown (рекомендуется для базового индекса)
python scripts/build_index_openai.py

# Из .docx (роли размечаются по VA.*-кодам разделов)
python scripts/parse_standards.py --input data/Vertical_franchise_standards.docx
```

Оба скрипта пишут в `data/chunks_cache.json` и `data/embeddings_cache.npy`.

---

## Поиск по ролям

Каждый чанк индекса несёт `roles` (кому адресован) и `audience` (staff/guest).
Сотрудник при старте выбирает роль цифрой; RAG отдаёт только чанки его роли
или `all_staff`, гостевые разделы (`audience=guest`) не отдаются никогда.

- Роли и маппинг «папка → роль»: `data/roles.json` (правится без рестарта — поллер
  перечитывает конфиг каждый цикл). Один документ на несколько департаментов =
  положить копию в каждую ролевую папку.
- Бэкфилл старого индекса (одноразово, с бэкапами `*.bak`):
  `python scripts/backfill_roles.py --dry-run` → `python scripts/backfill_roles.py`
- Проверка фильтрации на живом OpenAI (кейсы `data/eval_roles.json`):
  `python scripts/eval_roles.py`
- Тесты и линт: `python -m pytest tests/ -v` и `ruff check app/ scripts/ tests/`

---

## Запуск

```bash
uvicorn app.bitrix_bot:app --host 0.0.0.0 --port 8000
```

Для локальной разработки пробросить порт:

```bash
ngrok http 8000
```

Настроить в Bitrix24 → Разработчикам → Исходящие вебхуки:

| Бот              | URL                         | События                          |
|------------------|-----------------------------|----------------------------------|
| Employee-бот     | `https://*.ngrok/`          | `ONIMBOTMESSAGEADD`              |
| HR-бот           | `https://*.ngrok/hr`        | `ONIMBOTMESSAGEADD`              |
| Disk (опционально)| `https://*.ngrok/disk-webhook` | `OnDiskFileAdd`               |

---

## Стоимость API

- Индексация: `text-embedding-3-small` — ~$0.002 за весь базовый документ
- RAG-вопрос: embeddings + `gpt-4o-mini` — ~$0.001
- Генерация курса (15 вопросов): `gpt-4o-mini` — ~$0.003 за курс
