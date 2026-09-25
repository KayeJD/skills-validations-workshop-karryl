# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Optimize Python performance and logging inside the container
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8080

# Set the working directory in the container
WORKDIR /app

# Install any needed packages specified in requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app
COPY . .

# Create and run as a non-privileged system user for cloud security hardening
RUN useradd -u 1001 -m appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Chainlit applications listen on port 8000 by default.
# Cloud Run services listen on the port specified by the PORT environment variable (default 8080).
# We run Chainlit in headless mode and bind to the dynamic PORT environment variable.
CMD ["sh", "-c", "chainlit run chainlit_app.py --host 0.0.0.0 --port ${PORT} --headless"]