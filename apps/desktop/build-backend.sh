#!/usr/bin/env bash
# Builds the standalone PyInstaller bundle of the AuraAudio backend.
#
# Produces apps/desktop/dist/aura-backend/ — a --onedir bundle whose
# aura-backend executable starts the FastAPI app (apps/api) on the fixed
# port 8317 with no `uv`/Python environment required on PATH at runtime.
#
# --collect-data basic_pitch is required, not optional: basic-pitch ships
# its trained model weights (SavedModel .pb + variables) as package data
# files, which PyInstaller's default static-import analysis does not pick
# up (it only follows Python imports, not data-file access). Without this
# flag the bundle boots and serves /healthz fine, but any real transcription
# request fails at model-load time. See task-1-report.md for how this was
# found (a real basic-pitch inference smoke test, not just healthz).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

uv run --package aura-api pyinstaller \
  --onedir \
  --name aura-backend \
  --distpath apps/desktop/dist \
  --workpath apps/desktop/build \
  --specpath apps/desktop \
  --collect-data basic_pitch \
  --noconfirm \
  apps/desktop/run_backend.py

echo "Bundle built at apps/desktop/dist/aura-backend/aura-backend"
