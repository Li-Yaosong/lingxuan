# ── Stage 1: Build lingxuan wheel ──────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

# Install build dependencies
COPY pyproject.toml ./
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini ./

RUN pip install --no-cache-dir --upgrade pip build && \
    python -m build --wheel

# ── Stage 2: Runtime ───────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

LABEL maintainer="lingxuan"
LABEL description="灵轩 — 基于 NapCatQQ 的 AI QQ 机器人"

# System dependencies required by NapCat + LinuxQQ:
#   - xvfb: virtual framebuffer for headless QQ
#   - g++: compile napcat-linux-launcher (libnapcat_launcher.so)
#   - curl, unzip: download NapCat / LinuxQQ
#   - libgbm1, libnss3, libatk-bridge2.0-0, etc.: LinuxQQ runtime deps
#   - fonts-noto-cjk: CJK font rendering for QQ
#   - dbus: D-Bus communication needed by QQ
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Shanghai
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    xvfb \
    g++ \
    curl \
    unzip \
    libgbm1 \
    libnss3 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxfixes3 \
    libx11-xcb1 \
    libasound2 \
    libgtk-3-0 \
    libnotify4 \
    libxss1 \
    libxtst6 \
    xdg-utils \
    libsecret-1-0 \
    libpango-1.0-0 \
    libcairo2 \
    fonts-noto-cjk \
    dbus \
    && rm -rf /var/lib/apt/lists/*

# Install lingxuan from the built wheel
COPY --from=builder /build/dist/*.whl /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

# Create data directories
RUN mkdir -p /app/data/napcat /app/data/qq /app/data/memory

WORKDIR /app

# Copy alembic migrations (needed for auto-migrate on startup)
COPY alembic/ alembic/
COPY alembic.ini ./

# Environment defaults (can be overridden via docker-compose / docker run)
ENV DRIVER=~fastapi \
    OPENAI_API_KEY="" \
    OPENAI_BASE_URL="https://api.deepseek.com/v1" \
    OPENAI_MODEL="deepseek-chat" \
    BOT_NAME="灵轩" \
    BOT_PERSONA="" \
    BOT_ADMINS="" \
    MEMORY_WINDOW=20 \
    GROUP_OBSERVE_WINDOW=20 \
    GROUP_OBSERVE_DELAY=1.5 \
    GROUP_OBSERVE_COOLDOWN=30 \
    GROUP_BURST_MERGE_WINDOW=10 \
    GROUP_FOLLOWUP_WINDOW=60 \
    GROUP_CHAT_CONTEXT=6 \
    GROUP_CHAT_MAX_TOKENS=512 \
    ENABLE_STREAM_CHUNK=true \
    GROUP_MSG_CHUNK_MAX=35 \
    GROUP_MSG_CHUNK_MIN=6 \
    GROUP_MSG_CHUNK_LIMIT=6 \
    GROUP_CHUNK_DELAY_MIN=0.4 \
    GROUP_CHUNK_DELAY_MAX=1.2 \
    ENABLE_PRIVATE_CHAT=true \
    ENABLE_GROUP_CHAT=true \
    ENABLE_GROUP_OBSERVE=true \
    ENABLE_MEMORY_SUMMARY=true \
    ENABLE_USER_MEMORY=true \
    USER_MEMORY_BURST_MERGE=3.0 \
    USER_MEMORY_MAX_FACTS=30 \
    ENABLE_USER_COGNITION_REFINE=true \
    USER_COGNITION_REFINE_INTERVAL=5 \
    USER_COGNITION_REFINE_DELAY=2.0 \
    USER_COGNITION_MAX_CHARS=150 \
    DB_URL="sqlite+aiosqlite:///./data/lingxuan.db" \
    DATA_ROOT="./data" \
    AUTO_MIGRATE=true \
    ADMIN_HOST="0.0.0.0" \
    ADMIN_PORT=8081 \
    SECRET_KEY="" \
    JWT_ACCESS_TTL=900 \
    JWT_REFRESH_TTL=604800 \
    NAPCAT_DIR="./data/napcat" \
    NAPCAT_QQ_DIR="./data/qq" \
    NAPCAT_WS_URL="ws://127.0.0.1:8080/onebot/v11/ws" \
    NAPCAT_AUTO_START=true \
    NAPCAT_QUICK_ACCOUNT="" \
    NAPCAT_NO_SANDBOX=true \
    NAPCAT_USE_XVFB=true

# NoneBot2 (FastAPI) listens on 8080; Admin panel on 8081
EXPOSE 8080 8081

# Persistent data volume
VOLUME ["/app/data"]

# Health check: verify the NoneBot FastAPI server is responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://127.0.0.1:8080/onebot/v11/ws > /dev/null || curl -sf http://127.0.0.1:8081/ > /dev/null || exit 1

# Entry point: start lingxuan (which auto-starts NapCat when NAPCAT_AUTO_START=true)
CMD ["lingxuan", "run"]
