# SPDX-License-Identifier: Apache-2.0
"""Re-analysis of the legacy stopwatch stopwatch ladder (read-only: saved raw arrays, no IBM access).

Answers: (1) per-job and per-probe scatter, (2) job-level REF drift across ALL job pairs,
(3) fixed + per-op model with honest uncertainty, (4) what the 3 points can / cannot separate.

usage: python reanalyse_stopwatch.py [--json]
"""
from __future__ import annotations

import itertools
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stopwatch_core as SW  # noqa: E402
import suite as SU           # noqa: E402

DATA = os.path.join(HERE, "data", "legacy_stopwatch")
JOBS = {c: c for c in ("NOIF", "SPARSE2", "SPARSE5", "WVOTE")}   # case -> file stem


class R:      # tiny shim for the saved legacy 4-job ladder (raw probe bits, one job per case)
    @staticmethod
    def _load(stem):
        meta = json.load(open(os.path.join(DATA, f"{stem}_meta.json")))
        result = json.load(open(os.path.join(DATA, f"{stem}_result.json")))
        with np.load(os.path.join(DATA, f"{stem}.npz")) as z:
            raw = {k: z[k] for k in z.files}
        return {"id": stem, "meta": meta, "result": result, "raw": raw}

    reference_drift_ns = staticmethod(SW.reference_drift_ns)
# structural descriptors of each expression (computed from the actual circuits below, asserted)
STRUCT = {"SPARSE2": dict(ops=7, inputs=2), "SPARSE5": dict(ops=19, inputs=5), "WVOTE": dict(ops=27, inputs=7)}


def structure():
    """ops / active inputs / dependency depth of the three stored expressions, from the real code."""
    from qiskit import ClassicalRegister
    from qiskit.circuit.classical import expr
    out = {}
    for case in ("SPARSE2", "SPARSE5", "WVOTE"):
        w, T, _ = SW.weighted_params(case)
        S = ClassicalRegister(8, "dat")
        e = SU.wsum_expr(S, w)

        def depth(x):
            if isinstance(x, expr.Binary):
                return 1 + max(depth(x.left), depth(x.right))
            if isinstance(x, expr.Unary):
                return 1 + depth(x.operand)
            if isinstance(x, expr.Cast):
                return depth(x.operand)
            return 0
        out[case] = dict(ops=SW.n_ops(e), inputs=int(sum(1 for x in w if x)), depth=depth(e),
                         ops_in_if_cond=1, weights=w, T=T)
    return out


def per_probe(job_id, case):
    j = R._load(job_id)
    m = j["meta"]
    n_z, K, ref_D = int(m["n_z"]), int(m["K"]), tuple(m["ref_D"])
    cases = (case,)
    prs = {k[3:]: v for k, v in j["raw"].items() if k.startswith("pr_")}
    names = [f"REF{d}" for d in ref_D] + list(cases)
    S = {n: SW.scores(prs[n], n_z) for n in names}
    est, coefs = SW.estimate(S, K, ref_D, cases)
    n_p = len(S[names[0]])
    N = len(prs[names[0]])
    s0, s1 = S[names[0]], S[f"REF{ref_D[-1]}"]
    sig = np.sqrt(s0 * (1 - s0) / N + s1 * (1 - s1) / N) + 1e-9
    good = (s0 - s1) / sig > 3
    slope = np.array([np.polyval(np.polyder(coefs[k]), est[case][k]) for k in range(n_p)])
    sc = S[case]
    sd = np.sqrt(np.maximum(sc * (1 - sc), 1e-4) / N) / np.maximum(np.abs(slope), 1e-12)
    use = good & (slope < 0)
    v = est[case] / K
    if case == "NOIF":
        v = SW.NOIF_D - v
    return dict(v=v, sd=sd / K, use=use, ref=np.array([S[f"REF{d}"] for d in ref_D]), K=K)


def main(as_json=False):
    res = {}
    st = structure()
    res["structure"] = st
    # ---- per-job headline (identical to report.py) and probe-level scatter
    heads = {}
    for case, jid in JOBS.items():
        j = R._load(jid)
        val = j["result"][case]
        pp = per_probe(jid, case)
        u = pp["use"]
        v, sd = pp["v"][u], pp["sd"][u]
        w = 1 / sd ** 2
        mu = np.sum(w * v) / np.sum(w)
        se_w = 1 / np.sqrt(np.sum(w))
        chi2 = np.sum(w * (v - mu) ** 2) / (len(v) - 1)
        se_cl = se_w * np.sqrt(max(chi2, 1.0))
        heads[case] = dict(job=jid, headline_ns=val["ns_per_decision"], boot_sigma_ns=val["sigma_ns"],
                           n_probes_used=int(u.sum()), weighted_mean_ns=float(mu), se_weighted_ns=float(se_w),
                           chi2_per_dof_probe_scatter=float(chi2), se_scatter_inflated_ns=float(se_cl),
                           median_probe_ns=float(np.median(v)), probe_sd_ns=float(np.std(v, ddof=1)))
    res["per_job"] = heads
    # ---- job-level drift of the (identical) REF circuits across all job pairs
    loaded = {c: R._load(j) for c, j in JOBS.items()}
    pair = {}
    for a, b in itertools.combinations(JOBS, 2):
        try:
            d = R.reference_drift_ns(loaded[a], loaded[b])
            pair[f"{a}~{b}"] = dict(cross_offsets_ns=d["cross_offsets_ns"], probes=d["common_monotonic_probes"],
                                    sep_s=abs((__import__("datetime").datetime.strptime(loaded[a]["meta"]["submitted"], "%Y-%m-%d %H:%M:%S")
                                               - __import__("datetime").datetime.strptime(loaded[b]["meta"]["submitted"], "%Y-%m-%d %H:%M:%S")).total_seconds()))
        except Exception as ex:  # noqa: BLE001
            pair[f"{a}~{b}"] = dict(error=str(ex))
    res["ref_drift_pairs"] = pair
    # signed offsets relative to WVOTE job (the median cross offset = job-level zero shift, ns of dead time/decision)
    ref_curves = {c: per_probe(j, c)["ref"].mean(1) for c, j in JOBS.items()}
    res["ref_mean_scores"] = {c: v.tolist() for c, v in ref_curves.items()}
    offs = []
    for a, b in itertools.combinations(JOBS, 2):
        o = pair[f"{a}~{b}"].get("cross_offsets_ns")
        if o:
            offs.append(max(abs(o[0]), abs(o[1])))
    res["job_level_sd_ns_estimate"] = float(np.sqrt(np.mean(np.square(offs)))) if offs else None

    # ---- fixed + per-op models on the 3 increments
    cases = ("SPARSE2", "SPARSE5", "WVOTE")
    y = np.array([heads[c]["headline_ns"] for c in cases])
    s_stat = np.array([heads[c]["boot_sigma_ns"] for c in cases])
    n = np.array([st[c]["ops"] for c in cases], float)
    inp = np.array([st[c]["inputs"] for c in cases], float)
    dep = np.array([st[c]["depth"] for c in cases], float)
    sj = res["job_level_sd_ns_estimate"] or 0.0
    s_job_tot = np.hypot(s_stat, sj)
    # NOTE: the increment is measured WITHIN each job against that job's own REF0, so the REF drift is only a
    # systematic on the ABSOLUTE decision time, not on the increment. Use it as an upper-bound random effect.

    def wls(x, y, s):
        A = np.vstack([np.ones_like(x), x]).T
        W = np.diag(1 / s ** 2)
        cov = np.linalg.inv(A.T @ W @ A)
        beta = cov @ A.T @ W @ y
        r = y - A @ beta
        return beta, cov, float(np.sum((r / s) ** 2)), r

    fits = {}
    for label, s in (("stat_only", s_stat), ("stat+job_level", s_job_tot)):
        beta, cov, chi2, r = wls(n, y, s)
        fits[label] = dict(intercept_ns=float(beta[0]), intercept_se=float(np.sqrt(cov[0, 0])),
                           slope_ns_per_op=float(beta[1]), slope_se=float(np.sqrt(cov[1, 1])),
                           corr=float(cov[0, 1] / np.sqrt(cov[0, 0] * cov[1, 1])),
                           chi2=chi2, dof=1, residuals_ns=r.tolist())
        # through-origin model (no fixed term): y = b*n
        b0 = np.sum(n * y / s ** 2) / np.sum(n ** 2 / s ** 2)
        chi0 = float(np.sum(((y - b0 * n) / s) ** 2))
        fits[label].update(origin_slope=float(b0), origin_se=float(1 / np.sqrt(np.sum(n ** 2 / s ** 2))),
                           origin_chi2=chi0, origin_dof=2)
        # mean-increment-per-op ratios
    res["fits"] = fits
    res["ratio_ns_per_op"] = (y / n).tolist()
    res["ratio_ns_per_op_se"] = (s_stat / n).tolist()
    # leave-one-out: predict SPARSE5 (interpolation) from the two ends, and extrapolation checks
    def two_pt(i, j):
        b = (y[j] - y[i]) / (n[j] - n[i])
        a = y[i] - b * n[i]
        se_b = np.hypot(s_stat[i], s_stat[j]) / (n[j] - n[i])
        return a, b, se_b
    a, b, seb = two_pt(0, 2)
    pred5 = a + b * n[1]
    sd_pred5 = np.sqrt(((n[2] - n[1]) / (n[2] - n[0]) * s_stat[0]) ** 2 + ((n[1] - n[0]) / (n[2] - n[0]) * s_stat[2]) ** 2)
    res["loo_sparse5"] = dict(pred=float(pred5), pred_sd=float(sd_pred5), meas=float(y[1]), meas_sd=float(s_stat[1]),
                              z=float((y[1] - pred5) / np.hypot(sd_pred5, s_stat[1])))
    a, b, seb = two_pt(0, 1)
    pred27 = a + b * n[2]
    sd27 = np.hypot(s_stat[0] * (n[2] - n[1]) / (n[1] - n[0]), s_stat[1] * (n[2] - n[0]) / (n[1] - n[0]))
    res["loo_wvote_from_7_19"] = dict(pred=float(pred27), pred_sd=float(sd27), meas=float(y[2]), meas_sd=float(s_stat[2]),
                                      z=float((y[2] - pred27) / np.hypot(sd27, s_stat[2])))
    # collinearity
    res["collinearity"] = dict(ops=n.tolist(), inputs=inp.tolist(), depth=dep.tolist(),
                               ops_equals_4n_minus_1=bool(np.all(n == 4 * inp - 1)),
                               corr_ops_inputs=float(np.corrcoef(n, inp)[0, 1]),
                               corr_ops_depth=float(np.corrcoef(n, dep)[0, 1]))
    # parametric bootstrap of slope / intercept (stat only and with job-level)
    rng = np.random.default_rng(0)
    for label, s in (("stat_only", s_stat), ("stat+job_level", s_job_tot)):
        sl, ic = [], []
        for _ in range(20000):
            yy = y + rng.normal(0, s)
            beta, *_ = wls(n, yy, s)
            ic.append(beta[0]); sl.append(beta[1])
        res["fits"][label]["slope_boot_95"] = np.percentile(sl, [2.5, 97.5]).tolist()
        res["fits"][label]["intercept_boot_95"] = np.percentile(ic, [2.5, 97.5]).tolist()
    # flat
    ff = heads["NOIF"]
    res["flat_vs_ibm_600"] = dict(flat_ns=ff["headline_ns"], sigma=ff["boot_sigma_ns"],
                                  z_vs_600=float((600 - ff["headline_ns"]) / ff["boot_sigma_ns"]),
                                  ci95=[ff["headline_ns"] - 1.96 * ff["boot_sigma_ns"], ff["headline_ns"] + 1.96 * ff["boot_sigma_ns"]])
    # total decision times
    meas = loaded["NOIF"]["meta"]["measure_2_s"] * 1e9
    res["total_from_measure_start_ns"] = {c: float(meas + ff["headline_ns"] + heads[c]["headline_ns"]) for c in cases}
    res["total_from_measure_start_ns"]["flat_only"] = float(meas + ff["headline_ns"])
    res["measure_2_ns"] = float(meas)
    if as_json:
        print(json.dumps(res, indent=1))
        return res
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main("--json" in sys.argv)
