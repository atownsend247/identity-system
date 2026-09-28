# CLAUDE.md

A central login service for [sessionkit](https://github.com/atownsend247/bb-py-sessionkit)-backed
apps: sits behind a reverse proxy's forward-auth hook (`GET /verify`) and is
the one place a browser types a password (`/login`). Not an OAuth2/OIDC
provider — no client registration, no signed tokens, no consent screens. See
`README.md` for the full endpoint list and proxy wiring examples (nginx
`auth_request`, Traefik `ForwardAuth`).

## Where things are

- `src/identity_system/`
  - `app.py` — `create_app(auth: AuthService, settings: Settings) -> FastAPI`.
    All routes live here. Takes an already-built `AuthService` (same shape as
    sessionkit's own `examples/fastapi_app.py`), so tests never touch a real
    file.
  - `config.py` — `Settings`, read from `IDENTITY_SYSTEM_*` env vars
    (`.env.example` has the full list). `Settings.is_trusted_redirect()` is
    the open-redirect guard for `/login`'s `?rd=`; `Settings.is_admin()` is
    the `GET /admin` allowlist check.
  - `templates.py` — two inline HTML templates (login form, admin user
    list). No JS framework, no build step, no external stylesheet.
  - `main.py` — the only module that reads env vars or opens a real
    `SqliteAuthStore`. `uvicorn identity_system.main:app` entrypoint.
- `tests/conftest.py` — `store` (in-memory `SqliteAuthStore`), `settings`
  (fixed test `Settings`), `auth` (`AuthService` over both), `client`
  (`TestClient` — note its `base_url` is `http://sso.example.com`, not the
  default `testserver`, because the session cookie is scoped to
  `settings.cookie_domain` and a mismatched host would silently drop it
  between requests).
- `deploy/` — no Dockerfile; this ships to a Debian/Proxmox LXC container
  like the other apps. `deploy.sh` (run from Jenkins — see `Jenkinsfile`)
  rsyncs the checkout to the container and runs `remote-setup.sh` there
  over SSH, which builds/refreshes a plain venv (`pip install .` — no
  uv.lock in this repo), and installs `identity-system-api.service` +
  `nginx-identity-system.conf`. `__BACKEND_DIR__/.env` and `.../data/` are
  never touched by the rsync, so a hand-provisioned `.env` and the live
  `auth.db` both survive every deploy — see the `.service` file's header
  comment for the one-time setup that requires.

## Commands

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest

cp .env.example .env                             # then edit it
sessionkit --db ./auth.db add you@example.com     # provision an account first
uvicorn identity_system.main:app --reload
```

## Rules (don't violate)

- **This depends on sessionkit; it doesn't reimplement it.** Every rule
  (password hashing, session lifetime, TOTP lockout, email/password
  validation) lives in `AuthService`/`AuthStore`. A route handler's job is
  HTTP concerns only — reading the cookie, mapping `AuthError` subclasses to
  status codes, rendering the login form. If a change feels like it belongs
  in sessionkit instead (a new rule, a new field), it probably does — make it
  there, cut a release, bump the pin here (see below).
- **The `sessionkit` dependency in `pyproject.toml` is a pinned git tag**
  (`sessionkit @ git+https://github.com/atownsend247/bb-py-sessionkit.git@vX.Y.Z`),
  not a path or a branch — same reasoning as sessionkit's own README: a
  direct git reference has no `>=`/`~=` range syntax, so pinning a tag is the
  only way to get a reproducible install and a deliberate, visible bump.
  `tool.hatch.metadata.allow-direct-references = true` is required for
  hatchling to accept that dependency form at all — don't remove it.
- **`_ERROR_STATUS` in `app.py` needs an entry for every `AuthError`
  subclass that can reach a JSON route**, including ones that don't
  subclass `AuthenticationError` — `OtpInvalid` is the trap: unlike
  `OtpRequired`/`OtpLocked` it's a direct `AuthError` subclass, so it's easy
  to forget and get a bare 500 instead of a 422 the first time a route that
  can raise it (`/2fa/confirm`) is exercised for real. `/login` and `/admin`
  are the two routes that don't use this map — `/login` catches `AuthError`
  itself so it can re-render the HTML form instead of returning JSON;
  `/admin` catches `AuthenticationError` itself so it can redirect to
  `/login?rd=/admin` instead of returning a bare 401.
- **Every `?rd=` (and any future redirect target) must go through
  `Settings.is_trusted_redirect()` before being used.** It's the only thing
  stopping `/login?rd=https://evil.example/phish` from being a working
  phishing link.

## Gotchas

- **`POST /password` does not re-check the caller's current password.**
  Unlike `set_email`/`disable_totp`, sessionkit's `AuthService.set_password`
  takes no `current_password` argument — the session cookie is the only
  proof of identity this route requires. Don't assume otherwise; if stronger
  reverification is needed, it has to be added to sessionkit first (there's
  no private hook to reach into from here).
- **No self-registration endpoint, deliberately.** Accounts are provisioned
  with sessionkit's bundled CLI against the same db file
  (`sessionkit --db <path> add ...` — note `--db` is a *top-level* arg,
  it goes before the subcommand, not after). `AuthService.create_user`
  already does the work if self-service signup is wanted later.
- **No CSRF token on the login form.** `SameSite=Lax` on the session cookie
  covers the common cross-site POST case but isn't a complete answer —
  documented gap, not an oversight (same posture sessionkit itself takes
  with login rate-limiting).
- **No per-app authorization model.** `/verify` and `/me` answer "who is
  this", not "can they do X in this app" — sessionkit has no roles/scopes
  concept. Each downstream app owns its own authorization decisions based on
  the identity it's given. `GET /admin` is the one exception: it's this
  service's own page, not a downstream app's, so it makes its own call via
  a static `IDENTITY_SYSTEM_ADMIN_EMAILS` allowlist (`Settings.is_admin()`)
  rather than a real role stored anywhere — no in-app management, edit
  `.env` and restart to change who's on it.
- **`SqliteAuthStore` is single-writer-friendly, not a production multi-app
  answer.** Fine for one instance; a real multi-writer deployment should
  implement `AuthStore` against Postgres instead (structural change only —
  see sessionkit's `docs/architecture.md#bring-your-own-storage`).
