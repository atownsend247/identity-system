# identity-system

A central login service for [sessionkit](https://github.com/atownsend247/bb-py-sessionkit)-backed
apps. Sits behind a reverse proxy's forward-auth hook — every request to a
protected app is checked with `GET /verify` first; an unauthenticated
browser is sent to `/login` and, once signed in, back to wherever it came
from.

Deliberately not an OAuth2/OIDC provider: no client registration, no signed
tokens, no consent screens. It works when every app behind it is yours (or
otherwise trusts the same proxy) — the proxy is what enforces the check, not
each app individually.

## How it fits together

```
 browser ──▶ reverse proxy ──▶ protected app (app1.example.com, app2...)
                 │
                 │ auth_request / ForwardAuth
                 ▼
          identity-system  (sso.example.com)
             /verify   -> 200 + X-Auth-User-* headers, or 401
             /login    -> the only place a human types a password
```

`AuthService` + `AuthStore` do all the actual rules (password hashing,
sessions, TOTP, lockouts) — this repo is a thin FastAPI wrapper around them,
following the same shape as sessionkit's own
[`examples/fastapi_app.py`](https://github.com/atownsend247/bb-py-sessionkit/blob/main/examples/fastapi_app.py).

## Run it locally

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # then edit IDENTITY_SYSTEM_COOKIE_DOMAIN etc.

# provision the first account, against the same db the service will open
sessionkit --db ./auth.db add you@example.com

uvicorn identity_system.main:app --reload
```

Then open `http://localhost:8000/login`.

## Endpoints

| | |
|---|---|
| `GET /login` | the sign-in page. `?rd=<url>` is where a successful login redirects to (validated against `IDENTITY_SYSTEM_COOKIE_DOMAIN` — an off-domain `rd` is ignored, falls back to `/me`). |
| `POST /login` | form fields `email`, `password`, `otp` (optional), `rd`. Sets the session cookie and redirects on success; re-renders the form with a 401 on failure. |
| `POST /logout` | clears the cookie, redirects to `/login`. |
| `GET /verify` | **the forward-auth target.** 200 with `X-Auth-User-Id` / `X-Auth-User-Email` / `X-Auth-User-Name` response headers if the request's cookie is a live session; 401 otherwise. No body either way — cheap to call on every request. |
| `GET /me` | same identity check as `/verify`, as JSON, for an app that wants to call this service directly instead of reading proxy-injected headers. |
| `PATCH /profile` | JSON `{"name": ...}` and/or `{"email": ..., "current_password": ...}`. `current_password` is required to change email, optional (but recommended) — see note below. |
| `POST /password` | JSON `{"new_password": ...}`. **Revokes every other session on the account** (sessionkit's `set_password` always does this) — including the one making the request. |
| `POST /2fa/setup` | starts TOTP enrollment; returns `{"secret", "otpauth_uri"}` to show as a QR code. |
| `POST /2fa/confirm` | JSON `{"otp": "123456"}`; returns `{"recovery_codes": [...]}` (ten, shown once). |
| `POST /2fa/disable` | JSON `{"current_password": ...}`. |
| `POST /2fa/recovery-codes/regenerate` | JSON `{"current_password": ...}`; returns a fresh set of ten. |
| `GET /admin` | HTML page listing every account (email, name, created, last login, 2FA). Restricted to `IDENTITY_SYSTEM_ADMIN_EMAILS` — no session redirects to `/login?rd=/admin`; a session that isn't on the allowlist gets a `403`. |

All endpoints except `/login`/`/logout`/`/verify`/`/me` require the session
cookie; sessionkit's `AuthError` subclasses are mapped to status codes the
same way as the reference FastAPI example (401 / 404 / 409 / 422 — see
[`docs/architecture.md#errors`](https://github.com/atownsend247/bb-py-sessionkit/blob/main/docs/architecture.md#errors)
in sessionkit for the full table, including why `OtpInvalid` needs its own
entry). `/admin`, like `/login`, handles its own auth instead of going
through that map — it's a page a human browses, not a JSON API.

## Wiring up a reverse proxy

**nginx** (`auth_request`):

```nginx
location = /_verify {
    internal;
    proxy_pass http://identity-system/verify;
    proxy_set_header X-Original-URI $request_uri;
}

location / {
    auth_request /_verify;
    auth_request_set $user_email $upstream_http_x_auth_user_email;
    proxy_set_header X-Auth-User-Email $user_email;

    error_page 401 = @login_redirect;
    proxy_pass http://your-app;
}

location @login_redirect {
    return 302 https://sso.example.com/login?rd=$scheme://$http_host$request_uri;
}
```

**Traefik** (`ForwardAuth` middleware, labels form):

```yaml
labels:
  - "traefik.http.middlewares.sso.forwardauth.address=http://identity-system/verify"
  - "traefik.http.middlewares.sso.forwardauth.authResponseHeaders=X-Auth-User-Id,X-Auth-User-Email,X-Auth-User-Name"
  - "traefik.http.routers.your-app.middlewares=sso"
```

Traefik's `ForwardAuth` follows a redirect itself less cleanly than nginx's
`error_page` trick — point its `errorPage`/`address` behaviour at
`/login?rd=...` per Traefik's own forward-auth-with-redirect docs for your
version.

## Configuration

See [`.env.example`](.env.example) — `IDENTITY_SYSTEM_DB`,
`IDENTITY_SYSTEM_COOKIE_DOMAIN`, `IDENTITY_SYSTEM_COOKIE_SECURE`,
`IDENTITY_SYSTEM_SESSION_DAYS`, `IDENTITY_SYSTEM_ISSUER`,
`IDENTITY_SYSTEM_ADMIN_EMAILS`.

`IDENTITY_SYSTEM_ADMIN_EMAILS` is a comma-separated, case-insensitive
allowlist gating `GET /admin` — empty by default, so nobody can reach it
until it's set. There's no in-app role management; sessionkit itself has no
roles/scopes concept (see [Known gaps](#known-gaps-deliberate-not-oversights)),
so this is the one place identity-system makes its own authorization call.

`IDENTITY_SYSTEM_COOKIE_DOMAIN` does double duty: it's the `Domain=` on the
session cookie (so it's shared across every subdomain of it) **and** the
allow-list a post-login `?rd=` redirect is checked against, to stop that
parameter being used for an open redirect.

## Managing accounts

There's no self-registration endpoint (see [Known gaps](#known-gaps-deliberate-not-oversights)
below) — every account is created with sessionkit's own CLI, installed as
the `sessionkit` console script alongside this package. It operates
directly on the SQLite file this service opens, so `--db` — a **top-level**
flag, it goes *before* the subcommand, not after — always has to point at
whatever `IDENTITY_SYSTEM_DB` resolves to.

```sh
sessionkit --db ./auth.db add newperson@example.com --name "New Person"
```

You'll be prompted for a password twice (`Password:` / `Confirm password:`)
— there's no `--password` flag, so this can't be scripted non-interactively.
`--name` is optional; it defaults to the email's local part.

Other account-management subcommands, all in the same
`sessionkit --db <path> <command> ...` form:

| | |
|---|---|
| `list` | list every account (id, email, name). |
| `passwd <email>` | set a new password (prompts, same as `add`). |
| `rename <email> <name>` | change the display name. |
| `set-email <email> <new-email>` | change the login email. |
| `delete <email>` | delete an account. |
| `2fa-disable <email>` | turn off TOTP for an account — the lockout-recovery path if someone loses their authenticator and their recovery codes. |

Run `sessionkit --help` (or `sessionkit <command> --help`) for the full
reference — it's
[sessionkit's own CLI](https://github.com/atownsend247/bb-py-sessionkit/blob/main/src/sessionkit/cli.py),
not something this repo wraps or extends.

**In production**, the CLI lives in the deployed service's own venv, and
the db path is whatever `IDENTITY_SYSTEM_DB` is set to in
`/opt/identity-system/.env` (`/opt/identity-system/data/auth.db` by
default — see `deploy/identity-system-api.service`):

```sh
ssh root@<DEPLOY_HOST>
/opt/identity-system/.venv/bin/sessionkit --db /opt/identity-system/data/auth.db add newperson@example.com
```

## Known gaps (deliberate, not oversights)

- **No self-registration endpoint.** Provision accounts with sessionkit's
  own CLI — see [Managing accounts](#managing-accounts) above. Easy to add
  later (`AuthService.create_user` already does the work) if you want it.
- **No CSRF token on the login form.** `SameSite=Lax` covers the common
  cross-site POST case but isn't a complete answer. Same posture sessionkit
  itself takes with rate-limiting: an explicit, documented gap.
- **Authentication only, not authorization.** `/verify` answers "who is
  this", not "can they do X in this app" — sessionkit has no roles/scopes
  model. Each downstream app still owns its own authorization decisions
  based on the identity headers it's given.
- **`POST /password` doesn't itself re-check the current password** —
  sessionkit's `AuthService.set_password` doesn't take one (unlike
  `set_email`/`disable_totp`, which do). The session cookie is the only
  proof of identity this route requires. Worth hardening if this service
  is exposed somewhere a stolen-but-not-yet-expired cookie is a realistic
  threat.
- **`SqliteAuthStore`** is fine for a single-instance deployment; a
  multi-writer production setup should implement `AuthStore` against
  Postgres instead (structural — see sessionkit's
  `docs/architecture.md#bring-your-own-storage`).

## Tests

```sh
pytest
```

Runs entirely against an in-memory `SqliteAuthStore` via
`identity_system.app.create_app(auth, settings)` — no real file touched.
