#!/usr/bin/env bash
# services.sh - start Redis, PostgreSQL and MinIO for EngineAI, all local.
#
# No systemd in this WSL image, so each daemon is started directly with its
# state under ~/.engineai/data. Everything binds 127.0.0.1: these carry the
# workspace and the job queue, and neither belongs on a network interface.
#
#   bash services.sh start | stop | status
source ~/miniconda3/etc/profile.d/conda.sh
conda activate svc 2>/dev/null

ROOT=~/.engineai
PGDATA=$ROOT/pgdata
MINIODATA=$ROOT/minio
LOGS=$ROOT/logs
mkdir -p "$LOGS" "$MINIODATA"

start_redis() {
  if redis-cli -p 6379 ping >/dev/null 2>&1; then echo "  redis    already up"; return; fi
  redis-server --port 6379 --bind 127.0.0.1 --daemonize yes \
    --dir "$ROOT" --dbfilename engineai.rdb \
    --logfile "$LOGS/redis.log" --save 300 10
  sleep 1
  redis-cli -p 6379 ping >/dev/null 2>&1 && echo "  redis    started" || echo "  redis    FAILED"
}

start_pg() {
  if pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null; then echo "  postgres already up"; return; fi
  if [ ! -d "$PGDATA" ]; then
    initdb -D "$PGDATA" -U engineai --auth=trust -E UTF8 >"$LOGS/initdb.log" 2>&1 \
      && echo "  postgres cluster created" || { echo "  postgres initdb FAILED"; return; }
  fi
  pg_ctl -D "$PGDATA" -l "$LOGS/postgres.log" \
    -o "-p 5432 -k $ROOT -c listen_addresses=127.0.0.1" start >/dev/null 2>&1
  sleep 2
  if pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null; then
    psql -h 127.0.0.1 -U engineai -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname='engineai'" 2>/dev/null | grep -q 1 \
      || createdb -h 127.0.0.1 -U engineai engineai 2>/dev/null
    echo "  postgres started"
  else
    echo "  postgres FAILED"; tail -3 "$LOGS/postgres.log" 2>/dev/null
  fi
}

start_minio() {
  if curl -s -o /dev/null -m 2 http://127.0.0.1:9000/minio/health/live; then
    echo "  minio    already up"; return; fi
  MINIO_ROOT_USER=engineai MINIO_ROOT_PASSWORD=engineai-local \
    setsid nohup ~/.local/bin/minio server "$MINIODATA" \
    --address 127.0.0.1:9000 --console-address 127.0.0.1:9001 \
    >"$LOGS/minio.log" 2>&1 < /dev/null &
  sleep 3
  curl -s -o /dev/null -m 3 http://127.0.0.1:9000/minio/health/live \
    && echo "  minio    started" || echo "  minio    FAILED"
}

case "${1:-status}" in
  start) echo "starting:"; start_redis; start_pg; start_minio ;;
  stop)
    echo "stopping:"
    redis-cli -p 6379 shutdown nosave 2>/dev/null && echo "  redis    stopped"
    pg_ctl -D "$PGDATA" stop -m fast >/dev/null 2>&1 && echo "  postgres stopped"
    pkill -f 'minio server' 2>/dev/null && echo "  minio    stopped"
    ;;
  status)
    redis-cli -p 6379 ping >/dev/null 2>&1 && echo "  redis    up" || echo "  redis    down"
    pg_isready -h 127.0.0.1 -p 5432 -q 2>/dev/null && echo "  postgres up" || echo "  postgres down"
    curl -s -o /dev/null -m 2 http://127.0.0.1:9000/minio/health/live \
      && echo "  minio    up" || echo "  minio    down"
    ;;
esac
