# CLAUDE.md

A central login service for [sessionkit](https://github.com/atownsend247/bb-py-sessionkit)-backed
apps: sits behind a reverse proxy's forward-auth hook (`GET /verify`) and is
the one place a browser types a password (`/login`). Also a minimal OAuth2
Authorization Code + PKCE + OIDC provider (`/.well-known/openid-configuration`,
`/api/oidc/*` — see `oidc.py`) for relying parties that can't sit behind the
proxy and speak OIDC themselves (Jenkins' bundled plugin was the first) —
deliberately minimal: no self-service registration (clients are registered by
an admin on `/admin/oidc-clients`) and no consent screen (any registered client
is auto-approved, same trust posture as the admin allowlist). Also its own small
user-facing hub: `/` is an apps directory (the default landing page, public
whether or not you're signed in, managed by admins at `/admin/apps`) and
`/account` is where a signed-in user sees their own details. See `README.md` for the full endpoint list, proxy
wiring examples (nginx `auth_request`, Traefik `ForwardAuth`), and OIDC
client registration.

## Where things are

- `src/identity_system/` — a pure JSON API, no server-rendered HTML at all
  (`frontend/` owns every page — see below).
  - `app.py` — `create_app(auth: AuthService, settings: Settings, registry: ConfigRegistry, oidc_signing_key: rsa.RSAPrivateKey) -> FastAPI`.
    All routes live here. Takes an already-built `AuthService` (same shape as
    sessionkit's own `examples/fastapi_app.py`), the `ConfigRegistry` (apps
    directory + OIDC clients), and the signing key, so tests never touch a
    real file. `oidc_signing_key=None` leaves the OIDC routes unmounted
    entirely. Admin routes all go through `require_admin` (the
    `IDENTITY_SYSTEM_ADMIN_EMAILS` check).
  - `config.py` — `Settings`, read from `IDENTITY_SYSTEM_*` env vars
    (`.env.example` has the full list). `Settings.is_trusted_redirect()` is
    the open-redirect guard for `POST /api/login`'s `rd`; `Settings.is_admin()`
    is the `GET /api/admin/users` allowlist check. `oidc_issuer_url` is a
    separate concept from `issuer` — the latter is only sessionkit's TOTP
    label, don't conflate them.
  - `registry.py` — `ConfigRegistry`, the SQLite-backed apps directory and
    OIDC client registry. Sibling tables (`apps`, `oidc_clients`,
    `identity_meta`) in the same db file as sessionkit's, on their own
    connection — so they sit under the same `data/` protection as accounts.
    Managed from the admin pages; the one-shot `import_legacy` seeds it from
    the old files on first startup, flagged in `identity_meta`.
  - `apps.py` — `load_apps(path)`, reads the legacy `apps.json`. Only used by
    the one-time import now.
  - `oidc_clients.py` — `OidcClient`, plus `load_oidc_clients(path)` (legacy
    import only), `generate_client_secret`, and `hash_client_secret`/
    `verify_client_secret`, which wrap sessionkit's own `Argon2Hasher` —
    reused, not reimplemented, so client secrets get the same hashing
    treatment as user passwords. A generated secret is returned once and
    never stored in plaintext.
  - `oidc.py` — the OAuth2/OIDC router (`create_oidc_router`, which takes a
    `lookup_client` callable so a client registered in the admin page works
    without a restart), the in-memory
    one-time-use `AuthorizationCodeStore`, and `load_or_create_signing_key`
    (provisions the RSA signing key on first run, same posture as
    sessionkit provisioning its own db).
  - `main.py` — the only module that reads env vars, opens a real
    `SqliteAuthStore` (and the registry's connection to the same file), reads
    the legacy `apps.json`/`oidc_clients.json` off disk for the one-time
    import, and provisions the OIDC signing key file. `uvicorn
    identity_system.main:app` entrypoint.
- `frontend/` — Vite + React + TypeScript, same toolchain as this fleet's
  other apps (finance-system, invoice-system): React 19, `react-router-dom`,
  oxlint, vitest + testing-library. `frontend/.node-version` pins 22.17.0.
  `src/App.tsx` is the shell (fetches `GET /me` once, tolerating a 401 -
  unlike a downstream app's own frontend, this one must render `/` whether
  or not the visitor is signed in) with one component per page: `Apps.tsx`
  (`/`), `Login.tsx` (`/login`), `Account.tsx` (`/account`), and the
  admin-only pages `Admin.tsx` (`/admin`, users), `AdminApps.tsx`
  (`/admin/apps`) and `AdminOidcClients.tsx` (`/admin/oidc-clients`). The
  admin nav links only render when `GET /me` reports `is_admin`; the pages
  themselves still rely on the API's 401/403 (`useAdminList.ts`). `src/api.ts`
  is the one shared fetch wrapper. `npm run dev`
  proxies everything the backend still owns to `:8000` (see
  `vite.config.ts`) - run that alongside `uvicorn identity_system.main:app --reload`
  for local dev, same two-process shape as finance-system.
- `apps.json` (repo root) — the legacy apps-directory seed. Imported into the
  db exactly once, on first startup; after that the apps directory is managed
  at `/admin/apps`. Editing this file does nothing on a live box.
- `oidc_clients.json` (gitignored — `oidc_clients.json.example` is the
  committed template) — the legacy OIDC client seed, same one-time import.
  Hashes carry over as-is. Nothing reads it after the import.
- `tests/conftest.py` — `db` (in-memory sqlite connection), `store`
  (`SqliteAuthStore` over it), `registry` (`ConfigRegistry` over the same
  connection, as main.py shares one file), `settings` (fixed test `Settings`),
  `auth` (`AuthService` over `store`), `apps` and `oidc_clients` (these seed
  the registry with a small app and one test client; `oidc_clients` returns
  the registered client, and the fixture gives you the plaintext secret since
  you need it to drive `/api/oidc/token`'s client auth — only its hash is
  stored), `oidc_signing_key` (ephemeral in-memory RSA key),
  `client` (`TestClient` — note its `base_url` is `http://sso.example.com`,
  not the default `testserver`, because the session cookie is scoped to
  `settings.cookie_domain` and a mismatched host would silently drop it
  between requests).
- `deploy/` — no Dockerfile; this ships to a Debian/Proxmox LXC container
  like the other apps. New frontend page routes need a matching `location =`
  in `nginx-identity-system.conf`, or a refresh 404s. `deploy.sh` (run from Jenkins — see `Jenkinsfile`)
  rsyncs the checkout (including the `frontend/dist/` the Jenkinsfile's
  Frontend stage builds first) to the container and runs `remote-setup.sh`
  there over SSH, which builds/refreshes a plain venv (`pip install .` — no
  uv.lock in this repo) and installs `identity-system-api.service` +
  `nginx-identity-system.conf`. nginx serves `frontend/dist/` directly for
  the frontend's own page routes and proxies everything else to the backend
  (see the nginx config's own comment for why it's proxy-by-default rather
  than static-by-default). `__BACKEND_DIR__/.env`, `.../data/`, and
  `*.db` are never touched by the rsync, so a hand-provisioned `.env` and the
  live `auth.db` (which now also holds the apps directory and the OIDC client
  registry, plus, by default, the OIDC signing key under `data/`) all survive
  every deploy — see the `.service` file's header comment for the one-time
  setup that requires.

## Commands

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest

cp .env.example .env                             # then edit it
sessionkit --db ./auth.db add you@example.com     # provision an account first
uvicorn identity_system.main:app --reload         # terminal 1 - backend, :8000

cd frontend && npm install
npm run dev                                       # terminal 2 - frontend, :5173
```

## Rules (don't violate)

- **This depends on sessionkit; it doesn't reimplement it.** Every rule
  (password hashing, session lifetime, TOTP lockout, email/password
  validation) lives in `AuthService`/`AuthStore`. A route handler's job is
  HTTP concerns only — reading the cookie, mapping `AuthError` subclasses to
  status codes. If a change feels like it belongs in sessionkit instead (a
  new rule, a new field), it probably does — make it there, cut a release,
  bump the pin here (see below).
- **The `sessionkit` dependency in `pyproject.toml` is a pinned git tag**
  (`sessionkit @ git+https://github.com/atownsend247/bb-py-sessionkit.git@vX.Y.Z`),
  not a path or a branch — same reasoning as sessionkit's own README: a
  direct git reference has no `>=`/`~=` range syntax, so pinning a tag is the
  only way to get a reproducible install and a deliberate, visible bump.
  `tool.hatch.metadata.allow-direct-references = true` is required for
  hatchling to accept that dependency form at all — don't remove it.
- **`_ERROR_STATUS` in `app.py` needs an entry for every `AuthError`
  subclass that can reach a route**, including ones that don't subclass
  `AuthenticationError` — `OtpInvalid` is the trap: unlike
  `OtpRequired`/`OtpLocked` it's a direct `AuthError` subclass, so it's easy
  to forget and get a bare 500 instead of a 422 the first time a route that
  can raise it (`/2fa/confirm`, `POST /api/login`) is exercised for real.
  Every route is a plain JSON route now — there's no HTML-rendering
  exception left the way `/login` used to be.
- **Every `rd` (and any future redirect target) must go through
  `Settings.is_trusted_redirect()` before being used**, and the frontend
  must navigate to whatever `POST /api/login` hands back as `redirect_to`,
  never to the raw `?rd=` from the URL directly (see `frontend/src/Login.tsx`).
  It's the only thing stopping `/login?rd=https://evil.example/phish` from
  being a working phishing link.
- **Bare legacy paths can't move or change shape**: `POST /logout`,
  `GET /verify`, `GET /me`, `PATCH /profile`, `POST /password`,
  `POST /2fa/*`. Other apps' own nginx `auth_request` configs and
  finance-system's already-deployed frontend hardcode these (see
  `finance-system/frontend/src/App.tsx`'s `IDENTITY_SYSTEM_ORIGIN`).
  Anything new goes under `/api/` instead (`/api/login`, `/api/admin/users`,
  `/api/apps`). `GET /.well-known/openid-configuration` is the one
  deliberate exception — that path is OIDC-spec-fixed, not a choice made
  here.

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
- **No CSRF token on `/api/login`.** `SameSite=Lax` on the session cookie
  covers the common cross-site case but isn't a complete answer —
  documented gap, not an oversight (same posture sessionkit itself takes
  with login rate-limiting).
- **No per-app authorization model.** `/verify` and `/me` answer "who is
  this", not "can they do X in this app" — sessionkit has no roles/scopes
  concept. Each downstream app owns its own authorization decisions based on
  the identity it's given. `GET /api/admin/users` is the one exception: it's
  this service's own page, not a downstream app's, so it makes its own call
  via a static `IDENTITY_SYSTEM_ADMIN_EMAILS` allowlist (`Settings.is_admin()`)
  rather than a real role stored anywhere — no in-app management, edit
  `.env` and restart to change who's on it.
- **`SqliteAuthStore` is single-writer-friendly, not a production multi-app
  answer.** Fine for one instance; a real multi-writer deployment should
  implement `AuthStore` against Postgres instead (structural change only —
  see sessionkit's `docs/architecture.md#bring-your-own-storage`).
- **The apps directory and OIDC client registry are edited in the db, not
  in files.** `apps.json`/`oidc_clients.json` are only imported on first
  startup (see `main.py`, `identity_meta`'s `legacy_import_done` flag). Don't
  "fix" a deploy that appears to ignore a change to those files by re-running
  the import — the admin pages are the source of truth now, and a re-import
  would duplicate rows.
- **Deployed `IDENTITY_SYSTEM_DB` must resolve inside `__BACKEND_DIR__/data/`.**
  `deploy.sh`'s rsync `--delete` only spares `data/`, `.env`, and
  `*.db`/`*.db-*` (belt and suspenders) - `.env.example`'s own local-dev
  default (`./auth.db`, relative to `WorkingDirectory=__BACKEND_DIR__`)
  resolves *outside* `data/` if it's ever deployed unedited, and gets
  deleted the next deploy. `remote-setup.sh` checks this and warns loudly;
  don't remove that check.
- **The admin-managed OIDC client registry is hashes in `auth.db`.** Losing
  that db loses every registered client's credentials, same as losing the
  accounts — so the same `data/` rule applies, and a client secret that's
  lost can only be rotated, never read back.
- **Same rule, same check, for `IDENTITY_SYSTEM_OIDC_SIGNING_KEY_PATH`** —
  it has no `*.db`-style belt-and-suspenders exclude of its own, so `data/`
  is the only thing protecting it. Losing it isn't just data loss: a
  silently-regenerated key rotates every relying party's trust anchor and
  breaks SSO for all of them until they re-fetch `/api/oidc/jwks.json`.
- **OIDC authorization codes are in-memory only, by design** (see `oidc.py`'s
  module docstring) — don't "fix" a restart dropping in-flight codes by
  reaching for a db table without re-reading that reasoning first; it's a
  deliberate simplicity/availability tradeoff tied to the single-instance
  `SqliteAuthStore` posture below, not an oversight.
- **`/api/oidc/authorize`'s `redirect_uri` check is an exact match against
  the registry**, not `Settings.is_trusted_redirect()`'s domain-suffix
  match — don't reuse that helper here. A domain-suffix match would let
  anyone who can stand up a page anywhere under the cookie domain receive
  another relying party's authorization code.
