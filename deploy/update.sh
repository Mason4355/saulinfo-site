#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/saulinfo-site}"
BRANCH="${BRANCH:-main}"
CONTAINER_NAME="${CONTAINER_NAME:-saulinfo-site}"
WAIT_SECONDS="${WAIT_SECONDS:-90}"
DOCKER_CLEANUP="${DOCKER_CLEANUP:-1}"

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

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  fail "docker compose or docker-compose is required"
fi

[ -d "${PROJECT_DIR}" ] || fail "project dir not found: ${PROJECT_DIR}"

cd "${PROJECT_DIR}"

if [ ! -d .git ]; then
  fail "no git repository in ${PROJECT_DIR}"
fi

# Ignore executable-bit noise on Linux hosts so update commands don't fail
# after chmod fixes done by installer or admin.
git config core.filemode false || true

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
"${COMPOSE_CMD[@]}" up -d --build --remove-orphans

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
    "${COMPOSE_CMD[@]}" logs --tail=120
    fail "container ${CONTAINER_NAME} did not become healthy within ${WAIT_SECONDS}s (status=${STATUS})"
  fi

  sleep 3
done

if [ "${DOCKER_CLEANUP}" = "1" ]; then
  log "Pruning Docker builder cache"
  docker builder prune -af >/dev/null 2>&1 || true

  log "Pruning dangling Docker images"
  docker image prune -f >/dev/null 2>&1 || true
fi

log "Update complete: ${REMOTE_COMMIT}"
