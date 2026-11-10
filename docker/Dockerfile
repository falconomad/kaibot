FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY apps/api ./apps/api
COPY config ./config
RUN pip install --no-cache-dir -e .
ENV PYTHONUNBUFFERED=1
