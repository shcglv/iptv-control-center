# syntax=docker/dockerfile:1.7
#
# IPTV Control Center — API + web UI.
#
# Reproducibility rules (see docs/ARCHITECTURE.md §10):
#   * both base images are pinned by digest, not by tag,
#   * dependencies install from a lockfile with --frozen-lockfile,
#   * nothing is downloaded at container start,
#   * the process runs as UID 10001 with no shell-level privileges.
#
# The image contains no video stack on purpose: this service never touches media bytes.

ARG PYTHON_IMAGE=python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b
ARG NODE_IMAGE=node:24-alpine@sha256:116b800c3754b440ee0aa3ed2f20f5bf3d99a554bfd9adeea09ea8017f6e85f5
# ffprobe is only needed for IPTV_PROBE_MODE=active; the default image stays small.
ARG INSTALL_FFMPEG=false

# ---------------------------------------------------------------- web build
FROM ${NODE_IMAGE} AS web

WORKDIR /web
# pnpm must never prompt: the build has no TTY.
ENV CI=true
RUN npm install -g pnpm@11.28.5
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
# Vite writes to ../src/iptvcc/static, i.e. /src/iptvcc/static inside this stage.
RUN pnpm build

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

ARG INSTALL_FFMPEG
RUN if [ "$INSTALL_FFMPEG" = "true" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends ffmpeg \
      && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY --from=web /src/iptvcc/static ./src/iptvcc/static
COPY migrations ./migrations
COPY scripts ./scripts
RUN pip install --no-cache-dir . \
 && rm -rf /app/src

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

CMD ["sh", "/app/scripts/start.sh"]
