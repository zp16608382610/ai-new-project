#!/usr/bin/env bash
# Phase 8B backend container boot:
#   1) prepare schema + seed the demo database (idempotent; DATABASE_URL
#      normally points at the /data volume, e.g. sqlite+pysqlite:////data/demo.db)
#   2) start FastAPI with uvicorn, single worker, on 0.0.0.0:8000
set -euo pipefail
cd /app/backend

echo "[entrypoint] demo bootstrap (idempotent): ${DATABASE_URL:-sqlite+pysqlite:///./demo.db}"
python -m app.demo.bootstrap

echo "[entrypoint] starting uvicorn on 0.0.0.0:8000"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000