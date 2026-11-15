# QPU results p1, plan 287baa4d87af+A+B on ibm_marrakesh
submitted 2026-11-15 17:15:31; est 51 QPU-s

Op definition: op = one Binary, Unary or Index node of a store/if expression tree, counted on the TRANSPILED circuit; casts and literal nodes count 0; each use of a sub-expression counts again (tree, not DAG); depth = longest root-to-leaf chain of such nodes.
Metrics are counted on the TRANSPILED circuits (asserted equal to the builder at plan time).
Backend info at submission: {"name": "ibm_marrakesh", "processor_type": {"family": "Heron", "revision": "2"}, "backend_version": "1.0.21", "status": "active", "operational": true, "pending_jobs": 10, "properties_last_update": "2026-11-13 19:22:56+02:00", "read_at_utc": "2026-11-15T15:15:32Z"}

| probe | cone regs | peak live | temps | total reads | stores | max store ops | cone ops | unique nodes | result | code | informative patterns responding (CI lower > 0.6) | per-pattern correct [95% CI] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P1_R11x2_a | 11 | 12 | 2 | 11 | 1 | 10 | 10 | 10 | ACCEPTED |  | 3/3 | Z:0.93 [0.89,0.95]; A*:0.88 [0.84,0.91]; B:0.92 [0.89,0.94]; C*:0.78 [0.73,0.81]; D*:0.92 [0.88,0.94] |
| P1_R11x2_b | 11 | 12 | 2 | 11 | 1 | 10 | 10 | 10 | ACCEPTED |  | 3/3 | Z:0.93 [0.90,0.95]; A*:0.89 [0.85,0.91]; B:0.88 [0.85,0.91]; C*:0.81 [0.77,0.85]; D*:0.92 [0.89,0.94] |
| P1_R11x2_c | 11 | 12 | 2 | 11 | 1 | 10 | 10 | 10 | ACCEPTED |  | 3/3 | Z:0.91 [0.88,0.94]; A*:0.89 [0.86,0.92]; B:0.91 [0.88,0.94]; C*:0.78 [0.73,0.82]; D*:0.93 [0.89,0.95] |
| P1_R10x8_a | 10 | 11 | 2 | 10 | 1 | 9 | 9 | 9 | ACCEPTED |  | 3/3 | Z:0.72 [0.68,0.77]; A*:0.73 [0.68,0.77]; B:0.68 [0.63,0.72]; C*:0.73 [0.69,0.77]; D*:0.74 [0.69,0.78] |
| P1_R12x2_a | 12 | 13 | 2 | 12 | 1 | 11 | 11 | 11 | ERROR | 6057 |  |  |
| P1_R10x8_b | 10 | 11 | 2 | 10 | 1 | 9 | 9 | 9 | ACCEPTED |  | 3/3 | Z:0.73 [0.69,0.78]; A*:0.73 [0.68,0.77]; B:0.72 [0.68,0.77]; C*:0.76 [0.71,0.79]; D*:0.68 [0.64,0.73] |
| P1_R12x2_b | 12 | 13 | 2 | 12 | 1 | 11 | 11 | 11 | ERROR | 6057 |  |  |
| P1_overlapNF | 10 | 11 | 2 | 14 | 3 | 5 | 11 | 11 | ACCEPTED |  | 4/4 | Z:0.97 [0.95,0.98]; A*:0.95 [0.92,0.97]; B:0.94 [0.91,0.96]; C*:0.95 [0.93,0.97]; D*:0.95 [0.92,0.97]; E*:0.96 [0.94,0.98] |
| P1_split6 | 12 | 13 | 2 | 14 | 3 | 5 | 11 | 11 | ERROR | 6057 |  |  |
| P1_staged | 12 | 8 | 2 | 14 | 3 | 5 | 11 | 11 | ACCEPTED |  | 3/3 | Z:0.90 [0.87,0.93]; A*:0.89 [0.86,0.92]; B:0.90 [0.87,0.93]; C*:0.92 [0.88,0.94]; D*:0.92 [0.89,0.94] |
| P1_alloc4 | 3 | 7 | 3 | 16 | 5 | 6 | 27 | 28 | ACCEPTED |  | 2/2 | Z:0.82 [0.78,0.85]; A:0.89 [0.86,0.92]; B:0.94 [0.91,0.96]; C*:0.93 [0.89,0.95]; D*:0.85 [0.81,0.88]; E:0.79 [0.74,0.82] |
| P1_alloc8 | 3 | 11 | 3 | 32 | 9 | 7 | 55 | 56 | ERROR | 6057 |  |  |
| P1_alloc12 | 3 | 15 | 3 | 48 | 13 | 11 | 83 | 84 | ERROR | 6057 |  |  |
| P1_ir96 | 4 | 4 | 5 | 4 | 1 | 96 | 96 | 96 | ACCEPTED |  | 2/2 | A*:0.98 [0.96,0.99]; B*:0.99 [0.97,0.99] |
| P1_ir104 | 4 | 4 | 6 | 4 | 1 | 104 | 104 | 104 | ERROR | 6057 |  |  |
| P1_ir126 | 4 | 4 | 5 | 4 | 1 | 126 | 126 | 126 | ERROR | 6057 |  |  |
| P1_dag104 | 4 | 4 | 6 | 4 | 1 | 104 | 104 | 56 | ERROR | 6057 |  |  |
| P1_bal104 | 4 | 4 | 7 | 4 | 1 | 104 | 104 | 104 | ACCEPTED |  | 2/2 | A*:0.78 [0.74,0.82]; B*:0.99 [0.98,1.00] |
| P1_chain104 | 4 | 4 | 2 | 4 | 1 | 104 | 104 | 104 | ACCEPTED |  | 2/2 | A*:0.99 [0.98,1.00]; B*:0.99 [0.98,1.00] |
| P1_C8bal | 8 | 8 | 7 | 8 | 1 | 63 | 63 | 63 | ERROR | 6057 |  |  |
| P1_C8chain | 8 | 8 | 2 | 8 | 1 | 63 | 63 | 63 | ACCEPTED |  | 2/2 | A*:0.99 [0.97,0.99]; B*:0.99 [0.98,1.00] |
| P1_old_T1 | 1 | 1 | 5 | 3 | 2 | 103 | 104 | 59 | ERROR | 6057 |  |  |
| P1_old_CONE | 3 | 9 | 5 | 24 | 7 | 28 | 173 | 134 | ERROR | 6057 |  |  |
| P1_old_LUT85K8 | 1 | 1 | 5 | 1 | 1 | 84 | 84 | 49 | ERROR | 6057 |  |  |
| P1_old_W512 | 1 | 1 | 2 | 1534 | 1023 | 2 | 1194 | 514 | ERROR | 6057 |  |  |

(* = informative: its expected output differs from the all-zero pattern's, so ignoring operands cannot reproduce it)

## Full error messages (verbatim)
- P1_R12x2_a: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_R12x2_b: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_split6: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_alloc8: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_alloc12: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_ir104: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_ir126: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_dag104: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_C8bal: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_old_T1: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_old_CONE: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_old_LUT85K8: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`
- P1_old_W512: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`

## Pre-registered predictions vs outcome
| probe | observed | cone | peak_live | alloc | pool_LR (post hoc) | total_reads | cone+store_ops | cone+cone_ops | cone+unique_nodes | cone+depth | historic |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P1_R11x2_a | pass | ? | ? | ? | ? | ? | ? | ? | ? | ? | - |
| P1_R11x2_b | pass | ? | ? | ? | ? | ? | ? | ? | ? | ? | - |
| P1_R11x2_c | pass | ? | ? | ? | ? | ? | ? | ? | ? | ? | - |
| P1_R10x8_a | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| P1_R12x2_a | FAIL | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | - |
| P1_R10x8_b | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| P1_R12x2_b | FAIL | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | - |
| P1_overlapNF | pass | P ok | P ok | P ok | P ok | F WRONG | P ok | P ok | P ok | P ok | - |
| P1_split6 | FAIL | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | - |
| P1_staged | pass | F WRONG | P ok | P ok | P ok | F WRONG | F WRONG | F WRONG | F WRONG | F WRONG | - |
| P1_alloc4 | pass | P ok | P ok | P ok | P ok | F WRONG | P ok | P ok | P ok | P ok | - |
| P1_alloc8 | FAIL | P WRONG | P WRONG | ? | ? | F ok | P WRONG | P WRONG | P WRONG | P WRONG | - |
| P1_alloc12 | FAIL | P WRONG | F ok | F ok | F ok | F ok | P WRONG | P WRONG | P WRONG | P WRONG | - |
| P1_ir96 | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| P1_ir104 | FAIL | P WRONG | P WRONG | P WRONG | ? | P WRONG | F ok | F ok | F ok | P WRONG | - |
| P1_ir126 | FAIL | P WRONG | P WRONG | P WRONG | F ok | P WRONG | F ok | F ok | F ok | P WRONG | - |
| P1_dag104 | FAIL | P WRONG | P WRONG | P WRONG | ? | P WRONG | F ok | F ok | P WRONG | P WRONG | - |
| P1_bal104 | pass | P ok | P ok | P ok | P ok | P ok | F WRONG | F WRONG | F WRONG | P ok | - |
| P1_chain104 | pass | P ok | P ok | P ok | P ok | P ok | F WRONG | F WRONG | F WRONG | ? | - |
| P1_C8bal | FAIL | P WRONG | P WRONG | F ok | F ok | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | - |
| P1_C8chain | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| P1_old_T1 | FAIL | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | F ok | F ok | P WRONG | P WRONG | F ok |
| P1_old_CONE | FAIL | P WRONG | P WRONG | ? | F ok | F ok | P WRONG | F ok | F ok | P WRONG | F ok |
| P1_old_LUT85K8 | FAIL | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | F ok |
| P1_old_W512 | FAIL | P WRONG | P WRONG | F ok | F ok | F ok | P WRONG | F ok | F ok | P WRONG | F ok |
| score ok / wrong | | 11 / 11 | 13 / 9 | 15 / 5 | 17 / 2 | 13 / 9 | 13 / 9 | 15 / 7 | 13 / 9 | 10 / 11 | 4 / 0 |

## Verdict lines (rule-based; read against the tables)
- R11x2 (3 runs): all ACCEPTED -> theta = 12
- R10x8 replicates: [True, True] (expected accepted): as expected
- R12x2 replicates: [False, False] (expected 6057): as expected
- non-foldable overlap (cone 10, total reads 14, xor vs add): ACCEPTED -> total reads is not the trigger
- staged liveness: staged accepted, same stores unstaged FAILED -> distinct measured registers in the cone is not the trigger; simultaneous liveness is
- allocation pressure (3 measured registers, k stored Uint16 intermediates): {'P1_alloc4': True, 'P1_alloc8': False, 'P1_alloc12': False} -> failures grow with k: register-allocation pressure (stored registers) matters even with only 3 measured registers
- irreducible op-count bisection (distinct leaves, 4 measured registers): {'P1_ir96': True, 'P1_ir104': False, 'P1_ir126': False, 'P1_dag104': False}
- DAG vs tree at 104 expanded ops: same outcome -> the count is not on unique nodes
- P1_old_T1 (old shape, replicated today): FAILED again -> still present today
- P1_old_CONE (old shape, replicated today): FAILED again -> still present today
- P1_old_LUT85K8 (old shape, replicated today): FAILED again -> still present today
- P1_old_W512 (old shape, replicated today): FAILED again -> still present today
- 104 ops, balanced tree vs left chain (4 registers): bal accepted, chain accepted -> shape does not matter
- 8 measured registers + 63 ops: balanced FAILED, chain accepted -> combined pressure (registers + temporaries) triggers 6057 although each single-factor rule predicts a pass: H_alloc supported
- failure codes observed: 6057: P1_R12x2_a, P1_R12x2_b, P1_split6, P1_alloc8, P1_alloc12, P1_ir104, P1_ir126, P1_dag104, P1_C8bal, P1_old_T1, P1_old_CONE, P1_old_LUT85K8, P1_old_W512. IBM's registry: 6057 = more classical registers than the control hardware provides; 6073 = classical-control MEMORY limit; 6116 = control-system resources / amount of control flow. A 6073 or 6116 here would point to a different resource than registers.
- Hypotheses with no wrong prediction among this tier's outcomes: none

## Replicate consistency (run A = recovered duplicate submission, run B = manifest_<hash>.json)
Mapping check: same circuit names per probe, same status and error code in A and B, same result keys: **ALL CONSISTENT** (a mismatch would mean the recovered run-A job mapping is wrong).

| probe | A | B | circuits match | outcome match | result keys match |
|---|---|---|---|---|---|
| P1_R11x2_a | DONE | DONE | yes | yes | yes |
| P1_R11x2_b | DONE | DONE | yes | yes | yes |
| P1_R11x2_c | DONE | DONE | yes | yes | yes |
| P1_R10x8_a | DONE | DONE | yes | yes | yes |
| P1_R12x2_a | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_R10x8_b | DONE | DONE | yes | yes | yes |
| P1_R12x2_b | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_overlapNF | DONE | DONE | yes | yes | yes |
| P1_split6 | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_staged | DONE | DONE | yes | yes | yes |
| P1_alloc4 | DONE | DONE | yes | yes | yes |
| P1_alloc8 | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_alloc12 | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_ir96 | DONE | DONE | yes | yes | yes |
| P1_ir104 | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_ir126 | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_dag104 | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_bal104 | DONE | DONE | yes | yes | yes |
| P1_chain104 | DONE | DONE | yes | yes | yes |
| P1_C8bal | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_C8chain | DONE | DONE | yes | yes | yes |
| P1_old_T1 | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_old_CONE | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_old_LUT85K8 | ERROR 6057 | ERROR 6057 | yes | yes | yes |
| P1_old_W512 | ERROR 6057 | ERROR 6057 | yes | yes | yes |

## Per-replicate vs pooled correctness for informative patterns (pooled CI = both runs concatenated)
| probe | pattern | A | B | pooled [95% CI] |
|---|---|---|---|---|
| P1_R11x2_a | A | 0.86 | 0.90 | 0.880 [0.844,0.908] |
| P1_R11x2_a | C | 0.81 | 0.74 | 0.775 [0.732,0.813] |
| P1_R11x2_a | D | 0.93 | 0.90 | 0.915 [0.884,0.939] |
| P1_R11x2_b | A | 0.89 | 0.89 | 0.887 [0.853,0.915] |
| P1_R11x2_b | C | 0.80 | 0.83 | 0.815 [0.774,0.850] |
| P1_R11x2_b | D | 0.91 | 0.93 | 0.917 [0.886,0.941] |
| P1_R11x2_c | A | 0.89 | 0.89 | 0.890 [0.856,0.917] |
| P1_R11x2_c | C | 0.82 | 0.73 | 0.777 [0.734,0.816] |
| P1_R11x2_c | D | 0.92 | 0.94 | 0.925 [0.895,0.947] |
| P1_R10x8_a | A | 0.72 | 0.73 | 0.730 [0.684,0.771] |
| P1_R10x8_a | C | 0.74 | 0.72 | 0.733 [0.687,0.774] |
| P1_R10x8_a | D | 0.73 | 0.74 | 0.738 [0.692,0.778] |
| P1_R10x8_b | A | 0.71 | 0.74 | 0.728 [0.682,0.769] |
| P1_R10x8_b | C | 0.74 | 0.77 | 0.755 [0.711,0.795] |
| P1_R10x8_b | D | 0.68 | 0.69 | 0.682 [0.635,0.726] |
| P1_overlapNF | A | 0.96 | 0.93 | 0.948 [0.921,0.965] |
| P1_overlapNF | C | 0.95 | 0.95 | 0.953 [0.927,0.969] |
| P1_overlapNF | D | 0.94 | 0.96 | 0.948 [0.921,0.965] |
| P1_overlapNF | E | 0.95 | 0.97 | 0.963 [0.939,0.977] |
| P1_staged | A | 0.89 | 0.89 | 0.890 [0.856,0.917] |
| P1_staged | C | 0.94 | 0.89 | 0.915 [0.884,0.939] |
| P1_staged | D | 0.92 | 0.92 | 0.917 [0.886,0.941] |
| P1_alloc4 | C | 0.93 | 0.93 | 0.925 [0.895,0.947] |
| P1_alloc4 | D | 0.86 | 0.83 | 0.848 [0.809,0.879] |
| P1_ir96 | A | 0.98 | 0.97 | 0.980 [0.961,0.990] |
| P1_ir96 | B | 0.99 | 0.98 | 0.988 [0.971,0.995] |
| P1_bal104 | A | 0.79 | 0.78 | 0.780 [0.737,0.818] |
| P1_bal104 | B | 0.99 | 0.99 | 0.995 [0.982,0.999] |
| P1_chain104 | A | 0.98 | 1.00 | 0.993 [0.978,0.997] |
| P1_chain104 | B | 0.99 | 0.99 | 0.995 [0.982,0.999] |
| P1_C8chain | A | 0.98 | 0.99 | 0.988 [0.971,0.995] |
| P1_C8chain | B | 0.99 | 0.99 | 0.993 [0.978,0.997] |

## POST HOC register-pool model (fitted after seeing run A and B; the pre-registered H_alloc used the Ershov number and failed on several probes, see the scoreboard)
pool = peak live registers (measured + register-valued stores) + naive left-to-right expression-stack need of the largest stored expression (left operand held while the right is evaluated; leaf = 1). Bit-level stored variables are a separate pool (W512 has 513 live bit variables).
| probe | outcome | peak live | LR stack | pool | bit vars | Ershov temps |
|---|---|---|---|---|---|---|
| P1_old_W512 | FAIL | 1 | 2 | 3 | 513 | 2 |
| P1_chain104 | pass | 4 | 2 | 6 | 1 | 2 |
| P1_C8chain | pass | 8 | 2 | 10 | 1 | 2 |
| P1_alloc4 | pass | 7 | 3 | 10 | 0 | 3 |
| P1_staged | pass | 8 | 2 | 10 | 0 | 2 |
| P1_bal104 | pass | 4 | 7 | 11 | 1 | 7 |
| P1_ir96 | pass | 4 | 8 | 12 | 1 | 5 |
| P1_old_LUT85K8 | FAIL | 1 | 11 | 12 | 1 | 5 |
| P1_old_T1 | FAIL | 1 | 11 | 12 | 2 | 5 |
| P1_R10x8_a | pass | 11 | 2 | 13 | 0 | 2 |
| P1_R10x8_b | pass | 11 | 2 | 13 | 0 | 2 |
| P1_overlapNF | pass | 11 | 2 | 13 | 0 | 2 |
| P1_R11x2_a | pass | 12 | 2 | 14 | 0 | 2 |
| P1_R11x2_b | pass | 12 | 2 | 14 | 0 | 2 |
| P1_R11x2_c | pass | 12 | 2 | 14 | 0 | 2 |
| P1_alloc8 | FAIL | 11 | 3 | 14 | 0 | 3 |
| P1_dag104 | FAIL | 4 | 10 | 14 | 1 | 6 |
| P1_ir104 | FAIL | 4 | 10 | 14 | 1 | 6 |
| P1_C8bal | FAIL | 8 | 7 | 15 | 1 | 7 |
| P1_R12x2_a | FAIL | 13 | 2 | 15 | 0 | 2 |
| P1_R12x2_b | FAIL | 13 | 2 | 15 | 0 | 2 |
| P1_ir126 | FAIL | 4 | 11 | 15 | 1 | 5 |
| P1_split6 | FAIL | 13 | 2 | 15 | 0 | 2 |
| P1_old_CONE | FAIL | 9 | 7 | 16 | 0 | 5 |
| P1_alloc12 | FAIL | 15 | 3 | 18 | 0 | 3 |

Pass pools: max 14. Fail pools: min 3. Failures at or below the highest pass: P1_alloc8 (pool 14, bit vars 0), P1_ir104 (pool 14, bit vars 1), P1_dag104 (pool 14, bit vars 1), P1_old_T1 (pool 12, bit vars 2), P1_old_LUT85K8 (pool 12, bit vars 1), P1_old_W512 (pool 3, bit vars 513). Treat the model as a description of these 25 probes, not a validated law: ties at pool 14 (R11x2 passes; alloc8, ir104, dag104 fail), and the two Shannon-form expressions (old T1, LUT85) fail at pool 12 (they contain NOT operators), so temporaries are not counted exactly right.
