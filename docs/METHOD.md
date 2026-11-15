# Method, usage and reference

> Paths such as `data/...` and `rules/...` are relative to the repository root. Back to the [README](../README.md).

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

Predicts, before you submit, whether the backend is likely to reject a dynamic circuit (error 6057 and a few other patterns) and roughly the observed decision latency. It follows every `store` into every `if_else` condition (including nested blocks) and compares the result with the **rules file** `rules/ibm_heron_r2_2026-11-15.json` (hazards, accepted constructs, IBM's documented limits, latency) and the **envelope** `rules/envelope_ibm_heron_r2_2026-11-15.json` (resource vectors of all probes run on the QPU). Nothing is hard-coded; every message it prints carries either a verbatim IBM quote with its URL or a measured probe (`test_dccheck.py` asserts this).

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
`docs/` this documentation, `docs/make_figures.py` and `docs/img/` (figures regenerated from the shipped data).
