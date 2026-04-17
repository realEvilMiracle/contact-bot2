FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot.py contacts.json ./

RUN mkdir -p /app/img && useradd --create-home --uid 1000 bot && chown -R bot:bot /app
USER bot

CMD ["python", "bot.py"]
