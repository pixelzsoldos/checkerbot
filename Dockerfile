FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl wget gnupg ca-certificates \
    && pip install -r requirements.txt \
    && playwright install --with-deps chromium \
    && apt-get purge -y --auto-remove curl wget gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY . .

CMD ["python", "bot.py"]

