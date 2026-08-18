# Score Preview + Playback UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder page with the first real product surface: Home (project list + upload) and Score view (OSMD-rendered notation, synced playback of original recording or synthesized score, export) — fully offline inside the existing Tauri shell.

**Architecture:** `apps/desktop/web/` becomes a Svelte + Vite TypeScript app wired into Tauri's `beforeDevCommand`/`beforeBuildCommand`. Three additive backend endpoints (project list with job+exports, score JSON, normalized audio) plus an origin-allowlisted CORS change confined to `apps/desktop/run_backend.py`. Playback sync is one pure timeline (`onsetSeconds` per event, document order matched to OSMD cursor steps) driving a `requestAnimationFrame` cursor over either audio source.

**Tech Stack:** Svelte 5 + Vite (TS), OpenSheetMusicDisplay (npm), a sampler library with locally-bundled soundfonts (smplr or soundfont-player — verified in its task), FastAPI/Starlette additions, Vitest for frontend units, pytest for backend.

**Spec:** `docs/superpowers/specs/2026-08-17-score-preview-playback-ui-design.md`

## Global Constraints

- **Fixed port `8317`**, base URL `http://127.0.0.1:8317` — same literal everywhere, no new port constants.
- **Fully offline at runtime**: no CDN fetches, no Google Fonts in the app itself, soundfonts bundled locally. (`npm install` at build time is fine.)
- **`aura_api.main` stays untouched by the CORS change** — allowlist logic lives in `apps/desktop/run_backend.py` only, same discipline as sub-project 2. Never a wildcard on `/v1/*`; `/healthz`'s existing wildcard route is unchanged.
- **No exact third-party API names from memory**: OSMD, the sampler library, and Tauri v2 download/dialog APIs must be verified against the installed package (read `node_modules` typings / real docs) before use — TS compile + a real runtime check is the proof, mirroring sub-project 2's Rust discipline. Python code in this plan IS exact (written against the real codebase).
- **Task 1 is a gate**: if OSMD cannot acceptably render this repo's real guitar-tab and grand-staff MusicXML, STOP and report — do not build Tasks 5-9 on a broken foundation. Verovio is the named fallback, decided by the controller, not improvised.
- **Existing routes must not change shape.** New endpoints only.
- **Visual language** per the approved canvas (https://claude.ai/code/artifact/e595c5db-c61d-48ce-82be-6b92fde37551, "Recommended — B + A Hybrid" + "Home" boards): dark UI `#1e1d21`/`#26242a`/border `#37343c`, text `#e8e5df`/dim `#9b968c`, amber accent `#d99a4e`, warm paper `#f5f1e8`. System font stack (`system-ui`) — no webfont dependency.
- **Frontend tests use Vitest**; backend tests follow existing `apps/api/tests` + `apps/desktop/tests` patterns (real `LocalStorageClient` via monkeypatch, unconditional test env overrides — never `setdefault`).

## File Structure

```text
apps/desktop/web/                  # replaced wholesale by the Vite app
  package.json, vite.config.ts, tsconfig.json, svelte.config.js
  index.html                        # Vite entry
  src/
    main.ts                         # mount + hash router
    App.svelte                      # route switch (Home | Score)
    lib/
      api.ts                        # typed client for /v1/* (base URL constant)
      types.ts                      # ProjectListItem, ScoreJson, TimelineEntry...
      timeline.ts                   # buildTimeline, cursorIndexAt (pure, unit-tested)
      playback.ts                   # playback store: position/playing/source/volume
      projects.ts                   # projects store + job polling
    components/
      Home.svelte                   # top bar, drop zone, project rows
      ScoreView.svelte              # hybrid layout: sidebar + paper + transport
      Sidebar.svelte                # facts, view options, export buttons
      Transport.svelte              # play/pause, time, scrubber, source toggle, volume
      Notation.svelte               # OSMD mount + cursor control
    assets/soundfonts/              # bundled piano + guitar soundfonts (Task 8)
  tests/ (Vitest)
    timeline.test.ts
    playback.test.ts
apps/api/src/aura_api/routers/projects.py    # + GET /v1/projects
apps/api/src/aura_api/routers/scores.py      # new: score JSON + audio endpoints
apps/api/src/aura_api/schemas.py             # + list/response models
apps/api/src/aura_api/main.py                # include scores router (additive)
apps/api/tests/test_projects_list.py         # new
apps/api/tests/test_scores_endpoints.py      # new
apps/desktop/run_backend.py                  # CORS allowlist for /v1/*
apps/desktop/tests/test_cors_scope.py        # extended pins
apps/desktop/src-tauri/tauri.conf.json       # frontendDist/dev wiring
docs/superpowers/SESSION-HANDOFF.md          # Task 9
```

---

### Task 1: Vite + Svelte scaffold and the OSMD render spike (GATE)

**Files:**
- Create: `apps/desktop/web/` Vite app (package.json, vite.config.ts, index.html, src/main.ts, src/App.svelte — via the official scaffolder, not hand-written)
- Modify: `apps/desktop/src-tauri/tauri.conf.json` (`build.beforeDevCommand`, `build.beforeBuildCommand`, `build.devUrl`, `frontendDist`)
- Create: two real MusicXML fixtures under `apps/desktop/web/spike/` (guitar tab + piano grand staff, produced by the real `musicxml` package)

**Interfaces:**
- Produces: a building, launching frontend app; spike verdict on OSMD (render quality + cursor API ergonomics) with screenshots; the confirmed OSMD cursor idiom later tasks use (document the real API names found — e.g. cursor iteration and element position access — in the task report for Tasks 6-7's dispatches).

- [ ] **Step 1: Move the old placeholder aside and scaffold**

The current `apps/desktop/web/` holds only `index.html` (the placeholder). Delete it (git tracks history) and scaffold with the official tool:

```bash
cd /home/user/AuraAudio/apps/desktop
rm -rf web && npm create vite@latest web -- --template svelte-ts
cd web && npm install && npm install opensheetmusicdisplay
```

Accept whatever Svelte major the scaffolder pins (record it). Strip demo content (`Counter.svelte`, logos) down to an empty `App.svelte` shell.

- [ ] **Step 2: Wire Tauri to Vite**

In `apps/desktop/src-tauri/tauri.conf.json`, per Tauri v2's real schema (verify field names against the installed `tauri.conf.json` JSON schema — Global Constraints): `build.beforeDevCommand: "npm run dev --prefix ../web"`, `build.devUrl: "http://localhost:5173"`, `build.beforeBuildCommand: "npm run build --prefix ../web"`, `frontendDist: "../web/dist"`. Confirm `cargo tauri dev` (under `xvfb-run -a`, the established pattern) opens the window showing the empty Svelte app — the Rust health gate still runs first and must still pass.

- [ ] **Step 3: Generate real MusicXML fixtures**

```bash
cd /home/user/AuraAudio && uv run --package musicxml python - <<'EOF'
from pathlib import Path
from score_schema.models import build_score
from musicxml.export import score_json_to_musicxml
guitar = build_score(instrument="guitar", tempo_bpm=96.0, meter="4/4", key="E minor",
    confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7}, time_map=[{"beat": 0, "seconds": 0.0}],
    measures=[{"number": 1, "events": [
        {"id": "n0", "pitch": 52, "onsetSeconds": 0.0, "offsetSeconds": 0.5, "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1, "confidence": 0.9, "locked": False, "string": 5, "fret": 2, "hand": None},
        {"id": "n1", "pitch": 55, "onsetSeconds": 0.5, "offsetSeconds": 1.0, "notatedOnset": "1/4", "notatedDuration": "1/4", "voice": 1, "confidence": 0.9, "locked": False, "string": 4, "fret": 0, "hand": None},
        {"id": "n2", "pitch": 59, "onsetSeconds": 1.0, "offsetSeconds": 1.5, "notatedOnset": "1/2", "notatedDuration": "1/4", "voice": 1, "confidence": 0.9, "locked": False, "string": 3, "fret": 4, "hand": None},
        {"id": "n3", "pitch": 64, "onsetSeconds": 1.5, "offsetSeconds": 2.0, "notatedOnset": "3/4", "notatedDuration": "1/4", "voice": 1, "confidence": 0.9, "locked": False, "string": 2, "fret": 5, "hand": None}]}])
piano = build_score(instrument="piano", tempo_bpm=96.0, meter="4/4", key="C major",
    confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7}, time_map=[{"beat": 0, "seconds": 0.0}],
    measures=[{"number": 1, "events": [
        {"id": "p0", "pitch": 40, "onsetSeconds": 0.0, "offsetSeconds": 1.0, "notatedOnset": "0/1", "notatedDuration": "1/2", "voice": 1, "confidence": 0.9, "locked": False, "string": None, "fret": None, "hand": "left"},
        {"id": "p1", "pitch": 76, "onsetSeconds": 1.0, "offsetSeconds": 2.0, "notatedOnset": "1/2", "notatedDuration": "1/2", "voice": 1, "confidence": 0.9, "locked": False, "string": None, "fret": None, "hand": "right"}]}])
out = Path("apps/desktop/web/spike"); out.mkdir(parents=True, exist_ok=True)
score_json_to_musicxml(guitar, out / "guitar.musicxml")
score_json_to_musicxml(piano, out / "piano.musicxml")
print("wrote", list(out.iterdir()))
EOF
```

(Field names above match `packages/score_schema` v4 — if `build_score`'s real signature differs, read `packages/score_schema/src/score_schema/models.py` and adapt; the fixtures must come from the real exporter either way.)

- [ ] **Step 4: Spike page**

Add a temporary spike route/component that loads each fixture into OSMD, renders it, and walks the cursor five steps forward with visible highlight. Use OSMD's real API — read `node_modules/opensheetmusicdisplay`'s typings for load/render/cursor names rather than memory. Run under `xvfb-run -a cargo tauri dev`, screenshot both fixtures rendered and one cursor-advanced state.

- [ ] **Step 5: Gate verdict**

PASS = tab numbers legible on the guitar fixture, two-staff brace on the piano fixture, cursor steps map 1:1 to notes in document order (assert by logging cursor notes vs fixture event order). FAIL on any = STOP, report per Global Constraints. On PASS: delete the spike route, keep fixtures (Tests reuse them), commit.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/web apps/desktop/src-tauri/tauri.conf.json
git commit -m "feat(web): Svelte+Vite scaffold wired into Tauri; OSMD spike passed"
```

---

### Task 2: `GET /v1/projects` — list with latest job + exports

**Files:**
- Modify: `apps/api/src/aura_api/routers/projects.py`
- Modify: `apps/api/src/aura_api/schemas.py`
- Create: `apps/api/tests/test_projects_list.py`

**Interfaces:**
- Produces: `GET /v1/projects` → `[{id, title, instrument, created_at, duration_ms, job: {id, status, stage, progress} | null, exports: [{id, format}]}]`, newest first. `exports` non-empty only when the latest job succeeded.

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_projects_list.py
from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import Export, MediaAsset, Project, TranscriptionJob


def _seed(db, title, status=None, with_exports=False):
    p = Project(owner_id="anonymous", title=title, instrument="guitar")
    db.add(p); db.flush()
    a = MediaAsset(project_id=p.id, kind="source", object_key=f"uploads/x/{title}.wav", duration_ms=31000)
    db.add(a); db.flush()
    if status is not None:
        j = TranscriptionJob(project_id=p.id, media_asset_id=a.id, input_hash=f"h-{title}", status=status, stage="export", progress=100 if status == "succeeded" else 40)
        db.add(j); db.flush()
        if with_exports:
            db.add(Export(project_id=p.id, job_id=j.id, format="midi", status="succeeded", object_key=f"jobs/{j.id}/exports/out.mid"))
            db.add(Export(project_id=p.id, job_id=j.id, format="musicxml", status="succeeded", object_key=f"jobs/{j.id}/exports/out.musicxml"))
    db.commit()
    return p


def test_list_projects_newest_first_with_job_and_exports(db_session):
    _seed(db_session, "older", status="succeeded", with_exports=True)
    _seed(db_session, "newer", status="running")
    client = TestClient(create_app())
    resp = client.get("/v1/projects")
    assert resp.status_code == 200
    items = resp.json()
    assert [i["title"] for i in items] == ["newer", "older"]
    assert items[0]["job"]["status"] == "running" and items[0]["exports"] == []
    assert items[1]["job"]["status"] == "succeeded"
    assert sorted(e["format"] for e in items[1]["exports"]) == ["midi", "musicxml"]
    assert items[1]["duration_ms"] == 31000


def test_list_projects_project_without_job(db_session):
    _seed(db_session, "no-job", status=None)
    client = TestClient(create_app())
    items = client.get("/v1/projects").json()
    assert items[0]["job"] is None and items[0]["exports"] == []
```

- [ ] **Step 2: Run to verify failure**

`uv run --package aura-api pytest apps/api/tests/test_projects_list.py -v` — expect 404-based assertion failures (route doesn't exist).

- [ ] **Step 3: Implement**

Add to `apps/api/src/aura_api/schemas.py`:

```python
class ProjectJobSummary(BaseModel):
    id: str
    status: str
    stage: str | None
    progress: int


class ProjectExportSummary(BaseModel):
    id: str
    format: str


class ProjectListItem(BaseModel):
    id: str
    title: str
    instrument: str
    created_at: str
    duration_ms: int | None
    job: ProjectJobSummary | None
    exports: list[ProjectExportSummary]
```

Add to `apps/api/src/aura_api/routers/projects.py` (imports: `Export`, `TranscriptionJob`, the new schemas):

```python
@router.get("/projects", response_model=list[ProjectListItem])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectListItem]:
    projects = (
        db.query(Project).filter(Project.deleted_at.is_(None))
        .order_by(Project.created_at.desc()).all()
    )
    items: list[ProjectListItem] = []
    for p in projects:
        asset = (
            db.query(MediaAsset)
            .filter(MediaAsset.project_id == p.id, MediaAsset.kind == "source")
            .order_by(MediaAsset.id.desc()).first()
        )
        job = (
            db.query(TranscriptionJob)
            .filter(TranscriptionJob.project_id == p.id)
            .order_by(TranscriptionJob.created_at.desc()).first()
        )
        exports = []
        if job is not None and job.status == "succeeded":
            exports = [
                ProjectExportSummary(id=e.id, format=e.format)
                for e in db.query(Export).filter(Export.job_id == job.id).all()
            ]
        items.append(ProjectListItem(
            id=p.id, title=p.title, instrument=p.instrument,
            created_at=p.created_at.isoformat(),
            duration_ms=asset.duration_ms if asset else None,
            job=ProjectJobSummary(id=job.id, status=job.status, stage=job.stage, progress=job.progress) if job else None,
            exports=exports,
        ))
    return items
```

(Per-project queries are fine at desktop scale — single local user; noted deliberately, not an oversight.)

- [ ] **Step 4: Run tests to verify pass**, then full api suite (`uv run --package aura-api pytest apps/api/tests -v`).

- [ ] **Step 5: Commit** — `feat(api): list projects with latest job and export summaries`

---

### Task 3: Score JSON + audio endpoints

**Files:**
- Create: `apps/api/src/aura_api/routers/scores.py`
- Modify: `apps/api/src/aura_api/main.py` (include router)
- Create: `apps/api/tests/test_scores_endpoints.py`

**Interfaces:**
- Consumes: `StageArtifact` rows (`stage == "assign"` for score, `stage == "normalize"` for audio) written by the pipeline; `storage_client.get_bytes`/`path_for`.
- Produces: `GET /v1/projects/{id}/score` → score JSON (200) or 404; `GET /v1/projects/{id}/audio` → WAV `FileResponse` or 404.

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_scores_endpoints.py
import json
from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import MediaAsset, Project, StageArtifact, TranscriptionJob


def _project_with_job(db, status="succeeded"):
    p = Project(owner_id="anonymous", title="T", instrument="guitar")
    db.add(p); db.flush()
    a = MediaAsset(project_id=p.id, kind="source", object_key="uploads/x/r.wav")
    db.add(a); db.flush()
    j = TranscriptionJob(project_id=p.id, media_asset_id=a.id, input_hash="h", status=status)
    db.add(j); db.flush()
    db.commit()
    return p, j


def test_score_endpoint_returns_assign_artifact(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage
    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.scores as scores_module
    monkeypatch.setattr(scores_module, "storage_client", storage.storage_client)

    p, j = _project_with_job(db_session)
    payload = {"schemaVersion": 4, "parts": []}
    storage.storage_client.put_bytes(f"jobs/{j.id}/stage/assign.json", json.dumps(payload).encode())
    db_session.add(StageArtifact(job_id=j.id, stage="assign", version=2, object_key=f"jobs/{j.id}/stage/assign.json", sha256="x"))
    db_session.commit()

    client = TestClient(create_app())
    resp = client.get(f"/v1/projects/{p.id}/score")
    assert resp.status_code == 200 and resp.json() == payload


def test_score_endpoint_404_without_succeeded_job(db_session):
    p, _ = _project_with_job(db_session, status="running")
    client = TestClient(create_app())
    assert client.get(f"/v1/projects/{p.id}/score").status_code == 404


def test_audio_endpoint_serves_normalize_artifact(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage
    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.scores as scores_module
    monkeypatch.setattr(scores_module, "storage_client", storage.storage_client)

    p, j = _project_with_job(db_session)
    storage.storage_client.put_bytes(f"jobs/{j.id}/stage/normalized.wav", b"RIFF-fake")
    db_session.add(StageArtifact(job_id=j.id, stage="normalize", version=1, object_key=f"jobs/{j.id}/stage/normalized.wav", sha256="y"))
    db_session.commit()

    client = TestClient(create_app())
    resp = client.get(f"/v1/projects/{p.id}/audio")
    assert resp.status_code == 200 and resp.content == b"RIFF-fake"
```

(Before writing, read `workers/transcription/src/aura_worker/stages/normalize.py` for the REAL normalize artifact object key and adapt the test/implementation to it — the key literal above is illustrative; the artifact lookup by `stage == "normalize"` row is the contract, not the key string.)

- [ ] **Step 2: Run to verify failure** (`ModuleNotFoundError` / 404s).

- [ ] **Step 3: Implement `apps/api/src/aura_api/routers/scores.py`**

```python
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.models import Project, StageArtifact, TranscriptionJob
from aura_api.storage import storage_client

router = APIRouter(tags=["scores"])


def _latest_artifact(db: Session, project_id: str, stage: str) -> StageArtifact | None:
    job = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.project_id == project_id, TranscriptionJob.status == "succeeded")
        .order_by(TranscriptionJob.created_at.desc()).first()
    )
    if job is None:
        return None
    return (
        db.query(StageArtifact)
        .filter(StageArtifact.job_id == job.id, StageArtifact.stage == stage)
        .order_by(StageArtifact.version.desc()).first()
    )


@router.get("/projects/{project_id}/score")
def get_score(project_id: str, db: Session = Depends(get_db)) -> dict:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    artifact = _latest_artifact(db, project_id, "assign")
    if artifact is None:
        raise HTTPException(status_code=404, detail="no transcribed score yet")
    try:
        return json.loads(storage_client.get_bytes(artifact.object_key))
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="score artifact missing")


@router.get("/projects/{project_id}/audio")
def get_audio(project_id: str, db: Session = Depends(get_db)) -> FileResponse:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    artifact = _latest_artifact(db, project_id, "normalize")
    if artifact is None:
        raise HTTPException(status_code=404, detail="no audio artifact yet")
    try:
        path = storage_client.path_for(artifact.object_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="audio artifact missing")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="audio artifact missing")
    return FileResponse(path, media_type="audio/wav", filename="normalized.wav")
```

In `main.py`'s `create_app()`, import and `app.include_router(scores.router, prefix="/v1")` alongside the existing routers.

- [ ] **Step 4: Run tests to pass, then full api suite.**

- [ ] **Step 5: Commit** — `feat(api): score JSON and normalized-audio endpoints`

---

### Task 4: CORS origin allowlist for `/v1/*`

**Files:**
- Modify: `apps/desktop/run_backend.py`
- Modify: `apps/desktop/tests/test_cors_scope.py`

**Interfaces:**
- Produces: `/v1/*` responses carry CORS headers ONLY for origins in `WEBVIEW_ORIGINS`; `/healthz` wildcard route byte-identical to today. `aura_api.main` untouched.

- [ ] **Step 1: Determine the real webview origin empirically**

Run the app (`xvfb-run -a cargo tauri dev`) with a temporary log of `Origin` request headers in `run_backend.py` (or read it from an existing request log), hitting any `/v1/*` route from the webview console. Record the exact origin (Tauri v2 Linux/WebKitGTK — expect `http://tauri.localhost` or `tauri://localhost`, but use what is OBSERVED). Remove the temporary logging.

- [ ] **Step 2: Write the failing tests**

Extend `apps/desktop/tests/test_cors_scope.py`:

```python
WEBVIEW_ORIGIN = "http://tauri.localhost"  # replace with the observed value from Step 1
DEV_ORIGIN = "http://localhost:5173"
FOREIGN_ORIGIN = "http://evil.example"


def test_v1_allows_webview_origin():
    resp = _client().get("/v1/projects", headers={"Origin": WEBVIEW_ORIGIN})
    assert resp.headers.get("access-control-allow-origin") == WEBVIEW_ORIGIN


def test_v1_allows_vite_dev_origin():
    resp = _client().get("/v1/projects", headers={"Origin": DEV_ORIGIN})
    assert resp.headers.get("access-control-allow-origin") == DEV_ORIGIN


def test_v1_denies_foreign_origin():
    resp = _client().get("/v1/projects", headers={"Origin": FOREIGN_ORIGIN})
    assert "access-control-allow-origin" not in resp.headers


def test_healthz_wildcard_unchanged():
    resp = _client().get("/healthz", headers={"Origin": FOREIGN_ORIGIN})
    assert resp.headers.get("access-control-allow-origin") == "*"
```

(Adapt `_client()` to this file's existing helper; keep the existing four tests passing unchanged.)

- [ ] **Step 3: Implement in `run_backend.py`**

Wrap the mounted app with `CORSMiddleware` restricted to the exact-origin list (this wraps the MOUNT inside the desktop entrypoint's composition — `aura_api.main.app` itself is not modified):

```python
WEBVIEW_ORIGINS = [
    "http://tauri.localhost",   # observed in Step 1 — replace/extend with real values
    "tauri://localhost",
    "http://localhost:5173",    # vite dev
    "http://127.0.0.1:5173",
]

v1_app = CORSMiddleware(
    app, allow_origins=WEBVIEW_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["*"]
)
# root_app routes: Route("/healthz", ...) [existing wildcard], Mount("/", app=v1_app)
```

Keep the existing `/healthz` Route exactly as is; only the Mount target changes from `app` to the wrapped `v1_app`.

- [ ] **Step 4: Run** `uv run --package aura-api pytest apps/desktop/tests -v` — all pass (existing + new).

- [ ] **Step 5: Live check** — from the running webview, `fetch('http://127.0.0.1:8317/v1/projects')` succeeds; from a plain browser page on a foreign origin the same fetch is CORS-blocked (or verify via curl that the foreign `Origin` gets no CORS headers).

- [ ] **Step 6: Commit** — `feat(desktop): origin-allowlisted CORS for /v1 in the desktop entrypoint`

---

### Task 5: API client, types, Home screen

**Files:**
- Create: `apps/desktop/web/src/lib/api.ts`, `src/lib/types.ts`, `src/lib/projects.ts`
- Create: `apps/desktop/web/src/components/Home.svelte`
- Modify: `apps/desktop/web/src/App.svelte`, `src/main.ts` (hash routing: `#/` → Home, `#/project/{id}` → ScoreView placeholder)

**Interfaces:**
- Consumes: Tasks 2-4's endpoints.
- Produces: `api.listProjects(): Promise<ProjectListItem[]>`, `api.upload(file: File): Promise<{object_key: string}>`, `api.createProject(title, instrument, object_key)`, `api.startTranscription(projectId)`, `api.getJob(jobId)`, `api.scoreUrl(projectId)`, `api.audioUrl(projectId)`, `api.exportDownloadUrl(exportId)`; `projects` store with `refresh()` and self-managed 1s polling while any job is non-terminal.

- [ ] **Step 1: Types + client (`types.ts`, `api.ts`)**

```ts
// types.ts
export interface ProjectJobSummary { id: string; status: string; stage: string | null; progress: number }
export interface ProjectExportSummary { id: string; format: string }
export interface ProjectListItem {
  id: string; title: string; instrument: string; created_at: string;
  duration_ms: number | null; job: ProjectJobSummary | null; exports: ProjectExportSummary[];
}
```

```ts
// api.ts
export const BASE = "http://127.0.0.1:8317";

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

export const api = {
  listProjects: () => fetch(`${BASE}/v1/projects`).then((r) => json<ProjectListItem[]>(r)),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${BASE}/v1/uploads`, { method: "POST", body: form }).then((r) => json<{ object_key: string }>(r));
  },
  createProject: (title: string, instrument: string, object_key: string) =>
    fetch(`${BASE}/v1/projects`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, instrument, object_key }) }).then((r) => json<{ id: string }>(r)),
  startTranscription: (projectId: string) =>
    fetch(`${BASE}/v1/projects/${projectId}/transcriptions`, { method: "POST" }).then((r) => json<{ job_id: string; status: string }>(r)),
  getJob: (jobId: string) => fetch(`${BASE}/v1/jobs/${jobId}`).then((r) => json<{ id: string; status: string; stage: string | null; progress: number; error_code: string | null; error_detail: string | null }>(r)),
  scoreUrl: (projectId: string) => `${BASE}/v1/projects/${projectId}/score`,
  audioUrl: (projectId: string) => `${BASE}/v1/projects/${projectId}/audio`,
  exportDownloadUrl: (exportId: string) => `${BASE}/v1/exports/${exportId}/download`,
};
```

(Upload flow needs `instrument` — Home asks with a two-button choice (Guitar/Piano) after the file drop, per the backend's required field; title defaults to the filename.)

- [ ] **Step 2: `projects.ts` store** — writable store `{items, loading, error}`, `refresh()` fetches list, and while any `item.job` is non-terminal (`status` not in `succeeded|failed`) an interval re-fetches every 1s and stops when all terminal. Unit-testable: extract `hasActiveJob(items): boolean` as a pure export.

- [ ] **Step 3: `Home.svelte`** — implement the Home artboard: top bar (wordmark SVG + "New transcription" triggering a hidden file input), drop zone (dragover/drop + click), rows from the store (instrument SVG icon, title, facts line `instrument · duration · relative date`, status chip for `succeeded`, inline progress bar + stage label for running, error chip + detail tooltip + "Retry" button calling `startTranscription` for failed). Row click navigates `#/project/{id}` (only when succeeded). Colors/typography per Global Constraints. Inline error panel when `listProjects` fails.

- [ ] **Step 4: Manual verification** — `xvfb-run -a cargo tauri dev`: upload a real fixture WAV (generate with `test_fixtures` like sub-project 2's smoke test), watch the row progress live to Transcribed, screenshot. Confirm retry on a fabricated failure if cheap, else cover by store unit test.

- [ ] **Step 5: Vitest setup + store test** — add `vitest` to `apps/desktop/web`, `npm test` script; test `hasActiveJob` and the polling stop condition (fake timers, mocked `api.listProjects`).

- [ ] **Step 6: Commit** — `feat(web): Home screen with upload, live progress, project list`

---

### Task 6: Score view layout + OSMD notation

**Files:**
- Create: `apps/desktop/web/src/components/ScoreView.svelte`, `Sidebar.svelte`, `Notation.svelte`
- Modify: `App.svelte` (route `#/project/{id}` → ScoreView)

**Interfaces:**
- Consumes: `api.scoreUrl`/`listProjects` (for facts + export ids), MusicXML via `api.exportDownloadUrl(musicxmlExportId)`; Task 1's verified OSMD idioms.
- Produces: `Notation.svelte` exposing `loadMusicXml(xml: string): Promise<void>` and `getCursor(): OSMDCursorHandle` (the handle Task 7 drives: `reset()`, `next()`, `show()`, current-element position access per Task 1's findings); `Sidebar.svelte` with facts (from score JSON `parts[0]`: `key`, `tempoBpm`, `meter`, instrument), tab-visibility toggle, zoom +/- (OSMD zoom API), export buttons.

- [ ] **Step 1: ScoreView skeleton** — hybrid-artboard layout: collapsible sidebar (state in component; collapse button per mockup), paper panel (`#f5f1e8`, shadow, radius) hosting `Notation`, docked transport placeholder strip (real transport in Task 7). Fetch score JSON + project list entry on mount; inline error panel on failure.

- [ ] **Step 2: Notation.svelte** — mount OSMD into the paper div with the real API names from Task 1; fetch the MusicXML export text and render. Tab toggle = re-render with the tab part hidden (OSMD option — verify; if no clean option exists, hide via the score's part filtering and note it). Zoom via OSMD's zoom property + re-render.

- [ ] **Step 3: Export buttons** — verify the working download mechanism in the real webview (Tauri v2: try plain `<a href={downloadUrl} download>` first; if WebKitGTK ignores it, use fetch→blob→Tauri dialog+fs plugin — read the installed `@tauri-apps/api`/plugin typings, Global Constraints). Implement whichever works; record which in the report.

- [ ] **Step 4: Manual verification** — real transcribed project renders in-app (both instruments via two uploads); sidebar facts match detection; zoom and tab toggle work; exports produce real files on disk. Screenshots.

- [ ] **Step 5: Commit** — `feat(web): score view with OSMD notation, sidebar, export`

---

### Task 7: Timeline sync + recording playback

**Files:**
- Create: `apps/desktop/web/src/lib/timeline.ts`, `src/lib/playback.ts`
- Create: `apps/desktop/web/src/components/Transport.svelte`
- Create: `apps/desktop/web/tests/timeline.test.ts`, `tests/playback.test.ts`
- Modify: `ScoreView.svelte` (wire transport + cursor loop)

**Interfaces:**
- Consumes: score JSON events; `Notation.getCursor()`.
- Produces:
  - `buildTimeline(score: ScoreJson): TimelineEntry[]` where `TimelineEntry = {t: number; step: number}` — one entry per DISTINCT onset (chord notes share a step count contribution; step = cursor-step index in document order), sorted ascending, throws on unsorted input.
  - `cursorIndexAt(timeline: TimelineEntry[], t: number): number` — binary search, last entry with `t_i <= t`, `-1` before the first.
  - `playback` store: `{position, duration, playing, source: "recording" | "synth", volume}` with `play()`, `pause()`, `seek(t)`, `setSource(s)`, `setVolume(v)`.

- [ ] **Step 1: Failing unit tests first (Vitest)** — timeline: multi-event score → entries sorted, chord (two events, same `onsetSeconds`) collapses to one entry advancing step by the chord size; empty score → `[]`; unsorted onsets → throw. `cursorIndexAt`: before-first → -1, exact hit, between entries, after last. Playback store: seek clamps to `[0, duration]`, `setSource` preserves position/playing.

- [ ] **Step 2: Implement `timeline.ts` (pure)**

```ts
export interface TimelineEntry { t: number; step: number }

export function buildTimeline(score: ScoreJson): TimelineEntry[] {
  const events = score.parts[0].measures.flatMap((m) => m.events);
  const entries: TimelineEntry[] = [];
  let step = 0;
  let lastT = -Infinity;
  for (const ev of events) {
    if (ev.onsetSeconds < lastT) throw new Error("events out of onset order");
    if (ev.onsetSeconds > lastT) {
      entries.push({ t: ev.onsetSeconds, step });
      lastT = ev.onsetSeconds;
    }
    step += 1;
  }
  return entries;
}

export function cursorIndexAt(timeline: TimelineEntry[], t: number): number {
  let lo = 0, hi = timeline.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (timeline[mid].t <= t) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return ans;
}
```

(OSMD cursor-step semantics for chords must match what Task 1 observed — if a chord is ONE cursor step, `step += 1` per distinct onset instead; reconcile against the spike's logged mapping and encode the real rule, updating the tests to match reality. The unit tests pin whichever rule is real.)

- [ ] **Step 3: Recording source + rAF loop** — `<audio src={api.audioUrl(projectId)}>` element owned by ScoreView; `playback` store wraps it. rAF loop (running while `playing`): read `audio.currentTime`, `cursorIndexAt`, and when the index changed since last frame, move the OSMD cursor (reset+advance or direct-jump per the real API's cheapest idiom) and `scrollIntoView` its element. Seek from the scrubber does the same lookup once.

- [ ] **Step 4: Transport.svelte** — per the hybrid artboard: skip-to-start, play/pause (accent circle), elapsed/total (`m:ss.d`), scrubber bound to `position/duration` with drag-seek, source toggle (Recording active; Synth disabled until Task 8 with a tooltip), volume slider.

- [ ] **Step 5: Manual verification** — real project: play, cursor tracks audibly-correct positions through the clip; drag-seek moves both audio and cursor; pause/resume stable. Screenshot mid-playback.

- [ ] **Step 6: Run Vitest suite; commit** — `feat(web): synced playback of the original recording`

---

### Task 8: Synth source

**Files:**
- Create: `apps/desktop/web/src/lib/synth.ts`
- Create: `apps/desktop/web/src/assets/soundfonts/` (bundled piano + guitar instrument data)
- Modify: `src/lib/playback.ts` (source switching), `Transport.svelte` (enable toggle)

**Interfaces:**
- Consumes: score JSON events (`pitch`, `onsetSeconds`, `offsetSeconds`), `playback` store contract from Task 7.
- Produces: `createSynthSource(score: ScoreJson, instrument: "guitar" | "piano"): PlaybackSource` where `PlaybackSource = {play(from: number): void; pause(): void; seek(t: number): void; currentTime(): number; duration: number; setVolume(v: number): void}` — the same interface a thin wrapper gives the `<audio>` element, so `playback.setSource()` swaps implementations behind one contract.

- [ ] **Step 1: Library verification first (mini-spike inside the task)** — install the candidate (`smplr`, else `soundfont-player`), and prove OFFLINE loading: bundle one instrument's soundfont data as local asset(s) imported through Vite (no runtime URL fetch to any non-`127.0.0.1` origin — check the network panel/webview logs). If neither library loads local data cleanly, STOP and report (fallback decision belongs to the controller: vendored midi-js soundfont `.js` data files, or WebAudio + per-note sample slicing).
- [ ] **Step 2: `synth.ts`** — WebAudio clock (`AudioContext.currentTime`-anchored): `play(from)` schedules every event with `onsetSeconds >= from` at `ctx.currentTime + (ev.onsetSeconds - from)` (duration `offsetSeconds - onsetSeconds`), retains scheduled nodes for `pause()` cancellation; `currentTime()` = `from + (ctx.currentTime - anchor)`; `duration` = max `offsetSeconds`. Gain node for volume.
- [ ] **Step 3: Wrap `<audio>` in the same `PlaybackSource` interface; `setSource` swap** = `old.pause(); new.seek(pos); if (playing) new.play(pos)` — position from the store, one code path.
- [ ] **Step 4: Unit tests** — scheduling math pure-function tests (extract `schedulePlan(events, from): {at: number; dur: number; pitch: number}[]`); toggle preserves position (mock sources).
- [ ] **Step 5: Manual verification** — toggle mid-playback both directions; synth audibly plays the transcription's notes for both instruments; cursor keeps tracking across the toggle. Confirm zero external network requests.
- [ ] **Step 6: Commit** — `feat(web): synthesized playback source with bundled soundfonts`

---

### Task 9: End-to-end verification + docs

**Files:**
- Modify: `docs/superpowers/SESSION-HANDOFF.md`

- [ ] **Step 1: Full-journey check** (`xvfb-run -a`, real app, fresh app-data dir): upload guitar fixture → progress → score renders → play Recording (cursor moves) → toggle Synth mid-playback → export MusicXML + MIDI to files → repeat abbreviated flow for piano fixture. Screenshots at each stage; real command log in the report.
- [ ] **Step 2: Full test suites** — `make test` (all packages incl. desktop), `npm test` in `apps/desktop/web`, `cargo build`/`clippy` clean (Rust untouched but verify no drift).
- [ ] **Step 3: `tauri build` sanity** — `vite build` succeeds and the packaged app (deb path from sub-project 2) still launches with the new frontend; not a full re-verification of packaging, just that the frontend swap didn't break the build.
- [ ] **Step 4: Update `SESSION-HANDOFF.md`** — sub-project 3 status with the honest convention: "N/N tasks implemented and reviewed clean; final whole-branch review pending" (NOT "DONE" — that word only lands after the whole-branch review, per the twice-established precedent), pointer to sub-project 4 next, note any new environment gotchas (npm/Vite specifics).
- [ ] **Step 5: Commit** — `docs: record score preview + playback UI progress`

## Self-Review Notes

- Spec coverage: Home (T5), Score view/layout (T6), sync + recording (T7), synth + toggle (T8), backend endpoints (T2-3), CORS (T4), OSMD spike gate (T1), export buttons (T6), errors (T5/T6 inline panels + retry), testing (unit T5/T7/T8, backend T2-4, e2e T9), docs (T9). Waveform/PDF/editing correctly absent (non-goals).
- Chord/cursor-step ambiguity is acknowledged and pinned to spike reality (T7 Step 2) rather than guessed.
- Type/name consistency: `PlaybackSource`, `TimelineEntry`, `api.*` names match across T5-T8.
