import argparse, json, re, csv
from pathlib import Path

TOKEN_RE = re.compile(r"tokens used\\s*\\n\\s*([0-9,]+)", re.IGNORECASE)

def read_tokens(log_path: Path):
    if not log_path.exists():
        return None
    txt = log_path.read_text(encoding="utf-8", errors="replace")
    m = TOKEN_RE.search(txt)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))

def patch_stats(patch_path: Path):
    if not patch_path.exists():
        return {"patch_bytes": 0, "files_changed": 0, "add": 0, "del": 0}
    txt = patch_path.read_text(encoding="utf-8", errors="replace")
    files = sum(1 for line in txt.splitlines() if line.startswith("diff --git "))
    add = sum(1 for line in txt.splitlines() if line.startswith("+") and not line.startswith("+++"))
    dele = sum(1 for line in txt.splitlines() if line.startswith("-") and not line.startswith("---"))
    return {"patch_bytes": len(txt.encode("utf-8")), "files_changed": files, "add": add, "del": dele}

def read_passfail(report_json: Path, instance_id: str):
    # harness 的 report json 结构可能版本不同：尽量鲁棒
    if not report_json.exists():
        return None
    data = json.loads(report_json.read_text(encoding="utf-8", errors="replace"))
    # 常见两种：{"resolved": [...]} 或 {"instances": {"id": {"resolved": true}}}
    if isinstance(data, dict):
        if "resolved" in data and isinstance(data["resolved"], list):
            return instance_id in set(data["resolved"])
        if "instances" in data and isinstance(data["instances"], dict):
            inst = data["instances"].get(instance_id)
            if isinstance(inst, dict):
                return bool(inst.get("resolved"))
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["A","B"], required=True)
    ap.add_argument("--runs_dir", default="ab_spec_study/runs")
    ap.add_argument("--report_json", required=True)  # e.g. ab_spec_study/eval/reports/A_run/report.json
    ap.add_argument("--out_csv", default="ab_spec_study/results.csv")
    args = ap.parse_args()

    base = Path(args.runs_dir) / args.mode
    report = Path(args.report_json)
    out_csv = Path(args.out_csv)

    rows = []
    for inst_dir in sorted(base.iterdir()):
        out_dir = inst_dir / "output"
        meta_path = out_dir / "run_meta.json"
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))

        # 日志文件名：A 用 agent_log.txt；B 第二阶段你用了 fix_log.txt（你也可能仍叫 agent_log.txt）
        token_a = read_tokens(out_dir / "agent_log.txt")
        token_b = read_tokens(out_dir / "fix_log.txt")
        tokens = token_b if token_b is not None else token_a

        ps = patch_stats(out_dir / "patch.diff")
        passed = read_passfail(report, inst_dir.name)

        rows.append({
            "instance_id": inst_dir.name,
            "mode": args.mode,
            "fix_engine": meta.get("fix_engine"),
            "repo": meta.get("repo"),
            "base_commit": meta.get("base_commit"),
            "elapsed_sec": meta.get("elapsed_sec"),
            "tokens_used": tokens,
            **ps,
            "pass": passed,
        })

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)

    print(f"[OK] wrote {len(rows)} rows -> {out_csv}")

if __name__ == "__main__":
    main()
