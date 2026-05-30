# syntax=docker/dockerfile:1.7

# ---- builder ----------------------------------------------------------------
FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Install build deps for any wheels that need compiling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

# Install into a relocatable prefix so the runtime stage can copy it directly.
RUN pip install --prefix=/install .

# ---- runtime ----------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8765 \
    DB_PATH=/data/events.db \
    OAUTH_DB_PATH=/data/oauth.db

# Non-root user. uid/gid 10001 is a common convention for unprivileged service
# accounts and stays well clear of host user ranges.
RUN groupadd --system --gid 10001 app \
    && useradd  --system --uid 10001 --gid app --home /app --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data

COPY --from=builder /install /usr/local

WORKDIR /app
USER app

VOLUME ["/data"]
EXPOSE 8765

# TCP-port probe avoids needing the auth token for healthchecks.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket,os,sys; s=socket.socket(); s.settimeout(3); \
sys.exit(0) if s.connect_ex(('127.0.0.1', int(os.environ.get('PORT','8765'))))==0 else sys.exit(1)"

CMD ["python", "-m", "nyc_events.server"]
