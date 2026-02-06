#!/usr/bin/env bash
set -euo pipefail

# Pre-seed all repo mirrors for faster batch runs
# Usage: ./ab_spec_study/preseed_mirrors.sh [instance_list.txt]

LIST="${1:-ab_spec_study/instance_ids_50.txt}"
ROOT="$(pwd)"
CACHE_DIR="$ROOT/ab_spec_study/repo_cache"

mkdir -p "$CACHE_DIR"

# Extract unique repos from instance IDs
# Format: OWNER__REPO-ISSUE_NUMBER -> OWNER/REPO
get_unique_repos() {
  grep -v '^[[:space:]]*$' "$LIST" | grep -v '^#' | \
    sed 's/-[0-9]*$//' | sed 's/__/\//' | sort -u
}

repo_to_mirror_path() {
  # Match run_one.sh: tr '/' '__' actually produces single underscore (tr is char-to-char)
  echo "$CACHE_DIR/$(echo "$1" | tr '/' '_').git"
}

clone_mirror() {
  local repo="$1"
  local mirror
  mirror="$(repo_to_mirror_path "$repo")"

  if [[ -d "$mirror" ]]; then
    echo "[SKIP] mirror exists: $repo"
    return 0
  fi

  echo "[CLONE] creating mirror for $repo ..."

  # Use blobless clone for faster download - still gets all commits/branches
  # but blobs are fetched on-demand
  if git clone --mirror --filter=blob:none \
       -c http.version=HTTP/1.1 \
       -c http.postBuffer=524288000 \
       "https://github.com/$repo.git" "$mirror" 2>/dev/null; then
    echo "[OK] $repo"
    return 0
  fi

  # Fallback to regular mirror if partial clone not supported
  echo "[RETRY] falling back to full mirror for $repo"
  git clone --mirror \
    -c http.version=HTTP/1.1 \
    -c http.postBuffer=524288000 \
    "https://github.com/$repo.git" "$mirror"
}

echo "=== Pre-seeding repo mirrors ==="
echo "Instance list: $LIST"
echo "Cache dir: $CACHE_DIR"
echo ""

REPOS=$(get_unique_repos)
TOTAL=$(echo "$REPOS" | wc -l)
COUNT=0

for repo in $REPOS; do
  COUNT=$((COUNT + 1))
  echo "[$COUNT/$TOTAL] $repo"
  clone_mirror "$repo" || echo "[WARN] failed to clone $repo"
done

echo ""
echo "=== Done ==="
ls -lh "$CACHE_DIR"
