"""SQLite-backed registry for the apps directory and the OIDC client
registry, managed from the admin pages (see app.py's ``/api/admin/apps`` and
``/api/admin/oidc-clients``).

Lives in the same db file as sessionkit's own tables, as sibling tables on
its own connection (see sessionkit's ``connect``, and ``oidc.py``'s note on
extending the schema this way). That puts it under the same ``data/``
protection the rest of this service's state already relies on - see
CLAUDE.md - so an admin's edits survive every deploy.

``apps.json`` and ``oidc_clients.json`` are no longer the source of truth.
They're imported exactly once, on first startup against a db that hasn't
seen them yet (see ``main.py``), and after that the admin pages own the
data. ``identity_meta`` holds the one-shot import flag.
"""

from __future__ import annotations

import json
import sqlite3
import threading

from .oidc_clients import OidcClient

_SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS oidc_clients (
    client_id          TEXT PRIMARY KEY,
    client_secret_hash TEXT NOT NULL,
    redirect_uris      TEXT NOT NULL,
    allowed_scopes     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identity_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_LEGACY_IMPORT_KEY = "legacy_import_done"


class ConfigRegistry:
    """Methods are serialised on ``self._lock``, same as sessionkit's
    ``SqliteAuthStore`` - the connection is shared across the threadpool
    FastAPI runs sync routes on."""

    def __init__(self, conn: sqlite3.Connection, *, lock: threading.Lock | None = None) -> None:
        self._conn = conn
        self._lock = lock or threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------- apps

    def list_apps(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, url, description FROM apps ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def add_app(self, name: str, url: str, description: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO apps (name, url, description) VALUES (?, ?, ?)",
                (name, url, description),
            )
            self._conn.commit()
            return cur.lastrowid

    def update_app(self, app_id: int, name: str, url: str, description: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE apps SET name = ?, url = ?, description = ? WHERE id = ?",
                (name, url, description, app_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_app(self, app_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ----------------------------------------------------- oidc clients

    def get_oidc_client(self, client_id: str) -> OidcClient | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM oidc_clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        return _client_from_row(row) if row is not None else None

    def list_oidc_clients(self) -> list[OidcClient]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM oidc_clients ORDER BY client_id").fetchall()
        return [_client_from_row(row) for row in rows]

    def add_oidc_client(self, client: OidcClient) -> bool:
        """False if ``client_id`` is already registered."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO oidc_clients "
                    "(client_id, client_secret_hash, redirect_uris, allowed_scopes) "
                    "VALUES (?, ?, ?, ?)",
                    _client_to_row(client),
                )
            except sqlite3.IntegrityError:
                return False
            self._conn.commit()
            return True

    def update_oidc_client(
        self, client_id: str, redirect_uris: frozenset[str], allowed_scopes: frozenset[str]
    ) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE oidc_clients SET redirect_uris = ?, allowed_scopes = ? WHERE client_id = ?",
                (
                    json.dumps(sorted(redirect_uris)),
                    json.dumps(sorted(allowed_scopes)),
                    client_id,
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def set_oidc_client_secret_hash(self, client_id: str, secret_hash: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE oidc_clients SET client_secret_hash = ? WHERE client_id = ?",
                (secret_hash, client_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_oidc_client(self, client_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM oidc_clients WHERE client_id = ?", (client_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------ legacy import

    def legacy_import_done(self) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM identity_meta WHERE key = ?", (_LEGACY_IMPORT_KEY,)
            ).fetchone()
        return row is not None

    def import_legacy(self, apps: list[dict], clients: list[OidcClient]) -> None:
        """Seeds the tables from the old static files and sets the import
        flag, all in one transaction - a crash partway through leaves the
        flag unset, so the next startup retries the whole import cleanly."""
        with self._lock, self._conn:
            for entry in apps:
                self._conn.execute(
                    "INSERT INTO apps (name, url, description) VALUES (?, ?, ?)",
                    (entry["name"], entry["url"], entry.get("description", "")),
                )
            for client in clients:
                self._conn.execute(
                    "INSERT INTO oidc_clients "
                    "(client_id, client_secret_hash, redirect_uris, allowed_scopes) "
                    "VALUES (?, ?, ?, ?)",
                    _client_to_row(client),
                )
            self._conn.execute(
                "INSERT INTO identity_meta (key, value) VALUES (?, 'true')",
                (_LEGACY_IMPORT_KEY,),
            )


def _client_from_row(row: sqlite3.Row) -> OidcClient:
    return OidcClient(
        client_id=row["client_id"],
        client_secret_hash=row["client_secret_hash"],
        redirect_uris=frozenset(json.loads(row["redirect_uris"])),
        allowed_scopes=frozenset(json.loads(row["allowed_scopes"])),
    )


def _client_to_row(client: OidcClient) -> tuple[str, str, str, str]:
    return (
        client.client_id,
        client.client_secret_hash,
        json.dumps(sorted(client.redirect_uris)),
        json.dumps(sorted(client.allowed_scopes)),
    )


__all__ = ["ConfigRegistry"]
