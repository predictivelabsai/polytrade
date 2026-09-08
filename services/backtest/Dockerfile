FROM ghcr.io/astral-sh/uv:0.11.11 AS uv

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY services/backtest/pyproject.toml services/backtest/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY services/backtest/polytrade_backtest ./polytrade_backtest
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10002 polytrade \
    && chown -R polytrade:polytrade /app

USER polytrade
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=3)"
CMD ["uvicorn", "polytrade_backtest.server:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1", "--no-access-log"]
