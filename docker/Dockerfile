# --- STAGE 1: Builder ---
FROM python:3.13-slim AS builder

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./

RUN uv venv
RUN uv sync


# --- STAGE 2: Final Image ---
FROM python:3.13-slim

WORKDIR /app

RUN useradd --create-home appuser

COPY --from=builder /app/.venv ./.venv

COPY . .

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# 容器内一般不使用本地 dev 证书；需要 TLS 时请前置反向代理
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
