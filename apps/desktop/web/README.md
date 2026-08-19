# AuraAudio desktop — web frontend

Svelte 5 + TypeScript + Vite frontend for the AuraAudio Tauri desktop app
(guitar/piano transcription: upload audio, review the transcription, edit
notation, export MusicXML/MIDI). Runs inside the Tauri webview in
production; talks to the PyInstaller-bundled FastAPI backend
(`apps/desktop/src-tauri/src/backend.rs`) over HTTP on a fixed local port.

## Develop

```bash
npm install
npm run dev      # Vite dev server, used by `cargo tauri dev` via beforeDevCommand
```

## Build

```bash
npm run build    # type-checks via svelte-check/tsc, then bundles to dist/
                  # (frontendDist for `cargo tauri build`)
npm run check     # svelte-check + tsc, no emit
```

## Test

```bash
npm test          # vitest unit/store suite
npm run test:e2e  # Playwright: transcribe -> edit -> undo -> export journey,
                   # against a real spawned backend + Vite dev server (slow,
                   # not part of `npm test`)
```

See `apps/desktop/src-tauri/` for the Rust/Tauri shell and
`docs/superpowers/SESSION-HANDOFF.md` for repo-wide context.
