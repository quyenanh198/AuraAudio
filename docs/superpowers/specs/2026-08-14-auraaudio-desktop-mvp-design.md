# AuraAudio Desktop MVP Design

**Status:** Approved  
**Date:** 2026-08-14  
**Target:** Private desktop beta for Windows 10/11 and macOS  
**Primary user:** A musician who wants an editable solo-piano transcription draft from a local recording

## 1. Product definition

AuraAudio is a fully local desktop application that converts a user-owned audio or video file into an editable solo-piano score. The application creates an assisted transcription draft, lets the user correct timing and pitches while listening to synchronized audio, and exports MusicXML, MIDI, and PDF.

The beta does not promise publication-ready automatic transcription. It provides a focused correction workflow so the final user-reviewed export can become publication-ready in an external notation application or through AuraAudio's supported edits.

## 2. Confirmed constraints

- The application runs locally and does not require a network connection after installation.
- Supported platforms are Windows 10/11 and macOS.
- Processing must work on a CPU-only computer with 16 GB RAM.
- Input length is limited to 5 minutes.
- The first release supports one prominent solo-piano part.
- The private beta is intended for 20–50 testers.
- The initial infrastructure budget is under $100 per month; normal operation requires no hosted backend.
- Source media never leaves the user's computer.
- The focused editor supports pitch, onset, duration, note creation/deletion, tempo, and measure-boundary corrections.
- The first release exports MusicXML, MIDI, and PDF.

## 3. Goals

1. Prove that a CPU-capable transcription model can create a useful editable piano draft.
2. Import supported audio/video safely without modifying the source file.
3. Produce a deterministic, resumable local transcription pipeline.
4. Keep notation synchronized with the original performance.
5. Let users correct common transcription errors without learning a full engraving tool.
6. Save projects reliably and recover after application or worker crashes.
7. Produce interoperable MusicXML, faithful MIDI, and readable PDF.
8. Package and test native installers for Windows and macOS.

## 4. Non-goals

The MVP does not include:

- guitar tablature or guitar transcription;
- full-band source separation;
- live microphone or streaming input;
- cloud processing, user accounts, collaboration, or synchronization;
- mobile applications;
- lyrics, dynamics, articulations, pedal markings, or fingering;
- a complete notation engraving or page-layout editor;
- automatic application updates;
- telemetry;
- GPU-specific optimization;
- publication-ready automatic output without user review.

## 5. Architecture

AuraAudio uses PySide6 for the desktop shell and Python for application, audio, inference, notation, and export logic. Keeping one primary runtime reduces cross-platform packaging and inter-process integration work for an AI-assisted solo developer.

Long-running audio and model work runs in a separate worker process. The UI process owns windows, project state, command history, and worker supervision. Process isolation prevents CPU-heavy inference from freezing the UI and allows the application to cancel or recover from a failed job.

Major layers:

- **UI:** PySide6 windows, dialogs, playback controls, waveform, piano-roll editor, and notation preview.
- **Application:** use-case coordinators for import, transcription, editing, project lifecycle, and export.
- **Domain:** immutable score revisions, performed-time events, notated-time events, tempo map, measures, and edit commands.
- **Infrastructure:** SQLite repositories, filesystem project storage, FFmpeg adapter, worker IPC, logging, and atomic file operations.
- **Transcription:** replaceable model adapter, windowing, event decoding, confidence values, and benchmark runner.
- **Notation:** beat analysis, quantization, voice assignment, ties, measure construction, and MusicXML generation.
- **Export:** MIDI and PDF generation plus export validation.

## 6. Repository boundaries

```text
apps/
  desktop/
    auraaudio/
      ui/
      application/
      domain/
      infrastructure/
      transcription/
      notation/
      export/
      resources/
    tests/
      unit/
      integration/
      end_to_end/
      fixtures/
benchmarks/
  manifests/
  references/
  reports/
docs/
  superpowers/
    specs/
    plans/
  decisions/
scripts/
  package/
  benchmark/
```

Each module has one responsibility. UI widgets call application services; they do not invoke FFmpeg, SQLite, or model APIs directly. Infrastructure implementations satisfy interfaces owned by the application or domain layer.

## 7. Core interfaces

The implementation plan must preserve these boundaries:

```python
@dataclass(frozen=True)
class PerformedNote:
    id: str
    pitch: int
    onset_seconds: float
    offset_seconds: float
    velocity: int
    confidence: float

@dataclass(frozen=True)
class TranscriptionRequest:
    normalized_audio_path: Path
    model_id: str
    sample_rate: int

class TranscriptionEngine(Protocol):
    def transcribe(
        self,
        request: TranscriptionRequest,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> list[PerformedNote]: ...

class ProjectRepository(Protocol):
    def create(self, project: Project) -> None: ...
    def get(self, project_id: str) -> Project: ...
    def save_revision(self, revision: ScoreRevision) -> None: ...
    def latest_revision(self, project_id: str) -> ScoreRevision: ...

class Exporter(Protocol):
    def export(self, revision: ScoreRevision, destination: Path) -> Path: ...
```

Model-specific tensors and decoder output never cross the `TranscriptionEngine` boundary. UI-specific Qt types never enter the domain layer.

## 8. Project storage

Each project uses an opaque UUID and a dedicated application-data directory:

```text
projects/<project-id>/
  source/
    original.<extension>
  audio/
    normalized.wav
    waveform.bin
  artifacts/
    transcription-<pipeline-version>.json
    analysis-<pipeline-version>.json
  exports/
  logs/
```

SQLite stores compact metadata and revision history:

- `projects(id, title, source_path, source_sha256, duration_ms, created_at, updated_at)`
- `jobs(id, project_id, status, stage, progress, pipeline_version, error_code, created_at, updated_at)`
- `score_revisions(id, project_id, parent_id, revision_number, score_json, created_at)`
- `edit_operations(id, revision_id, operation_type, payload_json, sequence_number)`
- `exports(id, project_id, revision_id, format, destination_path, created_at)`

Large audio and model artifacts remain on disk. Database paths are relative to the project root when possible. Schema migrations are ordered, deterministic, and covered by fixtures.

## 9. Processing data flow

1. User selects an MP3, WAV, M4A, MP4, or MOV file.
2. `MediaImporter` confirms that the file exists, is readable, is no longer than 5 minutes, and contains a decodable audio stream.
3. The source is copied into the project directory without changing the original.
4. `AudioNormalizer` invokes bundled FFmpeg with deterministic arguments and creates model-required WAV plus a waveform cache.
5. `TranscriptionCoordinator` starts the worker and sends a versioned request over local IPC.
6. The selected `TranscriptionEngine` emits performed notes with confidence values.
7. `BeatAnalyzer` estimates beats, tempo, meter, and candidate key.
8. `Quantizer` creates notated positions, measures, rests, voices, and ties while preserving performed seconds.
9. `ScoreRepository` saves the first immutable score revision.
10. The editor displays the waveform, synchronized playback cursor, piano roll, confidence markers, and notation preview.
11. User edits are represented as undoable semantic commands and produce autosaved revisions.
12. Exporters create MusicXML, MIDI, and PDF from a selected revision.
13. Validation reopens MusicXML and checks MIDI/PDF file integrity before reporting success.

## 10. Editor design

The editor prioritizes correction speed instead of complete engraving.

Main workspace:

- project and transport controls at the top;
- waveform and playback cursor;
- zoomable piano roll aligned to performed time;
- standard-notation preview aligned to measures;
- inspector for selected note pitch, onset, duration, velocity, and confidence;
- job and export status area.

Supported commands:

- select one or multiple notes;
- add and delete notes;
- move notes in pitch or time;
- resize note duration;
- change tempo anchors;
- move measure boundaries;
- undo and redo;
- loop selection;
- slow playback without changing pitch;
- jump between low-confidence notes.

Every edit updates the working projection immediately. Autosave coalesces rapid drag operations into one semantic command and writes a new revision after a short idle interval.

## 11. Model feasibility phase

Before product implementation, evaluate at least two replaceable CPU-capable piano transcription pipelines against 30–50 rights-cleared clips. The dataset must include clean and noisy recordings, different tempos, varied polyphony, acoustic and digital piano, and multiple recording devices.

Report:

- note-onset F1;
- note-onset-plus-offset F1;
- frame F1;
- median and p95 runtime;
- peak resident memory;
- model/package size;
- failure rate;
- qualitative correction effort on at least 10 clips.

Reference hardware is a CPU-only machine with 16 GB RAM. A 5-minute file must finish without total application memory exceeding 12 GB. Phase 0 selects one baseline engine, one fallback engine if justified, a pinned model checksum, decoder thresholds, and measurable launch gates. If neither model produces useful editable drafts, narrow supported recording conditions before building the editor.

## 12. Failure handling and recovery

- Unsupported, corrupt, unreadable, or over-limit media fails before inference.
- Each stage writes to a temporary artifact, validates it, then atomically renames it.
- The job record stores the last completed stage; retry resumes from that stage when inputs and pipeline version match.
- A worker heartbeat detects crashes and hangs.
- Cancellation terminates work at a safe checkpoint while preserving completed artifacts.
- Memory exhaustion returns a specific error and suggests a shorter crop or lower-compute engine.
- Low-confidence results open in the editor with a warning rather than failing.
- The source file is immutable.
- Score revisions are immutable; undo and redo apply semantic commands.
- Startup recovery opens the latest valid revision and identifies interrupted jobs.
- Project deletion requires explicit confirmation and removes its database records and local artifacts.
- Logs exclude source filenames and musical event contents by default.

Structured error codes:

- `UNSUPPORTED_MEDIA`
- `MEDIA_TOO_LONG`
- `MEDIA_UNREADABLE`
- `DECODE_FAILED`
- `NO_PIANO_DETECTED`
- `MODEL_FAILED`
- `OUT_OF_MEMORY`
- `EXPORT_FAILED`
- `WORKER_LOST`
- `INTERNAL_ERROR`

## 13. Privacy and security

- No network permission is required for normal use.
- No audio, score, diagnostic, or usage data is transmitted.
- Bundled FFmpeg runs with constrained arguments and no shell interpolation.
- User-controlled filenames are never treated as command fragments.
- Imported media is probed by content rather than trusted extension or MIME type.
- Temporary files live inside the project workspace and are removed after successful promotion or recovery.
- Export destinations require explicit user selection.
- Diagnostic bundles require explicit user action and show included files before creation.

## 14. Testing strategy

### Unit tests

Cover score types, rational notated time, time mapping, quantization costs, measure sums, ties, edit commands, undo/redo, validation, state transitions, migration logic, and exporter mappings.

### Property-based tests

Prove that:

- every note has `offset_seconds > onset_seconds`;
- measure durations sum correctly;
- tied segments preserve total performed duration;
- edit replay produces the same revision;
- undo followed by redo restores the same score;
- MusicXML round trips preserve pitch and duration semantics.

### Integration tests

Use small rights-cleared fixtures to exercise FFmpeg probing and decoding, worker IPC, cancellation, stage resume, SQLite persistence, model adapter execution, MusicXML validation, MIDI generation, and PDF rendering.

### End-to-end tests

On both platforms:

1. Import a short piano fixture.
2. Observe progress.
3. Cancel and resume one run.
4. Complete transcription.
5. Correct a low-confidence note.
6. Reload the project.
7. Confirm synchronized playback.
8. Export all formats.
9. Open MusicXML in MuseScore.
10. Verify the original source hash is unchanged.

### Packaging tests

Install on clean Windows and macOS machines, launch without Python installed, process a fixture offline, create exports, uninstall, and verify that user projects remain unless explicitly removed.

## 15. Success criteria

### Model gate

- A 5-minute supported file completes on reference CPU hardware within a benchmark-defined p95 accepted before implementation proceeds.
- Peak application memory is at most 12 GB.
- Quality metrics and human correction effort support a useful-draft claim.
- The selected engine, model checksum, and decoder configuration are reproducible.

### Product gate

- At least 90% of valid beta files complete or return an actionable error.
- At least 80% of testers can correct and export a result without assistance.
- Import, transcription, cancellation, resume, autosave, recovery, playback, and all exports pass end-to-end tests.
- MusicXML opens in MuseScore without structural errors.
- MIDI preserves performed timing.
- PDF matches the corrected notation preview.
- Windows and macOS installers run on clean supported machines.
- No normal workflow attempts network access.
- Source media remains unchanged.

## 16. Delivery roadmap

### Sub-project 1: Model feasibility

Build the benchmark harness, evaluation manifest, model adapters, metrics, performance report, and model decision record.

**Exit:** one baseline pipeline is selected or input conditions are narrowed.

### Sub-project 2: Core transcription pipeline

Build media validation, FFmpeg normalization, versioned artifacts, worker protocol, transcription adapter, stage state machine, cancellation, and resume.

**Exit:** a command-line flow turns a fixture into deterministic performed-note JSON twice without recomputing valid stages.

### Sub-project 3: Desktop vertical slice

Build project creation, import UI, progress UI, worker supervision, project persistence, waveform, playback, and initial piano-roll display.

**Exit:** packaged development builds import and transcribe a fixture without freezing the UI.

### Sub-project 4: Rhythm and notation

Build beat analysis, tempo map, quantization, measures, voices, ties, canonical score revisions, MusicXML, and notation preview.

**Exit:** benchmark outputs are structurally valid and open in MuseScore.

### Sub-project 5: Focused correction editor

Build selection, add/delete/move/resize, tempo anchors, measure boundaries, confidence navigation, loop/slow playback, command history, autosave, and crash recovery.

**Exit:** a user corrects a fixture, reloads it, and obtains the same score and edit history.

### Sub-project 6: Export and packaging

Build MIDI and PDF exports, validation, application resources, model bundling, Windows packaging, macOS packaging, signing/notarization preparation, and clean-machine tests.

**Exit:** offline installers process and export a fixture on both platforms.

### Sub-project 7: Private beta hardening

Add actionable error presentation, diagnostic bundle preview, performance profiling, accessibility checks, release checklist, tester guide, feedback workflow, and regression suite.

**Exit:** product and model gates hold across the private beta evaluation set.

## 17. Deferred roadmap

After MVP evidence supports expansion:

1. Apple Silicon and NVIDIA acceleration.
2. Longer recordings.
3. Rich notation and page-layout controls.
4. Piano pedal, dynamics, articulations, and fingering.
5. Guitar transcription and tablature as a separate design/spec/plan cycle.
6. Mixed-audio source separation.
7. Optional cloud inference and project synchronization.
8. Automatic updates and opt-in telemetry.
