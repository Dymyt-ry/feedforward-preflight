> **Superseded notes:** "Hypotheses still consistent with all data: H_cone" below was generated before round 4; the staged-liveness probe (round 4) falsified H_cone. See the README.

# QPU results split, plan 138f38fc85af on ibm_marrakesh
submitted 2026-11-15 16:28:50; est 10 QPU-s; 200 shots per pattern circuit

Probe = G registers (2 bits, prepared basis states, measure_2) -> several stores of XOR over registers -> final `S` -> `if S[0]` -> X on out qubit. Pattern A expects out=1, B expects out=0. fanin_max = distinct registers read by the widest store; cone = measured registers reachable from the final store; total_reads = sum of fan-ins. Stores per probe as TRANSPILED are in the manifest (`store_report`).

| probe | stores | fanin_max | cone | total_reads | result | code | A: P(out=1) [CI] | B: P(out=0) [CI] | verified |
|---|---|---|---|---|---|---|---|---|---|
| S_12split6 | 3 | 6 | 12 | 14 | ERROR | 6057 |  |  |  |
| S_24split3x8 | 4 | 8 | 24 | 27 | ERROR | 6057 |  |  |  |
| S_12chain | 2 | 7 | 12 | 13 | ERROR | 6057 |  |  |  |
| S_10overlap | 3 | 6 | 10 | 14 | ACCEPTED |  | 0.910 [0.862,0.942] | 0.910 [0.862,0.942] | YES |
| S_R12x2c | 1 | 12 | 12 | 12 | ERROR | 6057 |  |  |  |

## Threshold consistency per hypothesis (fail iff metric >= theta)
| hypothesis | consistent with all data | theta in (pass max, fail min] |
|---|---|---|
| H_store | NO | pass at 10 >= fail at 6 |
| H_cone | yes | (10, 12] |
| H_store_cone | NO | pass at 14 >= fail at 12 |

### Verdict
- S_12split6 FAILED: splitting does not bypass; limit follows the cone or total reads (see consistency table).
- S_10overlap (cone 10 registers, total reads 14): ACCEPTED. G4 and G5 cancel algebraically under XOR, so a folding compiler sees an 8-register cone; this probe therefore cannot separate cone from total reads unless the compiler is known not to fold (see the non-foldable overlap probe in the later tiers).
- Hypotheses still consistent with all data: H_cone
