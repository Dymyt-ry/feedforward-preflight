# SPDX-License-Identifier: Apache-2.0
"""Probe-level robust latency estimators for the qubit-stopwatch data (no IBM access).

Every probe gives its own estimate of extra dead time per decision (ns) by inverting that probe's reference curve.
The legacy analysis combined probes with inverse-variance weights from shot noise only, after a one-pass 4-sigma clip.
Probe-to-probe scatter is several times larger than shot noise (probe chi2/dof 9-58), so that estimator is clip-dependent.
Here each estimator is reported side by side, errors come from a leave-one-probe-out jackknife, and the headline is
the Huber location estimate (equal probe weights, MAD scale) with its jackknife standard error.
"""
from __future__ import annotations

import numpy as np

import stopwatch_core as SW


def probe_estimates(prs, n_z, K, ref_D, case, noif_d=SW.NOIF_D, ref_prefix="REF"):
    """ref_prefix 'REF' = delay inserted AFTER the conditional X (legacy calibration); 'REFB' = delay BEFORE an unconditional X."""
    names = [f"{ref_prefix}{d}" for d in ref_D] + [case]
    S = {n: SW.scores(prs[n], n_z) for n in names}
    Dref = np.array(ref_D, float) * K
    Y = np.array([S[f"{ref_prefix}{d}"] for d in ref_D])
    coefs = [np.polyfit(Dref, Y[:, j], 2) for j in range(Y.shape[1])]
    lo, hi = -1.0 * Dref[-1], 1.5 * Dref[-1]
    est = {case: np.array([SW._invert(coefs[j], S[case][j], lo, hi) for j in range(Y.shape[1])])}
    n_p = len(S[names[0]])
    N0, N1 = len(prs[names[0]]), len(prs[f"{ref_prefix}{ref_D[-1]}"])
    s0, s1 = S[names[0]], S[f"{ref_prefix}{ref_D[-1]}"]
    sig = np.sqrt(s0 * (1 - s0) / N0 + s1 * (1 - s1) / N1) + 1e-9
    good = (s0 - s1) / sig > 3
    slope = np.array([np.polyval(np.polyder(coefs[j]), est[case][j]) for j in range(n_p)])
    sc = S[case]
    sd = np.sqrt(np.maximum(sc * (1 - sc), 1e-4) / len(prs[case])) / np.maximum(np.abs(slope), 1e-12)
    good &= slope < 0
    v = est[case] / K
    if case == "NOIF":
        v = noif_d - v
    return dict(v=v, sd=sd / K, good=good, is_z=np.arange(n_p) < n_z, scores=S, coefs=coefs)


def huber(x, c=1.345, iters=50):
    x = np.asarray(x, float)
    mu = np.median(x)
    s = 1.4826 * np.median(np.abs(x - mu))
    if s < 1e-9:
        return float(mu)
    for _ in range(iters):
        r = (x - mu) / s
        w = np.where(np.abs(r) <= c, 1.0, c / np.maximum(np.abs(r), 1e-12))
        new = float(np.sum(w * x) / np.sum(w))
        if abs(new - mu) < 1e-6:
            mu = new
            break
        mu = new
    return float(mu)


def jackknife(x, f):
    x = np.asarray(x, float)
    n = len(x)
    if n < 3:
        return float("nan")
    loo = np.array([f(np.delete(x, i)) for i in range(n)])
    return float(np.sqrt((n - 1) / n * np.sum((loo - loo.mean()) ** 2)))


def wmean(v, sd):
    w = 1 / sd ** 2
    return float(np.sum(w * v) / np.sum(w))


def analyse_robust(prs, n_z, K, ref_D, case, noif_d=SW.NOIF_D, ref_prefix="REF"):
    """Returns all estimators + errors for one case in one job."""
    pe = probe_estimates(prs, n_z, K, ref_D, case, noif_d, ref_prefix)
    g = pe["good"]
    v, sd, isz = pe["v"][g], pe["sd"][g], pe["is_z"][g]
    n = int(g.sum())
    out = dict(case=case, n_probes_usable=n, n_probes_total=len(pe["v"]))
    if n < 3:
        out["error"] = "fewer than 3 usable probes"
        return out
    w = 1 / sd ** 2
    mu_w = wmean(v, sd)
    chi2 = float(np.sum(w * (v - mu_w) ** 2) / (n - 1))
    se_w = float(1 / np.sqrt(np.sum(w)))
    out.update(
        chi2_per_dof=chi2, se_shot_weighted=se_w, se_scatter_inflated=se_w * float(np.sqrt(max(chi2, 1.0))),
        wmean_unclipped=mu_w, median=float(np.median(v)), huber=huber(v),
        jackknife_se_median=jackknife(v, np.median), jackknife_se_huber=jackknife(v, huber),
        probe_sd=float(np.std(v, ddof=1)),
    )
    # legacy estimator: weighted mean after one-pass 4-sigma clip (reproduced from stopwatch_core.analyse_data.combine)
    r = (v - mu_w) * np.sqrt(w)
    keep = np.abs(r) < 4
    out["wmean_clipped_legacy"] = float(np.sum(w[keep] * v[keep]) / np.sum(w[keep])) if keep.any() else mu_w
    out["clip_kept"] = f"{int(keep.sum())}/{n}"
    for lab, m in (("Z", isz), ("X", ~isz)):
        if m.sum() >= 2:
            out[f"median_{lab}"] = float(np.median(v[m]))
            out[f"n_{lab}"] = int(m.sum())
    out["headline"] = out["huber"]
    out["headline_se"] = out["jackknife_se_huber"]
    out["per_probe_ns"] = [round(float(a), 1) for a in v]
    out["per_probe_is_z"] = [bool(a) for a in isz]
    return out
