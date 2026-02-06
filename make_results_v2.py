
import re, csv, json
from pathlib import Path
from datetime import datetime

ROOT = Path("ab_spec_study")
RUNS = ROOT / "runs"
EVAL_REPORTS = ROOT / "eval" / "reports"

def read_text(p: Path) -> str:
    try:
        return p.read_text(errors="ignore")
    except Exception:
        return ""

def count_patch_stats(patch_path: Path):
    txt = read_text(patch_path)
    files = set()
    add = rem = 0
    for line in txt.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            if m:
                files.add(m.group(2))
        elif line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            rem += 1
    return len(files), add, rem, len(txt.encode("utf-8"))

def extract_tokens(log_txt: str):
    m = re.search(r"tokens used\s*\n\s*([0-9,]+)", log_txt, re.IGNORECASE)
    if not m:
        m = re.search(r"tokens used\s*[:=]\s*([0-9,]+)", log_txt, re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None

def extract_session_id(log_txt: str):
    m = re.search(r"session id:\s*([0-9a-fA-F\-]+)", log_txt)
    return m.group(1) if m else None

def estimate_elapsed(out_dir: Path):
    candidates = [
        out_dir/"agent_log.txt",
        out_dir/"fix_log.txt",
        out_dir/"spec_gen_log.txt",
        out_dir/"spec.md",
        out_dir/"patch.diff",
    ]
    times = [p.stat().st_mtime_ns for p in candidates if p.exists()]
    if len(times) < 2:
        return None
    return (max(times) - min(times)) / 1e9  # float seconds

def parse_complexity(log_txt: str):
    # 稳定粗代理：不要用 DOTALL 贪婪匹配
    exec_count = len(re.findall(r"^\s*exec\s*$", log_txt, flags=re.MULTILINE))
    file_update_count = len(re.findall(r"^\s*file update", log_txt, flags=re.MULTILINE))
    apply_patch_count = len(re.findall(r"\bapply_patch\b", log_txt))

    rg_count = len(re.findall(r"\brg\b", log_txt))
    sed_count = len(re.findall(r"\bsed\b", log_txt))
    git_cmd_count = len(re.findall(r"\bgit\b", log_txt))

    return {
        "codex_exec_count": exec_count,
        "file_update_count": file_update_count,
        "apply_patch_count": apply_patch_count,
        "rg_count": rg_count,
        "sed_count": sed_count,
        "git_cmd_count": git_cmd_count,
    }

def harness_status(mode: str, instance_id: str):
    """
    Return: (True/False/None, source_path)
      - True  -> resolved
      - False -> unresolved
      - None  -> not found in any report
    """
    # 你现在明确存在的两个（最关键）
    primary = [Path("codex-A.A_codex_one.json")] if mode == "A" else [Path("codex-B.B_codex_one.json")]
    secondary = sorted(EVAL_REPORTS.rglob("*.json"))

    for p in primary + secondary:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        rid = data.get("resolved_ids")
        uid = data.get("unresolved_ids")

        if isinstance(rid, list) and instance_id in rid:
            return True, str(p)
        if isinstance(uid, list) and instance_id in uid:
            return False, str(p)

    return None, ""

rows = []
for mode in ["A", "B"]:
    mode_dir = RUNS / mode
    if not mode_dir.exists():
        continue

    for inst_dir in sorted(mode_dir.glob("sympy__sympy-*")):
        iid = inst_dir.name
        out = inst_dir / "output"
        patch = out / "patch.diff"
        if not patch.exists():
            continue

        log_candidates = [out/"agent_log.txt", out/"fix_log.txt"]
        log_path = next((p for p in log_candidates if p.exists()), None)
        log_txt = read_text(log_path) if log_path else ""

        files_changed, add, rem, patch_bytes = count_patch_stats(patch)
        tokens = extract_tokens(log_txt) if log_txt else None
        session = extract_session_id(log_txt) if log_txt else None
        elapsed_est = estimate_elapsed(out)
        cx = parse_complexity(log_txt) if log_txt else {}

        ok, report_path = harness_status(mode, iid)

        rows.append({
            "instance_id": iid,
            "mode": mode,
            "harness_resolved": ok,
            "harness_report": report_path,
            "elapsed_sec_est": elapsed_est,
            "tokens_used": tokens,
            "session_id": session,
            "files_touched_in_patch": files_changed,
            "patch_add_lines": add,
            "patch_del_lines": rem,
            "patch_bytes": patch_bytes,
            "codex_exec_count": cx.get("codex_exec_count"),
            "file_update_count": cx.get("file_update_count"),
            "apply_patch_count": cx.get("apply_patch_count"),
            "rg_count": cx.get("rg_count"),
            "sed_count": cx.get("sed_count"),
            "git_cmd_count": cx.get("git_cmd_count"),
            "log_file": str(log_path) if log_path else "",
            "patch_file": str(patch),
            "spec_file": str(out/"spec.md") if (out/"spec.md").exists() else "",
        })

out_csv = ROOT / "results_v2.csv"
out_csv.parent.mkdir(parents=True, exist_ok=True)

if rows:
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
else:
    out_csv.write_text("", encoding="utf-8")

def summarize(mode):
    ms = [r for r in rows if r["mode"] == mode]
    total = len(ms)
    resolved = sum(1 for r in ms if r["harness_resolved"] is True)
    unresolved = sum(1 for r in ms if r["harness_resolved"] is False)
    unknown = sum(1 for r in ms if r["harness_resolved"] is None)

    def avg(key):
        vals = [r.get(key) for r in ms if isinstance(r.get(key), (int, float)) and r.get(key) is not None]
        return (sum(vals)/len(vals)) if vals else None

    return {
        "total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "unknown": unknown,
        "avg_tokens": avg("tokens_used"),
        "avg_elapsed_sec_est": avg("elapsed_sec_est"),
        "avg_patch_bytes": avg("patch_bytes"),
        "avg_exec": avg("codex_exec_count"),
        "avg_rg": avg("rg_count"),
        "avg_file_update": avg("file_update_count"),
    }

A = summarize("A")
B = summarize("B")

report = ROOT / "report_v2.md"
report.write_text(f"""# A/B Report (auto-generated, v2)

Generated at: {datetime.now().isoformat(timespec="seconds")}

## Summary
### A
- total: {A["total"]}
- resolved: {A["resolved"]}, unresolved: {A["unresolved"]}, unknown: {A["unknown"]}

### B
- total: {B["total"]}
- resolved: {B["resolved"]}, unresolved: {B["unresolved"]}, unknown: {B["unknown"]}

## Cost / time / complexity (proxy)
- avg tokens: A={A["avg_tokens"] if A["avg_tokens"] is not None else "N/A"} | B={B["avg_tokens"] if B["avg_tokens"] is not None else "N/A"}
- avg elapsed_sec_est (mtime-based): A={A["avg_elapsed_sec_est"] if A["avg_elapsed_sec_est"] is not None else "N/A"} | B={B["avg_elapsed_sec_est"] if B["avg_elapsed_sec_est"] is not None else "N/A"}
- avg patch bytes: A={A["avg_patch_bytes"] if A["avg_patch_bytes"] is not None else "N/A"} | B={B["avg_patch_bytes"] if B["avg_patch_bytes"] is not None else "N/A"}
- avg codex exec count: A={A["avg_exec"] if A["avg_exec"] is not None else "N/A"} | B={B["avg_exec"] if B["avg_exec"] is not None else "N/A"}
- avg rg count (search proxy): A={A["avg_rg"] if A["avg_rg"] is not None else "N/A"} | B={B["avg_rg"] if B["avg_rg"] is not None else "N/A"}
- avg file update count: A={A["avg_file_update"] if A["avg_file_update"] is not None else "N/A"} | B={B["avg_file_update"] if B["avg_file_update"] is not None else "N/A"}

## Per-instance table
See `results_v2.csv`.
""", encoding="utf-8")

print("[OK] wrote:", out_csv)
print("[OK] wrote:", report)

