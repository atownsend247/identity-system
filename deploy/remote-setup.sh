#!/usr/bin/env bash
# Runs ON the deploy target - a Debian 13 (trixie) Proxmox LXC, invoked
# over SSH by deploy/deploy.sh after it rsyncs the repo there. Also
# runnable standalone if you SSH in yourself: `BACKEND_DIR=/opt/identity-system
# bash deploy/remote-setup.sh` (both env vars below default sensibly if
# unset, so a bare `bash deploy/remote-setup.sh` from the checkout works
# too).
#
# Installs required packages, builds/refreshes the backend venv (plain
# venv + pip, same install this project's README/Dockerfile always used -
# there's no uv.lock here to make `uv sync` meaningful), and installs +
# reloads the systemd unit and nginx config from this directory.
#
# Relies on Debian trixie's system `python3` already being 3.13 (see
# pyproject.toml's requires-python / .python-version) rather than pinning
# a specific interpreter itself - the version check below fails loudly
# instead of silently building a venv against the wrong Python if this
# ever runs against an older Debian/Ubuntu release.
#
# Does NOT provision any accounts (that's a one-time manual step - see
# README.md's "sessionkit --db <path> add ...") and does NOT create
# __BACKEND_DIR__/.env (copy .env.example there and fill it in by hand
# first - see identity-system-api.service's header comment for why).

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root." >&2
    exit 1
fi

BACKEND_DIR="${BACKEND_DIR:-/opt/identity-system}"
SERVICE_NAME="${SERVICE_NAME:-identity-system-api}"
SERVICE_USER="root"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUIRED_PACKAGES=(python3-venv python3-pip nginx curl rsync)

echo "Installing required packages (${REQUIRED_PACKAGES[*]})..."
apt-get update
apt-get install -y --no-install-recommends "${REQUIRED_PACKAGES[@]}"

PYTHON_VERSION="$(python3 -c 'import platform; print(platform.python_version())')"
if [[ "${PYTHON_VERSION}" != 3.13.* ]]; then
    echo "System python3 is ${PYTHON_VERSION}, not 3.13.x - this script" >&2
    echo "assumes a Debian 13 (trixie) target where python3 is already" >&2
    echo "3.13. Install python3.13 yourself first (or move to trixie)." >&2
    exit 1
fi

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    echo "Creating system user '${SERVICE_USER}'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

echo "Ensuring ${BACKEND_DIR}/data exists and is owned by ${SERVICE_USER}..."
mkdir -p "${BACKEND_DIR}/data"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${BACKEND_DIR}/data"

echo "Building/refreshing the venv and installing identity-system in ${BACKEND_DIR}..."
if [[ ! -d "${BACKEND_DIR}/.venv" ]]; then
    python3 -m venv "${BACKEND_DIR}/.venv"
fi
"${BACKEND_DIR}/.venv/bin/pip" install --no-cache-dir --upgrade pip
# --force-reinstall matters here: this installs from a local directory path,
# not a version-pinned requirement, and pyproject.toml's version is bumped
# manually (see src/identity_system/__init__.py) - without this flag, pip
# resolves "identity-system" to the same version already in the venv and
# skips reinstalling altogether, silently leaving the OLD code running even
# though rsync just synced new source here. Not just a style nit: this bit
# the OIDC rollout for real (routes 404'd post-deploy because the venv was
# still serving the pre-OIDC build).
"${BACKEND_DIR}/.venv/bin/pip" install --no-cache-dir --force-reinstall "${BACKEND_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${BACKEND_DIR}/.venv"

if [[ ! -f "${BACKEND_DIR}/.env" ]]; then
    echo "WARNING: ${BACKEND_DIR}/.env is missing - copy .env.example there" >&2
    echo "and fill it in, then re-run this script (or just restart ${SERVICE_NAME})." >&2
else
    # IDENTITY_SYSTEM_DB has to resolve inside data/ - that's the only place
    # (besides .env itself) deploy.sh's rsync --delete doesn't wipe on the
    # next deploy. .env.example's own local-dev default (./auth.db) is
    # exactly the wrong value here if it ever gets deployed unedited - warn
    # loudly rather than silently losing the account database next deploy.
    db_path="$(grep -E '^IDENTITY_SYSTEM_DB=' "${BACKEND_DIR}/.env" | tail -1 | cut -d= -f2-)"
    if [[ -n "${db_path}" && "${db_path}" != "${BACKEND_DIR}/data/"* ]]; then
        echo "WARNING: IDENTITY_SYSTEM_DB in ${BACKEND_DIR}/.env is '${db_path}'," >&2
        echo "not inside ${BACKEND_DIR}/data/ - deploy.sh's rsync --delete will" >&2
        echo "wipe it on the next deploy (only data/, .env, *.db and *.db-* are" >&2
        echo "excluded from that sweep). Move the db file into ${BACKEND_DIR}/data/," >&2
        echo "point IDENTITY_SYSTEM_DB at it there, and restart ${SERVICE_NAME}." >&2
    fi

    # Same reasoning as IDENTITY_SYSTEM_DB above, for the OIDC signing key
    # oidc.load_or_create_signing_key provisions on first run - it's a
    # secret, not a .db file, so it has no belt-and-suspenders *.ext
    # exclude rule in deploy.sh; data/ is the only thing protecting it.
    key_path="$(grep -E '^IDENTITY_SYSTEM_OIDC_SIGNING_KEY_PATH=' "${BACKEND_DIR}/.env" | tail -1 | cut -d= -f2-)"
    if [[ -n "${key_path}" && "${key_path}" != "${BACKEND_DIR}/data/"* ]]; then
        echo "WARNING: IDENTITY_SYSTEM_OIDC_SIGNING_KEY_PATH in ${BACKEND_DIR}/.env is" >&2
        echo "'${key_path}', not inside ${BACKEND_DIR}/data/ - deploy.sh's rsync --delete" >&2
        echo "will wipe it on the next deploy, silently rotating the OIDC signing key" >&2
        echo "and breaking every relying party until they re-fetch the new JWKS. Move" >&2
        echo "it into ${BACKEND_DIR}/data/, point IDENTITY_SYSTEM_OIDC_SIGNING_KEY_PATH" >&2
        echo "at it there, and restart ${SERVICE_NAME}." >&2
    fi
fi

echo "Installing systemd unit..."
sed "s|__BACKEND_DIR__|${BACKEND_DIR}|g" "${SCRIPT_DIR}/${SERVICE_NAME}.service" \
    | install -m 644 /dev/stdin "/etc/systemd/system/${SERVICE_NAME}.service"

echo "Installing nginx config..."
sed "s|__BACKEND_DIR__|${BACKEND_DIR}|g" "${SCRIPT_DIR}/nginx-identity-system.conf" \
    | install -m 644 /dev/stdin "/etc/nginx/sites-available/identity-system"
if [[ ! -e "/etc/nginx/sites-enabled/identity-system" ]]; then
    ln -s "/etc/nginx/sites-available/identity-system" "/etc/nginx/sites-enabled/identity-system"
fi

echo "Reloading systemd..."
systemctl daemon-reload

echo "(Re)starting ${SERVICE_NAME}..."
systemctl enable --now "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

echo "Testing and reloading nginx..."
nginx -t
systemctl reload nginx

echo "Done."
