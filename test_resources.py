# SPDX-License-Identifier: Apache-2.0
"""Tests for resources.py (vector definitions, liveness, nested ifs, envelope verdict) and the envelope rules file. run: python test_resources.py"""
import json
import os
import sys

from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, qpy
from qiskit.circuit.classical import expr, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import resources as R  # noqa: E402

U = types.Uint


def regs_circuit(nr, w, nread=None, staged=False):
    q = QuantumRegister(nr * w + 1, "q")
    out = ClassicalRegister(1, "out")
    gs = [ClassicalRegister(w, f"G{i}") for i in range(nr)]
    qc = QuantumCircuit(q, out, *gs)
    for i, g in enumerate(gs):
        for j in range(w):
            qc.measure(q[i * w + j], g[j])
    nread = nread or nr
    e = expr.bit_xor(gs[0], gs[1])
    for g in gs[2:nread]:
        e = expr.bit_xor(e, g)
    S = ClassicalRegister(w, "S")
    qc.add_register(S)
    qc.store(S, e)
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[-1])
    return qc


def test_basic_counts():
    v = R.resource_vector(regs_circuit(10, 2))
    assert v["cone_regs"] == 10 and v["peak_live_regs"] == 11 and v["stored_count"] == 1 and v["stored_bits"] == 2
    assert v["cone_ops"] == 9 and v["max_store_ops"] == 9 and v["lr_stack"] == 2 and v["ershov"] == 2 and v["n_ifs"] == 1
    assert v["unary_ops"] == 0 and v["literal_count"] == 0 and sum(v[f] for f in R.FLAG_DIMS) == 0
    assert set(v) == set(R.DIMENSIONS)


def test_unread_registers_do_not_stay_live():
    a, b = regs_circuit(12, 2, nread=2), regs_circuit(12, 2)
    va, vb = R.resource_vector(a), R.resource_vector(b)
    assert va["cone_regs"] == 2 and vb["cone_regs"] == 12 and va["peak_live_regs"] < vb["peak_live_regs"]


def test_stack_need_and_ershov():
    q = QuantumRegister(2, "q")
    m = ClassicalRegister(8, "m")
    S = ClassicalRegister(1, "S")
    leaf = [expr.lift(m[i]) for i in range(8)]
    for shape, lr, er in (("left", 2, 2), ("right", 8, 2)):
        qc = QuantumCircuit(q, m, S)
        for i in range(8):
            qc.measure(q[0], m[i])
        if shape == "left":
            e = leaf[0]
            for x in leaf[1:]:
                e = expr.bit_xor(e, x)
        else:
            e = leaf[7]
            for x in reversed(leaf[:7]):
                e = expr.bit_xor(x, e)
        qc.store(S[0], e)
        with qc.if_test(expr.lift(S[0])):
            qc.x(q[1])
        v = R.resource_vector(qc)
        assert v["lr_stack"] == lr and v["ershov"] == er and v["cone_ops"] == 7, (shape, v)


def test_flags_literals_unary_and_nested():
    q = QuantumRegister(2, "q")
    d = ClassicalRegister(8, "d")
    S = ClassicalRegister(8, "S")
    qc = QuantumCircuit(q, d, S)
    for j in range(8):
        qc.measure(q[0], d[j])
    qc.store(S, expr.add(expr.mul(expr.bit_and(expr.shift_right(d, expr.lift(3, U(8))), expr.lift(1, U(8))), expr.lift(37, U(8))), expr.lift(5, U(8))))
    with qc.if_test(expr.greater(S, expr.lift(9, U(8)))):
        qc.x(q[1])
        with qc.if_test(expr.bit_not(expr.lift(d[0]))):
            qc.x(q[1])
    v = R.resource_vector(qc)
    assert v["has_shift"] == v["has_mul"] == v["has_add"] == v["has_compare"] == 1
    assert v["literal_count"] >= 4 and v["max_literal_bits"] == 8 and v["n_ifs"] == 2 and v["unary_ops"] == 1


def _vec(n, **kw):
    v = dict.fromkeys(R.DIMENSIONS, n)
    v["unanalysed"] = 0
    v.update(kw)
    return v


def test_envelope_logic():
    acc = [("a1", _vec(3)), ("a2", _vec(1))]
    rej = [("r1", _vec(5))]
    assert R.envelope_verdict(_vec(2), acc, rej)[0] == "PASS"
    assert R.envelope_verdict(_vec(4), acc, rej)[0] == "UNCERTAIN"
    assert R.envelope_verdict(_vec(6), acc, rej)[0] == "FAIL"
    assert R.envelope_verdict(_vec(3), acc, [("r0", _vec(2))])[0] == "CONTRADICTION"
    assert R.envelope_verdict(_vec(2, peak_live_regs=9), acc, rej)[0] == "UNCERTAIN"       # one dimension above every accepted vector -> not PASS
    assert R.envelope_verdict(_vec(2, unanalysed=1), acc, rej)[0] == "UNCERTAIN"           # not fully analysed -> never PASS
    assert R.envelope_verdict(_vec(2, n_ifs=0), acc, rej)[0] == "UNCERTAIN"                # no decision at all -> never PASS


def test_rules_file_consistent():
    p = os.path.join(HERE, "rules", "envelope_ibm_heron_r2_2026-11-15.json")
    d = json.load(open(p))
    assert d["error_code"] == 6057 and set(d["dimensions"]) == set(R.DIMENSIONS)
    acc = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "accept" and r["backend"] == "ibm_marrakesh"]
    rej = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "reject6057" and r["backend"] == "ibm_marrakesh"]
    assert acc and rej
    assert all(set(v) == set(R.DIMENSIONS) for _, v in acc + rej)
    for an, a in acc:
        for rn, r in rej:
            assert not R.dominates(a, r, d["pass_dims"]), f"accepted {an} dominates rejected {rn}: a dimension is missing"
    for n, v in acc:       # every stored probe reproduces itself
        verdict, _ = R.envelope_verdict(v, acc, rej, d["pass_dims"], d["fail_dims"])
        assert verdict == "PASS", (n, verdict)
    for n, v in rej:
        verdict, _ = R.envelope_verdict(v, acc, rej, d["pass_dims"], d["fail_dims"])
        assert verdict == "FAIL", (n, verdict)
    assert d["leave_one_out_wrong"] == [] and d["leave_one_out_all_probes_wrong"] == []
    assert all("measured_date" in r and r["circuit_shipped"] for r in d["probes"])


def test_rules_file_regenerates_from_shipped_circuits():
    import build_envelope as BE
    d = json.load(open(os.path.join(HERE, "rules", "envelope_ibm_heron_r2_2026-11-15.json")))
    rows = {r["name"]: r["vector"] for r in BE.load_rows()}
    stored = {r["name"]: r["vector"] for r in d["probes"]}
    assert rows == stored, [n for n in rows if rows[n] != stored.get(n)][:5]


def _with_cond(qc, newcond):
    from qiskit.circuit import IfElseOp
    out = qc.copy_empty_like()
    for ins in qc.data:
        op = ins.operation
        if op.name == "if_else":
            out.append(IfElseOp(newcond, op.blocks[0], None), ins.qubits, ins.clbits)
        else:
            out.append(op, ins.qubits, ins.clbits)
    return out


def test_every_condition_form_follows_the_cone_for_rejected_probes():
    """B1 regression: rewrite the if condition of each QPU-rejected probe into every equivalent form; the cone must be followed and the verdict must never be PASS."""
    import tiers_v2 as T
    d = json.load(open(os.path.join(HERE, "rules", "envelope_ibm_heron_r2_2026-11-15.json")))
    acc = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "accept" and r["backend"] == "ibm_marrakesh"]
    rej = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "reject6057" and r["backend"] == "ibm_marrakesh"]
    for name in ("P1_ir104", "P1_C8bal", "P1_R12x2_a", "P1_split6", "P1_alloc12"):
        qc = T.get_prog(name).circuits()[0]
        base = R.resource_vector(qc)
        final = [i.operation for i in qc.data if i.operation.name == "if_else"][-1].condition
        forms = {"as built": final}
        tgt = [i.operation for i in qc.data if i.operation.name == "store"][-1].lvalue
        reg = tgt.var if isinstance(tgt.var, ClassicalRegister) else qc.find_bit(tgt.var).registers[0][0]
        bit = tgt.var if not isinstance(tgt.var, ClassicalRegister) else tgt.var[0]
        forms["tuple (register, 1)"] = (reg, 1)
        forms["tuple (bit, 1)"] = (bit, 1)
        if len(reg) > 1:
            forms["expr.equal(register, 1)"] = expr.equal(expr.lift(reg), expr.lift(1, U(len(reg))))
            forms["expr.index(register, 0)"] = expr.index(expr.lift(reg), 0)
        for fname, c in forms.items():
            v = R.resource_vector(_with_cond(qc, c))
            assert v["unanalysed"] == 0, (name, fname)
            for dim in ("cone_ops", "max_store_ops", "stored_count", "lr_stack", "peak_live_regs"):
                assert v[dim] >= base[dim] - (1 if dim == "cone_ops" else 0) or fname.startswith("expr.index"), (name, fname, dim, v[dim], base[dim])
            verdict, _ = R.envelope_verdict(v, acc, rej, d["pass_dims"], d["fail_dims"])
            assert verdict != "PASS", (name, fname, verdict)


def _with_conds(qc, fn, which):
    """copy of qc where the condition of the if_else ops listed in `which` (indices among the if_else ops) is replaced by fn(old_condition)."""
    from qiskit.circuit import IfElseOp
    out = qc.copy_empty_like()
    k = -1
    for ins in qc.data:
        op = ins.operation
        if op.name == "if_else":
            k += 1
            if k in which:
                op = IfElseOp(fn(op.condition), op.blocks[0], op.blocks[1] if len(op.blocks) > 1 else None)
        out.append(op, ins.qubits, ins.clbits)
    return out


def _as_expr(c):
    from qiskit.circuit import Clbit
    if isinstance(c, tuple):
        t, val = c
        t = expr.lift(t)
        return expr.equal(t, expr.lift(val, t.type)) if t.type.kind is not types.Bool else (t if val else expr.logic_not(t))
    return expr.lift(c) if isinstance(c, (Clbit, ClassicalRegister)) else c


def test_condition_forms_for_rejected_probes_with_several_ifs():
    """The rejected probes that have more than one `if` (P1_old_LUT85K8, P1_old_CONE, old:T3m_materialized, E1_decode9) were skipped by the single-if form test:
    rewrite the condition of each `if` (one at a time, and all together) into equivalent forms; no variant may ever be PASS."""
    d = json.load(open(os.path.join(HERE, "rules", "envelope_ibm_heron_r2_2026-11-15.json")))
    acc = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "accept" and r["backend"] == "ibm_marrakesh"]
    rej = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "reject6057" and r["backend"] == "ibm_marrakesh"]
    idx = json.load(open(os.path.join(HERE, "data", "envelope_probes", "index.json")))["probes"]
    wraps = {"as built (as expr)": _as_expr,
             "not-not": lambda c: expr.logic_not(expr.logic_not(_as_expr(c))),
             "and True": lambda c: expr.logic_and(_as_expr(c), True),
             "or False": lambda c: expr.logic_or(_as_expr(c), False)}
    n_multi = n_cases = 0
    for x in idx:
        if x["class_"] != "reject6057":
            continue
        with open(os.path.join(HERE, "data", "envelope_probes", x["qpy"]), "rb") as f:
            qc = qpy.load(f)[0]
        k = sum(1 for i in qc.data if i.operation.name == "if_else")
        if k < 2:
            continue
        n_multi += 1
        for wname, fn in wraps.items():
            for which in [{j} for j in range(k)] + [set(range(k))]:
                try:
                    q2 = _with_conds(qc, fn, which)
                except Exception:       # a form qiskit cannot build for this condition type
                    continue
                v = R.resource_vector(q2)
                verdict, _ = R.envelope_verdict(v, acc, rej, d["pass_dims"], d["fail_dims"])
                assert verdict != "PASS", (x["name"], wname, sorted(which), verdict)
                n_cases += 1
    assert n_multi == 4, n_multi
    assert n_cases >= 4 * 4, n_cases


def test_register_read_of_bitwise_stores_and_unknown_forms():
    q = QuantumRegister(14, "q")
    g = [ClassicalRegister(1, f"g{i}") for i in range(12)]
    S = ClassicalRegister(4, "S")
    qc = QuantumCircuit(q, *g, S)
    for i in range(12):
        qc.measure(q[i], g[i][0])
    e = expr.lift(g[0][0])
    for i in range(1, 12):
        e = expr.bit_xor(e, expr.lift(g[i][0]))
    qc.store(S[0], e)
    with qc.if_test(expr.equal(expr.lift(S), expr.lift(1, U(4)))):        # whole-register read of a register written bit by bit
        qc.x(q[12])
    v = R.resource_vector(qc)
    assert v["cone_regs"] == 12 and v["cone_ops"] == 11 and v["stored_count"] == 1, v
    assert R.normalise_condition(object()) is None and R.normalise_condition(None) is None
    # anything the analysis could not follow can never be PASS
    unk = dict(v, unanalysed=1)
    assert R.envelope_verdict(unk, [("a", _vec(99))], [])[0] == "UNCERTAIN"
    assert R.envelope_verdict(dict(v, n_ifs=0), [("a", _vec(99))], [])[0] == "UNCERTAIN"


def test_loops_and_index_lvalue_do_not_crash():
    q = QuantumRegister(3, "q")
    c = ClassicalRegister(4, "c")
    qc = QuantumCircuit(q, c)
    qc.measure(q[0], c[0])
    qc.store(expr.index(expr.lift(c), 1), expr.lift(c[0]))
    with qc.for_loop(range(2)):
        qc.x(q[1])
    with qc.if_test(expr.lift(c[1])):
        qc.x(q[2])
    v = R.resource_vector(qc)
    assert v["unanalysed"] >= 1 and set(v) == set(R.DIMENSIONS)


def _docs_text():
    """README plus every docs/*.md (the documentation is split across these files)."""
    import glob
    parts = [os.path.join(HERE, "README.md")] + sorted(glob.glob(os.path.join(HERE, "docs", "*.md")))
    return "\n".join(open(p).read() for p in parts)


def test_readme_numbers_match_shipped_files():
    """Key README numbers must equal the shipped rules/data files (stops documentation drift)."""
    import re
    readme = _docs_text()
    d = json.load(open(os.path.join(HERE, "rules", "envelope_ibm_heron_r2_2026-11-15.json")))
    n = len(R.DIMENSIONS)
    assert len(d["dimensions"]) == n
    # every "<k> (resource) dimensions" statement must equal the true dimension count
    for m in re.finditer(r"\b(\d+) (?:resource )?dimensions", readme):
        assert int(m.group(1)) == n, f"README says {m.group(1)} dimensions, resources.py has {n}"
    # failure dimensions
    m = re.search(r"failure dimensions \(([a-z_, ]+)\)", readme)
    assert m and sorted(x.strip() for x in m.group(1).split(",")) == sorted(d["fail_dims"]), (m and m.group(1), d["fail_dims"])
    # envelope counts
    acc = sum(r["class_"] == "accept" and r["backend"] == "ibm_marrakesh" for r in d["probes"])
    rej = sum(r["class_"] == "reject6057" and r["backend"] == "ibm_marrakesh" for r in d["probes"])
    m = re.search(r"\((\d+) accepted incl\. the packed fix-pack circuits, (\d+) rejected with 6057", readme)
    assert m and (int(m.group(1)), int(m.group(2))) == (acc, rej), (m and m.groups(), acc, rej)
    # unique-vector leave-one-out headline
    u, lo = d["unique_counts"], d["leave_one_out"]
    m = re.search(r"(\d+) of (\d+) accepted predicted PASS and (\d+) UNCERTAIN", readme)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) == (lo["accept"]["PASS"], u["accepted"], lo["accept"]["UNCERTAIN"]), m and m.groups()
    m = re.search(r"(\d+) of (\d+) rejected predicted FAIL and (\d+) UNCERTAIN", readme)
    assert m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) == (lo["reject6057"]["FAIL"], u["rejected"], lo["reject6057"]["UNCERTAIN"]), m and m.groups()
    # secondary (replicate-inclusive) numbers
    sec = d["leave_one_out_all_probes"]
    assert f"{sec['accept']['PASS']} of {acc} and {sec['reject6057']['FAIL']} of {rej}" in readme


def test_probe_counts_and_cross_backend_replications_match_files():
    """Probe-count badge / '<N> probes' statements equal the envelope; the kingston and fez replication data, outcomes, coverage and run_metadata agree with the README."""
    import glob
    import re
    docs = _docs_text()
    main = open(os.path.join(HERE, "README.md")).read()
    d = json.load(open(os.path.join(HERE, "rules", "envelope_ibm_heron_r2_2026-11-15.json")))
    n = len(d["probes"])
    assert f"QPU%20probes-{n}-" in main, n
    for m in re.finditer(r"envelope over (\d+) probes", docs):
        assert int(m.group(1)) == n, (m.group(0), n)
    for m in re.finditer(r"Scatter of (\d+) probes", main):
        assert int(m.group(1)) == n
    king = [p for p in d["probes"] if p["backend"] == "ibm_kingston"]
    assert sorted(p["name"] for p in king) == sorted(f"p3k:P3_{x}" for x in ("R10x2", "R11x2", "R12x2", "split6", "overlapNF")) and all(p["measured_date"] == "2026-11-16" for p in king)
    mar = {p["name"].split(":", 1)[1]: p for p in d["probes"] if p["name"].startswith("p3m:")}
    for p in king:                                           # identical outcome and vector as the marrakesh repeat
        q = mar[p["name"].split(":", 1)[1]]
        assert p["class_"] == q["class_"] and p["vector"] == q["vector"], p["name"]
    fez = [p for p in d["probes"] if p["backend"] == "ibm_fez"]
    assert sorted(p["name"] for p in fez) == sorted(f"p3f:P3_{x}" for x in ("R10x2", "R11x2", "R12x2", "split6", "overlapNF")) and all(p["measured_date"] == "2026-11-16" for p in fez)
    for p in fez:                                            # identical outcome and vector as the marrakesh repeat and the kingston probe
        q = mar[p["name"].split(":", 1)[1]]
        assert p["class_"] == q["class_"] and p["vector"] == q["vector"], p["name"]
    assert d["backends_covered"] == ["ibm_marrakesh"] and sorted(d["backends_partial"]) == ["ibm_fez", "ibm_kingston"]
    assert d["backend_coverage"] == "ibm_marrakesh (all probes) + ibm_kingston and ibm_fez (5 register-rule probes each)"
    assert "1.3.37" in d["backends_partial"]["ibm_fez"] and "1.0.0" in d["backends_partial"]["ibm_kingston"]
    assert len(d["replicated_probe_outcome_agreement"]) == 10 and all(x["agrees"] and not x["contradicts"] for x in d["replicated_probe_outcome_agreement"])
    assert d["unique_counts"] == {"accepted": 65, "rejected": 21}                         # headline counts are over ibm_marrakesh and did not move
    # data folder: no job ids, scrubbed manifest, backend version as stated in the docs
    kd = os.path.join(HERE, "data", "p3_kingston")
    man = json.load(open(os.path.join(kd, "manifest_234a8fad3bff.json")))
    assert man["backend"] == "ibm_kingston" and all(j["job_id"] is None for j in man["jobs"]) and man["backend_info"]["backend_version"] == "1.0.0"
    for f in glob.glob(os.path.join(kd, "**", "*"), recursive=True):
        if os.path.isfile(f) and not f.endswith((".npz", ".bin")):
            assert not re.search(r"\bd[a-z0-9]{19}\b", open(f).read()), f
    meta = json.load(open(os.path.join(HERE, "data", "run_metadata.json")))
    k = meta["rounds"]["round5_p3_kingston_replication"]
    assert k["backend"] == "ibm_kingston" and k["charged_qpu_seconds"] == sum(j["qpu_charge_seconds"] for j in k["jobs"]) == 11
    assert meta["backend_versions_recorded_at_submission"]["plans"]["234a8fad3bff"]["backend_version"] == "1.0.0"
    res = open(os.path.join(kd, "RESULTS_QPU_p3_kingston.md")).read()
    assert res.count("| ACCEPTED |") == 3 and res.count("| ERROR | 6057 |") == 2
    for t in ("ibm_kingston", "1.0.0", "1.0.21", "identical", "not** run there", "11 QPU seconds"):
        assert t in docs, t
    # ibm_fez: scrubbed data folder, identical outcomes, run_metadata (submission vs execution window, charge, version), README wording
    fd = os.path.join(HERE, "data", "p3_fez")
    fman = json.load(open(os.path.join(fd, "manifest_faf87bdc3fa6.json")))
    assert fman["backend"] == "ibm_fez" and all(j["job_id"] is None for j in fman["jobs"]) and fman["backend_info"]["backend_version"] == "1.3.37"
    for f in glob.glob(os.path.join(fd, "**", "*"), recursive=True):
        if os.path.isfile(f):                                 # binaries included
            assert not re.search(rb"\bd[a-z0-9]{19}\b", open(f, "rb").read()), f
    fres = open(os.path.join(fd, "RESULTS_QPU_p3_fez.md")).read()
    assert fres.count("| ACCEPTED |") == 3 and fres.count("| ERROR | 6057 |") == 2
    z = meta["rounds"]["round4_p3_ibm_fez"]
    assert z["backend"] == "ibm_fez" and z["status_counts"] == {"DONE": 3, "ERROR": 2} and z["charged_qpu_seconds"] == sum(j["qpu_charge_seconds"] for j in z["jobs"]) == 11
    assert z["submission_window_utc"][1] < "2026-11-16" <= z["execution_window_utc"][0] and z["execution_window_utc"][1] < "2026-11-17"
    assert all(j["status"] in ("DONE", "ERROR") and j["running_utc"] and j["finished_utc"] for j in z["jobs"])
    assert meta["backend_versions_recorded_at_submission"]["plans"]["faf87bdc3fa6"]["backend_version"] == "1.3.37"
    assert meta["totals"]["charged_qpu_seconds_all_plans"] == sum(r.get("charged_qpu_seconds") or 0 for r in meta["rounds"].values())
    assert "1.0.21 / 1.0.0 / 1.3.37" in main and "identical outcomes" in main and "measured only on `ibm_marrakesh`" in main
    assert "still queued" not in docs and "replication pending" not in docs and "is queued" not in main
    for t in ("ibm_fez", "1.3.37", "22.5 h", "14:00:50 to 14:03:30 UTC"):
        assert t in docs, t


def test_readme_dates_match_measured_dates():
    """Dates stated in the README must equal the set of measured_date values (rules) plus run dates in run_metadata.json."""
    import re
    readme = _docs_text()
    d = json.load(open(os.path.join(HERE, "rules", "envelope_ibm_heron_r2_2026-11-15.json")))
    meta = json.load(open(os.path.join(HERE, "data", "run_metadata.json")))
    dates = {p["measured_date"] for p in d["probes"].values()} if isinstance(d.get("probes"), dict) else {p["measured_date"] for p in d["probes"]}
    for k, r in meta["rounds"].items():
        for ts in (r.get("window_utc") or []):                       # UTC dates (submitted_local can fall on the next local day)
            dates |= set(re.findall(r"\d{4}-\d\d-\d\d", ts or ""))
        dates |= {m.replace("_", "-") for m in re.findall(r"\d{4}_\d\d_\d\d", k)}
    assert len(dates) == 4, dates                                   # 2026-11-06, -09, -15 (ibm_marrakesh) and 2026-11-16 (ibm_kingston and ibm_fez replications)
    main_readme = open(os.path.join(HERE, "README.md")).read()          # the front page itself must state the dates
    m = re.search(r"measured on `ibm_marrakesh`: ([^;.]*?\(.*?\)), ([^;.]*?\(.*?\)), ([^;.]*?\(.*?\)); replications on `ibm_kingston` and `ibm_fez`: ([^;.]*?\(.*?\))[;.]", main_readme)
    assert m, "README must attribute the dates: marrakesh 3, kingston/fez replications 1"
    assert "2026-11-16" in m.group(4) and "ibm_kingston" in m.group(4) and "ibm_fez" in m.group(4), m.group(4)
    assert re.search(r"measured on `ibm_marrakesh`", main_readme) and "5 register-rule probes were replicated on `ibm_kingston` and `ibm_fez`" in main_readme
    assert "ibm_fez" in main_readme and "not affiliated with" in main_readme and "monotonicity" in main_readme.lower() and "UNCERTAIN is common by design" in main_readme
    stated = set(re.findall(r"\d{4}-\d\d-\d\d", " ".join(m.groups())))
    assert stated == dates, (stated, dates)
    assert not re.search(r"\b(one|single) day\b", readme, re.I), "README still claims a single day"
    for c in d["caveats"]:
        assert not re.search(r"\b(one|single) day\b", c, re.I), c


def test_figures_shipped_and_clean():
    """Figures referenced by the README exist (light and dark SVG + PNG) and carry no local paths."""
    import re
    main = open(os.path.join(HERE, "README.md")).read()
    names = set(re.findall(r"docs/img/([a-z0-9]+-(?:light|dark)\.svg)", main))
    assert len(names) >= 8, names
    for n in names:
        svg = open(os.path.join(HERE, "docs", "img", n)).read()
        assert "/Users/" not in svg and "/home/" not in svg
        assert os.path.exists(os.path.join(HERE, "docs", "img", n[:-4] + ".png"))


def test_frozen_rounds_e1_e2_verdicts_unchanged_under_current_rule():
    """The README says the e1/e2 verdicts are identical under the 5- and 4-dimension failure rules and the current envelope: verify from the stored manifests."""
    import glob
    import re
    d = json.load(open(os.path.join(HERE, "rules", "envelope_ibm_heron_r2_2026-11-15.json")))
    acc = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "accept" and r["backend"] == "ibm_marrakesh"]
    rej = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "reject6057" and r["backend"] == "ibm_marrakesh"]
    four = sorted(d["fail_dims"])
    five = sorted(set(four) | {"peak_live_bitvars"})
    frozen_dims = {}
    for rnd in ("e1", "e2"):
        man = json.load(open(glob.glob(os.path.join(HERE, "data", rnd, "manifest_*.json"))[0]))
        text = man["predictions"]
        m = re.search(r"failure dimensions \[([^\]]+)\]", text)
        frozen_dims[rnd] = sorted(x.strip().strip("'") for x in m.group(1).split(","))
        frozen = {mm.group(1): mm.group(2) for mm in re.finditer(r"^(E[12]_\w+)\s+(PASS|FAIL|UNCERTAIN)\b", text, re.M)}
        assert frozen
        for rec in man["jobs"]:
            v = {**dict.fromkeys(R.DIMENSIONS, 0), **rec["meta"]["vector"]}
            acc_o = [(n, x) for n, x in acc if not n.startswith(f"{rnd}:")]        # leave the round's own probes out of the current envelope
            rej_o = [(n, x) for n, x in rej if not n.startswith(f"{rnd}:")]
            for dims in (four, five):
                got, _ = R.envelope_verdict(v, acc_o, rej_o, d["pass_dims"], dims)
                assert got == frozen[rec["name"]], (rnd, rec["name"], got, frozen[rec["name"]], dims)
    assert frozen_dims["e1"] == five and frozen_dims["e2"] == four, frozen_dims
    readme = _docs_text()
    assert "**five** failure dimensions" in readme and "**four** (cone_ops, lr_stack, peak_live_regs, unary_ops)" in readme


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
            print("ok  ", name)
    print(f"{n}/{n} tests passed")
