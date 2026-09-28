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
# target's own SQLite file lives (see remote-setup.sh) - so a real .env and
# an already-provisioned auth.db both survive every future deploy.
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
    "${REPO_ROOT}/" "${SSH_TARGET}:${BACKEND_DIR}/"

echo "Running remote setup on ${DEPLOY_HOST}..."
ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" \
    "BACKEND_DIR='${BACKEND_DIR}' SERVICE_NAME='${BACKEND_SERVICE}' bash '${BACKEND_DIR}/deploy/remote-setup.sh'"

echo "Done."
