FROM python:3.11-slim

# Optimize Python performance inside the container
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8080

WORKDIR /app

# Leverage Docker layer caching for dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py parse_transcript.py ./

# Create and run as a non-privileged system user for cloud security hardening
RUN useradd -u 1001 -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Run using Gunicorn with dynamic port binding for serverless scaling
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "0", "main:app"]