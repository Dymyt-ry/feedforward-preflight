# QPU results v1, plan d04c4f8c8d43 on ibm_marrakesh
submitted 2026-11-15 17:38:17; est 10 QPU-s

Op definition: op = one Binary, Unary or Index node of a store/if expression tree, counted on the TRANSPILED circuit; casts and literal nodes count 0; each use of a sub-expression counts again (tree, not DAG); depth = longest root-to-leaf chain of such nodes.
Metrics are counted on the TRANSPILED circuits (asserted equal to the builder at plan time).
Backend info at submission: {"name": "ibm_marrakesh", "processor_type": {"family": "Heron", "revision": "2"}, "backend_version": "1.0.21", "status": "active", "operational": true, "pending_jobs": 0, "properties_last_update": "2026-11-13 19:22:56+02:00", "read_at_utc": "2026-11-15T15:38:17Z"}

| probe | cone regs | peak live | temps | total reads | stores | max store ops | cone ops | unique nodes | result | code | informative patterns responding (CI lower > 0.6) | per-pattern correct [95% CI] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V1_rc10_P3 | 3 | 3 | 3 | 3 | 1 | 11 | 11 | 11 | ACCEPTED |  | 2/2 | A*:0.99 [0.97,1.00]; B*:0.96 [0.93,0.98] |
| V1_lc10_P3 | 3 | 3 | 2 | 3 | 1 | 11 | 11 | 11 | ACCEPTED |  | 2/2 | A*:0.98 [0.95,0.99]; B*:1.00 [0.98,1.00] |
| V1_rc4_P9 | 9 | 9 | 3 | 9 | 1 | 11 | 11 | 11 | ACCEPTED |  | 2/2 | A*:0.98 [0.95,0.99]; B*:1.00 [0.98,1.00] |
| V1_rc4_P10 | 10 | 10 | 3 | 10 | 1 | 12 | 12 | 12 | ERROR | 6057 |  |  |
| V1_rc12_P1 | 1 | 1 | 3 | 1 | 1 | 12 | 12 | 12 | ACCEPTED |  | 2/2 | A*:0.96 [0.93,0.98]; B*:0.99 [0.97,1.00] |

(* = informative: its expected output differs from the all-zero pattern's, so ignoring operands cannot reproduce it)

## Full error messages (verbatim)
- V1_rc4_P10: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`

## Pre-registered predictions vs outcome
| probe | observed | cone | peak_live | alloc | pool_LR (post hoc) | total_reads | cone+store_ops | cone+cone_ops | cone+unique_nodes | cone+depth | historic |
|---|---|---|---|---|---|---|---|---|---|---|---|
| V1_rc10_P3 | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| V1_lc10_P3 | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| V1_rc4_P9 | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| V1_rc4_P10 | FAIL | P WRONG | P WRONG | P WRONG | ? | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | - |
| V1_rc12_P1 | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| score ok / wrong | | 4 / 1 | 4 / 1 | 4 / 1 | 4 / 0 | 4 / 1 | 4 / 1 | 4 / 1 | 4 / 1 | 4 / 1 | 0 / 0 |

## Verdict lines (rule-based; read against the tables)
- failure codes observed: 6057: V1_rc4_P10. IBM's registry: 6057 = more classical registers than the control hardware provides; 6073 = classical-control MEMORY limit; 6116 = control-system resources / amount of control flow. A 6073 or 6116 here would point to a different resource than registers.
- Hypotheses with no wrong prediction among this tier's outcomes: H_pool_LR

## Validation scoreboard (out-of-sample; models frozen before the run)
| probe | observed | M_w | M_u | M_peak | M_cone | M_SU | M_ops |
|---|---|---|---|---|---|---|---|
| V1_rc10_P3 | pass | F WRONG | P ok | P ok | P ok | P ok | P ok |
| V1_lc10_P3 | pass | P ok | P ok | P ok | P ok | P ok | P ok |
| V1_rc4_P9 | pass | P ok | P ok | P ok | P ok | P ok | P ok |
| V1_rc4_P10 | FAIL | F ok | F ok | P WRONG | P WRONG | P WRONG | P WRONG |
| V1_rc12_P1 | pass | F WRONG | P ok | P ok | P ok | P ok | P ok |
| correct / wrong | | 3 / 2 | 5 / 0 | 4 / 1 | 4 / 1 | 4 / 1 | 4 / 1 |

Verdict: model(s) with no wrong prediction: M_u. A prediction that holds is evidence but not proof (5 probes, one-unit margin at the boundary); a failure falsifies that model as stated.
