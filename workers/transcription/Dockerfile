FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 appuser
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . /app

RUN uv sync --package aura-worker --no-dev

USER appuser
CMD uv run --package aura-worker rq worker transcription --url $REDIS_URL
