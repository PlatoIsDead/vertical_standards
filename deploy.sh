#!/usr/bin/env bash
# deploy.sh — деплой бота онбординга на сервер nikita@195.63.168.145:58528.
#
# ДЕПЛОЙ СТРОГО ИЗ GIT: на сервер уезжает git archive HEAD (не рабочая папка),
# незакоммиченные изменения в деплоящихся путях блокируют запуск — «что крутится
# на сервере» всегда равно конкретному коммиту. Образ дополнительно тегируется
# хешем коммита (vertical-standards:<rev>) — мгновенный откат без пересборки.
#
#   ./deploy.sh                — деплой текущего HEAD
#   ./deploy.sh --tag v1.1     — то же + создать и запушить annotated-тег
#
# Откат: см. DEPLOY.md («Откат»).
# Требует: docker уже установлен на сервере (разовая подготовка — см. DEPLOY.md).
# Состояние (.env, state/) сидируется ТОЛЬКО при первом деплое — повторные
# запуски серверные данные не трогают.
set -euo pipefail

HOST=195.63.168.145
PORT=58528
RUSER=nikita
KEY="$HOME/.ssh/id_ed25519_timeweb"
BASE=/home/nikita/vertical-standards
IMAGE=vertical-standards:latest
CONTAINER=vertical-standards-bot
MUX=/tmp/mux-vert   # короткий путь — лимит unix-сокета 108 байт

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
SSH_OPTS=(-i "$KEY" -p "$PORT" -o ControlMaster=auto -o ControlPath="$MUX"
          -o ControlPersist=600 -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)

# Деплоящиеся пути — ровно то, что попадает в build context
SRC_PATHS=(app scripts requirements.txt Dockerfile .dockerignore)

TAG="${2:-}"
if [ "${1:-}" = "--tag" ] && [ -z "$TAG" ]; then
  echo "Формат: ./deploy.sh --tag v1.1" >&2; exit 1
fi

# Гейт «деплой = коммит»: незакоммиченное в SRC_PATHS блокирует деплой
if [ -n "$(git -C "$LOCAL_DIR" status --porcelain -- "${SRC_PATHS[@]}")" ]; then
  echo "✋ Незакоммиченные изменения в ${SRC_PATHS[*]} — закоммить/стэшнуть:" >&2
  git -C "$LOCAL_DIR" status --short -- "${SRC_PATHS[@]}" >&2
  exit 1
fi
REV=$(git -C "$LOCAL_DIR" rev-parse --short HEAD)
echo "== Деплой коммита $REV${TAG:+ (тег $TAG)}"

if [ -n "$TAG" ]; then
  git -C "$LOCAL_DIR" tag -a "$TAG" -m "release $TAG"
  git -C "$LOCAL_DIR" push origin "$TAG"
fi

run() {  # ssh с ретраями: канал до RU-хостов флапает
  local n
  for n in 1 2 3 4 5; do
    if ssh "${SSH_OPTS[@]}" "$RUSER@$HOST" "$@"; then return 0; fi
    echo "  ssh retry $n/5..." >&2
    ssh -o ControlPath="$MUX" -O exit "$RUSER@$HOST" 2>/dev/null || true
    sleep $((n * 3))
  done
  echo "ssh не прошёл после 5 попыток: $*" >&2
  return 1
}

sync() {  # rsync с теми же ретраями
  local n
  for n in 1 2 3 4 5; do
    if rsync -az -e "ssh ${SSH_OPTS[*]}" "$@"; then return 0; fi
    echo "  rsync retry $n/5..." >&2
    sleep $((n * 3))
  done
  return 1
}

echo "== 1/5 Каталоги на сервере"
run "mkdir -p $BASE/src $BASE/state/data"

echo "== 2/5 Код (git archive $REV) → $BASE/src/"
ARCHIVE=$(mktemp /tmp/vs-src-XXXX.tgz)
trap 'rm -f "$ARCHIVE"' EXIT
git -C "$LOCAL_DIR" archive --format=tar.gz -o "$ARCHIVE" HEAD "${SRC_PATHS[@]}"
sync "$ARCHIVE" "$RUSER@$HOST:$BASE/src.tgz"
run "rm -rf $BASE/src && mkdir -p $BASE/src \
    && tar -xzf $BASE/src.tgz -C $BASE/src && rm $BASE/src.tgz"

echo "== 3/5 Сидирование состояния (только отсутствующее)"
if ! run "test -f $BASE/.env"; then
  echo "  .env отсутствует — копирую локальный"
  sync "$LOCAL_DIR/.env" "$RUSER@$HOST:$BASE/.env"
fi
if ! run "test -f $BASE/state/onboarding.db"; then
  echo "  onboarding.db отсутствует — копирую локальную базу"
  sync "$LOCAL_DIR/onboarding.db" "$RUSER@$HOST:$BASE/state/"
fi
for f in chunks_cache.json embeddings_cache.npy roles.json; do
  if ! run "test -f $BASE/state/data/$f"; then
    echo "  data/$f отсутствует — копирую"
    sync "$LOCAL_DIR/data/$f" "$RUSER@$HOST:$BASE/state/data/"
  fi
done
if ! run "test -d $BASE/state/data/courses"; then
  echo "  data/courses/ отсутствует — копирую"
  sync "$LOCAL_DIR/data/courses" "$RUSER@$HOST:$BASE/state/data/"
fi

echo "== 4/5 Сборка образа ($REV)"
run "docker build -t $IMAGE -t vertical-standards:$REV $BASE/src"

echo "== 5/5 Перезапуск контейнера"
run "docker rm -f $CONTAINER 2>/dev/null || true
docker run -d --name $CONTAINER --restart unless-stopped \
  -p 8000:8000 \
  -e DB_PATH=/state/onboarding.db \
  -e GIT_COMMIT=$REV \
  -v $BASE/.env:/app/.env:ro \
  -v $BASE/state:/state \
  -v $BASE/state/data:/app/data \
  $IMAGE"

sleep 3
run "docker ps --filter name=$CONTAINER --format '{{.Names}} {{.Status}}' && docker logs --tail 15 $CONTAINER"
echo "== Готово"
