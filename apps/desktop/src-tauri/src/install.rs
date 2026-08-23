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

use std::process::Command;

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
  /// The install command ran and exited non-zero, or couldn't be spawned
  /// at all (e.g. `winget` itself isn't on PATH).
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
}

/// Result returned to the frontend for one `install_dependency` call.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallDependencyResult {
  outcome: InstallOutcome,
  /// The install command's exit code, when one actually ran. `None` for
  /// `Unsupported`/`BrewMissing` (nothing was spawned) and for a spawn
  /// failure (the process never started, so it never exited).
  exit_code: Option<i32>,
  /// Combined stdout+stderr tail (see `OUTPUT_TAIL_CHARS`), or a short
  /// explanatory message for `Unsupported`/`BrewMissing`. Shown by the
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
}

fn plan_for(dep: DependencyName) -> InstallPlan {
  if cfg!(windows) {
    windows_plan(dep)
  } else if cfg!(target_os = "macos") {
    macos_plan(dep)
  } else {
    linux_plan(dep)
  }
}

fn windows_plan(dep: DependencyName) -> InstallPlan {
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

fn macos_plan(dep: DependencyName) -> InstallPlan {
  if !command_exists("brew") {
    return InstallPlan::BrewMissing;
  }
  let package = match dep {
    DependencyName::Ffmpeg => "ffmpeg",
    DependencyName::YtDlp => "yt-dlp",
  };
  InstallPlan::Command { program: "brew", args: vec!["install", package] }
}

fn linux_plan(dep: DependencyName) -> InstallPlan {
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
      if !command_exists("pkexec") {
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

fn execute(program: &str, args: &[&str]) -> InstallDependencyResult {
  match Command::new(program).args(args).output() {
    Ok(output) => {
      let combined = format!(
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
      );
      let outcome = if output.status.success() { InstallOutcome::Success } else { InstallOutcome::Failed };
      InstallDependencyResult {
        outcome,
        exit_code: output.status.code(),
        output_tail: tail(&combined, OUTPUT_TAIL_CHARS),
      }
    }
    Err(err) => InstallDependencyResult {
      outcome: InstallOutcome::Failed,
      exit_code: None,
      output_tail: format!("failed to start {program}: {err}"),
    },
  }
}

fn run_install(dep: DependencyName) -> InstallDependencyResult {
  match plan_for(dep) {
    InstallPlan::Command { program, args } => execute(program, &args),
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
  }
}

/// Tauri command: installs `name` ("ffmpeg" | "ytDlp") using the fixed,
/// per-OS command for the current platform. Runs off the main thread (see
/// `tauri::async_runtime::spawn_blocking`) so a slow installer never blocks
/// the event loop; the frontend shows a spinner and awaits the result.
///
/// Returns `Err` only for a caller-input problem (an unrecognized `name`,
/// which the frontend never sends today given `DepName`'s fixed union) or
/// an internal task failure -- an install that ran and failed is still
/// `Ok(InstallDependencyResult { outcome: Failed, .. })`, not an `Err`.
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
    match windows_plan(DependencyName::YtDlp) {
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
    match windows_plan(DependencyName::Ffmpeg) {
      InstallPlan::Command { program, args } => {
        assert_eq!(program, "winget");
        assert!(args.contains(&"Gyan.FFmpeg"));
      }
      other => panic!("expected a Command plan, got {other:?}"),
    }
  }

  #[test]
  fn linux_plan_never_attempts_to_install_ffmpeg_at_runtime() {
    assert!(matches!(linux_plan(DependencyName::Ffmpeg), InstallPlan::Unsupported(_)));
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
}
