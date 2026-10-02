"""A minimal OAuth2 Authorization Code + PKCE + OIDC layer, sitting
alongside (not replacing) the cookie-session/forward-auth system in
``app.py``. Built for a small number of trusted relying parties on your own
network (Jenkins' OpenID Connect plugin was the first) - not a general-
purpose multi-tenant IdP:

- No consent screen. Any client registered in ``oidc_clients.json`` is
  auto-approved for a signed-in user, the same trust posture
  ``IDENTITY_SYSTEM_ADMIN_EMAILS``/``apps.json`` already take (whoever can
  edit that file is already trusted).
- Authorization codes live in memory only (``AuthorizationCodeStore``),
  one-time-use, ~60s TTL. A restart drops any mid-flight login - the user
  just retries - so this deliberately isn't persisted; sessionkit's
  ``AuthStore`` schema is confirmed safe to extend with sibling tables in
  the same db file if a future multi-instance deployment ever needs these
  to survive a restart instead.
- ID tokens and access tokens are both signed JWTs (RS256, one shared
  key), with the same scope-gated identity claims embedded in both - so
  ``/userinfo`` only ever needs to verify a signature, never a second
  token-store lookup. sessionkit's own session tokens are opaque and
  carry none of this - there is no crypto to reuse from there.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from jwt.algorithms import RSAAlgorithm
from sessionkit import AuthenticationError, AuthService

from .config import Settings
from .oidc_clients import OidcClient, verify_client_secret

_AUTH_CODE_TTL_SECONDS = 60
_ID_TOKEN_TTL_SECONDS = 300
_ACCESS_TOKEN_TTL_SECONDS = 300


# ------------------------------------------------------------ signing key

def load_or_create_signing_key(path: str) -> rsa.RSAPrivateKey:
    """Loads the RSA keypair used to sign ID/access tokens from ``path``,
    generating and writing one (mode 0600) on first run if it doesn't
    exist yet - same "provision on first startup" posture as sessionkit
    provisioning its own db file. No rotation support: a single long-lived
    key is the right amount of complexity for a handful of internal
    relying parties - rotate by replacing the file and restarting."""
    p = Path(path)
    if p.exists():
        return serialization.load_pem_private_key(p.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(pem)
    p.chmod(0o600)
    return key


def _kid_for(public_key: rsa.RSAPublicKey) -> str:
    """A stable key id derived from the public key itself, so it doesn't
    change across restarts as long as the key file doesn't."""
    numbers = public_key.public_numbers()
    raw = f"{numbers.n}:{numbers.e}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# --------------------------------------------------------- authorization codes

@dataclass(frozen=True)
class _AuthCode:
    client_id: str
    redirect_uri: str
    user_id: str
    email: str
    name: str
    scope: str
    nonce: str | None
    code_challenge: str
    expires_at: float


class AuthorizationCodeStore:
    """In-memory, one-time-use authorization codes. Snapshots the user's
    email/name at /authorize time rather than looking them up again at
    /token - sessionkit's AuthService has no get-by-id lookup (only
    find_user(email) and list_users()), and a code's ~60s lifetime makes a
    stale snapshot a non-issue."""

    def __init__(self, ttl_seconds: int = _AUTH_CODE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._codes: dict[str, _AuthCode] = {}

    def create(self, **fields) -> str:
        self._sweep()
        code = secrets.token_urlsafe(32)
        self._codes[code] = _AuthCode(expires_at=time.time() + self._ttl, **fields)
        return code

    def consume(self, code: str) -> _AuthCode | None:
        """Pops and returns the code's data - a code can only ever be
        consumed once, by construction. ``None`` if unknown or expired."""
        self._sweep()
        entry = self._codes.pop(code, None)
        if entry is None or entry.expires_at < time.time():
            return None
        return entry

    def _sweep(self) -> None:
        now = time.time()
        for code, entry in list(self._codes.items()):
            if entry.expires_at < now:
                del self._codes[code]


def _verify_pkce(code_challenge: str, code_verifier: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(expected, code_challenge)


# --------------------------------------------------------------- JWT issuance

def _claims_for_scope(scope: str, *, email: str, name: str) -> dict:
    claims: dict = {}
    scopes = set(scope.split())
    if "email" in scopes:
        claims["email"] = email
        claims["email_verified"] = True
    if "profile" in scopes:
        claims["name"] = name
    return claims


def _issue_jwt(
    *, issuer: str, signing_key: rsa.RSAPrivateKey, kid: str, ttl_seconds: int, claims: dict
) -> str:
    now = int(time.time())
    payload = {"iss": issuer, "iat": now, "exp": now + ttl_seconds, **claims}
    return jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": kid})


# --------------------------------------------------------------------- router

def create_oidc_router(
    *,
    auth: AuthService,
    settings: Settings,
    clients: dict[str, OidcClient],
    signing_key: rsa.RSAPrivateKey,
    code_store: AuthorizationCodeStore | None = None,
) -> APIRouter:
    code_store = code_store if code_store is not None else AuthorizationCodeStore()
    kid = _kid_for(signing_key.public_key())
    issuer = settings.oidc_issuer_url
    router = APIRouter()

    # --------------------------------------------------------- discovery

    @router.get("/.well-known/openid-configuration")
    def discovery():
        # Spec-fixed path - the one deliberate exception to "anything new
        # goes under /api/" (see CLAUDE.md).
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/api/oidc/authorize",
            "token_endpoint": f"{issuer}/api/oidc/token",
            "userinfo_endpoint": f"{issuer}/api/oidc/userinfo",
            "jwks_uri": f"{issuer}/api/oidc/jwks.json",
            "response_types_supported": ["code"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "profile"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_basic",
                "client_secret_post",
            ],
            "code_challenge_methods_supported": ["S256"],
            "claims_supported": ["sub", "email", "email_verified", "name"],
        }

    @router.get("/api/oidc/jwks.json")
    def jwks():
        jwk = json.loads(RSAAlgorithm.to_jwk(signing_key.public_key()))
        jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
        return {"keys": [jwk]}

    # ------------------------------------------------------- authorization

    @router.get("/api/oidc/authorize")
    def authorize(request: Request):
        params = request.query_params
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        state = params.get("state", "")

        client = clients.get(client_id)
        if client is None or redirect_uri not in client.redirect_uris:
            # redirect_uri isn't verified yet - never redirect an
            # unverified URI, that's the whole point of registering them.
            raise HTTPException(status_code=400, detail="unknown client_id or redirect_uri")

        def error_redirect(error: str, description: str) -> RedirectResponse:
            query = urlencode({"error": error, "error_description": description, "state": state})
            return RedirectResponse(url=f"{redirect_uri}?{query}", status_code=302)

        if params.get("response_type", "") != "code":
            return error_redirect("unsupported_response_type", "only 'code' is supported")

        requested_scopes = set(params.get("scope", "").split())
        if "openid" not in requested_scopes:
            return error_redirect("invalid_scope", "'openid' scope is required")
        if not requested_scopes <= client.allowed_scopes:
            return error_redirect("invalid_scope", "scope not allowed for this client")

        code_challenge = params.get("code_challenge", "")
        if params.get("code_challenge_method", "") != "S256" or not code_challenge:
            return error_redirect("invalid_request", "PKCE (code_challenge_method=S256) is required")

        token = request.cookies.get(settings.cookie_name)
        try:
            user = auth.user_for_token(token)
        except AuthenticationError:
            rd = f"{request.url.path}?{request.url.query}"
            return RedirectResponse(url=f"/login?{urlencode({'rd': rd})}", status_code=302)

        code = code_store.create(
            client_id=client_id,
            redirect_uri=redirect_uri,
            user_id=user.id or "",
            email=user.email,
            name=user.name,
            scope=" ".join(sorted(requested_scopes)),
            nonce=params.get("nonce"),
            code_challenge=code_challenge,
        )
        query = urlencode({"code": code, "state": state})
        return RedirectResponse(url=f"{redirect_uri}?{query}", status_code=302)

    # -------------------------------------------------------------- token

    @router.post("/api/oidc/token")
    async def token_endpoint(
        request: Request,
        grant_type: str = Form(...),
        code: str = Form(...),
        redirect_uri: str = Form(...),
        code_verifier: str = Form(...),
        client_id: str | None = Form(None),
        client_secret: str | None = Form(None),
    ):
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth_header[len("basic "):]).decode()
                client_id, client_secret = decoded.split(":", 1)
            except Exception:
                return JSONResponse(status_code=401, content={"error": "invalid_client"})

        client = clients.get(client_id or "")
        if client is None or not client_secret or not verify_client_secret(client, client_secret):
            return JSONResponse(status_code=401, content={"error": "invalid_client"})

        if grant_type != "authorization_code":
            return JSONResponse(status_code=400, content={"error": "unsupported_grant_type"})

        entry = code_store.consume(code)
        if entry is None or entry.client_id != client_id or entry.redirect_uri != redirect_uri:
            return JSONResponse(status_code=400, content={"error": "invalid_grant"})

        if not _verify_pkce(entry.code_challenge, code_verifier):
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_grant", "error_description": "PKCE verification failed"},
            )

        claims = {
            "sub": entry.user_id,
            "aud": client_id,
            **_claims_for_scope(entry.scope, email=entry.email, name=entry.name),
        }
        id_token_claims = dict(claims)
        if entry.nonce:
            id_token_claims["nonce"] = entry.nonce
        id_token_claims["auth_time"] = int(time.time())

        id_token = _issue_jwt(
            issuer=issuer, signing_key=signing_key, kid=kid,
            ttl_seconds=_ID_TOKEN_TTL_SECONDS, claims=id_token_claims,
        )
        access_token = _issue_jwt(
            issuer=issuer, signing_key=signing_key, kid=kid,
            ttl_seconds=_ACCESS_TOKEN_TTL_SECONDS, claims={**claims, "scope": entry.scope},
        )
        return {
            "id_token": id_token,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": _ACCESS_TOKEN_TTL_SECONDS,
            "scope": entry.scope,
        }

    # ----------------------------------------------------------- userinfo

    @router.get("/api/oidc/userinfo")
    def userinfo(request: Request):
        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                status_code=401, content={"error": "invalid_token"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth_header[len("bearer "):]
        try:
            claims = jwt.decode(
                token, signing_key.public_key(), algorithms=["RS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return JSONResponse(
                status_code=401, content={"error": "invalid_token"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        result = {"sub": claims["sub"]}
        for key in ("email", "email_verified", "name"):
            if key in claims:
                result[key] = claims[key]
        return result

    return router


__all__ = ["AuthorizationCodeStore", "create_oidc_router", "load_or_create_signing_key"]
