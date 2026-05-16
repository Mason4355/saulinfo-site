#!/usr/bin/env bash

SAUL_PROGRESS_TOTAL="${SAUL_PROGRESS_TOTAL:-0}"
SAUL_PROGRESS_CURRENT="${SAUL_PROGRESS_CURRENT:-0}"
SAUL_PROGRESS_STARTED_AT="${SAUL_PROGRESS_STARTED_AT:-$(date +%s)}"

progress_init() {
  SAUL_PROGRESS_TOTAL="${1:-0}"
  SAUL_PROGRESS_CURRENT="0"
  SAUL_PROGRESS_STARTED_AT="$(date +%s)"
  if [ -n "${2:-}" ]; then
    echo
    echo "$2"
  fi
}

progress_bar() {
  local current="$1"
  local total="$2"
  local width=24
  local filled percent empty
  if [ "${total}" -le 0 ]; then
    total=1
  fi
  percent=$((current * 100 / total))
  filled=$((current * width / total))
  empty=$((width - filled))
  printf '['
  printf '%*s' "${filled}" '' | tr ' ' '#'
  printf '%*s' "${empty}" '' | tr ' ' '-'
  printf '] %3s%%' "${percent}"
}

progress_step() {
  local label="$1"
  local now elapsed
  SAUL_PROGRESS_CURRENT=$((SAUL_PROGRESS_CURRENT + 1))
  now="$(date +%s)"
  elapsed=$((now - SAUL_PROGRESS_STARTED_AT))
  printf '\n'
  progress_bar "${SAUL_PROGRESS_CURRENT}" "${SAUL_PROGRESS_TOTAL}"
  printf '  %s/%s  %s  (%ss)\n' "${SAUL_PROGRESS_CURRENT}" "${SAUL_PROGRESS_TOTAL}" "${label}" "${elapsed}"
}

progress_note() {
  printf '        -> %s\n' "$*"
}
