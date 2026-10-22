# Central login service: Becoming an OpenID Connect provider - Part 4

*~28 min read*

Everything so far assumed one thing: that the app I'm protecting can sit behind
my reverse proxy. The proxy asks the login service "is this request allowed?",
the answer arrives as a status code and a few headers, and the app never has to
know how any of it happened.

Then I wanted Jenkins to use the same accounts. Jenkins can't play that game.
It isn't a little app I wrote; it has its own idea of users, sessions and
security realms, and it ships with a plugin that wants to speak **OpenID
Connect** to an identity provider. The forward-auth trick doesn't help, because
there's nothing for the proxy to "vouch" to: Jenkins insists on running a login
flow of its own.

So the central login service had to learn a second protocol. This part is about
that: what OAuth2 and OIDC actually are, the roughly 350 lines of `oidc.py` that
implement the minimum I needed, the security decisions embedded in it, and the
deployment consequences of holding a signing key. The work landed in the commit
`feat: Enable oidc endpoints and functionality`, with its follow-up
deployment fixes.

## OAuth2 and OIDC in plain terms

Three parties are involved in the flow I implemented, called the **Authorization
Code** flow:

- The **user**, sitting at a browser.
- The **relying party** (RP), the app that wants to know who the user is.
  Here, Jenkins. In OAuth2 terminology this is the *client*.
- The **identity provider** (IdP), the thing that actually authenticates the
  user. Here, `identity-system`.

The shape of the exchange is:

```
1. Browser at Jenkins ──▶ "Sign in" ──▶ Jenkins redirects the browser to
   idp/authorize?client_id=...&redirect_uri=...&scope=openid email profile&state=...

2. IdP checks who the browser is (session cookie). Not signed in? Show the
   login page, then come back to step 2.

3. IdP redirects the browser back to the RP's redirect_uri with a short-lived,
   one-time `code` (and the `state` it was given).

4. RP's *server* (not the browser) calls idp/token with the code and its own
   client secret ──▶ gets back an ID token (a signed JWT saying who the user is)
   and an access token.

5. RP verifies the ID token's signature using the IdP's public keys, reads
   the user's email and name from it, and logs the user in locally.
```

The reason for the two-step dance (a code through the browser, then a token over
a direct server-to-server call) is that the *browser is the untrusted part*. The
code is useless without the client secret, which only the RP's server holds, so
someone who intercepts the redirect can't use it.

**OAuth2** is the framework for delegating access. **OpenID Connect** is a thin
layer on top that adds the part OAuth2 deliberately leaves out: a standard way to
say *who the user is*. In practice that adds: the `openid` scope, the **ID
token** (a JWT with identity claims in it), a **discovery document** so clients
can configure themselves, and a **userinfo** endpoint.

I implemented the smallest slice of this that real software accepts: five
endpoints.

| Endpoint | What it's for |
|---|---|
| `GET /.well-known/openid-configuration` | Discovery: tells a client where everything else is |
| `GET /api/oidc/jwks.json` | The public key(s) clients use to verify tokens |
| `GET /api/oidc/authorize` | Step 1 to 3 above |
| `POST /api/oidc/token` | Step 4: code for tokens |
| `GET /api/oidc/userinfo` | Fetch the user's claims with an access token |

All of it lives in one module, `oidc.py`, exposed as a FastAPI `APIRouter` that
`create_app` mounts. Notice the path of the first one: `/.well-known/...` is
fixed by the specification, so it's the one deliberate exception to the "anything
new goes under `/api/`" rule from Part 3.

## What "minimal" means here

The docstring at the top of `oidc.py` is the most important part of the file,
because it records what I decided *not* to build:

- **No consent screen.** Any registered client is auto-approved for a signed-in
  user. A public identity provider must ask "Allow Jenkins to see your email?"
  because it can't trust the clients. Mine can: every client is something *I*
  registered, so the consent prompt would be ceremony. The trust posture is "the
  people who can register a client are already trusted", the same posture as the
  admin allowlist.
- **No self-service client registration.** Clients are added by an administrator.
  (At this point that meant editing a file by hand; Part 6 gives it a UI.)
- **No refresh tokens.** When a relying party's session lapses, it re-runs the
  flow in the browser, which is invisible if the user still has a live session
  here.
- **Authorization codes in memory only.** I'll defend this one at length below.
- **One signing key, no rotation.** Rotate by replacing the file and restarting.
  A single long-lived key is the right amount of complexity for a handful of
  internal relying parties.

Each "no" is also a smaller attack surface and fewer moving parts to get wrong. If
any of them stops being true (a second household, an app I don't control), that's
the point to reach for an off-the-shelf identity provider rather than extending
this one.

## The signing key

ID tokens are **signed JWTs**. A relying party must be able to confirm that a token
really came from my service and wasn't forged, and it does so with a public key
published at the JWKS endpoint. That means the service needs an RSA keypair, and it
provisions one for itself on first run:

```python
def load_or_create_signing_key(path: str) -> rsa.RSAPrivateKey:
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
```

"Provision on first run" is the same posture the underlying library takes with its
own database: you shouldn't need a setup step to get a working service. A few
things in this function are worth a second look.

**The key is generated on the server and never leaves it.** There's no key
ceremony and nobody ever handles the private key.

**`chmod(0o600)`.** Owner read/write only. A private signing key readable by other
users on the machine is a credential leak waiting for a bad day.

**`write_bytes` then `chmod`, rather than creating the file restricted from the
start.** There's a window, short but real, in which the file exists with default
permissions. Opening with `os.open(..., 0o600)` would close it. On a container
where the only account is the service's own it doesn't matter, but it's the sort of
thing I'd fix in a shared environment, and I'd rather say so than pretend it's
airtight.

**The key has no passphrase** (`NoEncryption`). A passphrase would need to live
somewhere the service can read at startup, which just relocates the secret. File
permissions and the container boundary are doing that job.

The JWKS endpoint publishes only the public half:

```python
@router.get("/api/oidc/jwks.json")
def jwks():
    jwk = json.loads(RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return {"keys": [jwk]}
```

and there's a test whose entire job is to assert `"d" not in keys[0]`, because `d`
is the private exponent in a JWK and leaking it would be catastrophic. A test that
checks for an *absence* is cheap insurance for a mistake you only get to make once.

### A key ID that survives restarts

Each JWT carries a `kid` header so a client knows which key in the JWKS verified
it. That ID is derived from the key itself rather than generated randomly:

```python
def _kid_for(public_key: rsa.RSAPublicKey) -> str:
    numbers = public_key.public_numbers()
    raw = f"{numbers.n}:{numbers.e}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]
```

A hash of the public numbers is stable for as long as the key file is. If the `kid`
were random per start, every restart would give tokens a different identifier and a
client that cached the JWKS would see tokens it couldn't match. Deriving it from
the key makes the identifier a pure function of the thing it identifies.

## Authorization codes: one-time, short-lived, in memory

When a signed-in user is sent back to the relying party, they carry a `code`. The
code is just a lookup key into a server-side table:

```python
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
    def __init__(self, ttl_seconds: int = _AUTH_CODE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._codes: dict[str, _AuthCode] = {}

    def create(self, **fields) -> str:
        self._sweep()
        code = secrets.token_urlsafe(32)
        self._codes[code] = _AuthCode(expires_at=time.time() + self._ttl, **fields)
        return code

    def consume(self, code: str) -> _AuthCode | None:
        self._sweep()
        entry = self._codes.pop(code, None)
        if entry is None or entry.expires_at < time.time():
            return None
        return entry
```

Properties worth naming:

- **Unguessable.** `secrets.token_urlsafe(32)` is 256 bits from the operating
  system's cryptographic generator. No sequential IDs, no `random`.
- **Single use, by construction.** `consume` uses `dict.pop`, which returns the value
  *and removes it* in one step. There is no code path where a code can be read twice,
  which is exactly what the spec requires; a test confirms a second exchange of the
  same code returns `invalid_grant`.
- **Short-lived.** Sixty seconds. The legitimate use is "the browser follows a
  redirect and the RP's server makes a call", which takes milliseconds. A long
  lifetime only helps an attacker.
- **It snapshots the user's details.** Email and name are copied into the code entry
  at authorise time instead of being looked up again at the token endpoint. The
  library's `AuthService` has no "get user by id" method, only a lookup by email, and
  for a code that lives sixty seconds, a stale snapshot is a non-issue.

The `_sweep()` calls prune expired entries on every operation. There's no background
thread or scheduler; the table is small and the cleanup is amortised into requests
that are already happening.

### Why in memory, and why that's a deliberate trade

A restart loses any code that's mid-flight. The user's browser lands on the relying
party with a code that no longer exists, the RP shows a login error, the user clicks
sign in again. The window for this to matter is the sixty seconds around a restart.

The alternative is a database table, and the module docstring spells out why I
didn't: the whole service is already committed to a single-instance posture (a single
SQLite file), so the "multiple instances share codes" benefit of a database doesn't
apply, and persisting them adds a write on the login path plus a cleanup job. The
project notes carry a warning to future-me: *do not "fix" the restart behaviour by
reaching for a table without re-reading that reasoning first.* It's a simplicity and
availability trade tied to a larger architectural decision, not an oversight.

## `/authorize`: the interesting endpoint

This is where most of the security-relevant decisions live, and the ORDER of checks
matters as much as the checks.

```python
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
```

**First, validate who's asking and where the answer will go.** If the `client_id` is
unknown, or the `redirect_uri` isn't one the client registered, the response is a
plain `400` shown to the user. It must *not* be a redirect to the supplied URI with an
error attached, because at this point the URI is untrusted: redirecting to it would
turn the endpoint into an open redirect (the same class of bug as `?rd=` in Part 1).
Everything after this check can safely redirect errors back, because the destination
has been proven to be one the administrator approved.

The comparison is `redirect_uri not in client.redirect_uris`: an **exact string
match** against a set. That's stricter than the `?rd=` check, which allows any
subdomain of the cookie domain, and the strictness is intentional, and written into
the project notes as a rule: *don't reuse `is_trusted_redirect` here.* A domain-suffix
match would let anyone who can stand up a page anywhere under the domain receive
another relying party's authorization code. For `?rd=` the worst case is "a user
lands on another of my pages"; for an authorization code the worst case is "someone
else logs in as the user". Same shape of check, very different stakes, so a different
rule.

Then come the protocol checks, which *do* redirect with an OAuth-style error:

```python
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
```

Two details. The `<=` is set inclusion: every requested scope must be one the client
is permitted. A client registered for only `openid` can't ask for `email` and get it.
And errors carry `state` back, because the spec requires the RP to be able to match a
response to the request it started.

Only then does it look at who the *user* is:

```python
token = request.cookies.get(settings.cookie_name)
try:
    user = auth.user_for_token(token)
except AuthenticationError:
    rd = f"{request.url.path}?{request.url.query}"
    return RedirectResponse(url=f"/login?{urlencode({'rd': rd})}", status_code=302)
```

If there's no valid session, the user is sent to the login page with `rd` set to *this
exact authorize request*, path and query string. After login, the frontend (following
the server's `redirect_to`, remember) comes straight back to `/api/oidc/authorize`
with the same parameters, now with a cookie. The existing login machinery handles it
with no OIDC-specific code at all. A test asserts that the redirect starts with
`/login?rd=%2Fapi%2Foidc%2Fauthorize`, which is the path URL-encoded into the `rd`
parameter.

That reuse is the payoff of building `rd` as a generic "come back here" mechanism in
Part 1. And it's still safe: the `rd` here is a *relative path*, which
`is_trusted_redirect` accepts.

With a session, the code is minted and the browser is sent back:

```python
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
```

### PKCE

The first version *required* **PKCE** (Proof Key for Code Exchange, pronounced
"pixie"). PKCE closes a hole in the code flow for apps that can't keep a secret, like
mobile apps and single-page apps. The client invents a random string (the
`code_verifier`), sends only its SHA-256 hash (the `code_challenge`) at authorise
time, and sends the original string at token time. The server checks that the
verifier hashes to the challenge it saw earlier. An attacker who steals the code in
transit doesn't have the verifier, so can't redeem it.

```python
def _verify_pkce(code_challenge: str, code_verifier: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return secrets.compare_digest(expected, code_challenge)
```

The base64 here is the URL-safe variant with the `=` padding stripped, per the spec
(RFC 7636). Getting that detail wrong produces a mismatch that looks exactly like a
wrong verifier. And `secrets.compare_digest` does a constant-time comparison, so the
check doesn't leak how many leading characters were right through response timing.
For a value that's about to be compared to a secret, that's the right habit even when
the practical risk is small.

I made PKCE mandatory because "secure by default" felt right, and the next part is the
story of how that default met Jenkins.

## `/token`: exchanging a code for tokens

The token endpoint runs on a direct server-to-server call, and it authenticates the
*client*:

```python
client = clients.get(client_id or "")
if client is None or not client_secret or not verify_client_secret(client, client_secret):
    return JSONResponse(status_code=401, content={"error": "invalid_client"})
```

Client secrets are stored **hashed**, using the same Argon2 hasher the library uses for
user passwords. I reused it deliberately instead of adding a second hashing scheme:

```python
def hash_client_secret(secret: str) -> str:
    return Argon2Hasher().hash(secret)

def verify_client_secret(client: OidcClient, secret: str) -> bool:
    return Argon2Hasher().verify(client.client_secret_hash, secret)
```

The effect is that a leaked copy of the client registry exposes no more plaintext
secrets than a leaked user database would expose plaintext passwords. The registry file
is also treated like `.env`: gitignored (with a committed `.example` template), and kept
out of the repository entirely.

With the client authenticated, the code is consumed and cross-checked:

```python
entry = code_store.consume(code)
if entry is None or entry.client_id != client_id or entry.redirect_uri != redirect_uri:
    return JSONResponse(status_code=400, content={"error": "invalid_grant"})

if not _verify_pkce(entry.code_challenge, code_verifier):
    return JSONResponse(status_code=400, content={...})
```

The `client_id` and `redirect_uri` must match what the code was issued for. Without
that, a client could redeem a code that was issued to a *different* client. Note the
code is consumed (removed) *before* the PKCE check, so a failed attempt burns the code,
and an attacker gets exactly one guess.

Then come the tokens:

```python
claims = {
    "sub": entry.user_id,
    "aud": client_id,
    **_claims_for_scope(entry.scope, email=entry.email, name=entry.name),
}
id_token_claims = dict(claims)
if entry.nonce:
    id_token_claims["nonce"] = entry.nonce
id_token_claims["auth_time"] = int(time.time())
```

`sub` is the **subject**: a stable, unique identifier for the user. I use the account's
ID, never the email, because emails can be changed (Part 3 added exactly that feature)
and a relying party that keyed accounts on email would see a renamed user as a brand
new one. `aud` is the audience, the client the token was issued *for*. The `nonce`, if
the client sent one at authorise time, is echoed back so the client can bind the token
to its own session and detect replay.

Claims are gated by scope:

```python
def _claims_for_scope(scope: str, *, email: str, name: str) -> dict:
    claims: dict = {}
    scopes = set(scope.split())
    if "email" in scopes:
        claims["email"] = email
        claims["email_verified"] = True
    if "profile" in scopes:
        claims["name"] = name
    return claims
```

A client that only asked for `openid` gets just `sub`. One that asked for `email` gets
the address. (`email_verified` is hard-coded to `True`. That's only defensible because
accounts are created by an administrator rather than self-registered, so there's
no unverified address to flag; it would be wrong the day signup opened up.)

Both the ID token and the access token are RS256 JWTs signed by the same key, five
minutes of life each, carrying the same scope-gated claims:

```python
def _issue_jwt(*, issuer, signing_key, kid, ttl_seconds, claims) -> str:
    now = int(time.time())
    payload = {"iss": issuer, "iat": now, "exp": now + ttl_seconds, **claims}
    return jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": kid})
```

Making the access token a self-contained JWT means `/userinfo` never needs a token
database: it verifies a signature and returns the claims inside.

```python
claims = jwt.decode(
    token, signing_key.public_key(), algorithms=["RS256"],
    options={"verify_aud": False},
)
```

Notice `algorithms=["RS256"]`, an explicit allow-list. JWT libraries historically had
a class of vulnerability where a token claiming `alg: none` or switching to a symmetric
algorithm was accepted because the verifier trusted the token's own header. Pinning the
algorithm on the verifying side closes it. `verify_aud` is off here because userinfo
doesn't know which client is calling and the signature already proves the token is
ours.

## Discovery

The discovery document is what lets an administrator paste one URL into Jenkins and be
done:

```python
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
    "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
    "code_challenge_methods_supported": ["S256"],
    "claims_supported": ["sub", "email", "email_verified", "name"],
}
```

The `issuer` comes from a *new* setting, and here a mistake I'd planned for from the
start earns its keep. Part 1 introduced `issuer` as the label shown in an authenticator
app. This is a different concept that needs a different name. The OIDC issuer is the
service's real, externally reachable origin, with no trailing slash, and every URL in
the document and the `iss` claim in every token is built from it. If the two settings
were one, changing the label would break every token. So there's
`IDENTITY_SYSTEM_OIDC_ISSUER_URL`, with a comment in `.env.example` and a note in the
project docs saying "not the same thing".

An issuer mismatch is the classic reason an OIDC integration fails with an unhelpful
message: the client fetches the discovery document, sees an `issuer` that doesn't
match the URL it fetched it from, and (correctly) refuses to continue.

## The test suite

`test_oidc.py` is about 220 lines and reads as a checklist of the properties above.
The helper at the heart of it plays the part of a relying party:

```python
def _pkce_pair():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge
```

and the main test drives the entire flow end to end: sign in, authorise, take the code
out of the redirect `Location`, exchange it, decode the ID token, call userinfo with the
access token, and compare. Around it sit the negative cases:

- unknown client and non-matching `redirect_uri` both get a `400`, not a redirect;
- missing `openid` scope, or a scope the client isn't allowed, get an `invalid_scope`
  redirect;
- a code can't be reused;
- an expired code is rejected, tested by monkeypatching `time.time` forward an hour
  rather than sleeping for sixty seconds;
- a wrong PKCE verifier and a wrong client secret are rejected, with `invalid_grant` and
  `invalid_client` respectively;
- userinfo rejects a missing or garbage bearer token.

The test fixture for the registered client returns the **plaintext** secret alongside
registering its hash. That's necessary (a test driving the token endpoint needs the real
secret) and illustrates the property from the other side: only the hash is stored, so the
plaintext has to be handed out at creation.

## Operating a signing key

Holding a private key changes the deployment story, and it's the part of this commit I'm
most glad I thought about before shipping.

The key lives in a file, `data/oidc_signing_key.pem` by default. And Part 2 told you what
`rsync --delete` does to files outside `data/`. If the key file ended up anywhere else,
the next deploy would delete it, and on the next start the service would *helpfully
generate a brand new one*. Nothing would error. But every relying party caches the public
key from the old JWKS, so every token the new key signs would fail verification until each
RP re-fetched the key set. SSO would silently break for everything at once.

So the same defences that protect the database were extended to the key:

- `.env.example` documents the path with a stern note.
- `remote-setup.sh` got a second check next to the database one, warning loudly if
  `IDENTITY_SYSTEM_OIDC_SIGNING_KEY_PATH` isn't inside `data/`.
- There's no `*.pem`-style catch-all exclude in `deploy.sh`, and I deliberately didn't
  add one, as it would hide the problem rather than surface it. `data/` is the only thing
  protecting the key, and the project notes say so.

The client registry, a JSON file holding hashed secrets, was initially handled the other
way: gitignored, excluded from rsync by name. That held up for about 90 minutes. By the
time the follow-up commits arrived (`feat: sync dpeloy updated`, typo preserved), it had moved into
`data/` too, with a comment explaining why a name-based exclude was the wrong tool:
`apps.json` sits right next to it and *must* sync, so excluding by file name was
fragile, whereas "everything stateful lives in `data/`" is one rule that covers all of
it.

The deploy-time surprise from Part 2, where `pip install` skipped reinstalling because
the version string hadn't changed, also surfaced here. The OIDC routes returned `404`
after a green deploy because the virtual environment was still running the pre-OIDC
code. That was the bug the `--force-reinstall` flag fixed.

## What this part changed

Before this commit the service had one way in: a cookie and a header. After it, it has
two, sharing the same accounts, sessions and login page. A request to `/api/oidc/authorize`
without a cookie is bounced through the same `/login` form as everything else, and the same
`rd` mechanism brings it back.

The decisions I'd call out as load-bearing:

1. **Validate the redirect URI before ever redirecting to it**, and match it exactly.
2. **Codes are unguessable, single-use and short-lived**, enforced by data structure, not
   by discipline.
3. **Client secrets are hashed**, the same way passwords are.
4. **The signing key is stateful data**, and gets the same protection as the database.
5. **Pin the JWT algorithm** on the verifying side.

What I hadn't planned for was how much of the work was still ahead once the code compiled
and the tests passed, because real software doesn't always implement the specification the
way you read it. The next part is about the afternoon I spent teaching a deliberately strict
identity provider to get along with Jenkins.

---

<!-- commits: dcc1b7a 6ad88f2 163c1fd -->

Previous: [← An admin page and a proper frontend](03-an-admin-page-and-a-proper-frontend.md) · Next: [Making Jenkins sign in →](05-making-jenkins-sign-in.md)
