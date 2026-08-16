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

      // Spawn the bundled backend and poll its health endpoint on a
      // background OS thread so the event loop / window creation is never
      // blocked. The main window is created with `visible: false` (see
      // tauri.conf.json); this thread is responsible for showing it once
      // the backend answers /healthz, or surfacing an error if it doesn't.
      let app_handle = app.handle().clone();
      std::thread::spawn(move || {
        backend::spawn_backend_and_gate_window(&app_handle);
      });

      Ok(())
    })
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
