FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.8.22
COPY packages/contracts /app/packages/contracts
COPY apps/gateway /app/apps/gateway
RUN uv sync --project /app/apps/gateway --frozen --no-dev

WORKDIR /app/apps/gateway
EXPOSE 4000
CMD ["uvicorn", "polytrade_gateway.main:app", "--host", "0.0.0.0", "--port", "4000", "--proxy-headers"]
