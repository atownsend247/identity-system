"""Reads the legacy ``apps.json`` file. The apps directory itself now lives
in SQLite and is managed from the admin page (see ``registry.py``); this
loader only runs once, for the first-startup import (see ``main.py``)."""

from __future__ import annotations

import json
from pathlib import Path


def load_apps(path: str) -> list[dict]:
    """Each entry is ``{"name", "url", "description"}``. A missing file just
    means there's nothing to import."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return []
    return json.loads(text)


__all__ = ["load_apps"]
