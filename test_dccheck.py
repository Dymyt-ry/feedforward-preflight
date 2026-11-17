# SPDX-License-Identifier: Apache-2.0
"""Tests for dccheck (pre-flight checker), the rules exporter and --fix-pack. Offline, no QPU, no IBM access.
run:  ../../.venv/bin/python test_dccheck.py          (plain asserts; prints the confusion matrices)
"""
import copy
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import traceback
from contextlib import redirect_stdout

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, qpy
from qiskit.circuit.classical import expr, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dccheck as D  # noqa: E402
import dcfix as F  # noqa: E402
import dcrules as R  # noqa: E402
import export_rules as E  # noqa: E402

PY = sys.executable
OUTCOMES = os.path.join(HERE, "data", "probe_outcomes.json")
P1OUT = os.path.join(HERE, "data", "probe_outcomes_round4_p1.json")
V1OUT = os.path.join(HERE, "data", "probe_outcomes_v1.json")
FPOUT = os.path.join(HERE, "data", "probe_outcomes_fixpack_v1.json")
FP2OUT = os.path.join(HERE, "data", "probe_outcomes_fixpack_v2.json")
ALLOUT = [OUTCOMES, P1OUT, V1OUT, FPOUT, FP2OUT]
CURATION = os.path.join(HERE, "rules", "curation_ibm_heron_r2.json")
RULES = R.load_rules(os.path.join(HERE, "rules", "ibm_heron_r2_2026-11-15.json"))
ENV = R.load_envelope(RULES)
import resources as RES  # noqa: E402
TODAY = dt.date.fromisoformat(RULES["measured_date"])
LOG = []


def say(*a):
    print(*a)
    LOG.append(" ".join(str(x) for x in a))


# ------------------------------------------------------------------ circuit builders
def regs_circuit(n_regs, width, sink_width=None, name=None, measure="measure", use_index=False, nreads=None):
    """n_regs measured registers; S = XOR of the first nreads registers (whole-register Uint) or, with use_index, XOR of bit 1 of each."""
    nreads = nreads or n_regs
    q = QuantumRegister(n_regs * width + 1, "q")
    out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(width, f"G{i}") for i in range(n_regs)]
    S = ClassicalRegister(width, "S")
    L = ClassicalRegister(1, "L")
    qc = QuantumCircuit(q, out, *regs, S, L, name=name or f"R{n_regs}x{width}")
    for i, r in enumerate(regs):
        for j in range(width):
            qc.h(q[i * width + j])
            qc.measure(q[i * width + j], r[j])
    if use_index:
        e = expr.bit_xor(expr.lift(regs[0][1]), expr.lift(regs[1][1]))
        for r in regs[2:nreads]:
            e = expr.bit_xor(e, expr.lift(r[1]))
        qc.store(L[0], e)
        cond = expr.lift(L[0])
    else:
        e = expr.bit_xor(regs[0], regs[1])
        for r in regs[2:nreads]:
            e = expr.bit_xor(e, r)
        qc.store(S, e)
        cond = expr.lift(S[0])
    with qc.if_test(cond):
        qc.x(q[n_regs * width])
    qc.measure(q[n_regs * width], out[0])
    return qc


def three_ifs():
    """one circuit, three ifs with different cones: 3, 11 and 12 registers."""
    n, w = 12, 2
    q = QuantumRegister(n * w + 3, "q")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]
    A, B, C = (ClassicalRegister(1, nm) for nm in "ABC")
    qc = QuantumCircuit(q, *regs, A, B, C, name="three_ifs")
    for i, r in enumerate(regs):
        for j in range(w):
            qc.h(q[i * w + j])
            qc.measure(q[i * w + j], r[j])

    def xor_of(rs):
        e = expr.bit_xor(expr.lift(rs[0][0]), expr.lift(rs[1][0]))
        for r in rs[2:]:
            e = expr.bit_xor(e, expr.lift(r[0]))
        return e
    for tgt, rs, qq in ((A, regs[:3], n * w), (B, regs[:11], n * w + 1), (C, regs[:12], n * w + 2)):
        qc.store(tgt[0], xor_of(rs))
        with qc.if_test(expr.lift(tgt[0])):
            qc.x(q[qq])
    return qc


# ------------------------------------------------------------------ tests: rules file
def test_rules_file_valid_and_reproducible():
    assert R.validate_rules(RULES) == []
    regen = E.build_rules(ALLOUT, CURATION, log=lambda *a: None)
    committed = json.load(open(os.path.join(HERE, "rules", "ibm_heron_r2_2026-11-15.json")))
    assert regen == committed, "rules file is out of sync with data/probe_outcomes*.json + curation (re-run export_rules.py)"
    assert RULES["rules"] == [] and RULES["envelope_file"] and ENV["pass_dims"] and ENV["fail_dims"], "resource verdicts come from the envelope, not thresholds"
    sigs = {c["signature"] for c in RULES["accepted_constructs"]}
    assert {"store:Bool()", "Binary:BIT_XOR:Bool()", "Binary:SHIFT_RIGHT:Uint(8)"} <= sigs
    assert not any(sg.startswith("Cast:") or "SHIFT_LEFT" in sg for sg in sigs), "no accepted probe used a Cast or a left shift"
    h = [x for x in RULES["static_hazards"] if x["id"] == "shift_cast_in_store_6066"][0]
    assert h["evidence"] == ["FP_R12x8", "FP_S24"] and h["error_code"] == 6066 and "ibm_marrakesh 2026-11-15" in h["observed"]
    say(f"rules file valid; regenerated from data == committed; envelope {RULES['envelope_file']} with {len(ENV['probes'])} probes "
        f"({sum(p['class_'] == 'accept' for p in ENV['probes'])} accepted / {sum(p['class_'] == 'reject6057' for p in ENV['probes'])} rejected 6057 / "
        f"{sum(p['class_'] == 'other6066' for p in ENV['probes'])} rejected 6066); {len(sigs)} accepted constructs; latency {RULES['latency']['intercept_ns']:.0f} + "
        f"{RULES['latency']['slope_ns_per_op']:.1f} ns/op")


def test_schema_rejects_bad_rules():
    bad = copy.deepcopy(RULES)
    del bad["latency"]
    assert any("latency" in e for e in R.validate_rules(bad))
    bad = copy.deepcopy(RULES)
    bad["measured_date"] = "15/11/2026"
    assert R.validate_rules(bad)
    bad = copy.deepcopy(RULES)
    bad["static_hazards"][0]["official"] = ["no_such_text"]
    assert any("no_such_text" in e for e in R.validate_rules(bad))
    bad = copy.deepcopy(RULES)
    bad["accepted_constructs"][0]["evidence"] = "x"
    assert R.validate_rules(bad)


def _env_vectors():
    return {x["name"].split(":", 1)[1]: x["vector"] for x in ENV["probes"]}


def test_rebuilt_probe_vectors_equal_the_envelope_file():
    """resources.resource_vector of every probe we can rebuild (suite builders, p1/v1 programs, fix-pack QPY) equals the vector stored in the envelope file."""
    vec = _env_vectors()
    n, skipped = 0, []
    for name, v in vec.items():
        try:
            cs = E.probe_circuits(name)
        except KeyError:
            skipped.append(name)
            continue
        got = RES.resource_vector(cs[0])
        assert got == v, (name, {k: (got[k], v[k]) for k in v if got[k] != v[k]})
        n += 1
    assert n >= 50, n
    say(f"{n} rebuilt probes have the same resource vector as the envelope file ({len(skipped)} not rebuildable here: old-day shapes and others)")


# ------------------------------------------------------------------ tests: probe validation (the key test)
def _verdict_to_pred(st):
    return {"PASS": "accepted", "FAIL": "rejected", "UNCERTAIN": "uncertain"}[st]


def _pred(st):
    return {"PASS": "accepted", "FAIL": "rejected", "UNCERTAIN": "uncertain"}[st]


def test_every_probe_predicts_observed_outcome():
    """All rounds (r1-r3, p1, v1, fix-pack v1, old-day shapes, ...) = every probe of the envelope file. Probes we can rebuild go through the whole
    checker (dccheck.check on the circuit); the others use their stored resource vector. 0 wrong in-sample; no rejected probe is ever PASS."""
    rows, via_check = [], 0
    for x in ENV["probes"]:
        name = x["name"].split(":", 1)[1]
        rnd = x["name"].split(":", 1)[0]
        obs = "accepted" if x["class_"] == "accept" else "rejected"
        try:
            cs = E.probe_circuits(name)
            st = R.worst(D.check(c, RULES, today=TODAY)["status"] for c in cs)
            via_check += 1
        except KeyError:
            v, wit = RES.envelope_verdict(x["vector"], *_split_env(ENV), ENV["pass_dims"], ENV["fail_dims"])
            st = {"CONTRADICTION": "UNCERTAIN"}.get(v, v)
        rows.append((name, obs, _pred(st), rnd, x["class_"]))
    cm = {(o, p): 0 for o in ("accepted", "rejected") for p in ("accepted", "rejected", "uncertain")}
    for _, o, p, _, _ in rows:
        cm[(o, p)] += 1
    say(f"confusion matrix over {len(rows)} probes, all rounds ({via_check} through dccheck.check on the rebuilt circuit, the rest from stored vectors); "
        f"rows observed on QPU, columns dccheck:")
    say("                 pred PASS  pred FAIL  UNCERTAIN")
    for o in ("accepted", "rejected"):
        say(f"obs {o:9s}   {cm[(o, 'accepted')]:8d}  {cm[(o, 'rejected')]:9d}  {cm[(o, 'uncertain')]:9d}")
    for rnd in sorted({r[3] for r in rows}):
        sub = [r for r in rows if r[3] == rnd]
        say(f"  {rnd:4s} n={len(sub):2d}  PASS-ok {sum(1 for r in sub if r[1] == 'accepted' and r[2] == 'accepted'):2d}  "
            f"FAIL-ok {sum(1 for r in sub if r[1] == 'rejected' and r[2] == 'rejected'):2d}  UNCERTAIN {sum(1 for r in sub if r[2] == 'uncertain'):2d}  "
            f"wrong {sum(1 for r in sub if r[2] != 'uncertain' and r[2] != r[1])}")
    wrong = [(n, o, p) for n, o, p, _, _ in rows if p != "uncertain" and p != o]
    assert not wrong, f"wrong predictions: {wrong}"
    assert not [n for n, o, p, _, _ in rows if o == "rejected" and p == "accepted"], "a rejected probe was predicted PASS"
    byname = {n: p for n, o, p, _, _ in rows}
    # the rejected fix-pack v1 circuits (error 6066) are caught by the observed hazard, not by the 6057 envelope
    assert byname["FP_R12x8"] == "rejected" and byname["FP_S24"] == "rejected" and byname["FP_B14x3"] == "accepted"
    assert byname["P1_staged"] == "accepted" and byname["P1_R12x2_a"] == "rejected" and byname["V1_rc4_P10"] == "rejected"
    assert cm[("accepted", "accepted")] + cm[("rejected", "rejected")] >= 50
    # transpiled circuits (FakeKingston + measure_2) give the same verdicts as the logical ones (a sample: transpile cost)
    from qiskit import transpile
    import suite as S
    be, _ = S.get_target("fake")
    sample = ["A96t_K1", "C6x16", "R12x8", "B_R10x8", "S_24split3x8", "P1_staged", "P1_R11x2_a", "P1_R12x2_a", "P1_alloc8", "P1_C8chain", "V1_rc4_P10", "FP_B14x3"]
    mism = []
    for name in sample:
        t = transpile(E.probe_circuits(name)[0], be, optimization_level=1, seed_transpiler=7)
        st = _pred(D.check(t, RULES, today=TODAY)["status"])
        if st != byname[name]:
            mism.append((name, byname[name], st))
    say(f"transpiled (FakeKingston+measure_2) verdict == logical verdict for {len(sample) - len(mism)}/{len(sample)} sampled probes" + (f"; differing: {mism}" if mism else ""))
    assert not mism, mism


def test_every_shipped_probe_circuit_through_full_check():
    """Run the FULL dccheck.check path (envelope + hazards + transpile) on every circuit shipped in data/envelope_probes/ (all QPY files, including the
    29 rebuilt 2026-11-06 circuits). No accepted probe may be FAIL, no rejected probe may be PASS."""
    idx = json.load(open(os.path.join(HERE, "data", "envelope_probes", "index.json")))["probes"]
    assert len(idx) == len(ENV["probes"]) == len(os.listdir(os.path.join(HERE, "data", "envelope_probes"))) - 1
    cm = {(o, v): 0 for o in ("accepted", "rejected") for v in ("PASS", "FAIL", "UNCERTAIN")}
    bad = []
    for x in idx:
        with open(os.path.join(HERE, "data", "envelope_probes", x["qpy"]), "rb") as f:
            circs = qpy.load(f)
        assert len(circs) == 1, x["name"]
        st = D.check(circs[0], RULES, today=TODAY)["status"]
        o = "accepted" if x["class_"] == "accept" else "rejected"
        cm[(o, st)] += 1
        if (o == "accepted" and st == "FAIL") or (o == "rejected" and st == "PASS"):
            bad.append((x["name"], o, st))
    say(f"full check() on all {len(idx)} shipped probe circuits (rows observed on QPU incl. 2 rejected with 6066, columns dccheck):")
    say("                 PASS  FAIL  UNCERTAIN")
    for o in ("accepted", "rejected"):
        say(f"obs {o:9s}   {cm[(o, 'PASS')]:4d}  {cm[(o, 'FAIL')]:4d}  {cm[(o, 'UNCERTAIN')]:9d}")
    assert not bad, bad


def _split_env(env, drop=()):
    acc = [(x["name"], x["vector"]) for x in env["probes"] if x["class_"] == "accept" and x["name"] not in drop]
    rej = [(x["name"], x["vector"]) for x in env["probes"] if x["class_"] == "reject6057" and x["name"] not in drop]
    return acc, rej


def test_leave_one_probe_out():
    """Out-of-sample: remove one probe from the envelope, predict it. The critical number is PASS for a probe the QPU rejected (must be 0)."""
    cm = {(o, v): 0 for o in ("accepted", "rejected") for v in ("PASS", "FAIL", "UNCERTAIN")}
    bad = []
    for x in ENV["probes"]:
        if x["class_"] == "other6066":
            continue
        acc, rej = _split_env(ENV, drop=(x["name"],))
        v, _ = RES.envelope_verdict(x["vector"], acc, rej, ENV["pass_dims"], ENV["fail_dims"])
        v = "UNCERTAIN" if v == "CONTRADICTION" else v
        o = "accepted" if x["class_"] == "accept" else "rejected"
        cm[(o, v)] += 1
        if (o == "rejected" and v == "PASS") or (o == "accepted" and v == "FAIL"):
            bad.append((x["name"], o, v))
    say("leave-one-probe-out (6057 probes only; the probe is removed from the envelope, then predicted):")
    for o in ("accepted", "rejected"):
        say(f"obs {o:9s}   PASS {cm[(o, 'PASS')]:3d}  FAIL {cm[(o, 'FAIL')]:3d}  UNCERTAIN {cm[(o, 'UNCERTAIN')]:3d}")
    say(f"  critical: rejected probe predicted PASS = {cm[('rejected', 'PASS')]};  accepted probe predicted FAIL (wrong FAIL) = {cm[('accepted', 'FAIL')]};  "
        f"accepted probe predicted PASS = {cm[('accepted', 'PASS')]}")
    assert cm[("rejected", "PASS")] == 0, bad
    assert not bad, bad


def test_v1_validation_probes_out_of_sample():
    """v1 (out-of-sample round): with the v1 probes REMOVED from the envelope, V1_rc4_P10 (10 live registers, stack need 4, rejected 6057) must come out
    FAIL or UNCERTAIN, never PASS; R10x8 (10 registers, stack need 2) is the accepted neighbour."""
    drop = tuple(x["name"] for x in ENV["probes"] if x["name"].startswith("v1:"))
    assert len(drop) == 5
    acc, rej = _split_env(ENV, drop=drop)
    res = {}
    for x in ENV["probes"]:
        if x["name"] in drop:
            v, _ = RES.envelope_verdict(x["vector"], acc, rej, ENV["pass_dims"], ENV["fail_dims"])
            res[x["name"].split(":", 1)[1]] = ("UNCERTAIN" if v == "CONTRADICTION" else v, x["class_"])
    say(f"v1 predicted from the envelope WITHOUT v1: {res}")
    assert res["V1_rc4_P10"][0] in ("FAIL", "UNCERTAIN"), res
    assert not [n for n, (v, c) in res.items() if c == "reject6057" and v == "PASS"]
    # and through the whole checker, with the full envelope
    import export_rules as E2
    rep = D.check(E2.probe_circuits("V1_rc4_P10")[0], RULES, today=TODAY)
    assert rep["status"] in ("FAIL", "UNCERTAIN") and rep["vector"]["peak_live_regs"] == 10 and rep["vector"]["lr_stack"] == 4
    ok = D.check(E2.probe_circuits("B_R10x8")[0], RULES, today=TODAY)
    assert ok["vector"]["peak_live_regs"] >= 10 and ok["vector"]["lr_stack"] == 2
    assert "model estimate" not in D.format_report(rep)       # the post-hoc pool model "live registers + stack need" was falsified out of sample (e2): not shown


def test_latency_inside_interval():
    obj = json.load(open(OUTCOMES))
    n = 0
    for lst in obj["rounds"].values():
        for r in lst:
            if r.get("latency_ns") is None:
                continue
            rep = D.check(E.probe_circuits(r["probe"])[0], RULES, today=TODAY)
            lat = rep["ifs"][0]["latency"]
            assert lat["ops"] == r["store_ops"], (r["probe"], lat["ops"], r["store_ops"])
            assert lat["lo_ns"] <= r["latency_ns"] <= lat["hi_ns"], (r["probe"], lat, r["latency_ns"])
            n += 1
    assert n == 5
    a, b = RULES["latency"]["intercept_ns"], RULES["latency"]["slope_ns_per_op"]
    est = R.latency_estimate(RULES["latency"], 40)
    assert abs(est["estimate_ns"] - (a + 40 * b)) < 1e-6 and est["lo_ns"] < est["estimate_ns"] < est["hi_ns"] and not est["extrapolated"]
    assert R.latency_estimate(RULES["latency"], 200)["extrapolated"]
    say(f"latency: observed value inside the (wide) predicted range for {n}/5 stopwatch probes (in-sample)")


# ------------------------------------------------------------------ tests: synthetic circuits
def test_no_if():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.measure([0, 1], [0, 1])
    rep = D.check(qc, RULES, today=TODAY)
    assert rep["n_control_flow"] == 0 and rep["ifs"] == [] and rep["status"] == "NOT_APPLICABLE" and "NOT APPLICABLE" in rep["note"]
    assert "NOT APPLICABLE" in D.format_report(rep)
    pf = D.preflight(qc, RULES, today=TODAY)
    assert pf["status"] == "NOT_APPLICABLE"


def test_several_ifs_different_cones():
    rep = D.check(three_ifs(), RULES, today=TODAY)
    ifs = rep["ifs"]
    assert [len(i["cone_registers"]) for i in ifs] == [3, 11, 12]
    env_st = R.envelope_check(ENV, rep["vector"])["status"]
    assert {i["status"] for i in ifs} == {env_st} and rep["vector"]["cone_regs"] == 12 and rep["vector"]["n_ifs"] == 3      # the envelope judges the whole circuit
    assert ifs[0]["cone_registers"] == ["G0", "G1", "G2"] and len(ifs[2]["cone_registers"]) == 12
    assert rep["status"] == env_st
    bad = D.check(regs_circuit(12, 2), RULES, today=TODAY)["ifs"][0]
    c = [c for c in bad["checks"] if c["rule"] == "envelope"][0]
    assert c["status"] == "FAIL" and c["error_code"] == 6057 and any("ibm_marrakesh 2026-11-15" in x.get("text", "") for x in c["sources"])
    txt = D.format_report(D.check(regs_circuit(12, 2), RULES, today=TODAY))
    assert "measured on ibm_marrakesh 2026-11-15" in txt and "error 6057" in txt


def test_cone_through_split_stores_and_overlap():
    """stores S1(G0..5), S2(G4..9): cone is the DISTINCT register set (10), not the sum of reads (14)."""
    import suite as S
    sp = next(x for x in S.SPLIT if x["name"] == "S_10overlap")
    rep = D.check(S.build_split(sp, "A"), RULES, today=TODAY)
    i = rep["ifs"][0]
    assert i["metrics"]["cone_regs"] == 10 and i["n_stores"] == 3 and i["status"] == "PASS"


def test_nested_if():
    n = 3
    q = QuantumRegister(n + 2, "q")
    regs = [ClassicalRegister(1, f"m{i}") for i in range(n)]
    s = ClassicalRegister(1, "s")
    qc = QuantumCircuit(q, *regs, s, name="nested")
    for i, r in enumerate(regs):
        qc.measure(q[i], r[0])
    qc.store(s[0], expr.bit_xor(expr.lift(regs[1][0]), expr.lift(regs[2][0])))
    with qc.if_test(expr.lift(regs[0][0])):
        with qc.if_test(expr.lift(s[0])):
            qc.x(q[n])
    rep = D.check(qc, RULES, today=TODAY)
    kinds = [(i["level"], len(i["cone_registers"]), i["control_registers"]) for i in rep["ifs"]]
    assert kinds == [(0, 1, []), (1, 2, ["m0"])], kinds
    assert rep["ifs"][1]["status"] == "FAIL"
    nc = [c for c in rep["ifs"][1]["checks"] if c["rule"] == "nested_conditionals"][0]
    assert nc["status"] == "FAIL" and "Nested conditionals are not allowed." in nc["sources"][0]["quote"] and nc["sources"][0]["url"].startswith("https://")
    assert rep["status"] == "FAIL"


def test_vars():
    q = QuantumRegister(3, "q")
    m = ClassicalRegister(2, "m")
    qc = QuantumCircuit(q, m, name="vars")
    qc.measure(q[0], m[0])
    qc.measure(q[1], m[1])
    v = qc.add_var("v", expr.bit_xor(expr.lift(m[0]), expr.lift(m[1])))      # initialiser is an expression of measured bits
    with qc.if_test(v):
        qc.x(q[2])
    rep = D.check(qc, RULES, today=TODAY)
    i = rep["ifs"][0]
    assert i["cone_vars"] == ["v"] and i["metrics"]["cone_regs"] == 1, i
    assert i["status"] == "UNCERTAIN" and any(c["rule"] == "var_in_cone" for c in i["checks"])
    assert rep["n_vars"] == 1
    # a declared Var with a LITERAL initialiser is a documented rejection (6066)
    qc2 = QuantumCircuit(q, m, name="varlit")
    w = qc2.add_var("w", expr.lift(True))
    qc2.measure(q[0], m[0])
    qc2.store(w, expr.lift(m[0]))
    with qc2.if_test(w):
        qc2.x(q[2])
    rep2 = D.check(qc2, RULES, today=TODAY)
    assert rep2["status"] == "FAIL" and any(h["error_code"] == 6066 for h in rep2["hazards"]), rep2["hazards"]


def test_static_hazards():
    q = QuantumRegister(4, "q")
    a = ClassicalRegister(2, "a")
    b = ClassicalRegister(bits=[a[1]] + [ClassicalRegister(1, "t")[0]], name="b")           # shares a[1] with a
    qc = QuantumCircuit(q, a, name="overlap")
    qc.add_register(b)
    qc.measure(q[0], a[0])
    rep = D.check(qc, RULES, today=TODAY)
    assert any(h["error_code"] == 6327 for h in rep["hazards"]) and rep["status"] == "FAIL"
    # copy store (6062)
    m = ClassicalRegister(2, "m")
    L = ClassicalRegister(1, "L")
    qc = QuantumCircuit(q, m, L, name="copy")
    qc.measure(q[0], m[0])
    qc.store(L[0], expr.lift(m[0]))
    with qc.if_test(expr.lift(L[0])):
        qc.x(q[1])
    rep = D.check(qc, RULES, today=TODAY)
    assert any(h["error_code"] == 6062 for h in rep["hazards"])
    assert all(h["status"] == "UNCERTAIN" for h in rep["hazards"] if h["error_code"] == 6062) and rep["status"] != "PASS"    # context dependent: not FAIL


def test_rules_age_and_backend_warning():
    qc = regs_circuit(3, 2)
    rep = D.check(qc, RULES, today=TODAY + dt.timedelta(days=61), backend="ibm_other")
    w = " ".join(rep["warnings"])
    assert "61 days old" in w and "ibm_other" in w
    rep = D.check(qc, RULES, today=TODAY + dt.timedelta(days=61), max_age_days=90)
    assert not any("days old" in x for x in rep["warnings"])
    # ibm_kingston: partially covered (5 register-rule probes), must say so and must not say "not covered" nor claim full coverage
    w = " ".join(D.check(qc, RULES, today=TODAY, backend="ibm_kingston")["warnings"])
    assert "partially" in w and "5 register-rule probes" in w and "not covered" not in w and "still rests on ibm_marrakesh" in w, w
    assert not any("partially" in x for x in D.check(qc, RULES, today=TODAY, backend="ibm_marrakesh")["warnings"])
    # ibm_fez: same partial coverage (5 register-rule probes), executed 2026-11-16, backend_version 1.3.37
    w = " ".join(D.check(qc, RULES, today=TODAY, backend="ibm_fez")["warnings"])
    assert "partially" in w and "5 register-rule probes" in w and "not covered" not in w and "still rests on ibm_marrakesh" in w and "1.3.37" in w, w
    w = " ".join(D.check(qc, RULES, today=TODAY, backend="ibm_aachen")["warnings"])
    assert "ibm_aachen is not covered" in w and "partially" not in w
    assert not D.check(qc, RULES, today=TODAY + dt.timedelta(days=60))["warnings"]
    # backend names are normalised (case, surrounding whitespace) before the coverage lookup
    for spelled in ("IBM_FEZ", " ibm_fez ", "ibm_fez\n", "Ibm_Fez"):
        w = " ".join(D.check(qc, RULES, today=TODAY, backend=spelled)["warnings"])
        assert "partially" in w and "not covered" not in w and "1.3.37" in w, (spelled, w)
    assert not any("partially" in x or "not covered" in x for x in D.check(qc, RULES, today=TODAY, backend=" IBM_Marrakesh ")["warnings"])
    assert D.norm_backend(" IBM_Kingston\t") == "ibm_kingston" and D.norm_backend(None) is None


def test_fixpack_uses_the_backend_and_shows_the_coverage_warning():
    qc = regs_circuit(12, 8, name="fpbackend")
    res = F.fix_pack(qc, RULES, backend="ibm_fez", today=TODAY)
    assert res.changed, res.refused or res.notes
    assert "FakeFez" in res.transpile["target"], res.transpile
    assert any("partially" in w and "ibm_fez" in w for w in res.before["warnings"])
    txt = F.format_fix(res)
    assert "WARNING" in txt and "partially" in txt and "FakeFez" in txt, txt
    res2 = F.fix_pack(qc, RULES, backend=" IBM_KINGSTON ", today=TODAY)
    assert "FakeKingston" in res2.transpile["target"] and "partially" in F.format_fix(res2)
    res3 = F.fix_pack(qc, RULES, backend="ibm_nowhere", today=TODAY)
    assert "FakeMarrakesh" in res3.transpile["target"] and "not covered" in F.format_fix(res3)
    # the CLI text path shows it too
    p = tempfile.mkdtemp()
    qp = os.path.join(p, "c.qpy")
    with open(qp, "wb") as fh:
        qpy.dump(qc, fh)
    out = subprocess.run([PY, os.path.join(HERE, "dccheck.py"), qp, "--fix-pack", "--backend", "ibm_fez", "--today", TODAY.isoformat()], capture_output=True, text=True).stdout
    assert "WARNING" in out and "partially" in out and "FakeFez" in out, out


def test_envelope_semantics_and_explanations():
    """envelope_check on a synthetic envelope: PASS / FAIL / UNCERTAIN / contradiction, each naming its witness; then the real envelope on round-4 shapes."""
    def vec(**k):
        v = {d: 0 for d in RES.DIMENSIONS}
        v["n_ifs"] = 1
        v.update(k)
        return v
    env = {"pass_dims": ["peak_live_regs", "lr_stack"], "fail_dims": ["peak_live_regs", "lr_stack"], "backends_covered": ["be"], "measured_date": "2026-11-15",
           "probes": [{"name": "r1:okA", "class_": "accept", "vector": vec(peak_live_regs=5, lr_stack=2)},
                      {"name": "r1:okB", "class_": "accept", "vector": vec(peak_live_regs=2, lr_stack=6)},
                      {"name": "r1:bad", "class_": "reject6057", "vector": vec(peak_live_regs=8, lr_stack=2)}]}
    ch = lambda a, b: R.envelope_check(env, vec(peak_live_regs=a, lr_stack=b))  # noqa: E731
    r = ch(3, 1)
    assert r["status"] == "PASS" and r["witness"] and "dominated by accepted probe ok" in r["reason"] and "be, measured 2026-11-15" in r["reason"]
    r = ch(9, 3)
    assert r["status"] == "FAIL" and r["witness"] == ["r1:bad"] and "at least as demanding as rejected probe bad" in r["reason"]
    r = ch(6, 2)
    assert r["status"] == "UNCERTAIN" and "exceeds every accepted probe" in r["reason"] and "peak_live_regs" in r["reason"]
    env["probes"].append({"name": "r1:okC", "class_": "accept", "vector": vec(peak_live_regs=9, lr_stack=3)})
    r = ch(8, 2)
    assert r["status"] == "UNCERTAIN" and "contradiction" in r["reason"]
    # real envelope, round-4 shapes through the whole checker: every verdict names a witness probe
    import export_rules as E2
    want = {"P1_staged": "PASS", "P1_R11x2_a": "PASS", "P1_R12x2_a": "FAIL", "P1_split6": "FAIL", "P1_alloc8": "FAIL", "V1_rc4_P10": "FAIL"}
    for n, st in want.items():
        rep = D.check(E2.probe_circuits(n)[0], RULES, today=TODAY)
        c = [x for x in rep["ifs"][-1]["checks"] if x["rule"] == "envelope"][0]
        assert rep["status"] == st and c["status"] == st, (n, rep["status"], c["reason"])
        assert any(w in c["reason"] for w in ("accepted probe", "rejected probe")), c["reason"]
    # a brand-new shape bigger than anything measured: UNCERTAIN, never PASS
    q = QuantumRegister(9, "q")
    X = ClassicalRegister(8, "X")
    out = ClassicalRegister(1, "o")
    qc = QuantumCircuit(q, X, out, name="bigger")
    for k in range(8):
        qc.measure(q[k], X[k])
    lvl = [expr.lift(X[k % 8]) for k in range(4096)]
    while len(lvl) > 1:                                       # balanced tree: 4095 ops, shallow (no recursion problems)
        lvl = [expr.bit_xor(lvl[i], lvl[i + 1]) for i in range(0, len(lvl), 2)]
    qc.store(out[0], lvl[0])
    with qc.if_test(expr.lift(out[0])):
        qc.x(q[8])
    rep = D.check(qc, RULES, today=TODAY)
    c = [x for x in rep["ifs"][0]["checks"] if x["rule"] == "envelope"][0]
    assert rep["status"] == "UNCERTAIN" and c["status"] == "UNCERTAIN" and "cone_ops" in c["reason"], c["reason"]


# ------------------------------------------------------------------ tests: CLI / loading
def test_cli_and_loaders():
    with tempfile.TemporaryDirectory() as tmp:
        bad, good = regs_circuit(12, 2), regs_circuit(4, 2)
        pb, pg = os.path.join(tmp, "bad.qpy"), os.path.join(tmp, "good.qpy")
        for p, c in ((pb, bad), (pg, good)):
            with open(p, "wb") as f:
                qpy.dump(c, f)
        r = subprocess.run([PY, os.path.join(HERE, "dccheck.py"), pb, "--today", str(TODAY)], capture_output=True, text=True)
        assert r.returncode == 1 and "FAIL" in r.stdout and "6057" in r.stdout, r.stdout + r.stderr
        r = subprocess.run([PY, os.path.join(HERE, "dccheck.py"), pg, "--json", "--today", str(TODAY)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        j = json.loads(r.stdout)
        assert j["status"] == "PASS" and j["logical"]["ifs"][0]["metrics"]["cone_regs"] == 4 and j["transpiled"]["status"] == "PASS"
        q = QuantumRegister(3, "q")
        c = ClassicalRegister(2, "c")
        wl = QuantumCircuit(q, c, name="whileloop")
        wl.measure(q[0], c[0])
        with wl.while_loop(expr.lift(c[0])):
            wl.x(q[1])
        pw = os.path.join(tmp, "while.qpy")
        with open(pw, "wb") as f:
            qpy.dump(wl, f)
        r = subprocess.run([PY, os.path.join(HERE, "dccheck.py"), pw, "--today", str(TODAY)], capture_output=True, text=True)
        assert r.returncode == 2 and "UNCERTAIN" in r.stdout, r.stdout + r.stderr
        r = subprocess.run([PY, os.path.join(HERE, "dccheck.py"), "no.such.module:f"], capture_output=True, text=True, cwd=HERE)
        assert r.returncode == 64
        # python API: file.py:func
        mod = os.path.join(tmp, "mycirc.py")
        open(mod, "w").write("from dccheck_examples import ten_registers\ndef build():\n    return ten_registers()\n")
        circs = D.load_circuits(mod + ":build")
        assert len(circs) == 1 and D.check(circs[0], RULES, today=TODAY)["status"] == "PASS"      # 10 registers: accepted 4/4 in round 4
        # --transpiled pair
        r = subprocess.run([PY, os.path.join(HERE, "dccheck.py"), pg, "--transpiled", pg, "--today", str(TODAY)], capture_output=True, text=True)
        assert r.returncode == 0 and "PREFLIGHT" in r.stdout and "monotone resource demand" in r.stdout, r.stdout + r.stderr


# ------------------------------------------------------------------ tests: fix-pack
def _fixpack_case(name, qc, after=("PASS",)):
    res = F.fix_pack(qc, RULES, today=TODAY)
    assert not res.refused, f"{name}: {res.refused}"
    assert res.changed and res.before["status"] == "FAIL" and res.after["status"] in after, (name, res.before["status"], res.after["status"])
    assert res.equivalence["equal"] and res.equivalence["n_patterns"] >= 100 and res.transpile["ok"]
    # independent re-verification of the returned circuit, with different random seeds
    eq = F.prove_equivalent(qc, res.circuit, n_random=300, seed=99)
    assert eq["equal"], (name, eq)
    # whitelist: every construct that the rewrite emitted (and the original did not use) is backed by an accepted QPU probe, and no hazard fires
    covered = {c["signature"]: c["evidence"] for c in RULES["accepted_constructs"]}
    emitted = D.construct_signatures(res.circuit) - D.construct_signatures(qc)
    assert emitted <= set(covered), (name, sorted(emitted - set(covered)))
    sig = D.construct_signatures(res.circuit)
    assert not [x for x in sig if x.startswith("Cast:") or "SHIFT" in x], sig               # v2 never emits shifts or casts
    rep = D.check(res.circuit, RULES, today=TODAY)
    assert rep["hazards"] == [], rep["hazards"]
    buf = io.BytesIO()
    qpy.dump(res.circuit, buf)
    buf.seek(0)
    back = qpy.load(buf)[0]
    assert RES.resource_vector(back)["peak_live_regs"] < RES.resource_vector(qc)["peak_live_regs"]
    v0, v1 = RES.resource_vector(qc), RES.resource_vector(res.circuit)
    say(f"fix-pack v2 {name}: {res.before['status']} -> {res.after['status']}; peak live registers {v0['peak_live_regs']} -> {v1['peak_live_regs']}, "
        f"stack need {v0['lr_stack']} -> {v1['lr_stack']}, cone ops {v0['cone_ops']} -> {v1['cone_ops']}; emitted constructs {sorted(emitted)} (evidence "
        f"{sorted({covered[c][0] for c in emitted})}); equivalence {res.equivalence['mode']} over {res.equivalence['n_patterns']} patterns / {res.equivalence['n_events']} events")
    return res


def test_fixpack_whole_register_xor():
    _fixpack_case("12x2 whole-register XOR (plain measure)", regs_circuit(12, 2))
    res = _fixpack_case("12x8 whole-register XOR (plain measure)", regs_circuit(12, 8))
    assert [g["width"] for g in res.groups] == [32, 32, 32]


def test_fixpack_suite_failing_probe_measure2():
    """the suite's own rejected probes (6057 on the QPU), with measure_2: FAIL before; after the v2 remap the envelope says PASS or UNCERTAIN, never FAIL."""
    for nm, after in (("B_R12x8c", ("PASS",)), ("S_12split6", ("PASS",)), ("S_24split3x8", ("PASS", "UNCERTAIN")), ("B_R12x2", ("PASS",))):
        _fixpack_case(f"{nm} (suite probe, QPU-observed 6057)", E.probe_circuits(nm)[0], after=after)


def test_fixpack_bit_level_uses():
    """Index / clbit-level reads only (no whole-register reads): each Index becomes a plain clbit; stores untouched."""
    qc = regs_circuit(14, 3, use_index=True, name="bitlevel14")
    res = _fixpack_case("14x3 bit-level (clbit) reads", qc)
    assert res.after["ifs"][0]["metrics"]["max_store_ops"] == res.before["ifs"][0]["metrics"]["max_store_ops"]


def test_fixpack_v2_emits_what_the_accepted_14x3_probe_used():
    """the accepted v1 probe FP_B14x3 and the v2 whole-register circuits use the same construct set: Bool XOR of single clbits stored into a clbit."""
    acc = D.construct_signatures(E.probe_circuits("FP_B14x3")[0])
    res = F.fix_pack(regs_circuit(12, 8), RULES, today=TODAY)
    assert D.construct_signatures(res.circuit) <= acc | {"store:Uint(8)"} or D.construct_signatures(res.circuit) <= acc, D.construct_signatures(res.circuit)
    rej = D.construct_signatures(E.probe_circuits("FP_R12x8")[0])
    assert any(x.startswith("Cast:") for x in rej) and not any(x.startswith("Cast:") for x in D.construct_signatures(res.circuit))


def test_fixpack_rigid_register_stays_and_nested_stores_are_refused():
    # a measured register that is ALSO read as a whole by a compare cannot be bit-expanded: it stays a register, the others are packed
    n, w = 13, 2
    q = QuantumRegister(n * w + 2, "q")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]
    S = ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, *regs, S, name="rigidcmp")
    for i, r in enumerate(regs):
        for j in range(w):
            qc.measure(q[i * w + j], r[j])
    e = expr.bit_xor(regs[1], regs[2])
    for r in regs[3:]:
        e = expr.bit_xor(e, r)
    qc.store(S, e)
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[n * w])
    with qc.if_test(expr.greater(regs[0], expr.lift(1, types.Uint(w)))):
        qc.x(q[n * w + 1])
    res = F.fix_pack(qc, RULES, today=TODAY)
    # G0 stays a register (read whole by a compare); the packed result would still carry that compare, which no accepted probe used -> refused, naming the carried construct
    assert res.refused and "Binary:GREATER:Uint(2)" in res.refused and "already in the input" in res.refused and res.circuit is None, res.refused
    # whole-register stores inside a control-flow body are refused (cannot be expanded safely)
    regs = [ClassicalRegister(2, f"G{i}") for i in range(12)]
    sel = ClassicalRegister(1, "sel")
    T = ClassicalRegister(2, "T")
    q = QuantumRegister(26, "q")
    qc = QuantumCircuit(q, *regs, sel, T, name="nestedstore")
    for i, r in enumerate(regs):
        qc.measure(q[2 * i], r[0])
    qc.measure(q[24], sel[0])
    with qc.if_test(expr.lift(sel[0])):
        e = expr.bit_xor(regs[0], regs[1])
        for r in regs[2:]:
            e = expr.bit_xor(e, r)
        qc.store(T, e)
    with qc.if_test(expr.lift(T[1])):
        qc.x(q[25])
    r2 = F.fix_pack(qc, RULES, today=TODAY)
    assert r2.refused and r2.circuit is None


def test_fixpack_unchanged_and_refusals():
    ok = F.fix_pack(regs_circuit(6, 2), RULES, today=TODAY)
    assert not ok.refused and not ok.changed and ok.circuit is not None and "nothing to pack" in ok.notes[0]
    q = QuantumRegister(26, "q")
    regs = [ClassicalRegister(2, f"G{i}") for i in range(12)]
    # Var present
    qc = QuantumCircuit(q, *regs, name="withvar")
    v = qc.add_var("v", expr.lift(False))
    for i, r in enumerate(regs):
        qc.measure(q[2 * i], r[0])
    e = expr.bit_xor(regs[0], regs[1])
    for r in regs[2:]:
        e = expr.bit_xor(e, r)
    qc.store(v, expr.index(e, 0))
    with qc.if_test(v):
        qc.x(q[25])
    res = F.fix_pack(qc, RULES, today=TODAY)
    assert res.refused and "Vars" in res.refused and res.circuit is None, res.refused
    # whole-register value consumed by a shift / add / compare / cast: cannot be remapped without a construct no accepted probe used -> refused
    for label, mk in (("shift", lambda e: expr.shift_right(e, 1)), ("add", lambda e: expr.add(e, e)),
                      ("cast", lambda e: expr.cast(e, types.Uint(1)) if False else expr.bit_and(expr.cast(e, types.Uint(4)), expr.lift(1, types.Uint(4))))):
        regs = [ClassicalRegister(2, f"G{i}") for i in range(12)]
        S = ClassicalRegister(2 if label != "cast" else 4, "S")
        qc = QuantumCircuit(q, *regs, S, name=f"consumer_{label}")
        for i, r in enumerate(regs):
            qc.measure(q[2 * i], r[0])
        e = expr.bit_xor(regs[0], regs[1])
        for r in regs[2:]:
            e = expr.bit_xor(e, r)
        qc.store(S, mk(e))
        with qc.if_test(expr.lift(S[0])):
            qc.x(q[25])
        res = F.fix_pack(qc, RULES, today=TODAY)
        assert res.refused and res.circuit is None, (label, res.refused)
    # every register is a store target (rigid): nothing mergeable
    regs = [ClassicalRegister(2, f"G{i}") for i in range(12)]
    S = ClassicalRegister(2, "S")
    qc = QuantumCircuit(q, *regs, S, name="allrigid")
    for i, r in enumerate(regs):
        qc.measure(q[2 * i], r[0])
        qc.store(r, expr.bit_xor(r, r))
    e = expr.bit_xor(regs[0], regs[1])
    for r in regs[2:]:
        e = expr.bit_xor(e, r)
    qc.store(S, e)
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[25])
    res = F.fix_pack(qc, RULES, today=TODAY)
    assert res.refused and res.circuit is None, res.refused
    # switch_case
    qs = QuantumRegister(14, "q")
    regs = [ClassicalRegister(2, f"G{i}") for i in range(12)]
    sw = ClassicalRegister(2, "sw")
    qc = QuantumCircuit(qs, *regs, sw, name="switch")
    for i, r in enumerate(regs):
        qc.measure(qs[i], r[0])
    qc.measure(qs[12], sw[0])
    S = ClassicalRegister(1, "S")
    qc.add_register(S)
    e = expr.bit_xor(expr.lift(regs[0][0]), expr.lift(regs[1][0]))
    for r in regs[2:]:
        e = expr.bit_xor(e, expr.lift(r[0]))
    qc.store(S[0], e)
    with qc.switch(sw) as case:
        with case(0):
            with qc.if_test(expr.lift(S[0])):
                qc.x(qs[13])
        with case(case.DEFAULT):
            qc.x(qs[13])
    res = F.fix_pack(qc, RULES, today=TODAY)
    assert res.refused and res.circuit is None, res.refused
    say(f"fix-pack refusals: Vars / shift-add-cast consumers / all-rigid / switch -> e.g. {res.refused!r}")


def test_equivalence_checker_has_teeth():
    a = regs_circuit(12, 2, nreads=12)
    b = regs_circuit(12, 2, nreads=11)                        # a different function of the inputs
    assert not F.prove_equivalent(a, b)["equal"]
    res = F.fix_pack(a, RULES, today=TODAY)
    broken = res.circuit.copy_empty_like()
    swapped = 0
    for inst in res.circuit.data:
        op = inst.operation
        if op.name == "store" and not swapped and isinstance(op.rvalue, expr.Binary):
            broken.store(op.lvalue, expr.Binary(expr.Binary.Op.BIT_OR, op.rvalue.left, op.rvalue.right, op.rvalue.type))     # XOR -> OR
            swapped += 1
        elif op.name == "store":
            broken.store(op.lvalue, op.rvalue)
        else:
            broken.append(op, inst.qubits, inst.clbits)
    assert swapped and not F.prove_equivalent(a, broken)["equal"], "a sabotaged rewrite passed the equivalence check"
    # unread bits of a store target are NOT compared (stored values are not returned in the job result)
    assert F.prove_equivalent(a, res.circuit, n_random=200)["equal"]


def test_evaluator_vs_aer():
    """validate the local evaluator itself against qiskit-aer on the ORIGINAL (shift-free) circuit, deterministic inputs."""
    try:
        from qiskit_aer import AerSimulator
    except Exception:  # noqa: BLE001
        say("aer not available: evaluator-vs-aer check skipped")
        return
    import suite as S
    sim = AerSimulator(method="stabilizer")          # X + measure only: Clifford, so 33 qubits are cheap
    n = 0
    for nr, w, nread, which in ((4, 8, 4, "A"), (4, 8, 4, "B"), (6, 2, 6, "A"), (12, 2, 12, "A"), (12, 2, 12, "B")):
        qc = S.build_regs_det(nr, w, nread, which, ibm=False)
        counts = sim.run(qc, shots=8).result().get_counts()
        assert len(counts) == 1                                          # deterministic by construction
        key = next(iter(counts)).replace(" ", "")
        bits_msb_first = [int(ch) for ch in key]
        aer_vals = bits_msb_first[::-1]                                  # index = clbit index
        meas_vals = {qc.find_bit(c).index: aer_vals[qc.find_bit(c).index] for c in qc.clbits}
        order = []
        for inst in qc.data:
            if inst.operation.name == "measure":
                order.append(meas_vals[qc.find_bit(inst.clbits[0]).index])
        sm = F.simulate(qc, lambda i, t, order=order: order[i])
        want = tuple(aer_vals[qc.find_bit(c).index] for c in qc.clbits)
        assert sm.final() == want, (nr, w, which)
        n += 1
    say(f"local evaluator agrees with qiskit-aer (final clbits incl. store/if results) on {n} deterministic suite circuits")


# ------------------------------------------------------------------ tests: official rules, uncertain list, info, traceability
def _ck(rep, i=0):
    return {c["rule"]: c for c in rep["ifs"][i]["checks"]}


def test_official_hard_rules_fail_with_quote():
    q = QuantumRegister(4, "q")
    m = ClassicalRegister(2, "m")
    big = ClassicalRegister(40, "big")
    o = ClassicalRegister(1, "o")
    qc = QuantumCircuit(q, m, big, o, name="official")
    qc.measure(q[0], m[0])
    qc.measure(q[1], big[0])
    with qc.if_test(expr.lift(m[0])):
        qc.x(q[2])
        qc.measure(q[3], o[0])                                       # measurement inside a conditional
    with qc.if_test(expr.equal(big, 5)):                              # 40-bit operand
        qc.x(q[2])
    rep = D.check(qc, RULES, today=TODAY)
    assert rep["status"] == "FAIL"
    h = [x for x in rep["hazards"] if x["rule"] == "measure_in_conditional"]
    assert h and h[0]["status"] == "FAIL" and h[0]["sources"][0]["quote"] == "Having reset or measurements inside conditionals is not supported."
    c = _ck(rep, 1)["operand_over_32_bits"]
    assert c["status"] == "FAIL" and c["sources"][0]["quote"].startswith("The operand used in an if_test statement must be 32 or fewer bits")
    assert rep["ifs"][1]["operand_bits"] == 40 and rep["ifs"][0]["operand_bits"] == 1
    # a single bit of a wide register, and a 32-bit register, are fine
    qc2 = QuantumCircuit(q, big, ClassicalRegister(32, "w32"), name="bitofwide")
    qc2.measure(q[0], big[3])
    with qc2.if_test(expr.lift(big[3])):
        qc2.x(q[1])
    assert "operand_over_32_bits" not in _ck(D.check(qc2, RULES, today=TODAY))


def test_control_flow_kinds_are_uncertain_with_both_quotes():
    q = QuantumRegister(3, "q")
    c = ClassicalRegister(2, "c")
    qc = QuantumCircuit(q, c, name="flows")
    qc.measure(q[0], c[0])
    with qc.while_loop(expr.lift(c[0])):
        qc.x(q[1])
    with qc.for_loop(range(2)):
        qc.x(q[1])
    with qc.switch(c) as case:
        with case(0):
            qc.x(q[2])
        with case(case.DEFAULT):
            qc.x(q[1])
    rep = D.check(qc, RULES, today=TODAY)
    assert [i["kind"] for i in rep["ifs"]] == ["while_loop", "for_loop", "switch_case"]
    for i in rep["ifs"]:
        assert i["status"] == "UNCERTAIN", i["kind"]
        keys = {s["key"] for s in i["checks"][0]["sources"] if s["kind"] == "official"} if i["checks"] else set()
        u = [x for x in i["checks"] if x["rule"] == "non_if_control_flow"][0]
        assert {s["key"] for s in u["sources"]} == {"flow_only_if", "flow_guide"}
        assert not any(x["status"] == "FAIL" for x in i["checks"])


def test_pressure_detectors_stored_literals_and_arithmetic_note():
    # wide literals -> UNCERTAIN (never PASS)
    q = QuantumRegister(18, "q")
    X = ClassicalRegister(16, "X")
    outs = [ClassicalRegister(16, f"T{i}") for i in range(5)]
    o = ClassicalRegister(1, "o")
    qc = QuantumCircuit(q, X, *outs, o, name="pressure")
    for k in range(16):
        qc.measure(q[k], X[k])
    for t in outs:
        qc.store(t, expr.bit_and(X, expr.lift(0xFFFF, types.Uint(16))))
    e = expr.bit_xor(outs[0], outs[1])
    for t in outs[2:]:
        e = expr.bit_xor(e, t)
    qc.store(outs[0], e)
    with qc.if_test(expr.lift(outs[0][0])):
        qc.x(q[17])
    rep = D.check(qc, RULES, today=TODAY)
    ck = _ck(rep)
    assert rep["ifs"][0]["metrics"]["stored_count"] == 5 and rep["ifs"][0]["max_literal_width"] == 16
    assert ck["wide_literals"]["status"] == "UNCERTAIN" and rep["status"] != "PASS"
    # arithmetic: informational note carrying both IBM statements, no verdict change
    import export_rules as E2
    rep = D.check(E2.probe_circuits("POP8")[0], RULES, today=TODAY)
    n = [x for x in rep["ifs"][0]["notes"] if x["id"] == "arithmetic_conflict"]
    assert n and {s["key"] for s in n[0]["sources"] if s["kind"] == "official"} == {"arith_unsupported", "arith_partial"}
    assert rep["status"] == "PASS" and any(s["kind"] == "measured" and "POP8" in s["probes"] for s in n[0]["sources"])


def test_broadcast_counting_and_job_level():
    q = QuantumRegister(4, "q")
    m = ClassicalRegister(3, "m")
    qc = QuantumCircuit(q, m, name="bc")
    qc.measure(q[0], m[0])
    qc.measure(q[1], m[1])
    for _ in range(2):                                                # same unchanged bit twice = one broadcast
        with qc.if_test(expr.lift(m[0])):
            qc.x(q[2])
    with qc.if_test(expr.lift(m[1])):                                 # a different bit
        qc.x(q[2])
    qc.measure(q[0], m[0])                                            # value changes -> new broadcast
    with qc.if_test(expr.lift(m[0])):
        qc.x(q[3])
    rep = D.check(qc, RULES, today=TODAY)
    b = rep["broadcast"]
    assert (b["n_broadcasts"], b["total_bits"]) == (3, 3) and not b["above_guidance"] and b["approx_budget"] == 2400
    assert b["sources"][0]["quote"].startswith("Currently, if you broadcast 60 bits")
    job = D.check_job([qc, qc], RULES, today=TODAY)
    assert job["broadcast"]["n_broadcasts"] == 6
    assert D.broadcast_info(RULES, 5000, 5000)["above_guidance"] and not D.broadcast_info(RULES, 100, 6000)["above_guidance"]
    assert "ABOVE guidance (informational" in D._fmt_broadcast(D.broadcast_info(RULES, 5000, 5000), "job")


def test_latency_is_a_wide_range_not_a_law():
    rg = RULES["latency"]["ranges"]
    for ops in (15, 40, 63, 5, 120):
        e = R.latency_estimate(RULES["latency"], ops)
        lo = max(rg["intercept_ns"][0] + rg["slope_ns_per_op"][0] * ops - rg["point_error_ns"][0], 0)
        hi = rg["intercept_ns"][1] + rg["slope_ns_per_op"][1] * ops + rg["point_error_ns"][1]
        assert abs(e["lo_ns"] - lo) < 1e-6 and abs(e["hi_ns"] - hi) < 1e-6 and e["lo_ns"] <= e["estimate_ns"] <= e["hi_ns"]
        assert e["extrapolated"] == (ops < 15 or ops > 63)
    assert (rg["intercept_ns"], rg["slope_ns_per_op"], rg["point_error_ns"]) == ([0, 400], [25, 30], [90, 230])
    # every stopwatch point lies inside the band
    for pt in RULES["latency"]["points"]:
        e = R.latency_estimate(RULES["latency"], pt["ops"])
        assert e["lo_ns"] <= pt["latency_ns"] <= e["hi_ns"], pt
    txt = D.format_report(D.check(regs_circuit(12, 2), RULES, today=TODAY))
    assert "EXTRAPOLATION" in txt and "not a statistical interval" in txt and "intercept unresolved" in txt


def test_failing_output_quotes_official_text_and_labels_empirical():
    txt = D.format_report(D.check(regs_circuit(12, 2), RULES, today=TODAY))
    assert "The job requires more classical registers than the control hardware provides." in txt
    assert "reduce the number of classical variables and measurement results used in subsequent conditions" in txt
    assert "https://quantum.cloud.ibm.com/docs/en/errors" in txt
    assert "measured empirically on ibm_marrakesh 2026-11-15, not published by IBM" in txt or "not published by IBM" in txt
    assert "peak live registers" in txt and "stack need" in txt and "measured register(s)" in txt and "rejected probe" in txt


def _walk_sources(rep):
    """every (message-id, sources) pair a report prints"""
    out = []
    for h in rep["hazards"]:
        out.append((h["rule"], h["sources"]))
    for i in rep["ifs"]:
        for c in i["checks"]:
            out.append((c["rule"], c["sources"]))
        for n in i["notes"]:
            out.append((n["id"], n["sources"]))
        out.append(("latency", i["latency"]["sources"]))
    out.append(("broadcast", rep["broadcast"]["sources"]))
    return out


def test_every_message_is_traceable():
    """Every check / hazard / note / latency / broadcast line carries a source: an official quote with URL, or a measured probe
    (or, for earlier-day rejections, a named reference). No bare assertions."""
    import suite as S
    circs = [regs_circuit(12, 2), regs_circuit(9, 2), regs_circuit(4, 2), three_ifs(), E.probe_circuits("POP8")[0],
             E.probe_circuits("S_24split3x8")[0], E.probe_circuits("A96t_K1")[0], E.probe_circuits("C6x16")[0]]
    q = QuantumRegister(4, "q")
    m = ClassicalRegister(2, "m")
    big = ClassicalRegister(40, "big")
    qc = QuantumCircuit(q, m, big, name="mix")
    w = qc.add_var("w", expr.lift(True))
    qc.measure(q[0], m[0])
    qc.store(w, expr.lift(m[0]))
    qc.store(m[1], expr.lift(m[0]))
    with qc.if_test(w):
        with qc.if_test(expr.equal(big, 1)):
            qc.measure(q[1], m[0])
    with qc.while_loop(expr.lift(m[0])):
        qc.x(q[1])
    circs.append(qc)
    seen_kinds = set()
    n_items = 0
    for c in circs:
        rep = D.check(c, RULES, today=TODAY)
        for name, srcs in _walk_sources(rep):
            n_items += 1
            assert srcs, f"{c.name}: message {name!r} has no source"
            for s in srcs:
                seen_kinds.add(s["kind"])
                if s["kind"] == "official":
                    assert s["quote"] and s["url"].startswith("https://quantum.cloud.ibm.com/docs/"), (name, s)
                    assert s["quote"] == RULES["official_texts"][s["key"]]["quote"]
                elif s["kind"] == "measured":
                    assert s["probes"] or s.get("reference"), (name, s)
                elif s["kind"] == "measured_earlier":
                    assert s["reference"], (name, s)
                else:
                    raise AssertionError((name, s))
    assert {"official", "measured", "measured_earlier"} <= seen_kinds, seen_kinds
    say(f"traceability: {n_items} printed items over {len(circs)} circuits, every one carries an official quote+URL or a measured probe/reference")


# ------------------------------------------------------------------ tests: exporter on a manifest
def test_export_rules_from_manifest():
    import shutil
    import suite as S
    import test_suite as TS
    tmp = tempfile.mkdtemp()
    try:
        ad = TS.FakeAdapter(fail=("R12x8",))
        pl, _ = S.submit(ad, "minimal", execute=False, outdir=tmp, quiet=True)
        _, mp = S.submit(ad, "minimal", execute=True, confirm=pl["hash"], outdir=tmp, quiet=True)
        S.fetch_jobs(ad, mp, outdir=tmp)
        rules = E.build_rules([mp], CURATION, log=lambda *a: None)
        assert R.validate_rules(rules) == []
        assert rules["backends"] == ["fake_marrakesh"] and len(rules["latency"]["points"]) == 5 and rules["rules"] == []
        assert {"store:Bool()", "Binary:BIT_XOR:Bool()"} <= {c["signature"] for c in rules["accepted_constructs"]}
        out = os.path.join(tmp, "new_rules.json")
        json.dump(rules, open(out, "w"))
        loaded = R.load_rules(out)
        D.check(regs_circuit(7, 2), loaded, today=dt.date.fromisoformat(loaded["measured_date"]))
        r = subprocess.run([PY, os.path.join(HERE, "suite.py"), "--export-rules", mp, "--curation", CURATION, "--rules-out", out + "2"],
                           capture_output=True, text=True, cwd=HERE)
        assert r.returncode == 0 and os.path.exists(out + "2"), r.stdout + r.stderr
        say(f"suite.py --export-rules on a (fake-adapter) manifest builds a valid rules file: {len(rules['accepted_constructs'])} accepted constructs from "
            f"{rules['generated_by']['n_probes']} probes, latency refit, envelope file reused")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ tests: fix-pack QPU verification plan (never touches IBM)
def test_packed_operand_over_32_bits_is_flagged_and_refused():
    """IBM: the if operand must be <= 32 bits. v2 packs into <= 32-bit registers and never feeds a wide register to an if, but a hand-written 48-bit
    operand is flagged FAIL with IBM's quote, and a whole-register condition is not repacked (it stays a rigid register)."""
    big = ClassicalRegister(48, "pk")
    q2 = QuantumRegister(2, "q")
    qc2 = QuantumCircuit(q2, big, name="manual48")
    qc2.measure(q2[0], big[0])
    with qc2.if_test(expr.equal(expr.bit_and(big, big), 3)):
        qc2.x(q2[1])
    c = [x for x in D.check(qc2, RULES, today=TODAY)["ifs"][0]["checks"] if x["rule"] == "operand_over_32_bits"][0]
    assert c["status"] == "FAIL" and c["sources"][0]["quote"].startswith("The operand used in an if_test")
    res = F.fix_pack(regs_circuit(12, 8), RULES, today=TODAY, group_width=48)       # an explicit 48-bit request is still packed bit-level
    assert not res.refused and all(g["width"] <= 48 for g in res.groups)
    res = F.fix_pack(regs_circuit(12, 8), RULES, today=TODAY)
    assert all(g["width"] <= 32 for g in res.groups)


def test_fixpack_qpu_plan_guards_and_fetch():
    import shutil
    import types as _t
    import fixpack_qpu as FQ
    import suite as S

    class Res:
        def __init__(self, **regs):
            self.data = _t.SimpleNamespace(**{k: _t.SimpleNamespace(to_bool_array=(lambda order="little", v=v: v)) for k, v in regs.items()})

    class Job:
        def __init__(self, names, shots, bad=False):
            self.names, self.shots, self.bad = names, shots, bad

        def status(self):
            return _t.SimpleNamespace(name="ERROR" if self.bad else "DONE")

        def error_message(self):
            return "Error code 6057; The job requires more classical registers than the control hardware provides."

        def result(self):
            rng = np.random.default_rng(3)
            return [Res(out=(rng.random((self.shots, 1)) < (0.08 if nm.endswith("_all") else 0.9))) for nm in self.names]

    class Ad:
        name = "fake_be"

        def __init__(self, remaining=85.0, bad=()):
            self.be, _ = S.get_target("fake")
            self.remaining, self.calls, self.meta, self.bad = remaining, [], {}, set(bad)

        def backend(self):
            return self.be

        def remaining_s(self):
            return self.remaining, 600.0, "2026-12-01T00:00:00Z"

        def run(self, isas, shots):
            jid = f"fp{len(self.calls):03d}"
            self.calls.append(jid)
            self.meta[jid] = ([c.name for c in isas], shots)
            return jid

        def get(self, jid):
            names, shots = self.meta[jid]
            return Job(names, shots, bad=names[0].split("_")[1] in self.bad)
    tmp = tempfile.mkdtemp()
    try:
        ad = Ad()
        pl, mp = FQ.submit(ad, RULES, execute=False, outdir=tmp, quiet=True)
        assert mp is None and ad.calls == [] and len(pl["jobs"]) == 3 and pl["est_total_s"] <= 10 and all(len(j["isas"]) == 3 for j in pl["jobs"])
        assert [j["name"] for j in pl["jobs"]] == ["FP2_R12x8", "FP2_S24", "FP2_R12x2"]            # priority order
        assert all(max(g["width"] for g in j["groups"]) <= 32 for j in pl["jobs"])
        assert FQ.build_plan(ad, RULES)["hash"] == pl["hash"]                                     # deterministic
        for bad in (None, "deadbeef0000"):
            try:
                FQ.submit(ad, RULES, execute=True, confirm=bad, outdir=tmp, quiet=True)
                raise AssertionError("submitted with a bad confirm")
            except SystemExit as e:
                assert "REFUSED" in str(e)
        try:
            FQ.submit(Ad(remaining=8.0), RULES, execute=True, confirm=pl["hash"], outdir=tmp, quiet=True)
            raise AssertionError("quota guard failed")
        except SystemExit as e:
            assert "REFUSED" in str(e) and "remaining" in str(e)
        assert ad.calls == []
        _, mp = FQ.submit(ad, RULES, execute=True, confirm=pl["hash"], outdir=tmp, quiet=True)
        assert len(ad.calls) == 3 and os.path.exists(mp) and os.path.basename(mp) == f"manifest_{pl['hash']}.json"
        try:                                                                                       # duplicate-manifest guard
            FQ.submit(ad, RULES, execute=True, confirm=pl["hash"], outdir=tmp, quiet=True)
            raise AssertionError("duplicate submit not refused")
        except SystemExit as e:
            assert "already exists" in str(e) and "--resubmit" in str(e)
        assert len(ad.calls) == 3
        FQ.submit(ad, RULES, execute=True, confirm=pl["hash"], resubmit=True, outdir=tmp, quiet=True)
        assert len(ad.calls) == 6
        man, verd = FQ.fetch(ad, mp, outdir=tmp)
        assert all("computes correctly" in v for v in verd.values()), verd
        assert os.path.exists(os.path.join(tmp, "RESULTS_QPU_fixpack.md"))
        ad.bad = {"S24"}
        man, verd = FQ.fetch(ad, mp, outdir=tmp)
        assert verd["FP2_S24"].startswith("ERROR 6057") and "computes correctly" in verd["FP2_R12x8"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fixpack_candidates_written_and_valid():
    import fixpack_qpu as FQ
    idx = json.load(open(os.path.join(FQ.CAND_DIR, "index.json")))["candidates"]
    assert len(idx) == 9 and {c["pattern"] for c in idx} == {"last", "mid", "all"}
    for c in idx:
        with open(os.path.join(FQ.CAND_DIR, c["file"]), "rb") as f:
            qc = qpy.load(f)[0]
        rep = D.check(qc, RULES, today=TODAY)
        assert max(i["metrics"]["cone_regs"] for i in rep["ifs"]) <= 8 and c["max_register_bits"] <= 32
        assert not any(i["status"] == "FAIL" for i in rep["ifs"]) and rep["hazards"] == []
        assert c["expected_out"] == (0 if c["pattern"] == "all" else 1)


# ------------------------------------------------------------------ tests: leave-one-out per rule
def test_leave_one_out_per_rule():
    """leave-one-probe-out per failure-dimension set: which single resources / minimal combinations predict the rejections out of sample (PASS side uses all dimensions)."""
    sets = [("peak_live_regs",), ("lr_stack",), ("cone_ops",), ("ershov",), ("cone_regs",)] + [tuple(m) for m in ENV["minimal_dimension_sets"]] + [tuple(ENV["fail_dims"])]
    say("leave-one-probe-out per failure-dimension set (6057 probes; wrong FAIL = accepted probe predicted FAIL):")
    say(f"{'fail dims':58s} {'rej->FAIL':>9s} {'rej->UNC':>8s} {'rej->PASS':>9s} {'acc->FAIL':>9s} {'acc->PASS':>9s} {'acc->UNC':>8s}")
    for fd in sets:
        c = {k: 0 for k in ("rf", "ru", "rp", "af", "ap", "au")}
        for x in ENV["probes"]:
            if x["class_"] == "other6066":
                continue
            acc, rej = _split_env(ENV, drop=(x["name"],))
            v, _ = RES.envelope_verdict(x["vector"], acc, rej, ENV["pass_dims"], list(fd))
            v = "UNCERTAIN" if v == "CONTRADICTION" else v
            key = ("r" if x["class_"] == "reject6057" else "a") + {"FAIL": "f", "UNCERTAIN": "u", "PASS": "p"}[v]
            c[key] += 1
        say(f"{'+'.join(fd):58s} {c['rf']:9d} {c['ru']:8d} {c['rp']:9d} {c['af']:9d} {c['ap']:9d} {c['au']:8d}")
        if tuple(fd) == tuple(ENV["fail_dims"]):
            assert c["rp"] == 0 and c["af"] == 0


def test_analyzer_and_resources_agree_on_the_cone():
    """dccheck's dataflow Analyzer and resources.resource_vector must report the same cone for tuple, expression and Var conditions (blocker B1 class)."""
    import export_rules as E2
    from qiskit.circuit import IfElseOp

    def with_cond(qc, cond):
        out = qc.copy_empty_like()
        for ins in qc.data:
            op = ins.operation
            if op.name == "if_else":
                out.append(IfElseOp(cond, op.blocks[0], None), ins.qubits, ins.clbits)
            else:
                out.append(op, ins.qubits, ins.clbits)
        return out
    for nm in ("P1_ir104", "P1_C8bal", "V1_rc4_P10", "B_R12x2", "P1_alloc8"):
        qc = E2.probe_circuits(nm)[0]
        last = [i.operation for i in qc.data if i.operation.name == "store"][-1].lvalue
        reg = last.var if isinstance(last.var, ClassicalRegister) else qc.find_bit(last.var).registers[0][0]
        conds = {"as built": None, "tuple": (reg, 1), "equal": expr.equal(reg, 1), "index": expr.index(reg, 0)}
        base = None
        for lab, c in conds.items():
            q2 = qc if c is None else with_cond(qc, c)
            an = D.analyze(q2)
            own = max(sum(e["store_ops"]) for e in an.entries)
            vec = an.vector["cone_ops"]
            assert own == vec, (nm, lab, own, vec)
            base = vec if base is None else base
            assert vec == base, (nm, lab, vec, base)


def test_historical_witness_is_labelled():
    old = [x for x in ENV["probes"] if x.get("measured_date") and x["measured_date"] != ENV["measured_date"][:10] and x["class_"] == "accept"]
    assert old, "the envelope ships dated historical probes"
    r = R.envelope_check(ENV, old[0]["vector"])
    assert r["status"] == "PASS" and (old[0]["name"] in r["witness"] or "historical, not re-run since" in r["reason"] or r["witness"])
    # a vector only an old probe dominates names it as historical
    new_only = [(x["name"], x["vector"]) for x in ENV["probes"] if x["class_"] == "accept" and x.get("measured_date") == ENV["measured_date"][:10]]
    for x in old:
        if not any(RES.dominates(v, x["vector"], ENV["pass_dims"]) for _, v in new_only):
            rr = R.envelope_check(ENV, x["vector"])
            if rr["status"] == "PASS":
                meta = {p["name"]: p for p in ENV["probes"]}
                if meta[rr["witness"][0]]["measured_date"] != ENV["measured_date"][:10]:
                    assert "historical, not re-run since" in rr["reason"] and meta[rr["witness"][0]]["measured_date"] in rr["reason"], rr
                    return
    raise AssertionError("no vector found that only a historical probe dominates")


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception:  # noqa: BLE001
            fails += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - fails}/{len(tests)} tests passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
