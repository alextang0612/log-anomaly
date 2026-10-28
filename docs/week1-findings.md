# Week 1 — Data, parsing, and what an anomaly actually looks like

**Dataset:** HDFS_v1 (LogHub / LogPAI) · 11,175,629 lines · 575,061 blocks · 2.93% anomalous
**Parser:** Drain3, `sim_th=0.85`, custom masking · 31 templates
**Status:** Week 1 gate passed.

---

## 1. What this phase produced

| Artifact | What it is |
|---|---|
| `data/processed/templates.csv` | 31 log templates with counts and example lines |
| `data/processed/sessions.csv` | 575,061 rows: `block_id, label, first_ts, length, sequence` |
| `data/processed_th085/` | Alternate parse at fine exception granularity (52 templates), kept for the week-9 ablation |

`sessions.csv` is the actual dataset for everything downstream. Each row is one
block's lifecycle as a sequence of integer template IDs.

`first_ts` (each block's earliest log timestamp) is recorded now specifically so
the week-2 chronological split does not require reparsing 11.2M lines.

---

## 2. Parsing methodology

### Masking before template mining

Raw log lines are almost all unique — block IDs, IPs, ports and sizes differ on
every line. Without masking, Drain3 treats each distinct value as a distinct
token and produces thousands of near-duplicate templates.

Masks applied, in order:

| Pattern | Replaced with | Why |
|---|---|---|
| `blk_-?\d+` | `<BLK>` | Block IDs are the join key, not event identity |
| `java\.[\w.$]+(?:Exception\|Error)\b.*` | `<EXCEPTION>` | See §2.3 |
| IPv4 (+ optional port) | `<IP>` | Host identity is not event identity |
| Filesystem paths | `<PATH>` | |
| Bare integers | `<NUM>` | Sizes, offsets, responder numbers |

Ordering matters: `<IP>` must be applied before `<PATH>`, or `/10.250.19.102:50010`
is consumed by the path rule and the endpoint/path distinction is lost.

### Similarity threshold — tuned, not defaulted

Drain3's default `sim_th=0.4` produced a **silent, damaging merge**:

```
PacketResponder <NUM> for block <BLK> <*>     ← 1,706,728 lines
```

The trailing wildcard concealed two semantically different events:

```
1,706,679  terminating     (normal completion)
       49  Interrupted.    (failure)
```

Both were assigned the same template ID, so 49 failure events were
indistinguishable from 1.7M successful completions.

**Why it merged:** after masking, the two messages are six tokens each and differ
in one — similarity 5/6 ≈ 0.83, comfortably above the 0.4 threshold.

**Why raising the threshold was safe:** masking had already removed the genuine
variance, so most templates match at similarity 1.0. Raising `sim_th` to 0.85
only affected positions where Drain3 itself had guessed. Verified: the ten
highest-frequency templates were byte-identical before and after, with identical
counts.

This is worth stating plainly — **nothing in the summary statistics would have
revealed this merge.** Block count, anomaly rate, and label join were all exactly
correct in the broken parse. Only reading the templates caught it.

### Exception granularity — a deliberate choice

At `sim_th=0.85` with no exception masking, the parse produced 52 templates, of
which ~20 were exception subtypes:

```
writeBlock <BLK> received exception java.io.IOException: Broken pipe          5 lines
writeBlock <BLK> received exception java.net.NoRouteToHostException: ...      1 line
writeBlock <BLK> received exception java.nio.channels.ClosedByInterrupt...    7 lines
...
```

These were collapsed into `writeBlock <BLK> received exception <EXCEPTION>`.

**Rationale — this is an out-of-vocabulary decision, not a compression one.** A
template occurring once in 11.2M lines cannot produce a meaningful learned
embedding. Worse, at inference time a Java exception class absent from training
data becomes an *unseen template with no ID*. Grouping dissolves that failure
mode: any novel exception still matches the general template.

The principle is the same one applied to IP addresses — the exception class is a
**parameter** of the event, not a different event.

**Validation against the reference parse.** HDFS_v1 ships `HDFS.log_templates.csv`
(29 templates) from the dataset authors. It independently confirms both decisions:

- It keeps `PacketResponder ... terminating` (E11) and `PacketResponder ...
  Interrupted` (E8) as **separate** templates — agreeing with the `sim_th` fix.
- It groups all exception subtypes into single templates (E7, E10, E14) —
  agreeing with the `<EXCEPTION>` mask.

Final count 31 vs the reference's 29; the remaining difference is variable-arity
events (replication to one datanode vs two), which this parse keeps separate.

### Parse integrity

| Check | Result |
|---|---|
| Lines matching expected header format | 11,175,629 / 11,175,629 (100%) |
| Lines containing a block ID | 11,175,629 (100%) |
| Blocks recovered | 575,061 — exact match to label file |
| Blocks without a label | 0 |
| Anomaly rate | 2.93% — matches published rate |

---

## 3. What distinguishes an anomalous block

> **An anomalous HDFS block is one whose write lifecycle does not complete
> cleanly.** Normal blocks are near-identical: ~19 events, following
> allocate → receive → respond → addStoredBlock → delete. Anomalies break that
> pattern in one of three ways — the sequence truncates early, the block completes
> but then requires re-replication or transfer, or the write path throws an
> exception. Notably, not every error message indicates an anomaly.

### 3.1 Length is bimodal — and the median hides it

| | min | p25 | p50 | p75 | p90 | p99 | max |
|---|---|---|---|---|---|---|---|
| Normal | **13** | 19 | 19 | 20 | 25 | 32 | 298 |
| Anomaly | **2** | **4** | 20 | 26 | 30 | 42 | 284 |

Normal blocks are remarkably uniform — p25, p50 and p75 all fall within 19–20.

Anomalies split into two populations: a truncated group (p25 = 4) and a
longer-than-normal group (p75 = 26, from replication retries).

**The medians are nearly identical (19 vs 20), so a median-based check reports
"length is not a useful signal." That conclusion is wrong.** No normal block has
fewer than 13 events, so `length < 13` is a near-zero-false-positive rule
capturing roughly a quarter of all anomalies.

*Consequence for week 2:* include an explicit length-only baseline. Any model
result needs to be measured against it, or part of the score may be length in
disguise.

### 3.2 Positive indicators

Share of blocks in each class containing the template at least once:

| id | Template | Normal | Anomaly |
|---|---|---|---|
| 25 | `Unexpected error trying to delete block <BLK>. BlockInfo not found...` | 0.04% | **30.4%** |
| 6 | `Received block <BLK> src: <IP> dest: <IP> of size <NUM>` | 0.32% | 20.6% |
| 7 | `<IP>:Transmitted block <BLK> to <IP>` | 0.32% | 20.6% |
| 11 | `BLOCK* ask <IP> to replicate <BLK> to datanode(s) <IP>` | 0.32% | 20.3% |
| 12 | `<IP> Starting thread to transfer block <BLK> to <IP>` | 0.32% | 20.3% |
| 14 | `writeBlock <BLK> received exception <EXCEPTION>` | **0.00%** | 19.6% |
| 26 | `addStoredBlock request received ... But it does not belong to any file` | 0.00% | 7.4% |
| 19 | `Redundant addStoredBlock request received for <BLK>` | 0.00% | 5.8% |

**Templates 6, 7, 11 and 12 are one causal chain, not four independent signals.**
Their rates are nearly identical because they always co-occur: ask to replicate →
start transfer thread → transmit → receive. Treat as a single feature.

### 3.3 Absence indicators — failure by omission

| id | Template | Normal | Anomaly |
|---|---|---|---|
| 3 | `PacketResponder <NUM> for block <BLK> terminating` | **100%** | 63.3% |
| 4 | `Received block <BLK> of size <NUM> from <IP>` | **100%** | 63.1% |
| 5 | `BLOCK* NameSystem.addStoredBlock: blockMap updated: ...` | **100%** | 63.1% |
| 16 | `Deleting block <BLK> file <PATH>` | 81.7% | 61.7% |
| 24 | `BLOCK* NameSystem.delete: <BLK> is added to invalidSet of <IP>` | 81.5% | 61.4% |

Roughly **37% of anomalous blocks never complete the write lifecycle** — these are
almost certainly the same truncated blocks identified in §3.1. The signal is what
is *missing*, not what is present.

### 3.4 Two templates carry no signal at all

Templates 1 (`Receiving block ... src ... dest`) and 2 (`allocateBlock`) appear in
**100% of both classes**. Every block begins identically. They contribute nothing
to classification.

### 3.5 A false friend

Template 27, `<IP>:Got exception while serving <BLK> to <IP>:`, appears in **22.6%
of normal blocks and 18.9% of anomalous ones** — slightly *negative* signal despite
being an error message and occurring 356,207 times.

A keyword-based baseline that treats "exception" as an anomaly indicator will fire
on this constantly. Useful for interpreting the week-2 regex baseline's precision.

---

## 4. Implications for week 2

1. **Add a length-only baseline.** `length < 13` alone is expected to be
   high-precision. Whatever the neural model scores must be compared against it.
2. **The regex baseline should be built from evidence, not intuition.** Templates
   25, 14, 26 and 19 are near-perfect indicators; "exception" as a bare keyword is
   not (§3.5).
3. **Expect TF-IDF + LR to be strong.** Several templates are near-perfect
   single-feature discriminators, and the absence signals are simple presence
   features. This is the bar, and it is a high one.
4. **Chronological split uses `first_ts`,** already recorded per block.

---

## 5. Open questions and planned ablations

| Question | How it gets answered |
|---|---|
| Does **order** add anything over **presence**? | Week 9: sequence model vs bag-of-templates on identical features. This is the project's central empirical question — several strong indicators are presence-only, so the sequence model has to earn its place. |
| Coarse vs fine exception granularity | Week 9: `data/processed/` (31 templates) vs `data/processed_th085/` (52). Hypothesis: coarse wins, because fine templates are too sparse to learn and create OOV exposure at test time. |
| Do truncated blocks dominate the anomaly class? | Cross-tabulate `length < 13` against the absence indicators. If they are the same blocks, the effective anomaly taxonomy has two classes, not one. |
| Should the four replication templates be merged? | They are perfectly collinear (§3.2); merging may reduce noise without losing signal. |

---

## 6. Reproducing

```bash
python scripts/parse_logs.py                  # ~1.1 min, 175k lines/s
python scripts/inspect_templates.py           # manual review gate
python scripts/explore_sessions.py            # EDA in this document
```

---

## 7. What didn't work

- **Trusting Drain3's default threshold.** `sim_th=0.4` silently merged a failure
  event into a success event. Every summary statistic looked correct. Only manual
  template review caught it.
- **Trusting a median-based length check.** The automated EDA heuristic compared
  medians (19 vs 20) and concluded length was uninformative. The distribution is
  bimodal and length is in fact one of the strongest available signals. Comparing
  full distributions, not point estimates, would have caught this immediately.
- **Environment setup consumed most of the phase.** A full Windows disk caused an
  ext4 read-only remount mid-install (`df` inside WSL reports the virtual disk's
  nominal size, not the host's real free space), and the GPU driver predated the
  CUDA version PyTorch shipped against.

---

## Citation

Datasets from LogHub / LogPAI. Cite:

- Wei Xu, Ling Huang, Armando Fox, David Patterson, Michael Jordan. *Detecting
  Large-Scale System Problems by Mining Console Logs.* SOSP 2009.
- Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. *Loghub: A Large
  Collection of System Log Datasets for AI-driven Log Analytics.* ISSRE 2023.
