.PHONY: install test lint up down

install:
	uv sync --all-packages

test:
	uv run --package score-schema pytest packages/score_schema/tests
	uv run --package musicxml pytest packages/musicxml/tests
	uv run --package test-fixtures pytest packages/test_fixtures/tests
	uv run --package aura-api pytest apps/api/tests
	uv run --package aura-worker pytest workers/transcription/tests
	uv run --package aura-api pytest apps/desktop/tests

lint:
	uv run ruff check .

up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.yml down
