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
//!   Spawning (`spawn_backend_process`) happens synchronously on the main/
//!   event-loop thread; only the health polling and window-showing that
//!   follow (`poll_health_and_gate_window`) run on a background thread. See
//!   `spawn_backend_process`'s doc comment for why that split matters.
//! - Poll `GET /healthz` until it succeeds or a bounded timeout elapses.
//! - Show the main window once healthy, or emit an explicit failure event
//!   the placeholder page renders as an error state, per the brief's Step 3.
//! - Terminate the child on a clean app quit (`shutdown_backend`, wired to
//!   `RunEvent::ExitRequested` in `lib.rs`) and, independently, on a hard
//!   `kill -9` of this process via a Linux parent-death-signal registration
//!   made in the child at spawn time (see `spawn_backend_process`).

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use std::os::unix::process::CommandExt;

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

/// Resolves the backend paths and spawns the bundled backend as a child
/// process, storing the handle in managed state.
///
/// MUST be called from the main/event-loop thread (i.e. synchronously from
/// `.setup()` in `lib.rs`), not from the background health-polling thread —
/// see the long comment on the `pre_exec` registration below for why.
/// Returns `true` if the child was spawned successfully; on `false` an
/// error has already been logged, emitted to the frontend, and the main
/// window has already been shown, so the caller has nothing further to do.
pub fn spawn_backend_process(app: &AppHandle) -> bool {
  let exe_path = match resolve_backend_executable(app) {
    Ok(path) => path,
    Err(err) => {
      log::error!("could not locate bundled backend executable: {err}");
      let _ = app.emit("backend-health-failed", err);
      show_main_window(app);
      return false;
    }
  };

  let data_dir = match resolve_app_data_dir(app) {
    Ok(path) => path,
    Err(err) => {
      log::error!("could not resolve app data directory: {err}");
      let _ = app.emit("backend-health-failed", err);
      show_main_window(app);
      return false;
    }
  };
  let database_url = format!("sqlite:///{}/aura.db", data_dir.display());

  log::info!(
    "spawning bundled backend at {} with AURA_DATA_DIR={}",
    exe_path.display(),
    data_dir.display()
  );
  let mut command = Command::new(&exe_path);
  command
    .current_dir(
      exe_path
        .parent()
        .expect("bundled executable path has a parent directory"),
    )
    .env("AURA_DATA_DIR", &data_dir)
    .env("DATABASE_URL", &database_url);

  // Linux-only orphan guard for the case Tauri itself can't run any of its
  // own shutdown code at all: a hard `kill -9` (or crash) of this process.
  // SIGKILL can't be caught or handled by us, so `RunEvent::ExitRequested`
  // (used for a normal quit, see `shutdown_backend` below) never fires for
  // it. Instead, register the child's Linux `prctl(2)` parent-death signal
  // *in the child itself* (via `pre_exec`, which runs post-fork/pre-exec in
  // the child) so the kernel — not our process — sends the child SIGTERM
  // the moment its parent thread dies, including via SIGKILL. See
  // `PR_SET_PDEATHSIG` in
  // `libc-0.2.189/src/unix/linux_like/linux_l4re_shared.rs:1036`.
  //
  // Documented gotcha, CONFIRMED BY REAL TESTING for this task (see
  // task-5-report.md): the "parent" `prctl` tracks is the specific OS
  // *thread* that called fork(), not the process as a whole. An earlier
  // version of this function ran on the short-lived background
  // health-polling thread; real `ps aux` output showed the backend being
  // killed and left as a `<defunct>` zombie ~1-2s after a *successful*
  // health check, as soon as that thread returned — while the app process
  // itself was still very much alive. Moving the actual `spawn()` call to
  // the main/event-loop thread (which only ever dies together with the
  // whole process) fixed it; only the health-polling and window-showing
  // that don't need fork-thread longevity now run on the background
  // thread (see `poll_health_and_gate_window`).
  #[cfg(target_os = "linux")]
  unsafe {
    command.pre_exec(|| {
      if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) != 0 {
        return Err(std::io::Error::last_os_error());
      }
      Ok(())
    });
  }

  let spawn_result = command.spawn();

  let child = match spawn_result {
    Ok(child) => child,
    Err(err) => {
      let message = format!("failed to start backend process ({exe_path:?}): {err}");
      log::error!("{message}");
      let _ = app.emit("backend-health-failed", message);
      show_main_window(app);
      return false;
    }
  };

  if let Some(state) = app.try_state::<BackendProcess>() {
    *state.0.lock().expect("backend process mutex poisoned") = Some(child);
  }

  true
}

/// Polls the backend's health endpoint and shows (or reports failure for)
/// the main window. Intended to run on a background thread so it never
/// blocks Tauri's event loop.
///
/// Must only be called after a successful `spawn_backend_process` — see
/// that function's doc comment for why the two are split across different
/// threads.
pub fn poll_health_and_gate_window(app: &AppHandle) {
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

/// Terminates the spawned backend child process, if one is running.
///
/// Called from the `RunEvent::ExitRequested` handler in `lib.rs` on a
/// normal app quit (all windows closed, or a programmatic
/// `AppHandle::exit`/`restart`). This covers the *clean* shutdown path;
/// the crash/hard-kill path (`kill -9` on this process itself) is handled
/// separately and unconditionally by the `pre_exec` parent-death-signal
/// registration in `spawn_backend_process` above, since no code in this
/// process runs at all when it receives SIGKILL.
///
/// Takes the child out of managed state (leaving `None` behind) so a
/// second call — e.g. if both `ExitRequested` and `Exit` end up wired to
/// this in the future — is a harmless no-op rather than a double-kill.
pub fn shutdown_backend(app: &AppHandle) {
  let Some(state) = app.try_state::<BackendProcess>() else {
    return;
  };
  let mut guard = state.0.lock().expect("backend process mutex poisoned");
  let Some(mut child) = guard.take() else {
    log::info!("shutdown_backend: no backend child process to terminate");
    return;
  };

  match child.kill() {
    Ok(()) => log::info!("sent kill signal to backend child process (pid {})", child.id()),
    Err(err) => {
      // `kill()` on Unix returns an error for `ESRCH` (already exited) too
      // — that's not a problem, just log it and still reap below.
      log::warn!("failed to signal backend child process: {err}");
    }
  }

  // Reap the process so it doesn't linger as a zombie waiting for the
  // parent to collect its exit status.
  match child.wait() {
    Ok(status) => log::info!("backend child process exited with {status}"),
    Err(err) => log::warn!("failed to wait on backend child process: {err}"),
  }
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

/// Resolves the real, per-OS app-data directory for the current platform
/// (e.g. `~/.local/share/com.auraaudio.desktop` on Linux, via `app.path()`'s
/// `app_data_dir()` — confirmed against `tauri-2.11.5/src/path/desktop.rs:247`,
/// which resolves to `dirs::data_dir()/${bundle_identifier}`; the identifier
/// is `com.auraaudio.desktop` per `tauri.conf.json`), creating it (and any
/// missing parents) if it doesn't already exist so the child process can rely
/// on it being present the moment it starts.
fn resolve_app_data_dir(app: &AppHandle) -> Result<PathBuf, String> {
  let dir = app
    .path()
    .app_data_dir()
    .map_err(|err| format!("could not resolve app data directory: {err}"))?;

  std::fs::create_dir_all(&dir)
    .map_err(|err| format!("could not create app data directory {dir:?}: {err}"))?;

  Ok(dir)
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
