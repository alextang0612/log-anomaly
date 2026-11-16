# Week 5 — BGL, and checking it before committing to it

## In plain terms

HDFS turned out to be too easy to tell good models from bad ones, so this week
added a second dataset: BGL, logs from a Blue Gene/L supercomputer. Before
spending the next four weeks on it, the job was to check whether BGL is *also*
too easy.

It isn't. On HDFS, memorising past examples answers 92% of questions correctly.
On BGL, the same trick answers almost none — every failure looks structurally
new. The best "cheat" we could find scores 0.36 where the HDFS equivalent scored
0.99.

**Decision: BGL is where the model gets built and measured.**

One catch, deferred to week 6: BGL's windows vary from 1 event to 129,607. That
range has to be dealt with before a model can consume them.

---

## 1. The windowing decision, and why it isn't arbitrary

BGL labels individual **messages**, not sessions. HDFS came with a natural unit
(the block); BGL does not, so sequences have to be constructed, and the choice
determines whether the task is hard.

Landauer et al. (FSE 2024) measured the options directly:

| Grouping | New-event detector | DeepLog |
|---|---|---|
| Sequence identifier | F1 0.988 | F1 0.989 |
| 30-minute time windows | F1 0.680 | F1 0.682 |
| 30-min windows, chronological | **F1 0.470** | — |

Grouped by identifier, BGL is trivially solvable: normal and anomalous event
types are nearly disjoint sets, so "flag any event type not seen before" scores
0.988 and a deep model beats it by 0.001. That configuration cannot distinguish
architectures — the same dead end HDFS turned out to be, reached by a different
route.

**30-minute windows, chronologically ordered, is the configuration where simple
methods collapse.** That is what this project uses, and it is why.

Reading that paper before writing code saved roughly a week of discovering the
same thing empirically.

---

## 2. Parse results

```
lines parsed            4,747,963
unparsed                        0
alert lines               348,460  (7.34% — matches the published rate exactly)
templates                     409
windows (30 min)            5,776
anomalous windows             760  (13.16%)
```

Window-level anomaly rate (13.16%) is higher than the message-level rate (7.34%)
because a window is anomalous if *any* message in it is an alert, and alerts
cluster.

### The chronological split

| split | windows | anomalies | rate | time range (unix) |
|---|---|---|---|---|
| train | 4,043 | 569 | 14.07% | 1117838570 → 1130638431 |
| val | 866 | 93 | 10.74% | 1130640672 → 1132972325 |
| test | 867 | 98 | 11.30% | 1132975527 → 1136390405 |

Non-overlap asserted in code, as on HDFS. The rate varies across splits, which
is real — failures cluster in time — and is left alone.

---

## 3. The saturation gate

| Diagnostic | HDFS | BGL (30-min, chronological) |
|---|---|---|
| Unique training sequences | 4.7% | **72.1%** |
| Verbatim overlap into val | 99.2% | **33.7%** |
| Lookup baseline F1 | 0.9193 | **0.0213** |
| New-event-type baseline F1 | — | **0.3600** |
| Novelty lift over base rate | 20× | 1.5× |

**The lookup baseline is the clearest contrast.** On HDFS it scored 0.9193 with
perfect precision — memorisation nearly solved the task. On BGL it catches one
anomaly out of 93, because **anomalous windows are structurally novel every
time**. Nothing repeats.

Novelty does not rescue it either: an unfamiliar BGL sequence is only 1.5× more
likely to be anomalous, against 20× on HDFS. And the new-event-type detector
that scores 0.988 under identifier grouping scores **0.360** here — a single
comparison that justifies the windowing choice better than argument could.

Measured 0.36 sits slightly below the paper's 0.47, and well under the >0.90
figure most published work reports for this configuration. That gap is the
tripwire: **if the trained model lands far above 0.90, suspect leakage before
celebrating.**

### Robustness

The gate was run against two different template vocabularies (437 and 409
templates, before and after the masking fix below). The new-event-type baseline
was identical at 0.3600 both times. The conclusion does not depend on parsing
granularity.

---

## 4. The masking took three attempts, and the first two failures were invisible

| Attempt | Rule | Result |
|---|---|---|
| 1 | mask any 8+ char `[0-9a-fA-F]` run as `<HEX>` | `1347195 double-hummer alignment exceptions` → `<NUM>`, `12364800 double-hummer alignment exceptions` → `<HEX>`. **One event split into two templates by the magnitude of its counter** (175k and 121k lines). |
| 2 | require at least one letter a–f for `<HEX>` | Fixed the counter split, broke address fields instead: `iar 003a90fc` → `<HEX>` but `iar 12364800` → `<NUM>`. Template count rose 417 → 437. |
| 3 | mask every bare 8+ char run as `<NUM>`, keep `<HEX>` only for `0x`-prefixed | Both renderings collapse. Templates 437 → 409. |

The underlying problem is a variable whose *rendering* varies. The only stable
answer is to give every rendering the same mask — the same reasoning used to
group Java exception subtypes in the HDFS parser.

**All three parses reported identical summary statistics**: 4,747,963 lines,
0 unparsed, 7.34% alert rate matching the published figure. Every integrity
check passed while one event sat split across two templates.

This is the third time in this project that only reading the output caught a
silent error — after the HDFS `PacketResponder` merge in week 1 and the
frontier-model output cap in week 4. The pattern is consistent enough to state
as a finding: **in log parsing, aggregate statistics cannot detect semantic
errors. A human has to read the templates.**

---

## 5. Deferred to week 6: window length

```
window length:  min 1   p50 20   p90 844   p99 19,912   max 129,607
```

A 1,000× spread. Most windows are nearly empty; a few contain an entire burst.
A transformer cannot attend over 129,607 events, so one of these is required:

| Option | Cost |
|---|---|
| Truncate to first N events | Loses the tail of bursts — plausibly where the signal is |
| Shorter time windows | More windows, less context each, and breaks comparability with the 30-min literature |
| Pool / downsample long windows | Loses ordering information inside the pooled region, which is the thing a sequence model exists to use |

Each option costs something real, and the bursts are plausibly where the
anomalies live. **This is the most consequential design decision in the project
so far** and must be made deliberately, documented, and ideally measured more
than one way.

---

## 6. Reproducing

```bash
# BGL.zip from https://zenodo.org/records/8196385 -> data/raw/
python scripts/parse_bgl.py
python scripts/inspect_templates.py --templates data/bgl_processed/templates.csv
python scripts/make_split.py --sessions data/bgl_processed/sessions.csv --out data/bgl_splits
python scripts/saturation_check.py --splits data/bgl_splits --templates data/bgl_processed/templates.csv
```

`parse_bgl.py` emits the same schema as the HDFS pipeline, so `make_split.py`,
`baselines.py`, `explore_sessions.py` and `saturation_check.py` all work on BGL
unchanged.

## Reference

Landauer, Onder, Skopik, Wurzenberger. *A Critical Review of Common Log Data
Sets Used for Evaluation of Sequence-Based Anomaly Detection Techniques.*
FSE 2024. https://dl.acm.org/doi/10.1145/3660768
