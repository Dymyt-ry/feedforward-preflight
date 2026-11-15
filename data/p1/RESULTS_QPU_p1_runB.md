# QPU results p1, plan 287baa4d87af on ibm_marrakesh
submitted 2026-11-15 17:15:31; est 51 QPU-s

Op definition: op = one Binary, Unary or Index node of a store/if expression tree, counted on the TRANSPILED circuit; casts and literal nodes count 0; each use of a sub-expression counts again (tree, not DAG); depth = longest root-to-leaf chain of such nodes.
Metrics are counted on the TRANSPILED circuits (asserted equal to the builder at plan time).
Backend info at submission: {"name": "ibm_marrakesh", "processor_type": {"family": "Heron", "revision": "2"}, "backend_version": "1.0.21", "status": "active", "operational": true, "pending_jobs": 10, "properties_last_update": "2026-11-13 19:22:56+02:00", "read_at_utc": "2026-11-15T15:15:32Z"}

| probe | cone regs | peak live | temps | total reads | stores | max store ops | cone ops | unique nodes | result | code | informative patterns responding (CI lower > 0.6) | per-pattern correct [95% CI] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P1_R11x2_a | 11 | 12 | 2 | 11 | 1 | 10 | 10 | 10 | ACCEPTED |  | 3/3 | Z:0.93 [0.89,0.96]; A*:0.90 [0.84,0.93]; B:0.91 [0.86,0.94]; C*:0.74 [0.68,0.80]; D*:0.90 [0.85,0.93] |
| P1_R11x2_b | 11 | 12 | 2 | 11 | 1 | 10 | 10 | 10 | ACCEPTED |  | 3/3 | Z:0.91 [0.86,0.94]; A*:0.89 [0.84,0.93]; B:0.84 [0.78,0.88]; C*:0.83 [0.78,0.88]; D*:0.93 [0.89,0.96] |
| P1_R11x2_c | 11 | 12 | 2 | 11 | 1 | 10 | 10 | 10 | ACCEPTED |  | 3/3 | Z:0.93 [0.88,0.95]; A*:0.89 [0.84,0.93]; B:0.90 [0.85,0.93]; C*:0.73 [0.67,0.79]; D*:0.94 [0.89,0.96] |
| P1_R10x8_a | 10 | 11 | 2 | 10 | 1 | 9 | 9 | 9 | ACCEPTED |  | 3/3 | Z:0.68 [0.61,0.74]; A*:0.73 [0.67,0.79]; B:0.65 [0.58,0.71]; C*:0.72 [0.66,0.78]; D*:0.74 [0.68,0.80] |
| P1_R12x2_a | 12 | 13 | 2 | 12 | 1 | 11 | 11 | 11 | ERROR | 6057 |  |  |
| P1_R10x8_b | 10 | 11 | 2 | 10 | 1 | 9 | 9 | 9 | ACCEPTED |  | 3/3 | Z:0.72 [0.65,0.78]; A*:0.74 [0.68,0.80]; B:0.69 [0.62,0.75]; C*:0.77 [0.71,0.82]; D*:0.69 [0.62,0.75] |
| P1_R12x2_b | 12 | 13 | 2 | 12 | 1 | 11 | 11 | 11 | ERROR | 6057 |  |  |
| P1_overlapNF | 10 | 11 | 2 | 14 | 3 | 5 | 11 | 11 | ACCEPTED |  | 4/4 | Z:0.98 [0.95,0.99]; A*:0.93 [0.89,0.96]; B:0.96 [0.92,0.98]; C*:0.95 [0.92,0.98]; D*:0.96 [0.92,0.98]; E*:0.97 [0.94,0.99] |
| P1_split6 | 12 | 13 | 2 | 14 | 3 | 5 | 11 | 11 | ERROR | 6057 |  |  |
| P1_staged | 12 | 8 | 2 | 14 | 3 | 5 | 11 | 11 | ACCEPTED |  | 3/3 | Z:0.91 [0.86,0.94]; A*:0.89 [0.84,0.93]; B:0.94 [0.89,0.96]; C*:0.89 [0.84,0.93]; D*:0.92 [0.87,0.95] |
| P1_alloc4 | 3 | 7 | 3 | 16 | 5 | 6 | 27 | 28 | ACCEPTED |  | 2/2 | Z:0.81 [0.74,0.85]; A:0.85 [0.79,0.89]; B:0.95 [0.92,0.98]; C*:0.93 [0.88,0.95]; D*:0.83 [0.77,0.88]; E:0.81 [0.75,0.86] |
| P1_alloc8 | 3 | 11 | 3 | 32 | 9 | 7 | 55 | 56 | ERROR | 6057 |  |  |
| P1_alloc12 | 3 | 15 | 3 | 48 | 13 | 11 | 83 | 84 | ERROR | 6057 |  |  |
| P1_ir96 | 4 | 4 | 5 | 4 | 1 | 96 | 96 | 96 | ACCEPTED |  | 2/2 | A*:0.97 [0.94,0.99]; B*:0.98 [0.96,0.99] |
| P1_ir104 | 4 | 4 | 6 | 4 | 1 | 104 | 104 | 104 | ERROR | 6057 |  |  |
| P1_ir126 | 4 | 4 | 5 | 4 | 1 | 126 | 126 | 126 | ERROR | 6057 |  |  |
| P1_dag104 | 4 | 4 | 6 | 4 | 1 | 104 | 104 | 56 | ERROR | 6057 |  |  |
| P1_bal104 | 4 | 4 | 7 | 4 | 1 | 104 | 104 | 104 | ACCEPTED |  | 2/2 | A*:0.78 [0.71,0.83]; B*:0.99 [0.97,1.00] |
| P1_chain104 | 4 | 4 | 2 | 4 | 1 | 104 | 104 | 104 | ACCEPTED |  | 2/2 | A*:1.00 [0.98,1.00]; B*:0.99 [0.97,1.00] |
| P1_C8bal | 8 | 8 | 7 | 8 | 1 | 63 | 63 | 63 | ERROR | 6057 |  |  |
| P1_C8chain | 8 | 8 | 2 | 8 | 1 | 63 | 63 | 63 | ACCEPTED |  | 2/2 | A*:0.99 [0.97,1.00]; B*:0.99 [0.97,1.00] |
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
| probe | observed | cone | peak_live | alloc | total_reads | cone+store_ops | cone+cone_ops | cone+unique_nodes | cone+depth | historic |
|---|---|---|---|---|---|---|---|---|---|---|
| P1_R11x2_a | pass | ? | ? | ? | ? | ? | ? | ? | ? | - |
| P1_R11x2_b | pass | ? | ? | ? | ? | ? | ? | ? | ? | - |
| P1_R11x2_c | pass | ? | ? | ? | ? | ? | ? | ? | ? | - |
| P1_R10x8_a | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| P1_R12x2_a | FAIL | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | - |
| P1_R10x8_b | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| P1_R12x2_b | FAIL | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | - |
| P1_overlapNF | pass | P ok | P ok | P ok | F WRONG | P ok | P ok | P ok | P ok | - |
| P1_split6 | FAIL | F ok | F ok | F ok | F ok | F ok | F ok | F ok | F ok | - |
| P1_staged | pass | F WRONG | P ok | P ok | F WRONG | F WRONG | F WRONG | F WRONG | F WRONG | - |
| P1_alloc4 | pass | P ok | P ok | P ok | F WRONG | P ok | P ok | P ok | P ok | - |
| P1_alloc8 | FAIL | P WRONG | P WRONG | ? | F ok | P WRONG | P WRONG | P WRONG | P WRONG | - |
| P1_alloc12 | FAIL | P WRONG | F ok | F ok | F ok | P WRONG | P WRONG | P WRONG | P WRONG | - |
| P1_ir96 | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| P1_ir104 | FAIL | P WRONG | P WRONG | P WRONG | P WRONG | F ok | F ok | F ok | P WRONG | - |
| P1_ir126 | FAIL | P WRONG | P WRONG | P WRONG | P WRONG | F ok | F ok | F ok | P WRONG | - |
| P1_dag104 | FAIL | P WRONG | P WRONG | P WRONG | P WRONG | F ok | F ok | P WRONG | P WRONG | - |
| P1_bal104 | pass | P ok | P ok | P ok | P ok | F WRONG | F WRONG | F WRONG | P ok | - |
| P1_chain104 | pass | P ok | P ok | P ok | P ok | F WRONG | F WRONG | F WRONG | ? | - |
| P1_C8bal | FAIL | P WRONG | P WRONG | F ok | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | - |
| P1_C8chain | pass | P ok | P ok | P ok | P ok | P ok | P ok | P ok | P ok | - |
| P1_old_T1 | FAIL | P WRONG | P WRONG | P WRONG | P WRONG | F ok | F ok | P WRONG | P WRONG | F ok |
| P1_old_CONE | FAIL | P WRONG | P WRONG | ? | F ok | P WRONG | F ok | F ok | P WRONG | F ok |
| P1_old_LUT85K8 | FAIL | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | P WRONG | F ok |
| P1_old_W512 | FAIL | P WRONG | P WRONG | F ok | F ok | P WRONG | F ok | F ok | P WRONG | F ok |
| score ok / wrong | | 11 / 11 | 13 / 9 | 15 / 5 | 13 / 9 | 13 / 9 | 15 / 7 | 13 / 9 | 10 / 11 | 4 / 0 |

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
