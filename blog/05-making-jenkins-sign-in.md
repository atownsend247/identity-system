# Central login service: Making Jenkins sign in - Part 5

*~20 min read*

[Part 4](04-becoming-an-openid-connect-provider.md) ended with an OpenID Connect
provider that passed its test suite, covered the whole Authorization Code
flow, required PKCE, hashed its client secrets, and was, by the specification
as I'd read it, correct.

Then I pointed Jenkins at it, and the next half hour produced three
commits with titles like `fix jenkins login`, `fixing the fix further` and
`Adding preffered username`. This part is about those three commits. They're short
(the three of them touch well under a hundred lines of production code between
them), but they are the most educational part of the whole project, because each one is a
place where "correct according to the spec" and "works with the software on the
other end" came apart.

I think the pattern matters more than the individual bugs, so I'll go through
each one as a small case study: the symptom, what I wrongly assumed, the actual
cause, and the fix.

## Setting the scene

Jenkins' OpenID Connect plugin lets you replace its security realm (the thing that
answers "who is this person?") with an external provider. You give it a client ID
and secret, and a discovery URL, and in principle it configures itself: it fetches
`/.well-known/openid-configuration`, learns the endpoints, and runs the
Authorization Code flow.

The setup, in the README I wrote, is three bullet points:

- Client ID / Client Secret: the values registered with the identity service.
- Configuration mode: automatic, using the discovery URL.
- Scopes: `openid email profile`.

Authorisation *inside* Jenkins (who may build what, who is an administrator) stays
with Jenkins' own role-based strategy, in line with the rule from Part 1 that this
service answers "who is this", never "what may they do".

The discovery fetch worked. The redirect to my login page worked. I signed in. And
then Jenkins showed an error.

## Problem 1: "Could not extract credentials from request"

The message Jenkins displayed was a generic credentials-extraction failure. It said
nothing about OAuth, nothing about PKCE, nothing about which request had failed or
why. That's a classic shape for this kind of integration bug: the *relying party's*
error message describes what it was trying to do, not what the server did wrong.

When you're the identity provider and the client is a black box, the debugging
method is to reason from the protocol. What do I know about what Jenkins sent me?

My `/authorize` endpoint, as written in Part 4, had this check:

```python
code_challenge = params.get("code_challenge", "")
if params.get("code_challenge_method", "") != "S256" or not code_challenge:
    return error_redirect("invalid_request", "PKCE (code_challenge_method=S256) is required")
```

and a matching one at `/token`, where `code_verifier` was a required form field:

```python
code_verifier: str = Form(...),
```

Jenkins' OIDC plugin, in the configuration I was using, doesn't send PKCE
parameters unless they are explicitly enabled. So the authorise request had no
`code_challenge`, my endpoint dutifully redirected back with an `invalid_request`
error, and the plugin, which was expecting a `code` and got an `error`, reported the
only thing it understood: it couldn't extract what it needed from the response.

There was a second, subtler failure behind it. Even if the authorise step had
succeeded, `code_verifier: str = Form(...)` marks the field as required at the
*framework* level. FastAPI validates the request body before my handler ever runs, so
a token request without `code_verifier` would be rejected with a framework-generated
`422 Unprocessable Entity` carrying FastAPI's own validation-error JSON, which is not
an OAuth error at all. That's why the module docstring I wrote afterwards says the
failure surfaced as a generic error "because the request never reached our own error
handling".

That second point is worth sitting with, because it's a general trap. **When your web
framework validates required fields declaratively, requests that violate them never
reach your code, so your carefully designed protocol-shaped errors are bypassed.** An
OAuth client expects `{"error": "invalid_request", ...}`; it gets a different shape,
and it can't make sense of it. For a standards-based endpoint, the framework's
validation layer is part of your public contract.

### Was I wrong to require PKCE?

This deserves an honest answer, because "I removed a security feature to make a
client work" is exactly the sort of sentence that should make you suspicious.

PKCE exists to protect **public clients**: applications that can't keep a secret,
like a mobile app or a browser-only single-page app. If an attacker intercepts the
authorisation code from the redirect, a public client has nothing else standing
between that code and a token, so PKCE adds the missing proof.

Every client registered with this service is a **confidential client**. It has a
client secret that it must present at the token endpoint, and an intercepted code is
useless without that secret. So for these clients, PKCE is defence in depth rather
than the only defence. Requiring it unconditionally was strictness that blocked a
legitimate relying party without protecting anything the secret wasn't already
protecting.

What I did instead was make it **verified when present, optional when absent**:

```python
# PKCE is verified when a client uses it, but not required: it
# exists to protect *public* clients that can't hold a secret, and
# every client registered here is confidential ...
code_challenge = params.get("code_challenge") or None
code_challenge_method = params.get("code_challenge_method") or None
if code_challenge is not None and code_challenge_method != "S256":
    return error_redirect("invalid_request", "only code_challenge_method=S256 is supported")
```

and at the token endpoint:

```python
code_verifier: str | None = Form(None),
...
# Only verified if /authorize actually received a code_challenge
if entry.code_challenge is not None:
    if not code_verifier or not _verify_pkce(entry.code_challenge, code_verifier):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_grant", "error_description": "PKCE verification failed"},
        )
```

The stored challenge is now `str | None` on the code record. The logic is:

- If the client sent a challenge at authorise time, then at token time it **must**
  produce a matching verifier. Skipping the verifier when a challenge exists would
  be a downgrade attack, so `not code_verifier` is treated as a failure, not as
  "nothing to check".
- If the client sent no challenge, there's nothing to verify, and the client's secret
  is the proof.
- If the client sent a challenge using any method other than `S256` (such as
  `plain`, which sends the verifier unhashed and so protects almost nothing), it's
  rejected rather than silently accepted. Only the strong method is supported.

The conditions are tight enough that I'm comfortable with the trade, and it's one I
have documented as a deliberate posture, with the reasoning, in the module docstring,
the README and the project notes. But I'll also say plainly that it is a loosening.
If this service ever registered a public client, a client with no way to hold a
secret, then this setting would be wrong for it, and PKCE would need to be required
*per client* rather than not at all. That's a future problem that the data model
(a client record with scopes and redirect URIs) would accommodate easily.

### The tests that changed

The tests tell the story. The old test `test_authorize_rejects_missing_pkce` was
replaced with `test_authorize_rejects_code_challenge_with_unsupported_method`, which
asserts the new, narrower rule. A new test, `test_full_round_trip_without_pkce`, runs
the whole flow with no challenge and no verifier, and its comment is the reason the
test exists: "an RP not sending it (e.g. Jenkins' OIDC plugin without PKCE enabled)
must still be able to complete the flow."

The helpers also got an option. `_authorize` grew a `use_pkce=True` parameter, and
`_token_request` made `verifier` optional, so every existing test kept exercising the
PKCE path by default while the new one opted out. That's the cheap way to widen a test
suite: add a switch with a default that preserves old behaviour, rather than
rewriting every test.

## Problem 2: a registry file that crashed the whole service

While in there, the same commit made a defensive change that had nothing to do with
OIDC protocol rules and everything to do with operating the thing.

At this point, clients were registered by hand in a JSON file. The loader did this:

```python
entries = json.loads(text)
return {
    entry["client_id"]: OidcClient(
        client_id=entry["client_id"],
        ...
    )
    for entry in entries
}
```

Registering a single client, the natural way to write the file is a single JSON
object:

```json
{
  "client_id": "jenkins",
  "client_secret_hash": "...",
  ...
}
```

That parses as perfectly valid JSON. It's also wrong, because the loader expects a
*list* of clients. Iterating over a dict yields its **keys**, which are strings, so
`entry["client_id"]` becomes `"client_id"["client_id"]`, which raises `TypeError:
string indices must be integers`. The message points at an indexing expression, not
at the file, and it happens during module import, because `main.py` loads the registry
at startup.

The consequence is what makes it worth a fix: the loader was called unconditionally at
startup, so a typo in an *OIDC client file* took down the **entire service**, login
page, `/verify` and every app behind the proxy included. A mistake in an optional
feature's configuration had a blast radius covering the core feature.

The fix is a type check with an error message written for the person who'll read it at
2 a.m.:

```python
entries = json.loads(text)
if not isinstance(entries, list):
    raise ValueError(
        f"{path} must be a JSON array of client objects (even for a single "
        f"client) - see oidc_clients.json.example. Got a top-level "
        f"{type(entries).__name__} instead."
    )
```

It still fails at startup, which I think is right: a malformed registry is a
configuration error you want to find immediately rather than discover when a login
mysteriously doesn't work. But now the failure says what's wrong and where to look.
The principle I took from it is that **validation errors in hand-edited files should
name the file, the rule and the fix.** (This particular loader was later demoted from
"how clients are configured" to "one-time legacy import", as Part 6 describes, which
quietly removes the whole category of mistake. But the habit applies to every
hand-edited file.)

## Problem 3: the secret that never matched

The registry fix went in and the PKCE change went in and, in the logs, sign-in got
further. Jenkins now got a code, and called my token endpoint. And my token endpoint
said `401 invalid_client`.

This is the maddening kind of authentication bug: the secret configured in
Jenkins was supposed to be the one hashed into the registry, and hashing and
verification worked fine in the tests. And yet verification failed against a real
client.

The difference between the tests and Jenkins was the *transport*. My tests sent
`client_id` and `client_secret` as ordinary form fields (the `client_secret_post`
method). Jenkins used HTTP Basic authentication (`client_secret_basic`), which is
the default in most OIDC clients and which my discovery document had advertised. In
the first version, my code handled it like this:

```python
auth_header = request.headers.get("authorization", "")
if auth_header.lower().startswith("basic "):
    try:
        decoded = base64.b64decode(auth_header[len("basic "):]).decode()
        client_id, client_secret = decoded.split(":", 1)
    except Exception:
        return JSONResponse(status_code=401, content={"error": "invalid_client"})
```

That's the textbook HTTP Basic decode: base64-decode, split on the first colon. It's
what you'd write for any other API. And for OAuth2 it is subtly wrong.

### RFC 6749, Appendix B

The OAuth2 specification says that when a client uses Basic authentication, the
client ID and secret are first encoded with the `application/x-www-form-urlencoded`
algorithm (the same encoding as form fields, where a space becomes `+` and special
characters become `%XX`), *and then* joined with a colon and base64-encoded.

Which means the receiving side must do two decodes: base64, then URL-decoding of each
half. Skipping the second step is harmless when a secret is made of plain letters and
digits, because they pass through URL-encoding unchanged. It breaks on any character
that the encoding changes: `+`, `/`, `=`, `%`, space, and others.

Now consider how a strong secret is typically produced: something like
`openssl rand -base64 32`, and base64 output **is made of letters,
digits, `+`, `/` and `=`**. So almost any strong, machine-generated secret contains
characters that the client dutifully percent-encoded before sending, and that my
server compared in encoded form: `abc%2Bdef` against the stored hash of `abc+def`. They
could never match. The weaker and more boring the secret, the better it worked, which is
an unpleasant property for a security feature.

The fix is two lines, and a comment that cites the section:

```python
raw_id, raw_secret = decoded.split(":", 1)
# RFC 6749 Appendix B: client_id/client_secret are
# application/x-www-form-urlencoded *before* Basic-encoding
# - skipping this decode step meant any secret containing
# +, /, =, % (i.e. almost anything generated with
# `openssl rand -base64 ...`) never matched, because we'd
# be comparing the still-percent-encoded string.
client_id, client_secret = unquote_plus(raw_id), unquote_plus(raw_secret)
```

`unquote_plus` is the right function here, as opposed to `unquote`: it also turns `+`
back into a space, which is what the form-urlencoded algorithm specifies. (A secret
that originally contained a literal `+` arrives as `%2B` and decodes to `+`; one that
contained a space arrives as `+` and decodes to a space.)

### The test that would have caught it

The regression test is a good example of testing at the right level. It deliberately
builds its own app with a client whose secret is `tricky+secret/with=chars`, encodes it
the way a spec-compliant client would, and sends it as a Basic header:

```python
secret = "tricky+secret/with=chars"
...
basic = base64.b64encode(f"jenkins:{quote_plus(secret)}".encode()).decode()
resp = http_client.post(
    "/api/oidc/token",
    data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
    headers={"Authorization": f"Basic {basic}"},
)
assert resp.status_code == 200
```

Look at how it differs from the original tests. The original secret in the shared
fixture was a plain string, and the original requests used form fields. Neither of
those exercised the encoding path. **My tests were passing because they were testing
the easy case.** The fix wasn't only code; it was recognising that the test data was
too friendly, and that realistic test data (here, a secret full of the awkward
characters real secrets contain) is part of the test's value.

There's a related lesson in the code I did *not* change. The `client_secret_post`
path, where the secret is a form field, doesn't need decoding, because the web
framework has already decoded the form. Two ways of presenting the same credential, two
different encoding rules. That's an argument for supporting only one if you control both
ends, but I don't control Jenkins's end.

## Problem 4: no username

The last commit of the series is the shortest, and it's about a claim, not a bug.
Signing in worked. Jenkins got its tokens, verified the signature, and read my claims.
What it didn't have was a *username*: a short, human-friendly name to display and key
its own internal user records on.

OpenID Connect has a standard claim for this, `preferred_username`, in the `profile`
scope. The library underneath me has no separate username field. A user is an email, a
name and an ID, so I had to produce one. The natural stand-in is the part of the email
before the `@`:

```python
if "profile" in scopes:
    claims["name"] = name
    # sessionkit's User has no separate username field - the email
    # local part is the natural stand-in. Per the OIDC spec this claim
    # is explicitly a display hint, not a stable/unique identifier
    # (that's `sub`), so it doesn't need to be unique across domains.
    claims["preferred_username"] = email.split("@", 1)[0]
```

Three small follow-through edits make it a complete feature rather than a patch:
`claims_supported` in the discovery document gains `"preferred_username"`, so a client
that reads the document knows it's available; the userinfo endpoint's allow-list of
claims to pass through gains it too; and the README's endpoint table is updated.

The caveat in the comment is the important engineering judgement. A local part is *not*
unique: `alex@example.com` and `alex@example.org` would both produce `alex`. That's
acceptable here for two reasons: the spec says `preferred_username` is a display hint and
**not** an identifier (that's the job of `sub`, which is the account's real ID), and every
account on this service is one I created. But a relying party that decided to key its own
user table on this claim would be building on sand, and I'd tell anyone integrating
against the service not to.

Both new assertions went into the existing end-to-end test as two extra lines, one on the
decoded ID token and one on the userinfo response, rather than a new test: the claim's
behaviour is a property of the flow, so it's tested in the flow.

## What I'd take from the afternoon

Four problems across three commits, and the common thread isn't that I'd misread the
specification. On PKCE, I'd read it correctly and applied it more strictly than the world
does. On Basic auth, I'd *skipped* a section. On the registry I'd written a loader for
the file I intended to write rather than the file I might write. On the claim, I'd built
the protocol and forgotten that clients are people's expectations in code.

A few things generalise:

**Build the first integration test against the real client early.** Everything in Part 4's
test suite was written by me, simulating a relying party as I understood relying parties.
The tests encoded my assumptions, so they couldn't contradict them. A real client is the
only thing that can. If I did it again I'd stand up a throwaway Jenkins (or any real OIDC
client) before writing the second half of the endpoints.

**Prefer being liberal in what you accept when the extra strictness buys nothing.** The
PKCE decision wasn't "security versus convenience"; it was recognising that the strict
version defended against an attacker who was already stopped by the client secret. Strict
checks that defend nothing are just compatibility bugs.

**Framework validation is part of your protocol surface.** A required form field that a
real client omits will produce the framework's error, not yours, and the client may not
understand it.

**Make test data hostile.** Secrets with `+/=`, emails with plus-addressing, names with
accents, URLs with query strings. Realistic awkwardness is where the bugs live.

**Write down *why*, in the file, at the moment you learn it.** Each of these fixes carries
a comment explaining the symptom ("surfacing there as a generic 'Could not extract
credentials from request'"), so the next person to be tempted to tighten PKCE again
(including me, in a year) hits the explanation first.

With Jenkins signing in through the same accounts as everything else, the identity service
did what I'd built it to do. What it still couldn't do was be *configured* without me
editing files over SSH: adding an app to the landing page, or registering a new OIDC client,
meant touching JSON on the server and restarting. That's the subject of the next part.

---

<!-- commits: 0828435 fd63a60 ad3177c -->

Previous: [← Becoming an OpenID Connect provider](04-becoming-an-openid-connect-provider.md) · Next: [Moving configuration into the database →](06-moving-configuration-into-the-database.md)
