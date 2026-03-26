#!/usr/bin/env python3
"""
Build SWE-bench predictions JSONL files for all modes (A, B).

Usage:
    python make_predictions_all.py \
        --runs_dir ab_spec_study/runs \
        --engine claude \
        --out_dir ab_spec_study/eval
"""
import argparse
import json
from pathlib import Path


MODES = ["A", "B"]  # C and D not yet run in this repo
# MODES = ["A", "B", "C", "D"]  # uncomment when C/D runs are available


def build_predictions(mode_dir: Path, engine: str, mode: str) -> list[dict]:
    rows = []
    if not mode_dir.exists():
        return rows
    for inst_dir in sorted(mode_dir.iterdir()):
        patch_path = inst_dir / "output" / "patch.diff"
        if not patch_path.exists():
            continue
        patch = patch_path.read_text(encoding="utf-8", errors="replace")
        rows.append({
            "instance_id": inst_dir.name,
            "model_name_or_path": f"{engine}_{mode}",
            "model_patch": patch,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs_dir", default="ab_spec_study/runs")
    ap.add_argument("--engine", required=True)
    ap.add_argument("--out_dir", default="ab_spec_study/eval")
    ap.add_argument("--modes", nargs="+", default=MODES,
                    help="Which modes to export (default: A B C D)")
    args = ap.parse_args()

    runs = Path(args.runs_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for mode in args.modes:
        engine_dir = runs / args.engine / mode
        rows = build_predictions(engine_dir, args.engine, mode)

        jsonl_path = out / f"{mode}_{args.engine}_predictions_all.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        print(f"[OK] mode={mode}  {len(rows)} predictions -> {jsonl_path}")


if __name__ == "__main__":
    main()
