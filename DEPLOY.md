# Деплой

Сервер: `nikita@195.63.168.145` порт `58528` (vdsina, Debian 13, 2 ГБ RAM),
ключ `~/.ssh/id_ed25519_timeweb`, sudo без пароля. Выдан Сергеем Бабиным 12.08.2026.

## Разовая подготовка сервера (уже выполнена 12.08.2026)

```bash
sudo apt-get update && sudo apt-get install -y docker.io rsync curl
sudo usermod -aG docker nikita   # затем перелогиниться
sudo systemctl enable --now docker
```

(get.docker.com не подошёл — на голом сервере не было curl; docker.io 26.1.5 из apt.)

## Деплой / редеплой

```bash
./deploy.sh                # деплой текущего HEAD
./deploy.sh --tag v1.1     # то же + создать и запушить annotated-тег релиза
```

Деплой СТРОГО ИЗ GIT: уезжает `git archive HEAD` (не рабочая папка);
незакоммиченное в app/scripts/requirements.txt/Dockerfile блокирует запуск.
Образ тегируется `vertical-standards:latest` + `vertical-standards:<rev>`,
хеш коммита виден в логе старта (`[build] git commit: ...`) и в `GIT_COMMIT`
env контейнера. Перезапуск контейнера `vertical-standards-bot` (порт 8000).

## Откат

Быстрый (на сервере, без пересборки — старые образы хранят каждый деплой):

```bash
docker images vertical-standards        # выбрать <rev>
docker rm -f vertical-standards-bot
# повторить docker run из deploy.sh (шаг 5/5), подставив vertical-standards:<rev>
```

Каноничный (через git-тег):

```bash
git checkout v1.0 && ./deploy.sh && git checkout master
```
`.env`, `state/onboarding.db`, `state/data/` сидируются **только при первом деплое** —
повторные запуски серверное состояние не перезаписывают. Исключение — `roles.json`,
см. ниже. Канал до сервера может флапать — внутри ретраи (ControlMaster,
сокет `/tmp/mux-vert`).

### roles.json: слияние, а не копия

Реестр ролей живёт в двух руках: в git (`data/roles.json`) и на сервере, где его
правит HR прямо из бота — «Аббревиатура добавить FIN Финансовая служба» дописывает
префикс и роль через `save_roles_config`. Поэтому каждый деплой зовёт
`scripts/merge_roles.py`: он доливает то, чего на сервере нет, ничего не удаляет
и **не перезаписывает расхождения** — печатает их в лог деплоя строкой
`⚠️ расхождение (оставлено значение сервера)`, решение за человеком.

Перед записью кладётся бэкап `state/data/roles.json.bak-<дата>-<время>`; откат —
вернуть его на место, рестарт не нужен (`load_roles_config()` читает файл на каждый
вызов). Если слияние ничего не добавило, печатается «изменений нет» и файл не трогается.

Почему так: копия «только при отсутствии» держала на сервере конфиг от 11.08 без роли
«Менеджер» и ключа `all_access` — HR-роль легаси-аккаунтов молча не применялась, и
сотрудники получали меню выбора роли вместо назначенной (разобрано 09.09).

## Раскладка на сервере

```
~/vertical-standards/
  src/                 # код (build context), перезаписывается каждым деплоем
  .env                 # секреты — правится ТОЛЬКО на сервере
  state/
    onboarding.db      # SQLite (курсы, сессии, менеджеры) — НЕ переносить с локали повторно
    data/              # RAG-индекс: chunks_cache.json, embeddings_cache.npy, roles.json, courses/
```

Оба каталога состояния примонтированы в контейнер (`/state`, `/app/data`),
`DB_PATH=/state/onboarding.db` задаётся через `-e` в deploy.sh.

## Переключение ботов Bitrix (руками, один раз)

В настройках приложений на portal.becar.ru заменить handler-URL с ngrok на сервер:

| Бот           | URL                              |
|---------------|----------------------------------|
| Employee-бот  | `http://195.63.168.145:8000/`    |
| HR-бот        | `http://195.63.168.145:8000/hr`  |

Если портал не примет http (потребует https) — нужен домен/сертификат, вопрос к Сергею.

## Проверка

```bash
ssh -i ~/.ssh/id_ed25519_timeweb -p 58528 nikita@195.63.168.145 \
  "docker logs --tail 50 vertical-standards-bot"
```

В логе при старте: `[poller] Started`, `[rating] Started`, `[reminder] Started`,
`[escalation] Started`. Написать боту в портале — в логе появится `MSG user=...`.
