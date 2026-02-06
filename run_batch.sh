#!/usr/bin/env bash
set -euo pipefail

# Usage:
# FIX_ENGINE=codex ./ab_spec_study/run_batch.sh ab_spec_study/instance_ids_50.txt
LIST="${1:?need instance list file}"

ROOT="$(pwd)"
FIX_ENGINE="${FIX_ENGINE:-codex}"
RETRY="${RETRY:-3}"                 # max attempts per (mode,id)
RETRY_SLEEP_BASE="${RETRY_SLEEP_BASE:-2}"  # base seconds for backoff

# -------------------------
# hard-coded skips (optional)
# -------------------------
SKIP_BOTH_IDS=()
SKIP_A_ONLY_IDS=()

in_list() {
  local needle="$1"; shift
  local x
  for x in "$@"; do
    [[ "$x" == "$needle" ]] && return 0
  done
  return 1
}

already_done() {
  local mode="$1"
  local id="$2"
  local patch="$ROOT/ab_spec_study/runs/$FIX_ENGINE/$mode/$id/output/patch.diff"
  [[ -s "$patch" ]]
}

read_ids() {
  # ignore blanks + comments
  grep -v '^[[:space:]]*$' "$LIST" | grep -v '^[[:space:]]*#'
}

run_one_once() {
  local mode="$1"
  local id="$2"

  # hard skips
  if in_list "$id" "${SKIP_BOTH_IDS[@]}"; then
    echo "[SKIP] [$mode] $id (hard skip both)"
    return 0
  fi
  if [[ "$mode" == "A" ]] && in_list "$id" "${SKIP_A_ONLY_IDS[@]}"; then
    echo "[SKIP] [A] $id (hard skip A-only)"
    return 0
  fi

  # auto skip
  if already_done "$mode" "$id"; then
    echo "[SKIP] [$mode] $id (already has non-empty patch.diff)"
    return 0
  fi

  echo "==== [$mode] $id ===="
  FIX_ENGINE="$FIX_ENGINE" ./ab_spec_study/run_one.sh "$mode" "$id"
}

run_one_with_retry() {
  local mode="$1"
  local id="$2"
  local attempt=1

  while (( attempt <= RETRY )); do
    if run_one_once "$mode" "$id"; then
      return 0
    fi

    if already_done "$mode" "$id"; then
      echo "[OK] [$mode] $id produced patch.diff despite nonzero exit; treating as done"
      return 0
    fi

    if (( attempt == RETRY )); then
      echo "[FAIL] [$mode] $id failed after $RETRY attempts"
      return 1
    fi

    local sleep_s=$(( RETRY_SLEEP_BASE * attempt ))
    echo "[WARN] [$mode] $id failed (attempt $attempt/$RETRY). retrying in ${sleep_s}s..."
    sleep "$sleep_s"
    ((attempt++))
  done
}

run_phase() {
  local mode="$1"
  local fail=0

  echo "[INFO] Phase $mode: loading instance IDs"

  # ⭐⭐⭐ 关键修复点：使用 FD 3，彻底隔离 stdin ⭐⭐⭐
  exec 3< <(read_ids)

  while IFS= read -r ID <&3; do
    [[ -z "$ID" ]] && continue
    echo "[INFO] [$mode] running $ID"
    if ! run_one_with_retry "$mode" "$ID"; then
      # 不 break：继续跑后面的，保证“续跑且不重跑”
      fail=1
    fi
  done

  exec 3<&-
  return "$fail"
}

A_FAIL=0
B_FAIL=0

echo "[INFO] Starting phase A (FIX_ENGINE=$FIX_ENGINE, RETRY=$RETRY)"
if ! run_phase "A"; then A_FAIL=1; fi

echo "[INFO] Starting phase B (FIX_ENGINE=$FIX_ENGINE, RETRY=$RETRY)"
if ! run_phase "B"; then B_FAIL=1; fi

if [[ "$A_FAIL" -eq 1 || "$B_FAIL" -eq 1 ]]; then
  echo "[DONE] batch finished with some failures (see logs under ab_spec_study/runs/*/*/output/)"
  exit 0
fi

echo "[DONE] batch finished successfully"
