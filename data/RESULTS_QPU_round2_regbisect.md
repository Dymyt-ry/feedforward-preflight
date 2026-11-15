> **Superseded notes:** the "read_regs theta in (10, 12]" threshold fit below was made before round 3 and 4; the later rounds showed that distinct measured registers in the cone is not the trigger (staged probe) and no closed-form model survived out-of-sample. See the README.

# QPU results regbisect, plan ef2d74ac0e55 on ibm_marrakesh
submitted 2026-11-15 16:14:44; est 18 QPU-s; 200 shots per pattern circuit (2 circuits per job)

## What 'register' means in these probes
A classical register = one `ClassicalRegister` in the circuit. Each probe declares R registers G0..G(R-1) of w bits (every bit is the target of a mid-circuit `measure_2` on a prepared qubit), one store target S (w bits), the unused 1-bit L (as in R12x8) and the 1-bit `out`. ONE `store` computes S = G0 ^ ... ^ G(nread-1) (nread-1 Uint ops) and ONE `if S[0]` drives X on the output qubit. There are no Vars. Classical bits are not added for readback: the check uses the existing `out` bit, with the inputs PREPARED (X then measure) so the expected value is known: pattern A expects out=1, pattern B expects out=0 (dead or wrong computation would give 0 on A / 1 on B).

## Jobs and readback
| probe | read regs | read bits | declared regs | total bits | result | code | A: P(out=1) [95% CI] | B: P(out=0) [95% CI] | verified |
|---|---|---|---|---|---|---|---|---|---|
| B_R4x8 | 4 | 32 | 7 | 42 | ACCEPTED |  | 0.950 [0.910,0.973] | 0.915 [0.868,0.946] | YES |
| B_R6x8 | 6 | 48 | 9 | 58 | ACCEPTED |  | 0.905 [0.856,0.938] | 0.930 [0.886,0.958] | YES |
| B_R8x8 | 8 | 64 | 11 | 74 | ACCEPTED |  | 0.890 [0.839,0.926] | 0.870 [0.816,0.910] | YES |
| B_R10x8 | 10 | 80 | 13 | 90 | ACCEPTED |  | 0.750 [0.686,0.805] | 0.780 [0.718,0.832] | NO (accepted but not verified) |
| B_R12x8c | 12 | 96 | 15 | 106 | ERROR | 6057 |  |  |  |
| B_R6x16 | 6 | 96 | 9 | 114 | ACCEPTED |  | 0.775 [0.712,0.827] | 0.780 [0.718,0.832] | YES |
| B_R3x32 | 3 | 96 | 6 | 130 | ACCEPTED |  | 0.925 [0.880,0.954] | 0.805 [0.745,0.854] | YES |
| B_R12x2 | 12 | 24 | 15 | 28 | ERROR | 6057 |  |  |  |
| B_R12x8u | 2 | 16 | 15 | 106 | ACCEPTED |  | 0.990 [0.964,0.997] | 0.960 [0.923,0.980] | YES |

## Threshold consistency (monotone: fail iff metric >= theta). Prior points: F1_regs150, A96t_n16, C6x16 accepted; round-1 R12x8 failed 6057
| metric | consistent | theta lies in (pass max, fail min] |
|---|---|---|
| read_regs | yes | (10, 12] |
| read_bits | NO (pass at 96 >= fail at 24) | |
| declared_regs | NO (pass at 153 >= fail at 15) | |
| total_bits | NO (pass at 152 >= fail at 28) | |
| measured_regs | NO (pass at 12 >= fail at 12) | |

### Interpretation
- Metrics still consistent with all data: read_regs: theta in (10, 12]
- Compare with the pre-registered table in the manifest (`predictions`). Decisive probes: B_R12x2 (count without bits), B_R6x16/B_R3x32 (bits without count), B_R12x8u (declared but unread), B_R12x8c (control must fail again; if it passes, the round-1 failure was not about this structure and the plan is void).
