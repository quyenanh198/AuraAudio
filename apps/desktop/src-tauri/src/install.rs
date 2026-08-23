//! Auto-install for the app's external dependencies (`ffmpeg`, `yt-dlp`),
//! invoked from the frontend's dependency banner.
//!
//! Why this lives in the Tauri shell and not the bundled Python backend:
//! actually running a package-manager install (`winget`, `brew`, `apt`) is a
//! privileged UI action -- it launches another process on the user's
//! machine -- and Tauri v2's IPC/ACL model is the layer meant to gate that
//! kind of thing (see `install_dependency`'s permission, registered via
//! `build.rs`'s `AppManifest` and referenced in `capabilities/default.json`),
//! not a plain HTTP endpoint on the backend's loopback port that anything
//! on the machine could hit.
//!
//! Every command this module can run is a FIXED, hardcoded argv -- `name`
//! (the only caller-supplied input) is matched against a small enum before
//! it ever reaches a `Command`, and none of it is interpolated into the
//! argv itself. There is no shell involved (`Command::new` execs the
//! program directly), so there is no injection surface here regardless of
//! what `name` contains.

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use serde::Serialize;

/// The two dependencies this app knows how to auto-install. Parsed from the
/// frontend's `DepName` string (`apps/desktop/web/src/lib/deps.ts`) --
/// anything else is rejected before any process is spawned.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DependencyName {
  Ffmpeg,
  YtDlp,
}

impl DependencyName {
  fn parse(name: &str) -> Option<Self> {
    match name {
      "ffmpeg" => Some(Self::Ffmpeg),
      "ytDlp" => Some(Self::YtDlp),
      _ => None,
    }
  }
}

/// How `install_dependency` resolved (or didn't) for one call.
#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case")]
enum InstallOutcome {
  /// The install command ran and exited 0.
  Success,
  /// The install command ran and exited non-zero, couldn't be spawned at
  /// all, or didn't finish within `INSTALL_TIMEOUT` and was terminated.
  Failed,
  /// No fixed command exists for this (os, dependency) pair -- e.g.
  /// ffmpeg on Linux, which arrives via the `.deb`'s own `Depends`
  /// (`tauri.conf.json`'s `bundle.linux.deb.depends`) rather than a
  /// runtime install. The frontend falls back to the manual copyable
  /// command in this case.
  Unsupported,
  /// macOS only: Homebrew itself isn't installed, so `brew install ...`
  /// can't even be attempted. Distinct from `Failed` so the frontend can
  /// show "install Homebrew first" guidance instead of a raw failure.
  BrewMissing,
  /// Windows only: `winget` itself isn't on PATH (real on Windows
  /// Server/LTSC editions, which don't ship App Installer by default).
  /// Distinct from `Failed` for the same reason `BrewMissing` is -- a
  /// missing package manager needs different guidance than a package
  /// manager that ran and failed.
  WingetMissing,
}

/// Result returned to the frontend for one `install_dependency` call.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallDependencyResult {
  outcome: InstallOutcome,
  /// The install command's exit code, when one actually ran to natural
  /// completion. `None` for `Unsupported`/`BrewMissing`/`WingetMissing`
  /// (nothing was spawned), a spawn failure (the process never started),
  /// and a timeout (killed before it could exit on its own).
  exit_code: Option<i32>,
  /// Combined stdout+stderr tail (see `OUTPUT_TAIL_CHARS`), or a short
  /// explanatory message for the non-`Command` outcomes. Shown by the
  /// frontend on failure so the user has something more actionable than
  /// "it didn't work".
  output_tail: String,
}

/// Bound on how much of the install command's combined output is kept, so
/// a chatty installer can't balloon the IPC payload back to the frontend.
/// Generous relative to `apps/api/.../routers/imports.py`'s 300-char
/// `_STDERR_TAIL_CHARS` -- an install log is something a user may actually
/// want to read in full to self-diagnose, not just a one-line error.
const OUTPUT_TAIL_CHARS: usize = 4000;

/// Upper bound on how long a single install command is allowed to run
/// before it's treated as hung and killed. Generous -- winget/brew/apt can
/// legitimately take minutes on a slow connection -- but bounded: without
/// this, a stalled installer (e.g. a `pkexec` polkit dialog nobody answers,
/// or a network install that never times out on its own) would leave the
/// frontend's spinner spinning forever, with no way to recover short of
/// restarting the app.
const INSTALL_TIMEOUT: Duration = Duration::from_secs(600);

/// Poll interval while waiting out `INSTALL_TIMEOUT`.
const INSTALL_POLL_INTERVAL: Duration = Duration::from_millis(200);

fn tail(text: &str, max_chars: usize) -> String {
  let char_count = text.chars().count();
  if char_count <= max_chars {
    return text.to_string();
  }
  text.chars().skip(char_count - max_chars).collect()
}

/// Fixed argv for one (dependency) install on the CURRENT platform (the
/// branch is picked by `cfg!`, not by any caller input) -- see the module
/// doc comment for why this must never interpolate anything caller-supplied
/// into the command or its arguments.
#[derive(Debug)]
enum InstallPlan {
  Command { program: &'static str, args: Vec<&'static str> },
  Unsupported(&'static str),
  BrewMissing,
  WingetMissing,
}

fn plan_for(dep: DependencyName) -> InstallPlan {
  if cfg!(windows) {
    windows_plan(dep, command_exists)
  } else if cfg!(target_os = "macos") {
    macos_plan(dep, command_exists)
  } else {
    linux_plan(dep, command_exists)
  }
}

/// `exists` is injected (rather than calling `command_exists` directly) so
/// unit tests can simulate winget/brew/pkexec being absent without
/// depending on the real test machine's PATH -- production always passes
/// the real `command_exists`, via `plan_for` above.
fn windows_plan(dep: DependencyName, exists: impl Fn(&str) -> bool) -> InstallPlan {
  if !exists("winget") {
    return InstallPlan::WingetMissing;
  }
  // `--accept-source-agreements --accept-package-agreements` avoids an
  // interactive prompt winget would otherwise block on with no attached
  // terminal to answer it from.
  match dep {
    DependencyName::Ffmpeg => InstallPlan::Command {
      program: "winget",
      args: vec![
        "install",
        "--id",
        "Gyan.FFmpeg",
        "--accept-source-agreements",
        "--accept-package-agreements",
      ],
    },
    // Package id verified against the real winget-pkgs manifest
    // (microsoft/winget-pkgs, manifests/y/yt-dlp/yt-dlp) -- NOT
    // "yt-dlp" or "yt-dlp.YtDlp", the id winget actually indexes it
    // under is "yt-dlp.yt-dlp".
    DependencyName::YtDlp => InstallPlan::Command {
      program: "winget",
      args: vec![
        "install",
        "--id",
        "yt-dlp.yt-dlp",
        "--accept-source-agreements",
        "--accept-package-agreements",
      ],
    },
  }
}

fn macos_plan(dep: DependencyName, exists: impl Fn(&str) -> bool) -> InstallPlan {
  if !exists("brew") {
    return InstallPlan::BrewMissing;
  }
  let package = match dep {
    DependencyName::Ffmpeg => "ffmpeg",
    DependencyName::YtDlp => "yt-dlp",
  };
  InstallPlan::Command { program: "brew", args: vec!["install", package] }
}

fn linux_plan(dep: DependencyName, exists: impl Fn(&str) -> bool) -> InstallPlan {
  match dep {
    // ffmpeg is NOT installed here on Linux: the `.deb` package already
    // declares it as a hard `Depends` (tauri.conf.json's
    // `bundle.linux.deb.depends`), so a correctly-installed app already
    // has it. There is no single safe, distro-agnostic runtime install
    // command to fall back to (apt/dnf/pacman/... all differ), so this
    // is `Unsupported` -- the frontend keeps its manual copyable
    // command for the rare case (e.g. running from source, not the
    // packaged .deb) where it's still genuinely missing.
    DependencyName::Ffmpeg => InstallPlan::Unsupported(
      "ffmpeg ships via this app's own .deb package dependencies on Linux; \
       it isn't auto-installed at runtime. If you're not running the packaged \
       .deb build, install it with your distro's package manager.",
    ),
    DependencyName::YtDlp => {
      if !exists("pkexec") {
        return InstallPlan::Unsupported(
          "pkexec isn't available, so yt-dlp can't be installed automatically here. \
           Install it with your distro's package manager.",
        );
      }
      InstallPlan::Command {
        program: "pkexec",
        args: vec!["apt-get", "install", "-y", "yt-dlp"],
      }
    }
  }
}

/// Checks whether `name` resolves on PATH, without spawning it -- avoids
/// accidentally triggering a side effect (e.g. `pkexec` popping its
/// polkit auth dialog) just to test for its existence.
fn command_exists(name: &str) -> bool {
  let Some(path_var) = std::env::var_os("PATH") else {
    return false;
  };
  std::env::split_paths(&path_var).any(|dir| dir.join(name).is_file())
}

/// Runs `program args...` to completion or `timeout`, whichever comes
/// first.
///
/// stdout/stderr are drained concurrently on two background threads while
/// this function polls `try_wait()` on the main thread -- reading them only
/// AFTER the process exits (the naive approach) risks a deadlock: if the
/// child writes enough output to fill the OS pipe buffer before exiting, it
/// blocks on that write forever while nothing is reading the other end.
/// This is the same reason the standard library's own `Command::output()`
/// spawns reader threads internally rather than reading post-wait.
///
/// On timeout the child is killed and reaped (`kill()` + `wait()`) so it
/// doesn't linger as a zombie -- but killing our DIRECT child is not
/// guaranteed to close its stdout/stderr pipes promptly: a shell wrapper
/// (`sh -c "..."`) can fork a grandchild that inherited the same pipe file
/// descriptors at fork time, and killing only the direct child leaves that
/// orphaned grandchild holding the write end open until IT exits on its
/// own -- confirmed empirically in this module's own timeout test (`sh -c
/// "sleep 5"` killed via `child.kill()` still left the reader thread
/// blocked in `read_to_end()` for the full 5s). So the reader threads are
/// joined via a bounded `recv_timeout` (`READER_DRAIN_GRACE`), not an
/// unbounded `JoinHandle::join()` -- on timeout, whatever was captured so
/// far is used and the reader thread is abandoned (harmless: it's a plain
/// OS thread with no resources this process needs back, and it exits on
/// its own once the leaked descendant eventually does).
fn execute(program: &str, args: &[&str], timeout: Duration) -> InstallDependencyResult {
  let mut command = Command::new(program);
  command.args(args).stdout(Stdio::piped()).stderr(Stdio::piped());

  let mut child = match command.spawn() {
    Ok(child) => child,
    Err(err) => {
      return InstallDependencyResult {
        outcome: InstallOutcome::Failed,
        exit_code: None,
        output_tail: format!("failed to start {program}: {err}"),
      };
    }
  };

  let stdout_reader = child.stdout.take().map(spawn_pipe_reader);
  let stderr_reader = child.stderr.take().map(spawn_pipe_reader);

  let deadline = Instant::now() + timeout;
  let mut natural_status = None;
  loop {
    match child.try_wait() {
      Ok(Some(status)) => {
        natural_status = Some(status);
        break;
      }
      Ok(None) => {}
      // Can't confirm exit status either way; treat like a timeout below
      // (kill and report Failed) rather than looping on a broken check.
      Err(_) => break,
    }
    if Instant::now() >= deadline {
      break;
    }
    std::thread::sleep(INSTALL_POLL_INTERVAL);
  }

  if natural_status.is_none() {
    // Either the deadline passed or `try_wait()` errored -- either way,
    // the child isn't confirmed exited, so kill and reap it rather than
    // leaving it running unsupervised.
    let _ = child.kill();
    let _ = child.wait();
  }

  let stdout = stdout_reader.and_then(|rx| rx.recv_timeout(READER_DRAIN_GRACE).ok()).unwrap_or_default();
  let stderr = stderr_reader.and_then(|rx| rx.recv_timeout(READER_DRAIN_GRACE).ok()).unwrap_or_default();
  let combined = format!("{}\n{}", String::from_utf8_lossy(&stdout), String::from_utf8_lossy(&stderr));
  let output_tail = tail(&combined, OUTPUT_TAIL_CHARS);

  match natural_status {
    Some(status) => {
      let outcome = if status.success() { InstallOutcome::Success } else { InstallOutcome::Failed };
      InstallDependencyResult { outcome, exit_code: status.code(), output_tail }
    }
    None => InstallDependencyResult {
      outcome: InstallOutcome::Failed,
      exit_code: None,
      output_tail: format!(
        "{program} did not finish within {}s and was terminated. Partial output: {output_tail}",
        timeout.as_secs(),
      ),
    },
  }
}

/// Bound on how long `execute` waits for a reader thread to finish
/// draining its pipe after the child has already been confirmed exited (or
/// killed on timeout) -- see `execute`'s doc comment for why this can't be
/// an unbounded `join()`. Short: by the time this is checked, the process
/// this reader cares about has already exited or been killed, so any
/// remaining output should already be in flight; this only guards against
/// the orphaned-grandchild-holds-the-pipe-open edge case.
const READER_DRAIN_GRACE: Duration = Duration::from_millis(500);

/// Spawns a thread that reads `pipe` to completion into an owned buffer and
/// sends it over the returned `Receiver` -- a channel (not a plain
/// `JoinHandle`) specifically so the caller can bound how long it waits via
/// `recv_timeout` instead of an unbounded `join()`. A read error (e.g. the
/// pipe closing abruptly on kill) is swallowed -- whatever was read up to
/// that point is still sent, which is the best available partial output
/// for a killed/timed-out process.
fn spawn_pipe_reader<R: Read + Send + 'static>(mut pipe: R) -> std::sync::mpsc::Receiver<Vec<u8>> {
  let (tx, rx) = std::sync::mpsc::channel();
  std::thread::spawn(move || {
    let mut buf = Vec::new();
    let _ = pipe.read_to_end(&mut buf);
    let _ = tx.send(buf);
  });
  rx
}

fn run_install(dep: DependencyName) -> InstallDependencyResult {
  match plan_for(dep) {
    InstallPlan::Command { program, args } => execute(program, &args, INSTALL_TIMEOUT),
    InstallPlan::Unsupported(message) => InstallDependencyResult {
      outcome: InstallOutcome::Unsupported,
      exit_code: None,
      output_tail: message.to_string(),
    },
    InstallPlan::BrewMissing => InstallDependencyResult {
      outcome: InstallOutcome::BrewMissing,
      exit_code: None,
      output_tail: "Homebrew isn't installed -- install it from https://brew.sh, then try again.".to_string(),
    },
    InstallPlan::WingetMissing => InstallDependencyResult {
      outcome: InstallOutcome::WingetMissing,
      exit_code: None,
      output_tail: "winget isn't available on this system -- install \"App Installer\" from the \
                     Microsoft Store, then try again."
        .to_string(),
    },
  }
}

/// Tauri command: installs `name` ("ffmpeg" | "ytDlp") using the fixed,
/// per-OS command for the current platform. Runs off the main thread (see
/// `tauri::async_runtime::spawn_blocking`) so a slow installer never blocks
/// the event loop; the frontend shows a spinner and awaits the result.
///
/// Returns `Err` only for a caller-input problem (an unrecognized `name`,
/// which the frontend never sends today given `DepName`'s fixed union) or
/// an internal task failure -- an install that ran and failed (or timed
/// out) is still `Ok(InstallDependencyResult { outcome: Failed, .. })`,
/// not an `Err`.
#[tauri::command]
pub async fn install_dependency(name: String) -> Result<InstallDependencyResult, String> {
  let dep = DependencyName::parse(&name).ok_or_else(|| format!("unknown dependency: {name}"))?;
  tauri::async_runtime::spawn_blocking(move || run_install(dep))
    .await
    .map_err(|err| format!("install task failed to complete: {err}"))
}

#[cfg(test)]
mod tests {
  use super::*;

  fn always(result: bool) -> impl Fn(&str) -> bool {
    move |_name: &str| result
  }

  #[test]
  fn parses_known_dependency_names() {
    assert_eq!(DependencyName::parse("ffmpeg"), Some(DependencyName::Ffmpeg));
    assert_eq!(DependencyName::parse("ytDlp"), Some(DependencyName::YtDlp));
  }

  #[test]
  fn rejects_unknown_dependency_names() {
    assert_eq!(DependencyName::parse("yt-dlp"), None);
    assert_eq!(DependencyName::parse("ffprobe"), None);
    assert_eq!(DependencyName::parse(""), None);
    assert_eq!(DependencyName::parse("; rm -rf /"), None);
  }

  #[test]
  fn tail_returns_input_unchanged_when_under_the_limit() {
    assert_eq!(tail("short", 10), "short");
  }

  #[test]
  fn tail_keeps_only_the_last_n_chars() {
    let text = "0123456789abcdef";
    assert_eq!(tail(text, 6), "abcdef");
  }

  #[test]
  fn tail_handles_multibyte_chars_without_panicking() {
    // Byte-slicing this string at an arbitrary offset would panic or
    // corrupt a UTF-8 boundary; char-based truncation must not.
    let text = "ffmpeg 안녕하세요 install log";
    let truncated = tail(text, 5);
    assert_eq!(truncated.chars().count(), 5);
  }

  #[test]
  fn windows_plan_uses_the_verified_yt_dlp_winget_id() {
    match windows_plan(DependencyName::YtDlp, always(true)) {
      InstallPlan::Command { program, args } => {
        assert_eq!(program, "winget");
        assert!(args.contains(&"yt-dlp.yt-dlp"));
        assert!(!args.contains(&"yt-dlp"));
      }
      other => panic!("expected a Command plan, got {other:?}"),
    }
  }

  #[test]
  fn windows_plan_uses_gyan_ffmpeg_id() {
    match windows_plan(DependencyName::Ffmpeg, always(true)) {
      InstallPlan::Command { program, args } => {
        assert_eq!(program, "winget");
        assert!(args.contains(&"Gyan.FFmpeg"));
      }
      other => panic!("expected a Command plan, got {other:?}"),
    }
  }

  #[test]
  fn windows_plan_reports_winget_missing_instead_of_attempting_the_command() {
    assert!(matches!(windows_plan(DependencyName::Ffmpeg, always(false)), InstallPlan::WingetMissing));
    assert!(matches!(windows_plan(DependencyName::YtDlp, always(false)), InstallPlan::WingetMissing));
  }

  #[test]
  fn macos_plan_reports_brew_missing_instead_of_attempting_the_command() {
    assert!(matches!(macos_plan(DependencyName::Ffmpeg, always(false)), InstallPlan::BrewMissing));
  }

  #[test]
  fn macos_plan_builds_the_brew_command_when_brew_is_present() {
    match macos_plan(DependencyName::YtDlp, always(true)) {
      InstallPlan::Command { program, args } => {
        assert_eq!(program, "brew");
        assert_eq!(args, vec!["install", "yt-dlp"]);
      }
      other => panic!("expected a Command plan, got {other:?}"),
    }
  }

  #[test]
  fn linux_plan_never_attempts_to_install_ffmpeg_at_runtime() {
    assert!(matches!(linux_plan(DependencyName::Ffmpeg, always(true)), InstallPlan::Unsupported(_)));
  }

  #[test]
  fn linux_plan_falls_back_to_unsupported_when_pkexec_is_absent() {
    assert!(matches!(linux_plan(DependencyName::YtDlp, always(false)), InstallPlan::Unsupported(_)));
  }

  #[test]
  fn linux_plan_builds_the_pkexec_command_when_pkexec_is_present() {
    match linux_plan(DependencyName::YtDlp, always(true)) {
      InstallPlan::Command { program, args } => {
        assert_eq!(program, "pkexec");
        assert_eq!(args, vec!["apt-get", "install", "-y", "yt-dlp"]);
      }
      other => panic!("expected a Command plan, got {other:?}"),
    }
  }

  #[test]
  fn command_exists_finds_a_real_binary_on_path() {
    // `sh` is present on every CI/dev Unix box this test can plausibly
    // run on; on Windows this specific assertion doesn't apply (no
    // `sh`), so it's gated to non-Windows targets.
    #[cfg(not(windows))]
    assert!(command_exists("sh"));
  }

  #[test]
  fn command_exists_returns_false_for_a_binary_that_does_not_exist() {
    assert!(!command_exists("definitely-not-a-real-binary-name-xyz123"));
  }

  #[cfg(not(windows))]
  #[test]
  fn execute_reports_success_and_captures_stdout() {
    let result = execute("sh", &["-c", "echo hello-from-test"], Duration::from_secs(10));
    assert!(matches!(result.outcome, InstallOutcome::Success));
    assert_eq!(result.exit_code, Some(0));
    assert!(result.output_tail.contains("hello-from-test"));
  }

  #[cfg(not(windows))]
  #[test]
  fn execute_reports_failed_on_nonzero_exit_and_captures_stderr() {
    let result = execute("sh", &["-c", "echo boom-error >&2; exit 3"], Duration::from_secs(10));
    assert!(matches!(result.outcome, InstallOutcome::Failed));
    assert_eq!(result.exit_code, Some(3));
    assert!(result.output_tail.contains("boom-error"));
  }

  #[cfg(not(windows))]
  #[test]
  fn execute_reports_failed_when_the_program_cannot_be_spawned() {
    let result = execute("definitely-not-a-real-binary-name-xyz123", &[], Duration::from_secs(10));
    assert!(matches!(result.outcome, InstallOutcome::Failed));
    assert_eq!(result.exit_code, None);
    assert!(result.output_tail.contains("failed to start"));
  }

  #[cfg(not(windows))]
  #[test]
  fn execute_kills_and_reports_failed_when_the_timeout_elapses() {
    // A short, deterministic timeout against a process that sleeps far
    // longer than it -- proves this returns promptly (well under the
    // process's own 5s sleep) instead of blocking for the full duration,
    // and that the timeout message (not a natural exit) is reported.
    let started = Instant::now();
    let result = execute("sh", &["-c", "sleep 5"], Duration::from_millis(200));
    let elapsed = started.elapsed();

    assert!(matches!(result.outcome, InstallOutcome::Failed));
    assert_eq!(result.exit_code, None);
    assert!(result.output_tail.contains("did not finish within"));
    assert!(elapsed < Duration::from_secs(3), "expected a prompt return, took {elapsed:?}");
  }
}
