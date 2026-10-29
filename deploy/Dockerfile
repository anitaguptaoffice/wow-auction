FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --group compact --no-install-project
COPY backend/ ./backend/
RUN mkdir -p /app/data
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/backend COMPACT_ENGINE=unified COMPACT_DATABASE=/data/market.duckdb COMPACT_MEMORY_LIMIT=256MB COMPACT_THREADS=2 ICON_CACHE_DIR=/tmp/icons
USER 65534:65534
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["uvicorn", "compact_market.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
