FROM ghcr.io/astral-sh/uv:0.10.9 AS uv

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY scripts ./scripts
RUN uv sync --frozen --no-dev \
    && playwright install --with-deps chromium \
    && rm -rf /root/.cache/uv

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /data/media /data/weibo-profile \
    && chown -R app:app /app /data /ms-playwright

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
