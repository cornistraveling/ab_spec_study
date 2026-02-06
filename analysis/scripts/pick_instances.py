import argparse
from datasets import load_dataset

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="SWE-bench/SWE-bench_Verified")
    ap.add_argument("--split", default="test")
    ap.add_argument("--repo", default="sympy/sympy")
    ap.add_argument("--k", type=int, default=9)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--exclude", default="sympy__sympy-20590")
    ap.add_argument("--out", default="ab_spec_study/instance_ids.txt")
    args = ap.parse_args()

    ds = load_dataset(args.dataset, split=args.split)
    rows = [r for r in ds if r.get("repo") == args.repo and r.get("instance_id") != args.exclude]

    # 稳定可复现：按 instance_id 排序后取前 k 个（最稳），或者你也可以改成 shuffle(seed)
    rows.sort(key=lambda r: r["instance_id"])
    picked = rows[:args.k]

    with open(args.out, "w", encoding="utf-8") as f:
        for r in picked:
            f.write(r["instance_id"] + "\n")

    print(f"[OK] picked {len(picked)} instances -> {args.out}")
    print("\n".join([r["instance_id"] for r in picked]))

if __name__ == "__main__":
    main()
