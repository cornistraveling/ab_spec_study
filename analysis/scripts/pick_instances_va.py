import argparse
from datasets import load_dataset
from collections import defaultdict

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="SWE-bench/SWE-bench_Verified")
    ap.add_argument("--split", default="test")
    ap.add_argument("--k", type=int, default=50)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--out", default="ab_spec_study/instance_ids_50.txt")
    args = ap.parse_args()

    ds = load_dataset(args.dataset, split=args.split)

    rows = [
        r for r in ds
        if r.get("instance_id") != args.exclude
    ]

    # 1. 按 repo 分组
    by_repo = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)

    # 2. repo 排序 + repo 内 instance 排序（保证可复现）
    repos = sorted(by_repo.keys())
    for repo in repos:
        by_repo[repo].sort(key=lambda r: r["instance_id"])

    # 3. round-robin 取
    picked = []
    idx = 0
    while len(picked) < args.k:
        progress = False
        for repo in repos:
            if idx < len(by_repo[repo]):
                picked.append(by_repo[repo][idx])
                progress = True
                if len(picked) >= args.k:
                    break
        if not progress:
            break  # 所有 repo 都取完了
        idx += 1

    # 4. 写文件
    with open(args.out, "w", encoding="utf-8") as f:
        for r in picked:
            f.write(r["instance_id"] + "\n")

    print(
        f"[OK] picked {len(picked)} instances "
        f"from {len(set(r['repo'] for r in picked))} repos"
    )
    print(f"-> {args.out}")

if __name__ == "__main__":
    main()
