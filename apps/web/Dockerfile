FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN pip install --no-cache-dir uv==0.8.22
COPY packages/contracts /app/packages/contracts
COPY apps/web /app/apps/web
RUN uv sync --project /app/apps/web --frozen --no-dev

WORKDIR /app/apps/web
EXPOSE 8080
CMD ["uvicorn", "polytrade_web.app:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
