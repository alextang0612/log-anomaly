#!/usr/bin/env python3
"""
The week-1 gate: read the templates yourself before trusting anything downstream.

Bad parsing is silent. It does not throw, it does not look wrong in a metric, it
just quietly destroys the signal your model is supposed to learn -- and you find
out in week 6 when the model won't train and you can't tell why.

So: run this, read every template, and answer three questions.

  1. Does each template read like a sentence with holes in it?
         GOOD  Receiving block <BLK> src: <IP> dest: <IP>
         BAD   Receiving block <BLK> src: <*> dest: <*> <*> <*> <*>

  2. Are two clearly different events sharing one template?
         -> threshold too LOW, raise --sim-th (0.4 -> 0.5)

  3. Is one obvious event split across several near-identical templates?
         -> threshold too HIGH, lower --sim-th (0.4 -> 0.3)

You are an infra person. You have read a million log lines. Trust that reaction:
if a template looks wrong to you, it is wrong.

Usage:
    python scripts/inspect_templates.py
    python scripts/inspect_templates.py --top 50
"""

import argparse
import csv
import os
import sys

csv.field_size_limit(10_000_000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", default="data/processed/templates.csv")
    ap.add_argument("--top", type=int, default=0,
                    help="show only the N most frequent (0 = all)")
    args = ap.parse_args()

    if not os.path.exists(args.templates):
        print(f"ERROR: {args.templates} not found -- run parse_logs.py first.",
              file=sys.stderr)
        return 1

    with open(args.templates, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.top:
        rows = rows[:args.top]

    total = sum(int(r["count"]) for r in rows)

    print("=" * 78)
    print(f"{len(rows)} TEMPLATES   ({total:,} lines matched)")
    print("=" * 78)

    for i, r in enumerate(rows, 1):
        cnt = int(r["count"])
        share = 100 * cnt / max(total, 1)
        print(f"\n[{i:>3}] id={r['template_id']:<5} {cnt:>10,} lines  ({share:5.2f}%)")
        print(f"      TEMPLATE: {r['template']}")
        print(f"      EXAMPLE : {r['example_line'][:150]}")

    print("\n" + "=" * 78)
    print("Now answer, in your own words, before moving on:")
    print("  - do these templates look like real HDFS events?")
    print("  - what distinguishes an anomalous block from a normal one?")
    print("If you cannot answer the second one yet, that is what the EDA is for.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
