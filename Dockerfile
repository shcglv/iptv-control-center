# syntax=docker/dockerfile:1.7
#
# IPTV Control Center — API + delivery.
#
# Reproducibility rules (see docs/ARCHITECTURE.md §10):
#   * the runtime base image is pinned by digest, not by tag,
#   * dependencies install with --no-cache-dir into a non-root user's environment,
#   * nothing is downloaded at container start,
#   * the process runs as UID 10001 with no shell-level privileges.
#
# The image contains no video stack on purpose: this service never touches media bytes.

ARG PYTHON_IMAGE=python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Dependency layer first: rebuilds only when pyproject changes.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
 && rm -rf /app/src

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

CMD ["python", "-m", "uvicorn", "iptvcc.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
