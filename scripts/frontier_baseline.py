#!/usr/bin/env python3
"""
The frontier-model baseline. Weeks 3-4.

WHAT THIS IS FOR, GIVEN WEEK 2
------------------------------
Not to find a number to beat. HDFS is saturated -- a dictionary scores 0.92 and
logistic regression 0.995, so there is no accuracy headroom for anyone. This run
is the COST ANCHOR the project's whole argument rests on: at roughly equal
accuracy, what does a frontier model cost per million lines, and what does local
inference cost?

So the accuracy columns matter less than the token counts. Record them properly.

GIVE IT A FAIR SHOT
-------------------
A rigged baseline destroys the project's credibility, and the cost gap will be
dramatic regardless. Iterate the prompt on TRAIN until it is genuinely good
before measuring anything.

SPENDING REAL MONEY -- the safety rails here are deliberate
-----------------------------------------------------------
  --dry-run     prints the first prompt and an estimated cost. Costs nothing.
                ALWAYS run this first.
  --max-calls   hard ceiling. Refuses to exceed it.
  resume        existing results are skipped, so a crash or Ctrl-C never
                re-bills work already done. Just re-run the same command.

PRICING
-------
Prices change, so none are hardcoded. Look up the current per-million-token
price for your model and pass them:

    --price-in 3.00 --price-out 15.00      (dollars per 1M tokens)

Usage:
    python scripts/frontier_baseline.py --input data/samples/train_n100_stratified.jsonl --dry-run
    python scripts/frontier_baseline.py --input data/samples/train_n100_stratified.jsonl --max-calls 100
    python scripts/frontier_baseline.py --input data/samples/val_n3000.jsonl --max-calls 3000
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evaluate import report  # noqa: E402

# --------------------------------------------------------------------------
# The prompt. EDIT THIS -- it is week 3's actual work.
#
# The description below comes from the week-1 EDA. That sentence was written
# for exactly this purpose: it is domain knowledge the model does not have, and
# withholding it would make the baseline unfair rather than clean.
# --------------------------------------------------------------------------
SYSTEM = """You are an expert site reliability engineer analysing HDFS \
(Hadoop Distributed File System) logs.

A "block" is a unit of data storage. Each block produces a sequence of log \
events over its lifetime. Your job is to decide whether a block's event \
sequence indicates a FAILURE.

Background on normal behaviour:
- A healthy block follows a consistent lifecycle: allocate -> receive -> \
packet responder terminates -> blockMap updated -> eventually deleted.
- Healthy blocks produce roughly 19 events. Fewer than 13 is unusual.

Signals of failure:
- The lifecycle truncates early and never completes.
- The block requires re-replication or transfer to another datanode.
- The write path throws an exception.
- An unexpected error occurs deleting the block.

Important: not every error-looking message indicates failure. \
"Got exception while serving" appears in healthy blocks slightly more often \
than in failed ones, and should not by itself trigger a FAILURE verdict.

Answer with exactly one word: NORMAL or ANOMALY."""

USER_TEMPLATE = """Event sequence for one HDFS block:

{body}

Is this block NORMAL or ANOMALY? Answer with one word."""


def build_prompt(rec, representation):
    if representation == "raw":
        lines = rec.get("raw_lines") or []
        if not lines:
            raise SystemExit("ERROR: no raw_lines in input. Re-run "
                             "sample_sessions.py without --no-raw.")
        body = "\n".join(lines)
    else:
        body = "\n".join(f"{i+1}. {t}" for i, t in enumerate(rec["templates"]))
    return USER_TEMPLATE.format(body=body)


def parse_verdict(text):
    """Returns 1 (anomaly), 0 (normal), or None (unparseable)."""
    t = (text or "").strip().upper()
    if re.search(r"\bANOMAL", t):
        return 1
    if re.search(r"\bNORMAL\b", t):
        return 0
    return None


# --------------------------------------------------------------------------
def call_anthropic(client, model, system, user, max_tokens):
    t0 = time.time()
    r = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return {
        "text": "".join(b.text for b in r.content if b.type == "text"),
        "in_tokens": r.usage.input_tokens,
        "out_tokens": r.usage.output_tokens,
        "latency_ms": (time.time() - t0) * 1000,
    }


def call_openai(client, model, system, user, max_tokens):
    t0 = time.time()
    r = client.chat.completions.create(
        model=model, max_completion_tokens=max_tokens,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return {
        "text": r.choices[0].message.content,
        "in_tokens": r.usage.prompt_tokens,
        "out_tokens": r.usage.completion_tokens,
        "latency_ms": (time.time() - t0) * 1000,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="jsonl from sample_sessions.py")
    ap.add_argument("--out", default=None)
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--model", default=None, help="exact model id for your provider")
    ap.add_argument("--representation", default="templates", choices=["templates", "raw"])
    ap.add_argument("--max-tokens", type=int, default=8)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-calls", type=int, default=100,
                    help="hard ceiling on API calls this run")
    ap.add_argument("--price-in", type=float, default=None,
                    help="$ per 1M input tokens (look it up)")
    ap.add_argument("--price-out", type=float, default=None,
                    help="$ per 1M output tokens")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: {args.input} not found.", file=sys.stderr)
        return 1

    recs = [json.loads(l) for l in open(args.input, encoding="utf-8")]
    out = args.out or args.input.replace("data/samples/", "data/frontier/") \
                                 .replace(".jsonl", f"_{args.representation}.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    done = {}
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            try:
                r = json.loads(line)
                done[r["block_id"]] = r
            except json.JSONDecodeError:
                pass
        if done:
            print(f"  resuming: {len(done):,} already done, skipping those")

    todo = [r for r in recs if r["block_id"] not in done]

    # ---- dry run -------------------------------------------------------
    if args.dry_run or not todo:
        if todo:
            p = build_prompt(todo[0], args.representation)
            print("=" * 74)
            print("SYSTEM PROMPT")
            print("=" * 74)
            print(SYSTEM)
            print()
            print("=" * 74)
            print(f"FIRST USER PROMPT  (label: {todo[0]['label']})")
            print("=" * 74)
            print(p[:3000] + ("\n...[truncated]" if len(p) > 3000 else ""))
            print()
            chars = sum(len(build_prompt(r, args.representation)) for r in todo)
            est_in = (chars + len(SYSTEM) * len(todo)) / 3.7   # rough chars/token
            est_out = args.max_tokens * len(todo)
            print("=" * 74)
            print(f"  sessions to process : {len(todo):,}")
            print(f"  est. input tokens   : {est_in:,.0f}")
            print(f"  est. output tokens  : {est_out:,.0f}")
            if args.price_in and args.price_out:
                cost = est_in / 1e6 * args.price_in + est_out / 1e6 * args.price_out
                print(f"  ESTIMATED COST      : ${cost:,.2f}")
            else:
                print("  cost: pass --price-in and --price-out to estimate")
            print("=" * 74)
            print("  Estimate only -- real token counts are recorded per call.")
        if not args.dry_run:
            print("  nothing left to do.")
        if todo and args.dry_run:
            return 0

    if len(todo) > args.max_calls:
        print(f"\nERROR: {len(todo):,} calls needed but --max-calls is "
              f"{args.max_calls:,}.", file=sys.stderr)
        print("Raise it deliberately once you have seen the dry-run estimate.",
              file=sys.stderr)
        return 1

    # ---- client --------------------------------------------------------
    if args.provider == "anthropic":
        try:
            from anthropic import Anthropic
        except ImportError:
            print("ERROR: uv pip install anthropic", file=sys.stderr)
            return 1
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ERROR: export ANTHROPIC_API_KEY=...", file=sys.stderr)
            return 1
        client, caller = Anthropic(), call_anthropic
    else:
        try:
            from openai import OpenAI
        except ImportError:
            print("ERROR: uv pip install openai", file=sys.stderr)
            return 1
        if not os.environ.get("OPENAI_API_KEY"):
            print("ERROR: export OPENAI_API_KEY=...", file=sys.stderr)
            return 1
        client, caller = OpenAI(), call_openai

    if not args.model:
        print("ERROR: pass --model with your provider's exact model id.", file=sys.stderr)
        return 1

    print(f"\n  calling {args.provider}/{args.model} on {len(todo):,} sessions "
          f"({args.representation}), concurrency {args.concurrency}")

    fout = open(out, "a", encoding="utf-8")
    state = {"n": 0, "in": 0, "out": 0, "err": 0, "t0": time.time()}

    def work(rec):
        user = build_prompt(rec, args.representation)
        try:
            r = caller(client, args.model, SYSTEM, user, args.max_tokens)
        except Exception as e:                      # noqa: BLE001
            return {"block_id": rec["block_id"], "label": rec["label"],
                    "error": f"{type(e).__name__}: {e}"}
        return {
            "block_id": rec["block_id"], "label": rec["label"],
            "response": r["text"], "pred": parse_verdict(r["text"]),
            "in_tokens": r["in_tokens"], "out_tokens": r["out_tokens"],
            "latency_ms": round(r["latency_ms"], 1),
        }

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for res in ex.map(work, todo):
            fout.write(json.dumps(res) + "\n")
            fout.flush()
            state["n"] += 1
            if "error" in res:
                state["err"] += 1
            else:
                state["in"] += res["in_tokens"]
                state["out"] += res["out_tokens"]
            if state["n"] % 25 == 0:
                el = time.time() - state["t0"]
                print(f"    {state['n']:>6,}/{len(todo):,}  "
                      f"{state['in']:,} in / {state['out']:,} out tokens  "
                      f"{state['n']/el:.1f} req/s  {state['err']} errors")
    fout.close()

    # ---- results -------------------------------------------------------
    rows = [json.loads(l) for l in open(out, encoding="utf-8")]
    ok = [r for r in rows if "error" not in r and r.get("pred") is not None]
    unparsed = sum(1 for r in rows if "error" not in r and r.get("pred") is None)
    errors = sum(1 for r in rows if "error" in r)

    tot_in = sum(r.get("in_tokens", 0) for r in rows if "error" not in r)
    tot_out = sum(r.get("out_tokens", 0) for r in rows if "error" not in r)
    lats = sorted(r["latency_ms"] for r in rows if "latency_ms" in r)

    print()
    print("=" * 74)
    print(f"FRONTIER BASELINE  {args.provider}/{args.model}  ({args.representation})")
    print("=" * 74)
    print(f"  sessions scored : {len(ok):,}")
    print(f"  unparseable     : {unparsed:,}")
    print(f"  API errors      : {errors:,}")

    if ok:
        report(f"{args.model} ({args.representation})",
               [1 if r["label"] == "Anomaly" else 0 for r in ok],
               [r["pred"] for r in ok])

    print()
    print("  MEASURED COST AND LATENCY  (actual, not estimated)")
    print("  " + "-" * 70)
    n = max(len(ok), 1)
    print(f"    tokens: {tot_in:,} in + {tot_out:,} out")
    print(f"    per session: {tot_in/n:.0f} in + {tot_out/n:.1f} out")
    if lats:
        print(f"    latency ms: p50 {lats[len(lats)//2]:.0f}  "
              f"p90 {lats[int(len(lats)*.9)]:.0f}  p99 {lats[int(len(lats)*.99)]:.0f}")

    if args.price_in and args.price_out:
        cost = tot_in / 1e6 * args.price_in + tot_out / 1e6 * args.price_out
        per = cost / n
        print(f"    cost this run      : ${cost:,.4f}")
        print(f"    cost per session   : ${per:.6f}")
        print(f"    -> 575,061 sessions: ${per*575_061:,.2f}")
        print(f"    -> per 1M log lines: ${per*575_061/11.18:,.2f}")
        print()
        print("    (11.18M lines / 575,061 sessions. State prices and date in the")
        print("     writeup -- they change.)")
    else:
        print("    pass --price-in / --price-out for cost figures")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
