"""The identity-system FastAPI app: a login page plus the JSON endpoints a
reverse proxy's forward-auth hook and downstream apps call.

``create_app(auth, settings)`` takes an already-constructed ``AuthService``
(same shape as sessionkit's own ``examples/fastapi_app.py``), so tests can
build one over an in-memory store with no real files touched.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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
from .templates import render_login

# Same shape as sessionkit's own examples/fastapi_app.py: one map from
# AuthError subclass to status code, checked with isinstance (first match
# wins), covering every JSON endpoint. /login is the one route that handles
# its errors itself (it needs to re-render the HTML form, not return JSON).
# OtpInvalid needs its own entry - unlike OtpRequired/OtpLocked it does NOT
# subclass AuthenticationError (see sessionkit's errors.py), so without this
# a bad 2FA code at /2fa/confirm would fall through to a bare 500.
_ERROR_STATUS: dict[type[AuthError], int] = {
    AuthenticationError: 401,
    OtpInvalid: 422,
    UserNotFound: 404,
    DuplicateUser: 409,
    ValidationError: 422,
}


def create_app(auth: AuthService, settings: Settings) -> FastAPI:
    app = FastAPI(title="identity-system")

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

    # ------------------------------------------------------------- login

    @app.get("/login", response_class=HTMLResponse)
    def login_form(rd: str = "") -> str:
        return render_login(rd=rd)

    @app.post("/login")
    def login_submit(
        email: str = Form(""),
        password: str = Form(""),
        otp: str = Form(""),
        rd: str = Form(""),
    ):
        try:
            result = auth.login(email, password, otp=otp or None)
        except AuthError as exc:
            # covers AuthenticationError (bad creds), OtpRequired, OtpLocked
            # and OtpInvalid - login() raises OtpInvalid as an authentication
            # failure, per docs/architecture.md#errors, so it belongs here
            # too rather than being re-shown as a form-validation error.
            return HTMLResponse(
                render_login(rd=rd, email=email, error=str(exc)), status_code=401
            )
        target = rd if settings.is_trusted_redirect(rd) else "/me"
        response = RedirectResponse(url=target, status_code=303)
        _set_session_cookie(response, result.token)
        return response

    @app.post("/logout")
    def logout(request: Request):
        auth.logout(_token(request))
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
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "totp_enabled": user.totp_enabled,
        }

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

    return app


__all__ = ["create_app"]
