# syntax=docker/dockerfile:1

FROM python:3.11-slim

# Keeps Python from generating .pyc files and enables stdout/stderr flushing
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy application source code and frontend
COPY app/ ./app/
COPY frontend/ ./frontend/

# Expose the port uvicorn listens on
EXPOSE 8000

# Run Uvicorn directly with a single worker. 
# Render's free tier has 512MB RAM; multiple Gunicorn workers will cause OOM crashes.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
