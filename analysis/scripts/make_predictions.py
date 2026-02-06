import argparse, json
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["A","B"], required=True)
    ap.add_argument("--runs_dir", default="ab_spec_study/runs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model_name", default="codex")
    args = ap.parse_args()

    base = Path(args.runs_dir) / args.mode
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for inst_dir in sorted(base.iterdir()):
        patch_path = inst_dir / "output" / "patch.diff"
        if not patch_path.exists():
            continue
        patch = patch_path.read_text(encoding="utf-8", errors="replace")
        # SWE-bench harness 期望字段：instance_id / model_patch / model_name_or_path
        rows.append({
            "instance_id": inst_dir.name,
            "model_name_or_path": args.model_name,
            "model_patch": patch,
        })

    with outp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[OK] wrote {len(rows)} predictions -> {outp}")

if __name__ == "__main__":
    main()
