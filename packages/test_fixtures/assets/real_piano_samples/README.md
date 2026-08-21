# Real piano samples (DQ-2 benchmark suite extension)

The 20 `.mp3` files in this directory are single-note recordings, one per
distinct pitch needed to render `test_fixtures.real_piano`'s two
real-piano-timbre benchmark fixtures. They are a **copy** of the same
per-semitone piano samples already vendored (and already committed to
this repo) at
`apps/desktop/web/src/assets/soundfonts/piano/{name}.mp3` for the
desktop app's synthesized-playback feature — see
`docs/superpowers/SESSION-HANDOFF.md`'s "smplr" / "tonejs-instrument-*"
notes for that feature's own sourcing. Same origin, same MIT license,
just duplicated here so `packages/test_fixtures` (a separate uv workspace
package) doesn't need a cross-package path dependency on
`apps/desktop/web`.

## Why this directory exists

Detection-quality roadmap item 2 (docs/superpowers/SESSION-HANDOFF.md
"Detection-quality roadmap") found that the committed synthetic benchmark
suite's piano fixtures (`timbre="tone"`, an additive decaying-harmonic
model — see `test_fixtures.generate._decaying_harmonic`) are NOT
representative of real piano audio, and that comparing engines on that
synthetic timbre alone gave a misleading signal (see
`docs/benchmarks/2026-08-21-dq2.md`'s "Fixture-timbre investigation"
section for the full controlled A/B evidence). These real per-semitone
samples let `test_fixtures.real_piano.render_real_piano_clip` build a WAV
using genuine piano audio at every note (no pitch-shifting needed — a
real recording exists for every note actually used), without depending on
a network fetch or an external SoundFont.

## License

MIT (Tone.js `tonejs-instrument-piano-mp3` sample pack), matching the
license already recorded for the identical files at
`apps/desktop/web/src/assets/soundfonts/piano/`.
