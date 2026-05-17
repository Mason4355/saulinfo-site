#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/progress.sh"

PROJECT_DIR="${PROJECT_DIR:-/root/saulinfo-site}"
BRANCH="${BRANCH:-main}"
CONTAINER_NAME="${CONTAINER_NAME:-saulinfo-site}"
WAIT_SECONDS="${WAIT_SECONDS:-90}"
DOCKER_CLEANUP="${DOCKER_CLEANUP:-1}"
USE_PREBUILT_IMAGES="${USE_PREBUILT_IMAGES:-1}"
ALLOW_LOCAL_BUILD="${ALLOW_LOCAL_BUILD:-auto}"
DOCKER_PRUNE_ALL_IMAGES="${DOCKER_PRUNE_ALL_IMAGES:-1}"

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

short_commit() {
  git rev-parse --short "$1"
}

commit_subject() {
  git log -1 --format=%s "$1"
}

require_cmd git
require_cmd docker

# Avoid Docker Buildx provenance/attestation hangs on small VPSes.
export COMPOSE_BAKE=false
export DOCKER_BUILDKIT=0
export COMPOSE_DOCKER_CLI_BUILD=0
export BUILDX_NO_DEFAULT_ATTESTATIONS=1
export BUILDKIT_PROGRESS=plain

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  fail "docker compose or docker-compose is required"
fi

[ -d "${PROJECT_DIR}" ] || fail "project dir not found: ${PROJECT_DIR}"

cd "${PROJECT_DIR}"
progress_init 7 "Cabinet update progress"

if [ ! -d .git ]; then
  fail "no git repository in ${PROJECT_DIR}"
fi

# Ignore executable-bit noise on Linux hosts so update commands don't fail
# after chmod fixes done by installer or admin.
git config core.filemode false || true

if [ -n "$(git status --porcelain)" ]; then
  if [ "${SAULINFO_KEEP_LOCAL_CHANGES:-0}" = "1" ]; then
    fail "working tree is dirty; commit or discard local changes before update"
  fi
  log "Working tree is dirty; saving local changes to git stash before update"
  git stash push -u -m "saulinfo auto-stash before cabinet update $(date -Iseconds)" >/dev/null
fi

progress_step "Fetch latest patches"
log "Fetching latest changes from origin/${BRANCH}"
git fetch origin "${BRANCH}"

LOCAL_COMMIT="$(git rev-parse HEAD)"
REMOTE_COMMIT="$(git rev-parse "origin/${BRANCH}")"
LOCAL_SHORT="$(short_commit "${LOCAL_COMMIT}")"
LOCAL_SUBJECT="$(commit_subject "${LOCAL_COMMIT}")"
REMOTE_SHORT="$(short_commit "${REMOTE_COMMIT}")"
REMOTE_SUBJECT="$(commit_subject "${REMOTE_COMMIT}")"
UPDATE_STATUS="NO CHANGES"

log "Current commit: ${LOCAL_SHORT} ${LOCAL_SUBJECT}"
log "Latest remote:  ${REMOTE_SHORT} ${REMOTE_SUBJECT}"

if [ "${LOCAL_COMMIT}" != "${REMOTE_COMMIT}" ]; then
  UPDATE_STATUS="UPDATED"
  progress_step "Apply patches"
  log "Applying new patches from origin/${BRANCH}"
  git merge --ff-only "origin/${BRANCH}"
else
  progress_step "Check patch state"
  log "Repository already up to date"
fi

FINAL_COMMIT="$(git rev-parse HEAD)"
FINAL_SHORT="$(short_commit "${FINAL_COMMIT}")"
FINAL_SUBJECT="$(commit_subject "${FINAL_COMMIT}")"

container_status() {
  local name="$1"
  if ! docker container inspect "${name}" >/dev/null 2>&1; then
    printf 'missing'
    return 0
  fi
  docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${name}" 2>/dev/null || printf 'unknown'
}

RECREATE_REASON="no changes"
SHOULD_RECREATE=0

if [ "${UPDATE_STATUS}" = "UPDATED" ]; then
  SHOULD_RECREATE=1
  RECREATE_REASON="git changed"
elif [ "${FORCE_REBUILD:-0}" = "1" ]; then
  SHOULD_RECREATE=1
  RECREATE_REASON="forced"
elif [ "${FORCE_REBUILD:-0}" = "auto" ]; then
  checked_status="$(container_status "${CONTAINER_NAME}")"
  if [ "${checked_status}" != "healthy" ] && [ "${checked_status}" != "running" ]; then
    SHOULD_RECREATE=1
    RECREATE_REASON="container ${CONTAINER_NAME} ${checked_status}"
  fi
fi

log "Recreate reason: ${RECREATE_REASON}"

if [ "${SHOULD_RECREATE}" = "1" ]; then
  if [ "${USE_PREBUILT_IMAGES}" = "1" ]; then
    progress_step "Pull prebuilt image and restart container (${RECREATE_REASON})"
    log "Pulling prebuilt image from registry"
    progress_note "Using prebuilt image first; local build starts automatically only if registry image is unavailable."
    if "${COMPOSE_CMD[@]}" pull && "${COMPOSE_CMD[@]}" up -d --no-build --remove-orphans; then
      :
    elif [ "${ALLOW_LOCAL_BUILD}" != "0" ]; then
      log "Prebuilt image unavailable or unusable; falling back to local build automatically"
      progress_note "Docker build output is shown below; provenance/Bake are disabled."
      "${COMPOSE_CMD[@]}" up -d --build --remove-orphans
    else
      fail "prebuilt image is unavailable or stale; local build is disabled by ALLOW_LOCAL_BUILD=0."
    fi
  else
    progress_step "Build and restart container (${RECREATE_REASON})"
    log "Rebuilding and restarting container"
    progress_note "Docker build output is shown below; provenance/Bake are disabled."
    "${COMPOSE_CMD[@]}" up -d --build --remove-orphans
  fi
else
  progress_step "Ensure container is running"
  log "No new patches; ensuring container is running without rebuild"
  "${COMPOSE_CMD[@]}" up -d --remove-orphans
fi

progress_step "Wait for container health"
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
  progress_step "Clean Docker cache"
  log "Pruning Docker builder cache"
  docker builder prune -af >/dev/null 2>&1 || true

  if [ "${DOCKER_PRUNE_ALL_IMAGES}" = "1" ]; then
    log "Pruning all unused Docker images"
    docker image prune -af >/dev/null 2>&1 || true
  else
    log "Pruning dangling Docker images"
    docker image prune -f >/dev/null 2>&1 || true
  fi
else
  progress_step "Skip Docker cleanup"
fi

progress_step "Print update result"
log "Patch result: ${UPDATE_STATUS}"
log "Before: ${LOCAL_SHORT} ${LOCAL_SUBJECT}"
log "After:  ${FINAL_SHORT} ${FINAL_SUBJECT}"
log "Recreate reason: ${RECREATE_REASON}"
if [ "${SHOULD_RECREATE}" = "1" ]; then
  log "Containers: RECREATED"
else
  log "Containers: SKIPPED RECREATE"
fi
log "Container ${CONTAINER_NAME}: ${STATUS}"
progress_step "Finish"
log "Update complete"
