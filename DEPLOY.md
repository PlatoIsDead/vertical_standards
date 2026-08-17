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
./deploy.sh
```

Скрипт: код → `~/vertical-standards/src/`, сборка образа `vertical-standards:latest`,
перезапуск контейнера `vertical-standards-bot` (порт 8000 наружу).
`.env`, `state/onboarding.db`, `state/data/` сидируются **только при первом деплое** —
повторные запуски серверное состояние не перезаписывают. Канал до сервера может
флапать — внутри ретраи (ControlMaster, сокет `/tmp/mux-vert`).

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
