# QPU results e2 (envelope vs M_u validation), plan 33f8b03c64d9 on ibm_marrakesh
submitted 2026-11-15 17:57:25; est 6 QPU-s

Everything below uses the circuits, vectors and predictions STORED in the manifest (not the current builders). Backend info at submission: {"name": "ibm_marrakesh", "processor_type": {"family": "Heron", "revision": "2"}, "backend_version": "1.0.21", "status": "active", "operational": true, "pending_jobs": 2, "properties_last_update": "2026-11-13 19:22:56+02:00", "read_at_utc": "2026-11-15T15:57:26Z"}

| probe | envelope (frozen) | M_u | observed | code | informative patterns responding (all outputs correct, CI lower > 0.6) | per-pattern all-outputs-correct [95% CI] |
|---|---|---|---|---|---|---|
| E2_shan112 | FAIL | PASS | FAIL | 6057 |  |  |
| E2_rc5_P9 | UNCERTAIN | FAIL | PASS |  | 2/2 | A*:1.00 [0.98,1.00]; B*:0.99 [0.97,1.00] |
| E2_rc8_P5 | UNCERTAIN | PASS | PASS |  | 2/2 | A*:1.00 [0.98,1.00]; B*:1.00 [0.98,1.00] |

## Validation scoreboard
- Envelope PASS/FAIL predictions correct / wrong: 1 / 0 (UNCERTAIN predictions make no claim)
- M_u correct / wrong: 1 / 2
- Disagreements (envelope PASS or FAIL vs M_u, or envelope UNCERTAIN): E2_shan112 (env FAIL, M_u PASS, observed FAIL), E2_rc5_P9 (env UNCERTAIN, M_u FAIL, observed PASS), E2_rc8_P5 (env UNCERTAIN, M_u PASS, observed PASS)

## Full error messages (verbatim)
- E2_shan112: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`

Verdict: every frozen envelope PASS/FAIL prediction held out-of-sample. M_u wrong for E2_shan112, E2_rc5_P9.
