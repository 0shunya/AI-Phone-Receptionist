#!/usr/bin/env sh
# Production entrypoint: apply migrations, seed reference data (idempotent), then serve.
# One worker process: the per-call rate limits live in memory, and each turn already runs in a thread.
set -e
python -m alembic upgrade head
python -m scripts.seed
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips="*" --no-server-header --ws-ping-interval 20
