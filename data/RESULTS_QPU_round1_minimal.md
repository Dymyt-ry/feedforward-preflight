> **Superseded notes:** this file was generated right after round 1. Its "R12x8 ... register count is limiting" line and the hypothesis tables (H_ops etc.) predate the round-2 to round-4 results; the README (Section 2 and "What we don't know") is authoritative. The latency table here uses the robust estimator; the older published-style numbers are in the README.

# QPU results, plan c4189b3af6be (minimal) on ibm_marrakesh
submitted 2026-11-15 16:07:58; est 44 QPU-s; K=16, shots/latency circuit 3000

## Jobs
| job | kind | status | error code | quantum s | note |
|---|---|---|---|---|---|
| XORC15 | LAT | DONE |  |  |  |
| XORT15 | LAT | DONE |  |  |  |
| SOUP63 | LAT | DONE |  |  |  |
| POP8 | LAT | DONE |  |  |  |
| WVOTE27 | LAT | DONE |  |  |  |
| A84t_K1 | ACC | DONE |  |  |  |
| A84t_K8 | ACC | DONE |  |  |  |
| A96t_K1 | ACC | DONE |  |  |  |
| A96c_K1 | ACC | DONE |  |  |  |
| C3x30 | ACC | DONE |  |  |  |
| C6x16 | ACC | DONE |  |  |  |
| R12x8 | ACC | ERROR | 6057 |  | rement results used in subsequent conditions. Contact support if required. -- https://ibm.biz/error_codes#6057 |

## Estimators side by side (ns over a 1-bit if; headline = Huber +/- leave-one-probe-out jackknife)
| probe | published-style (clipped, shot sigma) | weighted unclipped | median | Huber +/- jackknife | Z median | X median | probe chi2/dof | usable | clip kept |
|---|---|---|---|---|---|---|---|---|---|
| XORC15 | 737 +/- 45 | 656 | 772 | 743 +/- 59 | 723 | 772 | 8.8 | 23/24 | 21/23 |
| XORT15 | 743 +/- 40 | 651 | 733 | 741 +/- 36 | 685 | 783 | 8.8 | 24/24 | 23/24 |
| SOUP63 | 2077 +/- 75 | 1799 | 2128 | 2103 +/- 75 | 2175 | 2112 | 18.2 | 24/24 | 23/24 |
| POP8 | 845 +/- 53 | 607 | 871 | 875 +/- 47 | 849 | 897 | 26.6 | 24/24 | 22/24 |
| WVOTE27 | 835 +/- 65 | 397 | 999 | 1055 +/- 67 | 872 | 1258 | 58.2 | 24/24 | 9/24 |

## Latency increments over a 1-bit if (ns)
| probe | ops | depth | inputs | measured +/- boot | H_ops | H_inputs | H_depth |
|---|---|---|---|---|---|---|---|
| XORC15 | 15 | 15 | 16 | 743 +/- 59 | 659 | 2155 | 1781 |
| XORT15 | 15 | 4 | 16 | 741 +/- 36 | 659 | 2155 | 409 |
| SOUP63 | 63 | 6 | 16 | 2103 +/- 75 | 2155 | 2155 | 659 |
| POP8 | 24 | 10 | 8 | 875 +/- 47 | 908 | 1158 | 1033 |
| WVOTE27 | 28 | 10 | 7 | 1055 +/- 67 | 1033 | 1033 | 1033 |

Inter-job REF drift (RMS of max cross-offset over 10 pairs): 48 ns (used as job-level random effect)

## Hypothesis tests (3 hypotheses; pre-registered = old 3-point line, refit = free 2-parameter WLS)
| hypothesis | chi2 pre-registered (n dof) | refit intercept | refit slope /unit | chi2 refit (n-2 dof) | chi2 refit incl. job-level |
|---|---|---|---|---|---|
| H_ops | 8.2 (5) | 293 | 28.3 | 2.9 (3) | 1.4 |
| H_inputs | 2116.0 (5) | 942 | -0.4 | 283.3 (3) | 185.6 |
| H_depth | 772.5 (5) | 998 | -7.9 | 281.6 (3) | 179.5 |

## Pre-registered contrasts
- XORC15 - XORT15 (same 15 xor, depth 15 vs 4; H_ops predicts 0, H_depth +1372): +2 +/- 69 ns (+0.0 sigma stat; job-level 84 ns)
- SOUP63 vs extrapolation 2155 ns: -52 ns = -0.7 sigma (63 ops, depth 6, 16 inputs)
- WVOTE27 reproduction of old 1032 +/- 151: 1055 +/- 67 ns, z = +0.1
- WVOTE27 - POP8 (8 mul vs none; ops differ by 4): +180 +/- 82 ns (+2.2 sigma stat; job-level 95 ns)

## Acceptance probes (pass/fail; failures are data)
| probe | result | code | P(out=1) obs vs expected |
|---|---|---|---|
| A84t_K1 | ACCEPTED |  | 0.56 vs 0.48 (+1.3 sigma, readout-limited) |
| A84t_K8 | ACCEPTED |  | 0.58 vs 0.50 (+1.3 sigma, readout-limited) |
| A96t_K1 | ACCEPTED |  | 0.45 vs 0.52 (-1.1 sigma, readout-limited) |
| A96c_K1 | ACCEPTED |  | 0.23 vs 0.25 (-0.3 sigma, readout-limited) |
| C3x30 | ACCEPTED |  | 0.52 vs 0.52 (-0.1 sigma, readout-limited) |
| C6x16 | ACCEPTED |  | 0.38 vs 0.44 (-1.0 sigma, readout-limited) |
| R12x8 | ERROR | 6057 |  |

### Interpretation (rule-based; check against the table)
- 84 ops: K=1 and K=8 both ACCEPTED: 84 ops is under the limit and K=8 repetition is fine; the old LUT85 failure came from that expression's structure or the 83-103 limit sits above 84 for this shape (LUT85 had a different, wider-support structure).
- 96 ops tree vs chain: both ACCEPTED: depth is not the constraint; limit > 96 for this shape.
- cones (flattened 92 and 101 ops from several small stores): C3x30 OK, C6x16 OK: total cone size not limiting at ~100 ops with small stores.
- R12x8 (12 registers x 8 bits, small ops): FAILED, register count is limiting
