FROM python:3.11-slim AS base

RUN useradd --create-home --uid 1000 appuser
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . /app

RUN uv sync --package aura-api --no-dev

USER appuser
EXPOSE 8000
CMD ["uv", "run", "--package", "aura-api", "uvicorn", "aura_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
