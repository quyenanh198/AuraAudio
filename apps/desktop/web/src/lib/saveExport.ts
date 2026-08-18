// Native Save dialog for score exports (Tauri desktop). Plain-browser
// downloads (`<a download>`) silently drop into the WebKitGTK process cwd in
// the packaged desktop app — there is no way for a user to discover where
// the file went. This module routes the export through the OS-native save
// dialog instead, when running inside Tauri.
//
// Permission/scope handshake (verified against the installed crate source,
// not just docs): `tauri-plugin-dialog`'s `save` command, once the user
// picks a path, calls `window.try_fs_scope().allow_file(&path)` before
// returning it (tauri-plugin-dialog-2.7.2/src/commands.rs, `save()`). That
// dynamically extends the fs plugin's scope to that exact file for the rest
// of the session — so the capability only needs `dialog:allow-save` (to
// invoke the dialog) and `fs:allow-write-file` (to invoke the write
// command); no static fs scope/directory permission is required, since the
// dialog plugin grants the one path itself at runtime.
//
// The two plugin imports are dynamic and kept INSIDE the isTauri() branch
// so the plain-browser bundle (e.g. `npm run dev` outside the Tauri
// shell, or a future web build) never evaluates `@tauri-apps/plugin-*`,
// which assume a Tauri IPC bridge is present.

import { isTauri as isTauriRuntime } from "@tauri-apps/api/core";

export function isTauri(): boolean {
  return isTauriRuntime();
}

export type SaveExportResult = "saved" | "cancelled" | "fallback";

/**
 * Fetches `url`, then offers the bytes to the user via a native Save
 * dialog (Tauri) with `suggestedName` pre-filled. Outside Tauri, this is a
 * deliberate no-op that returns "fallback" — the caller is expected to keep
 * its existing `<a download>` behavior in that case.
 */
export async function saveExport(url: string, suggestedName: string): Promise<SaveExportResult> {
  if (!isTauri()) {
    return "fallback";
  }

  const [{ save }, { writeFile }] = await Promise.all([
    import("@tauri-apps/plugin-dialog"),
    import("@tauri-apps/plugin-fs"),
  ]);

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Export download failed: ${response.status} ${response.statusText}`);
  }
  const bytes = new Uint8Array(await response.arrayBuffer());

  const extension = suggestedName.includes(".") ? suggestedName.split(".").pop() ?? "" : "";
  const destination = await save({
    defaultPath: suggestedName,
    filters: extension ? [{ name: extension.toUpperCase(), extensions: [extension] }] : [],
  });

  if (destination === null) {
    return "cancelled";
  }

  await writeFile(destination, bytes);
  return "saved";
}
