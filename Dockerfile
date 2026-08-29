# syntax=docker/dockerfile:1

# ── Stage 1: base image ──────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# Keeps Python from generating .pyc files and enables stdout/stderr flushing.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── Stage 2: dependencies ─────────────────────────────────────────────────────
FROM base AS deps

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Stage 3: runtime ──────────────────────────────────────────────────────────
FROM deps AS runtime

# Copy application source
COPY app/ ./app/
# Copy .env only if present at build time; prefer injecting secrets via env vars.
# COPY .env .env

# Expose the port uvicorn listens on.
EXPOSE 8000

# TODO: Replace with gunicorn + uvicorn workers for production:
#   CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", \
#        "--bind", "0.0.0.0:8000", "--workers", "4"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
