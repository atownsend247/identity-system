# Central login service: Moving configuration into the database - Part 6

*~25 min read*

By the end of [Part 5](05-making-jenkins-sign-in.md), the service was doing real
work: apps behind the proxy used it for forward-auth, and Jenkins signed in through
it with OpenID Connect. What it wasn't was *pleasant to operate*, and the reason
was the same in both of its configurable parts.

There were two pieces of configuration that changed over time:

- **The apps directory**: the cards on the landing page, held in `apps.json`.
- **The OIDC client registry**: the relying parties allowed to sign users in, held
  in `oidc_clients.json`.

Both were files read once at startup. To add an app to the landing page I edited a
JSON file, committed it, waited for Jenkins to build and deploy, and the service
restarted to pick it up. One of the commits in the history is literally
`feat: add extra applications`, a two-entry change to `apps.json` that cost a full
pipeline run. To register a new OIDC client I had to compute an Argon2 hash by hand
in a Python one-liner, paste it into a file on the server, and restart.

This part is about replacing both files with a small SQLite-backed registry managed
from admin pages in the UI. The commit is `feat: enable application/oidc control from
within admin center`, 28 files and about 1,600 lines. Most of those lines are
unremarkable CRUD, so I'll skip the typing and concentrate on the design decisions that
weren't obvious: where the data lives, how the migration from files works without
duplicating anything, how a secret can be shown once, and how a live-editable security
setting interacts with the code that reads it.

## Why a database, and why *this* database

The question "files or database?" is really "who edits this, and how often?". A
`.env` file holds settings an operator picks once per deployment. A list of apps and a
list of clients are *content*: they change as the household's software changes, and the
person changing them is me in a browser, not me over SSH.

There was a second, less obvious argument for the database, which comes from Part 2.
Everything stateful on the container has to live in `data/` or the next deploy's
`rsync --delete` removes it. Part 4 had already stretched that rule to cover the OIDC
signing key, and the client registry file had briefly needed its own special-case
handling in `deploy.sh`. If configuration lives in the **same SQLite file as the
accounts**, it inherits the existing protection for free, and the number of things that
can be accidentally deleted by a deploy goes *down*.

That's the choice the new module makes, and its docstring states it directly:

> Lives in the same db file as sessionkit's own tables, as sibling tables on its own
> connection... That puts it under the same `data/` protection the rest of this
> service's state already relies on... so an admin's edits survive every deploy.

Three tables, all small:

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS oidc_clients (
    client_id          TEXT PRIMARY KEY,
    client_secret_hash TEXT NOT NULL,
    redirect_uris      TEXT NOT NULL,
    allowed_scopes     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identity_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
```

A few choices in there:

- **`IF NOT EXISTS` everywhere.** The registry runs the schema script on every
  construction. It's the whole "migration system": there's one version of the schema, and
  creating it is idempotent. When a column needs to change, that's the moment to introduce
  real migrations, and not before.
- **`apps` has an integer surrogate `id`**, while `oidc_clients` uses `client_id` itself as
  the primary key. An app has no natural unique key (two apps could share a name, and a
  URL might change), so the admin UI needs a stable handle to say "edit this one". A client
  already *has* a unique, human-chosen, immutable identifier, so inventing another would
  be redundant.
- **Lists stored as JSON text.** `redirect_uris` and `allowed_scopes` are JSON arrays in
  a `TEXT` column, written sorted. The registry only ever reads and writes whole client
  records and never queries "which clients allow this URI", so a join table would be
  structure for its own sake. Sorting makes the stored value deterministic.
- **`identity_meta`**, a generic key-value table, exists for one purpose that I'll get to
  in the migration section.

### Its own connection, its own lock

The registry doesn't reuse sessionkit's store object. It gets a *second connection to the
same file*:

```python
registry = ConfigRegistry(connect(settings.db_path, check_same_thread=False))
```

and serialises access with its own lock, in the same style as the library's store:

```python
def list_apps(self) -> list[dict]:
    with self._lock:
        rows = self._conn.execute(
            "SELECT id, name, url, description FROM apps ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]
```

Since FastAPI runs synchronous routes on a thread pool, and a SQLite connection isn't safe
for concurrent use, every method takes the lock before touching the connection. It's the
simplest correct answer, and it's consistent with the single-writer posture the project
documents everywhere: this is one instance of one small service.

Two connections to the same SQLite file means SQLite's own file locking arbitrates between
them, which is fine at this scale; writers briefly block each other. It also keeps the
registry fully decoupled from the library: no reaching into private attributes of the store
to borrow its connection, and no risk that a library upgrade changes something I was quietly
depending on.

## The API surface

The registry's methods map one-to-one to routes, all guarded by the same dependency. In
Part 3 the admin users route did its own `is_admin` check inline. With five more admin
routes arriving, that check was extracted:

```python
def require_admin(user: User = Depends(current_user)) -> User:
    if not settings.is_admin(user.email):
        raise HTTPException(status_code=403, detail="forbidden")
    return user
```

and every admin route declares it as a dependency:

```python
@app.get("/api/admin/apps")
def admin_list_apps(_admin: User = Depends(require_admin)):
    return registry.list_apps()
```

The leading underscore on `_admin` signals that the value is unused, and the dependency is
there purely for its side effect of rejecting the request. That's a small thing, but a
dependency is the *right* mechanism: it's declared in the function signature where you can't
miss it, it composes with `current_user` (so unauthenticated requests get `401` and
authenticated non-admins get `403`, from one place), and it's hard to forget when copying a
route.

"Hard to forget" is important, because forgetting is the failure mode here. A missing guard
on one admin route silently exposes it to everyone. The test file guards against that with a
table of every admin route:

```python
ADMIN_ROUTES = [
    ("get", "/api/admin/apps", None),
    ("post", "/api/admin/apps", {...}),
    ("put", "/api/admin/apps/1", {...}),
    ("delete", "/api/admin/apps/1", None),
    ("get", "/api/admin/oidc-clients", None),
    ("post", "/api/admin/oidc-clients", {...}),
    ("put", "/api/admin/oidc-clients/x", {...}),
    ("post", "/api/admin/oidc-clients/x/rotate-secret", None),
    ("delete", "/api/admin/oidc-clients/x", None),
]

def test_every_admin_config_route_requires_login(client):
    for method, path, body in ADMIN_ROUTES:
        assert _call(client, method, path, body).status_code == 401, (method, path)

def test_every_admin_config_route_rejects_non_admins(client, auth):
    _non_admin(client, auth)
    for method, path, body in ADMIN_ROUTES:
        assert _call(client, method, path, body).status_code == 403, (method, path)
```

One loop, two tests, all nine routes. The `(method, path)` tuple as the assertion message
means a failure names the exact route that's open. It's the sort of test that costs ten
minutes to write and protects against the one mistake most likely to be made in the next
dozen commits. It does have a weakness I'll acknowledge: it only covers routes *in the
list*. A tenth route added without being appended to `ADMIN_ROUTES` wouldn't be tested. Tests
catch forgotten guards only if they're also not forgotten.

## Validation at the edge

Previously a malformed apps file or client file was the operator's problem. Now arbitrary
strings arrive over HTTP, and some of them end up somewhere security-sensitive. The request
bodies are declared as Pydantic models with explicit constraints:

```python
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
```

The comment about `HttpUrl` is worth dwelling on, because it's an interaction between two
pieces of code that don't know about each other. Pydantic's built-in `HttpUrl` type is the
obvious choice for "a URL". But it *normalises* what it parses: `https://example.com` becomes
`https://example.com/`. Part 4's `/authorize` compares the supplied `redirect_uri` against
the stored set with an exact string match, and an OIDC client sends whatever it was
configured with. If the registry silently added a trailing slash on save, the stored value
would no longer equal what the relying party sends, and every login would fail with "unknown
client_id or redirect_uri" for a reason that's invisible in the admin UI. So the validator is
a plain regular expression on a plain string: it checks the shape and leaves the value alone.

The regex `^https?://\S+$` does three jobs. It requires an `http` or `https` scheme, which
rejects `javascript:alert(1)` (an apps-directory link is rendered as an `<a href>`, so this is
a stored-XSS vector, not a nicety), a bare hostname like `finance.example.com`, and anything
with whitespace. The test enumerates exactly those:

```python
for bad in ["javascript:alert(1)", "finance.example.com", "https://", "https://a b.example.com"]:
    resp = client.post("/api/admin/apps", json={"name": "X", "url": bad})
    assert resp.status_code == 422, bad
```

Other notes on this block:

- `allowed_scopes` is a `Literal` of the three known scopes, so a typo or an invented scope
  like `admin` is a `422` rather than a stored value that does nothing.
- `client_id` is restricted to a conservative character set: letters, digits, `.`, `_`, `-`.
  It ends up in URL paths (`/api/admin/oidc-clients/{client_id}`) and in the `aud` claim of
  tokens, so keeping it boring prevents encoding surprises in both.
- There's a separate `OidcClientUpdate` model without `client_id`. A client's identifier is
  immutable once created, so the update shape simply can't carry one, and the UI disables the
  field while editing.
- Creating a client with an ID that already exists returns `409 Conflict`, via the registry's
  `add_oidc_client` returning `False` on a SQLite `IntegrityError` rather than raising.

## Showing a secret exactly once

Creating a client means handing a secret to the administrator, who pastes it into the relying
party. Previously the administrator invented the secret, hashed it by hand, and so already
knew it. Now the **server generates it**:

```python
def generate_client_secret() -> str:
    """A fresh random client secret, generated server-side so the admin
    never has to pick (or hand-hash) one."""
    return secrets.token_urlsafe(32)
```

That's 256 bits from the OS's cryptographic source, and the URL-safe alphabet (letters,
digits, `-` and `_`) deliberately sidesteps the `+/=` characters that caused Part 5's Basic-auth
trouble. (The fix in Part 5 means such characters are handled correctly anyway, but a
generated secret that avoids them removes a whole class of copy-paste and encoding problems for
whatever relying party is on the other end.)

The create route hashes it, stores the hash, and returns the plaintext in the response, once:

```python
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
```

Every other representation of a client goes through one serialiser that never includes a secret
or a hash:

```python
def _oidc_client_json(client: OidcClient) -> dict:
    # Never the hash - and the plaintext secret only ever appears in the
    # create/rotate responses, once.
    return {
        "client_id": client.client_id,
        "redirect_uris": sorted(client.redirect_uris),
        "allowed_scopes": sorted(client.allowed_scopes),
    }
```

The test asserts both halves of the contract: the create response contains a secret of at least
32 characters, and *no listing ever contains either `client_secret` or `client_secret_hash`*. It
also reaches into the registry to check the stored hash differs from the plaintext and that
`verify_client_secret` accepts the real secret. The end-to-end test then uses the freshly created
client to complete a full authorisation-code flow, which proves the generated, hashed secret
actually works against the token endpoint, the test that catches an "I hashed it differently
from how I verify it" bug.

### The consequence: lost means rotated

If a secret is only ever shown once and only its hash is stored, a lost secret can't be recovered.
Nobody, including me with database access, can read it back. The remedy is **rotation**:

```python
@app.post("/api/admin/oidc-clients/{client_id}/rotate-secret")
def admin_rotate_oidc_client_secret(client_id: str, _admin: User = Depends(require_admin)):
    secret = generate_client_secret()
    if not registry.set_oidc_client_secret_hash(client_id, hash_client_secret(secret)):
        raise HTTPException(status_code=404, detail="client not found")
    return {"client_id": client_id, "client_secret": secret}
```

New secret, new hash, old hash overwritten, so **the old secret stops working immediately**.
The UI's confirm dialog says exactly that ("Rotate the secret for jenkins? The old one stops
working immediately"), because rotating while the relying party is still configured with the old
secret breaks its logins until someone updates it. The test checks both directions: the new secret
verifies and the old one doesn't.

On the frontend, the plaintext is held in a piece of component state and rendered in a banner with
"Copy it now. It won't be shown again - only a hash is kept." and a Dismiss button. It's never
written to storage and never logged. Starting an edit clears it; reloading the page loses it. That
is the intended behaviour: the page is a courier, not a vault.

## Live reads: making a security setting editable

Before this change, the OIDC router received a plain dictionary of clients, built once at startup.
That can't work any more: a client registered in the UI has to be usable immediately, and a client
deleted in the UI has to stop being usable immediately. So the router's parameter changed from data
to a **function**:

```python
def create_oidc_router(
    *,
    auth: AuthService,
    settings: Settings,
    lookup_client: Callable[[str], OidcClient | None],
    signing_key: rsa.RSAPrivateKey,
    ...
```

and every use site changed from `clients.get(client_id)` to `lookup_client(client_id)`, wired up in
`create_app` as `registry.get_oidc_client`. The OIDC module doesn't know a database exists; it asks
a callable. That keeps `oidc.py` free of any storage dependency, and it's why the existing OIDC
tests needed almost no changes.

Reading from the database on every authorise and token call costs a tiny indexed lookup on a table
with a handful of rows, which is irrelevant next to the Argon2 hash that follows it on the token
path. The benefit is that revocation is genuinely immediate, which two tests check:

```python
def test_update_changes_redirect_uris_and_scopes_live(client, auth, oidc_clients):
    ...
    # The old redirect_uri is no longer registered, so /authorize refuses it
    # outright - no restart needed.
    assert old.status_code == 400

def test_delete_client_stops_it_authorizing(client, auth, oidc_clients):
    assert client.delete("/api/admin/oidc-clients/jenkins").status_code == 204
    ...
    assert resp.status_code == 400
```

I think "can I revoke this *right now*?" is one of the questions to ask of any authentication-adjacent
configuration. A static file loaded at boot answers "after the next deploy". A live lookup answers
"yes".

## The migration: files in, exactly once

The service already had real data in the two JSON files on a running machine. Replacing them with
tables without migrating would mean re-entering everything by hand, and a migration that runs
*twice* would duplicate every row. The design needs a one-shot import with a durable "I've done it"
flag, and that's what `identity_meta` is for.

```python
def legacy_import_done(self) -> bool:
    with self._lock:
        row = self._conn.execute(
            "SELECT 1 FROM identity_meta WHERE key = ?", (_LEGACY_IMPORT_KEY,)
        ).fetchone()
    return row is not None

def import_legacy(self, apps: list[dict], clients: list[OidcClient]) -> None:
    """Seeds the tables from the old static files and sets the import
    flag, all in one transaction - a crash partway through leaves the
    flag unset, so the next startup retries the whole import cleanly."""
    with self._lock, self._conn:
        for entry in apps:
            self._conn.execute(
                "INSERT INTO apps (name, url, description) VALUES (?, ?, ?)",
                (entry["name"], entry["url"], entry.get("description", "")),
            )
        for client in clients:
            self._conn.execute("INSERT INTO oidc_clients ...", _client_to_row(client))
        self._conn.execute(
            "INSERT INTO identity_meta (key, value) VALUES (?, 'true')",
            (_LEGACY_IMPORT_KEY,),
        )
```

The load-bearing detail is `with self._lock, self._conn:`. Using a SQLite connection as a context
manager wraps the block in a transaction: commit on success, rollback on an exception. The rows and
the flag are written in the *same transaction*, so there are only two possible end states: all of it
happened, or none of it did. A crash halfway through the import leaves no flag and no rows, and the
next startup tries again from clean. Without the transaction, a crash after inserting the apps but
before setting the flag would cause the next boot to re-insert them, giving duplicates.

Hashes carry across untouched. The OIDC clients' `client_secret_hash` values from the old file are
inserted as they are, which means every registered relying party keeps working with its existing
secret through the migration. Nothing has to be rotated and nobody has to re-enter anything.

### A subtle trap in the startup code

The startup logic in `main.py` has a guard that took some thought:

```python
# Only mark the import done if there was something to import - otherwise a
# box that starts without either file would have its flag set, and a file
# copied in afterwards would silently never be imported.
legacy_files_present = any(
    Path(p).is_file() for p in (settings.apps_path, settings.oidc_clients_path)
)
if legacy_files_present and not registry.legacy_import_done():
    registry.import_legacy(
        load_apps(settings.apps_path),
        list(load_oidc_clients(settings.oidc_clients_path).values()),
    )
```

Consider the simplest version: "if the flag isn't set, import and set it". On a brand new box with
neither file, that sets the flag after importing *nothing*. If someone later drops an `apps.json` in
place expecting it to be picked up, the flag says the import already happened and the file is
silently ignored. Gating on "at least one file exists" means the flag only gets set once there was
actually something to migrate. The loaders themselves treat a missing file as "nothing to import".

There's a flip side that this guard doesn't cover. If only *one* of the two files exists, the
import runs for both with the missing one contributing nothing, and the flag is set; a later-arriving
second file is then ignored. It's a narrow case, and closing it would take two independent flags.
For a one-time migration of a single household's config that's a reasonable thing to leave alone, but
it is a gap.

The project notes record the operational consequence, in terms I'd want to read after a long gap:
**editing `apps.json` or `oidc_clients.json` on a live box now does nothing, and the way to "fix"
that is never to re-run the import, because a re-import would duplicate rows.** The admin pages are
the source of truth. And the committed `apps.json` stays in the repository as the seed for a fresh
install, with the loaders retained solely for that purpose.

The deploy script's header got a matching rewrite. Where it used to explain how the JSON files are
protected from `--delete`, it now says neither needs protecting: after the import the database is the
source of truth, and a missing file just means there's nothing to import. Deleting a whole paragraph
of special-case reasoning from a script is a very satisfying outcome of a design change.

## Admin navigation: showing only what you can use

There are now three admin pages: Users, Apps directory, OIDC clients. The nav bar should only show
those links to admins. But the frontend doesn't know who's an admin: it knows the user's name and
email from `/me`. The answer is to put the verdict in the response:

```python
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
```

and the nav conditionally renders:

```tsx
{user.is_admin && (
  <>
    <NavLink to="/admin">Users</NavLink>{' '}
    <NavLink to="/admin/apps">Apps directory</NavLink>{' '}
    <NavLink to="/admin/oidc-clients">OIDC clients</NavLink>{' '}
  </>
)}
```

The comment on `is_admin` states the security model plainly: this flag is for **presentation**, not
**enforcement**. Hiding a link isn't access control; anyone can type the URL. That's why every admin
route is independently guarded, why the pages still handle `401` and `403` themselves, and why the test
suite asserts every route rejects non-admins. The nav change is a courtesy so that non-admins don't see
links that would just say "Forbidden".

## Three admin pages, one hook

The three pages (users, apps, clients) all do the same dance: load a list, map `401` to a redirect and
`403` to a message, show a table and a form, refresh after a mutation. Instead of copying it a third
time, the logic became a hook, `useAdminList`:

```ts
export function useAdminList<T>(path: string) {
  const [items, setItems] = useState<T[]>([])
  const [status, setStatus] = useState<AdminStatus>('loading')

  const refresh = useCallback(async () => {
    try {
      setItems(await api.get<T[]>(path))
      setStatus('ok')
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) setStatus('unauthenticated')
      else if (err instanceof ApiError && err.status === 403) setStatus('forbidden')
      else setStatus('error')
    }
  }, [path])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { items, status, refresh }
}
```

Returning `refresh` is what lets a page re-query after a successful create, update or delete, so the
table reflects the server's truth rather than a locally patched copy. That choice costs one extra request
per mutation and removes a whole category of "UI drifted from reality" bugs. The API wrapper grew `put`
and `delete` methods, nginx got two more exact-match `location =` lines for `/admin/apps` and
`/admin/oidc-clients` (forget those and a refresh on either page returns `404`), and the frontend tests
grew by about 250 lines.

## What I'd flag

A few honest notes on what this does and doesn't do:

- **The admin pages are now a powerful surface.** Anyone on the admin allowlist can register an OIDC
  client with any redirect URI they like, and the system will happily issue authorisation codes to it.
  There's no consent screen, and I've been explicit about that trust model. It's also exactly why the
  allowlist is a static `.env` value that only I can change, not something editable in the same UI.
- **There's no audit trail.** Nothing records who changed what. For a household of one administrator, I
  accepted that; for anything bigger it would be the first thing to add.
- **Deleting a client doesn't revoke tokens already issued**, because tokens are self-contained signed
  JWTs with a five-minute lifetime. The window is short, but it exists.
- **Losing the database now loses more.** Accounts, apps and client credentials all live in `auth.db`.
  That concentrates risk in one file, which is the same file that already needed backing up.

What I got in exchange is the ability to add an app or a relying party from the browser with no deploy
and no restart: the same job that used to cost a commit, a pipeline run and a restart.

The service now does most of what I wanted. Two things were still missing: a way to create a *person*
without a command line, and a user interface that looked like it belonged to a finished product. The
final part covers both.

---

<!-- commits: 6adbfbd 5ead5e5 -->

Previous: [← Making Jenkins sign in](05-making-jenkins-sign-in.md) · Next: [Polish, and creating users from the browser →](07-polish-and-creating-users-from-the-browser.md)
