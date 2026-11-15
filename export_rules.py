# SPDX-License-Identifier: Apache-2.0
"""Build a dccheck rules file (rules/*.json) from QPU results.

Inputs (any mix):
  * probe-outcome files in the format of data/probe_outcomes.json  ({"backend", "date", "rounds": {name: [{probe, status, error_code, ...}]}})
  * suite manifests out/manifest_<hash>.json  (+ the sibling out/fetch_<hash>.json written by `suite.py --fetch`)
plus a curation file (rules/curation_*.json: rule list/order, curated uncertain zones, earlier-day observations, static hazards).

For every probe the circuit is REBUILT with suite.py and analysed with dccheck (cone registers, op counts), so the thresholds are
derived from (circuit metrics, accepted/rejected), never typed in. Latency is a weighted least-squares line over the LAT probes.

    python export_rules.py data/probe_outcomes.json --curation rules/curation_ibm_heron_r2.json --out rules/ibm_heron_r2_2026-11-15.json
    python suite.py --export-rules out/manifest_<hash>.json ... --curation rules/curation_X.json --rules-out rules/X.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from qiskit import qpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dccheck as D  # noqa: E402
import dcrules as R  # noqa: E402


def probe_circuits(name):
    """Rebuild the circuit(s) a suite probe submitted (several for the A/B pattern probes)."""
    if name.startswith("P1_") or name.startswith("V1_"):
        import p1_probes as P1
        return (P1.p1_progs() if name.startswith("P1_") else P1.v1_progs())[name].circuits()
    if name.startswith("FP_") or name.startswith("FP2_"):      # packed fix-pack circuits stored as QPY (v1: data/fixpack_candidates_v1, v2: data/fixpack_candidates)
        d = "fixpack_candidates_v1" if name.startswith("FP_") else "fixpack_candidates"
        out = []
        for pat in ("last", "mid", "all"):
            fn = os.path.join(HERE, "data", d, f"{name}_{pat}_packed.qpy")
            if not os.path.exists(fn):
                raise KeyError(name)
            with open(fn, "rb") as f:
                out.append(qpy.load(f)[0])
        return out
    import suite as S
    cat = {p["name"]: p for p in S.catalogue()}
    if name in cat and cat[name]["fam"] == "LAT":
        return [S.build_lat(cat[name], K=S.LAT_K, n_z=S.LAT_NZ, n_x=S.LAT_NX, case="CASE")]
    if any(x[0] == name for x in S.REGB):
        return S.regb_circuits(name)
    if any(x["name"] == name for x in S.SPLIT):
        return S.split_circuits(name)
    return [S.acc_circuit_by_name(name)]            # KeyError for unknown probes


# ------------------------------------------------------------------ outcome loading
def _alts(r):
    """Alternative estimators of the same latency point (median, unclipped weighted mean, older clipped headline), if recorded."""
    a = [r[k] for k in ("median_ns", "weighted_unclipped_ns") if r.get(k) is not None]
    if r.get("published_style_clipped_ns"):
        a.append(r["published_style_clipped_ns"][0])
    return a


def _records_from_outcomes(obj, src):
    recs = []
    for rnd, lst in obj["rounds"].items():
        for r in lst:
            recs.append(dict(probe=r["probe"], status=r["status"], error_code=r.get("error_code"), latency_ns=r.get("latency_ns"),
                             sigma_ns=r.get("se_ns", r.get("sigma_ns")), store_ops=r.get("store_ops"), backend=obj.get("backend"), date=obj.get("date"),
                             round=rnd, source=os.path.basename(src), alts=_alts(r)))
    return recs


def _records_from_manifest(path):
    man = json.load(open(path))
    fp = os.path.join(os.path.dirname(path), f"fetch_{man['plan_hash']}.json")
    if not os.path.exists(fp):
        raise SystemExit(f"{path}: no sibling {os.path.basename(fp)}; run `suite.py --fetch {path}` first")
    res = json.load(open(fp))
    import suite as S
    recs = []
    for rec in man["jobs"]:
        r = res.get(rec["name"], {})
        ent = dict(probe=rec["name"], status=r.get("status", "NOT_SUBMITTED"), error_code=r.get("code"), latency_ns=None, sigma_ns=None,
                   store_ops=None, backend=man["backend"], date=str(man["submitted_at"])[:10], round=man["tier"],
                   source=os.path.basename(path))
        if rec["kind"] == "LAT" and r.get("status") == "DONE":
            import numpy as np
            z = np.load(os.path.join(os.path.dirname(path), r["npz"]))
            prs = {k[3:]: z[k] for k in z.files if k.startswith("pr_")}
            v, s = S.SW.analyse_data(prs, man["n_z"], man["lat_k"], tuple(man["ref_D"]), (rec["name"],), verbose=False)[rec["name"]]
            ent.update(latency_ns=float(v), sigma_ns=float(s))
        recs.append(ent)
    return recs


def load_records(paths):
    recs = []
    for p in paths:
        obj = json.load(open(p))
        if "rounds" in obj:
            recs += _records_from_outcomes(obj, p)
        elif "plan_hash" in obj:
            recs += _records_from_manifest(p)
        else:
            raise SystemExit(f"{p}: neither a probe-outcome file nor a suite manifest")
    return recs


# ------------------------------------------------------------------ build
def collect(paths, circuits=probe_circuits, log=print):
    """Rebuild + analyse every probe of the inputs. Returns (observations, latency points, skipped probe names, excluded descriptions).
    Each observation carries the resource vector (resources.resource_vector) and the expression constructs of its first circuit."""
    import resources as RES
    recs = load_records(paths)
    obs, lat_pts, skipped, excluded = [], [], [], []
    for r in recs:
        try:
            cs = circuits(r["probe"])
        except KeyError:
            skipped.append(r["probe"])
            continue
        if r["status"] == "DONE":
            oc = "pass"
        elif r["status"] == "ERROR" and r["error_code"] is not None:
            oc = "fail"
        else:
            excluded.append(f"{r['probe']} ({r['status']}, code {r['error_code']})")
            continue
        sig = set()
        for c in cs:
            sig |= D.construct_signatures(c)
        obs.append(dict(probe=r["probe"], outcome=oc, error_code=r["error_code"], vector=RES.resource_vector(cs[0]), constructs=sorted(sig),
                        date=r["date"], backend=r["backend"], round=r["round"]))
        if r["latency_ns"] is not None:
            e = D.analyze(cs[0]).entries[0]
            if r["store_ops"] is not None and r["store_ops"] != e["latency_ops"]:
                log(f"warning: {r['probe']}: recorded store_ops {r['store_ops']} != analysed {e['latency_ops']}")
            lat_pts.append(dict(probe=r["probe"], ops=e["latency_ops"], latency_ns=r["latency_ns"], sigma_ns=r["sigma_ns"], klass=e["klass"],
                                alts=r.get("alts", [])))
    return obs, lat_pts, skipped, excluded


def build_rules(paths, curation_path, circuits=probe_circuits, log=print):
    cur = json.load(open(curation_path))
    obs, lat_pts, skipped, excluded = collect(paths, circuits, log)
    cov = {}
    for o in obs:                                   # constructs of ACCEPTED probes, with evidence
        if o["outcome"] == "pass":
            for sg in o["constructs"]:
                cov.setdefault(sg, []).append(o["probe"])
    accepted_constructs = [dict(signature=k, evidence=list(dict.fromkeys(v[:3] + v[-2:]))) for k, v in sorted(cov.items())]   # first three and latest two accepted probes
    lat = R.fit_latency(lat_pts) if len(lat_pts) >= 2 else None
    if lat is None:
        raise SystemExit("need >= 2 latency probes with data for the latency model")
    dates = sorted({o["date"] for o in obs if o["date"]})
    backends = sorted({o["backend"] for o in obs if o["backend"]})
    date = dates[-1]
    prov = f"measured on {', '.join(backends)} {date}" + (f" (earliest probe {dates[0]})" if dates[0] != date else "")
    lat["provenance"] = prov + f" (stopwatch method, {len(lat_pts)} probes)"
    lr = cur.get("latency_ranges")
    if lr:
        lat["ranges"] = lr
        for pt in lat["points"]:                                   # the curated band must contain every measured point
            lo = lr["intercept_ns"][0] + lr["slope_ns_per_op"][0] * pt["ops"] - lr["point_error_ns"][0]
            hi = lr["intercept_ns"][1] + lr["slope_ns_per_op"][1] * pt["ops"] + lr["point_error_ns"][1]
            if not lo <= pt["latency_ns"] <= hi:
                log(f"warning: latency point {pt['probe']} ({pt['latency_ns']} ns at {pt['ops']} ops) lies outside the curated range [{lo:.0f}, {hi:.0f}]")
    lat["excludes"] = "measurement duration (measure_2 is about 1.88 us scheduled) and any wait shared with a bare 1-bit if; ops counted as in the fit points"
    out = dict(
        schema_version=1, rules_id=cur["rules_id"], backend_family=cur.get("backend_family", ""), backends=backends, measured_date=date,
        provenance=prov, software=cur.get("software", {}), envelope_file=cur["envelope_file"],
        generated_by=dict(tool="export_rules.py", inputs=[os.path.basename(p) for p in paths] + [os.path.basename(curation_path)],
                          n_probes=len(obs), skipped_probes=skipped, excluded_probes=excluded),
        rules=[], accepted_constructs=accepted_constructs, model_estimates=cur.get("model_estimates", []),
        static_hazards=cur.get("static_hazards", []), latency=lat,
        official_texts=cur["official_texts"], official_rules=cur["official_rules"], uncertain=cur["uncertain"],
        info_notes=cur.get("info_notes", []), known_unmodeled=cur.get("known_unmodeled", []))
    errs = R.validate_rules(out)
    if errs:
        raise SystemExit("generated rules file is invalid: " + "; ".join(errs))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="+", help="probe_outcomes-format json and/or suite manifests")
    ap.add_argument("--curation", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = build_rules(a.inputs, a.curation)
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1, sort_keys=False)
        f.write("\n")
    print(f"wrote {a.out}: {out['generated_by']['n_probes']} probes, {len(out['accepted_constructs'])} accepted constructs, latency "
          f"{out['latency']['intercept_ns']:.0f} + {out['latency']['slope_ns_per_op']:.1f} ns/op; envelope: {out['envelope_file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
