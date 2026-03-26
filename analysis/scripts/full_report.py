#!/usr/bin/env python3
"""
Comprehensive analysis and report generator for the A/B/C/D spec experiment.

Computes per-run:
  - SWE-bench harness result (resolved / unresolved / error / empty)
  - Patch statistics (files, add/del lines, bytes)
  - Code-quality metrics on changed Python code:
      * Cyclomatic Complexity (radon cc)
      * Maintainability Index  (radon mi)
      * Halstead volume        (radon hal)
      * Raw LOC metrics        (radon raw)
      * Lizard complexity      (lizard)
      * Comment ratio          (comment_lines / total_lines in patch)
      * Depth of Inheritance Tree (DIT, computed via AST)
      * Cyclomatic Complexity per function (radon + lizard)
  - Error classification with subtypes
  - Timing and token usage

Usage (called by run_full_pipeline.sh):
    python full_report.py \
        --runs_dir       ab_spec_study/runs \
        --engine         claude \
        --eval_dir       ab_spec_study/eval \
        --out_dir        ab_spec_study \
        --modes A B
"""

import argparse
import ast
import csv
import io
import json
import re
import sys
import textwrap
from datetime import datetime
from pathlib import Path

# ── optional heavy deps ──────────────────────────────────────────────────────
try:
    from radon.complexity import cc_visit, average_complexity
    from radon.metrics import mi_visit, h_visit
    from radon.raw import analyze as raw_analyze
    HAS_RADON = True
except ImportError:
    HAS_RADON = False

try:
    import lizard
    HAS_LIZARD = True
except ImportError:
    HAS_LIZARD = False

try:
    from cognitive_complexity.api import get_cognitive_complexity
    HAS_COGNITIVE = True
except ImportError:
    HAS_COGNITIVE = False

HAS_TEXTSTAT = False  # prose readability metrics removed (not suitable for code)

# ─────────────────────────────────────────────────────────────────────────────
MODES = ["A", "B"]  # C and D not yet run in this repo
# MODES = ["A", "B", "C", "D"]  # uncomment when C/D runs are available
MODE_LABELS = {
    "A": "No spec",
    "B": "Simple spec",
    # "C": "OpenSpec",   # not yet run in this repo
    # "D": "spec-kit",   # not yet run in this repo
}

# ══════════════════════════════════════════════════════════════════════════════
# Patch parsing
# ══════════════════════════════════════════════════════════════════════════════

def parse_patch(patch_text: str) -> dict:
    """Return basic stats from a unified diff."""
    files, added, removed = set(), 0, 0
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            if m:
                files.add(m.group(2))
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {
        "files_changed": len(files),
        "lines_added": added,
        "lines_removed": removed,
        "patch_bytes": len(patch_text.encode()),
        "is_empty": len(patch_text.strip()) == 0,
    }


def extract_added_python(patch_text: str) -> dict[str, str]:
    """
    Return {filename: added_source_code} for Python files in the diff.
    Only the added (+) lines are returned — useful for CC / comment metrics.
    """
    result: dict[str, list[str]] = {}
    cur_file = None
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/.+ b/(.+)$", line)
            if m and m.group(1).endswith(".py"):
                cur_file = m.group(1)
                result.setdefault(cur_file, [])
        elif cur_file and line.startswith("+") and not line.startswith("+++"):
            result[cur_file].append(line[1:])
    return {k: "\n".join(v) for k, v in result.items() if v}


# ══════════════════════════════════════════════════════════════════════════════
# Code quality metrics
# ══════════════════════════════════════════════════════════════════════════════

def comment_ratio(source: str) -> float:
    """Fraction of non-blank lines that are comment/docstring lines."""
    lines = source.splitlines()
    total = sum(1 for l in lines if l.strip())
    if total == 0:
        return 0.0
    comment_lines = sum(
        1 for l in lines
        if l.strip().startswith("#") or l.strip().startswith('"""') or l.strip().startswith("'''")
    )
    return round(comment_lines / total, 4)


def extract_docstrings(source: str) -> str:
    """Pull all docstring/comment text from source for readability scoring."""
    fragments = []
    # inline comments
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            fragments.append(stripped[1:].strip())
    # string literals (docstrings) via AST
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    fragments.append(node.value.value)
    except SyntaxError:
        pass
    return " ".join(fragments)


def radon_metrics(source: str) -> dict:
    if not HAS_RADON or not source.strip():
        return {}
    out = {}
    # Cyclomatic Complexity
    try:
        blocks = cc_visit(source)
        ccs = [b.complexity for b in blocks]
        out["cc_avg"] = round(sum(ccs) / len(ccs), 2) if ccs else 0.0
        out["cc_max"] = max(ccs) if ccs else 0
        out["cc_n_funcs"] = len(ccs)
    except Exception:
        pass
    # Maintainability Index
    try:
        mi = mi_visit(source, multi=True)
        out["maintainability_index"] = round(mi, 2)
    except Exception:
        pass
    # Halstead
    try:
        h = h_visit(source)
        if h:
            report = h[0]
            out["halstead_volume"] = round(report.volume, 2)
            out["halstead_difficulty"] = round(report.difficulty, 2)
            out["halstead_effort"] = round(report.effort, 2)
    except Exception:
        pass
    # Raw LOC
    try:
        raw = raw_analyze(source)
        out["loc"] = raw.loc
        out["lloc"] = raw.lloc
        out["sloc"] = raw.sloc
        out["comments"] = raw.comments
        out["multi"] = raw.multi
        out["blank"] = raw.blank
        if raw.loc > 0:
            out["comment_ratio_radon"] = round((raw.comments + raw.multi) / raw.loc, 4)
    except Exception:
        pass
    return out


def lizard_metrics(source: str, filename: str = "patch.py") -> dict:
    if not HAS_LIZARD or not source.strip():
        return {}
    try:
        result = lizard.analyze_file.analyze_source_code(filename, source)
        fns = result.function_list
        if not fns:
            return {}
        ccs = [f.cyclomatic_complexity for f in fns]
        tok = [f.token_count for f in fns]
        return {
            "lizard_cc_avg": round(sum(ccs) / len(ccs), 2),
            "lizard_cc_max": max(ccs),
            "lizard_avg_tokens": round(sum(tok) / len(tok), 1),
            "lizard_n_funcs": len(fns),
        }
    except Exception:
        return {}


def compute_dit(source: str) -> dict:
    """Depth of Inheritance Tree (DIT) for classes in the patched source.

    DIT = number of ancestor classes up the inheritance chain.
    For a single file we approximate by counting base classes per class
    and resolving intra-file chains via a name→depth map.
    Lower DIT → shallower hierarchy → simpler design (↓ desired).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    # Build name → list-of-base-names map
    class_bases: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                if isinstance(b, ast.Name):
                    bases.append(b.id)
                elif isinstance(b, ast.Attribute):
                    bases.append(b.attr)
            class_bases[node.name] = bases

    # Compute depth recursively (cap at 10 to avoid infinite loops)
    cache: dict[str, int] = {}

    def depth(name: str, visited: set) -> int:
        if name in cache:
            return cache[name]
        if name not in class_bases or name in visited:
            return 0
        visited = visited | {name}
        d = 1 + max((depth(b, visited) for b in class_bases[name]), default=0)
        cache[name] = d
        return d

    depths = [depth(n, set()) for n in class_bases]
    if not depths:
        return {"dit_avg": 0.0, "dit_max": 0, "n_classes": 0}

    return {
        "dit_avg": round(sum(depths) / len(depths), 2),
        "dit_max": max(depths),
        "n_classes": len(depths),
    }


def compute_cognitive_complexity(source: str) -> dict:
    """Cognitive Complexity (SonarSource) for all functions in patched source.

    Unlike Cyclomatic Complexity, Cognitive Complexity penalises nesting depth
    and non-linear control flow (break/continue/recursion), giving a better
    measure of how hard the code is to understand.
    Lower is better (↓ desired).
    Returns cog_avg, cog_max, cog_total across all top-level functions/methods.
    """
    if not HAS_COGNITIVE:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    scores = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                scores.append(get_cognitive_complexity(node))
            except Exception:
                pass

    if not scores:
        return {"cog_avg": 0.0, "cog_max": 0, "cog_total": 0, "n_functions": 0}

    return {
        "cog_avg": round(sum(scores) / len(scores), 2),
        "cog_max": max(scores),
        "cog_total": sum(scores),
        "n_functions": len(scores),
    }


def compute_code_metrics(patch_text: str) -> dict:
    """Aggregate code-quality metrics across all changed Python files."""
    py_files = extract_added_python(patch_text)
    if not py_files:
        return {}

    combined = "\n".join(py_files.values())

    metrics: dict = {}

    # comment ratio (simple line-based)
    metrics["comment_ratio"] = comment_ratio(combined)

    # radon (CC, MI, Halstead, raw)
    metrics.update(radon_metrics(combined))

    # lizard (independent CC)
    metrics.update(lizard_metrics(combined))

    # Depth of Inheritance Tree (DIT)
    metrics.update(compute_dit(combined))

    # Cognitive Complexity (SonarSource)
    metrics.update(compute_cognitive_complexity(combined))

    return metrics


# ══════════════════════════════════════════════════════════════════════════════
# Log parsing
# ══════════════════════════════════════════════════════════════════════════════

def read_text(path: Path) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_tokens(log: str) -> int | None:
    for pat in [
        r"tokens used\s*\n\s*([0-9,]+)",
        r"tokens used\s*[:=]\s*([0-9,]+)",
        r"input_tokens\s*[:=]\s*([0-9,]+)",
        r'"input_tokens":\s*([0-9]+)',
    ]:
        m = re.search(pat, log, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def agent_action_counts(log: str) -> dict:
    return {
        "exec_count":         len(re.findall(r"^\s*exec\s*$", log, re.MULTILINE)),
        "file_edit_count":    len(re.findall(r"file update|FileEdit|Edit\b", log, re.IGNORECASE)),
        "apply_patch_count":  len(re.findall(r"\bapply_patch\b", log)),
        "search_count":       len(re.findall(r"\brg\b|\bgrep\b|\bGlob\b|\bGrep\b", log)),
        "git_cmd_count":      len(re.findall(r"\bgit\b", log)),
        "tool_call_count":    len(re.findall(r"<tool_use>|Tool call:|Calling tool", log, re.IGNORECASE)),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Error classification
# ══════════════════════════════════════════════════════════════════════════════

#
# Taxonomy:
#   understanding_error
#     misinterpreted_issue       – agent addressed wrong problem
#     incomplete_understanding   – addressed part of issue only
#     wrong_scope                – touched unrelated files/functions
#     missing_context            – needed info not in issue
#
#   patch_generation_error
#     empty_patch                – no diff produced at all
#     syntax_error               – patch contains Python syntax errors
#     wrong_file_modified        – modified file unrelated to issue
#     incomplete_patch           – patch cuts off / malformed
#     conflicting_changes        – patch conflicts with repo state
#
#   verification_error
#     test_failure               – harness ran but tests failed
#     regression_introduced      – new failures introduced
#     partial_fix                – some tests pass, others fail
#
#   process_error
#     api_error                  – upstream API unavailable / quota
#     timeout                    – run exceeded time limit
#     clone_failure              – couldn't clone/checkout repo
#     tool_execution_failure     – shell tool returned non-zero
#     spec_generation_failure    – spec gen step failed (B/C/D)
#
#   none                         – resolved successfully
#   unknown                      – not enough information
#

_PROCESS_PATTERNS: list[tuple[str, str]] = [
    (r"402 Payment Required|deactivated_workspace|quota exceeded|rate.?limit", "api_error"),
    (r"timed? ?out|SIGKILL|killed after|exceeded.*timeout", "timeout"),
    (r"fatal:.*clone|git clone.*error|could not clone|Failed to clone", "clone_failure"),
    (r"Traceback \(most recent\)|Error:.*\n.*at line|subprocess.*exited.*[1-9]", "tool_execution_failure"),
    (r"spec_gen.*failed|spec generation error|\[ERROR\].*spec", "spec_generation_failure"),
]

_PATCH_PATTERNS: list[tuple[str, str]] = [
    (r"SyntaxError|invalid syntax", "syntax_error"),
    (r"hunk.*failed|patch.*does not apply|patch.*rejected|cannot apply", "conflicting_changes"),
    (r"truncat|cut off|incomplete diff", "incomplete_patch"),
]

_UNDERSTANDING_PATTERNS: list[tuple[str, str]] = [
    (r"I could not find|unable to locate|no such file|file not found", "missing_context"),
    (r"outside the scope|unrelated|not mentioned in the issue", "wrong_scope"),
    (r"partial.*fix|only.*part.*issue|addressed.*one.*case", "incomplete_understanding"),
    (r"misunderstood|I misread|incorrect interpretation|I was wrong", "misinterpreted_issue"),
]


def classify_error(
    log: str,
    patch_text: str,
    harness_result: str,   # "resolved" | "unresolved" | "error" | "empty" | "unknown"
    run_meta: dict,
) -> dict:
    """
    Return {"error_category": str, "error_subtype": str, "error_evidence": str}.
    """
    if harness_result == "resolved":
        return {"error_category": "none", "error_subtype": "none", "error_evidence": ""}

    # ── process errors ─────────────────────────────────────────────────────
    for pattern, subtype in _PROCESS_PATTERNS:
        m = re.search(pattern, log, re.IGNORECASE)
        if m:
            snippet = log[max(0, m.start()-60): m.end()+60].strip().replace("\n", " ")
            return {"error_category": "process_error",
                    "error_subtype": subtype,
                    "error_evidence": snippet}

    # ── empty patch ────────────────────────────────────────────────────────
    if harness_result == "empty" or not patch_text.strip():
        # decide if it was a process failure or misunderstanding
        if re.search(r"I could not|unable|no.*found", log, re.IGNORECASE):
            return {"error_category": "understanding_error",
                    "error_subtype": "missing_context",
                    "error_evidence": "empty patch with 'could not find' in log"}
        return {"error_category": "patch_generation_error",
                "error_subtype": "empty_patch",
                "error_evidence": "patch.diff is empty or missing"}

    # ── patch generation errors ────────────────────────────────────────────
    for pattern, subtype in _PATCH_PATTERNS:
        m = re.search(pattern, log + patch_text, re.IGNORECASE)
        if m:
            snippet = (log + patch_text)[max(0, m.start()-60): m.end()+60].strip().replace("\n", " ")
            return {"error_category": "patch_generation_error",
                    "error_subtype": subtype,
                    "error_evidence": snippet}

    # syntax check on added Python lines
    py_files = extract_added_python(patch_text)
    for fname, src in py_files.items():
        try:
            ast.parse(src)
        except SyntaxError as e:
            return {"error_category": "patch_generation_error",
                    "error_subtype": "syntax_error",
                    "error_evidence": f"{fname}: {e}"}

    # ── verification errors (harness ran, tests failed) ────────────────────
    if harness_result == "unresolved":
        if re.search(r"FAILED.*test|test.*FAILED|AssertionError|ERRORS?\b", log, re.IGNORECASE):
            if re.search(r"regression|previously passing|broke.*test", log, re.IGNORECASE):
                subtype = "regression_introduced"
            elif re.search(r"partial|some.*pass|not all", log, re.IGNORECASE):
                subtype = "partial_fix"
            else:
                subtype = "test_failure"
            return {"error_category": "verification_error",
                    "error_subtype": subtype,
                    "error_evidence": "harness unresolved + test failure signals in log"}

    # ── understanding errors ───────────────────────────────────────────────
    for pattern, subtype in _UNDERSTANDING_PATTERNS:
        m = re.search(pattern, log, re.IGNORECASE)
        if m:
            snippet = log[max(0, m.start()-60): m.end()+60].strip().replace("\n", " ")
            return {"error_category": "understanding_error",
                    "error_subtype": subtype,
                    "error_evidence": snippet}

    # harness unresolved but no strong signals
    if harness_result == "unresolved":
        return {"error_category": "verification_error",
                "error_subtype": "test_failure",
                "error_evidence": "harness unresolved, no stronger signal found"}

    return {"error_category": "unknown", "error_subtype": "unknown", "error_evidence": ""}


# ══════════════════════════════════════════════════════════════════════════════
# Harness result lookup
# ══════════════════════════════════════════════════════════════════════════════

def load_harness_results(eval_dir: Path) -> dict[str, dict[str, str]]:
    """
    Return {mode: {instance_id: "resolved"|"unresolved"|"empty"|"error"}}
    by scanning eval/reports/<run_id>/<model>.<run_id>.json
    """
    results: dict[str, dict[str, str]] = {m: {} for m in MODES}
    reports_dir = eval_dir / "reports"
    if not reports_dir.exists():
        return results

    for json_path in reports_dir.rglob("*.json"):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        # Determine mode from filename or run_id embedded in json
        mode = None
        stem = json_path.stem  # e.g. "claude_A.A_run.json" -> stem = "claude_A.A_run"
        for m in MODES:
            if f"_{m}" in stem or f".{m}_" in stem or f"_{m}." in stem:
                mode = m
                break
        if mode is None:
            # try parent directory name
            for m in MODES:
                if f"_{m}" in str(json_path) or f"/{m}_" in str(json_path):
                    mode = m
                    break
        if mode is None:
            continue

        for iid in data.get("resolved_ids", []):
            results[mode][iid] = "resolved"
        for iid in data.get("unresolved_ids", []):
            results[mode].setdefault(iid, "unresolved")
        for iid in data.get("empty_patch_ids", []):
            results[mode].setdefault(iid, "empty")
        for iid in data.get("error_ids", []):
            results[mode].setdefault(iid, "error")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Main analysis loop
# ══════════════════════════════════════════════════════════════════════════════

def analyse_run(
    inst_dir: Path,
    mode: str,
    engine: str,
    harness_map: dict[str, str],   # instance_id -> status
) -> dict:
    iid = inst_dir.name
    out = inst_dir / "output"

    # ── metadata ────────────────────────────────────────────────────────────
    run_meta: dict = {}
    meta_path = out / "run_meta.json"
    if meta_path.exists():
        try:
            run_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # ── logs ────────────────────────────────────────────────────────────────
    log_path = next(
        (p for p in [out / "fix_log.txt", out / "agent_log.txt"] if p.exists()), None
    )
    log = read_text(log_path)
    spec_log = read_text(out / "spec_gen_log.txt")

    # ── patch ───────────────────────────────────────────────────────────────
    patch_path = out / "patch.diff"
    patch_text = read_text(patch_path)
    patch_stats = parse_patch(patch_text)

    # ── harness result ───────────────────────────────────────────────────────
    harness_result = harness_map.get(iid, "unknown")

    # ── code quality metrics ─────────────────────────────────────────────────
    cq = compute_code_metrics(patch_text)

    # ── timing / tokens ──────────────────────────────────────────────────────
    elapsed = run_meta.get("elapsed_sec")
    if elapsed is None:
        # mtime-based fallback
        candidates = [log_path, out / "spec.md", patch_path]
        times = [p.stat().st_mtime_ns for p in candidates if p and p.exists()]
        elapsed = (max(times) - min(times)) / 1e9 if len(times) >= 2 else None

    tokens = extract_tokens(log) or extract_tokens(spec_log)

    # ── agent action counts ──────────────────────────────────────────────────
    actions = agent_action_counts(log)

    # ── error classification ─────────────────────────────────────────────────
    error_info = classify_error(log + spec_log, patch_text, harness_result, run_meta)

    row = {
        "instance_id": iid,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "fix_engine": engine,
        "repo": run_meta.get("repo", ""),
        "base_commit": run_meta.get("base_commit", ""),
        "harness_result": harness_result,
        "elapsed_sec": round(elapsed, 2) if elapsed is not None else None,
        "tokens_used": tokens,
        **patch_stats,
        **actions,
        **{f"cq_{k}": v for k, v in cq.items()},
        **error_info,
        "spec_file": str(out / "spec.md") if (out / "spec.md").exists() else "",
        "log_file": str(log_path) if log_path else "",
        "patch_file": str(patch_path) if patch_path.exists() else "",
    }
    return row


# ══════════════════════════════════════════════════════════════════════════════
# Report generation
# ══════════════════════════════════════════════════════════════════════════════

def fmt(val, precision=2) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, float):
        return f"{val:.{precision}f}"
    return str(val)


def avg(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float)) and r[key] is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def summarise_mode(rows: list[dict]) -> dict:
    total = len(rows)
    by_result = {}
    for r in rows:
        k = r["harness_result"]
        by_result[k] = by_result.get(k, 0) + 1

    return {
        "total": total,
        "resolved": by_result.get("resolved", 0),
        "unresolved": by_result.get("unresolved", 0),
        "empty": by_result.get("empty", 0),
        "error": by_result.get("error", 0),
        "unknown": by_result.get("unknown", 0),
        "resolve_rate": round(by_result.get("resolved", 0) / total, 4) if total else 0,
        # timing
        "avg_elapsed_sec": avg(rows, "elapsed_sec"),
        "avg_tokens": avg(rows, "tokens_used"),
        # patch
        "avg_patch_bytes": avg(rows, "patch_bytes"),
        "avg_lines_added": avg(rows, "lines_added"),
        "avg_files_changed": avg(rows, "files_changed"),
        # code quality
        "avg_cc": avg(rows, "cq_cc_avg"),
        "avg_cc_max": avg(rows, "cq_cc_max"),
        "avg_mi": avg(rows, "cq_maintainability_index"),
        "avg_halstead_volume": avg(rows, "cq_halstead_volume"),
        "avg_comment_ratio": avg(rows, "cq_comment_ratio"),
        "avg_dit": avg(rows, "cq_dit_avg"),
        "avg_dit_max": avg(rows, "cq_dit_max"),
        "avg_lizard_cc": avg(rows, "cq_lizard_cc_avg"),
        "avg_cog": avg(rows, "cq_cog_avg"),
        "avg_cog_max": avg(rows, "cq_cog_max"),
    }


def composite_score(s: dict) -> dict:
    """
    Harness Score  = resolve_rate
    Quality Score  = 0.25*(MI/100) + 0.20*comment_ratio
                   + 0.15*max(0, 1-DIT/10)
                   + 0.20*max(0, 1-CC/20)
                   + 0.20*max(0, 1-Cog/15)
    Overall Score  = 0.60*Harness + 0.40*Quality
    Returns a dict with the three scores (None if data missing).
    Cog = Cognitive Complexity (SonarSource); penalises nesting.
    """
    h = s.get("resolve_rate")
    mi  = s.get("avg_mi")
    cr  = s.get("avg_comment_ratio")
    dit = s.get("avg_dit")
    cc  = s.get("avg_cc")
    cog = s.get("avg_cog")

    if any(v is None for v in [mi, cr, dit, cc]):
        quality = None
    else:
        cog_term = max(0.0, 1.0 - (cog or 0.0) / 15.0)
        quality = round(
            0.25 * (mi / 100)
            + 0.20 * cr
            + 0.15 * max(0.0, 1.0 - dit / 10.0)
            + 0.20 * max(0.0, 1.0 - cc / 20.0)
            + 0.20 * cog_term,
            4,
        )

    if h is None or quality is None:
        overall = None
    else:
        overall = round(0.60 * h + 0.40 * quality, 4)

    return {
        "harness_score": round(h, 4) if h is not None else None,
        "quality_score": quality,
        "overall_score": overall,
    }


def error_breakdown(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Return {category: {subtype: count}}."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        cat = r.get("error_category", "unknown")
        sub = r.get("error_subtype", "unknown")
        out.setdefault(cat, {})
        out[cat][sub] = out[cat].get(sub, 0) + 1
    return out


def render_report(all_rows: list[dict], modes: list[str]) -> str:
    lines: list[str] = []
    a = lines.append

    a(f"# Experiment Report (auto-generated)")
    a(f"")
    a(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    a(f"Modes: {', '.join(f'{m} ({MODE_LABELS[m]})' for m in modes)}")
    a(f"Total runs analysed: {len(all_rows)}")
    a(f"")

    # ── 1. Resolution rates ─────────────────────────────────────────────────
    a("## 1. SWE-bench Resolution Rates")
    a("")
    header = "| Mode | Label | Total | Resolved | Unresolved | Empty | Error | Unknown | Resolve% |"
    sep    = "|------|-------|-------|----------|-----------|-------|-------|---------|----------|"
    a(header); a(sep)
    summaries = {}
    for m in modes:
        rows = [r for r in all_rows if r["mode"] == m]
        s = summarise_mode(rows)
        summaries[m] = s
        a(f"| {m} | {MODE_LABELS.get(m,m)} | {s['total']} | {s['resolved']} | "
          f"{s['unresolved']} | {s['empty']} | {s['error']} | {s['unknown']} | "
          f"{s['resolve_rate']*100:.1f}% |")
    a("")

    # ── 2. Timing & cost ────────────────────────────────────────────────────
    a("## 2. Timing & Token Cost")
    a("")
    a("| Mode | Avg elapsed (s) | Avg tokens | Avg patch bytes | Avg lines added | Avg files changed |")
    a("|------|-----------------|------------|-----------------|-----------------|-------------------|")
    for m in modes:
        s = summaries[m]
        a(f"| {m} | {fmt(s['avg_elapsed_sec'])} | {fmt(s['avg_tokens'],0)} | "
          f"{fmt(s['avg_patch_bytes'],0)} | {fmt(s['avg_lines_added'],1)} | "
          f"{fmt(s['avg_files_changed'],1)} |")
    a("")

    # ── 3. Code quality ─────────────────────────────────────────────────────
    a("## 3. Code Quality Metrics (on changed Python code)")
    a("")
    a("> Metrics computed on the **added** lines of each `patch.diff`.")
    a("> CC = Cyclomatic Complexity (↓ simpler), MI = Maintainability Index 0-100 (↑ better),")
    a("> Cog = Cognitive Complexity/SonarSource (↓ easier to read; penalises nesting),")
    a("> DIT = Depth of Inheritance Tree (↓ shallower hierarchy is simpler),")
    a("> Halstead Vol = code volume/difficulty (↓ simpler), Comment ratio (↑ better).")
    a("")
    a("| Mode | CC avg | CC max | Cog avg | Cog max | MI | Halstead vol | Comment ratio | DIT avg | DIT max | Lizard CC |")
    a("|------|--------|--------|---------|---------|-----|-------------|--------------|---------|---------|-----------|")
    for m in modes:
        s = summaries[m]
        a(f"| {m} | {fmt(s['avg_cc'])} | {fmt(s['avg_cc_max'])} | "
          f"{fmt(s['avg_cog'])} | {fmt(s['avg_cog_max'])} | "
          f"{fmt(s['avg_mi'])} | {fmt(s['avg_halstead_volume'])} | "
          f"{fmt(s['avg_comment_ratio'],4)} | {fmt(s['avg_dit'])} | "
          f"{fmt(s['avg_dit_max'])} | {fmt(s['avg_lizard_cc'])} |")
    a("")

    # ── 4. Composite scores ──────────────────────────────────────────────────
    a("## 4. Composite Scores")
    a("")
    a("> **Quality Score** = 0.25·(MI/100) + 0.20·CommentRatio"
      " + 0.15·max(0,1−DIT/10) + 0.20·max(0,1−CC/20) + 0.20·max(0,1−Cog/15)")
    a("> **Overall Score** = 0.60·HarnessScore + 0.40·QualityScore")
    a("")
    a("| Mode | Label | Harness Score | Quality Score | Overall Score |")
    a("|------|-------|--------------|--------------|--------------|")
    for m in modes:
        cs = composite_score(summaries[m])
        a(f"| {m} | {MODE_LABELS.get(m,m)} | {fmt(cs['harness_score'],4)} | "
          f"{fmt(cs['quality_score'],4)} | **{fmt(cs['overall_score'],4)}** |")
    a("")

    # ── 5. Error classification ─────────────────────────────────────────────
    a("## 5. Error Classification")
    a("")
    a("### 4.1 By Category")
    a("")
    all_cats = sorted({r["error_category"] for r in all_rows})
    cat_header = "| Mode | " + " | ".join(all_cats) + " |"
    cat_sep    = "|------|" + "-------|" * len(all_cats)
    a(cat_header); a(cat_sep)
    for m in modes:
        rows_m = [r for r in all_rows if r["mode"] == m]
        eb = error_breakdown(rows_m)
        total_err = len(rows_m)
        cells = [f"{eb.get(c, {}).values().__iter__().__next__() if list(eb.get(c, {}).values()) else 0}" for c in all_cats]
        # actually count properly
        cells = []
        for c in all_cats:
            cnt = sum(eb.get(c, {}).values())
            pct = f"{cnt*100/total_err:.0f}%" if total_err else "0%"
            cells.append(f"{cnt} ({pct})")
        a(f"| {m} | " + " | ".join(cells) + " |")
    a("")

    a("### 4.2 Subtype Breakdown per Mode")
    a("")
    for m in modes:
        rows_m = [r for r in all_rows if r["mode"] == m]
        eb = error_breakdown(rows_m)
        a(f"#### Mode {m} — {MODE_LABELS.get(m, m)}")
        a("")
        a("| Category | Subtype | Count |")
        a("|----------|---------|-------|")
        for cat in sorted(eb):
            for sub, cnt in sorted(eb[cat].items(), key=lambda x: -x[1]):
                a(f"| {cat} | {sub} | {cnt} |")
        a("")

    # ── 5. Per-instance table ───────────────────────────────────────────────
    a("## 5. Per-Instance Results")
    a("")
    a("| Instance | Mode | Harness | Elapsed(s) | Tokens | Lines+ | CC avg | Comment% | Error category | Subtype |")
    a("|----------|------|---------|-----------|--------|--------|--------|----------|----------------|---------|")
    for r in sorted(all_rows, key=lambda x: (x["mode"], x["instance_id"])):
        cr = f"{r.get('cq_comment_ratio', 0)*100:.1f}%" if r.get("cq_comment_ratio") is not None else "N/A"
        a(f"| {r['instance_id']} | {r['mode']} | {r['harness_result']} | "
          f"{fmt(r.get('elapsed_sec'))} | {fmt(r.get('tokens_used'),0)} | "
          f"{r.get('lines_added','N/A')} | {fmt(r.get('cq_cc_avg'))} | "
          f"{cr} | {r.get('error_category','?')} | {r.get('error_subtype','?')} |")
    a("")

    a("---")
    a("")
    a("*Full data in `results_full.csv`. Generated by `analysis/scripts/full_report.py`.*")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs_dir",  default="ab_spec_study/runs")
    ap.add_argument("--engine",    required=True)
    ap.add_argument("--eval_dir",  default="ab_spec_study/eval")
    ap.add_argument("--out_dir",   default="ab_spec_study")
    ap.add_argument("--modes", nargs="+", default=MODES)
    args = ap.parse_args()

    runs = Path(args.runs_dir)
    eval_dir = Path(args.eval_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[report] loading harness results from {eval_dir}/reports/ ...")
    harness = load_harness_results(eval_dir)

    all_rows: list[dict] = []
    for mode in args.modes:
        engine_dir = runs / args.engine / mode
        if not engine_dir.exists():
            print(f"[report] mode={mode}: runs dir not found ({engine_dir}), skipping")
            continue
        inst_dirs = sorted(engine_dir.iterdir())
        print(f"[report] mode={mode}: {len(inst_dirs)} instances")
        for inst_dir in inst_dirs:
            if not (inst_dir / "output" / "patch.diff").exists():
                continue
            row = analyse_run(inst_dir, mode, args.engine, harness[mode])
            all_rows.append(row)

    if not all_rows:
        print("[report][WARN] no runs found — nothing to report")
        sys.exit(0)

    # ── CSV ──────────────────────────────────────────────────────────────────
    csv_path = out_dir / "results_full.csv"
    all_keys = []
    for r in all_rows:
        for k in r:
            if k not in all_keys:
                all_keys.append(k)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in all_keys})
    print(f"[report] CSV  -> {csv_path}")

    # ── Markdown report ───────────────────────────────────────────────────────
    md = render_report(all_rows, args.modes)
    md_path = out_dir / "report_full.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"[report] MD   -> {md_path}")


if __name__ == "__main__":
    main()
