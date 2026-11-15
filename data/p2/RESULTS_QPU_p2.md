# QPU results p2 (latency methodology), plan cfa6f9d61ba5 on ibm_marrakesh
submitted 2026-11-15 17:31:56; est 13 QPU-s; K=16
Backend info at submission: {"name": "ibm_marrakesh", "processor_type": {"family": "Heron", "revision": "2"}, "backend_version": "1.0.21", "status": "active", "operational": true, "pending_jobs": 0, "properties_last_update": "2026-11-13 19:22:56+02:00", "read_at_utc": "2026-11-15T15:31:57Z"}

## Jobs
| job | status | code | message |
|---|---|---|---|
| P2_place | DONE |  |  |

## Estimates (ns; Huber +/- jackknife, median, Z-probe / X-probe medians, probe chi2/dof)
| job | case | calibration | Huber | median | Z median | X median | chi2/dof | usable |
|---|---|---|---|---|---|---|---|---|
| P2_place | NULL | after | 65 +/- 27 | 56 | 113 | 37 | 0.8 | 21/24 |
| P2_place | XORC15 | after | 672 +/- 55 | 670 | 675 | 670 | 2.5 | 22/24 |
| P2_place | REF0 | before | -41 +/- 59 | -21 | -163 | -12 | 11.3 | 21/24 |
| P2_place | XORC15 | before | 777 +/- 53 | 801 | 845 | 737 | 1.8 | 22/24 |

## Contrasts
- NULL (identical to REF0) increment: +65 +/- 27 ns -> NONZERO false increment: the noise floor/systematic is at least this large
- placement check, XORC15 increment over a 1-bit if: via REF-after 672 +/- 55; via REFB-before 819 +/- 80 (difference -147 +/- 97 ns)
  - Z-type probes: after 675, before-minus-REF0 1009
  - X-type probes: after 670, before-minus-REF0 749
- X-minus-Z median asymmetry with REF-after calibration: -5 ns (round 1: +49, +98, -63, +48, +386)
