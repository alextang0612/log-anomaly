# Log Anomaly Detection — Project Handbook
### Everything in one place · RTX 3060 Ti (8 GB), Windows + WSL2 · Aug 2026

*Self-contained. You shouldn't need any chat history to work from this.*

---

## The pitch

**One sentence:**

> I'm training a small AI model to spot failures in server logs — the same job a big model like ChatGPT could do, but cheap enough to actually run on millions of lines.

**The paragraph:**

> Servers produce millions of log lines a day, and buried in them are the handful that mean something is broken. A big AI model like ChatGPT can spot those reliably, but it's far too slow and expensive to run across millions of lines, so nobody actually does it that way. I'm training my own small model to do that one narrow job, then measuring it head to head against the big model — how much accuracy do I give up, and how much cheaper and faster is it? I'm doing it because my job right now is plugging existing AI models into things, and I want to understand how they're actually built: the training, the data, the tradeoffs. When it's done I'll have a working system with real numbers attached, which says a lot more about what I can do than "I've used the OpenAI API."

Keep this at the top of your README. Every decision below should serve it.

**The thesis:** the frontier model is the baseline you beat, not a teacher you copy.

---

## Why this project

- **The GPU is load-bearing.** Throughput is the product, so optimization work is real rather than decorative.
- **It's model creation, not integration** — which is the gap on your resume.
- **The moat is cost at volume.** You cannot afford an LLM call per log line. Nobody can. That's the whole argument, and it means "why not just use Claude?" has a real answer.
- **It's your domain**, so you can tell whether an answer is right.

**What it won't do, honestly:** it won't generate interviews — it wins the ones you get. Non-technical recruiters won't understand it; engineers will. It won't make you an ML researcher. And the headline speedup number won't impress anyone on its own — everyone in the field knows distillation works. **The rigor is the differentiator, not the multiple.**

---

## The data

All public, free, no employer access needed.

| Dataset | Size | Anomaly rate | Labels |
|---|---|---|---|
| **HDFS** | ~11.2M entries | 2.93% of blocks | per session (`block_id`) |
| **BGL** | ~4.7M entries | 7.34% of messages | per message |

From [LogHub / logpai](https://github.com/logpai/loghub); large files on Zenodo, linked in the repo README. Free for research/academic use — cite the LogPAI paper in your README.

Start with **HDFS**. Add BGL later to show the approach generalizes.

**Why public data beats company logs here:** it's labeled (hand-labeling 11M lines is not a hobby), published papers report results on these exact datasets so your numbers are comparable to peer-reviewed baselines, there's no compliance conversation, and reviewers can reproduce your work.

---

# Part 1 — Environment setup

Windows PC with the GPU. Half a day, including the snags below.

## 1.1 Optional: remote access from a laptop

Worth doing — being able to start a run without sitting at the desk is what keeps the project alive on low-energy evenings.

1. Install **Tailscale** on both machines, sign into the same account on each. No router config needed.
2. Enable OpenSSH Server on Windows.
3. SSH in from the laptop; connect VS Code or Cursor via Remote-SSH.

**Snags worth pre-empting:**

- **Disable sleep on the GPU box.** It suspending and dropping off the network causes more confusion than every other step combined.
- Set the `sshd` service to **Automatic** (`Set-Service -Name sshd -StartupType Automatic`) or it won't survive a reboot.
- **Disable Tailscale key expiry** for that machine, or it silently drops in 180 days.
- If OpenSSH Server via Optional Features hangs for more than a few minutes, it's stuck, not slow. Cancel and use PowerShell instead.

## 1.2 BIOS — enable virtualization

WSL2 requires hardware virtualization. There is no software workaround.

**Check first:** Task Manager → Performance → CPU → bottom right, "Virtualization".

If it says **Disabled**:

1. Restart, press **Del** during boot (most desktop boards) or **F2**
2. Find the setting:
   - **AMD** → `SVM Mode`. On Gigabyte: **Tweaker → Advanced CPU Settings → SVM Mode**
   - **Intel** → `Intel (VMX) Virtualization Technology`. On Gigabyte: **Settings → Miscellaneous**
3. Set to **Enabled**, **F10** to save and exit
4. **Full power off — not a restart.** Power down, wait a few seconds, power on. Virtualization changes often don't take through a warm reboot, and this costs people an hour.
5. Confirm Task Manager now reads **Enabled**

Where it hides by brand:

| Brand | Location |
|---|---|
| Gigabyte | Tweaker → Advanced CPU Settings (AMD) / Settings → Miscellaneous (Intel) |
| ASUS | F7 for Advanced Mode → Advanced → CPU Configuration |
| MSI | OC or Advanced → CPU Features |
| ASRock | Advanced → CPU Configuration |
| Dell / HP / Lenovo prebuilt | often under **Security**, not Advanced |

SVM Mode is safe to leave enabled permanently — no performance cost for normal use or gaming.

## 1.3 Install WSL2 + Ubuntu

PowerShell **as Administrator**:

```powershell
wsl --update
wsl --install -d Ubuntu
```

Reboot: 2–5 min. First launch of Ubuntu then unpacks the distro (1–5 min) and asks for a UNIX username and password — Linux-only, unrelated to your Windows login. Pick something short, you'll type it for every `sudo`.

It sits on "Installing, this may take a few minutes..." long enough to look frozen. It isn't.

**If `wsl --list --verbose` says "has no installed distributions":** WSL installed but never pulled the distro. Run `wsl --install -d Ubuntu` again, or check `wsl --list --online` for the exact name (sometimes `Ubuntu-24.04`).

**Ubuntu's metrics collection prompt:** doesn't matter either way. Decline to keep the box quiet.

### Mental model

**WSL is not dual-boot.** The PC stays Windows; Ubuntu runs inside it, like an app. You reboot back into Windows as normal.

To get into Ubuntu: Windows key → type "Ubuntu" → Enter. Or type `wsl` in Windows Terminal / PowerShell.

How to tell where you are:

```
PS C:\Users\Alex>          ← Windows
alex@DESKTOP-XYZ:~$        ← Ubuntu
```

`exit` drops you back to Windows. Both run side by side.

## 1.4 Verify the GPU

In Ubuntu:

```bash
nvidia-smi
```

You want **NVIDIA GeForce RTX 3060 Ti**, roughly **8192MiB** memory, and a CUDA Version in the top right. Idle readings like `48C / P8 / 17W` are normal and healthy.

> ⚠️ **Do not install an NVIDIA driver inside WSL.** The Windows driver already exposes the GPU to Linux. Installing a second one inside WSL is the most common way to break CUDA here, and the failure mode is confusing. If `nvidia-smi` fails, diagnose — don't install a driver.

## 1.5 Python and PyTorch

**Skip the CUDA toolkit for now.** PyTorch's pip wheels bundle their own CUDA runtime. You only need the full toolkit for compiling custom kernels and Nsight profiling — that's Phase 3, weeks 10–12. Installing it today is an hour of pain for something two months away.

```bash
sudo apt update && sudo apt upgrade -y          # 2–5 min, lots of scrolling

curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
uv --version

mkdir -p ~/projects/log-anomaly && cd ~/projects/log-anomaly
uv venv --python 3.11
source .venv/bin/activate
uv pip install torch                             # ~2–3 GB, 3–10 min
```

**If `uv: command not found` persists**, don't fight it — uv is a convenience, not a requirement:

```bash
sudo apt install -y python3-venv python3-pip
cd ~/projects/log-anomaly
python3 -m venv .venv
source .venv/bin/activate
pip install torch
```

### The gate

```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Want: `True NVIDIA GeForce RTX 3060 Ti`

If `False`, get the current install command from pytorch.org rather than guessing — they publish the exact index URL for the current CUDA build and it changes with releases.

## 1.6 Habits worth starting now

- **Keep the project in the Linux filesystem** (`~/projects/log-anomaly`), never under `/mnt/c/`. Cross-filesystem I/O in WSL is dramatically slower and you'll read 11M lines repeatedly.
- **Terminal copy/paste:** `Ctrl+Shift+V` to paste, `Ctrl+Shift+C` to copy. Plain `Ctrl+C` sends an interrupt and kills what's running.
- Use **Windows Terminal**, not the legacy Ubuntu console window.
- Point VS Code at **WSL**, not Windows — Remote-SSH to the box, then reopen in WSL, or use the WSL extension locally.

---

# Part 2 — The 16-week roadmap

~10 hrs/week. Total cost ~$20–60, almost all of it the Phase 1 API calls.

## Timeline

| Weeks | Phase | Gate to continue |
|---|---|---|
| 1–2 | Data + dumb baselines | Eval harness you trust, two baseline numbers |
| 3–4 | Frontier model baseline | The number you're trying to beat |
| 5–9 | Train your model | You beat the classical baselines |
| 10–12 | Make it fast | Throughput before/after with profiler traces |
| 13–16 | Ship and write up | Live link + comparison table |

**Gates are hard stops.** If you haven't passed one, don't start the next phase — you'll build on a foundation you can't trust and lose more time than you saved.

## Tech stack — decided, so you don't relitigate it

| Layer | Choice |
|---|---|
| Language / env | Python 3.11, `uv` |
| Log parsing | **Drain3** |
| Classical baselines | scikit-learn (TF-IDF + logistic regression) |
| Training | PyTorch + HuggingFace Transformers |
| Model | small transformer encoder over template-ID sequences (2–6 layers, ~5–30M params) |
| Tracking | Weights & Biases |
| Inference | ONNX Runtime, then TensorRT |
| Profiling | Nsight Systems |
| Serving | FastAPI + Docker |
| Hosting | Fly.io or a small VPS |

**On the model choice:** sequences of parsed log *template IDs*, not raw text. That's what the established work does, it's far smaller and faster than a text encoder, and it fits 8 GB with room to spare. Try a text encoder later as an ablation.

## Prior art to read in week 1

Read before writing training code. These define the bar and save you from rediscovering known failure modes.

- **DeepLog** — the foundational LSTM approach
- **LogBERT** — the transformer version
- ***Log-based Anomaly Detection with Deep Learning: How Far Are We?*** — a skeptical evaluation documenting where published results are inflated. **The most useful one for you**
- *A Comprehensive Study of ML Techniques for Log-Based Anomaly Detection*

---

## Phase 0 — Data and honest baselines (weeks 1–2)

Do not skip to the neural network. This phase is what makes everything after it defensible.

### Week 1 — Get the data, look at it
- Download HDFS from LogHub/Zenodo. Read the papers while it transfers
- Parse with **Drain3**. **Manually inspect 50 generated templates** — bad parsing silently poisons everything downstream, and the similarity threshold usually needs tuning. You're an infra person; you'll know immediately if they look wrong
- Exploratory analysis: session length distribution, template frequency, what anomalous sessions actually look like

**Done when:** you can describe in your own words what distinguishes an anomalous block from a normal one.

### Week 2 — Harness and dumb baselines
- **Chronological split.** Train on earlier data, test on later. Never random
- Eval harness reporting precision, recall, F1 on the anomaly class, and PR-AUC. **Make it refuse to print accuracy** — literally leave it out so you're never tempted to quote it
- Baseline 1: keyword/regex rules, the kind an ops team would write
- Baseline 2: TF-IDF over templates + logistic regression

> ⚠️ **TF-IDF + LR is shockingly strong on log data.** Expect a high F1. **This is the real bar, not the frontier model.**

**GATE 1 — don't proceed until:** the harness is trustworthy, both baselines recorded, split is chronological.

---

## Phase 1 — The frontier model baseline (weeks 3–4)

### Week 3 — Prompt design
- Choose representation: raw lines or parsed templates?
- Write a real prompt with few-shot examples. Iterate on 100 sessions until it's genuinely good
- **Give it a fair shot.** A rigged baseline destroys the project's credibility, and the honest gap will still be dramatic

### Week 4 — Measure
- Run on 3,000–5,000 held-out sessions
- Record F1/precision/recall, **measured cost extrapolated to 1M and 11M lines**, per-item latency
- Budget ~$10–40

**GATE 2:** the frontier number sits in the same table as your baselines, measured the same way.

---

## Phase 2 — Train your model (weeks 5–9)

### Week 5 — Data pipeline
- Sessions → sequences of template IDs → tensors
- Vocabulary from templates; decide how to handle unseen templates at test time (matters more than it sounds)
- Dataloaders with the chronological split preserved

### Week 6 — First model, sanity checks
- Small transformer encoder, 2–4 layers
- **Overfit a tiny subset first — 50 sessions, loss to near zero.** If you can't, the pipeline is broken, and finding that now saves a week
- Then a real run. Expect mediocrity

### Week 7 — Class imbalance
- At ~3% positives this is the central problem
- Try class weighting, focal loss, and considered resampling. Run all three and report the difference — that's a legitimate ablation
- Tune the decision threshold explicitly. **Where you sit is a business decision** — false positives burn on-call attention, false negatives miss outages

### Week 8 — Model size sweep
- Train at ~3–4 sizes (say 2M / 10M / 30M / 60M params)
- Record accuracy **and** inference cost for each

That curve is the money artifact. It says "I understand the tradeoff space" in a way no single number can.

### Week 9 — Ablations and selection
- Templates vs raw text; sequence length; depth
- Pick the final model, justify it in writing
- **Checkpoint:** if you're behind, stop here. You already have a real result and could write it up

**GATE 3:** you beat TF-IDF + LR and have the curve. If you *didn't* beat it, that's a publishable finding — write it up honestly and move on rather than grinding.

---

## Phase 3 — Make it fast (weeks 10–12) — *the CUDA phase*

Throughput is the product here, so optimization is load-bearing.

### Week 10 — Quantization
- int8, export to ONNX Runtime
- **Measure the accuracy cost of every step.** "3× faster" is meaningless without "and F1 dropped 0.4 points"

### Week 11 — Profile
- Fix batching first — naive per-item inference wastes most of the GPU
- Nsight Systems trace. **The bottleneck is frequently tokenization and preprocessing on CPU, not the model.** Most people optimize the model and wonder why nothing improves

### Week 12 — Optimize the bottleneck
- Install the CUDA toolkit now, if you're writing kernels
- Attack whatever the profiler exposed
- Optional depth: hand-write a CUDA kernel for the hot path. **If the library version wins, report that** — knowing when not to hand-roll is a senior signal
- Final throughput: lines/second, cost per million lines on a named instance type

**GATE 4:** before/after throughput with traces, plus the accuracy cost of each step.

---

## Phase 4 — Ship and write up (weeks 13–16)

### Week 13 — The service
FastAPI service consuming a log stream and emitting detections. Health checks, structured logging, Prometheus metrics. Your home turf — make it good.

### Week 14 — The dashboard
Flagged events, confidence, live throughput, running cost. **Timebox to one week** — dashboard polish is the most seductive form of procrastination in this project.

### Week 15 — Deploy
Containerize, IaC, public URL. Verify a stranger can reach it and understand it in 30 seconds.

### Week 16 — The writeup
- README: pitch paragraph, **architecture diagram above the fold**, comparison table, live link
- Long-form: methodology, the curve, ablations, and a **"what didn't work"** section — experienced reviewers read it first
- 60–90 second demo video
- Publish the eval code so results are reproducible

**GATE 5:** a stranger can click a link, see it work, and read a number.

### The headline table

| | Regex | TF-IDF + LR | Frontier model | **Your model** |
|---|---|---|---|---|
| F1 (anomaly class) | | | | |
| PR-AUC | | | | |
| Cost / 1M lines | | | | |
| Latency / item | | | | |
| Throughput | | | | |

---

# Part 3 — Rules that keep it honest

## The three that matter most

**1. Split chronologically, never randomly.** Logs are time-ordered. A random split trains the model on events that happened *after* the ones you test it on — studying tomorrow's newspaper to predict today. Scores come out beautiful and mean nothing. This is a documented flaw in published work in this area, so getting it right is a differentiator, not just hygiene.

**2. Never report accuracy.** At 2.93% anomalies, always predicting "normal" scores 97%. Report precision, recall, F1 on the anomaly class, and PR-AUC.

**3. Build the dumb baselines first.** If your neural network doesn't beat TF-IDF, report that. An honest negative result reads as competence; a suspiciously good number invites the wrong kind of scrutiny.

## Tripwires

- **F1 above 0.99** → treat as a bug report, not a triumph. Re-verify the split
- **Model won't overfit 50 examples** → pipeline is broken
- **Templates look like garbage** → tune Drain3 before doing anything else

## The reframe that removes the fear

**Your deliverable is the comparison, not the victory.**

You're not betting 16 weeks on your model winning. You're building a table showing four approaches, what each costs, and how accurate each is. That table is valuable regardless of which row wins. Even the worst case — your neural network loses to a 1990s technique — is a finished project with real numbers and an honest write-up.

And you can answer "is this worth doing?" in about **20 hours**. By the end of week 2 you'll know whether there's an interesting gap to attack. Nothing in weeks 3–16 risks being meaningless, because week 2 tells you whether to keep going.

---

# Part 4 — Risks and escape hatches

| Risk | Signal | Mitigation |
|---|---|---|
| Bad log parsing poisons everything | Templates look like garbage | Inspect 50 templates in week 1; tune Drain3 threshold |
| Neural model loses to TF-IDF | Week 9 gate fails | Valid result — report it. Check you're modeling *sequence*, not bag-of-templates |
| Results too good to be true | F1 > 0.99 | Almost certainly leakage. Re-verify the chronological split |
| OOM on 8 GB | Training crashes | Smaller batch + gradient accumulation, gradient checkpointing, shorter sequences |
| **Motivation dip, weeks 8–10** | Lots of work done, nothing visible | **Deploy an ugly demo in week 9, before it's ready.** This kills more projects than any technical problem |
| Dashboard scope creep | Week 14 becomes week 17 | Hard timebox. Ugly and shipped beats beautiful and unfinished |

## If you fall behind

Cut in this order:

1. **BGL generalization** (a stretch goal anyway)
2. **The custom CUDA kernel** — quantization and batching already give you the throughput story
3. **The dashboard** → a static results page plus a demo video
4. **The model size sweep** → two sizes instead of four

**Never cut:** the chronological split, the classical baselines, or the writeup. Those are what make it credible rather than impressive-looking.

## Definition of done

- [ ] Live URL, or demo video if hosting the stream is impractical
- [ ] Architecture diagram above the fold in the README
- [ ] Comparison table, all four approaches measured identically
- [ ] Cost-versus-accuracy curve
- [ ] Reproducible eval code, split methodology documented
- [ ] "What didn't work" section, written honestly
- [ ] The pitch paragraph, unchanged, at the top

**The 90-second test** — recruiters spend under 90 seconds. Before calling it done: is there a link to click? A diagram above the fold? A number? One sentence saying why anyone should care?

---

# Part 5 — What comes next (optional)

**Real-time vision project, ~6 weeks after this one** — not another four months, because ~60% of the machinery carries over.

**Carries over:** training loop, experiment tracking, evaluation rigor, quantization, inference optimization, Nsight profiling, containerization, and the instinct to measure a baseline before optimizing.

**New:** image/video pipelines, augmentation, detection architectures, frame-budget optimization, video I/O.

**The moat is latency, not cost.** An API round trip is 500–2000ms; at 30fps you have 33ms. Not expensive — *impossible*. Cleaner argument than economics, and the deliverable is a video of something working live, which gets looked at far more than any README.

---

## Quick command reference

```bash
# get into Ubuntu (from Windows)
wsl

# activate the project env
cd ~/projects/log-anomaly && source .venv/bin/activate

# confirm GPU
nvidia-smi
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"

# terminal paste / copy
Ctrl+Shift+V   /   Ctrl+Shift+C
```

---

## Sources

- [LogHub — logpai](https://github.com/logpai/loghub)
- [Loghub-2.0 datasets on Zenodo](https://zenodo.org/records/8275861)
- [Loghub: A Large Collection of System Log Datasets](https://arxiv.org/pdf/2008.06448)
- [Log-based Anomaly Detection with Deep Learning: How Far Are We?](https://arxiv.org/pdf/2202.04301)
- [A Comprehensive Study of ML Techniques for Log-Based Anomaly Detection](https://arxiv.org/pdf/2307.16714)
- [Impact of log parsing on deep learning-based anomaly detection](https://link.springer.com/article/10.1007/s10664-024-10533-w)
- [Model distillation cost and latency, 2026 — Redis](https://redis.io/blog/model-distillation-llm-guide/)
