# Findings

> Paths such as `data/...` and `rules/...` are relative to the repository root. Back to the [README](../README.md).

## Scope and status
Measurements of what IBM's real-time classical engine does with `store` / `if` expressions: how long an expression takes to evaluate between a mid-circuit
measurement and the conditional gate, and which programs the controller refuses (error 6057). IBM names the error and publishes no numbers for registers, ops or latency. This folder is a test harness plus
the data from the first runs. Status: one backend (`ibm_marrakesh`), three dates: 2026-11-06 (29 envelope probes, historical, run once), 2026-11-09 (legacy latency ladder), 2026-11-15 (everything else); results are dated observations and not guarantees.

**The one assumption to read first: monotonicity.** The checker's PASS verdict rests on an assumption, not a law: a circuit that uses no more of every measured resource (peak live registers, stack need, ops, ...) than some accepted probe is predicted to be accepted. Nothing guarantees that; the 27 rejected probes only show that the data do not contradict it. A PASS is a prediction from similarity to probes run once on one backend, never a guarantee, and many realistic circuits are UNCERTAIN.

Run summary (rounds 1-3; round 4 is described under Findings 2 and in `data/run_metadata.json`: 25 probes executed twice, 52 QPU-seconds charged per execution): backend `ibm_marrakesh` (Heron r2, backend version 1.0.21, firmware not exposed), Open plan, 2026-11-15 14:07-14:29 UTC, qiskit 2.6.2, qiskit-ibm-runtime 0.51.0,
executor Sampler, transpiler `optimization_level=1`, `seed_transpiler=7`, fixed `initial_layout`. Three rounds: 12 jobs (latency + acceptance, plan `c4189b3af6be`), 9 jobs (register-count
bisection, `ef2d74ac0e55`), 5 jobs (split stores, `138f38fc85af`); 82 QPU-seconds charged (54 + 18 + 10; the cost model estimated 44 + 18 + 10: latency jobs were charged 8 s each against an
estimate of about 5.9 s). Per-job charges, UTC windows, layouts and the calibration timestamp are in `data/run_metadata.json`. Round 2 and 3 plans were designed after seeing the earlier rounds.

## Measurements

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
