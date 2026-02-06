Hello!
# SWE-bench A/B Spec Generation Study

This repository contains experiments for comparing different specification generation methods
on SWE-bench-Verified.

## Structure

- `analysis/scripts/` — data processing & evaluation scripts
- `instance_ids_*.txt` — benchmark subsets
- `results*.csv` — evaluation outputs
- `report*.md` — analysis reports
- `workspaces/` — ignored (runtime artifacts)
- `runs/` — ignored (runtime artifacts)
- `repo_cache/` — ignored (local repo mirrors)

## Experiments

A/B groups:
- A: Codex-generated specs
- B: Spec-kit / OpenSpec-generated specs

## Reproducibility

```bash
pip install -e .
bash run_batch.sh
