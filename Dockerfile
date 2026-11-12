# Kaibot engine: one image that runs the same on a laptop, Google Cloud, Oracle or any Docker host.
# Settings come from a mounted .env (KAIBOT_ENV_FILE) or plain environment variables. Paper trading only.
FROM python:3.13-slim
WORKDIR /app
COPY requirements-engine.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements-engine.txt  # hash-locked export of uv.lock (#122)
COPY src ./src
COPY config ./config
ARG CODE_SHA=unknown
ENV PYTHONPATH=/app/src KAIBOT_DATA_DIR=/data KAIBOT_CODE_SHA=${CODE_SHA} PYTHONUNBUFFERED=1
VOLUME /data
USER 1000:1000
CMD ["python", "-m", "kaibot.engine"]
