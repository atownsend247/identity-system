# identity-system

A central login service for [sessionkit](https://github.com/atownsend247/bb-py-sessionkit)-backed
apps. Sits behind a reverse proxy's forward-auth hook — every request to a
protected app is checked with `GET /verify` first; an unauthenticated
browser is sent to `/login` and, once signed in, back to wherever it came
from. Also its own small hub: `/` is an apps directory (the default landing
page, visible whether or not you're signed in) and `/account` is where a
signed-in user sees their own details.

The forward-auth model works when every app behind it is yours (or
otherwise trusts the same proxy) and can sit behind that proxy in the first
place — it's not a fit for something like Jenkins, which ships its own
built-in OpenID Connect security realm instead. For that handful of cases,
identity-system is *also* a small OAuth2 Authorization Code + PKCE + OIDC
provider (`/.well-known/openid-configuration`, `/api/oidc/*`) — see
[OIDC for other relying parties](#oidc-for-other-relying-parties) below.
It's a minimal one: a static client registry (no self-service client
registration) and no consent screen (any registered client is auto-approved
for a signed-in user — the same trust posture as `apps.json`).

## How it fits together

```
 browser ──▶ reverse proxy ──▶ protected app (app1.example.com, app2...)
                 │
                 │ auth_request / ForwardAuth
                 ▼
          identity-system  (sso.example.com)
             /verify   -> 200 + X-Auth-User-* headers, or 401
             /login    -> the only place a human types a password
             /         -> the apps directory (see apps.json)
             /account  -> a signed-in user's own details
```

`AuthService` + `AuthStore` do all the actual rules (password hashing,
sessions, TOTP, lockouts) — the backend (`src/identity_system/`) is a thin
FastAPI JSON API around them, following the same shape as sessionkit's own
[`examples/fastapi_app.py`](https://github.com/atownsend247/bb-py-sessionkit/blob/main/examples/fastapi_app.py).
Every page (`/`, `/login`, `/admin`, `/account`) is client-rendered by the
Vite/React app in `frontend/` — the backend has no HTML of its own.

## Run it locally

Two processes: the backend API and the frontend dev server.

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # then edit IDENTITY_SYSTEM_COOKIE_DOMAIN etc.

# provision the first account, against the same db the service will open
sessionkit --db ./auth.db add you@example.com

uvicorn identity_system.main:app --reload   # terminal 1, :8000
```

```sh
cd frontend
npm install
npm run dev                                 # terminal 2, :5173 - proxies the API to :8000
```

Then open `http://localhost:5173/`.

## Endpoints

| | |
|---|---|
| `POST /api/login` | JSON `{"email", "password", "otp" (optional), "rd" (optional)}`. Sets the session cookie and returns `{"user": {...}, "redirect_to": <url>}` on success — `redirect_to` is `rd` if it passed `IDENTITY_SYSTEM_COOKIE_DOMAIN` validation, `/account` otherwise. `401`/`422` JSON on failure (bad credentials, missing/bad 2FA code). |
| `POST /logout` | clears the cookie, redirects to `/login`. |
| `GET /verify` | **the forward-auth target.** 200 with `X-Auth-User-Id` / `X-Auth-User-Email` / `X-Auth-User-Name` response headers if the request's cookie is a live session; 401 otherwise. No body either way — cheap to call on every request. |
| `GET /me` | same identity check as `/verify`, as JSON, for an app that wants to call this service directly instead of reading proxy-injected headers. |
| `PATCH /profile` | JSON `{"name": ...}` and/or `{"email": ..., "current_password": ...}`. `current_password` is required to change email, optional (but recommended) — see note below. |
| `POST /password` | JSON `{"new_password": ...}`. **Revokes every other session on the account** (sessionkit's `set_password` always does this) — including the one making the request. |
| `POST /2fa/setup` | starts TOTP enrollment; returns `{"secret", "otpauth_uri"}` to show as a QR code. |
| `POST /2fa/confirm` | JSON `{"otp": "123456"}`; returns `{"recovery_codes": [...]}` (ten, shown once). |
| `POST /2fa/disable` | JSON `{"current_password": ...}`. |
| `POST /2fa/recovery-codes/regenerate` | JSON `{"current_password": ...}`; returns a fresh set of ten. |
| `GET /api/admin/users` | every account (id, email, name, created, last login, 2FA) as JSON. Restricted to `IDENTITY_SYSTEM_ADMIN_EMAILS` — `401` with no session, `403` if the session isn't on the allowlist. |
| `GET /api/apps` | the static apps directory (`apps.json`) as JSON — public, no session required. |
| `GET /.well-known/openid-configuration` | OIDC discovery document. Spec-fixed path — the one exception to everything new living under `/api/`. |
| `GET /api/oidc/jwks.json` | the public half of the signing key, as a JWK set. |
| `GET /api/oidc/authorize` | the OAuth2 authorization endpoint. No session → redirects to `/login?rd=...` (same machinery as above); signed in → redirects straight back to the client's `redirect_uri` with a one-time `code` (no consent screen — see above). PKCE (`S256`) is supported and verified if the client sends it, but not required — every registered client is confidential (authenticates at `/api/oidc/token` with its `client_secret` regardless), and PKCE exists to protect clients that can't hold one. |
| `POST /api/oidc/token` | exchanges a `code` (+ `code_verifier`) for `{id_token, access_token, token_type, expires_in, scope}`. Client auth via HTTP Basic or `client_secret_post`. |
| `GET /api/oidc/userinfo` | `Authorization: Bearer <access_token>` → the scope-gated claims (`sub`, `email`, `name`). |

All endpoints except `POST /api/login`/`POST /logout`/`GET /verify`/`GET /me`/`GET /api/apps`
require the session cookie; sessionkit's `AuthError` subclasses are mapped
to status codes the same way as the reference FastAPI example (401 / 404 /
409 / 422 — see
[`docs/architecture.md#errors`](https://github.com/atownsend247/bb-py-sessionkit/blob/main/docs/architecture.md#errors)
in sessionkit for the full table, including why `OtpInvalid` needs its own
entry).

**The pages themselves** (`/`, `/login`, `/admin`, `/account`) aren't
backend routes at all — they're client-side routes in `frontend/`. In
production nginx serves the built frontend for those paths directly (see
`deploy/nginx-identity-system.conf`); `npm run dev`'s dev server does the
same locally. A page component (e.g. `frontend/src/Admin.tsx`) calls the
matching JSON endpoint itself and handles a `401`/`403` client-side (e.g.
redirecting to `/login?rd=/admin`) — the backend endpoints above don't
redirect on their own.

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

## OIDC for other relying parties

For an app that speaks OIDC itself rather than sitting behind the reverse
proxy (Jenkins' bundled "OpenId Connect Authentication" plugin was the
first case) — point it at this service's discovery document instead of
wiring up `/verify`:

```
https://sso.example.com/.well-known/openid-configuration
```

**Registering an OIDC client**: there's no self-service registration
endpoint (same posture as account provisioning — see
[Managing accounts](#managing-accounts)). Add an entry to
`IDENTITY_SYSTEM_OIDC_CLIENTS_PATH` (`./oidc_clients.json` by default — see
[`oidc_clients.json.example`](oidc_clients.json.example) for the shape) by
hand:

```sh
python -c "from identity_system.oidc_clients import hash_client_secret; print(hash_client_secret('a-long-random-secret'))"
```

```json
{
  "client_id": "jenkins",
  "client_secret_hash": "<output from above>",
  "redirect_uris": ["https://jenkins.example.com/securityRealm/finishLogin"],
  "allowed_scopes": ["openid", "email", "profile"]
}
```

Restart the service (this file isn't live-reloaded, same as `apps.json`).
`redirect_uris` are matched **exactly** — no domain-suffix matching the way
`?rd=` gets on `/api/login` — so include the full path the relying party
actually redirects back to.

**Jenkins' OpenId Connect plugin**, concretely: install the plugin,
then under *Configure Global Security* → *Security Realm* → *OpenId
Connect*:

- Client ID / Client Secret: the values you just registered above.
- Configuration mode: "Automatic configuration" with the discovery URL
  above (or fill in the individual endpoints from it by hand).
- Scopes: `openid email profile`.

Authorization *inside* Jenkins (who can do what once they're signed in)
stays Jenkins' own Role-Based Authorization Strategy — identity-system
still only answers "who is this", not "what can they do here", the same
posture it takes with every other app (see
[Known gaps](#known-gaps-deliberate-not-oversights)).

## Configuration

See [`.env.example`](.env.example) — `IDENTITY_SYSTEM_DB`,
`IDENTITY_SYSTEM_COOKIE_DOMAIN`, `IDENTITY_SYSTEM_COOKIE_SECURE`,
`IDENTITY_SYSTEM_SESSION_DAYS`, `IDENTITY_SYSTEM_ISSUER`,
`IDENTITY_SYSTEM_ADMIN_EMAILS`, `IDENTITY_SYSTEM_APPS_PATH`,
`IDENTITY_SYSTEM_OIDC_ISSUER_URL`, `IDENTITY_SYSTEM_OIDC_CLIENTS_PATH`,
`IDENTITY_SYSTEM_OIDC_SIGNING_KEY_PATH`.

`IDENTITY_SYSTEM_ADMIN_EMAILS` is a comma-separated, case-insensitive
allowlist gating `GET /api/admin/users` — empty by default, so nobody can
reach it until it's set. There's no in-app role management; sessionkit
itself has no roles/scopes concept (see [Known gaps](#known-gaps-deliberate-not-oversights)),
so this is the one place identity-system makes its own authorization call.

`IDENTITY_SYSTEM_APPS_PATH` points at the JSON file `GET /api/apps` (and so
the `/` apps directory) reads once at startup — `./apps.json` by default;
see [`apps.json`](apps.json) at the repo root for the format. Not
live-reloaded; add or change an app, then restart.

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
- **No CSRF token on `POST /api/login`.** `SameSite=Lax` covers the common
  cross-site case but isn't a complete answer. Same posture sessionkit
  itself takes with rate-limiting: an explicit, documented gap.
- **Authentication only, not authorization.** `/verify` answers "who is
  this", not "can they do X in this app" — sessionkit has no roles/scopes
  model. Each downstream app still owns its own authorization decisions
  based on the identity headers it's given. (`/api/admin/users` is this
  service's own one exception — see `IDENTITY_SYSTEM_ADMIN_EMAILS` above.)
- **`apps.json` is a static file, not a database.** No admin UI to add/edit
  apps yet — deliberately the simplest possible MVP; see `apps.py`.
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
- **No OIDC consent screen.** Any client in `oidc_clients.json` is
  auto-approved for a signed-in user — fine for a handful of relying
  parties you run yourself, not a general-purpose multi-tenant posture.
- **OIDC authorization codes live in memory, not in a database.** One-time
  use, ~60 second lifetime — a restart mid-login just means the user
  retries. Not safe to run multiple instances of this service behind a
  load balancer for that reason (same single-instance caveat as
  `SqliteAuthStore` above).
- **No OIDC refresh tokens, no self-service client registration.** Add a
  relying party by hand-editing `oidc_clients.json` (see
  [OIDC for other relying parties](#oidc-for-other-relying-parties)); a
  relying party re-runs the authorization flow in the browser when its
  session lapses rather than silently refreshing in the background.

## Tests

```sh
pytest                       # backend
cd frontend && npm test      # frontend
```

Backend tests run entirely against an in-memory `SqliteAuthStore` and an
ephemeral in-memory signing key via
`identity_system.app.create_app(auth, settings, apps, oidc_clients, oidc_signing_key)`
— no real file touched. Frontend tests (vitest + testing-library) mock
`fetch` per component — see `frontend/src/Login.test.tsx` for the pattern.
