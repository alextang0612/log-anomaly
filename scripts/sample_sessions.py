#!/usr/bin/env python3
"""
Sample sessions for the frontier-model baseline, with BOTH representations.

sessions.csv only stores template IDs. To compare "raw lines" against "parsed
templates" as prompt representations -- the week-3 decision -- the original log
lines have to be pulled back out of HDFS.log. This does that in one pass.

Output is a self-contained .jsonl, one session per line:

    {"block_id": "...", "label": "Anomaly", "length": 21,
     "template_ids": [1, 2, 5, ...],
     "templates":    ["Receiving block <BLK> src: ...", ...],
     "raw_lines":    ["081109 203518 143 INFO dfs.DataNode...", ...]}

TWO SAMPLING MODES, AND THE DIFFERENCE MATTERS
----------------------------------------------
--stratify   roughly balanced classes. Use ONLY for prompt development, so you
             actually see failure cases while iterating. Metrics computed on a
             stratified sample are meaningless -- precision depends on the class
             ratio, and you changed it.

default      natural class ratio. Use for every number you intend to report.

WHICH SPLIT
-----------
Prompt development   --split train   (you may look at train freely)
Measurement          --split val     (comparable to the baselines already run)
Final report         --split test    (once, at the very end)

Usage:
    python scripts/sample_sessions.py --split train --n 100 --stratify
    python scripts/sample_sessions.py --split val   --n 3000
"""

import argparse
import csv
import json
import os
import random
import re
import sys

csv.field_size_limit(10_000_000)

LINE_RE = re.compile(r"^(\d{6})\s+(\d{6})\s+(\d+)\s+(\w+)\s+(\S+?):\s*(.*)$")
BLK_RE = re.compile(r"blk_-?\d+")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train", choices=["train", "val", "test"])
    ap.add_argument("--splits-dir", default="data/splits")
    ap.add_argument("--log", default="data/raw/HDFS.log")
    ap.add_argument("--templates", default="data/processed/templates.csv")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--stratify", action="store_true",
                    help="balance the classes. PROMPT DEVELOPMENT ONLY.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-raw", action="store_true",
                    help="skip raw line extraction (much faster)")
    args = ap.parse_args()

    split_path = os.path.join(args.splits_dir, f"{args.split}.csv")
    if not os.path.exists(split_path):
        print(f"ERROR: {split_path} not found -- run make_split.py first.", file=sys.stderr)
        return 1

    out = args.out or (f"data/samples/{args.split}_n{args.n}"
                       f"{'_stratified' if args.stratify else ''}.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    tmpl = {}
    with open(args.templates, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tmpl[int(r["template_id"])] = r["template"]

    with open(split_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    rng = random.Random(args.seed)

    if args.stratify:
        anom = [r for r in rows if r["label"] == "Anomaly"]
        norm = [r for r in rows if r["label"] == "Normal"]
        half = args.n // 2
        if len(anom) < half:
            print(f"  note: only {len(anom)} anomalies available, using all", file=sys.stderr)
        pick = (rng.sample(anom, min(half, len(anom)))
                + rng.sample(norm, min(args.n - min(half, len(anom)), len(norm))))
        rng.shuffle(pick)
        print("  !! STRATIFIED SAMPLE -- for prompt development only.")
        print("     Do not report precision/recall from this file.")
    else:
        pick = rng.sample(rows, min(args.n, len(rows)))

    wanted = {r["block_id"]: r for r in pick}
    raw = {b: [] for b in wanted} if not args.no_raw else {}

    if not args.no_raw:
        if not os.path.exists(args.log):
            print(f"ERROR: {args.log} not found.", file=sys.stderr)
            return 1
        print(f"  scanning {args.log} for {len(wanted):,} block ids ...")
        n = 0
        with open(args.log, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                n += 1
                # cheap pre-filter before the regex
                if "blk_" not in line:
                    continue
                for b in BLK_RE.findall(line):
                    if b in raw:
                        raw[b].append(line.rstrip("\n"))
                if n % 2_000_000 == 0:
                    print(f"    {n:>12,} lines")
        print(f"  scanned {n:,} lines")

    n_written = 0
    with open(out, "w", encoding="utf-8") as f:
        for r in pick:
            ids = [int(x) for x in r["sequence"].split()] if r["sequence"] else []
            rec = {
                "block_id": r["block_id"],
                "label": r["label"],
                "first_ts": r["first_ts"],
                "length": int(r["length"]),
                "template_ids": ids,
                "templates": [tmpl.get(i, f"<unknown template {i}>") for i in ids],
            }
            if not args.no_raw:
                rec["raw_lines"] = raw.get(r["block_id"], [])
            f.write(json.dumps(rec) + "\n")
            n_written += 1

    n_anom = sum(1 for r in pick if r["label"] == "Anomaly")
    print()
    print(f"  wrote {n_written:,} sessions -> {out}")
    print(f"  anomalies: {n_anom:,} ({100*n_anom/max(n_written,1):.2f}%)")
    if not args.no_raw:
        tot = sum(len(v) for v in raw.values())
        print(f"  raw lines extracted: {tot:,} "
              f"(avg {tot/max(n_written,1):.1f} per session)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
