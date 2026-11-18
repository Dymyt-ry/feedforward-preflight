# SPDX-License-Identifier: Apache-2.0
"""Rules as DATA for dccheck: load / validate / evaluate / derive.

A rules file (see rules/rules.schema.json) holds empirical thresholds measured on a QPU on a given date. Nothing about the
thresholds is hard-coded in dccheck.py; the only code-side knowledge is the list of *metric names* an analysis can produce
(see dccheck.METRICS) and the names of static detectors.

Verdict semantics for one threshold rule on one metric value v (inclusive integer bounds):
  v in uncertain zone                      -> UNCERTAIN
  fail_min is not None and v >= fail_min   -> FAIL       (UNCERTAIN if the rule is not `confirmed`)
  v <= pass_max                            -> PASS
  otherwise (above every accepted probe, below every rejected one, no zone) -> UNCERTAIN (outside the observed range)
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_DIR = os.path.join(HERE, "rules")
SCHEMA_PATH = os.path.join(RULES_DIR, "rules.schema.json")
PASS, FAIL, UNCERTAIN = "PASS", "FAIL", "UNCERTAIN"
NOT_APPLICABLE, ERROR = "NOT_APPLICABLE", "ERROR"
_ORDER = {NOT_APPLICABLE: 0, PASS: 1, UNCERTAIN: 2, FAIL: 3, ERROR: 4}


def worst(statuses):
    statuses = list(statuses)
    return max(statuses, key=lambda s: _ORDER[s]) if statuses else PASS


# ------------------------------------------------------------------ tiny JSON-schema subset validator
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def _check(v, sch, path, errs):
    t = sch.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is_type(v, x) for x in ts):
            errs.append(f"{path}: expected {'/'.join(ts)}, got {type(v).__name__}")
            return
    if "enum" in sch and v not in sch["enum"]:
        errs.append(f"{path}: {v!r} not in {sch['enum']}")
    if "minimum" in sch and isinstance(v, (int, float)) and v < sch["minimum"]:
        errs.append(f"{path}: {v} < minimum {sch['minimum']}")
    if isinstance(v, dict):
        for k in sch.get("required", []):
            if k not in v:
                errs.append(f"{path}: missing required key {k!r}")
        for k, sub in sch.get("properties", {}).items():
            if k in v:
                _check(v[k], sub, f"{path}.{k}", errs)
    if isinstance(v, list) and "items" in sch:
        for i, x in enumerate(v):
            _check(x, sch["items"], f"{path}[{i}]", errs)


def validate_rules(obj, schema=None):
    """Return a list of error strings (empty = valid): schema subset + semantic checks."""
    if schema is None:
        with open(SCHEMA_PATH) as f:
            schema = json.load(f)
    errs = []
    _check(obj, schema, "$", errs)
    if errs:
        return errs
    seen = set()
    for r in obj.get("rules", []):
        if r["id"] in seen:
            errs.append(f"duplicate rule id {r['id']}")
        seen.add(r["id"])
        pm, fm = r["pass_max"], r["fail_min"]
        if pm is not None and fm is not None and pm >= fm and r.get("consistent", True):
            errs.append(f"rule {r['id']}: pass_max {pm} >= fail_min {fm} but consistent is not false")
        z = r["uncertain"]
        if z and z["from"] > z["to"]:
            errs.append(f"rule {r['id']}: uncertain zone from > to")
    keys = set(obj["official_texts"])
    groups = list(obj.get("rules", [])) + list(obj.get("static_hazards", [])) + list(obj["official_rules"]) + list(obj["uncertain"]) + list(obj.get("info_notes", []))
    for g in groups:
        for k in g.get("official", []):
            if k not in keys:
                errs.append(f"{g['id']}: official text {k!r} is not defined in official_texts")
    for k, v in obj["official_texts"].items():
        if not (isinstance(v, dict) and v.get("quote") and str(v.get("url", "")).startswith("https://")):
            errs.append(f"official_texts.{k}: needs quote and https url")
    try:
        _dt.date.fromisoformat(obj["measured_date"])
    except ValueError:
        errs.append("measured_date must be YYYY-MM-DD")
    return errs


def load_rules(path=None):
    """Load + validate. path=None picks the newest rules/*.json by measured_date (schema/curation files are skipped)."""
    if path is None:
        cands = []
        for fn in sorted(os.listdir(RULES_DIR)):
            if not fn.endswith(".json") or fn == "rules.schema.json" or fn.startswith(("curation", "envelope")):
                continue
            try:
                with open(os.path.join(RULES_DIR, fn)) as f:
                    o = json.load(f)
                cands.append((o["measured_date"], fn))
            except Exception:  # noqa: BLE001
                continue
        if not cands:
            raise FileNotFoundError(f"no rules file in {RULES_DIR}")
        path = os.path.join(RULES_DIR, max(cands)[1])
    with open(path) as f:
        obj = json.load(f)
    errs = validate_rules(obj)
    if errs:
        raise ValueError(f"invalid rules file {os.path.basename(path)}: " + "; ".join(errs))
    obj["_path"] = os.path.basename(path)
    return obj


def data_provenance(rules, env=None):
    """Provenance of the data behind the rules, computed from the envelope probe list (not hard-coded):
    dict(n_probes, backends={name: dict(n, first, last)}, primary, newest). Falls back to the rules file's own backends/measured_date."""
    per = {}
    for p in (env or {}).get("probes", []):
        d = str(p.get("measured_date") or rules["measured_date"])[:10]
        e = per.setdefault(p.get("backend", "?"), dict(n=0, first=d, last=d))
        e["n"] += 1
        e["first"], e["last"] = min(e["first"], d), max(e["last"], d)
    if not per:
        return dict(n_probes=0, backends={b: dict(n=0, first=rules["measured_date"], last=rules["measured_date"]) for b in rules["backends"]},
                    primary=(rules["backends"] or ["?"])[0], newest=rules["measured_date"])
    primary = max(per, key=lambda b: (per[b]["n"], b))
    newest = max(max(e["last"] for e in per.values()), rules["measured_date"])
    return dict(n_probes=sum(e["n"] for e in per.values()), backends=per, primary=primary, newest=newest)


def provenance_text(prov, age):
    """'115 probes; ibm_marrakesh 2026-11-06..2026-11-15, replications on ibm_kingston and ibm_fez 2026-11-16; newest data 2 days old'"""
    rng = lambda e: e["first"] if e["first"] == e["last"] else f"{e['first']}..{e['last']}"  # noqa: E731
    pb = prov["backends"]
    parts = [f"{prov['primary']} {rng(pb[prov['primary']])}"]
    others = [b for b in pb if b != prov["primary"]]
    if others:
        dates = {rng(pb[b]) for b in others}
        names = " and ".join(others) if len(others) <= 2 else ", ".join(others)
        parts.append(f"replications on {names} " + (dates.pop() if len(dates) == 1 else "; ".join(f"{b} {rng(pb[b])}" for b in others)))
    head = f"{prov['n_probes']} probes; " if prov["n_probes"] else ""
    return f"{head}{', '.join(parts)}; newest data {age} days old"


def age_days(rules, today=None, env=None):
    """Age of the NEWEST measurement behind the rules (envelope probes included), not of the rules file's own date."""
    today = today or _dt.date.today()
    return (today - _dt.date.fromisoformat(data_provenance(rules, env)["newest"])).days


# ------------------------------------------------------------------ evaluation
def latency_estimate(lat, ops, z=2.0):
    """Estimate (ns, an INCREMENT over a bare 1-bit `if`, not an absolute time) with a deliberately WIDE interval.
    If the latency block has `ranges` (curated): lo = intercept_lo + slope_lo*ops - err_lo, hi = intercept_hi + slope_hi*ops + err_hi.
    Otherwise (a freshly fitted block without curation): +-z*sqrt(var(fit) + scatter^2). The central value is the fit; it is not a law."""
    a, b = lat["intercept_ns"], lat["slope_ns_per_op"]
    est = a + b * ops
    rg = lat.get("ranges")
    if rg:
        lo = max(rg["intercept_ns"][0] + rg["slope_ns_per_op"][0] * ops - rg["point_error_ns"][0], 0.0)
        hi = rg["intercept_ns"][1] + rg["slope_ns_per_op"][1] * ops + rg["point_error_ns"][1]
        lo, hi = min(lo, est), max(hi, est)
        level = (f"range: slope {rg['slope_ns_per_op'][0]:g}-{rg['slope_ns_per_op'][1]:g} ns/op, intercept {rg['intercept_ns'][0]:g}-{rg['intercept_ns'][1]:g} ns, "
             f"per-point scatter {rg['point_error_ns'][0]:g}-{rg['point_error_ns'][1]:g} ns; not a statistical interval")
        fr = rg.get("fit_range_ops") or lat.get("fit_range_ops")
    else:
        var = lat.get("intercept_se_ns", 0.0) ** 2 + (ops * lat.get("slope_se_ns", 0.0)) ** 2 + 2 * ops * lat.get("cov_ns2", 0.0)
        half = z * math.sqrt(max(var, 0.0) + lat["scatter_ns"] ** 2)
        lo, hi, level = max(est - half, 0.0), est + half, "~95% (2 sigma incl. scatter)"
        fr = lat.get("fit_range_ops")
    ext = bool(fr and (ops < fr[0] or ops > fr[1]))
    return dict(ops=ops, estimate_ns=est, lo_ns=lo, hi_ns=hi, level=level, extrapolated=ext, fit_range_ops=fr)


# ------------------------------------------------------------------ derivation (used by export_rules.py)
def _wls_line(x, y, s):
    """Weighted least squares y = a + b x. Returns a, b, se_a, se_b, cov_ab, chi2."""
    w = [1.0 / (si * si) for si in s]
    S = sum(w)
    Sx = sum(wi * xi for wi, xi in zip(w, x))
    Sxx = sum(wi * xi * xi for wi, xi in zip(w, x))
    Sy = sum(wi * yi for wi, yi in zip(w, y))
    Sxy = sum(wi * xi * yi for wi, xi, yi in zip(w, x, y))
    det = S * Sxx - Sx * Sx
    a = (Sxx * Sy - Sx * Sxy) / det
    b = (S * Sxy - Sx * Sy) / det
    chi2 = sum(wi * (yi - a - b * xi) ** 2 for wi, xi, yi in zip(w, x, y))
    return a, b, math.sqrt(Sxx / det), math.sqrt(S / det), -Sx / det, chi2


def fit_latency(points):
    """points: list of dict(probe, ops, latency_ns, sigma_ns[, klass]). -> latency block (without provenance)."""
    xs = [p["ops"] for p in points]
    ys = [p["latency_ns"] for p in points]
    ss = [p["sigma_ns"] for p in points]
    if len(set(xs)) < 2:
        raise ValueError("latency fit needs >= 2 distinct op counts")
    a, b, sa, sb, cov, chi2 = _wls_line(xs, ys, ss)
    res = [y - a - b * x for x, y in zip(xs, ys)]
    rms = math.sqrt(sum(r * r for r in res) / len(res))
    halves = sorted((max([p["latency_ns"]] + list(p.get("alts", []))) - min([p["latency_ns"]] + list(p.get("alts", [])))) / 2
                    for p in points if p.get("alts"))
    est_floor = halves[len(halves) // 2] if halves else 0.0          # median half-range between alternative estimators of the same probe
    scatter = math.sqrt(rms * rms + est_floor * est_floor)
    blk = dict(model="linear_ops", intercept_ns=round(a, 2), intercept_se_ns=round(sa, 2), slope_ns_per_op=round(b, 3),
               slope_se_ns=round(sb, 3), cov_ns2=round(cov, 3), scatter_ns=round(scatter, 1),
               residual_rms_ns=round(rms, 1), estimator_half_range_ns=round(est_floor, 1),
               fit_range_ops=[min(xs), max(xs)], chi2=round(chi2, 2), dof=len(xs) - 2,
               points=[dict(probe=p["probe"], ops=p["ops"], latency_ns=p["latency_ns"], sigma_ns=p["sigma_ns"],
                            klass=p.get("klass", "?"), residual_ns=round(r, 1),
                            alt_estimates_ns=[round(a_, 1) for a_ in p.get("alts", [])]) for p, r in zip(points, res)])
    by = {}
    for k in sorted({p.get("klass") for p in points if p.get("klass")}):
        sub = [p for p in points if p.get("klass") == k]
        if len(sub) >= 3 and len({p["ops"] for p in sub}) >= 2:
            a2, b2, sa2, sb2, _, c2 = _wls_line([p["ops"] for p in sub], [p["latency_ns"] for p in sub], [p["sigma_ns"] for p in sub])
            by[k] = dict(intercept_ns=round(a2, 1), slope_ns_per_op=round(b2, 2), n_points=len(sub), chi2=round(c2, 2))
        else:
            by[k] = dict(n_points=len(sub), note="too few points for a separate fit")
    blk["by_class"] = by
    return blk


def load_envelope(rules):
    """The empirical dominance envelope named by rules["envelope_file"] (rules/envelope_*.json, written by the conformance suite)."""
    fn = rules.get("envelope_file")
    if not fn:
        return None
    with open(os.path.join(RULES_DIR, fn)) as f:
        env = json.load(f)
    for k in ("probes", "pass_dims", "fail_dims"):
        if k not in env:
            raise ValueError(f"envelope file {fn}: missing {k}")
    return env


def witness_provenance(env, names):
    """"measured empirically on <backend> <date of the witness>" (with 'historical, not re-run since' for older probes)"""
    meta = {x["name"]: x for x in env["probes"]}
    base = str(env.get("measured_date", "?"))[:10]
    parts = []
    for n in names or []:
        d = meta.get(n, {}).get("measured_date") or base
        be = meta.get(n, {}).get("backend") or ", ".join(env.get("backends_covered", []))
        parts.append(f"{n.split(':', 1)[-1]}: measured empirically on {be} {d}" + (", historical, not re-run since" if d != base else ""))
    return "; ".join(parts) if parts else f"measured empirically on {', '.join(env.get('backends_covered', []))} {base}"


def envelope_check(env, vec):
    """Envelope verdict for a resource vector with the witnessing probe named. Returns dict(status, reason, witness, dims, probes).
    PASS: dominated (<=) by an accepted probe in every pass dimension. FAIL: dominates (>=) a rejected (6057) probe in every fail dimension.
    Both: UNCERTAIN (contradiction). Neither: UNCERTAIN, listing the dimensions in which the closest accepted probe is exceeded."""
    import resources as RES
    acc = [(x["name"], x["vector"]) for x in env["probes"] if x["class_"] == "accept"]
    rej = [(x["name"], x["vector"]) for x in env["probes"] if x["class_"] == "reject6057"]
    verdict, wit = RES.envelope_verdict(vec, acc, rej, env["pass_dims"], env["fail_dims"])
    short = lambda n: n.split(":", 1)[-1]  # noqa: E731
    meta = {x["name"]: x for x in env["probes"]}
    base = f"{', '.join(env.get('backends_covered', []))}, measured {str(env.get('measured_date', '?'))[:10]}"

    def when_of(name):
        d = meta.get(name, {}).get("measured_date")
        if d and d != str(env.get("measured_date", ""))[:10]:
            return f"{', '.join(env.get('backends_covered', []))}, measured {d}, historical, not re-run since"
        return base
    when = base
    if verdict == "PASS":
        return dict(status=PASS, witness=[wit], dims=[], reason=f"dominated by accepted probe {short(wit)} ({when_of(wit)}): no larger in any of the {len(env['pass_dims'])} resource dimensions")
    if verdict == "FAIL":
        w = dict(rej)[wit]
        return dict(status=FAIL, witness=[wit], dims=list(env["fail_dims"]),
                    reason=f"at least as demanding as rejected probe {short(wit)} ({when_of(wit)}) in every failure-relevant dimension ({', '.join(f'{d}={vec[d]}>={w[d]}' for d in env['fail_dims'])}): error 6057 expected")
    if verdict == "CONTRADICTION":
        return dict(status=UNCERTAIN, witness=list(wit), dims=[], reason=f"contradiction: dominated by accepted probe {short(wit[0])} but also at least as demanding as rejected probe {short(wit[1])}")
    best = min(acc, key=lambda a: (sum(1 for d in env["pass_dims"] if vec[d] > a[1][d]), a[0]))
    over = [d for d in env["pass_dims"] if vec[d] > best[1][d]]
    return dict(status=UNCERTAIN, witness=[best[0]], dims=over,
                reason=f"exceeds every accepted probe in some dimension; the closest, {short(best[0])}, is exceeded in {', '.join(f'{d} ({vec[d]} > {best[1][d]})' for d in over)}; "
                       f"and it is not at least as demanding as any rejected probe in all of {', '.join(env['fail_dims'])}")
