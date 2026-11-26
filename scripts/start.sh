#!/bin/sh
# Container entrypoint: wait for the database, apply migrations, then serve.
#
# On a first deploy the application container can start before PostgreSQL is accepting connections —
# Coolify starts resources in parallel, and a database container needs a few seconds to initialise.
# Without a wait, alembic fails, the container exits, and the deploy looks broken until the platform
# restarts it. The wait is bounded so a genuinely missing database still fails the deploy quickly.
set -eu

echo "waiting for the database"
python - <<'PY'
import os
import sys
import time

from iptvcc.config import get_settings

settings = get_settings()
url = settings.database_url
if not url.startswith("postgres"):
    print("sqlite: no wait needed")
    sys.exit(0)

import psycopg

deadline = time.time() + float(os.environ.get("IPTV_DB_WAIT_SECONDS", "120"))
attempt = 0
while True:
    attempt += 1
    try:
        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1), connect_timeout=5):
            print(f"database reachable after {attempt} attempt(s)")
            break
    except Exception as exc:  # noqa: BLE001 - any failure means "not yet"
        if time.time() >= deadline:
            print(f"database unreachable after {attempt} attempts: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
PY

echo "applying database migrations"
python -m alembic upgrade head

echo "starting api"
exec python -m uvicorn iptvcc.api:app --host 0.0.0.0 --port 8000 --proxy-headers
