import argparse
from datasets import load_dataset

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dataset",
        default="SWE-bench/SWE-bench_Verified",
        help="HF dataset name",
    )
    ap.add_argument(
        "--split",
        default="test",
        help="Dataset split",
    )
    ap.add_argument(
        "--out",
        default="ab_spec_study/instance_ids_all.txt",
        help="Output file",
    )
    args = ap.parse_args()

    print(f"[INFO] loading dataset {args.dataset} [{args.split}]")
    ds = load_dataset(args.dataset, split=args.split)

    # SWE-bench-Verified test already has unique instance_id
    instance_ids = [r["instance_id"] for r in ds]

    print(f"[INFO] total instances: {len(instance_ids)}")

    with open(args.out, "w", encoding="utf-8") as f:
        for iid in instance_ids:
            f.write(iid + "\n")

    print(f"[OK] wrote {len(instance_ids)} instance_ids -> {args.out}")

if __name__ == "__main__":
    main()
