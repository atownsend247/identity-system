"""Process entrypoint: ``uvicorn identity_system.main:app``.

The only module that reads env vars, opens a real SQLite file, or reads
``apps.json``/``oidc_clients.json`` off disk - opened/read once at import
time. The SQLite store is shared across the worker thread pool, per
sessionkit's own threading guidance (SqliteAuthStore.open() defaults to
check_same_thread=False specifically for this: one store, opened once,
serialised internally on its own lock). ``apps.json``/``oidc_clients.json``
aren't live-reloaded either - restart to pick up a change (see apps.py,
oidc_clients.py). The OIDC signing key is provisioned here too, on first
run if it doesn't exist yet (see oidc.load_or_create_signing_key).
"""

from __future__ import annotations

from sessionkit import AuthService, SqliteAuthStore

from .app import create_app
from .apps import load_apps
from .config import Settings
from .oidc import load_or_create_signing_key
from .oidc_clients import load_oidc_clients

settings = Settings.from_env()
store = SqliteAuthStore.open(settings.db_path)
auth = AuthService(store, session_days=settings.session_days, issuer=settings.issuer)
apps = load_apps(settings.apps_path)
oidc_clients = load_oidc_clients(settings.oidc_clients_path)
oidc_signing_key = load_or_create_signing_key(settings.oidc_signing_key_path)

app = create_app(auth, settings, apps, oidc_clients, oidc_signing_key)
