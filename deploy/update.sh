#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/saulinfo-site}"
BRANCH="${BRANCH:-main}"
CONTAINER_NAME="${CONTAINER_NAME:-saulinfo-site}"
WAIT_SECONDS="${WAIT_SECONDS:-90}"

log() {
  echo "[saulinfo-site:update] $*"
}

fail() {
  echo "[saulinfo-site:update] ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "command not found: $1"
}

require_cmd git
require_cmd docker

[ -d "${PROJECT_DIR}" ] || fail "project dir not found: ${PROJECT_DIR}"

cd "${PROJECT_DIR}"

if [ ! -d .git ]; then
  fail "no git repository in ${PROJECT_DIR}"
fi

if [ -n "$(git status --porcelain)" ]; then
  fail "working tree is dirty; commit or discard local changes before update"
fi

log "Fetching latest changes from origin/${BRANCH}"
git fetch origin "${BRANCH}"

LOCAL_COMMIT="$(git rev-parse HEAD)"
REMOTE_COMMIT="$(git rev-parse "origin/${BRANCH}")"

if [ "${LOCAL_COMMIT}" != "${REMOTE_COMMIT}" ]; then
  log "Updating repository to ${REMOTE_COMMIT}"
  git merge --ff-only "origin/${BRANCH}"
else
  log "Repository already up to date"
fi

log "Rebuilding and restarting container"
docker compose up -d --build --remove-orphans

log "Waiting for container health: ${CONTAINER_NAME}"
START_TS="$(date +%s)"
while true; do
  if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    fail "container ${CONTAINER_NAME} is not running"
  fi

  STATUS="$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null || true)"

  if [ "${STATUS}" = "healthy" ] || [ "${STATUS}" = "running" ]; then
    break
  fi

  NOW_TS="$(date +%s)"
  ELAPSED="$((NOW_TS - START_TS))"
  if [ "${ELAPSED}" -ge "${WAIT_SECONDS}" ]; then
    docker compose logs --tail=120
    fail "container ${CONTAINER_NAME} did not become healthy within ${WAIT_SECONDS}s (status=${STATUS})"
  fi

  sleep 3
done

log "Pruning dangling Docker images"
docker image prune -f >/dev/null 2>&1 || true

log "Update complete: ${REMOTE_COMMIT}"
