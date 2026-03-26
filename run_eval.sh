#!/usr/bin/env bash
# Run SWE-bench harness evaluation for all modes.
#
# Usage (from SWE-bench/ root):
#   ENGINE=claude ./ab_spec_study/run_eval.sh
#   ENGINE=claude MODES="A B" ./ab_spec_study/run_eval.sh
#   ENGINE=claude MODES="A" MAX_WORKERS=4 ./ab_spec_study/run_eval.sh
#
# Outputs per mode:
#   ab_spec_study/eval/{MODE}_{ENGINE}_predictions_all.jsonl
#   ab_spec_study/eval/reports/{MODE}_{ENGINE}_all/
set -euo pipefail

ENGINE="${ENGINE:-claude}"
MODES="${MODES:-A B}"
MAX_WORKERS="${MAX_WORKERS:-1}"
INSTANCE_IDS_FILE="ab_spec_study/instance_ids_all.txt"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/ab_spec_study/../.venv/bin/activate"
[[ -f "$VENV" ]] && source "$VENV"

echo "[eval] ENGINE=$ENGINE  MODES=$MODES  MAX_WORKERS=$MAX_WORKERS"

# Step 1: build prediction JSONL files
python ab_spec_study/analysis/scripts/make_predictions_all.py \
    --runs_dir ab_spec_study/runs \
    --engine   "$ENGINE" \
    --out_dir  ab_spec_study/eval \
    --modes    $MODES

# Step 2: run harness per mode
for MODE in $MODES; do
    PRED="ab_spec_study/eval/${MODE}_${ENGINE}_predictions_all.jsonl"
    RUN_ID="${MODE}_${ENGINE}_all"
    REPORT_DIR="ab_spec_study/eval/reports/${RUN_ID}"

    if [[ ! -f "$PRED" ]]; then
        echo "[eval][WARN] predictions file not found: $PRED — skipping mode $MODE"
        continue
    fi

    echo "[eval] running harness: mode=$MODE  run_id=$RUN_ID"
    python -m swebench.harness.run_evaluation \
        --dataset_name SWE-bench/SWE-bench_Verified \
        --split        test \
        --predictions_path "$PRED" \
        --instance_ids $(paste -sd, "$INSTANCE_IDS_FILE") \
        --max_workers  "$MAX_WORKERS" \
        --run_id       "$RUN_ID" \
        --report_dir   "$REPORT_DIR"

    echo "[eval] done: $REPORT_DIR"
done

echo "[eval] all modes finished"
