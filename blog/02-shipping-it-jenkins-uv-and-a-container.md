# Central login service: Shipping it - Jenkins, uv and a Debian container - Part 2

*~25 min read*

[Part 1](01-why-a-central-login-service.md) ended with a login service that
passed its tests and ran on my laptop. That's the easy half. A login service
that isn't reachable from the other machines in the house isn't a login service,
and "reachable" brought a pile of small decisions and an unglamorous run of
commits with messages like `ci: add uv.lock`, `fix python missmatch`
and `fix deploy script`.

This post is about all of that: the deployment target, the Jenkins pipeline,
moving the build to `uv`, the `rsync --delete` that nearly ate my accounts
database, and a deploy bug where the new code was copied across and then
silently never ran. None of it is clever. All of it is the sort of thing that
eats time the first time you meet it, so I'd rather write it down.

## The shape of the deployment

Everything I self-host runs in small Proxmox LXC containers, one service to a
container where practical. `identity-system` gets a Debian 13 ("trixie")
container with:

- the Python backend running under `systemd`, listening on a local port;
- `nginx` in front of it, terminating the public hostname;
- the application checked out under `/opt/identity-system`, with a virtual
  environment alongside it.

There is deliberately no Docker image. The container *is* the isolation
boundary, and layering another one on top would add a build step, a registry or
a local image cache, and a second place for things to go wrong, in return for
isolation I already have. The repository's `deploy/` directory holds exactly four
files:

```
deploy/
  deploy.sh                    # runs on the Jenkins agent
  remote-setup.sh              # runs on the container, over SSH
  identity-system-api.service  # systemd unit template
  nginx-identity-system.conf   # nginx site template
```

The flow is: Jenkins builds and tests, then `deploy.sh` copies the checkout to
the container with `rsync` and runs `remote-setup.sh` there. There's no
artifact registry, no image, no orchestrator. I'm describing the simplest thing
that could possibly work, and for a one-instance home service it's enough.

## The Jenkinsfile, and how it got to `uv`

The first Jenkinsfile was copied from the pipeline of an older project, and its
first draft of the Test stage did what most Python CI does: create a virtual
environment, `pip install -e ".[dev]"`, run `pytest`.

```groovy
stage('Test') {
    steps {
        sh '''
            python3 -m venv .venv
            .venv/bin/pip install --no-cache-dir -e ".[dev]"
            mkdir -p reports
            .venv/bin/pytest --junitxml=reports/junit.xml
        '''
    }
}
```

That works until the Python on the CI agent isn't the Python you think it is.
This project requires Python 3.13 (it says so in `requires-python`), and I'd
bumped to 3.13 deliberately in a `feat: upgrade to python 3.13` commit that
added a `.python-version` file. The CI agent's system Python was something
older.

### Failing loudly instead of testing the wrong thing

My first response was a guard in the Jenkinsfile:

```groovy
python_version="$(python3 -c 'import platform; print(platform.python_version())')"
case "$python_version" in
    3.13.*) ;;
    *) echo "Jenkins agent python3 is $python_version, not 3.13.x" >&2; exit 1 ;;
esac
```

The idea is sound and I still like it as a principle: if an assumption about
the environment matters, check it and fail with a message that names the
problem, rather than quietly testing against the wrong interpreter and getting
confusing results (or worse, passing). A matching check went into
`remote-setup.sh` for the production side, because the deploy target relies on
Debian trixie's *system* `python3` being 3.13:

```bash
PYTHON_VERSION="$(python3 -c 'import platform; print(platform.python_version())')"
if [[ "${PYTHON_VERSION}" != 3.13.* ]]; then
    echo "System python3 is ${PYTHON_VERSION}, not 3.13.x - this script" >&2
    echo "assumes a Debian 13 (trixie) target where python3 is already" >&2
    echo "3.13. Install python3.13 yourself first (or move to trixie)." >&2
    exit 1
fi
```

The very next commit, `fix python missmatch`, changed `python3` to `python` in
the Jenkins check, because on that agent the `python3` binary and the `python`
the rest of the toolchain used weren't the same interpreter. I then replaced the
whole approach about half an hour later, which brings me to `uv`.

### Letting uv own the toolchain

Other projects in my fleet already use [uv](https://docs.astral.sh/uv/), and the
thing that makes it a good fit for CI is that it can *download the right
Python itself*. If the agent doesn't have 3.13, `uv` fetches it, based on
`.python-version` and `requires-python`. That makes my hand-rolled version check
redundant, so the commit `ci: convert to UV` deleted it:

```groovy
stage('Install uv') {
    steps {
        sh 'curl -LsSf https://astral.sh/uv/install.sh | sh'
    }
}
...
stage('Test') {
    steps {
        sh '''
            mkdir -p reports
            uv sync --locked --extra dev
            uv run pytest --junitxml=reports/junit.xml
        '''
    }
}
```

Three details here are easy to get wrong:

**Bootstrapping uv inline.** Rather than assuming the agent has it installed,
the pipeline has an `Install uv` stage that runs the official installer. The
environment block adds `${env.HOME}/.local/bin` to `PATH` so the freshly
installed binary is found by later stages. It's a few seconds per build in
exchange for not having a pre-requisite on the agent that I'd forget about
until the agent was rebuilt.

**`--locked`.** `uv sync --locked` fails if `pyproject.toml` and `uv.lock` have
drifted apart, instead of quietly re-resolving dependencies. That's the entire
point of a lockfile in CI: the build tests exactly what you committed. The
companion commit `ci: add uv.lock` came right after the conversion
commit, with the message "CI would fail without it": the Jenkinsfile was asking
for a strict lockfile that hadn't been committed yet.

**`--extra dev`.** In the uv-native sibling projects, the test dependencies live
in a `[dependency-groups]` table, which a bare `uv sync` picks up by default.
Here they're a plain `[project.optional-dependencies]` extra, because I wanted
the README's `pip install -e ".[dev]"` to keep working for anyone not using uv.
The trade-off is that `uv sync` doesn't install an extra unless you ask for it,
so the pipeline spells out `--extra dev`. Omitting it produces a perfectly
healthy-looking sync followed by `pytest` not being installed.

The Jenkinsfile's own header comment records one more asymmetry that I'd
otherwise forget: `remote-setup.sh` still installs the backend with plain
`pip` into a plain virtual environment on the container, not with `uv`. So
`uv.lock` pins what **CI tests against**, not what's **deployed**. That's a real
gap between "tested" and "shipped", and I chose to leave it as is for now
because the deploy-time install resolves the same pinned `sessionkit` tag, and
because switching the container to `uv` would mean one more tool to install
and keep current there. It's the sort of thing worth writing in a comment so a
future reader doesn't assume the lockfile is the whole story.

### A Multibranch pipeline and a guarded Deploy

The pipeline is configured as a Multibranch Pipeline job, so `env.BRANCH_NAME`
is populated and the Deploy stage can be guarded:

```groovy
stage('Deploy') {
    when {
        branch 'main'
    }
    steps {
        sh 'chmod +x deploy/deploy.sh && ./deploy/deploy.sh'
    }
}
```

Feature branches run Test and stop. Only `main` deploys. Two options in the
`options {}` block are worth keeping on every pipeline:

```groovy
options {
    timestamps()
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '20'))
}
```

`disableConcurrentBuilds()` matters more than it looks, because two overlapping
deploys of the same service would race on the same directory. And the post
section runs `cleanWs()` in an `always` block so the workspace, which now
contains a checkout and a virtual environment, doesn't pile up on the agent.

The deploy target itself is a handful of environment variables:

```groovy
environment {
    PATH = "${env.HOME}/.local/bin:${env.PATH}"

    DEPLOY_HOST        = '<container address>'
    DEPLOY_USER        = 'root'
    BACKEND_DIR        = '/opt/identity-system'
    BACKEND_SERVICE    = 'identity-system-api'
}
```

One of the earliest commits (`feat: fix identity IP`) is entirely a
single changed address on that host line. The pipeline was copied from a
neighbouring project, and a copied pipeline carries its old target with it. If
a deploy "succeeds" but nothing changes, check where it went before anything else.

There's no Jenkins credential involved in the deploy: the agent is assumed to
already have working SSH key access to the container. That's a one-time setup
the scripts can't do for you, and `deploy.sh`'s header says so in plain words.
I prefer that to a credential plugin for a single home network, but it does
mean the agent's SSH key is the keys to the kingdom, and I treat it
accordingly.

## `deploy.sh`: the rsync that wants to delete things

`deploy.sh` runs on the *agent*, not the container. It does three things: make
sure `rsync` exists on the target, push the checkout, and run the remote setup
script.

```bash
rsync -az --delete \
    -e "ssh ${SSH_OPTS[*]}" \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude 'reports/' \
    --exclude 'data/' \
    --exclude '.env' \
    "${REPO_ROOT}/" "${SSH_TARGET}:${BACKEND_DIR}/"
```

`--delete` makes the destination match the source exactly: anything on the
container under `/opt/identity-system` that isn't in the checkout gets removed.
That's the right behaviour for source code (a file I deleted from the repo
shouldn't linger and keep being imported), and it's *exactly* the wrong
behaviour for anything the running service creates. The service creates two
kinds of thing:

1. The SQLite database containing every account, session and two-factor secret.
2. Its `.env`, which holds the deployment's real configuration and which is
   intentionally never in the repository.

Hence the two excludes that matter, `data/` and `.env`. The design is that the
database lives in `/opt/identity-system/data/auth.db`, the `data/` directory is
excluded from the sweep, and so the database survives every deploy.

Notice the `set -euo pipefail` at the top of the script. With `-e` a failing
`rsync` aborts the deploy rather than continuing on to restart a half-updated
service; `-u` makes an unset variable an error (the script also uses
`: "${DEPLOY_HOST:?DEPLOY_HOST must be set}"` for each required variable, which
gives a readable message naming exactly which one is missing); `pipefail` makes
failures inside pipelines count.

### The trap: a default that deletes your database

Here is the trap behind the innocuously titled `fix deploy script` commit.

`data/` is only protected if the database actually *lives* in `data/`. And the
`.env.example` I'd written for local development says:

```
IDENTITY_SYSTEM_DB=./auth.db
```

That's a sensible local default: a file in the current directory. But the
systemd unit sets `WorkingDirectory=/opt/identity-system`, so on the container
that relative path resolves to `/opt/identity-system/auth.db`, which is **outside
`data/`**. If I'd copied `.env.example` to the container unedited, the service
would have happily created its database in the one place the next deploy's
`rsync --delete` treats as garbage. Everything would work perfectly until the
second deploy, at which point every account would vanish.

It's a nasty class of bug because there's no error at any point. It takes a
deploy *succeeding* to destroy the data.

The fix was belt and braces, applied in three places:

**1. Say so in `.env.example`.** The comment above the variable now states
that when deployed it MUST be an absolute path inside `data/`, and explains
precisely why.

**2. A second safety net in rsync.**

```bash
    --exclude '*.db' \
    --exclude '*.db-*' \
```

Those patterns match a SQLite file anywhere in the tree (and its `-wal` and
`-shm` sidecar files), so even a database in the wrong place is spared. This is
a deliberate overlap: if the first protection is defeated by a typo, the second
catches it.

**3. A check in `remote-setup.sh` that warns loudly.**

```bash
db_path="$(grep -E '^IDENTITY_SYSTEM_DB=' "${BACKEND_DIR}/.env" | tail -1 | cut -d= -f2-)"
if [[ -n "${db_path}" && "${db_path}" != "${BACKEND_DIR}/data/"* ]]; then
    echo "WARNING: IDENTITY_SYSTEM_DB in ${BACKEND_DIR}/.env is '${db_path}'," >&2
    echo "not inside ${BACKEND_DIR}/data/ - deploy.sh's rsync --delete will" >&2
    echo "wipe it on the next deploy (only data/, .env, *.db and *.db-* are" >&2
    echo "excluded from that sweep). Move the db file into ${BACKEND_DIR}/data/," >&2
    echo "point IDENTITY_SYSTEM_DB at it there, and restart ${SERVICE_NAME}." >&2
fi
```

It reads the variable out of the deployed `.env` with `grep` and `tail -1`
(last assignment wins, as it would if the file were sourced), strips the name
with `cut -d= -f2-`, and compares against the expected prefix. Two details: `cut
-f2-` rather than `-f2` so a value containing an `=` isn't truncated, and the
whole check is only a *warning*. It deliberately doesn't abort, because the
service might legitimately be mid-migration and a hard failure here would block a
deploy that fixes it.

The project notes carry a line telling any future version of me not to remove
that check. Checks like this look like noise right up until the day they matter.

## `remote-setup.sh`: idempotent by construction

The script that runs on the container is written so that running it twice does
the same thing as running it once. That matters because it runs on **every
deploy**, not just the first. Walking through it:

```bash
echo "Installing required packages (${REQUIRED_PACKAGES[*]})..."
apt-get update
apt-get install -y --no-install-recommends "${REQUIRED_PACKAGES[@]}"
```

`apt-get install` of something already installed is a no-op, and
`--no-install-recommends` keeps the container lean. The package list is
`python3-venv python3-pip nginx curl rsync`.

```bash
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi
```

The "create the user if it doesn't exist" guard is the idiom for idempotence:
test, then act. The same pattern appears when linking the nginx site:

```bash
if [[ ! -e "/etc/nginx/sites-enabled/identity-system" ]]; then
    ln -s "/etc/nginx/sites-available/identity-system" "/etc/nginx/sites-enabled/identity-system"
fi
```

The venv is built only if missing (`if [[ ! -d "${BACKEND_DIR}/.venv" ]]`) and
then the package is installed into it. Then the unit and nginx config are
installed. Notice how:

```bash
sed "s|__BACKEND_DIR__|${BACKEND_DIR}|g" "${SCRIPT_DIR}/${SERVICE_NAME}.service" \
    | install -m 644 /dev/stdin "/etc/systemd/system/${SERVICE_NAME}.service"
```

The unit file in the repository contains a literal placeholder, `__BACKEND_DIR__`,
and `sed` substitutes the real path as it's installed (using `|` as the sed
delimiter because the replacement contains slashes). `install -m 644 /dev/stdin`
writes the result with the right permissions in one step. The same trick is used
for the nginx config. Keeping templates in the repository and substituting at
install time means there's exactly one place to edit them. A comment at the top
of the nginx file says as much, because the other way to lose an afternoon is
hand-editing the file on the container (for instance, letting `certbot` modify
it in place) and having the next deploy cheerfully overwrite your change.

Finally:

```bash
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

nginx -t
systemctl reload nginx
```

`daemon-reload` makes systemd re-read the unit; `enable --now` is a no-op if
it's already enabled and running; `restart` ensures new code is loaded;
`nginx -t` **tests the configuration before reloading**, so a typo in the config
aborts the script (thanks to `set -e`) instead of taking the proxy down.

### The unit file

```ini
[Service]
Type=simple
User=identity-system
Group=identity-system
WorkingDirectory=__BACKEND_DIR__
EnvironmentFile=__BACKEND_DIR__/.env
ExecStart=__BACKEND_DIR__/.venv/bin/uvicorn identity_system.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=2
```

`EnvironmentFile` is how the service gets its configuration without it ever
being in the repository. `Restart=on-failure` with a two-second delay means a
crash comes back on its own without hammering a hard failure in a tight loop.
And `--host 127.0.0.1` binds only to the loopback interface, so the application
port isn't reachable except via nginx on the same machine.

Note `User=identity-system`. The service runs as a dedicated, unprivileged
system account, not root. It only ever needs to read and write its own
database and signing key under `data/`, so that is all it is given: if the
process is ever compromised, the attacker is confined to what that one user
can touch rather than owning the container. For the one service in the house
that holds everyone's credentials, this matters more than for most.

The account is created by `remote-setup.sh` (the `useradd --system
--no-create-home --shell /usr/sbin/nologin` call shown earlier: no home
directory and no login shell, so nobody can sign in as it), and the same
script makes sure it owns what it must write to:

```bash
SERVICE_USER="identity-system"
...
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${BACKEND_DIR}/data"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${BACKEND_DIR}/.venv"
```

The `data/` directory is the one place the service writes, and the virtual
environment is chowned so the process can read its own installed packages
(the deploy itself still runs as root over SSH, which is why those `chown`
calls have to be explicit after each install). The application code in
`/opt/identity-system` stays root-owned and read-only to the service, so a
compromised process can't rewrite the code it's running from.

One permissions trap to watch for is that the `.env` file is read by systemd
*before* it drops privileges (`EnvironmentFile` is loaded by the service
manager, not the process), so it can stay root-owned with mode `600` and the
service still gets its settings, without the unprivileged account being able
to read the file itself.

## The nginx config

The first nginx config is short enough to quote entirely:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name sso.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

It listens on port 80 for IPv4 and IPv6, matches the hostname, and proxies
everything to the Python app, passing the original `Host` and client address
along. (In this series I use `sso.example.com` as the public name; the real
one is a different hostname on a private domain, and one of the commits,
`feat: enable correct server name`, is just the swap from the placeholder to
the real name.)

TLS is not terminated in this file. In my setup that happens one layer further
out, but the comment in the file is firm that this needs HTTPS in front of it
before it's treated as more than a starting point, because the session cookie is
`Secure` by default and every downstream app's forward-auth hook assumes the
login service is reachable over HTTPS. Running a login page over plain HTTP
isn't a configuration to leave in place.

## The deploy that ran the old code

The last bug in this post belongs chronologically to the OIDC work in a later
part, but it's a deploy bug, so it goes here. It's the most instructive one.

After shipping a large new feature, the new routes returned `404`. The deploy
had succeeded: Jenkins was green, `rsync` had copied the new source, the setup
script had run, and the service had restarted. And the *old* code was still
serving requests.

The culprit was this line in `remote-setup.sh`:

```bash
"${BACKEND_DIR}/.venv/bin/pip" install --no-cache-dir "${BACKEND_DIR}"
```

That installs the project from a local directory. `pip` reads the version out
of the project metadata, sees that version of `identity-system` is already installed in the
venv, concludes there is nothing to do, and skips the install. The source on disk
was new, but the *installed copy* inside the virtual environment (the thing
`uvicorn` actually imports) was the previous build. The package version is bumped
by hand, so it hadn't changed.

The fix is one flag:

```bash
"${BACKEND_DIR}/.venv/bin/pip" install --no-cache-dir --force-reinstall "${BACKEND_DIR}"
```

`--force-reinstall` makes `pip` reinstall regardless of the version it finds.
It's a bit heavy-handed, as it also reinstalls dependencies, but it removes a
whole category of "I deployed it and nothing changed" from the table. The commit
message called it out, and the comment in the script says it "bit the OIDC
rollout for real (routes 404'd post-deploy because the venv was still serving the
pre-OIDC build)".

The general lesson is about where the truth lives. When a deploy and a runtime
disagree, the first question isn't "what did I deploy?" but "what is the process
*actually* running?". Here those were two different directories that looked like
one.

## What the pipeline looks like now

Put together, a push to `main` does this:

1. **Prereq / Install uv / Checkout**: confirm the Node version manager is set
   up (the frontend arrives in the next part), bootstrap `uv`, check out the
   branch.
2. **Test**: `uv sync --locked --extra dev` then `pytest`, with the JUnit XML
   published so Jenkins shows per-test results.
3. **Deploy** (main only): `rsync` the checkout to the container, then run
   `remote-setup.sh` there, which installs packages, rebuilds the install,
   re-templates the systemd and nginx files, restarts the service, tests and
   reloads nginx.

The two things I'd change if I did it again are small: install with `uv` on the container so what's
tested is what's shipped; and add a post-deploy smoke test, a `curl` of
`/verify` expecting a `401`, so "deployed but broken" fails the build rather than
being discovered by whichever app I next try to open. The second would
have caught the stale-venv problem within seconds.

What I wouldn't change is the habit this part is really about: every time
something went wrong, the fix was paired with a note, either a comment at the
failure site, a startup warning, or a line in the project notes. Most of the
traps in this post are the sort that stay invisible until you hit them. Writing
them down where the next change will see them is cheap.

The service is now live and reachable, which exposes the next limitation: its
only interface is a small inline-HTML login page, and I wanted somewhere to see
who had accounts. The next part replaces the HTML with a real frontend.

---

<!-- commits: d8873ec fc7903e fefebf9 289f00f 6e7bb24 775585b 88d3fca 59a09f2 a52a8dc 163c1fd -->

Previous: [← One place to type a password](01-why-a-central-login-service.md) · Next: [An admin page and a proper frontend →](03-an-admin-page-and-a-proper-frontend.md)
