//! Sidecar management for the bundled AuraAudio FastAPI backend.
//!
//! Responsibilities:
//! - Resolve the on-disk path to the PyInstaller-bundled `aura-backend`
//!   executable via Tauri's resource resolver (`BaseDirectory::Resource`).
//!   This works for both `tauri build` and `tauri dev`/`cargo build`:
//!   `tauri-build`'s `build.rs` step (see `tauri-build-2.6.3/src/lib.rs`,
//!   `copy_resources`) copies `bundle.resources` next to the compiled
//!   binary on *every* compile, not just `tauri build`'s bundler pass, and
//!   `resource_dir()`'s Linux implementation
//!   (`tauri-utils-2.9.3/src/platform.rs`, `resource_dir_from`) explicitly
//!   detects a Cargo output directory (a `target/<profile>/` dir containing
//!   `.cargo-lock`) and returns the executable's own directory in that case
//!   — so the resolved resource path in dev mode is
//!   `target/debug/aura-backend/aura-backend`, confirmed by real `ps aux`
//!   output during this task's `tauri dev` verification run. A path under
//!   `src-tauri/resources/aura-backend/` (the directory `build-backend.sh`
//!   stages into, read via `CARGO_MANIFEST_DIR`) is kept as a defensive
//!   fallback in case that staging step is ever bypassed, though in
//!   practice `build.rs` fails the compile outright if the resource source
//!   files are missing, so this fallback is not expected to be exercised
//!   during a normal `cargo build`/`tauri dev`/`tauri build`.
//! - Spawn it as a child process on the fixed port baked into the bundle
//!   (`apps/desktop/run_backend.py` hardcodes 8317 — no CLI flag needed).
//! - Poll `GET /healthz` until it succeeds or a bounded timeout elapses.
//! - Show the main window once healthy, or emit an explicit failure event
//!   the placeholder page renders as an error state, per the brief's Step 3.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::path::BaseDirectory;
use tauri::{AppHandle, Emitter, Manager};

/// Fixed port the bundled backend listens on (locked in
/// `apps/desktop/run_backend.py`, see that file's module docstring).
const BACKEND_PORT: u16 = 8317;

/// Interval between health-check polls.
const HEALTH_POLL_INTERVAL: Duration = Duration::from_millis(200);

/// Total time budget for the backend to answer `/healthz`.
///
/// Task 1 observed ~1.35s real boot time to a reachable `/healthz` (tensorflow
/// / basic_pitch are lazy-imported and not touched on that path). 30s gives
/// roughly 22x that observed number as headroom for a slower host or a cold
/// filesystem cache, while staying well short of the ~24.7s cold-inference
/// number, which is a separate concern this task does not need to cover.
const HEALTH_TIMEOUT: Duration = Duration::from_secs(30);

/// Holds the spawned backend child process for the app's lifetime.
///
/// Kept in Tauri managed state (rather than just dropped after spawning) so
/// a later task (shutdown handling, per the plan) has a handle to terminate
/// the process on app exit instead of leaking it.
#[derive(Default)]
pub struct BackendProcess(pub Mutex<Option<Child>>);

/// Spawns the bundled backend, polls its health endpoint, and shows (or
/// reports failure for) the main window. Intended to run on a background
/// thread so it never blocks Tauri's event loop.
pub fn spawn_backend_and_gate_window(app: &AppHandle) {
  let exe_path = match resolve_backend_executable(app) {
    Ok(path) => path,
    Err(err) => {
      log::error!("could not locate bundled backend executable: {err}");
      let _ = app.emit("backend-health-failed", err);
      show_main_window(app);
      return;
    }
  };

  log::info!("spawning bundled backend at {}", exe_path.display());
  let spawn_result = Command::new(&exe_path)
    .current_dir(
      exe_path
        .parent()
        .expect("bundled executable path has a parent directory"),
    )
    .spawn();

  let child = match spawn_result {
    Ok(child) => child,
    Err(err) => {
      let message = format!("failed to start backend process ({exe_path:?}): {err}");
      log::error!("{message}");
      let _ = app.emit("backend-health-failed", message);
      show_main_window(app);
      return;
    }
  };

  if let Some(state) = app.try_state::<BackendProcess>() {
    *state.0.lock().expect("backend process mutex poisoned") = Some(child);
  }

  let started = Instant::now();
  if poll_health(BACKEND_PORT, HEALTH_TIMEOUT, HEALTH_POLL_INTERVAL) {
    log::info!(
      "backend healthy after {:.2}s, showing window",
      started.elapsed().as_secs_f64()
    );
    let _ = app.emit("backend-health-ok", ());
  } else {
    let message = format!(
      "backend did not respond OK on GET /healthz within {:?}",
      HEALTH_TIMEOUT
    );
    log::error!("{message}");
    let _ = app.emit("backend-health-failed", message);
  }

  show_main_window(app);
}

/// Resolves the real on-disk path to the bundled `aura-backend` executable.
///
/// `bundle.resources` in tauri.conf.json maps
/// `resources/aura-backend/` -> `aura-backend/`, so the resolved resource
/// path is `<resource_dir>/aura-backend/aura-backend` in both dev and
/// production builds (see the module doc comment for why this resolves
/// correctly under `tauri dev` too, not only `tauri build`).
fn resolve_backend_executable(app: &AppHandle) -> Result<PathBuf, String> {
  if let Ok(resource_path) = app
    .path()
    .resolve("aura-backend/aura-backend", BaseDirectory::Resource)
  {
    if resource_path.exists() {
      return Ok(resource_path);
    }
  }

  // Defensive fallback only — see module doc comment. Reads directly from
  // the staging directory `build-backend.sh` writes to.
  let dev_path =
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources/aura-backend/aura-backend");
  if dev_path.exists() {
    return Ok(dev_path);
  }

  Err(format!(
    "bundled backend executable not found via Tauri resource resolution or at fallback path \
     {dev_path:?}; run apps/desktop/build-backend.sh first"
  ))
}

fn show_main_window(app: &AppHandle) {
  match app.get_webview_window("main") {
    Some(window) => {
      if let Err(err) = window.show() {
        log::error!("failed to show main window: {err}");
      }
    }
    None => log::error!("no window labeled \"main\" to show"),
  }
}

/// Polls `GET /healthz` on `127.0.0.1:<port>` until it returns HTTP 200 or
/// `timeout` elapses.
fn poll_health(port: u16, timeout: Duration, interval: Duration) -> bool {
  let deadline = Instant::now() + timeout;
  loop {
    if check_health_once(port) {
      return true;
    }
    if Instant::now() >= deadline {
      return false;
    }
    std::thread::sleep(interval);
  }
}

/// Performs a single `GET /healthz` check over a raw TCP connection.
///
/// A minimal hand-rolled HTTP/1.1 request is used instead of pulling in an
/// HTTP client crate: this is a same-host, unauthenticated, single-endpoint
/// check, so the extra dependency isn't justified (YAGNI).
fn check_health_once(port: u16) -> bool {
  let addr: SocketAddr = match format!("127.0.0.1:{port}").parse() {
    Ok(addr) => addr,
    Err(_) => return false,
  };

  let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(300)) {
    Ok(stream) => stream,
    Err(_) => return false,
  };
  let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));

  let request =
    format!("GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
  if stream.write_all(request.as_bytes()).is_err() {
    return false;
  }

  let mut response = String::new();
  // Ignore the Result: a read timeout after the peer already sent and
  // closed is common and still leaves `response` fully populated.
  let _ = stream.read_to_string(&mut response);

  response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200")
}
