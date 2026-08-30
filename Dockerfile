# Runtime image: the API only. The capture extra (Playwright) is deliberately
# not installed — a session is captured on a workstation and the resulting
# auth.json is mounted in, so nothing here ever launches a browser.
FROM python:3.12-slim

# curl_cffi ships manylinux wheels; curl is here for the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so a source change does not invalidate the layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    AUTH_FILE=/data/auth.json \
    CACHE_DIR=/data/cache/profiles

# Runs unprivileged; /data is a mounted volume holding the credential and cache.
RUN useradd --system --uid 1001 liapi \
    && mkdir -p /data \
    && chown -R liapi:liapi /data /app
USER liapi

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
