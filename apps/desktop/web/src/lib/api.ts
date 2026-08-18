import type { ProjectListItem } from "./types";

// Fixed dev/desktop backend port — apps/desktop/run_backend.py:43
// (`AURA_BACKEND_PORT = 8317`), bound to 127.0.0.1 only.
export const BASE = "http://127.0.0.1:8317";

async function json<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

// Response shapes below are verified against apps/api/src/aura_api/schemas.py
// (see task-5-report.md for the exact file:line for each) rather than
// assumed from the plan — the plan explicitly calls this out as a
// verify-against-reality step.

/** apps/api/src/aura_api/schemas.py ProjectResponse (id, title, instrument, media_asset_id). */
export interface CreateProjectResponse {
  id: string;
  title: string;
  instrument: string;
  media_asset_id: string;
}

/** apps/api/src/aura_api/schemas.py CreateJobResponse (job_id, status). */
export interface CreateJobResponse {
  job_id: string;
  status: string;
}

/** apps/api/src/aura_api/schemas.py JobStatusResponse. */
export interface JobStatusResponse {
  id: string;
  status: string;
  stage: string | null;
  progress: number;
  error_code: string | null;
  error_detail: string | null;
}

export const api = {
  listProjects: () => fetch(`${BASE}/v1/projects`).then((r) => json<ProjectListItem[]>(r)),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${BASE}/v1/uploads`, { method: "POST", body: form }).then((r) =>
      json<{ object_key: string }>(r),
    );
  },
  createProject: (title: string, instrument: string, object_key: string) =>
    fetch(`${BASE}/v1/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, instrument, object_key }),
    }).then((r) => json<CreateProjectResponse>(r)),
  startTranscription: (projectId: string) =>
    fetch(`${BASE}/v1/projects/${projectId}/transcriptions`, { method: "POST" }).then((r) =>
      json<CreateJobResponse>(r),
    ),
  getJob: (jobId: string) => fetch(`${BASE}/v1/jobs/${jobId}`).then((r) => json<JobStatusResponse>(r)),
  scoreUrl: (projectId: string) => `${BASE}/v1/projects/${projectId}/score`,
  audioUrl: (projectId: string) => `${BASE}/v1/projects/${projectId}/audio`,
  exportDownloadUrl: (exportId: string) => `${BASE}/v1/exports/${exportId}/download`,
};
