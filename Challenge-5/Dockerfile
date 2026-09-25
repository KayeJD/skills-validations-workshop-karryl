# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# System deps kept minimal; add build-essential etc. here if a dependency needs compiling.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY snow-app.py .

ENV PORT=8080
EXPOSE 8080

# Cloud Run injects PORT; gunicorn binds to it. Single worker/thread config is a
# reasonable default for Cloud Run's per-instance concurrency model - tune as needed.
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 8 --timeout 0 snow-app:app
