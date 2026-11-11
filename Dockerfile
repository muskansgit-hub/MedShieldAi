FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MEDSHIELD_DB=/data/medshield_audit.db PORT=8000

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
# Train the risk classifier at build time so the first request is fast,
# and create a writable folder for the audit log.
RUN python -m medshield.risk > /dev/null \
 && mkdir -p /data && chown -R app:app /data /app/models /app/results

USER app
EXPOSE 8000
# Hosts like Render, Railway and Cloud Run inject $PORT.
CMD uvicorn medshield.api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*"
