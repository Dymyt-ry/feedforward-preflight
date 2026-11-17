# SPDX-License-Identifier: Apache-2.0
"""Rebuild the empirical dominance envelope from the shipped probe circuits (data/envelope_probes/*.qpy + index.json).

    python build_envelope.py                 # recompute every vector, run the consistency check and leave-one-out, print a summary
    python build_envelope.py --write         # also (re)write rules/envelope_ibm_heron_r2_2026-11-15.json

Everything is derived from the shipped circuits: vectors = resources.resource_vector(circuit). Outcomes come from index.json (QPU outcome, date, source).
The choice of the failure dimensions and the leave-one-out were made on ALL the data, so they are descriptive; only the frozen rounds (data/v1, data/e1, data/e2) are out-of-sample.
"""
from __future__ import annotations

import itertools
import json
import os
import sys

from qiskit import qpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import resources as R  # noqa: E402

sys.setrecursionlimit(50000)
DATA = os.path.join(HERE, "data", "envelope_probes")
RULES = os.path.join(HERE, "rules", "envelope_ibm_heron_r2_2026-11-15.json")


def load_rows():
    idx = json.load(open(os.path.join(DATA, "index.json")))["probes"]
    rows = []
    for p in idx:
        with open(os.path.join(DATA, p["qpy"]), "rb") as f:
            qc = qpy.load(f)[0]
        rows.append(dict(p, vector=R.resource_vector(qc)))
    return rows


def loo(rows, fail_dims, dims):
    """leave-one-out verdict counts over `rows` (list of dicts with class_, vector)."""
    acc = [(r["name"], r["vector"]) for r in rows if r["class_"] == "accept"]
    rej = [(r["name"], r["vector"]) for r in rows if r["class_"] == "reject6057"]
    stat = {"accept": dict.fromkeys(("PASS", "FAIL", "UNCERTAIN", "CONTRADICTION"), 0), "reject6057": dict.fromkeys(("PASS", "FAIL", "UNCERTAIN", "CONTRADICTION"), 0)}
    wrong = []
    for r in rows:
        if r["class_"] not in stat:
            continue
        a2 = [(n, v) for n, v in acc if n != r["name"]]
        r2 = [(n, v) for n, v in rej if n != r["name"]]
        verdict, _ = R.envelope_verdict(r["vector"], a2, r2, dims, fail_dims)
        stat[r["class_"]][verdict] += 1
        if (r["class_"] == "accept" and verdict in ("FAIL", "CONTRADICTION")) or (r["class_"] == "reject6057" and verdict in ("PASS", "CONTRADICTION")):
            wrong.append((r["name"], verdict))
    return stat, wrong


def build():
    rows = load_rows()
    dims = list(R.DIMENSIONS)
    mar = [r for r in rows if r["backend"] == "ibm_marrakesh"]
    acc = [r for r in mar if r["class_"] == "accept"]
    rej = [r for r in mar if r["class_"] == "reject6057"]
    contra = [(a["name"], b["name"]) for a in acc for b in rej if R.dominates(a["vector"], b["vector"], dims)]
    wit = {(a["name"], b["name"]): {d for d in dims if b["vector"][d] > a["vector"][d]} for a in acc for b in rej}
    pairs = list(wit.values())
    cand = sorted(set().union(*pairs)) if pairs else []
    best = None
    for size in range(1, 7):
        found = [S for S in itertools.combinations(cand, size) if all(w & set(S) for w in pairs)]
        if found:
            best = (size, found)
            break
    fail_dims = sorted(set().union(*[set(x) for x in best[1]])) if best else dims
    stat_all, wrong_all = loo(mar, fail_dims, dims)
    seen, uniq = set(), []
    for r in mar:
        k = (r["class_"], tuple(sorted(r["vector"].items())))
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    stat_uniq, wrong_uniq = loo(uniq, fail_dims, dims)
    # replicated-probe outcome agreement (NOT an independent test: the replicate vectors equal their marrakesh counterparts): each probe measured on another backend is judged by the envelope of the ibm_marrakesh probes ALONE (nothing left out
    # needed: no marrakesh probe is the kingston probe) and must come out as its observed outcome, or UNCERTAIN; a PASS/FAIL against the observed outcome is a mismatch
    xb = []
    ma = [(r["name"], r["vector"]) for r in acc]
    mr = [(r["name"], r["vector"]) for r in rej]
    for r in rows:
        if r["backend"] == "ibm_marrakesh" or r["class_"] not in ("accept", "reject6057"):
            continue
        verdict, _ = R.envelope_verdict(r["vector"], ma, mr, dims, fail_dims)
        xb.append(dict(name=r["name"], backend=r["backend"], observed=r["class_"], verdict_from_marrakesh_only=verdict,
                       agrees=verdict == {"accept": "PASS", "reject6057": "FAIL"}[r["class_"]], contradicts=verdict in ("CONTRADICTION", {"accept": "FAIL", "reject6057": "PASS"}[r["class_"]])))
    return dict(rows=rows, dims=dims, cross_backend=xb, contradictions=contra, minimal=best, fail_dims=fail_dims, loo_all=stat_all, loo_all_wrong=wrong_all,
                loo_unique=stat_uniq, loo_unique_wrong=wrong_uniq, n_unique=(sum(r["class_"] == "accept" for r in uniq), sum(r["class_"] == "reject6057" for r in uniq)),
                n_acc=len(acc), n_rej=len(rej))


def main(write=False):
    b = build()
    print(f"{b['n_acc']} accepted, {b['n_rej']} rejected (6057) probes on ibm_marrakesh; unique (vector, class) pairs: {b['n_unique'][0]} accepted, {b['n_unique'][1]} rejected")
    print("contradictions with all dimensions:", b["contradictions"] or "none")
    print("minimal dimension sets:", [list(s) for s in b["minimal"][1]] if b["minimal"] else None, "-> fail dims", b["fail_dims"])
    print("leave-one-out, UNIQUE (vector, class) pairs [headline]:", b["loo_unique"], "wrong:", b["loo_unique_wrong"])
    print("leave-one-out, all probes incl. identical replicates [secondary]:", b["loo_all"], "wrong:", b["loo_all_wrong"])
    xb = b["cross_backend"]
    print(f"replicated probes: {len(xb)} probes on other backends (same vectors as their marrakesh counterparts, so this records outcome agreement only, not an independent test); verdict from the ibm_marrakesh envelope alone agrees (PASS for accepted, FAIL for rejected) for {sum(x['agrees'] for x in xb)}, "
          f"contradicts for {sum(x['contradicts'] for x in xb)}:", [(x["name"], x["verdict_from_marrakesh_only"]) for x in xb])
    if write:
        out = dict(
            id="envelope_ibm_heron_r2_2026-11-15", kind="empirical monotone dominance envelope", error_code=6057, measured_date="2026-11-15 (plus 2026-11-06 historical probes, labelled per probe)",
            backend_family="ibm_heron_r2", backends_covered=["ibm_marrakesh"], backends_partial={"ibm_kingston": "5 register-rule probes only (R10x2, R11x2, R12x2, split6, non-foldable overlap), 2026-11-16, backend_version 1.0.0; latency, stack-need shapes and fix-pack were not run there",
                              "ibm_fez": "5 register-rule probes only (R10x2, R11x2, R12x2, split6, non-foldable overlap), submitted 2026-11-15 and executed 2026-11-16, backend_version 1.3.37; latency, stack-need shapes and fix-pack were not run there"},
            backend_coverage="ibm_marrakesh (all probes) + ibm_kingston and ibm_fez (5 register-rule probes each)", replicated_probe_outcome_agreement=b["cross_backend"],
            replicated_probe_outcome_agreement_note="The kingston/fez probes have the same resource vectors as their ibm_marrakesh counterparts (p3m), so the marrakesh-only envelope necessarily judges them as it judges those counterparts; this list therefore only records that the observed OUTCOMES agree across the three backends. It is not an independent test of the envelope on another backend.",
            vector_definition="resources.resource_vector(circuit) on the circuit as shipped (executed transpiled circuit where the source says so); see resources.py",
            dimensions=R.DIMENSIONS, flag_dimensions=list(R.FLAG_DIMS),
            verdict_rule=dict(PASS="vector <= the vector of some ACCEPTED probe in every dimension of pass_dims; never PASS if unanalysed > 0 or the circuit has no if (monotonicity is an ASSUMPTION)",
                              FAIL="vector >= the vector of some REJECTED probe in every dimension of fail_dims", UNCERTAIN="neither", CONTRADICTION="both (never occurs on the stored probes)"),
            pass_dims=b["dims"], fail_dims=b["fail_dims"], minimal_dimension_sets=[list(s) for s in b["minimal"][1]] if b["minimal"] else [], contradictions_all_dims=b["contradictions"],
            leave_one_out=b["loo_unique"], leave_one_out_wrong=[list(x) for x in b["loo_unique_wrong"]],
            leave_one_out_basis="UNIQUE (vector, class) pairs (identical replicates removed); computed on all data, so descriptive, not out-of-sample",
            leave_one_out_all_probes=b["loo_all"], leave_one_out_all_probes_wrong=[list(x) for x in b["loo_all_wrong"]],
            unique_counts=dict(accepted=b["n_unique"][0], rejected=b["n_unique"][1]),
            caveats=["measured on ibm_marrakesh; only the 5 register-rule probes were replicated on ibm_kingston and ibm_fez (identical outcomes, three backend versions 1.0.21 / 1.0.0 / 1.3.37; both executed 2026-11-16); latency, stack-need shapes and fix-pack were measured only on ibm_marrakesh. Dates in the project: four (2026-11-16: the kingston and fez replications; 2026-11-06: the 29 'old' probes, 25 accepted and 4 rejected, run once and never repeated; 2026-11-09: the legacy latency ladder, not part of this envelope; 2026-11-15: everything else)",
                     "the headline counts (accepted, rejected, unique, leave-one-out) are over the ibm_marrakesh probes; the kingston and fez probes are shipped in the envelope as replicates (their vectors equal those of the marrakesh p3 probes) and do not change a verdict",
                     "monotonicity is an assumption: the stored probes do not contradict it but %d rejected probes cannot prove it" % b["n_rej"],
                     "the failure dimensions and the leave-one-out were chosen/computed on ALL the data (descriptive, not out-of-sample); only data/v1, data/e1, data/e2 are frozen out-of-sample rounds",
                     "probes rejected with another code (6066, fix-pack) are excluded from this envelope",
                     "a vector takes the elementwise MAX over the cones of all ifs and the circuit-wide peak of live registers; the backend may count differently",
                     "PASS needs domination in ALL dimensions, so many realistic circuits are UNCERTAIN; that is intended (conservative)"],
            probes=[dict(name=r["name"], class_=r["class_"], backend=r["backend"], measured_date=r["measured_date"], source=r["circuit_source"], circuit_shipped=r["qpy"], vector=r["vector"]) for r in b["rows"]])
        json.dump(out, open(RULES, "w"), indent=1)
        print("wrote", RULES)
    return b


if __name__ == "__main__":
    main("--write" in sys.argv)
