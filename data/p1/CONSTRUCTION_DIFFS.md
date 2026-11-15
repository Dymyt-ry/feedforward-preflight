# Construction differences, counted on the transpiled expression trees (executed circuits)

Pairs: ir104 / dag104 (rejected) vs bal104 / chain104 (accepted) at 104 ops and 4 registers; C8bal (rejected) vs C8chain (accepted) at 63 ops and 8 registers; ir96 (accepted) and ir126 (rejected) for the random-shape family.

| feature | P1_ir96 | P1_ir104 | P1_ir126 | P1_dag104 | P1_bal104 | P1_chain104 | P1_C8bal | P1_C8chain |
|---|---|---|---|---|---|---|---|---|
| outcome | accepted | REJECTED 6057 | REJECTED 6057 | REJECTED 6057 | accepted | accepted | REJECTED 6057 | accepted |
| measured registers (cone) | 4 | 4 | 4 | 4 | 4 | 4 | 8 | 8 |
| register width (bits) | [32] | [32] | [32] | [32] | [32] | [32] | [8] | [8] |
| peak live registers | 4 | 4 | 4 | 4 | 4 | 4 | 8 | 8 |
| distinct leaf bits | 98 | 106 | 128 | 56 | 106 | 106 | 65 | 65 |
| leaf references | 98 | 106 | 128 | 106 | 106 | 106 | 65 | 65 |
| literals | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| casts | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Index ops | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| unary (NOT) ops | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| binary ops | 96 | 104 | 126 | 104 | 104 | 104 | 63 | 63 |
| op mix | {'BIT_OR': 38, 'BIT_AND': 31, 'BIT_XOR': 27} | {'BIT_OR': 31, 'BIT_XOR': 39, 'BIT_AND': 34} | {'BIT_XOR': 40, 'BIT_OR': 43, 'BIT_AND': 43} | {'BIT_XOR': 32, 'BIT_AND': 26, 'BIT_OR': 46} | {'BIT_AND': 42, 'BIT_OR': 33, 'BIT_XOR': 29} | {'BIT_OR': 29, 'BIT_AND': 43, 'BIT_XOR': 32} | {'BIT_AND': 24, 'BIT_XOR': 22, 'BIT_OR': 17} | {'BIT_XOR': 26, 'BIT_OR': 19, 'BIT_AND': 18} |
| tree depth | 10 | 13 | 14 | 13 | 7 | 104 | 6 | 63 |
| max nodes on one level | 46 | 34 | 38 | 36 | 96 | 2 | 64 | 2 |
| stores in cone | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| unique (hash-consed) nodes | 96 | 104 | 126 | 56 | 104 | 104 | 63 | 63 |
| Ershov / Sethi-Ullman need | 5 | 6 | 5 | 6 | 7 | 2 | 7 | 2 |
| left-to-right stack need | 8 | 10 | 11 | 10 | 7 | 2 | 7 | 2 |
| right-to-left stack need | 12 | 12 | 13 | 14 | 12 | 109 | 15 | 72 |
| pool = peak + LR need | 12 | 14 | 15 | 14 | 11 | 6 | 15 | 10 |
| 4*peak + 5*LR need | 56 | 66 | 71 | 66 | 51 | 26 | 67 | 42 |

Reading: ir104/ir126/dag104 vs bal104/chain104 share register count (4), width (32), leaf references (106), literals (0), casts (0), Index ops (0) and op classes; they differ in tree SHAPE: the random-shape trees need a naive left-to-right
evaluation stack of 10-11 (ir96 8, bal104 7, chain104 2) and in the level profile. C8bal vs C8chain differ only in shape too (stack 7 vs 2) at 8 registers. Which quantity the controller actually limits is what the validation round tests.
