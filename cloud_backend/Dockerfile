FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

COPY cloud_backend/requirements.txt /app/cloud_backend/requirements.txt
RUN python -m pip install --upgrade pip && pip install --no-cache-dir -r /app/cloud_backend/requirements.txt

COPY plex_inventory_app /app/plex_inventory_app
COPY cloud_backend /app/cloud_backend

CMD exec uvicorn cloud_backend.main:app --host 0.0.0.0 --port ${PORT}
