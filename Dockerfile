FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend
COPY config /app/config
COPY scripts/container_entrypoint.py /app/scripts/container_entrypoint.py

EXPOSE 10000
CMD ["python", "/app/scripts/container_entrypoint.py"]
