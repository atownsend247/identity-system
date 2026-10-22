# Central login service: An admin page and a proper frontend - Part 3

*~25 min read*

By the end of [Part 2](02-shipping-it-jenkins-uv-and-a-container.md) the login
service was deployed and doing its job, and its entire user interface was one
HTML form built from a Python string. That was fine for a service whose only
visible surface was "type your password". It stopped being fine within a day,
because I immediately wanted three things the form couldn't give me:

1. A way to see **who has an account** and when they last signed in.
2. A landing page listing **the apps** that sit behind the login, so the service
   is somewhere you *go*, not just somewhere you get bounced through.
3. An **account page** where a signed-in person can change their own name and
   email.

This part covers how I got those, including the part where I built the first one
the quick way, then threw the approach away roughly half an hour later. The
commits involved are `feat: add admin interface`, `feat: adding better frontend`
and `feat: add ability to update user data`.

## Step one: the quick admin page

The first attempt followed the grain of what already existed. The login page
was an inline HTML template, so the admin page would be one too:

```python
@app.get("/admin", response_class=HTMLResponse)
def admin_users(request: Request):
    try:
        user = current_user(request)
    except AuthenticationError:
        return RedirectResponse(url="/login?rd=/admin", status_code=303)
    if not settings.is_admin(user.email):
        return HTMLResponse("Forbidden", status_code=403)
    return render_admin_users(auth.list_users())
```

There are two ideas in it worth keeping, because they survive into the final
design.

**A static admin allowlist.** The library has no roles, so "who is an admin?"
had to be answered somewhere. I picked the dumbest mechanism that could work: an
environment variable.

```python
admin_emails=frozenset(
    email.strip().lower()
    for email in _env("ADMIN_EMAILS", "").split(",")
    if email.strip()
),

def is_admin(self, email: str) -> bool:
    return email.strip().lower() in self.admin_emails
```

Comma-separated, case-insensitive, empty by default, meaning *nobody can reach
the admin pages until the variable is set*. That default fails closed. There's no
in-app management of the list: to change who's an admin you edit `.env` and
restart. The project's notes are blunt that this is a deliberate choice rather
than a stopgap: "no in-app management, edit `.env` and restart to change who's
on it". Stored roles would be the proper answer, but they'd be a new rule, and
new rules belong in the library.

It does mean this service now has a tiny authorisation concept of its own,
which sits awkwardly with Part 1's "authentication, not authorisation". The note
in the code is honest about it: the admin page is *this service's own page*, not
a downstream app's, so it makes its own call.

**Redirect on 401, 403 on not-admin.** A human browsing to `/admin` while signed
out should be sent to sign in and then brought back, so a missing session
becomes a `303` to `/login?rd=/admin`. A signed-in non-admin should get a
refusal. The distinction between "I don't know who you are" and "I know who you
are, and no" matters for usability and for not leaking information, and it's the
distinction the later design keeps even though it moves who does the redirecting.

The page itself was an HTML table in a template with every value passed through
`html.escape`. The columns were email, name, created, last login and whether
two-factor is on. The tests followed the pattern from Part 1: not logged in gets
redirected, logged in but not on the list gets `403`, and an admin sees all the
users, including a "never" in the last-login column for an account that has
never signed in.

It worked. And it made the next problem obvious.

## Why the inline HTML had to go

An inline HTML template is a fine way to build exactly one form. By the time I
had two (login and the user list), the cracks were showing:

- All the styling was a `<style>` block in a Python string, duplicated per
  template.
- Every interactive behaviour would need hand-written JavaScript in a string
  too, with no tests and no tooling.
- The things I actually wanted next (an apps directory, an account editor, and
  eventually forms for creating users) are *applications*, with state, validation
  and error handling, not documents.

The other apps in my fleet already use Vite + React + TypeScript, with Vitest for
tests and `oxlint` for linting, so the decision wasn't much of a decision. The
consequential question was the *shape of the split*: what does the Python side
become when it no longer renders pages?

The answer, written at the top of `app.py` after the change: **a pure JSON API,
with no server-rendered HTML at all.** Every page, `/`, `/login`, `/admin` and
`/account`, is a client-side route that nginx serves from the built frontend.

That's a clean line to draw, and it's worth drawing. It means the backend's
tests never assert on HTML, the frontend's tests never need a real backend, and
the contract between them is a handful of JSON endpoints that I can reason about
independently.

## Reshaping the API

Moving to a JSON API changed three routes, and one of those changes had a
constraint I need to explain.

### Login becomes `POST /api/login`

The old login handler accepted a form post and replied with a `303` redirect. A
single-page app wants the opposite: send JSON, get JSON back, decide what to do
next. The new route:

```python
@app.post("/api/login")
def api_login(payload: dict):
    result = auth.login(
        payload.get("email", ""),
        payload.get("password", ""),
        otp=payload.get("otp") or None,
    )
    target = (
        payload.get("rd", "")
        if settings.is_trusted_redirect(payload.get("rd", ""))
        else "/account"
    )
    response = JSONResponse(
        {"user": _user_json(result.user), "redirect_to": target}
    )
    _set_session_cookie(response, result.token)
    return response
```

Notice what disappeared: the `try/except AuthError` that used to re-render the
form. With no HTML to re-render, a failed login can just raise and let the central
error table produce a JSON `401`, which is exactly the "every route is a plain
JSON route, no exceptions" simplification I was after. The `OtpInvalid` entry from
Part 1 now matters for this route too, since a wrong two-factor code at login is
one of the ways it can fail.

The part I care most about is `redirect_to`. The open-redirect defence from Part 1
(`is_trusted_redirect`) is still applied *on the server*, and the server hands
the verdict back as a field. The frontend's rule is then simple and absolute:

> **Navigate to `redirect_to`. Never navigate to the raw `?rd=` from the URL.**

If the browser code ever grabbed `rd` straight from the query string and used it,
the server-side validation would be decoration. So the component does this:

```tsx
const result = await api.post<LoginResponse>('/api/login', { email, password, otp, rd })
// redirect_to is computed server-side (Settings.is_trusted_redirect) -
// navigate to that, never to the raw ?rd= itself, or the
// open-redirect guard is for nothing (see CLAUDE.md).
window.location.assign(result.redirect_to)
```

And the test for it is written to fail if someone breaks the rule. It renders the
login form with a *malicious* `rd`, mocks the server replying with a safe
`redirect_to` of `/account`, and asserts the browser was sent to `/account`:

```tsx
renderLogin('https://evil.example.net/phish')
...
await waitFor(() => expect(assign).toHaveBeenCalledWith('/account'))
```

Testing a security property by *attacking your own component* is a good habit.
A test that only checked the happy path would pass just as happily if the
component ignored `redirect_to` entirely.

### The admin list becomes `GET /api/admin/users`

Same logic. The route returns JSON, gets its authentication from
`Depends(current_user)`, and checks the allowlist itself:

```python
@app.get("/api/admin/users")
def admin_users(user: User = Depends(current_user)):
    if not settings.is_admin(user.email):
        raise HTTPException(status_code=403, detail="forbidden")
    return [ { "id": u.id, "email": u.email, ... } for u in auth.list_users() ]
```

A comment explains the asymmetry that tripped me up briefly: `is_admin` isn't a
library exception, it's this service's own bespoke check, so there's no
`AuthError` subclass to put in the status table. It's a plain `HTTPException`.

What the route no longer does is redirect. A JSON endpoint has no business
returning a `303` to an HTML page; it returns `401` or `403` and the frontend
decides what to show. That decision moved to the page component, which I'll get
to shortly.

### A new public endpoint: `GET /api/apps`

The landing page lists the apps behind the login. Where does that list come
from? For this iteration the answer was the simplest possible thing: a JSON file
in the repository, loaded once at startup.

```json
[
  {
    "name": "Finance",
    "url": "https://finance.example.com",
    "description": "Accounts, transactions, and budgeting."
  },
  ...
]
```

```python
def load_apps(path: str) -> list[dict]:
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return []
    return json.loads(text)
```

A missing file returns an empty list rather than raising, so a fresh checkout
with no apps configured simply renders an empty directory. The route that serves
it is deliberately unauthenticated:

```python
@app.get("/api/apps")
def list_apps():
    # Public, deliberately unauthenticated - the apps directory (this
    # service's own default landing page) is meant to be visible
    # whether or not the visitor is signed in.
    return apps
```

That is a choice with consequences. Anyone who can reach the service can read the
list of app names and URLs. For a personal setup those are names I'd happily
publish (each app is behind the login anyway), but it's the sort of decision to
make consciously, not by default. It is also why the descriptions are written as
a one-line "what it does" rather than anything operational.

Editing the list means editing the file and restarting. That was fine for a first
cut. Part 6 is about why it didn't stay that way.

### The constraint: some paths can't move

While I was reshaping routes, I hit a rule I had to write into the project notes.
Four things, `POST /logout`, `GET /verify`, `GET /me`, and the profile, password
and two-factor routes, are **frozen**. Their bare paths and their shapes can't
change.

The reason is that other things already depend on them. Other apps' nginx configs
call `/verify` by that exact path. My finance app's already-deployed frontend
hardcodes the identity service's origin and calls `/me` and the account routes. If
I'd tidied everything into an `/api/` namespace for consistency, I'd have broken
every app behind the login in one deploy.

So the rule became: *anything new goes under `/api/`; anything that existed
before this point stays where it is.* The result is a slightly untidy namespace,
with bare legacy paths alongside `/api/*`, but it's untidy in a way that's
documented, and the alternative was a coordinated multi-repository change for no
functional gain. The Vite dev-server config even carries a comment about it:

```ts
// Dev-server-only proxy to the backend on :8000 (production nginx does the
// equivalent split - see deploy/nginx-identity-system.conf). Longer than a
// typical single '/api' entry because most of this backend's routes are
// still bare paths (/verify, /logout, /me, ...) rather than namespaced -
// see CLAUDE.md on why those can't move.
server: {
  proxy: {
    '/api': 'http://localhost:8000',
    '/verify': 'http://localhost:8000',
    '/logout': 'http://localhost:8000',
    '/me': 'http://localhost:8000',
    '/profile': 'http://localhost:8000',
    '/password': 'http://localhost:8000',
    '/2fa': 'http://localhost:8000',
  },
},
```

This is a general piece of API-design advice dressed as a config file: **the
first time something is depended on by another system, its URL becomes a public
contract**, however private it felt when you wrote it.

## The frontend

### Structure

The app is small enough to describe in one list:

- `App.tsx`: the shell. Fetches `GET /me` once, renders the nav and the router.
- `Apps.tsx`: the landing page (`/`).
- `Login.tsx`: the sign-in form (`/login`).
- `Account.tsx`: the signed-in user's own details (`/account`).
- `Admin.tsx`: the user list (`/admin`).
- `api.ts`: one shared `fetch` wrapper.
- `NavBar.tsx`, `types.ts`, `index.css`.

The toolchain matches the rest of the fleet: React 19, `react-router-dom`, Vite,
TypeScript, `oxlint`, and Vitest with Testing Library, with the Node version
pinned in a `.node-version` file.

### One fetch wrapper

Every page talks to the backend through one small module:

```ts
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      // no JSON body - keep the status text
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}
```

Three small design points. Errors are a typed `ApiError` carrying the HTTP status,
so callers can branch on `401` vs `403` without parsing message strings. The
backend consistently returns `{"detail": ...}` for failures (that's what the
central error handler produces), and the wrapper surfaces `detail` as the error
message, which means a form can display the server's own words (like "incorrect
email or password") without any per-form translation. And `204 No Content` is
special-cased, since calling `.json()` on an empty body throws; the profile
update route returns exactly that.

`credentials: 'same-origin'` is the line that makes the whole thing work with a
cookie: nothing in the frontend carries a token. The browser attaches the session
cookie to each request, and the server decides who's asking.

### The shell has to tolerate being signed out

A downstream app's frontend treats a `401` from `/me` as fatal. If you're not
signed in, nothing works, so it redirects to login. This app can't do that, because
its landing page is public. The shell therefore does something unusual:

```tsx
const [user, setUser] = useState<User | null>(null)
// Unlike a downstream app's own frontend (which requires auth for
// everything and treats a 401 from GET /me as fatal), this app has to
// render the Apps page whether or not the visitor is signed in - so a
// 401 here is just "not logged in", not an error.
const [checkedAuth, setCheckedAuth] = useState(false)

useEffect(() => {
  api
    .get<User>('/me')
    .then(setUser)
    .catch(() => setUser(null))
    .finally(() => setCheckedAuth(true))
}, [])
```

Two pieces of state, not one. `user` is `null` for "signed out", but the first
render also has `user === null` before the request finishes. Without
`checkedAuth` the nav would flash "Sign in" at a signed-in user for a moment on
every page load, or worse, the account page would redirect them to login before
`/me` had answered. The `if (!checkedAuth) return <p>Loading...</p>` guard waits
for the first answer.

### Logging out is a form post, not a `fetch`

```tsx
const handleLogout = () => {
  // A real top-level form submission, not fetch() - POST /logout
  // redirects to /login, which fetch() can't turn into a page navigation.
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = '/logout'
  document.body.appendChild(form)
  form.submit()
}
```

`POST /logout` is one of the frozen legacy routes, and it replies with a `303`
redirect to `/login`. A `fetch()` call would follow that redirect internally and
hand the JavaScript a login-page response body, leaving the browser sitting on
whatever page it was on. A real form submission is a top-level navigation, so the
browser follows the redirect and *lands* on the login page. It's four lines of
DOM code that exist purely to avoid rewriting a route I wasn't allowed to change.
(A later finance-side frontend does the same thing for the same reason, with its
form pointing at this service.)

### Page-level handling of 401 and 403

Back to the question the JSON admin route left open: if the API no longer
redirects, who does? The page does, by mapping the response onto a small state
machine:

```tsx
type Status = 'loading' | 'ok' | 'unauthenticated' | 'forbidden' | 'error'

.catch((err: unknown) => {
  if (err instanceof ApiError && err.status === 401) setStatus('unauthenticated')
  else if (err instanceof ApiError && err.status === 403) setStatus('forbidden')
  else setStatus('error')
})

...
if (status === 'unauthenticated') return <Navigate to="/login?rd=/admin" replace />
if (status === 'forbidden') {
  return <p className="error">Forbidden - your account isn't on the admin allowlist.</p>
}
```

The first version of this lived inside the `Admin` component. As more admin
pages arrived it was lifted into a reusable hook, `useAdminList`, which Part 6
leans on heavily. The behaviour is identical: signed out goes to login with a
return path, signed in but not allowed gets a message, anything else gets a
generic error. Using `<Navigate ... replace />` rather than pushing a new history
entry means the back button doesn't strand the user on a page that immediately
redirects them again.

### The apps page

The landing page is a grid of cards, each one a plain `<a>`:

```tsx
// Plain <a>, not react-router's <Link> - these point at other
// origins entirely. A logged-out click bounces through that
// app's own forward-auth back to /login?rd=<that app> ...
<a key={app.url} className="app-card" href={app.url}>
  <strong>{app.name}</strong>
  <p>{app.description}</p>
</a>
```

The comment records something I like about the design. The apps page doesn't need
to know whether you're signed in. A click on an app link goes to that app; if
you're signed out, the *app's own* forward-auth sends you to `/login?rd=<that
app>`; you sign in; you land back on the app. The directory is just links, and the
single sign-on machinery from Part 1 does all the work.

## Serving it: nginx and the proxy-by-default decision

In production there's no Node server. `vite build` produces static files in
`frontend/dist/`, and nginx serves them. The interesting question is *which
requests* get static files and which get proxied to Python.

A single-page app has client-side routes: `/login`, `/admin` and `/account` don't
exist as files; the browser asks nginx for them and nginx must hand back
`index.html` so React Router can take over. The natural config is to list those
routes explicitly:

```nginx
root __BACKEND_DIR__/frontend/dist;

location = / { try_files /index.html =404; }
location = /login { try_files /index.html =404; }
location = /admin { try_files /index.html =404; }
location = /account { try_files /index.html =404; }

location /assets/ {
    try_files $uri =404;
    expires 1y;
    add_header Cache-Control "public, immutable";
}

location / {
    proxy_pass http://127.0.0.1:8000;
    ...
}
```

The `location =` form is an *exact match*, which is why each page route is its
own line. The `/assets/` block serves Vite's hashed bundle files with a year-long
`immutable` cache header, which is safe because the filenames contain a content
hash, so a new build produces new names. And the last block is the part I want to
draw attention to.

**The default is to proxy, not to serve static files.** The usual SPA config does
the opposite: serve `index.html` for anything not matching a file. I didn't do
that because this backend has a long tail of bare legacy paths (`/verify`,
`/logout`, `/me`, `/profile`, `/password`, `/2fa/*`) that other systems hardcode.
With proxy-by-default, a new backend route just works without nginx needing to
know about it. The cost is that every new *frontend* page route needs a matching
`location =` line, and forgetting one means a browser refresh on that page
returns a `404`, as the router only handles in-app navigation. The project notes
carry that as a standing reminder.

## Jenkins gets a second stage

The pipeline needed a frontend build, and the Test stage became two parallel
branches:

```groovy
stage('Test') {
    parallel {
        stage('Backend') { ... uv sync --locked --extra dev; uv run pytest ... }
        stage('Frontend') {
            steps {
                dir('frontend') {
                    sh '''
                        mkdir -p ../reports
                        npm ci
                        npm run lint
                        npm test -- --reporter=junit --outputFile=../reports/junit-frontend.xml
                        npm run build
                    '''
                }
            }
        }
    }
}
```

`npm ci` (as opposed to `npm install`) installs exactly what the lockfile says
and fails if it disagrees, which is the same philosophy as `uv sync --locked`.
Lint, test and build run in that order so the cheapest checks fail first. The
Node version doesn't come from a Jenkins-managed tool installation. The
environment block puts the `nodenv` shims on `PATH`, and the version is dispatched
from `frontend/.node-version`, so CI and local development use the same Node.

The `frontend/dist/` the build produces is then simply part of the checkout that
`deploy.sh` rsyncs to the container. It isn't excluded from the sweep, so a new
build's output replaces the old one, with `--delete` removing stale hashed files.
Both test branches publish JUnit results, so a failure points at a specific test
rather than a console log.

## Letting people edit their own account

The last commit in this part gives the account page something to do. It had been
read-only; now it has two forms.

```tsx
const handleNameSubmit = async (e: FormEvent) => {
  e.preventDefault()
  setNameStatus('saving')
  setNameError(null)
  try {
    await api.patch('/profile', { name })
    await refreshUser()
    setNameStatus('idle')
  } catch (err) {
    setNameError(err instanceof ApiError ? err.message : 'Something went wrong')
    setNameStatus('error')
  }
}
```

There are two forms rather than one because the backend treats the two fields
differently, and the UI shouldn't pretend otherwise. Changing your **display
name** needs nothing but a session. Changing your **email** requires your
**current password**, because email is the thing a password-reset flow or an
account takeover would target. The email form therefore has a password field the
name form doesn't, with a comment pointing at why.

Two things in that component are worth pulling out.

**Re-fetch instead of trusting the response.** `PATCH /profile` returns
`204 No Content` (Part 1 explained why: there's no get-by-id to return the
refreshed record from). So after each successful save the form calls `/me` again
and pushes the result up to the shell. That way the name in the nav bar updates
immediately and the `User` the whole app holds is the server's version, not the
client's guess.

**Hooks before the early return.** Near the top of the component:

```tsx
// Hooks can't run conditionally, so these are declared before the
// !user redirect below - `user` is only ever null for the one render
// before that redirect takes effect.
const [name, setName] = useState(user?.name ?? '')
...
if (!user) return <Navigate to="/login?rd=/account" replace />
```

React requires hooks to be called in the same order on every render, which
rules out calling `useState` after a conditional `return`. So every piece of
state is declared first, seeded defensively from `user?.name ?? ''`, and only
then does the component bail out for a signed-out visitor. It looks fussy, and it
is precisely the sort of rule a linter catches and a human forgets.

The same commit also had to change `App.tsx` so the shell passes down an
`onUserUpdated` callback and the API wrapper grew a `patch` method alongside
`get` and `post`. The commit stat reads 185 lines added and 24 removed across four
files, roughly half of it test code covering both forms and their error states.

Password and two-factor management are still missing from this page. The text on
the page says so ("More account management... is coming here later"), and it was
a conscious stopping point. Those routes exist on the backend; they need UI.

## What this part changed

Counting in files, the story is: one commit adds a server-rendered admin page;
the next deletes all server-rendered HTML, adds 44 files and a few thousand lines
(the bulk being `package-lock.json`, tests and CSS), and turns Python into a JSON
API; the third gives the account page real forms.

Counting in lessons, it's shorter:

1. **Don't defend a sunk cost.** The inline-HTML admin page took a short time and
   was wrong for where the project was going. Replacing it quickly cost less than
   living with it.
2. **Draw the line between API and UI early, and draw it hard.** "No HTML in
   Python" is a rule I can check in code review by grepping for it.
3. **Treat URLs other systems use as a contract.** The frozen legacy paths are the
   reason the migration was safe, and the reason the namespace is a little untidy.
4. **Test security properties adversarially.** The login test feeds in a hostile
   `rd`, which is the case that matters.

With a frontend in place, the service can finally do something it couldn't when it
was only a login form: be part of someone else's login flow. The next part is
about teaching it to speak OpenID Connect.

---

<!-- commits: 134aef8 7642b15 fc4ee8b -->

Previous: [← Shipping it](02-shipping-it-jenkins-uv-and-a-container.md) · Next: [Becoming an OpenID Connect provider →](04-becoming-an-openid-connect-provider.md)
