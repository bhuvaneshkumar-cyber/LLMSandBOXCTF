# syntax=docker/dockerfile:1

# 1. Build the frontend (TypeScript + Vite).
FROM node:24-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2. Runtime: the API plus the built frontend, served from one origin, as an unprivileged user.
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ app/
COPY --from=frontend /frontend/dist frontend/dist
# /app writable so the default SQLite file works in a plain `docker run`; Render uses Postgres.
RUN useradd --system vault && chown vault /app
USER vault
EXPOSE 8000
# One worker: the app is I/O-bound and per-user limits live in this process. Render injects $PORT.
# exec makes uvicorn PID 1, so it receives SIGTERM and shuts down cleanly on redeploys.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
