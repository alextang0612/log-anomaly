# Revised roadmap — weeks 3–16

**Supersedes Part 2 of the project handbook.** Written after week 2 found that
HDFS cannot settle an accuracy argument (see `docs/week2-findings.md`).

---

## The change in one paragraph

The original plan used one dataset to answer two questions at once: *is my model
accurate* and *is my model cheap*. HDFS turned out to be saturated — a dictionary
scores 0.92, logistic regression 0.995 — so it can no longer answer the first
question. Rather than abandon it, the two questions are split across two datasets.
**HDFS measures cost at controlled accuracy. BGL measures whether architecture
matters.** One pipeline, two measurements. The machinery is written once.

## The two claims the project now makes

> **1. (HDFS)** Every approach reaches ~0.99, so accuracy is held constant by the
> data itself. Under that control, local inference is ~3 orders of magnitude
> cheaper than a frontier model. The benchmark is also near-saturated, which is
> worth reporting.
>
> **2. (BGL)** On a harder dataset where the ceiling is not pinned, here is what
> model design actually buys — and what it costs.

---

## Timeline

| Weeks | Phase | Gate to continue |
|---|---|---|
| 3–4 | Frontier baseline on HDFS (cost study) | Measured cost, latency, F1 — same table as the other baselines |
| 5 | **BGL data + saturation check** ← moved up | **BGL is demonstrably not a lookup problem** |
| 6 | Shared tensor pipeline + first model | Overfits 50 sessions; a real run completes |
| 7 | Class imbalance, threshold as a decision | Three imbalance strategies compared on val |
| 8 | Model size sweep | Accuracy-vs-cost curve on both datasets |
| 9 | Ablations and model selection | **Sequence vs presence, answered with evidence** |
| 10–12 | Throughput (the CUDA phase) | Before/after with traces + accuracy cost of each step |
| 13–16 | Ship and write up | A stranger clicks a link, sees it work, reads a number |

Gates remain hard stops.

---

## Phase 1 — Frontier baseline on HDFS (weeks 3–4)

Purpose has changed. This is **not** an attempt to find a number to beat on
accuracy; HDFS has no headroom. It is the cost anchor the entire thesis rests on.

### Week 3 — Prompt design
- Choose the representation: raw lines, or parsed templates? Templates are
  cheaper in tokens; raw lines are a fairer test of the model. Measure both on
  100 sessions and report the choice.
- Write a real prompt with few-shot examples. Iterate until genuinely good.
- **Give it a fair shot.** A rigged baseline destroys credibility, and the cost
  gap will be dramatic regardless.
- Use the week-1 sentence ("an anomalous block is one that…") as the basis for
  the prompt. That is what it was written for.

### Week 4 — Measure
- Run on 3,000–5,000 held-out sessions.
- Record **actual token counts returned by the API**, per call, input and output.
  Never estimate.
- Extrapolate to 575,061 sessions and to 1M / 11.2M lines.
- Record per-item latency, and throughput at realistic concurrency.
- **Also time the other baselines on the same sessions** — lookup, TF-IDF, rules.
  Comparability requires one clock.
- Budget $10–30.

**GATE 2:** the frontier model appears in the same table as every other approach,
measured identically, with cost and latency columns populated.

---

## Phase 1.5 — BGL and the saturation check (week 5) ← NEW

**Do this before committing five weeks to BGL.** HDFS looked fine until it was
checked too.

- Read Landauer et al. (FSE 2024) first — it analyses duplicate sequences across
  these benchmarks and may answer the question before any code runs.
- Download BGL (~4.7M messages, 7.34% anomalous, labelled **per message**).
- Parse with the existing Drain3 pipeline. **Retune the similarity threshold for
  BGL** — the 0.85 chosen for HDFS has no authority here. Inspect templates
  manually, same gate as week 1.
- **BGL needs a windowing decision HDFS did not.** Labels are per message, not
  per session, so sequences must be constructed: fixed-size windows, sliding
  windows, or time-based windows. This choice materially affects results and
  must be justified in writing.
- Run the week-2 diagnostics unchanged: distinct sequence count, verbatim
  overlap, lookup baseline, novelty rate.

**GATE 2.5 — the decision point:**

| Outcome | Action |
|---|---|
| BGL is **not** saturated | Proceed. BGL becomes the accuracy study. |
| BGL **is** also saturated | Do not grind. The project becomes a cost study plus a benchmark critique across two datasets — a legitimate and more interesting result. Rewrite the claims and skip to Phase 3. |

---

## Phase 2 — Train the model (weeks 6–9)

### Week 6 — Pipeline and first model
- Sessions → template-ID sequences → tensors. Shared code path for both datasets.
- **Unseen-template handling is the central design decision, not a detail.**
  Week 2 showed novelty is where all the residual signal lives — 57.7% of novel
  HDFS sequences are anomalous against a 2.93% base rate. Options: a reserved
  `<UNK>` token, nearest-template matching, or an explicit novelty flag as a
  feature. Try more than one and report the difference.
- Small transformer encoder, 2–4 layers.
- **Overfit 50 sessions to near-zero loss before anything else.** If it will not,
  the pipeline is broken and finding that now saves a week.

### Week 7 — Imbalance and threshold
- Class weighting, focal loss, considered resampling. Run all three, report the
  difference — that is a legitimate ablation.
- Tune the decision threshold **on val, never on test**.
- Where you sit is a business decision: false positives burn on-call attention,
  false negatives miss outages. State the choice and why.

### Week 8 — Size sweep
- Train at ~4 sizes (2M / 10M / 30M / 60M parameters).
- Record accuracy **and** inference cost for each, on both datasets.
- The resulting curve is the money artifact.

### Week 9 — Ablations and selection

The central one, run on **both** datasets:

| Ablation | Question |
|---|---|
| **Sequence model vs bag-of-templates** | Does order add anything over presence? HDFS predicts no. BGL is the real test. |
| Unseen-template strategy | Which novelty handling wins, and by how much? |
| Coarse vs fine exception templates | 31-template vs 52-template parse (both already generated in week 1) |
| Sequence length / depth | Standard capacity sweep |

**GATE 3 (rewritten).** The original was *beat TF-IDF + LR*. That is unachievable
on HDFS and the wrong target. The gate is now:

> You can state, with measured evidence from both datasets, **when a learned
> sequence model is worth its cost and when it is not.**

A result showing the transformer loses on both datasets passes this gate, as long
as it is measured properly and reported honestly.

**Checkpoint:** if behind schedule, stop here. There is already a real result.

---

## Phase 3 — Throughput (weeks 10–12)

Unchanged in substance. Benchmark primarily on **HDFS** — 11.2M lines gives the
better throughput story than BGL's 4.7M.

- **Week 10:** int8 quantisation, ONNX export. Measure the accuracy cost of every
  step. "3× faster" is meaningless without "and F1 dropped 0.4 points."
- **Week 11:** fix batching first, then profile with Nsight Systems. Expect the
  bottleneck to be CPU-side parsing and preprocessing, not the model.
- **Week 12:** attack whatever the profiler exposed. Optionally hand-write a CUDA
  kernel for the hot path — and **if the library version wins, report that.**

**Three rules for cost numbers, or the headline claim is not defensible:**

1. **Include preprocessing.** Drain3 parsing is real CPU cost. Most papers omit
   it. Yours will not.
2. **Report batch size.** Per-item latency and batched throughput differ by a
   large factor; quoting the best of each inflates results.
3. **Price against a named, rentable instance** with the date quoted. The 3060 Ti
   is not rentable — benchmark on it, price against a comparable cloud GPU, and
   say so.

**GATE 4:** before/after throughput with traces, plus the accuracy cost of every
optimisation step.

---

## Phase 4 — Ship and write up (weeks 13–16)

- **Week 13:** FastAPI service. Health checks, structured logging, Prometheus
  metrics. Home turf — make it good.
- **Week 14:** dashboard. **Hard timebox to one week.**
- **Week 15:** containerise, IaC, public URL.
- **Week 16:** the writeup.

### The two headline tables

**Table 1 — HDFS: cost at controlled accuracy**

| | Lookup | Rules | TF-IDF + LR | Frontier model | Your model |
|---|---|---|---|---|---|
| F1 (anomaly) | 0.9193 | 0.8744 | 0.9954 | | |
| PR-AUC | — | — | 0.9997 | | |
| Cost / 1M lines | | | | | |
| Latency / item | | | | | |
| Throughput | | | | | |

*Accuracy is roughly constant by construction. Read the cost row.*

**Table 2 — BGL: does architecture matter**

Same rows. Here the F1 column is the interesting one.

### Writeup structure

1. The pitch paragraph, unchanged
2. Architecture diagram above the fold
3. **The saturation finding** — why one dataset was not enough, with the lookup
   baseline as evidence. This is the credibility anchor; lead with it.
4. Table 1 (cost), Table 2 (accuracy)
5. The size-vs-cost curve
6. Methodology: chronological split, threshold tuning, windowing choice
7. **"What didn't work"** — experienced reviewers read it first

---

## If you fall behind

Cut in this order:

1. The custom CUDA kernel — quantisation and batching already carry the story
2. The dashboard → a static results page plus a demo video
3. The size sweep → two sizes instead of four
4. The BGL *optimisation* work — keep BGL accuracy results, benchmark throughput
   on HDFS only

**Never cut:** the chronological split, the classical baselines, the lookup
baseline, the BGL saturation check, or the writeup. Those are what make the work
credible rather than impressive-looking.

> **Note on what changed in the cut list.** BGL was previously first to cut. It is
> now protected, because without it the project has no answer to "does your model
> design matter?" What became cuttable is BGL's *optimisation* work, not its
> existence.

---

## Definition of done

- [ ] Live URL, or demo video if hosting a stream is impractical
- [ ] Architecture diagram above the fold
- [ ] Two comparison tables, every row measured identically
- [ ] Cost-versus-accuracy curve
- [ ] Saturation analysis with the lookup baseline, prior work cited
- [ ] Reproducible eval code; split methodology documented
- [ ] "What didn't work," written honestly
- [ ] The pitch paragraph, unchanged, at the top

**The 90-second test:** a link to click, a diagram above the fold, a number, and
one sentence saying why anyone should care.
