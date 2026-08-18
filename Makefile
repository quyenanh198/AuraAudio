.PHONY: install test lint up down e2e-web

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

# Slow (~1-2 min), real basic-pitch/tensorflow transcription against a real
# spawned backend + Vite dev server — not part of `make test`. See
# apps/desktop/web/playwright.config.ts and e2e/edit-journey.spec.ts.
e2e-web:
	cd apps/desktop/web && npm run test:e2e

up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.yml down
