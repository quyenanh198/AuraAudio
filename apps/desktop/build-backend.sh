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
#
# --add-data ...weights/piano/...:piano_weights (DQ-2, detection-quality
# roadmap item 2): the piano transcription model's checkpoint is NOT
# PyPI package data like basic-pitch's weights are -- it's fetched at
# build time by fetch_piano_weights.py (checksum-verified, see that
# script's module docstring for why it isn't vendored into git) into
# workers/transcription/weights/piano/. --add-data stages it into the
# bundle at <bundle_root>/piano_weights/, matching
# aura_worker.piano_engine._resolve_checkpoint_path's frozen-mode lookup
# (sys._MEIPASS / "piano_weights" / ...). Fetched here (not assumed
# already present) so a fresh clone's first build works unattended, same
# as this script already does for every other dependency.
#
# --add-data THIRD_PARTY_NOTICES.md:piano_weights: the checkpoint is
# CC-BY-4.0, which requires attribution to reach end users, not just this
# repo's docs -- staged into every packaged installer right next to the
# weights it documents, so a user who goes looking for it in the
# installed app finds it in the same place as the file it's about.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

uv run --package aura-worker python workers/transcription/scripts/fetch_piano_weights.py

uv run --package aura-api pyinstaller \
  --onedir \
  --name aura-backend \
  --distpath apps/desktop/dist \
  --workpath apps/desktop/build \
  --specpath apps/desktop \
  --collect-data basic_pitch \
  --add-data "${REPO_ROOT}/workers/transcription/weights/piano/piano_transcription_crnn.pth:piano_weights" \
  --add-data "${REPO_ROOT}/THIRD_PARTY_NOTICES.md:piano_weights" \
  --noconfirm \
  apps/desktop/run_backend.py

echo "Bundle built at apps/desktop/dist/aura-backend/aura-backend"

# Stage the bundle into src-tauri/resources/ so tauri.conf.json's
# bundle.resources map (Task 3: "resources/aura-backend/" -> "aura-backend/")
# has a source to copy from. tauri-build's build.rs copies this into
# target/<profile>/ on every `cargo build`/`tauri dev`/`tauri build`, not
# just full bundler runs, so this staging step must run before any of those
# — see apps/desktop/src-tauri/src/backend.rs's module doc comment for how
# that was confirmed (by reading tauri-build/tauri-utils source, not
# assumed).
STAGE_DIR="apps/desktop/src-tauri/resources/aura-backend"
rm -rf "$STAGE_DIR"
mkdir -p "$(dirname "$STAGE_DIR")"
cp -a apps/desktop/dist/aura-backend "$STAGE_DIR"

echo "Bundle staged at $STAGE_DIR/aura-backend"
