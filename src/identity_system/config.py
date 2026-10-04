"""Env-var settings. Plain os.environ - small enough that a settings
framework would be more ceremony than it saves."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

_PREFIX = "IDENTITY_SYSTEM_"


def _env(name: str, default: str) -> str:
    return os.environ.get(_PREFIX + name, default)


@dataclass(frozen=True)
class Settings:
    db_path: str
    cookie_domain: str
    cookie_secure: bool
    session_days: int
    issuer: str
    admin_emails: frozenset[str]
    apps_path: str
    oidc_issuer_url: str
    oidc_clients_path: str
    oidc_signing_key_path: str

    cookie_name: str = "identity_session"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=_env("DB", "./auth.db"),
            cookie_domain=_env("COOKIE_DOMAIN", ""),
            cookie_secure=_env("COOKIE_SECURE", "true").strip().lower() != "false",
            session_days=int(_env("SESSION_DAYS", "30")),
            issuer=_env("ISSUER", "identity-system"),
            admin_emails=frozenset(
                email.strip().lower()
                for email in _env("ADMIN_EMAILS", "").split(",")
                if email.strip()
            ),
            apps_path=_env("APPS_PATH", "./apps.json"),
            # Not the same thing as `issuer` above (that's sessionkit's TOTP
            # label) - this is the OIDC `iss` claim/discovery base URL, and
            # has to be the real externally-reachable origin, not a name.
            oidc_issuer_url=_env("OIDC_ISSUER_URL", ""),
            oidc_clients_path=_env("OIDC_CLIENTS_PATH", "./oidc_clients.json"),
            oidc_signing_key_path=_env("OIDC_SIGNING_KEY_PATH", "./data/oidc_signing_key.pem"),
        )

    def is_admin(self, email: str) -> bool:
        """True if ``email`` is on the static IDENTITY_SYSTEM_ADMIN_EMAILS
        allowlist - the only notion of authorization this service has, since
        sessionkit itself has no roles/scopes concept. Gates the apps and
        OIDC client admin pages too."""
        return email.strip().lower() in self.admin_emails

    def is_trusted_redirect(self, target: str) -> bool:
        """True if ``target`` is a same-site relative path, or points at
        ``cookie_domain`` (or a subdomain of it) - the only hosts a
        post-login redirect is allowed to send a browser to, so a crafted
        ``?rd=`` can't be used to phish a user off-domain after a real
        login."""
        if not target or target.startswith("//"):
            return False
        parsed = urlsplit(target)
        if not parsed.scheme and not parsed.netloc:
            return target.startswith("/")
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        domain = self.cookie_domain.lstrip(".")
        return bool(domain) and (host == domain or host.endswith("." + domain))


__all__ = ["Settings"]
