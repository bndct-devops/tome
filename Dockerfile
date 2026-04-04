# Build frontend
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --legacy-peer-deps
COPY frontend/ ./
RUN npm run build

# Runtime
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends unrar-free && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY backend/__init__.py ./backend/
RUN pip install --no-cache-dir .

COPY backend/ ./backend/
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

ENV TOME_DATA_DIR=/data \
    TOME_LIBRARY_DIR=/books \
    TOME_INCOMING_DIR=/bindery

RUN useradd -m -u 1000 tome \
    && mkdir -p /data /books /bindery \
    && chown tome:tome /data /books /bindery
USER tome

VOLUME ["/data", "/books", "/bindery"]
EXPOSE 8080

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
