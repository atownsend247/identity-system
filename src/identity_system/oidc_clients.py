"""OIDC/OAuth2 client registry types and secret helpers. The registry itself
now lives in SQLite and is managed from the admin pages (see
``registry.py``); ``load_oidc_clients`` only reads the legacy
``oidc_clients.json`` file, once, for the first-startup import (see
``main.py``).

Client secrets are hashed with sessionkit's own ``Argon2Hasher`` - reusing
its hashing rules rather than adding a second one, and so a leaked registry
db hands over no more plaintext secrets than a leaked user db would hand
over plaintext passwords. A secret is shown to the admin once, when it's
generated, and never stored or returned again.
"""

from __future__ import annotations

import json
import secrets
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
    """Keyed by client_id. Only used by the one-time legacy import (see
    main.py), so a missing file just means there's nothing to import.

    A malformed file here would otherwise surface as a bare ``TypeError``
    pointing at an indexing line instead of the actual mistake. The one check below exists because it's
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


def generate_client_secret() -> str:
    """A fresh random client secret, generated server-side so the admin
    never has to pick (or hand-hash) one."""
    return secrets.token_urlsafe(32)


def hash_client_secret(secret: str) -> str:
    return Argon2Hasher().hash(secret)


def verify_client_secret(client: OidcClient, secret: str) -> bool:
    return Argon2Hasher().verify(client.client_secret_hash, secret)


__all__ = [
    "OidcClient",
    "generate_client_secret",
    "hash_client_secret",
    "load_oidc_clients",
    "verify_client_secret",
]
