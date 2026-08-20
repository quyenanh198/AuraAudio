// Client-side prevalidation for the YouTube import affordance on Home.
// Mirrors apps/api/src/aura_api/routers/imports.py's `_ALLOWED_HOSTS`
// exactly. This is prevalidation only -- POST /v1/imports/youtube
// re-validates independently server-side and is the source of truth; a
// client bypass here can only reach the same backend 422, never skip it.

const ALLOWED_HOSTS = new Set([
  "youtube.com",
  "www.youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
]);

/** True for an http(s) URL whose PARSED hostname is one of the allowed
 * YouTube hosts. Checked on `URL.hostname` (post userinfo/port stripping),
 * never a substring of the raw string, so `https://youtube.com@evil.com/...`
 * -- whose real hostname is `evil.com` -- is rejected the same way the
 * backend rejects it. */
export function isYoutubeUrl(raw: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return false;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
  return ALLOWED_HOSTS.has(parsed.hostname.toLowerCase());
}

/** Best-effort human-readable fallback title for when the backend didn't
 * return one (yt-dlp's `--print` metadata can come back empty). Extracts
 * the video id when the URL shape makes that cheap; otherwise a generic
 * label. Never throws -- an unparseable `raw` (shouldn't happen, since
 * this is only called after `isYoutubeUrl` passes) still returns a label. */
export function defaultYoutubeTitle(raw: string): string {
  try {
    const parsed = new URL(raw);
    const id =
      parsed.hostname.toLowerCase() === "youtu.be"
        ? parsed.pathname.replace(/^\//, "")
        : parsed.searchParams.get("v");
    return id ? `YouTube import (${id})` : "YouTube import";
  } catch {
    return "YouTube import";
  }
}
