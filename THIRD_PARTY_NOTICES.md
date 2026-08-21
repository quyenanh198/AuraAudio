# Third-Party Notices

AuraAudio (MIT-licensed, see `LICENSE`) bundles model weights and media
assets from third parties. This file records their license terms and
attribution. It is staged into every packaged installer (`.deb`/`.dmg`/
`.msi`) by `apps/desktop/build-backend.sh`, alongside the piano
transcription checkpoint it documents, so the notice ships with the app a
user actually receives — not just this source repo.

## Piano transcription model checkpoint (CC-BY-4.0)

- **What**: `piano_transcription_crnn.pth`, the pretrained checkpoint for
  the piano transcription model used for piano projects (guitar projects
  use basic-pitch, covered separately below).
- **Citation**: Qiuqiang Kong, Bochen Li, Xuchen Song, Yuan Wan, Yuxuan
  Wang, "High-resolution Piano Transcription with Pedals by Regressing
  Onsets and Offsets Times", 2020.
- **Source**: Zenodo record 4034264 —
  <https://zenodo.org/records/4034264> (DOI: `10.5281/zenodo.4034264`).
- **License**: **CC-BY-4.0** (Creative Commons Attribution 4.0
  International) — per the Zenodo record's own published license
  metadata. Full text: <https://creativecommons.org/licenses/by/4.0/>.
- **Inference code**: the `piano_transcription_inference` PyPI package
  (code, not the checkpoint) is MIT-licensed per its own PyPI classifier.
  Source: <https://github.com/bytedance/piano_transcription> /
  <https://github.com/qiuqiangkong/piano_transcription_inference>.
- Full candidate assessment, resolution evidence, and benchmark:
  `docs/benchmarks/2026-08-21-dq2.md`.

## Piano and guitar sample audio (MIT)

- **What**: per-semitone piano and guitar note recordings used for two
  purposes in this app: (1) the desktop app's synthesized-playback
  feature (`apps/desktop/web/src/assets/soundfonts/{piano,guitar}/`), and
  (2) the transcription benchmark suite's real-piano-timbre fixtures
  (`packages/test_fixtures/assets/real_piano_samples/` — dev/test-only,
  not shipped in installers, listed here for completeness).
- **Source**: Tone.js `tonejs-instrument-piano-mp3` / `tonejs-instrument-
  guitar-mp3` sample packs.
- **License**: MIT.

## basic-pitch model weights (Apache-2.0)

- **What**: the ICASSP 2022 model weights basic-pitch bundles as its own
  PyPI package data, used for guitar transcription (and any instrument
  other than piano).
- **License**: Apache License 2.0, per basic-pitch's own project license
  (Spotify).
- Bundled automatically as part of the `basic-pitch` PyPI package's own
  data files (`apps/desktop/build-backend.sh`'s `--collect-data
  basic_pitch`) — not separately vendored by this repo.

## TensorFlow and other bundled dependencies

TensorFlow, PyTorch, and this app's other Python/Rust/JS dependencies
carry their own upstream licenses (TensorFlow: Apache-2.0; PyTorch:
BSD-3-Clause), not individually re-stated here. TensorFlow ships its own
machine-generated `THIRD_PARTY_NOTICES.txt` covering its full transitive
dependency tree, bundled automatically inside every packaged installer at
`tensorflow/THIRD_PARTY_NOTICES.txt` relative to the installed backend
(e.g. `aura-backend/_internal/tensorflow/THIRD_PARTY_NOTICES.txt` in the
Linux `.deb`'s installed layout) — present in every build, not something
this file needs to duplicate.

## Follow-up (tracked, not done here)

An in-app "Third-Party Notices" screen (surfacing this file's content
inside the app itself, not just on disk) is a reasonable follow-up but is
not implemented — see `docs/superpowers/SESSION-HANDOFF.md`'s
Detection-quality roadmap item 2 for the tracked open item.
