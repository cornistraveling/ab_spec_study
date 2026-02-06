#!/usr/bin/env python3
"""
Export a single SWE-bench instance (Verified) into a local folder:
- issue.md
- meta.json
- gold.patch.diff (if available)
"""

import argparse
import json
import os
from datasets import load_dataset


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="SWE-bench/SWE-bench_Verified")
    parser.add_argument("--split", default="test")
    parser.add_argument("--instance_id", required=True)
    parser.add_argument("--out_dir", required=True)
    args = parser.parse_args()

    ds = load_dataset(args.dataset, split=args.split)

    row = None
    for item in ds:
        if item.get("instance_id") == args.instance_id:
            row = item
            break

    if row is None:
        raise SystemExit(f"[ERROR] instance_id not found: {args.instance_id}")

    os.makedirs(args.out_dir, exist_ok=True)

    # -------- issue.md --------
    issue_md = f"""# Instance ID
{row['instance_id']}

# Repository
{row['repo']}

# Base Commit
{row['base_commit']}

# Problem Statement
{row['problem_statement']}
"""
    with open(os.path.join(args.out_dir, "issue.md"), "w", encoding="utf-8") as f:
        f.write(issue_md)

    # -------- meta.json --------
    meta = {
        "instance_id": row["instance_id"],
        "repo": row["repo"],
        "base_commit": row["base_commit"],
    }
    with open(os.path.join(args.out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # -------- gold patch (optional) --------
    gold_patch = row.get("patch", "")
    if gold_patch:
        with open(os.path.join(args.out_dir, "gold.patch.diff"), "w", encoding="utf-8") as f:
            f.write(gold_patch)

    print(f"[OK] Exported {args.instance_id} -> {args.out_dir}")


if __name__ == "__main__":
    main()
