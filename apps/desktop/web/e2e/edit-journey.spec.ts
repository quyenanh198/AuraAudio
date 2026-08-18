// Committed, repeatable regression test for the core desktop-app journey:
// transcribe an uploaded recording, select a note on the rendered score,
// edit its pitch, undo/redo that edit, then export MusicXML and confirm the
// downloaded file reflects the edit. Runs against the REAL backend (real
// basic-pitch/tensorflow transcription, no mocking — see
// e2e/global-setup.ts) and the REAL Vite dev server (playwright.config.ts's
// `webServer`), driven through the actual UI: file input -> instrument
// choice -> project row -> canvas click on a rendered notehead -> Inspector
// controls -> the real `<a download>` export button.
//
// One `describe.serial` block sharing a single `page` across steps (per
// Playwright's documented pattern for a multi-step journey) — later steps
// depend on earlier ones' state (a transcribed project, a selected note, an
// edited pitch), so splitting into independent `test()`s without serial
// sharing would mean re-deriving that state from scratch every time, which
// is both slower (each re-transcribes) and not what this journey is meant
// to exercise.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

import { nameOctaveToPitch, NOTE_NAME_OPTIONS, pitchToName, clampPitch } from "../src/lib/noteEdit";
import { readFixtureContext } from "./fixtureContext";

// See global-setup.ts's identical comment: this package is `"type":
// "module"`, so `__dirname` does not exist here — derive it from
// `import.meta.url` instead.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../../../..");

// Real basic-pitch/tensorflow transcription of a 2s clip is the slow part
// of this journey (~60-90s observed for this project's guitar fixture,
// per the task brief) — generous on top of that observed range for a
// loaded/sandboxed machine.
const TRANSCRIBE_TIMEOUT_MS = 180_000;
const RENDER_TIMEOUT_MS = 30_000;
const SETTLE_TIMEOUT_MS = 30_000;

/** Inverse of noteEdit.ts's `pitchToName` (that module only exports the
 * forward direction) — parses a displayed "C#4" / "E-1" style string back
 * into (name, octave) by longest-prefix match against the same
 * `NOTE_NAME_OPTIONS` table the app itself renders from, then hands off to
 * the app's own `nameOctaveToPitch` for the actual MIDI math. Mirrors the
 * app's helper rather than reimplementing pitch arithmetic, per this
 * spec's own "compute the expected pitch from the app's own helper"
 * design. */
function parseDisplayedPitch(display: string): number {
  const byLengthDesc = [...NOTE_NAME_OPTIONS].sort((a, b) => b.length - a.length);
  const name = byLengthDesc.find((candidate) => display.startsWith(candidate));
  if (!name) throw new Error(`parseDisplayedPitch: no known note name prefixes "${display}"`);
  const octave = Number(display.slice(name.length));
  if (!Number.isFinite(octave)) {
    throw new Error(`parseDisplayedPitch: could not parse octave from "${display}"`);
  }
  return nameOctaveToPitch(name, octave);
}

function normalizeXml(text: string): string {
  return text.replace(/>\s+</g, "><").trim();
}

/** Ground truth for "what should this edited pitch's exported <pitch>
 * element look like", computed by calling the REAL production spelling
 * function (`musicxml.export._spell_pitch`) the backend's own export
 * pipeline uses (`score_json_to_musicxml` -> `_build_note` ->
 * `_spell_pitch`) — not a reimplementation of music21's diatonic-spelling
 * rules, which would risk silently drifting from what the app actually
 * does. Renders a minimal one-note document through the real music21
 * MusicXML writer and extracts just the `<pitch>` block, so this is exact
 * down to whitespace/tag structure, not a hand-typed guess at either.
 *
 * The stream carries the SAME `key_obj` as an inserted element (mirroring
 * `_build_single_staff`'s `m21_part.insert(0, key_obj)`) — confirmed by
 * hand necessary, not a defensive guess: music21's MusicXML writer adds a
 * cautionary `<accidental>natural</accidental>`/`<alter>0</alter>` for a
 * natural-spelled note whose pitch class the key signature would otherwise
 * imply sharp/flat (e.g. plain F in a 2-sharp/D-major key signature,
 * observed directly from a real edited note in this fixture), and it only
 * does this when a key signature is actually in scope for the note — a
 * bare, key-less stream silently omits the tag real export always
 * includes, breaking the containment check below for exactly that class
 * of edit. */
function expectedPitchXmlFragment(midiPitch: number, keyString: string, outPath: string): string {
  const [tonic, mode] = keyString.split(" ");
  const script = [
    "import re",
    "from pathlib import Path",
    "from music21 import key as m21_key, note, stream",
    "from musicxml.export import _spell_pitch",
    "",
    `key_obj = m21_key.Key(${JSON.stringify(tonic)}, ${JSON.stringify(mode)})`,
    `p = _spell_pitch(${midiPitch}, key_obj)`,
    "n = note.Note(p)",
    "n.duration.quarterLength = 1.0",
    "s = stream.Stream()",
    "s.append(key_obj)",
    "s.append(n)",
    `out = Path(${JSON.stringify(outPath)})`,
    's.write("musicxml", fp=str(out))',
    "text = out.read_text()",
    'match = re.search(r"<pitch>.*?</pitch>", text, re.DOTALL)',
    "print(match.group(0))",
  ].join("\n");

  const result = spawnSync("uv", ["run", "--package", "musicxml", "python", "-c", script], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
  });
  if (result.status !== 0) {
    throw new Error(
      `expectedPitchXmlFragment: oracle script failed (exit ${result.status}).\n` +
        `stdout: ${result.stdout}\nstderr: ${result.stderr}`,
    );
  }
  return normalizeXml(result.stdout);
}

test.describe.serial("transcribe -> edit -> undo -> export journey", () => {
  let page: Page;
  let projectId: string;
  let originalPitchDisplay: string;
  let editedPitchDisplay: string;
  let originalMidiPitch: number;
  let editedMidiPitch: number;
  let keyString: string;
  let fixtureWavPath: string;
  let workDir: string;

  test.beforeAll(async ({ browser }) => {
    // Read lazily inside beforeAll (not at describe-body/module scope) so
    // `playwright test --list` can still statically enumerate this file
    // without globalSetup having run — globalSetup is what actually sets
    // these env vars, and `--list` skips it.
    ({ fixtureWavPath, workDir } = readFixtureContext());
    page = await browser.newPage();
  });

  test.afterAll(async () => {
    await page.close();
  });

  test("uploads audio and completes transcription", async () => {
    await page.goto("/");

    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(fixtureWavPath);

    await expect(page.getByText("Which instrument is this?")).toBeVisible();

    const createResponsePromise = page.waitForResponse(
      (resp) => resp.request().method() === "POST" && resp.url().endsWith("/v1/projects"),
    );
    await page.getByRole("button", { name: "Guitar", exact: true }).click();
    const createResponse = await createResponsePromise;
    const created = (await createResponse.json()) as { id: string };
    projectId = created.id;
    expect(projectId).toBeTruthy();

    // Real UI signal, not a sleep: the row's own status chip. `hasActiveJob`
    // polling (projects.ts) refreshes this every second while transcription
    // runs, so this settles the instant the backend job actually finishes.
    await expect(page.locator(".chip-success")).toBeVisible({ timeout: TRANSCRIBE_TIMEOUT_MS });
    await expect(page.locator(".chip-success")).toHaveText("Transcribed");
  });

  test("opens the score and renders notation", async () => {
    const row = page.locator(".row.clickable");
    await expect(row).toHaveCount(1);
    await row.click();

    await expect(page).toHaveURL(new RegExp(`#/project/${projectId}$`));
    // OSMD renders into `.notation-host` as a real SVG — presence of the
    // SVG element is the render-complete signal (see Notation.svelte:
    // `loadMusicXml()` awaits `osmd.load()`/`osmd.render()` before this
    // exists at all).
    await expect(page.locator(".notation-host svg")).toBeVisible({ timeout: RENDER_TIMEOUT_MS });
  });

  test("selects a note via a real canvas click", async () => {
    // A real click on an actual rendered notehead (VexFlow's own
    // `vf-notehead` class — confirmed present in the installed OSMD
    // 2.1.2 bundle) rather than a hardcoded pixel offset, so the click
    // lands on genuine note geometry regardless of layout/zoom.
    // `.first()` assumes DOM order == onset order (VexFlow draws
    // left-to-right, so the earliest-onset notehead is first in the DOM) —
    // holds for this fixture's single-voice notation staff (voice 1 only,
    // even where basic-pitch's harmonic detection produces same-onset
    // chord tones — those just tie for "first," not break the ordering).
    // Revisit if the fixture ever gains a second voice, where DOM emission
    // order across voices is not verified to track onset order.
    const notehead = page.locator(".notation-host .vf-notehead").first();
    await expect(notehead).toBeVisible({ timeout: RENDER_TIMEOUT_MS });
    await notehead.click();

    // Selection landing is asserted two ways: the Inspector panel appears
    // (Sidebar.svelte only renders `.inspector` when a note is selected),
    // and the click-to-select amber highlight overlay is drawn at the
    // clicked note's own position (Notation.svelte's `.event-highlight`,
    // driven by `editor.selectedEventId` through `highlightEvent()`).
    await expect(page.locator(".inspector")).toBeVisible();
    await expect(page.locator(".event-highlight")).toBeVisible();

    const pitchRow = page.locator(".inspector-row").filter({ has: page.locator(".inspector-label", { hasText: "Pitch" }) });
    originalPitchDisplay = (await pitchRow.locator(".stepper-value").innerText()).trim();
    originalMidiPitch = parseDisplayedPitch(originalPitchDisplay);

    const keySelect = page.locator('select[aria-label="Key"]');
    keyString = await keySelect.inputValue();
  });

  test("pitch-up edit applies and settles", async () => {
    editedMidiPitch = clampPitch(originalMidiPitch + 1);
    editedPitchDisplay = pitchToName(editedMidiPitch);

    await page.getByLabel("Pitch up a semitone").click();

    // The "Updating notation…" pill (`.updating-hint`) is `editor.updating`
    // made visible — it may come and go faster than this assertion can
    // observe it appear on a fast local rederive, so the reliable signal is
    // its eventual absence (rederive job settled), not a forced "must have
    // been seen visible" step.
    await expect(page.locator(".updating-hint")).toBeHidden({ timeout: SETTLE_TIMEOUT_MS });

    const pitchRow = page.locator(".inspector-row").filter({ has: page.locator(".inspector-label", { hasText: "Pitch" }) });
    await expect(pitchRow.locator(".stepper-value")).toHaveText(editedPitchDisplay);
  });

  test("undo reverts the pitch, redo re-applies it", async () => {
    const pitchRow = page.locator(".inspector-row").filter({ has: page.locator(".inspector-label", { hasText: "Pitch" }) });

    await page.getByRole("button", { name: "Undo", exact: true }).click();
    await expect(page.locator(".updating-hint")).toBeHidden({ timeout: SETTLE_TIMEOUT_MS });
    await expect(pitchRow.locator(".stepper-value")).toHaveText(originalPitchDisplay);

    await page.getByRole("button", { name: "Redo", exact: true }).click();
    await expect(page.locator(".updating-hint")).toBeHidden({ timeout: SETTLE_TIMEOUT_MS });
    await expect(pitchRow.locator(".stepper-value")).toHaveText(editedPitchDisplay);
  });

  test("exports MusicXML reflecting the edited pitch", async () => {
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "Export MusicXML", exact: true }).click();
    const download = await downloadPromise;

    const downloadedPath = path.join(workDir, "exported.musicxml");
    await download.saveAs(downloadedPath);
    const downloadedText = fs.readFileSync(downloadedPath, "utf-8");
    const normalizedDownloaded = normalizeXml(downloadedText);

    const oraclePath = path.join(workDir, "oracle.musicxml");
    const expectedFragment = expectedPitchXmlFragment(editedMidiPitch, keyString, oraclePath);

    expect(normalizedDownloaded).toContain(expectedFragment);
  });
});
