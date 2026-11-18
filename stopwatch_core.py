# SPDX-License-Identifier: Apache-2.0
"""Stopwatch core (vendored subset of the qubit-stopwatch analysis; no IBM access needed).

Method: idle "probe" qubits decohere while the branch decision is pending; each probe's score-vs-dead-time curve is
calibrated inside the same job with reference circuits that add a known delay per repetition, then inverted to turn a case's
score into extra dead time per decision. Probes are inverse-variance combined; sigma is a bootstrap over shots.
"""
from __future__ import annotations

import numpy as np
from qiskit.circuit.classical import expr

REF_D = (0, 250, 500, 1000)   # ns per rep
NOIF_D = 1000
REP_OVERHEAD_S = 260e-6        # per-shot overhead (rep delay + reset), measured on short circuits
CASES = ("NOIF",)
N_SRC_COMPACT = 7
# Frozen legacy decoder weights (Uint8 weighted votes used as latency fixtures: weights, threshold, store ops)
WVOTE = ([42, 33, 24, 29, 32, 38, 43], 72, 27)
SPARSE_PROFILES = {"SPARSE2": ([133, 107, 0, 0, 0, 0, 0], 73, 7), "SPARSE5": ([63, 51, 0, 0, 39, 34, 53], 73, 19)}


def weighted_params(case):
    if case == "WVOTE":
        return WVOTE
    if case in SPARSE_PROFILES:
        return SPARSE_PROFILES[case]
    raise ValueError(f"not a weighted decoder case: {case}")


def n_ops(e):
    if isinstance(e, expr.Binary):
        return 1 + n_ops(e.left) + n_ops(e.right)
    if isinstance(e, expr.Unary):
        return 1 + n_ops(e.operand)
    if isinstance(e, expr.Cast):
        return n_ops(e.operand)
    return 0


def pick_layout(be, n_z, n_x, n_src=N_SRC_COMPACT):
    """Probes: good readout, T1 > 50 us, T2 > 30 us, pairwise non-adjacent (no ZZ between flipping probes),
    alternating Z/X types; sources: 48 working qubits not adjacent to any probe (relaxed if needed); flag: any."""
    props, cm = be.properties(), be.coupling_map
    nq = be.num_qubits
    bad = set()
    for i in range(nq):
        try:
            if not props.is_qubit_operational(i):
                bad.add(i)
        except Exception:  # noqa: BLE001
            pass
    def calibration(getter, i, missing):
        try:
            value = getter(i)
            return missing if value is None or not np.isfinite(value) else float(value)
        except Exception:  # noqa: BLE001  (live snapshots can omit one qubit/property)
            return missing

    # A partially missing live calibration must never abort layout selection or
    # accidentally rank the unknown qubit as good. Unknown coherence -> 0;
    # unknown readout error -> 100%, which excludes it below.
    ro = {i: calibration(props.readout_error, i, 1.0) for i in range(nq)}
    t1 = {i: calibration(props.t1, i, 0.0) for i in range(nq)}
    t2 = {i: calibration(props.t2, i, 0.0) for i in range(nq)}
    nb = {i: set(cm.neighbors(i)) | {j for j in range(nq) if i in cm.neighbors(j)} for i in range(nq)}
    cand = sorted((i for i in range(nq) if i not in bad and t1[i] > 50e-6 and t2[i] > 30e-6 and ro[i] < 0.03),
                  key=lambda i: ro[i])
    probes, blocked = [], set()
    for i in cand:
        if len(probes) == n_z + n_x:
            break
        if i not in blocked:
            probes.append(i)
            blocked |= nb[i] | {i}
    if len(probes) < n_z + n_x:
        raise RuntimeError(f"only {len(probes)} probe qubits found")
    near = set().union(*(nb[p] for p in probes))
    rest = sorted((i for i in range(nq) if i not in bad and i not in probes), key=lambda i: (i in near, ro[i]))
    if len(rest) <= n_src:
        raise RuntimeError(f"only {len(rest)} usable non-probe qubits for {n_src} sources plus a flag")
    src = rest[:n_src]
    flag = rest[n_src]
    zp, xp = probes[0::2][:n_z], probes[1::2][:n_x]
    if len(zp) < n_z or len(xp) < n_x:
        zp, xp = probes[:n_z], probes[n_z:]
    info = {"n_src_adjacent_to_probe": sum(i in near for i in src),
            "probe_T1_us": [round(t1[i] * 1e6, 1) for i in zp + xp],
            "probe_T2_us": [round(t2[i] * 1e6, 1) for i in zp + xp],
            "probe_readout": [round(ro[i], 4) for i in zp + xp]}
    return src + zp + xp + [flag], info


def target_with_measure2(be, dur_s):
    """Fake backends lack IBM's measure_2 (MidCircuitMeasure); add it so transpile + scheduling work locally."""
    from qiskit.transpiler import InstructionProperties
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    tgt = be.target
    if "measure_2" not in tgt.operation_names:
        tgt.add_instruction(MidCircuitMeasure(), {(i,): InstructionProperties(duration=dur_s, error=0.01)
                                                  for i in range(be.num_qubits)})
    return tgt


def scores(pr, n_z):
    """Per-probe success: Z probes should read 1, X probes 0."""
    want = np.zeros(pr.shape[1], bool)
    want[:n_z] = True
    return (pr == want).mean(0)


def _invert(coef, s, lo, hi):
    """Root of polyval(coef, D) = s closest to the linear estimate, clipped to [lo, hi]."""
    a2, a1, a0 = coef
    lin = (s - a0) / a1
    if abs(a2) < 1e-30:
        return lin
    disc = a1 * a1 - 4 * a2 * (a0 - s)
    if disc < 0:
        return np.clip(lin, lo, hi)
    r = np.array([(-a1 + np.sqrt(disc)) / (2 * a2), (-a1 - np.sqrt(disc)) / (2 * a2)])
    return np.clip(r[np.argmin(np.abs(r - lin))], lo, hi)


def estimate(S, K, ref_D, cases):
    """S: dict name -> per-probe scores. Returns per-case per-probe D (ns, total over K reps), slope info."""
    Dref = np.array(ref_D, float) * K
    Y = np.array([S[f"REF{d}"] for d in ref_D])          # (n_ref, n_p)
    n_p = Y.shape[1]
    coefs = [np.polyfit(Dref, Y[:, j], 2) for j in range(n_p)]
    lo, hi = -1.0 * Dref[-1], 1.5 * Dref[-1]
    est = {c: np.array([_invert(coefs[j], S[c][j], lo, hi) for j in range(n_p)]) for c in cases}
    return est, coefs


def analyse_data(prs, n_z, K, ref_D=REF_D, cases=CASES, B=300, seed=0, verbose=True, truth=None, noif_d=NOIF_D):
    """prs: dict name -> (shots, n_p) bool probe bits. Returns dict case -> (ns, sigma ns): for NOIF the
    feed-forward latency of a 1-bit if (TRIV - NOIF), for the others the extra time per decision over TRIV."""
    names = [f"REF{d}" for d in ref_D] + list(cases)
    S = {n: scores(prs[n], n_z) for n in names}
    est, coefs = estimate(S, K, ref_D, cases)
    n_p = len(S[names[0]])
    Dref = np.array(ref_D, float) * K
    # usable probes: reference contrast > 3 sigma (binomial)
    N0, N1 = len(prs[names[0]]), len(prs[f"REF{ref_D[-1]}"])
    s0, s1 = S[names[0]], S[f"REF{ref_D[-1]}"]
    sig = np.sqrt(s0 * (1 - s0) / N0 + s1 * (1 - s1) / N1) + 1e-9
    good = (s0 - s1) / sig > 3
    if not np.any(good):
        raise RuntimeError(
            "no usable stopwatch probes: the REF0-to-REFmax contrast is below 3 sigma; "
            "increase --shots/--reps or improve probe selection"
        )
    # per-probe delta-method sigma -> fixed weights
    W = {}
    for c in cases:
        slope = np.array([np.polyval(np.polyder(coefs[j]), est[c][j]) for j in range(n_p)])
        sc = S[c]
        sd = np.sqrt(np.maximum(sc * (1 - sc), 1e-4) / len(prs[c])) / np.maximum(np.abs(slope), 1e-12)
        W[c] = np.where(good & (slope < 0), 1 / sd ** 2, 0.0)
    rng = np.random.default_rng(seed)

    def combine(est_c, w):
        sw = np.sum(w)
        if not np.isfinite(sw) or sw <= 0:
            raise RuntimeError("no finite positive stopwatch weights for this decoder case")
        m = np.sum(w * est_c) / sw
        # 4-sigma clip of probe outliers (e.g. TLS jump during the job), one pass
        r = (est_c - m) * np.sqrt(w)
        w2 = np.where(np.abs(r) < 4, w, 0)
        sw2 = np.sum(w2)
        # A small bootstrap resample can make every probe look like an
        # outlier. Keep the conservative pre-clip estimate instead of NaN.
        return m if not np.isfinite(sw2) or sw2 <= 0 else np.sum(w2 * est_c) / sw2

    conv = lambda c, x: noif_d - x if c == "NOIF" else x  # noqa: E731  (NOIF carries noif_d of delay per rep)
    point = {c: conv(c, combine(est[c], W[c]) / K) for c in cases}
    boot = {c: [] for c in cases}
    for _ in range(B):
        Sb = {n: scores(prs[n][rng.integers(0, len(prs[n]), len(prs[n]))], n_z) for n in names}
        eb, _ = estimate(Sb, K, ref_D, cases)
        for c in cases:
            boot[c].append(conv(c, combine(eb[c], W[c]) / K))
    res = {c: (point[c], float(np.std(boot[c]))) for c in cases}
    if verbose:
        rate = np.array([-np.polyval(np.polyder(coefs[j]), 0) * 1e3 for j in range(n_p)])  # score / us
        print(f"  probes usable {good.sum()}/{n_p}; clock rate d(score)/dt at D=0 [%/us]: "
              f"Z {np.round(100 * rate[:n_z], 3).tolist()}  X {np.round(100 * rate[n_z:], 3).tolist()}")
        print(f"  reference scores (mean over probes): " + "  ".join(
            f"d={d}:{S[f'REF{d}'].mean():.4f}" for d in ref_D) + f"   (D = {K} x d)")
        for c in cases:
            v, s = res[c]
            extra = "" if truth is None else f"   [injected {truth[c]:+.1f}]"
            label = "feed-forward latency (TRIV - NOIF)" if c == "NOIF" else "extra over TRIV"
            print(f"  {c:7s} {label:36s} {v:+8.1f} ns +- {s:5.1f} (1 sigma, bootstrap){extra}")
        if "NOIF" in cases:
            ff, sff = res["NOIF"]
            for c in cases:
                if c != "NOIF":
                    v, s = res[c]
                    print(f"  ==> {c}: decision latency relative to the no-if reference = {ff + v:7.1f} ns +- {np.hypot(sff, s):.1f}"
                          f"  (of which expression: {v:+.1f} ns)")
    return res


def _scores(job: dict) -> dict[str, np.ndarray]:
    n_z = int(job["meta"]["n_z"])
    return {
        name: scores(job["raw"][f"pr_{name}"], n_z)
        for name in (f"REF{d}" for d in job["meta"]["ref_D"])
    }


def reference_drift_ns(a: dict, b: dict) -> dict:
    """Estimate a conservative inter-job zero drift from their reference curves.

    Each job's REF0 score is inverted through the other job's quadratic clock
    calibration.  Only probes with decreasing, >3-sigma reference contrast in
    both jobs are used.  The largest absolute median cross-offset is retained as
    a one-sigma systematic; this deliberately over-covers fit asymmetry.
    """
    sa, sb = _scores(a), _scores(b)
    ref_d = tuple(a["meta"]["ref_D"])
    k = int(a["meta"]["K"])
    dref = np.asarray(ref_d, float) * k
    ya = np.asarray([sa[f"REF{d}"] for d in ref_d])
    yb = np.asarray([sb[f"REF{d}"] for d in ref_d])
    shots = int(a["meta"]["shots"])
    sigma_a = np.sqrt(ya[0] * (1 - ya[0]) / shots + ya[-1] * (1 - ya[-1]) / shots) + 1e-12
    sigma_b = np.sqrt(yb[0] * (1 - yb[0]) / shots + yb[-1] * (1 - yb[-1]) / shots) + 1e-12
    good = ((ya[0] - ya[-1]) / sigma_a > 3) & ((yb[0] - yb[-1]) / sigma_b > 3)
    coef_a = [np.polyfit(dref, ya[:, j], 2) for j in range(ya.shape[1])]
    coef_b = [np.polyfit(dref, yb[:, j], 2) for j in range(yb.shape[1])]
    good &= np.asarray([np.polyval(np.polyder(c), 0) < 0 for c in coef_a])
    good &= np.asarray([np.polyval(np.polyder(c), 0) < 0 for c in coef_b])
    cross = []
    for fit_y, own_y, other_y in ((ya, ya, yb), (yb, yb, ya)):
        values = []
        for j in np.flatnonzero(good):
            coef = np.polyfit(dref, fit_y[:, j], 2)
            own = _invert(coef, own_y[0, j], -dref[-1], 1.5 * dref[-1]) / k
            other = _invert(coef, other_y[0, j], -dref[-1], 1.5 * dref[-1]) / k
            values.append(other - own)
        if not values:
            raise ValueError("no common monotonic probes for inter-job drift estimate")
        cross.append(float(np.median(values)))
    return {
        "common_monotonic_probes": int(good.sum()),
        "cross_offsets_ns": cross,
        "systematic_sigma_ns": float(max(abs(v) for v in cross)),
        "mean_reference_score_delta": {
            str(d): float(sb[f"REF{d}"].mean() - sa[f"REF{d}"].mean()) for d in ref_d
        },
    }


