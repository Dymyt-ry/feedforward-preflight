# QPU results p3, plan faf87bdc3fa6 on ibm_fez
submitted 2026-11-15 17:31:45 (local, UTC+2; 15:31:45 UTC); executed 2026-11-16 14:00:50 to 14:03:30 UTC (the device was in maintenance at submission); est 10 QPU-s; 11 QPU seconds charged

Op definition: op = one Binary, Unary or Index node of a store/if expression tree, counted on the TRANSPILED circuit; casts and literal nodes count 0; each use of a sub-expression counts again (tree, not DAG); depth = longest root-to-leaf chain of such nodes.
Metrics are counted on the TRANSPILED circuits (asserted equal to the builder at plan time).
Backend info at submission: {"name": "ibm_fez", "processor_type": {"family": "Heron", "revision": "2"}, "backend_version": "1.3.37", "status": "maintenance", "operational": true, "pending_jobs": 82, "properties_last_update": "2026-11-13 18:27:59+02:00", "read_at_utc": "2026-11-15T15:31:45Z"}

| probe | cone regs | peak live | temps | total reads | stores | max store ops | cone ops | unique nodes | result | code | informative patterns responding (CI lower > 0.6) | per-pattern correct [95% CI] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P3_R10x2 | 10 | 11 | 2 | 10 | 1 | 9 | 9 | 9 | ACCEPTED |  | 3/3 | Z:0.78 [0.72,0.83]; A*:0.92 [0.87,0.95]; B:0.90 [0.85,0.93]; C*:0.80 [0.74,0.85]; D*:0.81 [0.74,0.85] |
| P3_R11x2 | 11 | 12 | 2 | 11 | 1 | 10 | 10 | 10 | ACCEPTED |  | 3/3 | Z:0.74 [0.68,0.80]; A*:0.91 [0.86,0.94]; B:0.90 [0.85,0.93]; C*:0.80 [0.74,0.85]; D*:0.81 [0.75,0.86] |
| P3_R12x2 | 12 | 13 | 2 | 12 | 1 | 11 | 11 | 11 | ERROR | 6057 |  |  |
| P3_split6 | 12 | 13 | 2 | 14 | 3 | 5 | 11 | 11 | ERROR | 6057 |  |  |
| P3_overlapNF | 10 | 11 | 2 | 14 | 3 | 5 | 11 | 11 | ACCEPTED |  | 4/4 | Z:0.97 [0.94,0.99]; A*:0.95 [0.91,0.97]; B:0.94 [0.89,0.96]; C*:0.96 [0.92,0.98]; D*:0.96 [0.93,0.98]; E*:0.97 [0.94,0.99] |

(* = informative: its expected output differs from the all-zero pattern's, so ignoring operands cannot reproduce it)

## Full error messages (verbatim)
- P3_R12x2: `Job <id removed> has failed:
Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P3_split6: `Job <id removed> has failed:
Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`

## Pre-registered predictions vs outcome
| probe | observed | cone | peak_live | alloc | pool_LR (post hoc) | total_reads | cone+store_ops | cone+cone_ops | cone+unique_nodes | cone+depth | historic |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P3_R10x2 | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| P3_R11x2 | pass | ? | ? | ? | ? | ? | ? | ? | ? | ? | - |
| P3_R12x2 | FAIL | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | - |
| P3_split6 | FAIL | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | - |
| P3_overlapNF | pass | P ok | P ok | P ok | P ok | F WRONG | P ok | P ok | P ok | P ok | - |
| score ok / wrong | | 4 / 0 | 4 / 0 | 4 / 0 | 4 / 0 | 3 / 1 | 4 / 0 | 4 / 0 | 4 / 0 | 4 / 0 | 0 / 0 |

## Verdict lines (rule-based; read against the tables)
- R11x2 (1 runs): all ACCEPTED -> theta = 12
- non-foldable overlap (cone 10, total reads 14, xor vs add): ACCEPTED -> total reads is not the trigger
- failure codes observed: 6057: P3_R12x2, P3_split6. IBM's registry: 6057 = more classical registers than the control hardware provides; 6073 = classical-control MEMORY limit; 6116 = control-system resources / amount of control flow. A 6073 or 6116 here would point to a different resource than registers.
- Hypotheses with no wrong prediction among this tier's outcomes: H_cone, H_peak_live, H_alloc, H_pool_LR, H_cone+store_ops, H_cone+cone_ops, H_cone+unique_nodes, H_cone+depth
