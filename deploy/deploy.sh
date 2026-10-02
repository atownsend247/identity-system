#!/usr/bin/env bash
# Deploys this checkout to the Proxmox LXC named by DEPLOY_HOST. Run from
# the Jenkins agent (or any machine with SSH access to the target) - NOT on
# the target itself. Rsyncs the repo there, then runs deploy/remote-setup.sh
# on the target over SSH to install packages, build the venv, and install +
# reload the systemd unit and nginx config.
#
# Requires DEPLOY_HOST, DEPLOY_USER, BACKEND_DIR, BACKEND_SERVICE set in the
# environment (the Jenkinsfile's `environment {}` block does this), and
# working SSH key-based access as DEPLOY_USER@DEPLOY_HOST from wherever this
# runs - that's a one-time setup this script can't do for you.
#
# .env and data/ are never touched by this rsync's --delete sweep: .env is
# gitignored so it isn't in this checkout at all, and data/ is where the
# target's own SQLite file (and, by default, the OIDC signing key - see
# oidc.py's load_or_create_signing_key) is supposed to live (see
# remote-setup.sh) - so a real .env and an already-provisioned auth.db both
# survive every future deploy, AS LONG AS __BACKEND_DIR__/.env's
# IDENTITY_SYSTEM_DB actually points inside data/ (see
# identity-system-api.service's header comment - remote-setup.sh also
# checks this and warns loudly if it doesn't, and does the same for
# IDENTITY_SYSTEM_OIDC_SIGNING_KEY_PATH). *.db/*.db-* are excluded too,
# everywhere, not just under data/ - belt and suspenders in case
# IDENTITY_SYSTEM_DB is ever pointed somewhere else by mistake: an
# untracked .db file sitting outside data/ has no exclude rule of its own
# otherwise, and --delete treats it as garbage to remove.
#
# oidc_clients.json holds OIDC client secrets (hashed, but still a
# credential) - same treatment as .env: gitignored, excluded here, and
# expected to already exist in __BACKEND_DIR__ from a prior manual copy of
# oidc_clients.json.example (see README.md#registering-an-oidc-client). A
# missing file isn't fatal - it just means no OIDC clients are registered
# yet (see oidc_clients.py's load_oidc_clients).
#
# frontend/dist/ (built by the Jenkinsfile's Frontend stage before this
# script runs) is NOT excluded - it ships as part of the normal whole-repo
# sync, same as finance-system, so nginx's `root __BACKEND_DIR__/frontend/dist`
# (see nginx-identity-system.conf) finds it with no separate FRONTEND_DIR
# or extra rsync step needed. frontend/node_modules IS excluded, same
# reasoning as .venv below - it's huge and gets rebuilt from
# frontend/package-lock.json if anyone ever needs it on the container
# itself (normally nobody does; only the built dist/ ships).
#
# Usage: ./deploy.sh   (from the repo root)

set -euo pipefail

: "${DEPLOY_HOST:?DEPLOY_HOST must be set}"
: "${DEPLOY_USER:?DEPLOY_USER must be set}"
: "${BACKEND_DIR:?BACKEND_DIR must be set}"
: "${BACKEND_SERVICE:?BACKEND_SERVICE must be set}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)

echo "Ensuring rsync is available on ${DEPLOY_HOST}..."
ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" \
    'apt-get update && apt-get install -y --no-install-recommends rsync'

echo "Syncing code to ${SSH_TARGET}:${BACKEND_DIR}..."
rsync -az --delete \
    -e "ssh ${SSH_OPTS[*]}" \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude 'reports/' \
    --exclude 'data/' \
    --exclude '.env' \
    --exclude '*.db' \
    --exclude '*.db-*' \
    --exclude 'oidc_clients.json' \
    --exclude 'frontend/node_modules/' \
    "${REPO_ROOT}/" "${SSH_TARGET}:${BACKEND_DIR}/"

echo "Running remote setup on ${DEPLOY_HOST}..."
ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" \
    "BACKEND_DIR='${BACKEND_DIR}' SERVICE_NAME='${BACKEND_SERVICE}' bash '${BACKEND_DIR}/deploy/remote-setup.sh'"

echo "Done."
