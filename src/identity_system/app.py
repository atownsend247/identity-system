"""The identity-system FastAPI app: the JSON API a reverse proxy's
forward-auth hook, downstream apps, and this service's own Vite/React
frontend (``frontend/``) all call. There's no server-rendered HTML here -
``/login``, ``/admin``, ``/`` (apps directory) and ``/account`` are pure
client-side routes; nginx serves the built frontend for those in
production (see ``deploy/nginx-identity-system.conf``) and Vite's dev
server proxies everything below to this app locally (see
``frontend/vite.config.ts``).

``create_app(auth, settings, apps, oidc_clients, oidc_signing_key)`` takes
an already-constructed ``AuthService`` (same shape as sessionkit's own
``examples/fastapi_app.py``), the static apps-directory list, and the
(also static) OIDC client registry + signing key the ``/api/oidc/*`` +
``/.well-known/openid-configuration`` routes need (see ``oidc.py``) - so
tests can build one over an in-memory store with no real files touched.
Passing no signing key leaves the OIDC surface unmounted entirely.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

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
from .oidc_clients import OidcClient

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
    apps: list[dict] | None = None,
    oidc_clients: dict[str, OidcClient] | None = None,
    oidc_signing_key: rsa.RSAPrivateKey | None = None,
) -> FastAPI:
    app = FastAPI(title="identity-system")
    apps = apps or []

    # OIDC is opt-in on whether a signing key was supplied (main.py always
    # supplies one - see load_or_create_signing_key) - this keeps create_app
    # usable without ever touching a key file for a caller that doesn't
    # want the OIDC surface mounted at all.
    if oidc_signing_key is not None:
        app.include_router(
            create_oidc_router(
                auth=auth,
                settings=settings,
                clients=oidc_clients or {},
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
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "totp_enabled": user.totp_enabled,
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

    @app.get("/api/admin/users")
    def admin_users(user: User = Depends(current_user)):
        # Depends(current_user) alone gives the standard 401 via
        # _ERROR_STATUS; is_admin has no sessionkit exception type of its
        # own (it isn't an AuthError - it's this service's own bespoke
        # allowlist check), so it's a plain HTTPException instead. Unlike
        # the old HTML page, this route doesn't redirect on 401/403 - the
        # frontend's Admin page owns that (see frontend/src/Admin.tsx).
        if not settings.is_admin(user.email):
            raise HTTPException(status_code=403, detail="forbidden")
        return [
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                "totp_enabled": u.totp_enabled,
            }
            for u in auth.list_users()
        ]

    # ---------------------------------------------------------------- apps

    @app.get("/api/apps")
    def list_apps():
        # Public, deliberately unauthenticated - the apps directory (this
        # service's own default landing page) is meant to be visible
        # whether or not the visitor is signed in. See apps.py/apps.json.
        return apps

    return app


__all__ = ["create_app"]
