"""Static apps-directory config, read once at startup (same spirit as
``.env`` - see ``main.py``). No database, no admin UI to manage it yet -
add an app by editing ``apps.json`` and redeploying."""

from __future__ import annotations

import json
from pathlib import Path


def load_apps(path: str) -> list[dict]:
    """Each entry is ``{"name", "url", "description"}``. A missing file
    (e.g. a fresh checkout with no ``apps.json`` configured yet) is not an
    error - the apps directory just renders empty."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return []
    return json.loads(text)


__all__ = ["load_apps"]
