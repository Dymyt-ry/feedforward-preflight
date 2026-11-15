"""Re-analyse ALL stopwatch latency jobs (legacy 2026-11-09 ladder + round 1) with the probe-level robust estimators.
usage: python reanalyse_latency_all.py [--legacy DIR] [--round1 DIR] [--json OUT.json]
Round-1 raw bits: round1_latency/<CASE>.npz (+ meta.json with K, n_z, ref_D). Legacy: legacy_stopwatch/<CASE>.npz + <CASE>_meta.json.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))   # package root: estimators.py, stopwatch_core.py
import estimators as E  # noqa: E402
import stopwatch_core as SW  # noqa: E402


def load_legacy(d):
    out = {}
    for case in ("NOIF", "SPARSE2", "SPARSE5", "WVOTE"):
        meta = json.load(open(os.path.join(d, f"{case}_meta.json")))
        z = np.load(os.path.join(d, f"{case}.npz"))
        prs = {k[3:]: z[k] for k in z.files if k.startswith("pr_")}
        out[case] = (prs, meta["n_z"], meta["K"], tuple(meta["ref_D"]))
    return out


def load_round1(d):
    meta = json.load(open(os.path.join(d, "meta.json")))
    out = {}
    for case in meta["cases"]:
        z = np.load(os.path.join(d, f"{case}.npz"))
        prs = {k[3:]: z[k] for k in z.files if k.startswith("pr_")}
        out[case] = (prs, meta["n_z"], meta["K"], tuple(meta["ref_D"]))
    return out


def table(title, data):
    rows = []
    for case, (prs, n_z, K, ref_D) in data.items():
        r = E.analyse_robust(prs, n_z, K, ref_D, case)
        leg = SW.analyse_data(prs, n_z, K, ref_D, (case,), verbose=False)[case]
        r["published_legacy_estimator"] = [float(leg[0]), float(leg[1])]
        rows.append(r)
    L = [f"### {title}", "| case | usable | published (clipped, shot sigma) | weighted unclipped | median | Huber +/- jackknife | Z-probe median | X-probe median | chi2/dof | scatter-inflated SE | clip kept |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            L.append(f"| {r['case']} | {r['n_probes_usable']} | {r['error']} |||||||||")
            continue
        p = r["published_legacy_estimator"]
        L.append(f"| {r['case']} | {r['n_probes_usable']}/{r['n_probes_total']} | {p[0]:.0f} +/- {p[1]:.0f} | {r['wmean_unclipped']:.0f} | {r['median']:.0f} | "
                 f"{r['huber']:.0f} +/- {r['jackknife_se_huber']:.0f} | {r.get('median_Z', float('nan')):.0f} | {r.get('median_X', float('nan')):.0f} | "
                 f"{r['chi2_per_dof']:.1f} | {r['se_scatter_inflated']:.0f} | {r['clip_kept']} |")
    return rows, "\n".join(L)


if __name__ == "__main__":
    a = sys.argv
    legacy = a[a.index("--legacy") + 1] if "--legacy" in a else os.path.join(os.path.dirname(HERE), "legacy_stopwatch")
    round1 = a[a.index("--round1") + 1] if "--round1" in a else os.path.join(os.path.dirname(HERE), "round1_latency")
    res = {}
    out = []
    for title, key, loader, d in (("Legacy ladder 2026-11-09 (K=8)", "legacy", load_legacy, legacy), ("Round 1 2026-11-15 (K=16)", "round1", load_round1, round1)):
        if os.path.isdir(d):
            rows, txt = table(title, loader(d))
            res[key] = rows
            out.append(txt)
    print("\n\n".join(out))
    if "--json" in a:
        json.dump(res, open(a[a.index("--json") + 1], "w"), indent=1)
