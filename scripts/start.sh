#!/bin/sh
# Container entrypoint: apply migrations, then serve.
#
# Alembic is idempotent and safe to run on every start; with a single API replica there is no
# migration race. When a worker container is added, it must NOT run migrations.
set -eu

echo "applying database migrations"
python -m alembic upgrade head

echo "starting api"
exec python -m uvicorn iptvcc.api:app --host 0.0.0.0 --port 8000 --proxy-headers
