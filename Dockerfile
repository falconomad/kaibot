# Kaibot engine: one image that runs the same on a laptop, Google Cloud, Oracle or any Docker host.
# Settings come from a mounted .env (KAIBOT_ENV_FILE) or plain environment variables. Paper trading only.
# Base image pinned by exact tag and digest (#122): bump it on purpose, like a package (docs/ops/hygiene.md).
FROM python:3.13.18-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /app
COPY requirements-engine.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements-engine.txt  # hash-locked export of uv.lock (#122)
COPY src ./src
COPY config ./config
COPY scripts/engine_entry.sh ./scripts/engine_entry.sh
# M20 9.3 (#727): scripts/build_image.sh builds from a rel-* tag and passes both. CODE_SHA is what every heartbeat and
# decision records ("<tag>@<short>"); BUILD_COMMIT is the full commit the entry compares with the host's promotion.
ARG CODE_SHA=unknown
ARG BUILD_COMMIT=unknown
ENV PYTHONPATH=/app/src KAIBOT_DATA_DIR=/data KAIBOT_CODE_SHA=${CODE_SHA} KAIBOT_BUILD_COMMIT=${BUILD_COMMIT} \
    PYTHONUNBUFFERED=1
LABEL org.opencontainers.image.source="https://github.com/falconomad/kaibot"
VOLUME /data
USER 1000:1000
# the engine starts only the build its host promoted when KAIBOT_REQUIRE_PROMOTED=1 (exit 6 otherwise); the watchdog
# (compose's `command`) runs python directly
CMD ["sh", "/app/scripts/engine_entry.sh"]
