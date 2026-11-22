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
COPY scripts ./scripts
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

# Bake ONNX weights into the image (not committed in git). CLIP is optional for brand scoring.
ENV MODEL_CACHE_DIR=/app/models
COPY docker/bake_models.py /tmp/bake_models.py
RUN mkdir -p /app/models && python /tmp/bake_models.py && rm /tmp/bake_models.py

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
# Persist /data via a dashboard-mounted disk if needed; ephemeral is fine for the prototype.
# App readiness is checked via railway.toml healthcheckPath (/api/health).

CMD ["/app/entrypoint.sh"]
