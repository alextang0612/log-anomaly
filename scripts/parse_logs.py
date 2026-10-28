#!/usr/bin/env python3
"""
Stage 1 of the log-anomaly pipeline: raw HDFS lines -> per-block template sequences.

What it does
------------
1. Streams HDFS.log line by line (never loads 1.5 GB into memory).
2. Splits off the fixed header (date, time, pid, level, component) so Drain3 only
   sees the message body -- this is what published work parses, so your templates
   stay comparable to theirs.
3. Masks the high-cardinality tokens (block ids, IPs, paths, bare numbers) before
   template mining. Without this Drain3 invents thousands of near-duplicate
   templates and the whole downstream model is garbage.
4. Groups every line into one sequence per block_id, in log order.
5. Joins the Normal/Anomaly label and records each block's FIRST timestamp, which
   is what makes the chronological split possible in week 2 without reparsing.

Outputs (data/processed/)
-------------------------
templates.csv  one row per discovered template: id, count, template, example line
sessions.csv   one row per block: block_id, label, first_ts, length, sequence

Usage
-----
    python scripts/parse_logs.py --limit 200000     # ~30s, use this first
    python scripts/parse_logs.py                    # full run, 10-25 min
    python scripts/parse_logs.py --sim-th 0.5       # retune if templates look wrong
"""

import argparse
import csv
import os
import re
import sys
import time
from array import array
from collections import defaultdict

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from drain3.masking import MaskingInstruction

# HDFS line format:
#   081109 203518 143 INFO dfs.DataNode$DataXceiver: Receiving block blk_-160... src: /10.250.19.102:54106
#   <date> <time> <pid> <level> <component>: <content>
LINE_RE = re.compile(r"^(\d{6})\s+(\d{6})\s+(\d+)\s+(\w+)\s+(\S+?):\s*(.*)$")

# Block ids are pulled from the RAW content, before masking replaces them.
# A single line can mention several blocks; the line joins every one of them.
BLK_RE = re.compile(r"blk_-?\d+")


def build_miner(sim_th: float, depth: int) -> TemplateMiner:
    """Drain3 configured for HDFS.

    Masking order matters. IP must be matched before PATH, or '/10.250.19.102:50010'
    gets eaten by the path rule and you lose the distinction between a path and an
    endpoint. NUM goes last so it only catches what nothing else claimed.
    """
    config = TemplateMinerConfig()
    config.drain_sim_th = sim_th
    config.drain_depth = depth
    config.profiling_enabled = False
    config.parametrize_numeric_tokens = False  # we do our own numeric masking
    config.masking_instructions = [
        MaskingInstruction(r"blk_-?\d+", "BLK"),
        MaskingInstruction(r"java\.[\w.$]+(?:Exception|Error)\b.*", "EXCEPTION"),
        MaskingInstruction(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?", "IP"),
        MaskingInstruction(r"/[\w\-./]+", "PATH"),
        MaskingInstruction(r"\b\d+\b", "NUM"),
    ]
    return TemplateMiner(config=config)


def load_labels(path: str) -> dict:
    """block_id -> 'Normal' | 'Anomaly'"""
    labels = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header and header[0].strip().lower() != "blockid":
            f.seek(0)
            reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                labels[row[0].strip()] = row[1].strip()
    return labels


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default="data/raw/HDFS.log")
    ap.add_argument("--labels", default="data/raw/preprocessed/anomaly_label.csv")
    ap.add_argument("--out", default="data/processed")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N lines (0 = all). Use this while iterating.")
    ap.add_argument("--sim-th", type=float, default=0.4,
                    help="Drain3 similarity threshold. Raise it if templates are "
                         "too coarse (unrelated lines merged), lower it if too many "
                         "near-identical templates.")
    ap.add_argument("--depth", type=int, default=4)
    args = ap.parse_args()

    for p in (args.log, args.labels):
        if not os.path.exists(p):
            print(f"ERROR: not found: {p}", file=sys.stderr)
            print("Run this from the project root (~/projects/log-anomaly).", file=sys.stderr)
            return 1

    os.makedirs(args.out, exist_ok=True)

    print(f"Loading labels from {args.labels} ...")
    labels = load_labels(args.labels)
    print(f"  {len(labels):,} labeled blocks")

    miner = build_miner(args.sim_th, args.depth)

    sequences = defaultdict(lambda: array("i"))   # block_id -> [template_id, ...]
    first_ts = {}                                 # block_id -> "YYMMDD HHMMSS"
    tmpl_count = defaultdict(int)                 # template_id -> occurrences
    tmpl_example = {}                             # template_id -> one raw content line

    unparsed = 0
    no_block = 0
    n = 0
    t0 = time.time()

    print(f"Parsing {args.log} ...")
    with open(args.log, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            n += 1
            if args.limit and n > args.limit:
                n -= 1
                break

            m = LINE_RE.match(line)
            if not m:
                unparsed += 1
                continue
            date, tm, _pid, _level, _component, content = m.groups()

            blocks = BLK_RE.findall(content)
            if not blocks:
                no_block += 1
                continue

            result = miner.add_log_message(content)
            tid = result["cluster_id"]
            tmpl_count[tid] += 1
            if tid not in tmpl_example:
                tmpl_example[tid] = content.strip()

            ts = f"{date} {tm}"
            for blk in dict.fromkeys(blocks):   # dedupe, keep order
                sequences[blk].append(tid)
                if blk not in first_ts:
                    first_ts[blk] = ts

            if n % 500_000 == 0:
                el = time.time() - t0
                rate = n / el
                print(f"  {n:>12,} lines  |  {rate:>8,.0f} lines/s  "
                      f"|  {len(tmpl_count):>3} templates  |  {el/60:>5.1f} min")

    elapsed = time.time() - t0
    print(f"\nParsed {n:,} lines in {elapsed/60:.1f} min ({n/max(elapsed,1):,.0f} lines/s)")
    print(f"  unparsed (header mismatch): {unparsed:,}")
    print(f"  no block id in line:        {no_block:,}")
    print(f"  distinct templates:         {len(tmpl_count)}")
    print(f"  distinct blocks:            {len(sequences):,}")

    # ---- templates.csv -------------------------------------------------
    clusters = {c.cluster_id: c.get_template() for c in miner.drain.clusters}
    tpath = os.path.join(args.out, "templates.csv")
    with open(tpath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["template_id", "count", "template", "example_line"])
        for tid, cnt in sorted(tmpl_count.items(), key=lambda kv: -kv[1]):
            w.writerow([tid, cnt, clusters.get(tid, ""), tmpl_example.get(tid, "")])
    print(f"\nWrote {tpath}")

    # ---- sessions.csv --------------------------------------------------
    spath = os.path.join(args.out, "sessions.csv")
    missing_label = 0
    n_anom = 0
    lengths = []
    with open(spath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["block_id", "label", "first_ts", "length", "sequence"])
        for blk, seq in sequences.items():
            lab = labels.get(blk)
            if lab is None:
                missing_label += 1
                lab = "Unknown"
            if lab == "Anomaly":
                n_anom += 1
            lengths.append(len(seq))
            w.writerow([blk, lab, first_ts[blk], len(seq),
                        " ".join(str(t) for t in seq)])
    print(f"Wrote {spath}")

    # ---- the numbers you actually check --------------------------------
    lengths.sort()
    def pct(p):
        return lengths[min(int(len(lengths) * p), len(lengths) - 1)] if lengths else 0

    print("\n" + "=" * 62)
    print("SANITY CHECK  -- compare these against the handbook")
    print("=" * 62)
    total = len(sequences)
    print(f"  sessions:          {total:,}        (expect ~575,061 on a full run)")
    print(f"  anomalies:         {n_anom:,} ({100*n_anom/max(total,1):.2f}%)   (expect 2.93%)")
    print(f"  blocks w/o label:  {missing_label:,}        (expect 0 on a full run)")
    print(f"  templates found:   {len(tmpl_count)}          (expect roughly 20-50)")
    print(f"  session length:    min {lengths[0] if lengths else 0}  "
          f"p50 {pct(0.50)}  p90 {pct(0.90)}  p99 {pct(0.99)}  max {lengths[-1] if lengths else 0}")
    print("=" * 62)
    print("\nNEXT: inspect the templates before trusting any of this.")
    print("      python scripts/inspect_templates.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
