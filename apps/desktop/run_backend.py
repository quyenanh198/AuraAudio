"""Standalone entrypoint for the PyInstaller-bundled AuraAudio backend.

This script is the concrete Python entrypoint PyInstaller bundles into
`aura-backend`. It cannot simply reference `aura_api.main:app` (an ASGI
app object) because PyInstaller needs a runnable script that boots the
process, so this imports the app object and drives it with `uvicorn.run`.

Fixed port 8317 is locked for this plan (desktop-shell-packaging) so every
later task that needs to reach the bundled backend uses the same literal.

`aura_api.db` requires `DATABASE_URL` (and reads `AURA_DATA_DIR`) at import
time. In the normal dev workflow those come from `.envrc`. A standalone
bundle has no `.envrc`, so this sets sane defaults *before* importing
`aura_api.main` — a SQLite DB file next to the bundled executable — while
still letting an operator override either var beforehand.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # Running as a PyInstaller bundle: anchor data next to the executable.
    _base_dir = Path(sys.executable).resolve().parent
else:
    _base_dir = Path(__file__).resolve().parent

_data_dir = _base_dir / "data"
os.environ.setdefault("AURA_DATA_DIR", str(_data_dir))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_data_dir / 'aura.db'}")

import uvicorn  # noqa: E402

from aura_api.main import app  # noqa: E402

AURA_BACKEND_PORT = 8317

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=AURA_BACKEND_PORT)
