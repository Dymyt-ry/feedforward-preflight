# QPU results: packed circuits (dccheck --fix-pack), plan da19f073ba27 on ibm_marrakesh
submitted 2026-11-15 18:12:31; est 6 QPU-s; 200 shots per pattern circuit

Probe = many small measured registers packed into few wide ones, prepared basis-state inputs, XOR store, `if S[0]`, X on out. Patterns: last = only the last register's bit is 1, mid = only a middle one, all = every register's bit is 1; expected out in the table. Wilson 95% intervals; chance is 0.5.

| probe | packed registers | pattern | expected out | P(correct) [CI] | status |
|---|---|---|---|---|---|
| FP2_R12x8 | pk0:32b, pk1:32b, pk2:32b | last | 1 | 0.720 [0.654, 0.778] | above chance |
| FP2_R12x8 | pk0:32b, pk1:32b, pk2:32b | mid | 1 | 0.670 [0.602, 0.731] | above chance |
| FP2_R12x8 | pk0:32b, pk1:32b, pk2:32b | all | 0 | 0.755 [0.691, 0.809] | above chance |
| FP2_S24 | pk0:32b, pk1:16b | last | 1 | 0.730 [0.665, 0.787] | above chance |
| FP2_S24 | pk0:32b, pk1:16b | mid | 1 | 0.790 [0.728, 0.841] | above chance |
| FP2_S24 | pk0:32b, pk1:16b | all | 0 | 0.715 [0.649, 0.773] | above chance |
| FP2_R12x2 | pk0:24b | last | 1 | 0.915 [0.868, 0.946] | above chance |
| FP2_R12x2 | pk0:24b | mid | 1 | 0.885 [0.833, 0.922] | above chance |
| FP2_R12x2 | pk0:24b | all | 0 | 0.810 [0.750, 0.858] | above chance |

## Verdict

- FP2_R12x8: ACCEPTED, computes correctly (all three patterns above chance)
- FP2_S24: ACCEPTED, computes correctly (all three patterns above chance)
- FP2_R12x2: ACCEPTED, computes correctly (all three patterns above chance)

A pattern above chance for last, mid and all means each packed lane influences the output as the original registers did (an ignored or mis-sliced lane flips at least one pattern). Readout error limits P(correct) well below 1 (see round 2 of the suite: 0.75 to 0.99).
