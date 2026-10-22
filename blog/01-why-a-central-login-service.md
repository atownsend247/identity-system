# Central login service: One place to type a password - Part 1

*~25 min read*

Every small tool I build for myself eventually needs a login. A finance
tracker, an invoicing app, a dashboard for something plugged in to the wall. The
first time, I wrote the whole thing inline: a user table, Argon2 hashing,
sessions, a password-change form, two-factor enrolment with recovery codes. It
worked, it was even fairly pleasant, and then I looked at the second app on my
list and realised I was about to write all of it again.

This series is about the project I built instead: a small central login
service that sits in front of everything else, and is the only place a browser
ever types a password. This first post covers the idea, the first working
version, and the decisions in it that turned out to matter. Later posts cover
deploying it, giving it a proper frontend, teaching it to speak OpenID Connect,
and moving its configuration out of files and into a database.

I'll show real code throughout. Where I describe a mistake, it's one that is
visible in the repository's history, not a tidied-up version of events.

## The problem with "just add a login"

Authentication is the sort of feature that feels finished the moment it works.
You hash the password, you set a cookie, you check the cookie. Done.

Except it's never one feature. A realistic list for even a hobby-grade login:

- Passwords hashed with a modern, slow algorithm and a per-user salt.
- Opaque, revocable server-side sessions with an expiry.
- Optional TOTP two-factor, which means enrolment, a QR code, confirmation
  that the user's authenticator actually works, recovery codes for when their
  phone doesn't, and a lockout so the six-digit code can't simply be guessed.
- Changing your name, your email and your password, each with different rules
  about whether you need to prove who you are first.
- Somebody to create the accounts in the first place.

That's a lot of surface area to carry in an app whose actual job is to show me
my bank balances. Worse, it's surface area I'd be carrying *per app*. When I
fix a subtle issue in one copy (say, that changing a password should revoke
other sessions), the other copies don't hear about it. Within a year I'd have
four slightly different login systems, each one the only copy that knew a
particular lesson.

So the question became: can one service own all of that, and every other app
simply believe it?

## The forward-auth pattern

Reverse proxies have a feature for exactly this. nginx calls it
`auth_request`; Traefik calls it `ForwardAuth`; the idea is identical. Before
the proxy forwards a request to a protected app, it makes a small subrequest to
an authentication service and asks one question: *is this request allowed?*

- If the auth service answers `200`, the proxy forwards the original request to
  the app. The `200` response can carry headers (who the user is), and the
  proxy copies those onto the forwarded request.
- If it answers `401`, the proxy doesn't forward anything. Typically it
  redirects the browser to the auth service's login page instead.

Drawn out, with three moving parts:

```
 browser ──▶ reverse proxy ──▶ protected app (app1.example.com, app2...)
                 │
                 │ auth_request / ForwardAuth
                 ▼
          identity-system  (sso.example.com)
             /verify   -> 200 + X-Auth-User-* headers, or 401
             /login    -> the only place a human types a password
```

The protected app never sees a password, never sets a session cookie, and never
needs a user table. It reads a header that says who the proxy vouched for, and
it believes it, because the only way a request reaches the app is through the
proxy. (That belief is only safe if the proxy overwrites those headers
unconditionally, so a client can't just send its own. I'll come back to that in
the wiring section.)

The session cookie lives on the *parent domain*, say `.example.com`, so the
browser sends it to `sso.example.com`, `app1.example.com` and
`app2.example.com` alike. Sign in once at the login service and every app
behind the proxy can see you.

The service is called `identity-system`, and its entire first-version contract
fits in two sentences. `GET /verify` takes the cookie and answers yes or no.
`/login` is where a human proves who they are.

## Standing on a library, not rebuilding one

I didn't write the hard parts here. They already existed in a library of mine
called [sessionkit](https://github.com/atownsend247/bb-py-sessionkit): an
`AuthService` that owns the rules (password hashing with Argon2, session
lifetime, TOTP and its lockout, email and password validation) over an
`AuthStore` that owns persistence. It ships a SQLite-backed store, and a small
CLI for provisioning accounts.

That split shaped the whole project, and it's worth stating as a rule because
it kept me honest for the rest of the build:

> A route handler's job is HTTP concerns only: reading the cookie, mapping
> errors to status codes. Every actual *rule* lives in `AuthService`.

If I ever find myself wanting to add a rule here (a new password policy, a new
account field), that's a sign it belongs in the library, with a release and a
version bump, not in this service. That rule ended up written into the
project's `CLAUDE.md` almost verbatim, and it shows in the dependency itself:

```toml
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.30",
    "python-multipart>=0.0.9",
    "sessionkit @ git+https://github.com/atownsend247/bb-py-sessionkit.git@v0.3.0",
]

[tool.hatch.metadata]
# sessionkit isn't on PyPI - it's a direct git+https reference, which
# hatchling refuses to package unless this is explicitly opted into.
allow-direct-references = true
```

Two details there are worth a sentence each. The dependency is a *pinned git
tag*, not a branch, because a direct git reference has no `>=` range syntax and
a tag is the only way to get a reproducible install and a deliberate, visible
bump. And `allow-direct-references` is required for the build backend to accept
that form of dependency at all; without it the build fails with an error that
doesn't obviously point at the cause.

## The first version of the app

The whole first commit is about 1,200 lines including tests, deployment scripts
and docs, but the application itself is one file of roughly 180 lines. I'll walk
through the interesting parts in the order a request would meet them.

### Building the app from parts

```python
def create_app(auth: AuthService, settings: Settings) -> FastAPI:
    app = FastAPI(title="identity-system")
    ...
```

`create_app` takes an already-built `AuthService` and a `Settings` object
instead of constructing them itself. This is the standard dependency-injection
move, but the payoff here is concrete: the tests can hand it an `AuthService`
over an in-memory SQLite database, so no test ever touches a real file. The
only module that reads environment variables or opens a real database is
`main.py`, a rule that held for the whole project.

### Turning library errors into HTTP status codes

`AuthService` signals problems by raising exceptions that all descend from
`AuthError`. The app has one table mapping those to HTTP statuses:

```python
_ERROR_STATUS: dict[type[AuthError], int] = {
    AuthenticationError: 401,
    OtpInvalid: 422,
    UserNotFound: 404,
    DuplicateUser: 409,
    ValidationError: 422,
}


async def _handle_auth_error(_request: Request, exc: AuthError) -> JSONResponse:
    status = next((s for t, s in _ERROR_STATUS.items() if isinstance(exc, t)), 400)
    return JSONResponse(status_code=status, content={"detail": str(exc)})

for exc_type in _ERROR_STATUS:
    app.add_exception_handler(exc_type, _handle_auth_error)
```

Each route can then just call the service and let exceptions propagate. There's
no `try/except` in the handlers for the ordinary failure cases, which keeps them
short and, more importantly, keeps the status-code policy in one place.

There's a trap in that table. Look at `OtpInvalid`. The other two-factor errors
(`OtpRequired`, `OtpLocked`) are subclasses of `AuthenticationError`, so the
`AuthenticationError: 401` entry covers them via the `isinstance` check.
`OtpInvalid` is a direct `AuthError` subclass instead. It needs its own entry,
or a wrong two-factor code falls through the handler registration entirely and
the user gets a bare `500 Internal Server Error` instead of a polite `422`.

It's an easy thing to forget because nothing about the happy path exercises it.
I now keep this in the project notes as an explicit rule: *every `AuthError`
subclass that can reach a route needs an entry*. It reappears later in the
series, when a new login route has the same exposure.

Why a list lookup with `isinstance` rather than a dictionary `.get(type(exc))`?
Because of subclassing: `OtpRequired` isn't in the table, but it is an
`AuthenticationError`, and an exact-type lookup would miss it. Order matters
too (first match wins), which is why the generic parent classes can sit in the
table without swallowing more specific ones, as long as the specific ones are
listed first.

### The session cookie

Sessions are opaque tokens: random strings with no meaning on their own,
looked up server-side by `AuthService.user_for_token`. That has a useful
property that signed tokens like JWTs don't: logging out, or changing your
password, genuinely invalidates the session, because the server forgets it.

The cookie is set in one helper so every login path gets identical flags:

```python
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
```

Each flag earns its place:

- `httponly=True`: JavaScript can't read the cookie, so a cross-site scripting
  bug can't trivially steal the session.
- `secure`: the browser only sends it over HTTPS. This defaults to **on**, and
  the setting to turn it off is named so it's clearly for local development.
  (A `Secure` cookie over plain `http://localhost` is silently never sent
  back, which makes for a baffling first ten minutes if you don't know.)
- `samesite="lax"`: the cookie isn't sent on cross-site POSTs, which blunts the
  most common request-forgery attacks without breaking ordinary link
  navigation, and link navigation is exactly what a login redirect is.
- `domain`: the parent domain, so every subdomain shares one session. This is
  what makes single sign-on across apps work at all.

`samesite=lax` is a mitigation, not a complete answer to cross-site request
forgery, and there's no anti-CSRF token on the login route. I'll return to that
under "known gaps"; it's deliberate and written down, not an oversight.

### The login route, and the open-redirect problem

A forward-auth flow needs the login page to send you *back* to where you came
from. The proxy redirects an unauthenticated browser to something like:

```
https://sso.example.com/login?rd=https://app1.example.com/some/page
```

and after a successful login, the service redirects to the `rd` value. Which is
a textbook open redirect: if the service blindly honours `rd`, then
`/login?rd=https://evil.example/phish` is a *genuine login link on a genuine
domain* that drops the user on an attacker's site right after they've typed
their real password. That's a strong phishing primitive, handed over for free.

The defence is a single method on `Settings`, and I treat it as one of the
project's load-bearing walls:

```python
def is_trusted_redirect(self, target: str) -> bool:
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
```

Walk through what each branch rejects or accepts:

- **Empty, or starting with `//`.** A protocol-relative URL like
  `//evil.example/x` has no scheme, and some browsers treat it as an absolute
  URL on a different host. It would otherwise look like a "relative path" to a
  naive check, so it's rejected explicitly before anything else.
- **No scheme and no host.** A genuinely relative path. It's accepted only if
  it starts with `/`, so a bare word like `evil.example/x` (which a browser
  would treat as relative to the current page) is not accepted as a trusted
  destination.
- **A scheme that isn't `http` or `https`.** `javascript:`, `data:`, `file:`
  and friends are all rejected.
- **An absolute URL.** The host must be exactly the cookie domain or a true
  subdomain of it. Note the `"." +` in `endswith`: without it,
  `evilexample.com` would match a domain of `example.com`. The dot makes it
  `.example.com`, which `evilexample.com` doesn't end with.
- **An empty cookie domain** (`bool(domain) and ...`) means no absolute
  redirect is ever trusted, so a misconfigured deployment fails closed.

The login handler then picks its destination like this: use `rd` if it passes
the check, otherwise fall back to a safe default. The tests pin down the two
outcomes that matter end to end, a login with a trusted `rd` lands there, and
a login with `https://evil.example.net/phish` quietly falls back to the default
page. I'd like to say every branch above has its own unit test; in truth the
function is covered through those two routes, and each branch exists because it
corresponds to a way open-redirect checks are commonly bypassed in other
people's software, not because a test of mine caught it.

One more thing about the cookie domain: it does *double duty*. It's the
`Domain=` attribute on the session cookie **and** the allow-list for redirects.
That's convenient, since the set of hosts that should receive your cookie is
precisely the set of hosts that are safe to send you back to, but it does mean
those two concepts can't be configured separately. For this project that's a
feature.

### `/verify`: the endpoint everything depends on

```python
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
```

It returns no body either way. The proxy calls this on *every request to every
protected app*, so it has to be cheap, and the only information the caller wants
is the status code and the headers. It's one of the few routes that catches its
own `AuthenticationError` rather than leaning on the central error table,
because the response it wants for failure (an empty `401`) is not the JSON error
body the table would produce.

There's a companion route, `GET /me`, which does the same check and returns the
identity as JSON. That's for the case where a *frontend* wants to know who's
signed in. Browsers can't read the headers the proxy injects, so `/me` lets a
single-page app ask the same question directly.

### Profile, password and two-factor

The remaining routes are almost embarrassingly thin, which was the point:

```python
@app.patch("/profile")
def update_profile(payload: dict, user: User = Depends(current_user)):
    if "name" in payload:
        auth.set_name(user.id, payload["name"])
    if "email" in payload:
        auth.set_email(
            user.id, payload["email"], current_password=payload.get("current_password")
        )
    return Response(status_code=204)

@app.post("/password")
def change_password(payload: dict, user: User = Depends(current_user)):
    auth.set_password(user.id, payload.get("new_password", ""))
    return Response(status_code=204)

@app.post("/2fa/setup")
def setup_2fa(user: User = Depends(current_user)):
    enrol = auth.start_totp_enrollment(user.id)
    return {"secret": enrol.secret, "otpauth_uri": enrol.uri}

@app.post("/2fa/confirm")
def confirm_2fa(payload: dict, user: User = Depends(current_user)):
    codes = auth.confirm_totp(user.id, payload.get("otp", ""))
    return {"recovery_codes": codes}
```

Every one is: authenticate with `Depends(current_user)`, call exactly one
`AuthService` method, return. The validation (what makes a password acceptable,
what makes an email valid) isn't here at all. A bad value raises a
`ValidationError` inside the library, and the central table turns it into a
`422`.

Two comments in the real code explain behaviours that aren't obvious from the
shape:

1. `PATCH /profile` returns `204` with no body. The service has no
   "look up a user by id" method to return the refreshed record from, only
   `find_user(email)` and `list_users()`. Rather than guess at the new state,
   the route returns nothing and the client re-fetches `/me`.
2. `POST /password` does **not** re-check the caller's current password.
   Changing an email requires `current_password`, and so does disabling
   two-factor, but the library's `set_password` takes no such argument. The
   session cookie is the only proof of identity the route asks for. That is a
   real gap in a service people might one day put somewhere a stolen cookie is a
   realistic threat. The fix isn't to bolt a check on here (there's no private
   hook to reach into); it's to add the argument to the library first. Until
   then it's documented, loudly, in two places.

I'll flag one more property of that password route, because it surprised me
when I first read the library's behaviour: changing a password revokes *every*
session on the account, including the one making the request. So a successful
password change immediately signs the user out everywhere. That's the safe
behaviour, but any frontend has to expect to be thrown back to the login page
afterwards.

## Configuration, kept boring

`Settings` is a frozen dataclass populated from environment variables with a
common prefix:

```python
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
    ...
```

There's no settings framework. The module's own docstring says why: "small
enough that a settings framework would be more ceremony than it saves." That
stayed true for the whole series, though the number of fields grew from five to
about ten.

A small thing worth copying: `issuer` here is **only** the label that shows up
in a user's authenticator app next to their code. It sounds like it should be
the service's public URL. It is not. Later, when the service grows an
OpenID Connect side that genuinely needs an issuer URL, that gets a completely
separate setting, and the project's notes carry a warning not to conflate them.
Two settings with confusingly similar names are an accident waiting to happen,
and "documented in the file that defines them" turned out to be a reasonable
mitigation.

`.env.example` documents each variable with the *why* rather than just the
default, which paid off repeatedly when I came back to the project after a gap.

## `main.py`: the one place that touches the world

```python
settings = Settings.from_env()
store = SqliteAuthStore.open(settings.db_path)
auth = AuthService(store, session_days=settings.session_days, issuer=settings.issuer)

app = create_app(auth, settings)
```

Four lines of real work. This is the entrypoint
(`uvicorn identity_system.main:app`), and it's the only module allowed to read
the environment or open a database file.

There's a threading subtlety hiding behind `SqliteAuthStore.open`. FastAPI runs
synchronous route handlers on a thread pool, so one SQLite connection gets used
from several threads. SQLite's Python binding refuses that by default
(`check_same_thread`), so the store opens its connection with that check turned
off and serialises access internally with its own lock. The practical rule from
the library's guidance is: open **one** store, once, at import time, and share
it. Opening a store per request would work but would be both slower and
pointless.

This is also where the honest limitation lives. A single SQLite file with an
internal lock is a perfectly good answer for one instance of one small service.
It is not an answer to "scale out to several instances", and the notes say so:
if that day ever comes, the answer is a different `AuthStore` implementation
over Postgres (a structural change to storage, with no change to anything above
it), not clever tricks with the SQLite file.

## The tests

Because `create_app` takes its collaborators, the test setup is tiny:

```python
@pytest.fixture
def store():
    conn = connect(":memory:", check_same_thread=False)
    try:
        yield SqliteAuthStore(conn)
    finally:
        conn.close()

@pytest.fixture
def client(auth, settings) -> TestClient:
    app = create_app(auth, settings)
    # The session cookie is scoped to settings.cookie_domain (.example.com);
    # TestClient's default host ("testserver") doesn't match that domain, so
    # its cookie jar would silently drop the cookie between requests unless
    # the client's own host is a subdomain of it.
    return TestClient(app, base_url="http://sso.example.com")
```

That comment is the single most expensive lesson in the test suite. My first
instinct was to use `TestClient` with its default `testserver` host. The login
test would succeed, the *next* request would come back as `401`, and the
cookie would be right there in the response headers. The reason is that the
cookie is scoped to `.example.com`, and a cookie jar quite correctly refuses to
send a `.example.com` cookie to a host called `testserver`. The fix is just to
give the test client a host under the domain. No code is wrong; the test's
world was.

The tests themselves read like a specification of the contract:

```python
def test_verify_without_cookie_is_401(client): ...
def test_verify_with_garbage_cookie_is_401(client): ...

def test_verify_with_valid_session_returns_identity_headers(client, auth):
    user = auth.create_user("alex@example.com", "password123", name="Alex")
    client.post("/login", data={"email": "alex@example.com", "password": "password123"})

    resp = client.get("/verify")
    assert resp.status_code == 200
    assert resp.headers["x-auth-user-id"] == user.id
    assert resp.headers["x-auth-user-email"] == "alex@example.com"
    assert resp.headers["x-auth-user-name"] == "Alex"
```

There are separate files for login/logout, profile, two-factor and the
forward-auth contract. The garbage-cookie test is cheap and earns its keep: it
pins down that a forged or stale token produces a clean `401`, never an
exception.

## Wiring a proxy up to it

The README ships examples for both proxies I might use. The nginx one is the
reference:

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

Reading it top to bottom:

- `/_verify` is `internal`, meaning clients can't request it directly; it only
  exists to be the target of `auth_request`.
- `auth_request /_verify` runs before the real proxying. `auth_request_set`
  captures a header from the verify response into a variable, and
  `proxy_set_header` writes it onto the forwarded request.
- A `401` from the verify call is caught by `error_page` and turned into a
  `302` to the login page, with the original URL in `rd`.

And here's the part that makes the whole scheme trustworthy:
`proxy_set_header` **overwrites** the header unconditionally. If a client sends
`X-Auth-User-Email: someone-else@example.com` itself, nginx replaces it with
whatever the verify call actually said (or an empty value). A forwarded-header
identity scheme is exactly as secure as that overwrite, and the app behind it
must also be unreachable except through the proxy, or the whole thing is
decorative.

The Traefik example uses `forwardauth.authResponseHeaders` to name the headers
to copy across, with a note that Traefik's redirect-on-401 behaviour is less
tidy than nginx's `error_page` trick and needs wiring per its own docs. My own
apps sit behind nginx, so that example is documentation rather than something
I've exercised, and the README is careful to say so.

## What I deliberately didn't build

The README has a "Known gaps" section, and I'd defend every item as a choice:

- **No self-registration.** There is no public signup form. Accounts are
  provisioned by the library's CLI against the same database file
  (`sessionkit --db ./auth.db add you@example.com`; note `--db` is a top-level
  argument and goes *before* the subcommand). For a personal service whose
  users are me and a few people I trust, an open signup endpoint is attack
  surface with no upside. A later post adds an admin-only way to do it from the
  web, which is a different thing.
- **No CSRF token on login.** `SameSite=Lax` covers the common cross-site POST
  case but isn't a complete answer. Same stance the library takes on login
  rate-limiting: an explicit, documented gap.
- **Authentication, not authorisation.** `/verify` answers "who is this?", not
  "may they do X in this app?". The library has no roles or scopes, so each
  downstream app owns its own authorisation decisions based on the identity it
  receives. This will matter again when I need an admin page of the service's
  own.
- **One SQLite file.** As discussed, fine for one instance.

Writing these down in the first commit was one of the better habits of this
project. Half of the later work is a gap being closed on purpose, not a bug
being found.

## Where this leaves us

At the end of the first commit there's a working central login service: about
180 lines of app, a settings module, an inline-HTML login page, four test files,
and a README that explains how to put a proxy in front of it. Any app behind
nginx can now stop thinking about passwords.

It also has an obvious problem, which is that it isn't deployed anywhere. The
next post is about getting it onto a real container, and the surprisingly large
number of small things that went wrong between "it passes its tests" and "it's
actually running".

---

<!-- commits: 3874212 -->

Next: [Shipping it: Jenkins, uv and a Debian container →](02-shipping-it-jenkins-uv-and-a-container.md)
