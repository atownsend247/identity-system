"""Process entrypoint: ``uvicorn identity_system.main:app``.

The only module that reads env vars or opens a real SQLite file - opened
once at import time and shared across the worker thread pool, per
sessionkit's own threading guidance (SqliteAuthStore.open() defaults to
check_same_thread=False specifically for this: one store, opened once,
serialised internally on its own lock).
"""

from __future__ import annotations

from sessionkit import AuthService, SqliteAuthStore

from .app import create_app
from .config import Settings

settings = Settings.from_env()
store = SqliteAuthStore.open(settings.db_path)
auth = AuthService(store, session_days=settings.session_days, issuer=settings.issuer)

app = create_app(auth, settings)
