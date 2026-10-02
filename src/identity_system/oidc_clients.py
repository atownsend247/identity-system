"""Static OIDC/OAuth2 client registry, read once at startup (same spirit as
``apps.py`` - no database, no admin UI to manage it yet). Unlike
``apps.json``, this file holds credentials, so it's treated like ``.env``:
gitignored, excluded from ``deploy.sh``'s rsync, and survives every deploy -
see ``oidc_clients.json.example`` and CLAUDE.md.

Client secrets are hashed with sessionkit's own ``Argon2Hasher`` - reusing
its hashing rules rather than adding a second one, and so a leaked registry
file doesn't hand over plaintext secrets any more than a leaked user db
would hand over plaintext passwords."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sessionkit import Argon2Hasher


@dataclass(frozen=True)
class OidcClient:
    client_id: str
    client_secret_hash: str
    redirect_uris: frozenset[str]
    allowed_scopes: frozenset[str]


def load_oidc_clients(path: str) -> dict[str, OidcClient]:
    """Keyed by client_id. A missing file (no relying parties registered
    yet) is not an error - every OIDC endpoint just rejects every
    client_id until one exists, the same posture ``load_apps`` takes.

    This is called unconditionally at startup (see main.py) - a malformed
    file here would otherwise crash the *entire* service, not just the
    OIDC surface, with a bare ``TypeError`` pointing at an indexing line
    instead of the actual mistake. The one check below exists because it's
    a genuinely easy mistake to make by hand: a single registered client
    written as a bare ``{...}`` object instead of a one-element ``[...]``
    list (see ``oidc_clients.json.example``) parses fine as JSON, but then
    iterates over the object's *keys* (strings) instead of the object
    itself."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return {}
    entries = json.loads(text)
    if not isinstance(entries, list):
        raise ValueError(
            f"{path} must be a JSON array of client objects (even for a single "
            f"client) - see oidc_clients.json.example. Got a top-level "
            f"{type(entries).__name__} instead."
        )
    return {
        entry["client_id"]: OidcClient(
            client_id=entry["client_id"],
            client_secret_hash=entry["client_secret_hash"],
            redirect_uris=frozenset(entry["redirect_uris"]),
            allowed_scopes=frozenset(entry.get("allowed_scopes", ["openid"])),
        )
        for entry in entries
    }


def hash_client_secret(secret: str) -> str:
    """Produces the ``client_secret_hash`` value to hand-write into
    ``oidc_clients.json`` when registering a new client - there's no
    registration endpoint, same posture as account provisioning."""
    return Argon2Hasher().hash(secret)


def verify_client_secret(client: OidcClient, secret: str) -> bool:
    return Argon2Hasher().verify(client.client_secret_hash, secret)


__all__ = ["OidcClient", "load_oidc_clients", "hash_client_secret", "verify_client_secret"]
