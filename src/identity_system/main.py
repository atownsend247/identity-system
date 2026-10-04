"""Process entrypoint: ``uvicorn identity_system.main:app``.

The only module that reads env vars, opens a real SQLite file, or reads the
legacy ``apps.json``/``oidc_clients.json`` off disk - all opened/read once at
import time. The SQLite store is shared across the worker thread pool, per
sessionkit's own threading guidance (SqliteAuthStore.open() defaults to
check_same_thread=False specifically for this: one store, opened once,
serialised internally on its own lock). The apps directory and OIDC client
registry live in that same db file (see registry.py) and are managed from the
admin pages; ``apps.json``/``oidc_clients.json`` are only imported once, on
first startup, to seed it. The OIDC signing key is provisioned here too, on
first run if it doesn't exist yet (see oidc.load_or_create_signing_key).
"""

from __future__ import annotations

from pathlib import Path

from sessionkit import AuthService, SqliteAuthStore
from sessionkit.sqlite_store import connect

from .app import create_app
from .apps import load_apps
from .config import Settings
from .oidc import load_or_create_signing_key
from .oidc_clients import load_oidc_clients
from .registry import ConfigRegistry

settings = Settings.from_env()
store = SqliteAuthStore.open(settings.db_path)
auth = AuthService(store, session_days=settings.session_days, issuer=settings.issuer)

registry = ConfigRegistry(connect(settings.db_path, check_same_thread=False))
# Only mark the import done if there was something to import - otherwise a
# box that starts without either file would have its flag set, and a file
# copied in afterwards would silently never be imported.
legacy_files_present = any(
    Path(p).is_file() for p in (settings.apps_path, settings.oidc_clients_path)
)
if legacy_files_present and not registry.legacy_import_done():
    registry.import_legacy(
        load_apps(settings.apps_path),
        list(load_oidc_clients(settings.oidc_clients_path).values()),
    )

oidc_signing_key = load_or_create_signing_key(settings.oidc_signing_key_path)

app = create_app(auth, settings, registry, oidc_signing_key)
