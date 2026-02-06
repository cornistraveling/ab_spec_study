#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?need mode A or B}"
INSTANCE_ID="${2:?need instance_id}"

# choose which engine to use: claude|codex|gemini
FIX_ENGINE="${FIX_ENGINE:-codex}"

ROOT="$(pwd)"
DATASET="SWE-bench/SWE-bench_Verified"
SPLIT="test"

WS_DIR="$ROOT/ab_spec_study/workspaces/$INSTANCE_ID"
# Include engine in path so different agents store results separately
RUN_DIR="$ROOT/ab_spec_study/runs/$FIX_ENGINE/$MODE/$INSTANCE_ID"
IN_DIR="$RUN_DIR/input"
OUT_DIR="$RUN_DIR/output"

mkdir -p "$IN_DIR" "$OUT_DIR"

echo "[INFO] instance=$INSTANCE_ID mode=$MODE fix_engine=$FIX_ENGINE"

# -------------------------
# Repo mirror cache
# -------------------------
CACHE_DIR="$ROOT/ab_spec_study/repo_cache"
mkdir -p "$CACHE_DIR"

repo_to_mirror_path() {
  echo "$CACHE_DIR/$(echo "$1" | tr '/' '__').git"
}

update_mirror() {
  local repo="$1"
  local mirror
  mirror="$(repo_to_mirror_path "$repo")"

  if [[ ! -d "$mirror" ]]; then
    echo "[INFO] creating mirror for $repo (blobless clone)"
    # Use blobless clone - much faster, blobs fetched on-demand
    if ! git -c http.version=HTTP/1.1 clone --mirror --filter=blob:none \
         "https://github.com/$repo.git" "$mirror" 2>/dev/null; then
      echo "[INFO] blobless not supported, using full mirror"
      git -c http.version=HTTP/1.1 clone --mirror "https://github.com/$repo.git" "$mirror"
    fi
  else
    echo "[INFO] updating mirror for $repo"
    git -C "$mirror" -c http.version=HTTP/1.1 remote update --prune || true
  fi
}

clone_from_mirror() {
  local repo="$1"
  local dst="$2"
  local mirror
  mirror="$(repo_to_mirror_path "$repo")"

  rm -rf "$dst"
  git clone --quiet "$mirror" "$dst"
}

# -------- NEW: gh clone --------
gh_clone() {
  local repo="$1"
  local dst="$2"

  echo "[INFO] gh repo clone $repo"
  rm -rf "$dst"

  if gh repo clone "$repo" "$dst" -- --quiet; then
    return 0
  fi

  echo "[WARN] gh repo clone failed: $repo"
  return 1
}

direct_clone_with_retries() {
  local repo="$1"
  local dst="$2"
  local tries="${RETRY_CLONE:-5}"
  local i=1

  export GIT_TERMINAL_PROMPT=0

  while (( i <= tries )); do
    echo "[INFO] direct clone attempt $i/$tries: $repo (blobless)"
    rm -rf "$dst"
    # Use blobless clone for much faster downloads
    if git -c http.version=HTTP/1.1 \
           -c http.postBuffer=524288000 \
           -c http.lowSpeedLimit=0 \
           -c http.lowSpeedTime=999999 \
           clone --filter=blob:none --quiet "https://github.com/${repo}.git" "$dst" 2>/dev/null; then
      return 0
    fi
    # Fallback to regular clone if blobless fails
    echo "[INFO] trying full clone for $repo"
    if git -c http.version=HTTP/1.1 \
           -c http.postBuffer=524288000 \
           -c http.lowSpeedLimit=0 \
           -c http.lowSpeedTime=999999 \
           clone --quiet "https://github.com/${repo}.git" "$dst"; then
      return 0
    fi
    echo "[WARN] direct clone failed (attempt $i). sleeping..."
    sleep $(( i * 2 ))
    ((i++))
  done

  echo "[ERROR] direct clone failed after $tries attempts: $repo" >&2
  return 1
}

# -------------------------
# Engines
# -------------------------
run_fix() {
  local prompt="$1"
  local log_file="${2:-agent_log.txt}"

  if [[ "$FIX_ENGINE" == "claude" ]]; then
    # Use --dangerously-skip-permissions for automated runs
    stdbuf -oL -eL claude --dangerously-skip-permissions -p "$prompt" 2>&1 | tee "$OUT_DIR/$log_file"
  elif [[ "$FIX_ENGINE" == "codex" ]]; then
    stdbuf -oL -eL codex exec --full-auto --sandbox workspace-write -C "$(pwd)" "$prompt" 2>&1 \
      | tee "$OUT_DIR/$log_file"
  elif [[ "$FIX_ENGINE" == "gemini" ]]; then
    stdbuf -oL -eL gemini -p "$prompt" 2>&1 | tee "$OUT_DIR/$log_file"
  else
    echo "[ERROR] unknown FIX_ENGINE=$FIX_ENGINE" >&2
    exit 2
  fi
}

generate_spec() {
  local issue_md="$1"

  echo "[INFO] generating spec via $FIX_ENGINE"

  if [[ "$FIX_ENGINE" == "codex" ]]; then
    stdbuf -oL -eL codex exec --sandbox read-only -C "$(pwd)" \
      "Write a technical specification in Markdown with sections: Summary, Root Cause, Expected Behavior, Files to Modify, Implementation Plan, Test Plan.

$issue_md

IMPORTANT: Output ONLY the Markdown spec." \
      2>&1 | tee "$OUT_DIR/spec_gen_log.txt"

    awk 'BEGIN{p=0} /^##[[:space:]]+Summary/ {p=1} {if(p) print}' \
      "$OUT_DIR/spec_gen_log.txt" > "$OUT_DIR/spec.md"

    if [[ ! -s "$OUT_DIR/spec.md" ]]; then
      awk 'BEGIN{p=0} /^#[[:space:]]+Summary/ {p=1} {if(p) print}' \
        "$OUT_DIR/spec_gen_log.txt" > "$OUT_DIR/spec.md"
    fi

    [[ -s "$OUT_DIR/spec.md" ]] || {
      echo "[ERROR] Failed to extract markdown spec" >&2
      return 3
    }
  elif [[ "$FIX_ENGINE" == "claude" ]]; then
    stdbuf -oL -eL claude --dangerously-skip-permissions -p \
      "Write a technical specification in Markdown with sections: Summary, Root Cause, Expected Behavior, Files to Modify, Implementation Plan, Test Plan.

$issue_md

IMPORTANT: Output ONLY the Markdown spec." \
      2>&1 | tee "$OUT_DIR/spec_gen_log.txt" > "$OUT_DIR/spec.md"
  else
    stdbuf -oL -eL "$FIX_ENGINE" -p \
      "Write a technical specification in Markdown with sections: Summary, Root Cause, Expected Behavior, Files to Modify, Implementation Plan, Test Plan.

$issue_md

IMPORTANT: Output ONLY the Markdown spec." \
      2>&1 | tee "$OUT_DIR/spec_gen_log.txt" > "$OUT_DIR/spec.md"
  fi
}

# -------------------------
# Export instance
# -------------------------
python "$ROOT/ab_spec_study/analysis/scripts/export_instance.py" \
  --dataset "$DATASET" \
  --split "$SPLIT" \
  --instance_id "$INSTANCE_ID" \
  --out_dir "$IN_DIR"

REPO="$(python - <<PY
import json
print(json.load(open("$IN_DIR/meta.json"))["repo"])
PY
)"

BASE_COMMIT="$(python - <<PY
import json
print(json.load(open("$IN_DIR/meta.json"))["base_commit"])
PY
)"

echo "[INFO] repo=$REPO base_commit=$BASE_COMMIT"
ISSUE_MD="$(cat "$IN_DIR/issue.md")"

# -------------------------
# Prepare workspace
# -------------------------
rm -rf "$WS_DIR"
mkdir -p "$WS_DIR"

mirror="$(repo_to_mirror_path "$REPO")"

if [[ -d "$mirror" ]]; then
  echo "[INFO] using existing mirror for $REPO"
  update_mirror "$REPO" || echo "[WARN] mirror update failed"
  clone_from_mirror "$REPO" "$WS_DIR/repo"
else
  echo "[INFO] no mirror found for $REPO"
  if gh_clone "$REPO" "$WS_DIR/repo"; then
    :
  elif update_mirror "$REPO"; then
    clone_from_mirror "$REPO" "$WS_DIR/repo"
  else
    echo "[WARN] gh + mirror failed; falling back to direct git clone"
    direct_clone_with_retries "$REPO" "$WS_DIR/repo"
  fi
fi

cd "$WS_DIR/repo"
git checkout --quiet "$BASE_COMMIT"

# -------------------------
# Run (A/B)
# -------------------------
START_TS_NS="$(date +%s%N)"
STATUS="ok"

write_meta() {
  local end_ns
  end_ns="$(date +%s%N)"

  python - <<PY
import json
from pathlib import Path

out = Path("$OUT_DIR")
out.mkdir(parents=True, exist_ok=True)

meta = {
  "instance_id": "$INSTANCE_ID",
  "mode": "$MODE",
  "fix_engine": "$FIX_ENGINE",
  "repo": "$REPO",
  "base_commit": "$BASE_COMMIT",
  "status": "$STATUS",
  "start_ts_ns": int("$START_TS_NS"),
  "end_ts_ns": int("$end_ns"),
  "elapsed_sec": (int("$end_ns") - int("$START_TS_NS")) / 1e9,
}
(out / "run_meta.json").write_text(json.dumps(meta, indent=2))
PY
}

trap 'STATUS="fail"; write_meta' ERR
trap 'write_meta' EXIT

echo "[INFO] applying fix via $FIX_ENGINE"

if [[ "$MODE" == "A" ]]; then
  run_fix "Fix the following issue in the current repository. Make code changes directly.

$ISSUE_MD"
else
  generate_spec "$ISSUE_MD"
  SPEC_TEXT="$(head -c 200000 "$OUT_DIR/spec.md")"
  run_fix "Fix the issue according to the following specification. Make code changes directly.

Issue:
$ISSUE_MD

Specification:
$SPEC_TEXT" "fix_log.txt"
fi

git diff > "$OUT_DIR/patch.diff"

echo "[DONE] outputs in $RUN_DIR"
