"""Bar-visible state, written where the Omarchy shell plugin can watch it.

The shell widget uses a Quickshell FileView with watchChanges, so this only
has to keep the file valid at every instant — hence the atomic replace.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

IDLE = "idle"
WORKING = "working"


def state_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(base) / "quill" / "state.json"


def write(state: str, detail: str = "") -> None:
    """Best-effort: a missing bar widget must never break an edit."""
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"state": state, "detail": detail}),
                       encoding="utf-8")
        # Rename rather than rewrite in place, so a watcher woken mid-write
        # never sees a truncated file.
        tmp.replace(path)
    except OSError:
        pass


@contextmanager
def working(detail: str = ""):
    write(WORKING, detail)
    try:
        yield
    finally:
        write(IDLE)
