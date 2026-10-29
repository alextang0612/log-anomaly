# Week 4 — The frontier baseline, and where its performance actually comes from

**Setup:** 5,000 validation sessions, natural class ratio, 164 anomalies (3.28%).
Prompt developed on the training split only; verified on a held-out training
sample before any validation session was touched.

**Status:** Gate 2 passed. The frontier model is in the same table as everything
else, measured identically.

---

## 1. Headline

| Approach | Precision | Recall | F1 | Cost / 1M lines |
|---|---|---|---|---|
| TF-IDF + logistic regression | 0.9935 | 0.9973 | **0.9954** | ~$0 |
| **Rules v2** (hand-written) | 0.9164 | 0.9716 | **0.9432** | **~$0** |
| **Claude Haiku 5.5**, expert prompt | 0.9091 | 0.9756 | **0.9412** | **$8.15** |
| **Claude Sonnet 5.5**, expert prompt | 0.9091 | 0.9756 | **0.9412** | **$124.29** |
| Rules v1 (markers only) | 0.9055 | 0.8454 | 0.8744 | ~$0 |
| **Claude Sonnet 5.5**, bare prompt | 0.9630 | 0.7927 | **0.8696** | **$75.72** |
| Naive keyword rule | 1.0000 | 0.6688 | 0.8016 | ~$0 |
| Length only (`len < 13`) | 1.0000 | 0.2763 | 0.4330 | ~$0 |

**The frontier model places fourth** — behind a 1990s statistical technique and
behind forty lines of hand-written conditionals.

---

## 2. Finding 1 — the performance is the prompt, not the model

Three measurements isolate it:

| What the model was given | F1 | Cost / 1M lines |
|---|---|---|
| A bare instruction ("is this block broken?") | 0.8696 | $75.72 |
| The same instruction plus domain knowledge from EDA | 0.9412 | $124.29 |
| That domain knowledge expressed as code instead | **0.9432** | **~$0** |

Domain knowledge is worth **+0.072 F1**. The model contributes nothing beyond
it: given the same knowledge, conditional logic matches the frontier model and
marginally exceeds it, at no cost and in microseconds rather than 1.2 seconds.

A second edge: the expert prompt costs **64% more** than the bare one ($124.29 vs
$75.72) because the rules lengthen the prompt. You pay a premium to supply the
knowledge, and the knowledge turns out to be the only thing doing work.

### The bare prompt's failure mode is recall, not noise

Precision actually *rises* without domain knowledge (0.9630 vs 0.9091) while
recall collapses (0.7927 vs 0.9756). Untutored, the model is conservative — it
flags only unambiguous cases and misses 34 of 164 anomalies.

For an on-call detector that is the expensive direction to be wrong in. A false
positive costs an engineer five minutes; a missed anomaly is an outage nobody
was paged for.

---

## 3. Finding 2 — model capability is irrelevant here

Claude Sonnet 5.5 and Claude Haiku 5.5, given the same expert prompt, produced
**identical predictions on all 5,000 sessions** — not merely identical aggregate
metrics, but the same verdict on every individual block, including all 16 false
alarms and all 4 misses.

```
5000/5000 identical predictions
```

The models differ by roughly 15× in measured price and a wide margin in general
capability. On this task that difference buys nothing, because the prompt fully
determines the output. The model is an interpreter executing a decision
procedure, not a reasoner forming a judgement.

**Corollary for anyone building this for real:** if your LLM prompt is specific
enough to be reliable, it is probably specific enough to be code.

---

## 4. Cost and latency, measured

Real token counts from API responses; no estimates. Prices as of this run —
Sonnet 5.5 $2/$10 per MTok, Haiku 5.5 $0.10/$0.50 per MTok.

| | Input tok/session | Output tok/session | p50 latency | $ / session | $ / 1M lines |
|---|---|---|---|---|---|
| Sonnet, expert | 1,172 | 7.2 | 1,207 ms | $0.002416 | $124.29 |
| Sonnet, bare | 681 | 10.9 | 1,320 ms | $0.001472 | $75.72 |
| Haiku, expert | 1,172 | 82.6 | 1,005 ms | $0.000159 | $8.15 |

**Sticker price is not measured price.** Haiku's per-token rate is 20× below
Sonnet's, but it emitted **82.6 output tokens per session against Sonnet's 7.2** —
it reasons far more to reach the same answer. The realised saving is 15×, not
20×. This is the reason the harness records actual usage rather than estimating
from character counts.

### Latency makes the argument without reference to money

At a p50 of 1.21 s per session, classifying all 575,061 blocks takes **193 hours
of serial API time** — eight days to analyse 38 hours of logs from a single
cluster. At the concurrency this run sustained (~6.4 req/s), still **25 hours**.

The economic argument is that frontier inference at volume is expensive. The
latency argument is stronger: it is *impossible*. You cannot analyse a day of
logs in a day.

---

## 5. Methodology

**Prompt development used the training split only.** Iteration ran on 100
stratified training sessions. The resulting prompt was then validated on a
*different* 100 training sessions (different seed) before any validation data
was used.

This matters: the first tuned prompt scored 1.0000 on the sample it had been
tuned against — a meaningless number, and the same circularity a chronological
split exists to prevent, applied to prompts instead of weights. On fresh
training sessions it also scored 1.0000; on 5,000 validation sessions, 0.9412.

**Fairness was enforced in both directions.** The initial rules baseline received
five marker templates while the prompt received those plus a length rule,
truncation, re-replication and a lifecycle-ordering check. Comparing them
measured who was told more, not which approach was better. Rules v2 gives the
code exactly the signal set the prompt has; v1 is retained to show what the extra
signals bought (0.8744 → 0.9432).

**Stratified samples were used for development only.** Precision depends on class
ratio, so it is meaningless on a balanced sample. Recall is ratio-invariant and
remains valid. All reported numbers come from natural-ratio validation data.

**Prompts are versioned** in `prompts/` alongside the results each produced.

---

## 6. Limitations

| Limitation | Effect |
|---|---|
| Baselines scored on all 86,254 validation sessions; frontier on a random 5,000 subset. | Both are validation data and the subset is random, so estimates should agree within sampling error — but "measured identically" is the project's own standard and this does not quite meet it. |
| 164 anomalies gives recall a confidence interval of roughly ±3 points. | Differences smaller than that are not meaningful. The Rules v2 / Sonnet gap (0.9432 vs 0.9412) is **not** a real difference — the correct reading is "indistinguishable". |
| One prompt lineage. | A differently-structured prompt might do better. The bare/expert contrast bounds the knowledge contribution but not the prompt-engineering ceiling. |
| HDFS only, and HDFS is near-saturated (see week 2). | These conclusions are about a benchmark where every approach clusters near 0.94–0.99. BGL will test whether they hold where the task is harder. |

---

## 7. What didn't work

- **An 8-token output cap silently dropped 10% of responses** — and not at
  random. The dropped sessions were the ones the model reasoned longest about,
  i.e. the hard ones. Measured recall was 0.9318 over the easy 90%; the honest
  figure once all sessions parsed was 0.8800. Every metric looked clean and
  plausible throughout.
- **Tuning a prompt and scoring it on the same sample** produced a perfect 1.0000
  that meant nothing. Caught before it reached a results table, but only just.
- **Resume logic treated failed calls as completed.** After 1,005 consecutive
  billing errors, re-running skipped them silently. Required manually stripping
  error rows.
- **No abort on repeated failure.** When the credit balance ran out at call
  4,000, the run burned through the remaining 1,000 calls failing one at a time
  instead of stopping. A run that fails loudly on the first error is cheaper than
  one that fails politely a thousand times.
- **A dropped SSH session killed a 15-minute run.** Fixed by using tmux — which
  matters far more for week 6, where runs take hours.

---

## 8. Reproducing

```bash
python scripts/sample_sessions.py --split val --n 5000

python scripts/frontier_baseline.py --input data/samples/val_n5000.jsonl \
  --model claude-sonnet-5-5 --max-calls 5000 --max-tokens 1024 \
  --concurrency 8 --price-in 2 --price-out 10

python scripts/frontier_baseline.py --input data/samples/val_n5000.jsonl \
  --system-prompt prompts/system_naive.txt --out data/frontier/val_n5000_naive.jsonl \
  --model claude-sonnet-5-5 --max-calls 5000 --max-tokens 1024 \
  --concurrency 8 --price-in 2 --price-out 10

python scripts/frontier_baseline.py --input data/samples/val_n5000.jsonl \
  --out data/frontier/val_n5000_haiku.jsonl \
  --model claude-haiku-5-5 --max-calls 5000 --max-tokens 1024 \
  --concurrency 8 --price-in 0.10 --price-out 0.50

python scripts/baselines.py
```

Total API spend: approximately $21.

---

## 9. What this changes

Nothing in the plan — it confirms it. The week-2 conclusion was that HDFS cannot
settle an accuracy argument and should be used to measure cost at controlled
accuracy. That is exactly what week 4 did, and the result is sharper than
expected: the frontier model is not "slightly worse but much more expensive," it
is **matched by conditional logic and beaten by logistic regression**.

The open question remains whether this survives on a harder dataset. Week 5 runs
the saturation diagnostics on BGL before committing to it.
