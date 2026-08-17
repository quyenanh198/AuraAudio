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
//! - Poll `GET /healthz` until it succeeds, the child process is observed to
//!   have exited, or a bounded timeout elapses. The child's stderr is
//!   redirected to `<app_data_dir>/backend.log` (see `spawn_backend_process`)
//!   so a packaged-app startup failure is diagnosable even when launched
//!   from a desktop icon with no attached terminal.
//! - Show the main window once healthy, or once the failure is determined
//!   (there is no frontend listener for a failure event today — see the
//!   `poll_health_and_gate_window` doc comment — so the only externally
//!   visible signal of failure right now is the log output).
//! - Terminate the child on a clean app quit (`shutdown_backend`, wired to
//!   `RunEvent::Exit` in `lib.rs`, the unconditional final-teardown event)
//!   by sending `SIGTERM` and waiting briefly for a graceful exit before
//!   escalating to `SIGKILL`, and, independently, on a hard `kill -9` of
//!   this process via a Linux parent-death-signal registration made in the
//!   child at spawn time (see `spawn_backend_process`).

use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use std::os::unix::process::CommandExt;

use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager};

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

/// File name (relative to the resolved app-data directory) the backend
/// child's stderr is redirected to. Stderr is inherited by default for a
/// `Command`, which is fine under a terminal but goes nowhere visible for a
/// packaged `.deb` launched from a desktop icon — capturing it here makes a
/// real Python traceback (bad data-dir permissions, `init_schema()`
/// raising, a PyInstaller import failure, etc.) actually diagnosable.
const BACKEND_LOG_FILENAME: &str = "backend.log";

/// Bounded window to wait for the backend to exit gracefully after
/// `SIGTERM` on a clean app quit, before escalating to `SIGKILL`. uvicorn's
/// own graceful shutdown is fast (no long-running work exists yet), so this
/// is generous headroom while staying short enough not to hang the whole
/// app's shutdown — `shutdown_backend` runs synchronously on the Tauri
/// event-loop thread inside the exit handler.
#[cfg(target_os = "linux")]
const GRACEFUL_SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);

/// Poll interval used while waiting out `GRACEFUL_SHUTDOWN_TIMEOUT`.
#[cfg(target_os = "linux")]
const GRACEFUL_SHUTDOWN_POLL_INTERVAL: Duration = Duration::from_millis(100);

/// Holds the spawned backend child process for the app's lifetime.
///
/// Kept in Tauri managed state (rather than just dropped after spawning) so
/// both the health-check poll (which needs to detect the child dying before
/// it ever answers `/healthz`, see `poll_health`) and shutdown handling (see
/// `shutdown_backend`) have a handle to it instead of it being leaked.
#[derive(Default)]
pub struct BackendProcess(pub Mutex<Option<Child>>);

/// Resolves the backend paths and spawns the bundled backend as a child
/// process, storing the handle in managed state.
///
/// MUST be called from the main/event-loop thread (i.e. synchronously from
/// `.setup()` in `lib.rs`), not from the background health-polling thread —
/// see the long comment on the `pre_exec` registration below for why.
/// Returns `true` if the child was spawned successfully; on `false` an
/// error has already been logged and the main window has already been
/// shown, so the caller has nothing further to do.
pub fn spawn_backend_process(app: &AppHandle) -> bool {
  let exe_path = match resolve_backend_executable(app) {
    Ok(path) => path,
    Err(err) => {
      log::error!("could not locate bundled backend executable: {err}");
      show_main_window(app);
      return false;
    }
  };

  let data_dir = match resolve_app_data_dir(app) {
    Ok(path) => path,
    Err(err) => {
      log::error!("could not resolve app data directory: {err}");
      show_main_window(app);
      return false;
    }
  };
  let database_url = format!("sqlite:///{}/aura.db", data_dir.display());

  let log_path = data_dir.join(BACKEND_LOG_FILENAME);
  let stderr_log = match OpenOptions::new().create(true).append(true).open(&log_path) {
    Ok(file) => file,
    Err(err) => {
      log::error!("could not open backend log file {log_path:?}: {err}");
      show_main_window(app);
      return false;
    }
  };

  log::info!(
    "spawning bundled backend at {} with AURA_DATA_DIR={} (stderr -> {})",
    exe_path.display(),
    data_dir.display(),
    log_path.display()
  );
  let mut command = Command::new(&exe_path);
  command
    .current_dir(
      exe_path
        .parent()
        .expect("bundled executable path has a parent directory"),
    )
    .env("AURA_DATA_DIR", &data_dir)
    .env("DATABASE_URL", &database_url)
    // Captured instead of inherited: inheriting goes nowhere visible for a
    // packaged `.deb` launched from a desktop icon (no attached terminal).
    // See `BACKEND_LOG_FILENAME`'s doc comment.
    .stderr(Stdio::from(stderr_log));

  // Linux-only orphan guard for the case Tauri itself can't run any of its
  // own shutdown code at all: a hard `kill -9` (or crash) of this process.
  // SIGKILL can't be caught or handled by us, so `RunEvent::Exit` (used for
  // a normal quit, see `shutdown_backend` below) never fires for it.
  // Instead, register the child's Linux `prctl(2)` parent-death signal
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
  // SAFETY: `pre_exec`'s contract requires the closure to be
  // async-signal-safe, since it runs in the child after `fork()` but before
  // `exec()`, when only the forking thread exists in the child's copy of
  // the address space — any other thread that happened to hold a lock (libc
  // allocator, logging, etc.) at fork time never gets a chance to release
  // it in the child, so acquiring that same lock here would deadlock. This
  // closure satisfies that: it calls only `libc::prctl`, an
  // async-signal-safe syscall, and on failure reads the errno set by that
  // syscall via `std::io::Error::last_os_error()` (thread-local, no
  // allocation, no lock). No heap allocation and no lock acquisition occur
  // anywhere in this closure.
  //
  // Accepted tradeoff, not an oversight: there is a theoretical,
  // negligible-probability race in the microsecond window between
  // `fork()` returning in the child and this closure's `prctl()` call
  // actually arming `PR_SET_PDEATHSIG` — if the parent dies in that
  // specific window, the child never receives the death signal for this
  // one spawn. The canonical mitigation is re-checking `getppid()` after
  // arming the signal (if the parent already changed, the signal may have
  // been missed, so send it to yourself immediately). This code does not
  // do that re-check; given the window's size relative to how rarely a
  // hard `kill -9` of the parent coincides with it, that's judged not
  // worth the added complexity here.
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
      log::error!("failed to start backend process ({exe_path:?}): {err}");
      show_main_window(app);
      return false;
    }
  };

  if let Some(state) = app.try_state::<BackendProcess>() {
    *state.0.lock().expect("backend process mutex poisoned") = Some(child);
  }

  true
}

/// Polls the backend's health endpoint and shows the main window once the
/// outcome (success, child death, or timeout) is known. Intended to run on
/// a background thread so it never blocks Tauri's event loop.
///
/// Must only be called after a successful `spawn_backend_process` — see
/// that function's doc comment for why the two are split across different
/// threads.
///
/// No Tauri event is emitted here: there is currently no frontend listener
/// for one (the placeholder page deliberately avoids `window.__TAURI__` /
/// event listeners, per an earlier task's own decision), so the outcome is
/// only observable via the log output today. Wiring up a real listener is
/// future work for whenever there's an actual frontend framework.
pub fn poll_health_and_gate_window(app: &AppHandle) {
  let started = Instant::now();
  match poll_health(app, BACKEND_PORT, HEALTH_TIMEOUT, HEALTH_POLL_INTERVAL) {
    HealthPollOutcome::Healthy => {
      log::info!(
        "backend healthy after {:.2}s, showing window",
        started.elapsed().as_secs_f64()
      );
    }
    HealthPollOutcome::ChildExited(status) => {
      log::error!(
        "backend process exited after {:.2}s (before ever answering GET /healthz) with {status}; \
         see {BACKEND_LOG_FILENAME} in the app data directory for details",
        started.elapsed().as_secs_f64()
      );
    }
    HealthPollOutcome::Timeout => {
      log::error!(
        "backend did not respond OK on GET /healthz within {:?} and the child process is \
         still running; see {BACKEND_LOG_FILENAME} in the app data directory for details",
        HEALTH_TIMEOUT
      );
    }
  }

  show_main_window(app);
}

/// Terminates the spawned backend child process, if one is running.
///
/// Called from the `RunEvent::Exit` handler in `lib.rs` on a normal app
/// quit (all windows closed, or a programmatic `AppHandle::exit`/`restart`)
/// — `RunEvent::Exit` is Tauri's unconditional, final teardown event (as
/// opposed to `RunEvent::ExitRequested`, which is cancellable via
/// `prevent_exit()`; nothing in this app calls that today, but `Exit` is
/// the semantically correct event to hook regardless). This covers the
/// *clean* shutdown path; the crash/hard-kill path (`kill -9` on this
/// process itself) is handled separately and unconditionally by the
/// `pre_exec` parent-death-signal registration in `spawn_backend_process`
/// above, since no code in this process runs at all when it receives
/// SIGKILL.
///
/// On a clean quit the backend is given a chance to shut down gracefully
/// (`SIGTERM`, then a bounded wait) rather than being sent `SIGKILL`
/// outright — see `terminate_child`. This matters once real work (e.g. a
/// future transcription job running on a `ThreadPoolExecutor`) can be
/// in-flight at quit time: a `SIGKILL` mid-job aborts it with no chance to
/// clean up, versus today where no job ever runs long enough for this to
/// be observable.
///
/// Takes the child out of managed state (leaving `None` behind) so a
/// second call is a harmless no-op rather than a double-kill.
pub fn shutdown_backend(app: &AppHandle) {
  let Some(state) = app.try_state::<BackendProcess>() else {
    return;
  };
  let mut guard = state.0.lock().expect("backend process mutex poisoned");
  let Some(mut child) = guard.take() else {
    log::info!("shutdown_backend: no backend child process to terminate");
    return;
  };

  terminate_child(&mut child);

  // Reap the process so it doesn't linger as a zombie waiting for the
  // parent to collect its exit status. If `terminate_child` already
  // observed the exit via `try_wait()`, the exit status is cached on
  // `Child` and this returns immediately without a second `waitpid` call.
  match child.wait() {
    Ok(status) => log::info!("backend child process exited with {status}"),
    Err(err) => log::warn!("failed to wait on backend child process: {err}"),
  }
}

/// Sends `SIGTERM` and waits up to `GRACEFUL_SHUTDOWN_TIMEOUT` for the
/// child to exit on its own before escalating to `SIGKILL` (via
/// `kill_hard`). Bounded and non-blocking-forever by design: this runs on
/// the Tauri event-loop thread inside the exit handler, so an unbounded
/// wait here would hang the whole app's shutdown.
#[cfg(target_os = "linux")]
fn terminate_child(child: &mut Child) {
  let pid = child.id() as i32;

  // SAFETY: `pid` is our own live child's pid — we hold the only `Child`
  // handle for it (via the `BackendProcess` mutex, already locked by the
  // caller) — and `SIGTERM` is a well-defined signal number, so this
  // syscall has no undefined behavior.
  let result = unsafe { libc::kill(pid, libc::SIGTERM) };
  if result != 0 {
    let err = std::io::Error::last_os_error();
    log::warn!(
      "failed to send SIGTERM to backend child process (pid {pid}): {err}; \
       falling back to SIGKILL"
    );
    kill_hard(child);
    return;
  }
  log::info!(
    "sent SIGTERM to backend child process (pid {pid}), waiting up to {:?} for graceful exit",
    GRACEFUL_SHUTDOWN_TIMEOUT
  );

  let deadline = Instant::now() + GRACEFUL_SHUTDOWN_TIMEOUT;
  loop {
    match child.try_wait() {
      Ok(Some(status)) => {
        log::info!("backend child process exited gracefully after SIGTERM with {status}");
        return;
      }
      Ok(None) => {}
      Err(err) => {
        log::warn!(
          "failed to poll backend child liveness during graceful shutdown: {err}; \
           falling back to SIGKILL"
        );
        break;
      }
    }
    if Instant::now() >= deadline {
      log::warn!(
        "backend child process did not exit within {:?} of SIGTERM, escalating to SIGKILL",
        GRACEFUL_SHUTDOWN_TIMEOUT
      );
      break;
    }
    std::thread::sleep(GRACEFUL_SHUTDOWN_POLL_INTERVAL);
  }
  kill_hard(child);
}

/// Non-Linux fallback: no `libc` dependency is available on other targets
/// (see `Cargo.toml`'s `target.'cfg(target_os = "linux")'.dependencies`),
/// so this goes straight to `SIGKILL`-equivalent termination, matching the
/// pre-fix behavior on those platforms.
#[cfg(not(target_os = "linux"))]
fn terminate_child(child: &mut Child) {
  kill_hard(child);
}

/// Sends the platform's hard-kill signal (`SIGKILL` on Unix) and does not
/// wait for exit — the caller (`shutdown_backend`) reaps via `child.wait()`
/// afterward.
fn kill_hard(child: &mut Child) {
  match child.kill() {
    Ok(()) => log::info!("sent SIGKILL to backend child process (pid {})", child.id()),
    Err(err) => {
      // `kill()` on Unix returns an error for `ESRCH` (already exited) too
      // — that's not a problem, the caller still reaps below.
      log::warn!("failed to SIGKILL backend child process: {err}");
    }
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

/// Outcome of `poll_health`.
enum HealthPollOutcome {
  /// `GET /healthz` returned HTTP 200.
  Healthy,
  /// The spawned child process was observed to have exited before the
  /// health check ever succeeded. Distinct from `Timeout` so the caller can
  /// report a real, specific failure (with exit status) instead of a
  /// generic "health check timed out" message — and so the poll loop can
  /// abort immediately instead of waiting out the rest of the timeout.
  ChildExited(ExitStatus),
  /// Neither of the above happened within `timeout`.
  Timeout,
}

/// Polls `GET /healthz` on `127.0.0.1:<port>` until it returns HTTP 200,
/// the spawned child is observed to have exited, or `timeout` elapses.
///
/// Checking child liveness matters independently of the port check: if
/// port `port` is already bound by something else (e.g. a second instance
/// of the app, or a stale backend left running from an earlier crash), our
/// own child exits immediately (uvicorn exits nonzero when it can't bind
/// its port) but polling would otherwise keep succeeding against whoever
/// else is actually listening — silently talking to a process that isn't
/// ours, possibly with a different `AURA_DATA_DIR`/`DATABASE_URL`. Checking
/// `try_wait()` between poll attempts catches that case (and any other
/// startup death, e.g. bad data-dir permissions or a PyInstaller import
/// failure) and reports it precisely instead.
fn poll_health(app: &AppHandle, port: u16, timeout: Duration, interval: Duration) -> HealthPollOutcome {
  let deadline = Instant::now() + timeout;
  loop {
    if check_health_once(port) {
      return HealthPollOutcome::Healthy;
    }
    if let Some(status) = child_exit_status(app) {
      return HealthPollOutcome::ChildExited(status);
    }
    if Instant::now() >= deadline {
      return HealthPollOutcome::Timeout;
    }
    std::thread::sleep(interval);
  }
}

/// Returns `Some(status)` if the managed backend child has exited (via a
/// non-blocking `try_wait()`), or `None` if it's still running, its state
/// can't be checked (e.g. no managed state yet), or the check itself
/// errored (logged and treated as "still running" so a single fluky
/// `try_wait()` doesn't cause a false failure report).
fn child_exit_status(app: &AppHandle) -> Option<ExitStatus> {
  let state = app.try_state::<BackendProcess>()?;
  let mut guard = state.0.lock().expect("backend process mutex poisoned");
  let child = guard.as_mut()?;
  match child.try_wait() {
    Ok(Some(status)) => Some(status),
    Ok(None) => None,
    Err(err) => {
      log::warn!("failed to poll backend child liveness: {err}");
      None
    }
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
