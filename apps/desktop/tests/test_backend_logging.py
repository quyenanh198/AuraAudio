"""Regression coverage for Bug C's console-window follow-through
(`apps/desktop/run_backend.py`): `build-backend.sh` now builds the Windows
bundle with PyInstaller `--noconsole`, which (per PyInstaller's own
documented pitfall) can leave `sys.stdout`/`sys.stderr` set to `None`
rather than a real, if invisible, stream — crashing the process on its
first `print()`/log write instead of just losing visible output.

Both halves of the fix are exercised in a genuinely fresh subprocess (not
an in-process `import run_backend` — see test_schema_init.py's module
docstring for why a subprocess is load-bearing here too: `aura_api.db`
binds its engine to whichever `DATABASE_URL` is set the first time it's
imported anywhere in the test process, and this file's own
`AURA_DATA_DIR`/log-dir setup must not collide with any other test module
that happens to import `run_backend` first):

1. `sys.stdout`/`sys.stderr` forced to `None` (the exact PyInstoller
   --noconsole symptom, simulated here since a genuinely console-less
   Windows process can't be produced off real Windows) before `import
   run_backend` — the import itself, and a subsequent `print()`, must not
   raise.
2. `AURA_DATA_DIR/logs/backend.log` is actually created and receives a
   real `logging`-module record once `uvicorn`-shaped logging is
   configured via `_UVICORN_LOG_CONFIG`.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent.parent

_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import logging
    import sys

    # Simulates the exact PyInstaller `--noconsole` symptom this fix
    # guards against: CPython sets sys.stdout/sys.stderr to None (not a
    # closed/broken stream) when the frozen Windows process inherits no
    # console handles at all. Set BEFORE importing run_backend, which
    # must patch both to a real stream as its very first order of
    # business (see its module docstring).
    sys.stdout = None
    sys.stderr = None

    sys.path.insert(0, sys.argv[1])
    import run_backend  # must not raise despite the None streams above

    # A None sys.stdout would raise AttributeError on the very first
    # print() -- this is the exact crash-on-first-output failure mode
    # the fix exists to prevent.
    print("hello from a formerly-None stdout")

    # uvicorn's own log_config is only applied when uvicorn.run() itself
    # calls dictConfig (see run_backend.py's __main__ guard) -- applying
    # it directly here proves the config dict is well-formed and routes
    # to the expected file without needing a real server to actually bind
    # a port.
    import logging.config
    logging.config.dictConfig(run_backend._UVICORN_LOG_CONFIG)
    logging.getLogger("uvicorn.error").info("backend logging regression probe")

    for handler in logging.getLogger().handlers:
        handler.flush()

    print("DONE", flush=True)
    """
)


def _run_with_none_stdio(data_dir: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "AURA_DATA_DIR": str(data_dir),
        "DATABASE_URL": f"sqlite:///{data_dir / 'aura.db'}",
    }
    return subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(DESKTOP_DIR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_import_and_print_survive_none_stdio(tmp_path: Path) -> None:
    result = _run_with_none_stdio(tmp_path)

    # A crash here is exactly the pre-fix failure mode: `AttributeError:
    # 'NoneType' object has no attribute 'write'` on the first print(),
    # surfacing as a nonzero exit. Not checked via captured OS-level
    # stdout/stderr: run_backend.py's fix REPLACES sys.stdout/sys.stderr
    # with the log file object inside the subprocess, so the interpreter's
    # own subsequent print() calls land in that file, not in the pipes
    # `subprocess.run(capture_output=True)` reads from — proof-of-life is
    # the clean exit code (and the log file assertions in the next test).
    assert result.returncode == 0, (
        f"run_backend import/print crashed with None stdio "
        f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
    )


def test_backend_log_file_is_created_and_receives_records(tmp_path: Path) -> None:
    _run_with_none_stdio(tmp_path)

    log_path = tmp_path / "logs" / "backend.log"
    assert log_path.exists(), f"expected {log_path} to exist after run_backend import"
    contents = log_path.read_text(encoding="utf-8")
    assert "backend logging regression probe" in contents
    # The formerly-None sys.stdout was replaced with this same log file
    # object (run_backend.py's fix), so the plain print() call the
    # subprocess script made also lands here.
    assert "hello from a formerly-None stdout" in contents
    assert "DONE" in contents


def test_module_import_alone_does_not_crash_with_real_stdio(tmp_path: Path) -> None:
    # Sanity check for the non-frozen/normal-dev case: importing
    # run_backend with ordinary (non-None) stdio must not touch
    # sys.stdout/sys.stderr at all (the `is None` guard is a no-op), and
    # must not raise.
    import os

    env = {
        "PATH": os.environ.get("PATH", ""),
        "AURA_DATA_DIR": str(tmp_path),
        "DATABASE_URL": f"sqlite:///{tmp_path / 'aura.db'}",
    }
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import run_backend; print('ok')", str(DESKTOP_DIR)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
