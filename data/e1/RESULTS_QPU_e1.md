# QPU results e1 (envelope vs M_u validation), plan 7b05c7152abf on ibm_marrakesh
submitted 2026-11-15 17:48:38; est 10 QPU-s

Everything below uses the circuits, vectors and predictions STORED in the manifest (not the current builders). Backend info at submission: {"name": "ibm_marrakesh", "processor_type": {"family": "Heron", "revision": "2"}, "backend_version": "1.0.21", "status": "active", "operational": true, "pending_jobs": 1, "properties_last_update": "2026-11-13 19:22:56+02:00", "read_at_utc": "2026-11-15T15:48:39Z"}

| probe | envelope (frozen) | M_u | observed | code | informative patterns responding (all outputs correct, CI lower > 0.6) | per-pattern all-outputs-correct [95% CI] |
|---|---|---|---|---|---|---|
| E1_wvote7 | PASS | PASS | PASS |  | 6/6 | A*:0.99 [0.97,1.00]; B*:1.00 [0.98,1.00]; C*:0.99 [0.97,1.00]; D*:0.96 [0.92,0.98]; E*:0.96 [0.92,0.98]; F*:0.99 [0.96,1.00]; Z:0.93 [0.89,0.96] |
| E1_syndrome6 | PASS | PASS | PASS |  | 6/6 | A*:0.94 [0.89,0.96]; B*:0.92 [0.87,0.95]; C*:0.95 [0.92,0.98]; D*:0.94 [0.90,0.97]; E*:0.93 [0.88,0.95]; F*:0.94 [0.90,0.97]; Z:0.95 [0.91,0.97] |
| E1_tele14 | PASS | PASS | PASS |  | 5/5 | A*:0.87 [0.82,0.91]; B*:0.88 [0.83,0.92]; C:0.83 [0.78,0.88]; D*:0.88 [0.83,0.92]; E*:0.89 [0.84,0.93]; F*:0.89 [0.83,0.92]; Z:0.85 [0.79,0.89] |
| E1_twoif | PASS | PASS | PASS |  | 6/6 | A*:0.94 [0.89,0.96]; B*:0.96 [0.92,0.98]; C*:0.96 [0.92,0.98]; D*:0.95 [0.92,0.98]; E*:0.94 [0.90,0.97]; F*:0.94 [0.90,0.97]; Z:0.94 [0.90,0.97] |
| E1_decode9 | FAIL | FAIL | FAIL | 6057 |  |  |

## Validation scoreboard
- Envelope PASS/FAIL predictions correct / wrong: 5 / 0 (UNCERTAIN predictions make no claim)
- M_u correct / wrong: 5 / 0
- Disagreements (envelope PASS or FAIL vs M_u, or envelope UNCERTAIN): none

## Full error messages (verbatim)
- E1_decode9: `Error code 6057; Failed to execute program: The job requires more classical registers than the control hardware provides. If possible, consider breaking the job up into multiple separate jobs or reducing the number of classical variables and measurement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057`

Verdict: every frozen envelope PASS/FAIL prediction held out-of-sample. M_u predicted every outcome.
