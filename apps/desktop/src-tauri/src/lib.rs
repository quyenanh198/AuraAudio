mod backend;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .manage(backend::BackendProcess::default())
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      // Spawn the bundled backend synchronously, here on the main/
      // event-loop thread. This is required (not just convenient): the
      // orphan guard for a hard `kill -9` of this process (see
      // `backend::spawn_backend_process`'s doc comment) registers a Linux
      // parent-death signal bound to whichever OS thread calls fork() —
      // that thread must live for the app's whole lifetime, which only the
      // main thread is guaranteed to do. `Command::spawn()` itself is
      // non-blocking (it only waits for `fork`+`exec`, not for the child to
      // become healthy), so this does not stall window creation.
      if backend::spawn_backend_process(app.handle()) {
        // Health polling can take up to `HEALTH_TIMEOUT`, so *that* part
        // does run on a background thread, to keep the event loop
        // responsive. The main window is created with `visible: false`
        // (see tauri.conf.json); this thread is responsible for showing it
        // once the backend answers /healthz, or surfacing an error if it
        // doesn't.
        let app_handle = app.handle().clone();
        std::thread::spawn(move || {
          backend::poll_health_and_gate_window(&app_handle);
        });
      }
      // else: spawn_backend_process already emitted the failure event and
      // shown the main window itself.

      Ok(())
    })
    // `.build(context)?.run(callback)` instead of the `.run(context)`
    // shortcut, because we need the `RunEvent` callback to terminate the
    // sidecar on app exit — `Builder::run(context)` (see
    // `tauri-2.11.5/src/app.rs:2449`) is a no-callback convenience wrapper
    // that can't express that. `RunEvent::Exit` (Tauri's unconditional,
    // final teardown event, fired after any pending `ExitRequested` has
    // resolved) is the normal-quit hook. `RunEvent::ExitRequested` is
    // deliberately NOT used here even though nothing today calls its
    // `prevent_exit()` cancellation hook: `Exit` is the semantically
    // correct "this is really happening" event to tie process teardown to.
    // Neither covers a hard `kill -9` of this process (no Rust code runs at
    // all for SIGKILL); that path is handled independently in `backend.rs`
    // via a Linux `PR_SET_PDEATHSIG` registration made in the child itself
    // at spawn time. See `backend::shutdown_backend` and
    // `backend::spawn_backend_process` for the full reasoning, confirmed
    // against real process-inspection testing in task-5-report.md.
    .build(tauri::generate_context!())
    .expect("error while building tauri application")
    .run(|app_handle, event| {
      if let tauri::RunEvent::Exit = event {
        backend::shutdown_backend(app_handle);
      }
    });
}
