"""The identity-system FastAPI app: the JSON API a reverse proxy's
forward-auth hook, downstream apps, and this service's own Vite/React
frontend (``frontend/``) all call. There's no server-rendered HTML here -
``/login``, ``/admin``, ``/admin/apps``, ``/admin/oidc-clients``, ``/`` (apps
directory) and ``/account`` are pure client-side routes; nginx serves the
built frontend for those in production (see
``deploy/nginx-identity-system.conf``) and Vite's dev server proxies
everything below to this app locally (see ``frontend/vite.config.ts``).

``create_app(auth, settings, registry, oidc_signing_key)`` takes an
already-constructed ``AuthService`` (same shape as sessionkit's own
``examples/fastapi_app.py``), the ``ConfigRegistry`` holding the apps
directory and OIDC client registry (see ``registry.py``), and the OIDC
signing key the ``/api/oidc/*`` + ``/.well-known/openid-configuration``
routes need (see ``oidc.py``) - so tests can build one over an in-memory
store with no real files touched. Passing no signing key leaves the OIDC
surface unmounted entirely.
"""

from __future__ import annotations

from typing import Annotated, Literal

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, StringConstraints

from sessionkit import (
    AuthenticationError,
    AuthError,
    AuthService,
    DuplicateUser,
    OtpInvalid,
    User,
    UserNotFound,
    ValidationError,
)

from .config import Settings
from .oidc import create_oidc_router
from .oidc_clients import OidcClient, generate_client_secret, hash_client_secret
from .registry import ConfigRegistry

# Same rules the public apps directory and the OIDC redirect_uri exact-match
# depend on - a plain absolute http(s) URL, no whitespace. Deliberately not
# pydantic's HttpUrl: that normalises the value (adds a trailing slash to a
# bare origin), and the redirect_uri check is an exact string match.
_HttpUrl = Annotated[str, StringConstraints(pattern=r"^https?://\S+$", max_length=2000)]
_OidcScope = Literal["openid", "email", "profile"]


class AppIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: _HttpUrl
    description: str = Field(default="", max_length=1000)


class OidcClientIn(BaseModel):
    client_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,200}$")]
    redirect_uris: list[_HttpUrl] = Field(min_length=1)
    allowed_scopes: list[_OidcScope] = Field(default_factory=lambda: ["openid"], min_length=1)


class OidcClientUpdate(BaseModel):
    redirect_uris: list[_HttpUrl] = Field(min_length=1)
    allowed_scopes: list[_OidcScope] = Field(min_length=1)

# Same shape as sessionkit's own examples/fastapi_app.py: one map from
# AuthError subclass to status code, checked with isinstance (first match
# wins). Every route below is a JSON route, so this covers all of them -
# unlike when /login rendered its own HTML form, there's no exception left.
# OtpInvalid needs its own entry - unlike OtpRequired/OtpLocked it does NOT
# subclass AuthenticationError (see sessionkit's errors.py), so without this
# a bad 2FA code at /2fa/confirm (or a bad code at /api/login) would fall
# through to a bare 500.
_ERROR_STATUS: dict[type[AuthError], int] = {
    AuthenticationError: 401,
    OtpInvalid: 422,
    UserNotFound: 404,
    DuplicateUser: 409,
    ValidationError: 422,
}


def create_app(
    auth: AuthService,
    settings: Settings,
    registry: ConfigRegistry,
    oidc_signing_key: rsa.RSAPrivateKey | None = None,
) -> FastAPI:
    app = FastAPI(title="identity-system")

    # OIDC is opt-in on whether a signing key was supplied (main.py always
    # supplies one - see load_or_create_signing_key) - this keeps create_app
    # usable without ever touching a key file for a caller that doesn't
    # want the OIDC surface mounted at all.
    if oidc_signing_key is not None:
        app.include_router(
            create_oidc_router(
                auth=auth,
                settings=settings,
                lookup_client=registry.get_oidc_client,
                signing_key=oidc_signing_key,
            )
        )

    async def _handle_auth_error(_request: Request, exc: AuthError) -> JSONResponse:
        status = next((s for t, s in _ERROR_STATUS.items() if isinstance(exc, t)), 400)
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    for exc_type in _ERROR_STATUS:
        app.add_exception_handler(exc_type, _handle_auth_error)

    def _token(request: Request) -> str | None:
        return request.cookies.get(settings.cookie_name)

    def current_user(request: Request) -> User:
        # AuthenticationError propagates to the handler above -> 401 JSON
        return auth.user_for_token(_token(request))

    def require_admin(user: User = Depends(current_user)) -> User:
        # is_admin has no sessionkit exception type of its own (it isn't an
        # AuthError - it's this service's own bespoke allowlist check), so
        # it's a plain HTTPException. Unlike the old HTML page, these routes
        # don't redirect on 401/403 - the frontend's admin pages own that
        # (see frontend/src/Admin.tsx).
        if not settings.is_admin(user.email):
            raise HTTPException(status_code=403, detail="forbidden")
        return user

    def _set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            settings.cookie_name,
            token,
            max_age=settings.session_days * 86400,
            path="/",
            domain=settings.cookie_domain or None,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )

    def _clear_session_cookie(response: Response) -> None:
        response.delete_cookie(
            settings.cookie_name, path="/", domain=settings.cookie_domain or None
        )

    def _user_json(user: User) -> dict:
        # is_admin is only here so the frontend can decide which admin pages
        # to show - every admin route still enforces it server-side.
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "totp_enabled": user.totp_enabled,
            "is_admin": settings.is_admin(user.email),
        }

    def _oidc_client_json(client: OidcClient) -> dict:
        # Never the hash - and the plaintext secret only ever appears in the
        # create/rotate responses, once.
        return {
            "client_id": client.client_id,
            "redirect_uris": sorted(client.redirect_uris),
            "allowed_scopes": sorted(client.allowed_scopes),
        }

    # ------------------------------------------------------------- login

    @app.post("/api/login")
    def api_login(payload: dict):
        result = auth.login(
            payload.get("email", ""),
            payload.get("password", ""),
            otp=payload.get("otp") or None,
        )
        # Computed server-side, same as the old HTML form's redirect target -
        # the frontend must navigate to this value, never the raw ?rd= it
        # was given, or is_trusted_redirect's open-redirect guard would be
        # for nothing (see CLAUDE.md).
        target = (
            payload.get("rd", "")
            if settings.is_trusted_redirect(payload.get("rd", ""))
            else "/account"
        )
        response = JSONResponse(
            {"user": _user_json(result.user), "redirect_to": target}
        )
        _set_session_cookie(response, result.token)
        return response

    @app.post("/logout")
    def logout(request: Request):
        auth.logout(_token(request))
        # nginx serves the SPA shell for this path now, not a server-rendered
        # form - the redirect target doesn't change.
        response = RedirectResponse(url="/login", status_code=303)
        _clear_session_cookie(response)
        return response

    # ------------------------------------------------------- forward-auth

    @app.get("/verify")
    def verify(request: Request) -> Response:
        try:
            user = auth.user_for_token(_token(request))
        except AuthenticationError:
            return Response(status_code=401)
        return Response(
            status_code=200,
            headers={
                "X-Auth-User-Id": user.id or "",
                "X-Auth-User-Email": user.email,
                "X-Auth-User-Name": user.name,
            },
        )

    @app.get("/me")
    def me(user: User = Depends(current_user)):
        return _user_json(user)

    # ----------------------------------------------------------- profile

    @app.patch("/profile")
    def update_profile(payload: dict, user: User = Depends(current_user)):
        if "name" in payload:
            auth.set_name(user.id, payload["name"])
        if "email" in payload:
            auth.set_email(
                user.id, payload["email"], current_password=payload.get("current_password")
            )
        # AuthService has no get-by-id lookup to return the refreshed record
        # from here (only find_user(email), and list_users()) - the client
        # re-fetches GET /me instead of this response guessing at the result.
        return Response(status_code=204)

    @app.post("/password")
    def change_password(payload: dict, user: User = Depends(current_user)):
        auth.set_password(user.id, payload.get("new_password", ""))
        return Response(status_code=204)

    # ---------------------------------------------------------------- 2FA

    @app.post("/2fa/setup")
    def setup_2fa(user: User = Depends(current_user)):
        enrol = auth.start_totp_enrollment(user.id)
        return {"secret": enrol.secret, "otpauth_uri": enrol.uri}

    @app.post("/2fa/confirm")
    def confirm_2fa(payload: dict, user: User = Depends(current_user)):
        codes = auth.confirm_totp(user.id, payload.get("otp", ""))
        return {"recovery_codes": codes}

    @app.post("/2fa/disable")
    def disable_2fa(payload: dict, user: User = Depends(current_user)):
        auth.disable_totp(user.id, current_password=payload.get("current_password"))
        return Response(status_code=204)

    @app.post("/2fa/recovery-codes/regenerate")
    def regenerate_recovery_codes(payload: dict, user: User = Depends(current_user)):
        codes = auth.regenerate_recovery_codes(user.id, payload.get("current_password", ""))
        return {"recovery_codes": codes}

    # --------------------------------------------------------------- admin

    def _admin_user_json(u: User) -> dict:
        return {
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "totp_enabled": u.totp_enabled,
        }

    @app.get("/api/admin/users")
    def admin_users(_admin: User = Depends(require_admin)):
        return [_admin_user_json(u) for u in auth.list_users()]

    @app.post("/api/admin/users", status_code=201)
    def admin_create_user(payload: dict, _admin: User = Depends(require_admin)):
        # Email format and password-strength rules live in AuthService
        # (DuplicateUser -> 409, ValidationError -> 422 via _ERROR_STATUS),
        # same as every other sessionkit-backed route here - this is the one
        # place besides the CLI (`sessionkit add`) that calls create_user.
        user = auth.create_user(
            payload.get("email", ""), payload.get("password", ""), name=payload.get("name") or None
        )
        return _admin_user_json(user)

    # ---------------------------------------------------------------- apps

    @app.get("/api/apps")
    def list_apps():
        # Public, deliberately unauthenticated - the apps directory (this
        # service's own default landing page) is meant to be visible
        # whether or not the visitor is signed in. Managed from the admin
        # page (see the /api/admin/apps routes below).
        return [
            {"name": a["name"], "url": a["url"], "description": a["description"]}
            for a in registry.list_apps()
        ]

    @app.get("/api/admin/apps")
    def admin_list_apps(_admin: User = Depends(require_admin)):
        return registry.list_apps()

    @app.post("/api/admin/apps", status_code=201)
    def admin_add_app(payload: AppIn, _admin: User = Depends(require_admin)):
        app_id = registry.add_app(payload.name, payload.url, payload.description)
        return {"id": app_id, **payload.model_dump()}

    @app.put("/api/admin/apps/{app_id}")
    def admin_update_app(app_id: int, payload: AppIn, _admin: User = Depends(require_admin)):
        if not registry.update_app(app_id, payload.name, payload.url, payload.description):
            raise HTTPException(status_code=404, detail="app not found")
        return {"id": app_id, **payload.model_dump()}

    @app.delete("/api/admin/apps/{app_id}")
    def admin_delete_app(app_id: int, _admin: User = Depends(require_admin)):
        if not registry.delete_app(app_id):
            raise HTTPException(status_code=404, detail="app not found")
        return Response(status_code=204)

    # ------------------------------------------------------- oidc clients

    @app.get("/api/admin/oidc-clients")
    def admin_list_oidc_clients(_admin: User = Depends(require_admin)):
        return [_oidc_client_json(c) for c in registry.list_oidc_clients()]

    @app.post("/api/admin/oidc-clients", status_code=201)
    def admin_add_oidc_client(payload: OidcClientIn, _admin: User = Depends(require_admin)):
        secret = generate_client_secret()
        client = OidcClient(
            client_id=payload.client_id,
            client_secret_hash=hash_client_secret(secret),
            redirect_uris=frozenset(payload.redirect_uris),
            allowed_scopes=frozenset(payload.allowed_scopes),
        )
        if not registry.add_oidc_client(client):
            raise HTTPException(status_code=409, detail="client_id already registered")
        # The one and only time the plaintext secret leaves the server.
        return {**_oidc_client_json(client), "client_secret": secret}

    @app.put("/api/admin/oidc-clients/{client_id}")
    def admin_update_oidc_client(
        client_id: str, payload: OidcClientUpdate, _admin: User = Depends(require_admin)
    ):
        updated = registry.update_oidc_client(
            client_id, frozenset(payload.redirect_uris), frozenset(payload.allowed_scopes)
        )
        if not updated:
            raise HTTPException(status_code=404, detail="client not found")
        return _oidc_client_json(registry.get_oidc_client(client_id))

    @app.post("/api/admin/oidc-clients/{client_id}/rotate-secret")
    def admin_rotate_oidc_client_secret(client_id: str, _admin: User = Depends(require_admin)):
        secret = generate_client_secret()
        if not registry.set_oidc_client_secret_hash(client_id, hash_client_secret(secret)):
            raise HTTPException(status_code=404, detail="client not found")
        return {"client_id": client_id, "client_secret": secret}

    @app.delete("/api/admin/oidc-clients/{client_id}")
    def admin_delete_oidc_client(client_id: str, _admin: User = Depends(require_admin)):
        if not registry.delete_oidc_client(client_id):
            raise HTTPException(status_code=404, detail="client not found")
        return Response(status_code=204)

    return app


__all__ = ["create_app"]
