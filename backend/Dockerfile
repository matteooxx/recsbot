FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_MODE=local \
    STORAGE_BACKEND=sqlite \
    MODEL_BACKEND=deterministic \
    AUTH_BACKEND=environment \
    LOCAL_DB_PATH=/data/recsbot.db \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY requirements-local.txt .
RUN pip install --no-cache-dir -r requirements-local.txt
COPY src/ .

RUN addgroup --system recsbot && adduser --system --ingroup recsbot recsbot \
    && mkdir -p /data && chown recsbot:recsbot /data
USER recsbot

EXPOSE 8000
VOLUME ["/data"]
CMD ["sh", "run.sh"]
