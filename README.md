# feedforward-preflight

**Pre-flight checks for IBM dynamic circuits: will your classical feedforward compile, and how long will it take?**

![license](https://img.shields.io/badge/license-Apache--2.0-blue)
![python](https://img.shields.io/badge/python-3.12-blue)
![qiskit](https://img.shields.io/badge/qiskit-2.6-6929c4)
![qiskit-ibm-runtime](https://img.shields.io/badge/qiskit--ibm--runtime-0.51-6929c4)
![measured on](https://img.shields.io/badge/measured%20on-ibm__marrakesh-lightgrey)
![QPU probes](https://img.shields.io/badge/QPU%20probes-100-lightgrey)
![status](https://img.shields.io/badge/status-empirical%20%2F%20research-orange)

> **Unofficial.** This project is not affiliated with, endorsed by, or supported by IBM. IBM, IBM Quantum and Qiskit are trademarks of International Business Machines Corporation; they are used here only to name the platform the measurements were taken on.

**Read this first.**
* **Scope:** one backend (`ibm_marrakesh`), three dates: 2026-11-06 (29 envelope probes, historical, run once), 2026-11-09 (legacy latency ladder), 2026-11-15 (everything else). Results are dated observations, not guarantees. The `ibm_fez` replication is pending (five jobs submitted, still queued).
* **Not published by IBM:** every threshold and latency here was measured empirically by us; IBM names the error and publishes no numbers for it.
* **Monotonicity is an assumption:** a PASS means "no more demanding than a probe that was accepted", which assumes that needing less of every measured resource cannot get a circuit rejected. The stored probes do not contradict it; it is unproven.
* **UNCERTAIN is common by design:** PASS needs the circuit to be dominated by an accepted probe in every dimension, so many circuits are UNCERTAIN rather than PASS or FAIL.

## Contents
[Why](#why) | [Quick start](#quick-start) | [Results at a glance](#results-at-a-glance) | [What we don't know (summary)](#what-we-dont-know-summary) | [Scope and status](#scope-and-status) | [Findings](#findings) | [What we don't know (full)](#what-we-dont-know) | [Definitions](#definitions) | [Limitations](#limitations) | [Running it](#running-it) | [Pre-flight checker](#pre-flight-checker-dccheckpy) | [Contributing](#contributing-results-from-other-backends) | [Layout](#layout) | [Licence](#licence)

## Why
IBM's error 6057 says the job "requires more classical registers than the control hardware provides" and gives no numbers, so you learn about it after queueing and a failed job.
This repository measures where that limit sits for real programs on one backend (100 probe circuits, each submitted to the QPU), and the latency of an in-controller expression.
`dccheck.py` turns those measurements into a pre-flight verdict (PASS / FAIL / UNCERTAIN) that names the probe it rests on, and `--fix-pack` can rewrite one common failing pattern (many small measured registers) into an equivalent packed circuit.

## Quick start
Tested with Python 3.12, `qiskit==2.6.2`, `qiskit-ibm-runtime==0.51.0`, `numpy`; nothing is installed by this folder and no IBM account is needed for the checker.

```
python dccheck.py circuit.qpy                          # verdict for a QPY file (checked as passed and as transpiled; worst wins)
python dccheck.py circuit.qpy --fix-pack -o packed.qpy # repack many small measured registers into few wide ones
python dccheck.py mypkg.mymod:build_circuit --json     # or module:func returning a QuantumCircuit; machine-readable output
```
```python
from dccheck import preflight, load_rules, format_report
rep = preflight(qc, load_rules())      # rep["status"]: PASS, FAIL, UNCERTAIN, NOT_APPLICABLE (no if_else) or ERROR
print(format_report(rep))
```
Exit status: 0 PASS, 1 FAIL, 2 UNCERTAIN, 3 internal error, 4 not applicable, 64 could not load.

Real output (trimmed) for the shipped probe `B_R12x8c` (12 registers of 8 bits, one XOR store, one `if`), which the QPU rejected with 6057 on 2026-11-15:
```
$ python dccheck.py data/envelope_probes/r2__B_R12x8c.qpy
=== PREFLIGHT 'B_R12x8c_A': OVERALL FAIL (worst of the circuit as passed and as transpiled) ===
if_else at if_else[140]   FAIL   cond: S[0]
  resources: peak live registers 13; left-to-right stack need 2 (Ershov 2); 12 measured register(s) in the cone ...
  - FAIL      envelope: at least as demanding as rejected probe R12x8 (ibm_marrakesh, measured 2026-11-15) in every
              failure-relevant dimension (cone_ops=11>=11, lr_stack=2>=2, peak_live_regs=13>=13, unary_ops=0>=0): error 6057 ...
      Ours: R12x8: measured empirically on ibm_marrakesh 2026-11-15; empirical monotone dominance envelope over 100 probes, not published by IBM.
  latency: central ~604 ns after measurement end, WIDE 185 to 960 ns for 11 ops (range, not a statistical interval) ...
OVERALL: FAIL

$ python dccheck.py data/envelope_probes/r2__B_R12x8c.qpy --fix-pack -o packed.qpy
  pk0 (32 bits) <- G0@0+8, G1@8+8, G2@16+8, G3@24+8
  pk1 (32 bits) <- G4@0+8, G5@8+8, G6@16+8, G7@24+8
  pk2 (32 bits) <- G8@0+8, G9@8+8, G10@16+8, G11@24+8
resources before -> after: peak live registers 13 -> 3; ...; verdict FAIL -> PASS
equivalence: structured+random, 2196 measurement-outcome patterns over 97 measurement events: traces and final clbits identical = True
note: ... this exact circuit has not been run on the QPU; run fixpack_qpu.py (v2 plan) to verify a packed circuit of this shape
```
The PASS after packing is again a prediction from the envelope; three fix-pack v2 packed circuits were accepted by the QPU (see Findings 2b and `data/fixpack_v2_executed/`). More options: [Pre-flight checker](#pre-flight-checker-dccheckpy).

## Results at a glance
| Finding | Evidence | Caveat |
|---|---|---|
| In-controller expression latency grows roughly 25 to 30 ns per op; examples: 15 xor ops 743 +/- 59 ns, 27-op weighted vote 1055 +/- 67 ns, 63 random ops 2103 +/- 75 ns (increment over an `if` on one bit) | [Findings 1](#1-latency-of-a-classical-expression-stopwatch-method), `data/rederive/`, `data/latency_reanalysis.json` | Descriptive range, not a law; fixed part (intercept 0 to 400 ns) not established; per-probe scatter far above shot noise; estimator choice changes the point estimate |
| Error 6057 rejections track peak live registers and stack need (the envelope's failure dimensions); for example 12 read registers of 8 bits (`B_R12x8c`) is rejected while up to 10 read registers were accepted | [Findings 2](#2-error-6057-more-classical-registers-than-the-control-hardware-provides), `data/probe_outcomes*.json` | No closed-form model survived out-of-sample; why the controller rejects is a hypothesis (register allocation), not a finding |
| An empirical monotone dominance envelope over 100 probes predicts PASS/FAIL with no wrong prediction in leave-one-out (unique vectors: 33 of 64 accepted PASS, 31 UNCERTAIN; 16 of 21 rejected FAIL, 5 UNCERTAIN) and in 6 of 6 frozen out-of-sample predictions | [Findings 2b](#2b-empirical-envelope-instead-of-a-formula), `rules/envelope_ibm_heron_r2_2026-11-15.json`, `build_envelope.py` | Rests on monotonicity (assumed); sparse, so UNCERTAIN is common; one backend |
| Packing many small measured registers into few wide ones (`--fix-pack` v2) was accepted by the QPU 3 of 3 circuits; fix-pack v1 (narrowing cast of a shifted register) was rejected with 6066 | [`--fix-pack`](#--fix-pack-v2-remap-only), `data/fixpack_v2_executed/`, `data/RESULTS_QPU_fixpack_v*.md` | Three circuits only; the trigger of 6066 is not isolated |
| Accepted probes were checked by prepared-input readback (output responds to operands) | [Findings 3](#3-acceptance-readback-what-accepted-was-checked-with) | Sampling check, not an exhaustive truth table |

## What we don't know (summary)
* **Cross-backend:** everything is `ibm_marrakesh`; the `ibm_fez` run is queued and nothing is assumed about other devices.
* **Firmware and date:** controller firmware is not exposed; limits and latencies can change with an update (the four 2026-11-06 failures reproduced nine days later, the only stability evidence).
* **Why 6057 happens:** no closed-form model survived; the envelope predicts, it does not explain.
* **Monotonicity:** assumed, not proven; 27 rejections cannot prove it.
* **Latency intercept and 6066 trigger:** the fixed latency part and the cause of 6066 are unresolved.
Full list: [What we don't know](#what-we-dont-know).

## Scope and status
Measurements of what IBM's real-time classical engine does with `store` / `if` expressions: how long an expression takes to evaluate between a mid-circuit
measurement and the conditional gate, and which programs the controller refuses (error 6057). IBM names the error and publishes no numbers for registers, ops or latency. This folder is a test harness plus
the data from the first runs. Status: one backend (`ibm_marrakesh`), three dates: 2026-11-06 (29 envelope probes, historical, run once), 2026-11-09 (legacy latency ladder), 2026-11-15 (everything else); results are dated observations and not guarantees.

**The one assumption to read first: monotonicity.** The checker's PASS verdict rests on an assumption, not a law: a circuit that uses no more of every measured resource (peak live registers, stack need, ops, ...) than some accepted probe is predicted to be accepted. Nothing guarantees that; the 27 rejected probes only show that the data do not contradict it. A PASS is a prediction from similarity to probes run once on one backend, never a guarantee, and many realistic circuits are UNCERTAIN.

Run summary (rounds 1-3; round 4 is described under Findings 2 and in `data/run_metadata.json`: 25 probes executed twice, 52 QPU-seconds charged per execution): backend `ibm_marrakesh` (Heron r2, backend version 1.0.21, firmware not exposed), Open plan, 2026-11-15 14:07-14:29 UTC, qiskit 2.6.2, qiskit-ibm-runtime 0.51.0,
executor Sampler, transpiler `optimization_level=1`, `seed_transpiler=7`, fixed `initial_layout`. Three rounds: 12 jobs (latency + acceptance, plan `c4189b3af6be`), 9 jobs (register-count
bisection, `ef2d74ac0e55`), 5 jobs (split stores, `138f38fc85af`); 82 QPU-seconds charged (54 + 18 + 10; the cost model estimated 44 + 18 + 10: latency jobs were charged 8 s each against an
estimate of about 5.9 s). Per-job charges, UTC windows, layouts and the calibration timestamp are in `data/run_metadata.json`. Round 2 and 3 plans were designed after seeing the earlier rounds.

## Findings

### 1. Latency of a classical expression (stopwatch method)
Increment over an `if` on a single measured bit, same job and layout (K = 16 repetitions, 3000 shots, 24 probe qubits, `measure_2` sources, reference delays 0/500/1000/2000/3000 ns).
`store ops` = operations in the stored expression excluding the final compare; `store depth` likewise (same convention everywhere). **Headline = Huber location over the usable probes with a
leave-one-probe-out jackknife standard error** (`data/rederive/`). Per-probe scatter is far larger than shot noise (probe chi2/dof 9-58 in this round, 2-3.6 on the legacy day), so an earlier shot-noise-only
estimator with a one-pass 4-sigma clip ("published-style") is shown for comparison; it is not recommended.

| probe | store ops | store depth | Huber +/- jackknife [ns] | median | published-style (clipped, shot sigma) | weighted, unclipped | probe chi2/dof |
|---|---|---|---|---|---|---|---|
| XORC15 (15 xor, chain) | 15 | 15 | 743 +/- 59 | 772 | 737 +/- 45 | 656 | 8.8 |
| XORT15 (same 15 xor, balanced tree) | 15 | 4 | 741 +/- 36 | 733 | 743 +/- 40 | 651 | 8.8 |
| POP8 (popcount of 8 bits, no mul) | 23 | 9 | 875 +/- 47 | 871 | 845 +/- 53 | 607 | 26.6 |
| WVOTE27 (7-input weighted vote with 7 mul) | 27 | 9 | 1055 +/- 67 | 999 | 835 +/- 65 | 397 | 58.2 |
| SOUP63 (63 random and/or/xor) | 63 | 6 | 2103 +/- 75 | 2128 | 2077 +/- 75 | 1799 | 18.2 |

The estimator matters: for WVOTE27 the 4-sigma clip kept 9 of 24 probes, giving 835; unclipped it is 397, the median 999, Huber 1055. Point estimates carry an estimator-dependent spread of
up to about 440 ns (WVOTE27) and 300 ns (SOUP63, POP8 unclipped); read them as +/- that, not as the jackknife SE alone.

* **Roughly linear in op count, about 28 ns/op between 15 and 63 ops** (28.3 +/- 1.7 ns/op, errors scaled by sqrt(chi2/dof)). The fixed part (293 +/- 45 ns) and an op-class effect are **not resolved**: with an input-count
  term the fit is 180 +/- 91 + 28.5 ns/op + 8 +/- 6 ns/input (chi2 0.8 / 2 dof), ops-only chi2 2.9 / 3 dof. Input count (16 for the Bool probes, 7-8 for the Uint ones), op class and the fixed cost are collinear in this
  design, and the marginal slopes between consecutive points are 17, 45 and 29 ns/op, so this is not a law and the intercept is not established. The CASE circuit also differs from the reference
  circuit by one `store` and one bit read, so a nonzero intercept exists by construction.
* **Depth: no effect seen for an XOR chain vs a tree** (15 xor, depth 15 vs 4): +2 +/- 69 ns; a depth-driven law predicted +1372 ns. XOR is re-associable, so the controller may rebalance it;
  non-reassociable ops have not been tested, and neither has whether the controller executes ops as written (algebraic folding).
* **Multiplication:** WVOTE27 - POP8 = +180 +/- 82 ns for 4 more ops (expected about +113 at 28 ns/op). No evidence that a multiply costs more than the average op; not a measurement of its cost.
* **Pre-registration was local only** (predictions were written into the plan manifest before submission; nothing was hashed, signed or committed, and the predictions code has been edited since).
  The registered law (190 + 31.2 ns/op, from a 3-point fit on 2026-11-09 data) **failed its own falsification rule under the original estimator** (WVOTE27 835 vs 1033, -3.0 sigma; chi2 19.0 on 5 dof). Under the
  robust estimator, chosen after seeing the data, the deviations are XORC15 +1.4, XORT15 +2.3, POP8 -0.7, WVOTE27 +0.3, SOUP63 -0.7 sigma (chi2 8.2 on 5 dof). The robust estimator and the refit above are therefore
  post hoc. The 63-op prediction also carried about +/-400 ns of its own uncertainty, so its hit is weak evidence.
* **Legacy comparison:** the 27-op vote measured 1032 +/- 151 ns on 2026-11-09 (K = 8, narrower reference range; 975 +/- 78 with the robust estimator) and 1055 +/- 67 now: consistent. The `data/legacy_stopwatch`
  flat 1-bit `if` against a barrier-only circuit was 96 +/- 86 ns (robust 160 +/- 76); see Definitions for why that is not IBM's feedforward latency.
* **Z-type vs X-type probes disagree** (X medians higher in 4 of 5 jobs, by up to 390 ns for WVOTE27: 1258 vs 872). A method assumption is unchecked: the reference delay is inserted after the conditional X
  while the controller latency falls before it, which need not be equivalent for X-type (echo) probes. A placement check was run in the second session (`data/p2/`) and is unresolved (see below).
* **Second session (`data/p2/`, same day, one job: null circuit, reference-placement check, XORC15 again) closes the latency section with these results:** XORC15 672 +/- 55 ns (round 1: 743 +/- 59), consistent. A case identical to the reference circuit (the null job) reads **+65 +/- 27 ns**: a false increment of about that size (2.4 sigma) exists, so every increment above carries a
  systematic of that order. The placement check (reference delay before an unconditional X instead of after the conditional X) gives XORC15 = 819 +/- 80 vs 672 +/- 55 with the original calibration (difference -147 +/- 97, 1.5 sigma): **unresolved**. The Z/X-probe asymmetry did not reproduce in this job (X minus Z median -5 ns against +49...+386 in round 1), so it is not established either way.
  Not run: the ops-vs-inputs design and the non-reassociable chain-vs-tree comparison (kept in the code as deferred), so op class, input count and the fixed cost remain unseparated and depth independence is shown only for XOR.
* Reproduce every number: `python data/rederive/reanalyse_latency_all.py` and `python data/rederive/latency_fit.py` use only `data/legacy_stopwatch` and `data/round1_latency` (raw probe bits).

### 2. Error 6057 ("more classical registers than the control hardware provides")
Every failure in every round returned error 6057 with the same message (full texts in `data/*/RESULTS_QPU_*.md`; no 6073 or 6116 was ever returned). IBM's registry (https://quantum.cloud.ibm.com/docs/en/errors, read 2026-11-15) words it
"The job requires more classical registers than the control hardware provides. If possible, break the job into multiple separate jobs or reduce the number of classical variables and measurement results used in subsequent conditions".
IBM's wording suggests (this is a hypothesis, not a finding; the closed-form versions of it were falsified, see below) **one resource: a pool of hardware registers shared by measured registers, stored registers and the temporaries needed to evaluate an expression**. The data are not explained by a count of measured registers in the
cone, a count of ops, or a count of total reads. Round 4 (`data/p1/`, 25 probes executed **twice** on `ibm_marrakesh`, 2026-11-15 ~15:15 UTC; the second execution was an accidental duplicate and is used as a replicate;
mapping of the first run to its probes was verified: same circuits, outcomes and result keys for all 25) gives:

| finding | probes (replicated outcomes, both runs agree for all 25) |
|---|---|
| Register count in a single store: 10 accepted, 11 accepted (3 probes x 2 runs), 12 rejected | B_R10x8, P1_R11x2_a/b/c, P1_R12x2_a/b (theta between 11 and 12 for this shape) |
| **Distinct measured registers in the cone is not the trigger; simultaneous liveness is**: same 12 registers and stores, accepted when the second group of 6 is measured only after the first group's store, rejected when all are measured first | P1_staged accepted (peak live 8), P1_split6 rejected (peak live 13) |
| Total register reads is not the trigger: a non-foldable overlap (XOR vs ADD, cone 10, 14 reads) accepted | P1_overlapNF |
| Stored intermediates draw on the same pool: 3 measured registers plus k stored Uint16 intermediates accepted for k = 4, rejected for k = 8 and 12 | P1_alloc4 / 8 / 12 |
| Expression shape matters, not op count or unique nodes: distinct-leaf random-shape expressions accepted at 96 ops, rejected at 104 and 126; a balanced tree and a left chain of 104 ops accepted; a 104-op DAG with only 56 unique nodes rejected | P1_ir96/104/126, P1_bal104, P1_chain104, P1_dag104 |
| Combined pressure: 8 measured registers plus a 63-op expression: left chain accepted, balanced tree rejected (each single-factor rule predicts both pass) | P1_C8chain, P1_C8bal |
| All four old failures (2026-11-06 to 11-09) **reproduce today**: the 103-op Shannon expression, the SIMD CONE shape, the 84-op LUT at K = 8, 512 live bit variables | P1_old_T1 / CONE / LUT85K8 / W512, all 6057 in both runs |

**Model fitting (post hoc; `data/modelfit.json`, `data/p1/CONSTRUCTION_DIFFS.md`, `data/p1/RESULTS_QPU_v1.md`, `data/e2/`).** I fitted threshold models (with an internal script; `data/modelfit.json` is a record of the table, not regenerated by this package, whose shipped circuits are the inputs) to all 93 probes with a known outcome (rounds 1-4, the validation rounds v1, e1 and e2 as executed, plus 29 reconstructable 2026-11-06 jobs; 66 accepted, 27 rejected), counting every
feature on the transpiled circuits. **No model separates all of them, and no closed-form model survived out-of-sample.** Errors out of 93 for single-metric thresholds: peak live registers + left-to-right stack need 11, Sethi-Ullman pool 14, peak live registers 15, measured registers in the cone 18, depth 21, stack need alone 21, cone ops 24, total reads 24, max ops of one store 24.
- **M_w** (4 x peak live registers + 5 x stack need >= 59): fitted on 80 probes, **falsified** by the frozen `v1` round (V1_rc10_P3 and V1_rc12_P1 were accepted).
- **M_u** (unweighted pool: *fail iff peak live registers + left-to-right stack need >= 14, or >= 513 live stored bit variables*): survived the frozen `v1` round 5 of 5 and was 5 of 5 on `e1` (computed afterwards from the stored vectors), then was **falsified** by the frozen `e2` round: wrong on E2_shan112 (a Shannon-form expression, pool 12, predicted accepted, rejected 6057) and on E2_rc5_P9
  (9 live registers, stack need 5, pool 14, predicted rejected, accepted). On all 93 probes it misclassifies 9 (R11x2 x3 at score 14 accepted; T1, LUT85 K=8, E2_shan112, T3, T3m rejected at scores 11-12; E2_rc5_P9 accepted at 14).
- The controller's real accounting is not captured by any of these (V1_rc12_P1 with stack need 12 passes, the Shannon-form T1 with 11 is rejected).
**The verdicts of the pre-flight tool come only from the empirical dominance envelope (Section 2b), the official hard rules quoted from IBM, and observed hazards (the 6066 packing hazard was observed on 2026-11-15; the 6327, 6060, 6062 and declared-Var rejections are recorded from earlier days and were not re-measured); none of the formulas above is used.**
The construction differences between the rejected (ir104, ir126, dag104, C8bal) and accepted (bal104, chain104, C8chain) probes are only tree shape (same registers, widths, leaf counts, literals, casts, op classes).

What this retracts or changes from earlier rounds: "6057 fires when the cone has >= 12 distinct measured registers" is replaced by the pool description above (the old register-ladder results remain true as one shape of it);
"splitting stores does not bypass the limit" holds only for stores whose measured registers are all live together: a staged program that measures later groups after earlier stores are done is accepted
(the staged probe adds two identical conditional X gates per later group to force the ordering, so it carries extra `if`s; the transpiler otherwise hoists the measurements); "total reads are irrelevant" is now supported by a non-foldable probe.
Older failures listed in earlier versions of this README are real and current, but their cause is still only partly modelled.

### 2b. Empirical envelope instead of a formula
Since no formula fits every probe, `resources.py` computes a **resource vector** (26 dimensions, all "more = more of some controller resource": peak live registers, peak live bit variables, measured registers in the cone, stored count and bits, ops of the largest
store and of the cone, depth, Ershov and left-to-right stack need, distinct leaf bits, unique nodes, NOT count, literals, number of ifs, shift/cast/compare/add/mul/index flags, and `unanalysed`, a count of constructs the analysis could not follow, which forces UNCERTAIN) from the **transpiled** circuit, and `rules/envelope_ibm_heron_r2_2026-11-15.json` stores the vectors of
every probe with a known outcome (71 accepted incl. the packed fix-pack circuits, 27 rejected with 6057, `ibm_marrakesh`; 29 of them are historical 2026-11-06 probes (25 accepted, 4 rejected) that were run once and never repeated, labelled with their date in the rules file; two probes rejected with 6066 are excluded). Every probe's circuit is shipped (`data/envelope_probes/`) and `python build_envelope.py` regenerates the vectors, the consistency check and the leave-one-out. PASS is never issued for a circuit with `unanalysed > 0` or without an `if`. Verdicts assume monotonicity: **PASS** if the vector is <= that of some accepted probe in every dimension; **FAIL** if it is >= that of some rejected probe in the
failure dimensions (cone_ops, lr_stack, peak_live_regs, unary_ops); otherwise **UNCERTAIN**. On the stored probes no accepted vector dominates a rejected one (so no dimension is missing for these data); the smallest dimension sets that remove every possible contradiction have 3 members, always peak_live_regs and
lr_stack plus one of cone_ops / unary_ops. Stack need is essential: without it the accepted R10x8 would dominate the rejected V1_rc4_P10. Leave-one-out on the 85 UNIQUE (vector, class) pairs (64 accepted, 21 rejected; identical replicates removed): **33 of 64 accepted predicted PASS and 31 UNCERTAIN (none predicted FAIL); 16 of 21 rejected predicted FAIL and 5 UNCERTAIN (none predicted PASS).** Counting every probe including identical replicates the numbers are 43 of 71 and 23 of 27 (secondary; inflated by replicates). The failure dimensions and this leave-one-out were chosen and computed on ALL the data, so they are descriptive, not out-of-sample. Monotonicity is an assumption and the envelope is sparse: many realistic circuits are UNCERTAIN.

**Out-of-sample scoreboard** (each round's predictions were written into the plan manifest before submission, with no external timestamp; analyses read only the manifest and the stored circuits; the scrubbed manifests with the registered predictions text, plan hash, submit time and the plan QPY of `v1`, `e1`, `e2` and `p2` are in `data/v1`, `data/e1`, `data/e2`, `data/p2`; no envelope hash was taken at the time, only the data counts stated in each registered predictions text):

| round | what was frozen | probes | result |
|---|---|---|---|
| `v1` (5 probes) | only closed-form models (M_w, M_u, peak, cone, Sethi-Ullman, op limit); **no envelope existed yet** | 4 accepted, 1 rejected | M_u 5/5, M_w 3/5, peak 4/5, cone 4/5, Sethi-Ullman 4/5, op limit 4/5 |
| `e1` (5 probes, plan data 58 accepted / 24 rejected) | envelope verdicts (4 PASS, 1 FAIL); M_u was computed afterwards from the stored vectors | 4 realistic circuits + 1 larger decode | envelope **5 correct, 0 wrong, 0 UNCERTAIN**; M_u 5/5 (post hoc column) |
| `e2` (3 probes, plan data 66 / 26) | envelope and M_u, chosen so that they disagree | Shannon-form 112 ops; two register/stack-need probes | envelope **1 correct (FAIL), 0 wrong, 2 UNCERTAIN (both accepted)**; M_u 1 correct, 2 wrong |

**Which rule was frozen when.** The `e1` manifest froze the envelope built from 58 accepted / 24 rejected probes with **five** failure dimensions (cone_ops, lr_stack, peak_live_bitvars, peak_live_regs, unary_ops); the `e2` manifest froze the envelope built from 66 / 26 probes with **four** (cone_ops, lr_stack, peak_live_regs, unary_ops), which is also the rule of the shipped envelope (71 / 27 probes, four failure dimensions). The `e1` and `e2` verdicts are identical under the five-dimension and the four-dimension rule and under the current, larger envelope with the round's own probes left out (checked by `test_resources.py` from the stored vectors).

Frozen envelope PASS/FAIL predictions so far: **6 correct, 0 wrong** (e1 5, e2 1), 2 UNCERTAIN predictions (made no claim), and no envelope prediction before `e1`. Every informative readback pattern responded in all accepted probes of these rounds. Six predictions on one backend on three dates do not make the envelope a guarantee.
**Single backend; fez replication pending:** the five `ibm_fez` jobs (plan `faf87bdc3fa6`, register probes R10/R11/R12, split6, non-foldable overlap) were still queued (the device in maintenance) when this was written; their job ids are not published.

### 3. Acceptance readback (what "accepted" was checked with)
Each register/split probe was executed with prepared basis-state inputs (X + `measure_2`), 200 shots for each of two patterns with known output (A -> out = 1, B -> out = 0). Correctness per pattern was 0.75-0.99
(Wilson 95% intervals in the tables; readout-limited; chance is 0.5). B_R10x8, the lower edge of the bracket, was the weakest (0.75 / 0.78, CI lower bound 0.686 for A, below the suite's own 0.7 criterion), so
its computation is confirmed only as "far from chance".
**Limit of this check:** pattern A puts its ones in the first 3-4 registers of the XOR (B in the first 4); a controller that ignored later registers would give the same outputs, so "verified" proves the output
responds to those registers, not that the whole cone was evaluated. In round 4 every informative register program has a pattern touching only the last register and one touching a middle register (marked * in the tables), and the pooled correctness of informative patterns was 0.68-1.00 with Wilson 95% lower bounds above 0.6 (R10x8 weakest, 0.68-0.76; chance 0.5), so each operand is shown to matter. The alloc probes have uninformative single-register patterns for some registers.
The round-1 acceptance probes (A84, A96, C3x30, C6x16) used random inputs and compare P(out = 1) with the exact expected value at 64 shots (sigma 0.06); that cannot tell a correct computation from a coin flip, so
for those "accepted" means only that the job completed without a controller error.

## What we don't know

* **Cross-backend.** Every result is from `ibm_marrakesh`. Three Heron r2 devices exist on the account with different backend software versions (marrakesh 1.0.21, fez 1.3.37, kingston 1.0.0). The register-probe core (R10/R11/R12, split6, non-foldable overlap) was submitted to `ibm_fez` (plan `faf87bdc3fa6`) but the five jobs were still queued (device in maintenance) when this was written, so there is **no cross-backend replication yet**; any marrakesh rule may be wrong on another device or version.
* **What is and is not recorded about the backends.** Recorded (`data/run_metadata.json`): processor family/revision (Heron r2) and `backend_version` for the three devices read after the runs (marrakesh 1.0.21, fez 1.3.37 in maintenance, kingston 1.0.0), and the same fields read just before submission for the round-4 plans (p1, v1, e1, e2, p2, p3). Not recorded: controller firmware, the calibration snapshot in force when each job ran, the `backend_version` at job time for rounds 1-3 and the fix-pack runs, and any server-side compiler version. The device versions differ, so a cross-backend difference could be a version difference.
* **Firmware and date.** Three dates on one backend (2026-11-06, 2026-11-09, 2026-11-15; see the status line), controller firmware not exposed by the API. Limits, error codes and latencies can change with a firmware or compiler update. The four 2026-11-06 failures reproduced nine days later, which is the only evidence about stability over time; the 25 accepted 2026-11-06 probes (K64..K1024, W32..W256, F1_regs150, ...) were measured once, never repeated, and serve as the only PASS witnesses for some regions of the envelope; nothing was re-run after 2026-11-15.
* **Latency.** The fixed part of the latency (293 +/- 45 ns in one fit, 180 +/- 91 ns with an input-count term) is not established; Bool vs Uint op classes and input count are collinear in the design; the null job reads +65 +/- 27 ns (a false increment of that size); the reference-placement check is unresolved (1.5 sigma); the Z/X-probe asymmetry appeared in one session and not in the other; depth independence is shown only for XOR (re-associable); multiply cost is bounded only weakly; per-op costs of compare and register-wide ops were never measured. The 96 ns "flat feedforward" and IBM's ~600 ns are different quantities.
* **What triggers 6066.** Both fix-pack v1 circuits that used a narrowing cast of a shifted wide register (shift by literal, then Uint(32) -> Uint(8)) were rejected with 6066, while fix-pack v2 (a different packing construction, same 32-bit register widths) was accepted 3 of 3. So register width alone is not the trigger, but whether the shift, the narrowing cast or the combination triggers it has **not been isolated**; the checker treats the observed construction as a hazard, not as a rule.
* **What triggers 6057.** No closed-form model survived out-of-sample (Section 2). The empirical envelope's PASS and FAIL predictions held in 6 of 6 frozen predictions, but 31 of the 64 unique accepted vectors are UNCERTAIN under it and nothing explains *why* the controller rejects what it rejects (register allocation is a hypothesis suggested by IBM's wording, not a finding).
* **Monotonicity is an assumption.** The envelope predicts PASS for a circuit that uses no more of every measured resource than an accepted one; nothing guarantees that a circuit with fewer registers, ops or stack depth cannot fail (for example through a resource the vector does not contain). The 27 rejected probes cannot prove it.
* **The readback is a sampling check, not exhaustive.** An accepted probe was run with prepared basis-state inputs (2 to 8 patterns, 200 shots each, informative patterns marked *); that shows the output responds to the operands the patterns touch, with readout noise limiting correctness to 0.68-1.00. It is not an exhaustive truth-table test on the QPU, round-1 acceptance probes used random inputs and a weak statistic, and a controller bug that only shows on other inputs would not be seen.
* **Statistics of repeats.** Most probes were run once; the p1 round was executed twice (one accidental duplicate), R11x2 six times in total, R10x8 and R12x2 at least four times each.

## Definitions

* **register**: one `qiskit.circuit.ClassicalRegister` object. Registers are never overlapped (overlap gives error 6327) and no `Var`s are used (declared `Var` with a literal initialiser gives 6066).
* **measured register**: a register whose bits are targets of a mid-circuit `measure_2` (`MidCircuitMeasure`).
* **cone** (of an `if`): all measured registers reachable from the condition by following `store` expressions backwards.
* **fan-in** of a store: distinct registers read by that store. **total reads**: sum of fan-ins over all stores.
* **op**: one binary or unary expression node (xor/and/or/not, add, mul, shift by literal, compare, index). Casts are not counted. A Uint op counts 1 regardless of width. `store ops` excludes the
  final compare in the `if`.
* **latency (stopwatch)**: idle probe qubits are held through the whole decision (the X on the probes cannot start before the branch resolves). Each probe's score-vs-dead-time curve is calibrated
  inside the job with reference circuits (per-repetition delays 0, 500, 1000, 2000, 3000 ns), then inverted. Reported numbers are **increments over an `if` on one measured bit (REF0)** within
  the same job. A flat 1-bit `if` against a barrier-only circuit was measured on 2026-11-09 as 96 +/- 86 ns (legacy data in `data/legacy_stopwatch/`).
* **What the stopwatch does not include**: the `measure_2` duration (1.884 us scheduled) and any latency that the two compared circuits share, such as result transport. The 96 ns flat value
  is therefore **not comparable** to the ~600 ns feedforward latency quoted in IBM's blog post on utility-scale dynamic circuits (https://www.ibm.com/quantum/blog/utility-scale-dynamic-circuits; the error-code and limits pages give no latency figure): it is a difference between two circuit shapes, and a wait common to both (for example waiting for the measurement results
  to reach the controller after a `barrier`) cancels. Absolute time from the start of measurement is not what is reported here.

## Limitations

* One backend (`ibm_marrakesh`), three dates: 2026-11-06 (29 envelope probes, historical, run once), 2026-11-09 (legacy latency ladder), 2026-11-15 (everything else); unknown controller firmware. Limits and latencies can change with firmware or backend; results older than a few weeks should be re-run.
* Latency: 5 shapes in this round plus a 4-job legacy ladder; one job per shape; headline errors are jackknife over 24 probes with probe chi2/dof up to 58; estimator choice moves point estimates by up to 440 ns;
  X-type and Z-type probes disagree; one replication within the day (the second session); the null job (a case identical to the reference) reads +65 +/- 27 ns, so a false increment of about that size exists.
* Op classes (Bool vs Uint), input count and fixed cost are collinear in the design; compare and register-wide (SIMD) ops have designed probes (`ideal` tier) but no data.
* The 6057 pool description is fitted after the fact on 25 probes (each executed twice in round 4); it has not been tested on held-out probes, ties at pool 14 and two exceptions exist, the second round-4 execution was an accidental duplicate (usable as a replicate, not an independent session), and the accounting of temporaries is a guess at the controller compiler's behaviour.
* "Pre-registered" means written into the manifest before submission, with no external timestamp; later rounds were designed after earlier results.

## Running it

Requires Python 3, `qiskit==2.6.2`, `qiskit-ibm-runtime==0.51.0`, `numpy` (see `requirements.txt`). Nothing is installed by this folder.

```
python suite.py --local                      # offline: exhaustive semantic checks, guards, transpile (fake backend), QPY round trip
python suite.py --local --target <backend>   # same, transpiled against the live target (read-only, needs a saved IBM account)
python test_suite.py                         # offline tests for submit/fetch guards using a fake adapter
python suite.py --plan --tier minimal        # jobs, shots, estimated QPU-seconds, pre-registered predictions (also: ideal, both)
python suite.py --submit --tier regbisect --backend <backend>              # DRY RUN: builds and transpiles, prints layout, estimate, quota, PLAN HASH
python suite.py --submit --tier regbisect --backend <backend> --execute --confirm <hash>   # REAL SUBMISSION
python suite.py --fetch out/manifest_<hash>.json [--wait] --backend <backend>               # download, analyse, write out/RESULTS_QPU*.md
python reanalyse_stopwatch.py                # rebuild the legacy 4-job latency ladder analysis from data/legacy_stopwatch
```

**`--submit --execute` spends your IBM quota.** It runs only with a `--confirm` equal to the printed plan hash (a hash of the transpiled circuits, shots and layout, so any change to the plan or
to live calibration invalidates it), and refuses if the estimate exceeds 60% of the remaining quota read from your account. The default is a dry run. Tiers: `minimal` (latency + a few acceptance probes,
charged 54 s on 2026-11-15; estimate 44 s), `regbisect` (18 s), `split` (10 s), `ideal` (estimate about 140 s, likely 20-35% more for latency jobs). Jobs run in job mode (the Open plan has no sessions). Estimates assume ~2 s billing floor per job.
Latency jobs use 24 sources + 24 probe qubits + a flag qubit selected from live calibration; register and split jobs use the best-readout qubits.

**Analysis defaults:** `stopwatch_core.analyse_data` defaults to the legacy reference delays (0, 250, 500, 1000); always pass `ref_D` from the manifest (this suite does).

## Pre-flight checker (`dccheck.py`)

Predicts, before you submit, whether the controller is likely to reject a dynamic circuit (error 6057 and a few other patterns) and roughly how long each in-controller decision takes. It follows every `store` into every `if_else` condition (including nested blocks) and compares the result with the **rules file** `rules/ibm_heron_r2_2026-11-15.json` (hazards, accepted constructs, IBM's documented limits, latency) and the **envelope** `rules/envelope_ibm_heron_r2_2026-11-15.json` (resource vectors of all probes run on the QPU). Nothing is hard-coded; every message it prints carries either a verbatim IBM quote with its URL or a measured probe (`test_dccheck.py` asserts this).

```
python dccheck.py circuit.qpy                                 # a QPY file; the circuit is ALSO transpiled here and the verdict is the worst of both (several circuits = one job)
python dccheck.py mypkg.mymod:build_circuit                   # or file.py:func / module:func (func() returns a QuantumCircuit)
python dccheck.py circuit.qpy --transpiled isa.qpy            # your own ISA circuit, checked IN ADDITION to the one transpiled here (`--transpiled` can only make the verdict stricter)
python dccheck.py isa.qpy --no-transpile                      # check exactly the circuit as passed (valid only if it is the one that runs)
python dccheck.py circuit.qpy --json --backend ibm_fez        # machine output; warns if the backend is not covered by the rules
python dccheck.py circuit.qpy --fix-pack -o packed.qpy        # repack measured registers (below)
python dccheck.py dccheck_examples:many_small_registers       # demo: 12 registers x 2 bits, rejected on ibm_marrakesh
```
```python
from dccheck import preflight, check, check_logical, load_rules, format_report, fix_pack
rep = preflight(qc, load_rules())   # worst of the circuit as passed and as transpiled: rep["status"], rep["logical"], rep["transpiled"], rep["differences"]
rep["logical"]["ifs"][i]["checks"]  # each check has "sources"
check(qc)          # same verdict as preflight() and the CLI default (as-passed report plus status_logical, transpiled, differences)
check_logical(qc)  # the as-passed analysis alone, NO verdict for what runs
print(format_report(rep))
```
**The verdict is for the circuit that actually runs.** By default dccheck transpiles the circuit against a bundled fake Heron r2 backend (`FakeMarrakesh`, or the fake named by `--backend` if bundled; `measure_2` added, optimization level 1, seed 7, the suite's settings; not the live device), shows both results, flags any change in the resource vector (a transpiler can hoist `measure_2` and raise the peak of live registers: a staged circuit that is PASS as written is FAIL once transpiled) and reports the WORST. A circuit that cannot be transpiled for the check is capped at UNCERTAIN. **`--transpiled` can only make the verdict stricter.** The circuit is always transpiled here and the verdict is the worst of the circuit as passed, its own transpilation and the circuit you supply; if the own transpilation is impossible (for example, too wide for the bundled target) the verdict is capped at UNCERTAIN whatever you supply, and the supplied circuit only adds a note (counts correspondence, and a sampled, not exhaustive, check that it makes the same classical decisions). A circuit without any `if_else` is reported NOT APPLICABLE (no classical feedforward; the limits measured here do not apply and no probe had no control flow), not PASS. An analysis it cannot complete (a construct the resource counter cannot follow, an internal error) is UNCERTAIN or ERROR, never a verdict. Every PASS output ends with the monotonicity assumption.
Exit status: 0 PASS, 1 FAIL, 2 UNCERTAIN (no FAIL), 3 internal error (no verdict), 4 NOT APPLICABLE, 64 could not load. FAIL and UNCERTAIN output quotes IBM's text, then our numbers labelled "measured empirically on ibm_marrakesh 2026-11-15, not published by IBM". A warning appears when the rules file is older than `--max-age-days` (default 60) or the backend is not one the rules were measured on.

**Verdict method: the empirical monotone dominance envelope of Section 2b** (no formula is used: none survived out of sample; the former pool model "peak live registers + stack need >= 14" was falsified by round e2 and is neither used nor shown). `resources.resource_vector` (one implementation, shared with the conformance suite) counts 26 resource dimensions of a circuit; `rules/envelope_*.json` holds the vector and outcome of every probe run on the QPU (accepted, rejected with 6057, rejected with another code, which is excluded from the 6057 envelope). Then:
* **PASS** iff the circuit's vector is no larger than that of some **accepted** probe in every dimension;
* **FAIL** iff it is at least as demanding as some **rejected** probe in the failure dimensions of the envelope file, or it hits an official hard rule or an observed hazard (below);
* **UNCERTAIN** otherwise: it exceeds every accepted probe in some dimension, or accepted and rejected probes conflict.
Every verdict names its witness probe and says why, for example "dominated by accepted probe P1_C8chain (ibm_marrakesh, measured 2026-11-15)", "at least as demanding as rejected probe R12x2 in every failure-relevant dimension (peak_live_regs=13>=13, ...)", or "exceeds every accepted probe in some dimension; the closest, C6x16, is exceeded in peak_live_regs (2 > 1), cone_regs (2 > 1), depth (7 > 5)".
**The assumption is monotonicity**; the stored probes do not contradict it, but a few dozen rejections cannot prove it, and the envelope is sparse: **UNCERTAIN is common by design** (PASS needs domination in all dimensions), and a circuit is never PASS because it merely looks small. The register count alone is not the criterion (`P1_staged` has 12 registers in its cone and was accepted; `V1_rc4_P10` has 10 live registers and was rejected because its expression needs a deeper evaluation stack than `B_R10x8`'s). **Single backend (`ibm_marrakesh`), three dates (2026-11-06, 2026-11-09, 2026-11-15); the `ibm_fez` replication is pending.** Regenerate the envelope after each new round; the out-of-sample scoreboard (frozen predictions: e1 5/5, e2 1 correct + 2 UNCERTAIN, 0 wrong) is in Section 2b.

Everything else in the verdict:

| what | verdict | basis |
|---|---|---|
| nested conditional; measure or reset inside a conditional; operand wider than 32 bits | FAIL | IBM documentation (quoted) |
| store with a narrowing cast of a shifted wide register (shift by literal + Cast Uint(32)->Uint(8)) | FAIL (error 6066) | **observed on ibm_marrakesh 2026-11-15**: both fix-pack v1 circuits using it (`FP_R12x8`, `FP_S24`) were rejected; the one that did not (`FP_B14x3`, clbit-level reads) was accepted; IBM's text for 6066 is only "Internal Compiler Error" |
| any expression construct that no accepted probe used (an op/type, a cast, a left shift, ...) | UNCERTAIN | `accepted_constructs` in the rules file, each backed by accepted probes |
| Uint literals >= 16 bits in the cone | UNCERTAIN | pattern of earlier failing probes |
| `while` / `for` / `switch` | UNCERTAIN | IBM's pages contradict each other (both quoted) |
| a Var in the cone | UNCERTAIN | no measured data |
| overlapping registers (6327), declared Var with literal initialiser (6066), shift by non-literal (6060), copy store (6062), non-literal bit index, Uint literal >= 2^32 | FAIL | earlier-day rejections recorded in `suite.py` `guards()`, not re-measured in `data/` |

Arithmetic in a cone produces an informational note, not a verdict: IBM says "Arithmetic operations are not supported" while its feature table says "partial", and add/compare worked in the measured probes. A job-level line counts unique broadcasts (a repeated `if` on an unchanged value counts once) and compares them with IBM's two quoted per-job figures (approximately 300 broadcasts at 60 bits, 2400 at 1 bit; interpolated, informational only).
**Latency** is reported as a central value (`a + b*ops` fit; ops = store ops in the cone plus the condition except a final compare/bit index) with a deliberately wide range: slope 25 to 30 ns/op, intercept 0 to 400 ns, per-point scatter 90 to 230 ns (independent review). It is not a law and not a statistical interval; outside the 15 to 63 ops actually measured it is labelled EXTRAPOLATION. It excludes the measurement itself (`measure_2` about 1.88 us).

**What dccheck is checked against** (`python test_dccheck.py`, which prints the matrices; `python test_adversarial.py` runs 43 perturbations (and asserts that `check()`, `preflight()` and the CLI default return the same verdict for them and for every rebuildable probe) of rejected probes and risky constructs plus 10 fix-pack cases from the independent final review, including legacy tuple conditions, deep chains, staged circuits that the transpiler reorders, and the exit codes): every probe of the envelope file (all rounds incl. p1, v1, e1, e2, fix-pack v1, old-day shapes). Probes that can be rebuilt here (suite builders, `p1_probes.py`, the fix-pack QPY files) go through `dccheck.check` on their circuit and their resource vector must equal the stored one; the others use the stored vector. In-sample: 0 wrong and no rejected probe is ever PASS (the two fix-pack v1 circuits rejected with 6066 are caught by the observed hazard). Leave-one-probe-out (the probe is removed from the envelope, then predicted; 6057 probes): rejected probe predicted PASS = 0 (the critical number), accepted probe predicted FAIL = 0, about half of the accepted probes PASS and the rest UNCERTAIN (the headline unique-vector numbers are in Section 2b). The `v1` round is also tested out of sample (with the five v1 probes removed, `V1_rc4_P10` is not PASS). The default check transpiles against the bundled FakeMarrakesh; `test_adversarial.py` asserts that `check()`, `preflight()` and the CLI agree on every rebuildable probe. Latency caveats of Section 1 apply to the reported latency range: a null job reads +65 ns and the reference-placement check is unresolved, so the range is wide.

### `--fix-pack` (v2: remap only)
Repacks measured registers into a few wide registers (<= 32 bits each; width is free) **using only constructs that an accepted QPU probe used**. v1 read the wide registers with shifts and narrowing casts, and the QPU rejected those circuits with 6066 (`FP_R12x8`, `FP_S24`; instruction diff against the accepted `FP_B14x3`: only the failing circuits contain `SHIFT_LEFT`/`SHIFT_RIGHT` on `Uint(32)`/`Uint(16)` registers and narrowing `Cast Uint(32)->Uint(8)/Uint(2)`; the accepted one has only Bool XORs of single clbits stored into a clbit). v2 therefore never emits a shift or a cast (**QPU evidence, ibm_marrakesh 2026-11-15:** v1 `FP_R12x8` and `FP_S24` rejected with 6066, v1 `FP_B14x3` accepted, `data/RESULTS_QPU_fixpack_v1.md`; v2 `FP2_R12x8`, `FP2_S24` and `FP2_R12x2` all accepted and computing correctly with every last/mid/all pattern above chance, `data/RESULTS_QPU_fixpack_v2.md`; `FP2_S24` was predicted UNCERTAIN and accepted):
* measurements keep their clbits (same objects, same order); only the register grouping changes;
* a whole-register Uint store built from bitwise ops (`S = G0 ^ G1 ^ ...`) is **expanded** to bit-level stores `S[k] = G0[k] ^ G1[k] ^ ...` for exactly the bits something reads (`if S[0]` needs lane 0 only); unread bits are not written (a stored value is not returned in the job result);
* `Index(register, literal)` becomes the clbit itself.
It **refuses** (with the reason) when a whole-register value feeds a shift, add, compare or cast, for a whole-register store inside a control-flow body, declared Vars, `switch_case`, overlapping registers, nothing mergeable (a measured register read as a whole by a compare simply stays a register), a result that does not lower the live-register peak, a hard rule, a result whose transpiled form (the circuit that would run) is FAIL, or ANY construct of the result (newly emitted or carried over from the input) missing from the rules' `accepted_constructs` (so an input with an uncovered construct, such as a whole-register compare, is refused, not carried through). A stored register read as a whole value by a compare or a legacy `(register, value)` condition is refused explicitly: expanding it would need compare/logic constructs no accepted probe used. It proves equivalence with a local classical evaluator (checked against qiskit-aer): original and rewritten circuit run on the same measurement outcomes (exhaustive if <= 14 measurement events, otherwise structured plus 2000 random patterns), comparing every branch decision, executed gate and the final value of every measured clbit; it round-trips QPY and transpiles (the same bundled fake as the check), and reports before/after peak live registers, stack need, measured registers and ops. `fixpack_qpu.py` is the guarded QPU plan used for that verification (v2, names `FP2_*`). The evidence covers these three shapes ("computes correctly" here means every pattern is above chance, 0.67 to 0.92 correct, limited by readout: each packed lane is shown to matter, not that the value is exact); another packed circuit is covered by the envelope and the construct whitelist, not by its own run.

Example (`python dccheck.py dccheck_examples:many_small_registers --fix-pack`): 12 registers x 2 bits, 13 live registers, FAIL (6057 expected) -> one 24-bit register, 1 live register, PASS, 2052 outcome patterns identical.

### What dccheck does NOT know
* **Firmware or backend changes.** The measured rules are observations of one backend (`ibm_marrakesh`) on three dates (2026-11-06, 2026-11-09, 2026-11-15; firmware not exposed), not a model of the controller, and several earlier-day failures are unexplained and not reproduced; a threshold can move without notice and the age warning is the only guard. IBM publishes no number for any of them.
* The 6057 mechanism has no validated formula (the best post-hoc pool model has errors); the verdict is an empirical envelope and is only as good as the measured points and the monotonicity assumption. Regenerate the rules after each new round.
* Repetitions K and PUBs per job and firmware changes are not modelled (`known_unmodeled` in the rules file); the old W512 live-values shape enters only as one rejected point.
* Latency: 5 shapes, probe scatter several times the shot noise, intercept and Bool/Uint split unresolved.
* Accepted does not mean correct: the controller can accept a program and the QPU result still depend on readout and timing.

### Regenerating the rules
Rules are derived, never typed in. Inputs: results (`data/probe_outcomes*.json`-format files or fetched suite manifests) and a curation file (`rules/curation_*.json`: official IBM quotes with URLs, documented and observed hazards, the explicit `uncertain` list, latency ranges, the envelope file name). Every probe circuit is rebuilt with `suite.py` / `p1_probes.py` (or loaded from the QPY files in `data/fixpack_candidates*/`) and analysed with `dccheck`; the expression constructs of accepted probes become `accepted_constructs`, the latency fit comes from the stopwatch probes. The resource envelope itself is written by the conformance suite (`resources.py`).
```
python suite.py --submit/--fetch ...                          # as above: produces out/manifest_<hash>.json + out/fetch_<hash>.json
python suite.py --export-rules out/manifest_<hash>.json [more manifests or probe_outcomes json] \
       --curation rules/curation_ibm_heron_r2.json --rules-out rules/<backend_family>_<date>.json
python export_rules.py data/probe_outcomes.json data/probe_outcomes_round4_p1.json data/probe_outcomes_v1.json data/probe_outcomes_fixpack_v1.json data/probe_outcomes_fixpack_v2.json --curation rules/curation_ibm_heron_r2.json --out rules/ibm_heron_r2_2026-11-15.json   # same thing, standalone
```
`dccheck` uses the newest file in `rules/` (by `measured_date`) unless `--rules FILE` is given; files are validated against `rules/rules.schema.json` on load. `test_dccheck.py` fails if the committed rules file is out of sync with `data/` + curation.

## Contributing results from other backends

Run the same tiers on your backend and send (a) the generated `out/RESULTS_QPU*.md`, (b) `out/fetch_<hash>.json` (per-job status, error code and full error message), (c) the raw `out/qpu_*.npz` bits and the manifest (layout, plan hash, reference delays), and (d) software versions, date, UTC window and the processor/firmware version if you know it. `data/probe_outcomes.json` is an assembled summary of this run's results, not a suite output. Failed jobs are data: keep the code and the complete message.

## Layout

`dccheck.py` pre-flight checker (CLI + API) / `test_adversarial.py` adversarial regression tests / `dcfix.py` `--fix-pack` and the equivalence evaluator / `dcrules.py` rules loading, validation, evaluation, derivation / `export_rules.py` rules exporter / `rules/` (rules file, envelope file, schema, curation) / `dccheck_examples.py` / `fixpack_qpu.py` / `p1_probes.py` /
`resources.py` resource vectors and the envelope verdict (one implementation) / `build_envelope.py` regenerates the envelope from the shipped circuits / `suite.py` harness (probes, guards, plan, guarded submit, fetch, analysis, `--export-rules`) / `tiers_v2.py` later probe tiers (p1-p3, v1, e2) / `estimators.py` robust latency estimators / `stopwatch_core.py` stopwatch analysis (vendored subset) /
`test_suite.py`, `test_resources.py`, `test_dccheck.py` / `reanalyse_stopwatch.py` (legacy ladder) /
`data/` results: `RESULTS_QPU_*.md`, `probe_outcomes*.json`, `run_metadata.json` (every QPU job), `predictions_round*.txt`, `latency_reanalysis.json`, `latency_fit.json`, `modelfit.json` (a record of the model-fit table; its inputs are shipped in `envelope_probes/` but the script is not), raw probe bits in `legacy_stopwatch/` and `round1_latency/`, `p1/`, `p2/`, `v1/`, `e1/`, `e2/` (results, scrubbed manifests with the frozen predictions, plan QPY),
`envelope_probes/` (one circuit per envelope probe + `index.json`), `fixpack_*` (packed circuits and outcomes), `rederive/` (estimator and fit scripts).
IBM job ids are not published; they are only resolvable by the IBM account that ran them.

## Licence
Apache-2.0 (see `LICENSE` and `NOTICE`). Copyright 2026 The feedforward-preflight authors. Every `.py` file carries an SPDX identifier.
