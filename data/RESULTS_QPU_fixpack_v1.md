# QPU results: packed circuits, fix-pack v1 (shift + narrowing-cast reads), plan 03b3cd042826 on ibm_marrakesh
submitted 2026-11-15 17:24; 200 shots per pattern circuit; circuits in data/fixpack_candidates_v1/ (QPY)

| probe | packed registers | result |
|---|---|---|
| FP_R12x8 | pk0, pk1, pk2 (32 bits each) | **ERROR 6066** ("Internal Compiler Error") |
| FP_S24 | pk0 (32 bits), pk1 (16 bits) | **ERROR 6066** ("Internal Compiler Error") |
| FP_B14x3 | pk0 (30 bits), pk1 (12 bits), clbit-level reads only | ACCEPTED; last 0.795 [0.734, 0.845], mid 0.840 [0.783, 0.884], all 0.805 [0.745, 0.854]: every pattern above chance |

Instruction diff: only the two rejected circuits contain SHIFT_LEFT / SHIFT_RIGHT on Uint(32) and Uint(16) registers and narrowing Cast Uint(32)->Uint(8) / Uint(2); the accepted one has only Bool XORs of single clbits stored into a clbit.
Fix-pack v2 (data/RESULTS_QPU_fixpack_v2.md) removed those constructs and was accepted 3/3.
