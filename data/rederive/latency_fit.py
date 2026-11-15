"""Fits of round-1 latency (Huber estimates, jackknife SE) vs op count / inputs / depth; pre-registered law check.
usage: python latency_fit.py [reanalysis.json]"""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
res = json.load(open(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else os.path.join(HERE, "..", "latency_reanalysis.json")))["round1"]
feat = {"XORC15": (15, 16, 15), "XORT15": (15, 16, 4), "POP8": (23, 8, 9), "WVOTE27": (27, 7, 9), "SOUP63": (63, 16, 6)}  # store ops, inputs, store depth
pre = {"XORC15": 659, "XORT15": 659, "POP8": 908, "WVOTE27": 1033, "SOUP63": 2155}   # pre-registered (190 + 31.2 ops, local, not externally timestamped)
names = [r["case"] for r in res]
y = np.array([r["huber"] for r in res]); s = np.array([r["jackknife_se_huber"] for r in res])
out = {"points": {r["case"]: dict(huber=round(r["huber"], 1), se=round(r["jackknife_se_huber"], 1), median=round(r["median"], 1), wmean_unclipped=round(r["wmean_unclipped"], 1),
       published=[round(x, 1) for x in r["published_legacy_estimator"]]) for r in res}}
z = (y - np.array([pre[n] for n in names])) / s
out["prereg"] = {"deviation_sigma": {n: round(float(a), 2) for n, a in zip(names, z)}, "chi2": round(float(np.sum(z ** 2)), 1), "dof": len(names),
                 "note": "prediction band (slope 31.3 +/- 6.3 from a 3-point fit) not included: at 63 ops the prediction itself had ~ +/-400 ns"}
def wls(cols, label):
    X = np.column_stack([np.ones(len(y))] + [np.array([feat[n][c] for n in names], float) for c in cols])
    W = np.diag(1 / s ** 2)
    cov = np.linalg.inv(X.T @ W @ X); b = cov @ X.T @ W @ y; r = y - X @ b
    chi2 = float(np.sum((r / s) ** 2)); dof = len(y) - X.shape[1]
    k = np.sqrt(max(chi2 / dof, 1.0)) if dof > 0 else 1.0
    return dict(model=label, coef=[round(float(v), 2) for v in b], se_scaled=[round(float(np.sqrt(cov[i, i]) * k), 2) for i in range(len(b))],
                chi2=round(chi2, 2), dof=dof, residuals={n: round(float(a), 0) for n, a in zip(names, r)})
out["fits"] = [wls([0], "ops"), wls([0, 1], "ops+inputs"), wls([0, 2], "ops+depth")]
# marginal slopes between consecutive ops levels
order = sorted(names, key=lambda n: feat[n][0])
out["marginal_ns_per_op"] = {f"{a}->{b}": round(float((out["points"][b]["huber"] - out["points"][a]["huber"]) / (feat[b][0] - feat[a][0])), 1) for a, b in zip(order, order[1:]) if feat[b][0] > feat[a][0]}
d = out["points"]["XORC15"]["huber"] - out["points"]["XORT15"]["huber"]; sd = float(np.hypot(out["points"]["XORC15"]["se"], out["points"]["XORT15"]["se"]))
out["chain_minus_tree"] = dict(ns=round(d, 1), se=round(sd, 1))
w = out["points"]["WVOTE27"]["huber"] - out["points"]["POP8"]["huber"]; sw = float(np.hypot(out["points"]["WVOTE27"]["se"], out["points"]["POP8"]["se"]))
out["wvote_minus_pop8"] = dict(ns=round(w, 1), se=round(sw, 1), note="WVOTE27 has 4 more ops (8 mul instead of 8 shifts... see structure), expected +4 ops * slope")
if "--write" in sys.argv:          # default: print only (the shipped data/latency_fit.json is not overwritten)
    json.dump(out, open(os.path.join(HERE, "..", "latency_fit.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
