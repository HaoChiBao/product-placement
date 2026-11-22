# Single Railway service: Next.js UI + Python package + ffmpeg + ONNX models (CPU).
# Listens on $PORT. Data under DATA_DIR (ephemeral /data by default).

# ---- Next.js build ----
FROM node:20-bookworm-slim AS web-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- Runtime ----
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      curl \
      ca-certificates \
      libglib2.0-0 \
      libgl1 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

# Bake ONNX weights into the image (not committed in git).
ENV MODEL_CACHE_DIR=/app/models
RUN mkdir -p /app/models \
    && python -c "from pathlib import Path; from product_placement.models_store import ensure_yunet, ensure_yolox; \
p=Path('/app/models'); ensure_yunet(p); ensure_yolox(p); print('models ok', list(p.iterdir()))"

# Next standalone output (+ static/public assets Next expects beside server.js)
COPY --from=web-build /app/web/.next/standalone /app/web-standalone
COPY --from=web-build /app/web/.next/static /app/web-standalone/.next/static
COPY --from=web-build /app/web/public /app/web-standalone/public

# Sample assets for the demo button (small, licensed).
COPY assets ./assets

COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /data

ENV REPO_ROOT=/app \
    DATA_DIR=/data \
    PYTHON_BIN=python3 \
    PYTHONPATH=/app/src \
    MODEL_CACHE_DIR=/app/models \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    MOCK_HIGGSFIELD=0

EXPOSE 3000
# Do not use Dockerfile VOLUME — Railway rejects it. Mount a Railway Volume at
# /data in the dashboard if you want persistence; otherwise /data is ephemeral.

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

CMD ["/app/entrypoint.sh"]
