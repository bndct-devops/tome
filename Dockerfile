# Build frontend
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --legacy-peer-deps
COPY frontend/ ./
RUN npm run build

# Build the classic frontend: the look of the last 2.x release, frozen at its
# tag, for servers that set TOME_UI=classic. It talks to the same API.
FROM node:22-alpine AS frontend-classic-build
ARG CLASSIC_UI_TAG=v2.7.0
WORKDIR /src
ADD https://github.com/bndct-devops/tome/archive/refs/tags/${CLASSIC_UI_TAG}.tar.gz /src/classic.tar.gz
RUN tar -xzf classic.tar.gz --strip-components=1 && rm classic.tar.gz
WORKDIR /src/frontend
RUN npm ci --legacy-peer-deps && npm run build

# Runtime
FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends unrar-free gosu && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY backend/__init__.py ./backend/
RUN pip install --no-cache-dir .

COPY backend/ ./backend/
COPY alembic.ini ./
COPY alembic/ ./alembic/
# What's-New panel reads release notes from the changelog at runtime
COPY CHANGELOG.md ./
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
COPY --from=frontend-classic-build /src/frontend/dist ./frontend/dist-classic

ENV TOME_DATA_DIR=/data \
    TOME_LIBRARY_DIR=/books \
    TOME_INCOMING_DIR=/bindery

RUN useradd -m -u 1000 tome \
    && mkdir -p /data /books /bindery \
    && chown tome:tome /data /books /bindery
COPY docker/entrypoint.sh /usr/local/bin/tome-entrypoint
RUN chmod +x /usr/local/bin/tome-entrypoint

# The entrypoint starts as root only to remap the "tome" user to PUID/PGID
# (default 1000:1000) and fix /data ownership, then drops privileges via gosu.
# Passing `user:` in compose still works: the entrypoint just execs as that user.

VOLUME ["/data", "/books", "/bindery"]
EXPOSE 8080

ENTRYPOINT ["tome-entrypoint"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
