# AuraAudio

Offline desktop app that turns a guitar or piano recording into an
**editable score** — standard notation plus guitar tablature or a piano
grand staff — with synchronized playback, semantic editing, and
MusicXML/MIDI export. Everything runs locally on your machine.

**Download (v1.1.0):**
[Linux `.deb`](https://github.com/quyenanh198/AuraAudio/releases/download/v1.1.0/AuraAudio_1.1.0_amd64.deb) ·
[macOS `.dmg` (Apple Silicon)](https://github.com/quyenanh198/AuraAudio/releases/download/v1.1.0/AuraAudio_1.1.0_aarch64.dmg) ·
[Windows `.msi`](https://github.com/quyenanh198/AuraAudio/releases/download/v1.1.0/AuraAudio_1.1.0_x64_en-US.msi) —
all releases on the [releases page](https://github.com/quyenanh198/AuraAudio/releases).

## What it does

1. **Get audio in** — record from the microphone, import an audio file,
   or paste a YouTube link (requires optional [yt-dlp](https://github.com/yt-dlp/yt-dlp)).
2. **Transcribe** — a local ML pipeline (basic-pitch + librosa) detects
   notes, tempo, key, and meter, then assigns guitar string/fret or
   piano left/right hand via constrained optimization.
3. **Read & play** — the score renders as notation + TAB (guitar) or a
   grand staff (piano), with a synchronized playback cursor over the
   original recording or a synthesized rendition.
4. **Edit** — click any note and change what it *means*: pitch, timing,
   duration, fingering, hand, or the piece's key/tempo/meter. Add,
   delete, lock notes. Full undo/redo. Edits re-derive fingering and
   exports automatically while you keep working.
5. **Export** — MusicXML (opens in MuseScore, Guitar Pro, etc.) and
   MIDI, saved wherever you choose.

AuraAudio is an **assisted transcription tool**: automatic polyphonic
transcription is inherently imperfect, so every inferred fact carries a
confidence indicator and everything is correctable in place.

- **Meters:** 10 supported for editing/export (2/4, 3/4, 4/4, 5/4, 2/2,
  3/8, 6/8, 7/8, 9/8, 12/8); 4 auto-detected (4/4, 3/4, 6/8, 2/4).
  Auto-detection of 6/8 vs 3/4 (and 2/4 vs its neighbors) is
  best-effort — a low confidence indicator means "check this"; fixing it
  is one click in the inspector.
- **Instruments:** one prominent guitar or piano part per project.

## Runtime requirements

| Dependency | Linux (.deb) | macOS / Windows |
|---|---|---|
| **ffmpeg** (required) | installed automatically by apt (`Depends: ffmpeg`) | the app detects it at startup and shows the install command (`brew install ffmpeg` / `winget install Gyan.FFmpeg`) |
| **yt-dlp** (optional, YouTube import only) | `sudo apt install yt-dlp` | `brew install yt-dlp` / `winget install yt-dlp` |

Everything else is bundled. The app talks to nothing outside your
machine — the single exception is YouTube import, which downloads audio
from the link you paste (make sure you have the rights to the content
you import; downloading may be subject to YouTube's Terms of Service).

## How it works

Three flows, drawn in [`docs/flow-map.html`](docs/flow-map.html)
(self-contained, open in a browser):

- **Transcription pipeline** — a Tauri shell spawns a bundled FastAPI
  backend on `127.0.0.1:8317`; jobs run a staged worker
  (probe → normalize → inference → structure → quantize → assign →
  export) with per-stage artifact caching.
- **Semantic editing** — pure edit operations produce new score
  revisions with a movable head pointer (undo/redo); a coalesced
  background job re-runs fingering around locked notes and refreshes
  exports in place.
- **Desktop shell & dependency checks** — startup health checks, an
  exact-origin CORS allowlist, and guided-install banners for missing
  dependencies.

## Development

Monorepo layout:

```text
apps/
  api/                 # FastAPI backend (projects, jobs, edits, exports)
  desktop/             # Tauri v2 shell + Svelte 5 web UI + PyInstaller bundling
workers/
  transcription/       # stage runner: ffmpeg, basic-pitch, librosa, DP assigners
packages/
  score_schema/        # canonical score JSON: schema, validation, edit ops, meters
  musicxml/            # MusicXML export (music21)
  test_fixtures/       # synthesized audio fixtures
docs/                  # flow map, specs, plans, session handoff
```

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 20+, Rust stable,
ffmpeg on PATH. Python is pinned to 3.11 (tensorflow wheels).

```bash
uv sync --all-packages --all-extras
cd apps/desktop/web && npm ci && cd -

make test          # all Python suites (386 tests)
cd apps/desktop/web && npx vitest run   # frontend (181 tests)
npm run test:e2e   # Playwright journey (boots a real backend)

cd apps/desktop && cargo tauri dev      # run the app
```

CI runs python/web/rust/e2e jobs on every push
(`.github/workflows/ci.yml`).

### Releasing

Bump `version` in `apps/desktop/src-tauri/tauri.conf.json` (and
`src-tauri/Cargo.toml`), merge to `main`, then either push a `vX.Y.Z`
tag or run the **Release** workflow manually with the `release_tag`
input. The workflow builds the PyInstaller backend and produces the
`.deb`, `.dmg`, and `.msi`, then attaches all three to a GitHub Release
for the tag (`.github/workflows/release.yml`).

## Documentation

- [`docs/flow-map.html`](docs/flow-map.html) — visual flow diagrams.
- [`docs/superpowers/SESSION-HANDOFF.md`](docs/superpowers/SESSION-HANDOFF.md)
  — living engineering handoff: environment gotchas, verified facts,
  release history, known limitations.
- [`docs/superpowers/specs/`](docs/superpowers/specs/) and
  [`docs/superpowers/plans/`](docs/superpowers/plans/) — design specs
  and implementation plans for each delivered sub-project.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the original (pre-pivot)
  cloud-product planning document, kept for historical context; the
  shipped product is the offline desktop app described here.

## Known limitations

- Transcription quality degrades on dense mixes, vocals, and heavy
  effects; solo instrumental recordings work best.
- 6/8-vs-3/4 and 2/4 meter auto-detection is unreliable on real
  material — correct it in the inspector when the confidence dots are
  low.
- The Windows build passes CI (including an ML-backend smoke test) but
  has not yet been exercised end-to-end on physical Windows hardware.
- One instrument part per project; no measure split/merge or
  multi-select editing yet.
