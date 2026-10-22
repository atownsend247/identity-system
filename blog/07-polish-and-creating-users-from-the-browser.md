# Central login service: Polish, and creating users from the browser - Part 7

*~22 min read*

The last two commits to date are the least dramatic and, in a way, the most
revealing about what a project looks like once the hard problems are solved. One
(`feat: make UI look nicer`) is almost entirely CSS. The other
(`feat: adding new user`) fills the last hole in the admin story: until now, the only way
to create an account was to SSH into the container and run a command-line tool.

This final part covers both, and then steps back to take stock of the whole series:
what the finished service looks like, which of its gaps are deliberate, and what I'd do
differently.

## Making it look like someone cared

By this point the frontend worked, and it looked like what it was: a set of unstyled
forms with a few rules from the first afternoon. The earlier CSS was about a hundred
lines, with colours written as literals (`#ddd`, `#b00020`) at the point of use. The
polish commit replaces it with roughly 490 lines, and the thing worth explaining isn't
any one rule. It's the structure.

### Design tokens first

The file starts with a block of CSS custom properties:

```css
:root {
  color-scheme: light dark;
  --bg: #f6f7f9;
  --surface: #ffffff;
  --text: #1c1f24;
  --muted: #5f6672;
  --border: #dde1e7;
  --accent: #2456d6;
  --accent-text: #ffffff;
  --accent-hover: #1d48b5;
  --danger: #b3261e;
  --danger-bg: #fdecea;
  --warn-bg: #fff8e1;
  --warn-border: #d9a400;
  --focus: rgba(36, 86, 214, 0.35);
  --radius: 0.6rem;
  --shadow: 0 1px 2px rgba(16, 24, 40, 0.06), 0 1px 3px rgba(16, 24, 40, 0.08);
  font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  line-height: 1.5;
}
```

Every rule after that refers to a token (`var(--surface)`, `var(--border)`), never a
literal colour. The benefit is that *themes become a matter of redefining a dozen
variables*, and the rest of the stylesheet doesn't change. Names describe roles
(`--surface`, `--muted`, `--danger-bg`) rather than appearances (`--light-grey`), which
is what lets the same name mean a different colour in a different theme.

The font stack is `system-ui` first. That means the OS's own UI font, with no web-font
download, no layout shift while it loads, and a result that looks native on whichever device
the page is opened on. For an internal tool that's the right default, and it's also
why the page has no external dependencies at all: no CDN, nothing to be blocked by a
content-security policy or to go down.

### Dark mode, with an escape hatch

The dark theme redefines the same variable names:

```css
@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']) {
    --bg: #0f1216;
    --surface: #171b21;
    --text: #e6e9ef;
    --muted: #9aa3b2;
    --border: #2a303a;
    --accent: #6e93f7;
    --accent-text: #0f1216;
    ...
  }
}

:root[data-theme='dark'] {
  --bg: #0f1216;
  ...
}
```

There are two selectors, which looks redundant until you work out what each is for:

1. The `@media (prefers-color-scheme: dark)` block follows the **operating system's**
   setting, but *unless* the page has been explicitly pinned to light
   (`:not([data-theme='light'])`). Default behaviour is "do what the user's device says".
2. The `:root[data-theme='dark']` block forces dark **regardless** of the OS setting, for a
   future theme toggle (or a screenshot tool) to set an attribute.

So three states are supported with no JavaScript: follow the system (no attribute), force
light, force dark. Nothing in the app sets that attribute today, so the forced modes are
currently unused: they're there so a toggle (or a screenshot tool) would be a one-line change rather
than a stylesheet rewrite. That's mildly speculative, and the cost is a duplicated block of variables.

The dark palette isn't the light one inverted. A few things differ on purpose:

- The accent colour is **lighter** in dark mode (`#6e93f7` rather than `#2456d6`), because
  a saturated mid-blue that reads well on white can look muddy against near-black.
- `--accent-text` flips from white to the dark background colour, so text on a primary
  button stays legible against the lighter accent.
- The error colour inverts its roles: in light mode it's a dark red on a pale pink
  (`#b3261e` on `#fdecea`), and in dark mode a pale pink on a dark red (`#f2b8b5` on
  `#3a1716`). Same meaning, readable in both.
- The shadow is darker and tighter. A soft grey drop shadow disappears on a dark
  background.

The commit doesn't include a contrast audit of these pairs. For an internal tool that's tolerable; for
anything public I'd check them against the WCAG thresholds first.

### Buttons: one primary, the rest quiet

The base `.btn` is a neutral bordered button. A second class, `.btn-primary`, is the filled
accent colour, and the commit's small TSX changes are all of the same kind: adding
`btn-primary` to the one button on each form that is *the thing the user is here to do* (Sign
in, Save, Register client), and `btn-danger` to the two Delete buttons.

```css
/* Primary action on a form: the one thing the user is here to do. */
.btn-primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-text);
}
```

That comment is also a design rule. If every button is loud, none is. The Edit and Rotate buttons inside the admin tables stay neutral (with a compact variant,
`table .btn`, so they don't bloat the rows), while Delete gets `.btn-danger`: red text, and a red
border and pale-red fill on hover.

Interaction states get real attention, since they're what separates "styled" from "usable":

```css
.btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--focus);
}

.btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
```

`:focus-visible` shows the ring for keyboard navigation but not for mouse clicks, so the
focus indicator isn't ugly for pointer users and isn't missing for keyboard users. Removing
the default `outline` is only acceptable *because* it's replaced with something at least as
visible; deleting focus styles without a replacement is a classic accessibility regression.
The disabled style matters here more than usual because the forms disable their submit button
while a request is in flight, which doubles as protection against a double-click creating
two of something.

### Details that earn their keep

A few smaller rules show the intent:

```css
.secret-banner {
  background: var(--warn-bg);
  border: 1px solid var(--warn-border);
  border-radius: var(--radius);
  padding: 0.9rem 1.1rem;
  margin: 1.25rem 0;
  word-break: break-all;
}
```

That's the banner from Part 6 that displays a freshly generated client secret. It gets its own
**warning colours**, distinct from error red and from normal content, because it carries a
message the user must act on *now* ("copy this, it won't be shown again"). `word-break:
break-all` is there because a 43-character URL-safe secret has no natural break points and
would otherwise push the layout sideways on a phone.

```css
@media (max-width: 40rem) {
  .app-header { padding: 0.6rem 1rem; }
  .app-main { margin-top: 1.25rem; padding: 0 1rem; }
}
```

The responsive handling is deliberately small: the header's flex row is allowed to wrap
(`flex-wrap: wrap`), the content column has a `max-width` and fluid padding, and wide admin
tables scroll *inside themselves* rather than forcing the whole page to scroll horizontally.
The apps directory uses `grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr))`, which
produces as many equal columns as fit and reflows to a single column on a phone without a
single media query.

The header also got a small touch of identity: a gradient square in front of the wordmark,
drawn with a `::before` pseudo-element, so it's decoration with no extra image or markup.

The commit touches five files, adding 430 lines and removing 54. Four of them are one-class changes to
TSX (the `btn-primary` and `btn-danger` additions); the component logic didn't change, because the
styling lives where it should.

## The last gap: creating people

Back to the substantive change. The `README` of this project has said since the very first
commit that there's **no self-registration**. That's deliberate: a public signup form on a
personal login service is attack surface for no benefit. Accounts were created with the
library's command-line tool, run on the server against the same database file.

That worked, and it had one painful property: adding a family member's account meant a shell
session on the container. Now that apps, clients and users are all visible in the admin
pages, "everything except creating a user" being a command-line job looked odd.

### The route

```python
@app.post("/api/admin/users", status_code=201)
def admin_create_user(payload: dict, _admin: User = Depends(require_admin)):
    user = auth.create_user(
        payload.get("email", ""), payload.get("password", ""), name=payload.get("name") or None
    )
    return _admin_user_json(user)
```

Four lines, and it's a good demonstration of the discipline from Part 1. The route does **no
validation of its own**: no email-format regex, no password-length check, no duplicate lookup.
All of that is `AuthService.create_user`'s job. It raises `ValidationError` for a malformed
email or a short password and `DuplicateUser` for an address already in use, and both are
already in the central status table, so they surface as `422` and `409` with no new code.
The same rules apply whether an account is created from the CLI or from this page, because
it's literally the same function.

Two small things in that route are worth noticing:

- `name=payload.get("name") or None` turns an empty string into `None`, so a blank name field
  in the form means "use the default" and not "set the name to the empty string". The default,
  as the test shows, is the local part of the email.
- The response is `201 Created` with the new user's admin view (no password material, naturally).
  And the earlier inline list comprehension that built the admin user dictionary was pulled out
  into `_admin_user_json`, because there were now two places that return it.

The list route changed in a way I like: a helper used in two places is what you extract when the
*second* use appears, not when you predict it.

### The test suite for it

Five tests cover the behaviour, and the interesting ones are the ones that check the account is
actually usable:

```python
def test_admin_can_create_a_user(client, auth):
    _login(client, auth, email="admin@example.com", password="password123", name="Admin")

    resp = client.post(
        "/api/admin/users",
        json={"email": "new@example.com", "password": "password123", "name": "New Person"},
    )
    assert resp.status_code == 201
    ...
    # The new account can actually sign in with the password the admin set.
    other = client.__class__(client.app, base_url=client.base_url)
    login = other.post("/api/login", json={"email": "new@example.com", "password": "password123"})
    assert login.status_code == 200
```

The last three lines build a **second, separate test client**. The first one holds the admin's
session cookie, and reusing it would make a "login" that proves nothing about the new
account. A fresh client with an empty cookie jar has to authenticate from scratch. It's the sort
of detail that distinguishes a test that checks the response from a test that checks the outcome.

The other tests nail down the edges: the name defaulting from the email, `401` when signed out
and `403` for a non-admin, `409` for a duplicate email, `422` for a too-short password. Together
they assert that the route is gated the same way as every other admin route, and that the
library's validation rules propagate through unchanged.

### The form

On the frontend, the Users page gained an "Add a user" section under the table: email, an
optional name, password, confirm password.

```tsx
const handleSubmit = async (e: FormEvent) => {
  e.preventDefault()
  setFormError(null)
  if (form.password !== form.confirmPassword) {
    setFormError('Passwords do not match')
    return
  }
  setSubmitting(true)
  try {
    // There's no self-registration endpoint, deliberately (see CLAUDE.md) -
    // this is the admin-only equivalent of `sessionkit add`, not a signup
    // form. Validation (email format, password strength) lives in
    // AuthService.create_user, same as everywhere else.
    await api.post('/api/admin/users', {
      email: form.email,
      password: form.password,
      name: form.name || undefined,
    })
    setForm(EMPTY_FORM)
    await onUserCreated()
  } catch (err) {
    setFormError(errorMessage(err))
  } finally {
    setSubmitting(false)
  }
}
```

Some decisions packed in here:

**The confirm-password check happens only in the browser**, and the API doesn't receive the
confirmation at all. That's the right division of labour: the "did you mistype it?" check is
a usability feature that protects against typos, not a security control, so it belongs where the
typing happens. The server's rules are the real ones. The test confirms the shape, asserting that
a mismatch stops submission *without ever calling the API*.

**`try/catch/finally`.** `finally` resets `submitting` whatever happens, so a failed request never
leaves the button permanently disabled. Without it, one rejected duplicate email would require a
page reload to try again.

**The server's own error message is displayed.** `errorMessage(err)` unwraps the `ApiError`, so the
admin sees the library's wording for a weak password or a duplicate rather than a generic failure.
Another consequence of keeping the validation in one place: the UI never has to translate or
duplicate the rules.

**On success the form resets and the list reloads** via the `refresh` function from `useAdminList`.
The new user appears in the table with "never" as their last login, which is a quick confirmation
that the right thing happened.

The page also gained an empty state: a "No users yet." message instead of a table with a header and no
rows. The kind of detail that only matters on a first run, which is exactly when a project makes its
first impression.

### What the admin doesn't get

The README is careful about the limits of this feature, and the honest summary is worth repeating:

- **The admin chooses the initial password and hands it over.** There's no emailed invitation, no
  "reset on first login", and no generated secret (unlike OIDC clients, where the secret is only ever
  needed by a machine). For a household, with me and the person standing in the same room, that's
  acceptable. For anything larger it would be the next thing to build.
- **Nothing else about an existing account is editable here.** Renaming, re-emailing, deleting,
  resetting a password or switching off a lost authenticator's two-factor still require the
  command-line tool. The project notes list that explicitly so I don't assume the page does more
  than it does.
- **There's no self-service password change in the frontend yet**, though the backend route exists
  (it has since the first commit). That's a gap in the account page rather than in the API.

## Where the series ends up

The finished service, as of the latest commit, is:

- **A forward-auth endpoint** (`GET /verify`) that any app behind my nginx can use to learn who's
  asking, with the session cookie shared across a parent domain.
- **A login page** that is the only place a password is typed, with TOTP two-factor and a
  carefully guarded post-login redirect.
- **An OpenID Connect provider** for software that can't sit behind the proxy, with PKCE verified when
  offered, hashed client secrets and a persistent signing key.
- **A hub**: a public apps directory at `/`, an account page at `/account`, and, for administrators,
  pages to manage users, apps and OIDC clients, all editable live.
- **A deploy pipeline** that tests with `uv` and `npm`, builds the frontend, and ships to a Debian
  container with an `rsync` and a re-templated systemd unit and nginx config.

All of it landed in under two weeks of commits, from the first on 28 September to the latest on 7 October.

### What I'd do differently



**Test with a real relying party earlier.** Part 5's four problems were all invisible to tests I wrote
myself. A throwaway real client at the start of the OIDC work would have turned an afternoon of
surprises into a few minutes.

**Make the deploy smoke-test itself.** A `curl` to `/verify` expecting a `401` after each deploy would
have caught the stale-virtualenv bug immediately, and it's about three lines of shell.

**Install with `uv` on the container**, so that what the tests ran against is what runs in production,
rather than leaving the lockfile as a statement about CI only.

**Add an audit log.** The admin pages can now create users, register clients that receive
authorisation codes, and change where those codes are sent. Nothing records who did what or when.

### What I'd keep

**The rule that rules live in the library.** Every time I was tempted to add a check to a route
handler, the answer was "that's `AuthService`'s job", and it kept the service thin enough to hold in
my head.

**Writing the gaps down.** Every deliberate omission (no CSRF token, no authorisation model, the
`POST /password` gap, in-memory authorisation codes) is documented where the next change will trip over
it. About half of the later work in this series was a documented gap being closed on purpose.

**Fail-closed defaults.** An empty admin list means no admins. An empty cookie domain trusts no
redirects. A missing file means "nothing to import". When a setting is absent, the safe behaviour is
also the default behaviour.

**Treating shared URLs as contracts.** The frozen legacy paths from Part 3 meant I could rebuild the whole
frontend without breaking a single app that relied on the service.

**Small comments that say *why*.** Nearly every non-obvious decision in the codebase has a comment that
explains the reasoning or the failure it came from. They're why I could write seven posts about this
project from the repository alone.

---

That's the story to date. The service sits quietly at the front of the house, and mostly what it does is
answer "who is this?" for each request without anyone noticing. Which, for an identity service, is the
best compliment I can think of.

<!-- commits: e79f8d3 6a2e506 -->

Previous: [← Moving configuration into the database](06-moving-configuration-into-the-database.md)
