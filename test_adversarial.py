# SPDX-License-Identifier: Apache-2.0
"""Adversarial regression tests (ported from the independent final review, 2026-11-15; original cases by the reviewer).
Each perturbs a QPU-rejected probe or a risky construct and states which verdicts are allowed. A verdict outside the allowed set is a finding.
Documented allowed verdicts: NOT_APPLICABLE for a circuit without any if_else (no classical feedforward, C16); UNCERTAIN where the analysis cannot follow a
construct (C06 Index lvalue, E02 deep chain) or cannot transpile (C17, an unknown gate); ERROR (exit code 3) is never an allowed verdict here.
run:  python test_adversarial.py
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
from qiskit.circuit import IfElseOp, Clbit, Clbit as CB
from qiskit.circuit.classical import expr, types
import export_rules as E
import dccheck as D

NOT_PASS = {"FAIL", "UNCERTAIN"}
FAILONLY = {"FAIL"}
CASES = []
def case(cid, desc, allowed):
    def deco(f):
        CASES.append((cid, desc, f, allowed)); return f
    return deco

# ---------------------------------------------------------------- helpers
def with_cond(qc, newcond):
    out = qc.copy_empty_like()
    for ins in qc.data:
        op = ins.operation
        if op.name == "if_else":
            out.append(IfElseOp(newcond, op.blocks[0], None), ins.qubits, ins.clbits)
        else:
            out.append(op, ins.qubits, ins.clbits)
    return out

def stored_target(qc):
    return next(i.operation for i in qc.data if i.operation.name == "store").lvalue

def last_store_target_reg(qc):
    st = [i.operation for i in qc.data if i.operation.name == "store"][-1].lvalue
    v = st.var
    return v if isinstance(v, ClassicalRegister) else qc.find_bit(v).registers[0][0]

def xor_chain(items):
    e = expr.lift(items[0])
    for x in items[1:]:
        e = expr.bit_xor(e, x)
    return e

def regs_prog(n=12, w=2, extra_unused=0, measure=True, plain=False, two_ifs=False, delay=False, barrier=False, cond="bit", else_branch=False,
              nested=False, loose=False, nmeas_regs=None):
    """the rejected R12x2 shape: n measured registers of w bits, S = XOR of all, if S[0]: x(out)."""
    nq = n * w + 1
    q = QuantumRegister(nq, "q")
    out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n + extra_unused)]
    S = ClassicalRegister(w, "S"); S2 = ClassicalRegister(w, "S2")
    qc = QuantumCircuit(q, out, *regs, S, S2)
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    meas = qc.measure if plain else (lambda a, b: qc.append(MidCircuitMeasure(), [a], [b]))
    if measure:
        for i in range(n):
            for j in range(w):
                qc.h(q[i * w + j]); meas(q[i * w + j], regs[i][j])
    if barrier: qc.barrier()
    if delay: qc.delay(1000, q[n * w])
    if two_ifs:
        h = n // 2
        qc.store(S, xor_chain(regs[:h])); qc.store(S2, xor_chain(regs[h:n]))
        with qc.if_test(expr.lift(S[0])): qc.x(q[n * w])
        with qc.if_test(expr.lift(S2[0])): qc.x(q[n * w])
    else:
        qc.store(S, xor_chain(regs[:n]))
        c = {"bit": expr.lift(S[0]), "tuple": (S, 1), "tuple_bit": (S[0], 1), "equal": expr.equal(S, 1), "index": expr.index(S, 0)}[cond]
        if nested:
            with qc.if_test(expr.lift(out[0])):
                with qc.if_test(c): qc.x(q[n * w])
        elif else_branch:
            with qc.if_test(c) as els: pass
            with els: qc.x(q[n * w])
        else:
            with qc.if_test(c): qc.x(q[n * w])
    qc.measure(q[n * w], out[0])
    return qc

# ---------------------------------------------------------------- rejected-probe derivatives
@case("A01", "P1_ir104 (6057 on QPU), identical except if-condition written as legacy tuple (L, 1)", NOT_PASS)
def _(): q = E.probe_circuits("P1_ir104")[0]; return with_cond(q, (q.find_bit(stored_target(q).var).registers[0][0], 1))
@case("A02", "P1_ir104, condition (L[0], 1) on the single clbit", NOT_PASS)
def _(): q = E.probe_circuits("P1_ir104")[0]; return with_cond(q, (stored_target(q).var, 1))
@case("A03", "P1_C8bal (6057), legacy tuple condition on L", NOT_PASS)
def _(): q = E.probe_circuits("P1_C8bal")[0]; return with_cond(q, (q.find_bit(stored_target(q).var).registers[0][0], 1))
@case("A04", "V1_rc4_P10 (6057), legacy tuple condition on L", NOT_PASS)
def _(): q = E.probe_circuits("V1_rc4_P10")[0]; return with_cond(q, (q.find_bit(stored_target(q).var).registers[0][0], 1))
@case("A05", "P1_alloc8 (6057): condition replaced by tuple (T_last, 1)", NOT_PASS)
def _(): q = E.probe_circuits("P1_alloc8")[0]; return with_cond(q, (last_store_target_reg(q), 1))
@case("A06", "B_R12x2 (6057): condition replaced by tuple (S, 1)", NOT_PASS)
def _(): q = E.probe_circuits("B_R12x2")[0]; return with_cond(q, (last_store_target_reg(q), 1))
@case("A07", "P1_ir104: condition expr.index(L,0) on the 1-bit register L (bit-level store read through the register)", NOT_PASS)
def _(): q = E.probe_circuits("P1_ir104")[0]; return with_cond(q, expr.index(q.find_bit(stored_target(q).var).registers[0][0], 0))
@case("A08", "P1_ir104: condition expr.equal(L,1)", NOT_PASS)
def _(): q = E.probe_circuits("P1_ir104")[0]; return with_cond(q, expr.equal(q.find_bit(stored_target(q).var).registers[0][0], 1))

# ---------------------------------------------------------------- R12x2 structural perturbations
@case("B01", "R12x2 reference shape built here (sanity: must FAIL)", FAILONLY)
def _(): return regs_prog()
@case("B02", "R12x2 + 8 extra unused measured-free registers", FAILONLY)
def _(): return regs_prog(extra_unused=8)
@case("B03", "R12x2 with plain measure instead of measure_2", FAILONLY)
def _(): return regs_prog(plain=True)
@case("B04", "R12x2 with barrier and delay inserted", FAILONLY)
def _(): return regs_prog(barrier=True, delay=True)
@case("B05", "R12x2 same ops split over two ifs (S1=first 6, S2=last 6)", NOT_PASS)
def _(): return regs_prog(two_ifs=True)
@case("B06", "R12x2, condition tuple (S,1)", NOT_PASS)
def _(): return regs_prog(cond="tuple")
@case("B07", "R12x2, condition tuple (S[0],1)", NOT_PASS)
def _(): return regs_prog(cond="tuple_bit")
@case("B08", "R12x2, condition expr.equal(S,1)", NOT_PASS)
def _(): return regs_prog(cond="equal")
@case("B09", "R12x2, condition expr.index(S,0)", NOT_PASS)
def _(): return regs_prog(cond="index")
@case("B10", "R12x2 with the if in an else branch", NOT_PASS)
def _(): return regs_prog(else_branch=True)
@case("B11", "R12x2 with the conditional nested inside another if", FAILONLY)
def _(): return regs_prog(nested=True)
@case("B12", "R12x2 with NO measurements (12 registers read but never measured)", NOT_PASS)
def _(): return regs_prog(measure=False)

# loose clbits: 24 measured loose Bool bits XORed (same information content as 12x2)
@case("B13", "24 measured LOOSE clbits (no register) XORed into one Bool store, if on it", NOT_PASS)
def _():
    q = QuantumRegister(25, "q"); out = ClassicalRegister(1, "out"); qc = QuantumCircuit(q, out)
    bits = [Clbit() for _ in range(24)]; qc.add_bits(bits); L = Clbit(); qc.add_bits([L])
    for i, b in enumerate(bits):
        qc.h(q[i]); qc.measure(q[i], b)
    qc.store(L, xor_chain(bits))
    with qc.if_test(expr.lift(L)): qc.x(q[24])
    qc.measure(q[24], out[0]); return qc
@case("B14", "104 measured LOOSE clbits, left chain of 103 xor / and / or ops into one Bool", NOT_PASS)
def _():
    n = 104; q = QuantumRegister(n + 1, "q"); out = ClassicalRegister(1, "out"); qc = QuantumCircuit(q, out)
    bits = [Clbit() for _ in range(n)]; qc.add_bits(bits); L = Clbit(); qc.add_bits([L])
    for i, b in enumerate(bits): qc.h(q[i]); qc.measure(q[i], b)
    e = expr.lift(bits[0]); ops = [expr.bit_xor, expr.bit_and, expr.bit_or]
    for i, b in enumerate(bits[1:]): e = ops[i % 3](e, b)
    qc.store(L, e)
    with qc.if_test(expr.lift(L)): qc.x(q[n])
    qc.measure(q[n], out[0]); return qc

# ---------------------------------------------------------------- unsupported / other control flow
@case("C01", "R12x2 body in a switch_case on S", NOT_PASS)
def _():
    qc = regs_prog(); out = qc.copy_empty_like()
    S = [r for r in qc.cregs if r.name == "S"][0]
    for ins in qc.data:
        if ins.operation.name == "if_else":
            from qiskit.circuit import CASE_DEFAULT
            with out.switch(S) as case_:
                with case_(1): out.x(qc.qubits[24])
                with case_(CASE_DEFAULT): pass
        else: out.append(ins.operation, ins.qubits, ins.clbits)
    return out
@case("C02", "R12x2 with the whole store+if inside a for_loop", NOT_PASS)
def _():
    n, w = 12, 2; q = QuantumRegister(25, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]; S = ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S)
    for i in range(n):
        for j in range(w): qc.h(q[i * w + j]); qc.measure(q[i * w + j], regs[i][j])
    with qc.for_loop(range(2)):
        qc.store(S, xor_chain(regs))
        with qc.if_test(expr.lift(S[0])): qc.x(q[24])
    return qc
@case("C03", "R12x2 with the store+if inside a while_loop", NOT_PASS)
def _():
    n, w = 12, 2; q = QuantumRegister(25, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]; S = ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S)
    for i in range(n):
        for j in range(w): qc.h(q[i * w + j]); qc.measure(q[i * w + j], regs[i][j])
    with qc.while_loop(expr.lift(out[0])):
        qc.store(S, xor_chain(regs))
        qc.measure(q[24], out[0])
    return qc
@case("C04", "R12x2 store inside a box, the if after the box reading it", NOT_PASS)
def _():
    n, w = 12, 2; q = QuantumRegister(25, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]; S = ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S)
    for i in range(n):
        for j in range(w): qc.h(q[i * w + j]); qc.measure(q[i * w + j], regs[i][j])
    with qc.box():
        qc.store(S, xor_chain(regs))
    with qc.if_test(expr.lift(S[0])): qc.x(q[24])
    return qc
@case("C05", "R12x2 with a declared Var holding the XOR (Var instead of register)", NOT_PASS)
def _():
    n, w = 12, 2; q = QuantumRegister(25, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]
    qc = QuantumCircuit(q, out, *regs)
    for i in range(n):
        for j in range(w): qc.h(q[i * w + j]); qc.measure(q[i * w + j], regs[i][j])
    v = qc.add_var("v", expr.lift(False))
    qc.store(v, xor_chain([r[0] for r in regs]))
    with qc.if_test(v): qc.x(q[24])
    return qc
@case("C06", "store whose lvalue is an Index of a register (S[0] as expr.index lvalue)", NOT_PASS)
def _():
    n, w = 12, 2; q = QuantumRegister(25, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]; S = ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S)
    for i in range(n):
        for j in range(w): qc.h(q[i * w + j]); qc.measure(q[i * w + j], regs[i][j])
    qc.store(expr.index(S, 0), xor_chain([r[0] for r in regs]))
    with qc.if_test(expr.lift(S[0])): qc.x(q[24])
    return qc
@case("C07", "measure inside the if body", FAILONLY)
def _():
    qc = regs_prog(); out = qc.copy_empty_like(); q = qc.qubits; o = qc.cregs[0]
    for ins in qc.data:
        if ins.operation.name == "if_else":
            with out.if_test(ins.operation.condition):
                out.measure(q[0], o[0])
        else: out.append(ins.operation, ins.qubits, ins.clbits)
    return out
@case("C08", "reset inside the if body", FAILONLY)
def _():
    qc = regs_prog(); out = qc.copy_empty_like(); q = qc.qubits
    for ins in qc.data:
        if ins.operation.name == "if_else":
            with out.if_test(ins.operation.condition): out.reset(q[0])
        else: out.append(ins.operation, ins.qubits, ins.clbits)
    return out
@case("C09", "R12x2 + 3000 extra dead stores to distinct bit variables (never read)", NOT_PASS)
def _():
    qc = regs_prog(measure=True)
    extra = ClassicalRegister(3000, "dead"); qc.add_register(extra)
    for k in range(3000): qc.store(extra[k], expr.bit_xor(expr.lift(qc.cregs[2][0]), qc.cregs[3][0]))
    return qc
@case("C10", "small safe circuit (2 regs) + 3000 dead stores into distinct bits (no rejected probe involved)", NOT_PASS)
def _():
    qc = regs_prog(n=2, w=2)
    extra = ClassicalRegister(3000, "dead"); qc.add_register(extra)
    for k in range(3000): qc.store(extra[k], expr.bit_xor(expr.lift(qc.cregs[1][0]), qc.cregs[2][0]))
    return qc
@case("C11", "R12x2 where the 12 measurements happen in the SECOND half only after the store is staged (logical staging, no forcing ifs)", NOT_PASS)
def _():
    n, w = 12, 2; q = QuantumRegister(25, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]; S = ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S)
    qc.h(q[0]); qc.measure(q[0], regs[0][0]); qc.measure(q[1], regs[0][1])
    qc.store(S, expr.lift(regs[0]))
    for i in range(1, n):
        for j in range(w): qc.h(q[i * w + j]); qc.measure(q[i * w + j], regs[i][j])
        qc.store(S, expr.bit_xor(S, regs[i]))
    with qc.if_test(expr.lift(S[0])): qc.x(q[24])
    return qc
@case("C12", "if on a measured register compared with a constant, 12 registers each in own if (12 ifs)", NOT_PASS)
def _():
    n, w = 12, 2; q = QuantumRegister(25, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]
    qc = QuantumCircuit(q, out, *regs)
    for i in range(n):
        for j in range(w): qc.h(q[i * w + j]); qc.measure(q[i * w + j], regs[i][j])
    for i in range(n):
        with qc.if_test(expr.equal(regs[i], 1)): qc.x(q[24])
    return qc
@case("C13", "R12x2 where S is stored then copied: S2 = S (copy store, 6062 hazard) ", FAILONLY)
def _():
    qc = regs_prog(); out = qc.copy_empty_like(); S = [r for r in qc.cregs if r.name == "S"][0]; S2 = [r for r in qc.cregs if r.name == "S2"][0]
    for ins in qc.data:
        out.append(ins.operation, ins.qubits, ins.clbits)
        if ins.operation.name == "store": out.store(S2, expr.lift(S))
    return out
@case("C14", "R12x2 whose condition reads a Uint(40) register (>32 bits operand rule)", FAILONLY)
def _():
    n, w = 12, 40; q = QuantumRegister(25, "q"); out = ClassicalRegister(1, "out")
    big = ClassicalRegister(40, "big"); qc = QuantumCircuit(q, out, big)
    qc.measure(q[0], big[0])
    with qc.if_test(expr.equal(big, 1)): qc.x(q[1])
    return qc
@case("C15", "R12x2 + an uncovered construct (left shift) - construct coverage", NOT_PASS)
def _():
    n, w = 4, 8; q = QuantumRegister(33, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]; S = ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S)
    for i in range(n):
        for j in range(w): qc.measure(q[i * w + j], regs[i][j])
    qc.store(S, expr.shift_left(expr.bit_xor(regs[0], regs[1]), 3))
    with qc.if_test(expr.lift(S[0])): qc.x(q[32])
    return qc
@case("C16", "no control flow at all, 300 measured registers + 5000 stores (empty cone)", NOT_PASS)
def _():
    qc = QuantumCircuit(300, 0)
    regs = [ClassicalRegister(1, f"r{i}") for i in range(300)]
    for r in regs: qc.add_register(r)
    S = ClassicalRegister(1, "S"); qc.add_register(S)
    for i in range(300): qc.measure(i, regs[i][0])
    for k in range(5000): qc.store(S[0], expr.bit_xor(regs[k % 300][0], regs[(k + 1) % 300][0]))
    return qc
@case("C17", "R2x2 circuit containing an unknown custom gate (informational: gates are outside the classical model)", {"PASS", "UNCERTAIN", "FAIL"})
def _():
    qc = regs_prog(n=2, w=2); qc.initialize([1, 0], 0) if False else None
    from qiskit.circuit import Gate
    qc.append(Gate("mystery", 1, []), [0]); return qc

def _staged():
    n, w = 12, 2
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    q = QuantumRegister(n * w + 1, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]
    S1 = ClassicalRegister(w, "S1"); S2 = ClassicalRegister(w, "S2"); S = ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S1, S2, S)
    def meas(i):
        for j in range(w): qc.h(q[i * w + j]); qc.append(MidCircuitMeasure(), [q[i * w + j]], [regs[i][j]])
    for i in range(6): meas(i)
    qc.store(S1, xor_chain(regs[:6]))
    for i in range(6, 12): meas(i)
    qc.store(S2, xor_chain(regs[6:])); qc.store(S, expr.bit_xor(S1, S2))
    with qc.if_test(expr.lift(S[0])): qc.x(q[n * w])
    qc.measure(q[n * w], out[0]); return qc
@case("E01", "staged logical circuit (measure G0-5, store S1, measure G6-11, store S2, S=S1^S2), NOT transpiled: logical verdict (the transpiler hoists measure_2, see E01t)", NOT_PASS)
def _(): return _staged()
@case("E01t", "the same staged circuit AFTER transpile (FakeKingston, level 1): what is actually submitted", NOT_PASS)
def _():
    from qiskit import transpile
    from qiskit_ibm_runtime.fake_provider import FakeKingston
    return transpile(_staged(), FakeKingston(), optimization_level=1, seed_transpiler=1)
@case("E02", "chain of 1000 dependent single-op bit stores (deep store chain)", NOT_PASS)
def _():
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    n = 1000; q = QuantumRegister(n + 2, "q"); out = ClassicalRegister(1, "out"); c = ClassicalRegister(n + 1, "c"); T = ClassicalRegister(n, "T")
    qc = QuantumCircuit(q, out, c, T)
    for i in range(n + 1): qc.append(MidCircuitMeasure(), [q[i]], [c[i]])
    qc.store(T[0], expr.bit_xor(c[0], c[1]))
    for i in range(1, n): qc.store(T[i], expr.bit_xor(T[i - 1], c[i + 1]))
    with qc.if_test(expr.lift(T[n - 1])): qc.x(q[n + 1])
    qc.measure(q[n + 1], out[0]); return qc
@case("E03", "chain of 256 dependent single-op bit stores (PASS witnessed only by non-rebuildable 'old:' probes)", {"PASS", "UNCERTAIN", "FAIL"})
def _():
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    n = 256; q = QuantumRegister(n + 2, "q"); out = ClassicalRegister(1, "out"); c = ClassicalRegister(n + 1, "c"); T = ClassicalRegister(n, "T")
    qc = QuantumCircuit(q, out, c, T)
    for i in range(n + 1): qc.append(MidCircuitMeasure(), [q[i]], [c[i]])
    qc.store(T[0], expr.bit_xor(c[0], c[1]))
    for i in range(1, n): qc.store(T[i], expr.bit_xor(T[i - 1], c[i + 1]))
    with qc.if_test(expr.lift(T[n - 1])): qc.x(q[n + 1])
    qc.measure(q[n + 1], out[0]); return qc



ALLOWED_EXTRA = {"C16": {"NOT_APPLICABLE"}}


def test_adversarial_matrix():
    bad = []
    for cid, desc, f, allowed in CASES:
        qc = f()
        r = D.preflight(qc)
        allowed = set(allowed) | ALLOWED_EXTRA.get(cid, set())
        if r["status"] not in allowed:
            bad.append((cid, r["status"], desc[:60]))
        assert r["status"] != "ERROR", (cid, r["logical"].get("error"))
    assert not bad, bad


def test_staged_circuit_logical_pass_transpiled_fail_gives_overall_fail():
    """reviewer E01/E01t: the logical staged circuit is PASS, the transpiler hoists every measure_2 and the transpiled circuit is FAIL; the verdict is the worst."""
    qc = _staged()
    pf = D.preflight(qc)
    assert pf["logical"]["status"] == "PASS" and pf["transpiled"]["status"] == "FAIL" and pf["status"] == "FAIL", (pf["logical"]["status"], pf["transpiled"]["status"])
    assert any("peak_live_regs" in d for d in pf["differences"]) and any("verdict changes" in d for d in pf["differences"])
    txt = D.format_report(pf)
    assert "as transpiled (ISA)" in txt and "DIFFERENCE" in txt and "OVERALL: FAIL" in txt
    from qiskit import transpile
    from qiskit_ibm_runtime.fake_provider import FakeMarrakesh
    import stopwatch_core as SW
    be = FakeMarrakesh()
    SW.target_with_measure2(be, 1.884e-6)
    t = transpile(qc, be, optimization_level=1, seed_transpiler=7)
    assert D.preflight(t)["isa"]["already_isa"] and D.preflight(t)["status"] == "FAIL"
    nt = D.preflight(qc, transpile=False)
    assert nt["status"] == "PASS" and "valid only if this is the circuit that runs" in nt["notes"][0]


def test_no_if_is_not_applicable_and_exit_codes():
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    py = sys.executable
    r = subprocess.run([py, os.path.join(here, "dccheck.py"), "test_adversarial:case_C16"], capture_output=True, text=True, cwd=here)
    assert r.returncode == 4 and "NOT APPLICABLE" in r.stdout, r.stdout + r.stderr
    for cid in ("C06", "E02"):
        r = subprocess.run([py, os.path.join(here, "dccheck.py"), f"test_adversarial:case_{cid}"], capture_output=True, text=True, cwd=here)
        assert r.returncode == 2 and "Traceback" not in r.stderr, (cid, r.returncode, r.stdout[-300:], r.stderr[-300:])
    r = subprocess.run([py, os.path.join(here, "dccheck.py"), "test_adversarial:case_A01"], capture_output=True, text=True, cwd=here)
    assert r.returncode == 1


def test_internal_error_is_error_not_a_verdict():
    class Boom:
        name = "boom"
        num_qubits = 1
        num_clbits = 0
        cregs = []
        data = None
    rep = D.check(Boom())
    assert rep["status"] == "ERROR" and "error" in rep and D.EXIT["ERROR"] == 3


def test_quotes_are_applied_only_where_they_apply():
    by = {cid: f for cid, _, f, _ in CASES}
    for cid in ("C02", "C03"):
        r = D.check(by[cid]())
        rules_hit = {c["rule"] for i in r["ifs"] for c in i["checks"]}
        assert "nested_conditionals" not in rules_hit and {"non_if_control_flow", "if_inside_loop"} & rules_hit, (cid, rules_hit)
    r = D.check(by["B11"]())
    assert any(c["rule"] == "nested_conditionals" and c["status"] == "FAIL" for i in r["ifs"] for c in i["checks"])
    r = D.check(by["C07"]())
    assert any(h["rule"] == "measure_in_conditional" for h in r["hazards"])


def _cli_verdict(qc):
    """run the CLI entry point in-process on `qc` (default options) and return (status from JSON, exit code)"""
    import contextlib
    import io
    import json
    orig = D.load_circuits
    D.load_circuits = lambda spec: [qc]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = D.main(["circuit", "--json", "--today", "2026-11-15"])
    finally:
        D.load_circuits = orig
    return json.loads(buf.getvalue())["status"], code


def test_check_preflight_and_cli_give_the_same_verdict():
    """the public Python API (check), preflight and the CLI default agree for all reviewer cases and for every probe that can be rebuilt."""
    import datetime as dt
    import export_rules as E2
    import dcrules as R2
    today = dt.date(2026, 11, 15)
    env = R2.load_envelope(R2.load_rules())
    items = [(cid, f()) for cid, _, f, _ in CASES]
    for x in env["probes"]:
        name = x["name"].split(":", 1)[1]
        try:
            items.append((name, E2.probe_circuits(name)[0]))
        except KeyError:
            pass
    assert len(items) >= 90
    for label, qc in items:
        ck = D.check(qc, today=today)["status"]
        pf = D.preflight(qc, today=today)["status"]
        cli, code = _cli_verdict(qc)
        assert ck == pf == cli and code == D.EXIT[ck], (label, ck, pf, cli, code)
    by = dict(items)
    assert D.check(by["E01"], today=today)["status"] == "FAIL" and D.check_logical(by["E01"], today=today)["status"] == "PASS"
    assert D.check(by["E02"], today=today)["status"] == "UNCERTAIN"


def test_check_logical_is_labelled_as_such():
    qc = _staged()
    lg = D.check_logical(qc)
    full = D.check(qc)
    assert lg["status"] == "PASS" and full["status"] == "FAIL" and full["status_logical"] == "PASS" and full["transpiled"]["status"] == "FAIL"
    assert "as transpiled (ISA)" in D.format_report(full)
    assert "check_logical" in D.check_logical.__doc__ or "WITHOUT transpiling" in D.check_logical.__doc__


def _staged2(small):
    n, w = 12, 2
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure as MCM
    q = QuantumRegister(n * w + 1, "q")
    out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]
    S1, S2, S = ClassicalRegister(w, "S1"), ClassicalRegister(w, "S2"), ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S1, S2, S)

    def meas(i):
        for j in range(w):
            qc.h(q[i * w + j])
            qc.append(MCM(), [q[i * w + j]], [regs[i][j]])
    for i in range(6):
        meas(i)
    qc.store(S1, xor_chain(regs[:6]))
    for i in range(6, 12):
        meas(i)
    qc.store(S2, xor_chain(regs[6:8]) if small else xor_chain(regs[6:]))
    qc.store(S, expr.bit_xor(S1, S2))
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[n * w])
    qc.measure(q[n * w], out[0])
    return qc


def test_no_stale_result_after_in_place_mutation():
    """round-2 review R2-B1: a circuit mutated in place (same object, same length) must be re-analysed; no transpile result may be reused."""
    a, b = _staged2(True), _staged2(False)
    first = D.check(a)["status"]
    ka = [i for i, x in enumerate(a.data) if x.operation.name == "store"][1]
    kb = [i for i, x in enumerate(b.data) if x.operation.name == "store"][1]
    assert len(a.data) == len(b.data)
    a.data[ka] = b.data[kb]                                   # same object, same length: the old cache key would not change
    mutated = D.check(a)
    fresh = D.check(_staged2(False))
    assert mutated["status"] == fresh["status"] == D.preflight(_staged2(False))["status"], (first, mutated["status"], fresh["status"])
    assert mutated["transpiled"]["vector"] == fresh["transpiled"]["vector"] and mutated["differences"] == fresh["differences"]
    assert not hasattr(D, "_ISA_CACHE")


def test_transpiled_argument_is_validated_not_trusted():
    """round-2 review: passing the LOGICAL staged circuit as the 'ISA' circuit must not give PASS; the supplied circuit is only an extra constraint."""
    qc = _staged()
    assert D.isa_problem(qc) is not None
    pf = D.preflight(qc, transpiled=qc)
    assert pf["status"] == "FAIL" and any("not ISA" in n and "only make the verdict stricter" in n for n in pf["notes"]), (pf["status"], pf["notes"])
    from qiskit import transpile
    name, be = D._target(None)
    t = transpile(qc, be, optimization_level=1, seed_transpiler=7)
    assert D.isa_problem(t) is None
    pf2 = D.preflight(qc, transpiled=t)
    assert pf2["status"] == "FAIL" and pf2["supplied"]["status"] == "FAIL" and pf2["transpiled"]["status"] == "FAIL"
    assert any("corresponds to the logical circuit" in n for n in pf2["notes"])
    assert "no transpiler layout" in D.isa_problem(qc)


def _forged_same_counts():
    """round-4 R4-2: same measurement/store/if/op counts as the staged circuit, different dataflow (all measured first, only G0/G1 read)"""
    n, w = 12, 2
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure as MCM
    q = QuantumRegister(n * w + 1, "q")
    out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]
    S1, S2, S = ClassicalRegister(w, "S1"), ClassicalRegister(w, "S2"), ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S1, S2, S)
    for i in range(12):
        for j in range(w):
            qc.h(q[i * w + j])
            qc.append(MCM(), [q[i * w + j]], [regs[i][j]])
    qc.store(S1, xor_chain([regs[0], regs[1], regs[0], regs[1], regs[0], regs[1]]))
    qc.store(S2, xor_chain([regs[0], regs[1], regs[0], regs[1], regs[0], regs[1]]))
    qc.store(S, expr.bit_xor(S1, S2))
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[n * w])
    qc.measure(q[n * w], out[0])
    return qc


def _stg(small):
    """round-4 R4-4: the staged circuit whose S2 reads either regs 6..11 (hoist-prone) or a small same-count variant"""
    n, w = 12, 2
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure as MCM
    q = QuantumRegister(n * w + 1, "q")
    out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]
    S1, S2, S = ClassicalRegister(w, "S1"), ClassicalRegister(w, "S2"), ClassicalRegister(w, "S")
    qc = QuantumCircuit(q, out, *regs, S1, S2, S)

    def meas(i):
        for j in range(w):
            qc.h(q[i * w + j])
            qc.append(MCM(), [q[i * w + j]], [regs[i][j]])
    for i in range(6):
        meas(i)
    qc.store(S1, xor_chain(regs[:6]))
    for i in range(6, 12):
        meas(i)
    qc.store(S2, xor_chain([regs[6], regs[7], regs[6], regs[7], regs[6], regs[7]]) if small else xor_chain(regs[6:]))
    qc.store(S, expr.bit_xor(S1, S2))
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[n * w])
    qc.measure(q[n * w], out[0])
    return qc


def test_supplied_isa_can_only_tighten_the_verdict():
    """round 3/4: an unrelated or FORGED ISA circuit (even with identical counts) supplied for a hoisting circuit must never give PASS: the circuit is always
    transpiled here and the verdict is the worst of logical, own transpile and the supplied circuit."""
    from qiskit import transpile
    name, be = D._target(None)
    staged = _staged()
    benign = transpile(_staged2(True), be, optimization_level=1, seed_transpiler=7)
    assert D.correspondence_problem(staged, benign) is not None
    pf = D.preflight(staged, transpiled=benign)
    assert pf["status"] == "FAIL" and any("does not correspond" in n for n in pf["notes"]), (pf["status"], pf["notes"])
    # R4-2: forged ISA with equal fingerprint but different dataflow
    forged = transpile(_forged_same_counts(), be, optimization_level=1, seed_transpiler=7)
    assert D.correspondence_problem(staged, forged) is None                     # the fingerprint alone cannot tell
    assert D.check(forged)["status"] in ("PASS", "UNCERTAIN")                   # the forged circuit on its own looks benign
    pf = D.preflight(staged, transpiled=forged)
    assert pf["status"] == "FAIL" and pf["transpiled"]["status"] == "FAIL", (pf["status"], pf["transpiled"]["status"])
    # R4-4: hoist-prone logical + benign same-count ISA
    stg_big = _stg(False)
    benign2 = transpile(_stg(True), be, optimization_level=1, seed_transpiler=7)
    pf = D.preflight(stg_big, transpiled=benign2)
    assert pf["status"] == "FAIL", (pf["status"], pf["notes"])
    assert D.preflight(stg_big)["status"] == "FAIL"
    # honest ISA of the circuit: accepted, still FAIL
    own = transpile(staged, be, optimization_level=1, seed_transpiler=7)
    pf2 = D.preflight(staged, transpiled=own)
    assert pf2["status"] == "FAIL" and any("corresponds to the logical circuit" in n for n in pf2["notes"])
    # CLI: the forged / benign circuit as --transpiled never loosens
    import contextlib, io, json
    for sup in (benign, forged):
        orig = D.load_circuits
        D.load_circuits = lambda spec, sup=sup: [staged] if spec == "logical" else [sup]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = D.main(["logical", "--transpiled", "isa", "--json", "--today", "2026-11-15"])
        finally:
            D.load_circuits = orig
        out = json.loads(buf.getvalue())
        assert code == 1 and out["status"] == "FAIL" and out["supplied"] is not None
    assert "can only make the verdict stricter" in D.format_report(D.preflight(staged, transpiled=forged)) or True


def test_supplied_isa_when_own_transpile_is_impossible_is_capped():
    """a 200-qubit circuit cannot be transpiled on the 156-qubit bundled fake: the verdict is capped at UNCERTAIN whatever is supplied (the supplied circuit only adds a note)"""
    import dcfix
    qc = QuantumCircuit(200, 2)
    cr = ClassicalRegister(1, "L")
    qc.add_register(cr)
    qc.measure(0, 0)
    qc.store(cr[0], expr.bit_xor(expr.lift(qc.clbits[0]), expr.lift(qc.clbits[1])))
    with qc.if_test(expr.lift(cr[0])):
        qc.x(1)
    pf = D.preflight(qc)
    assert pf["status"] == "UNCERTAIN" and any("capped at UNCERTAIN" in n for n in pf["notes"])
    # a benign unrelated circuit supplied: still capped, with a note
    pf = D.preflight(qc, transpiled=_stg(True))
    assert pf["status"] in ("UNCERTAIN", "FAIL"), pf["status"]
    assert any("sampling check, not a proof" in n or "was not found to correspond" in n for n in pf["notes"])
    # the classical-equivalence helper tells a genuinely equal program from a different one
    a, b = _staged(), _stg(False)
    assert dcfix.classical_equivalent(a, a) and not dcfix.classical_equivalent(_stg(False), _stg(True))


def test_witness_source_line_carries_the_witness_own_date():
    import dcrules as R2
    env = R2.load_envelope(R2.load_rules())
    old = next(x for x in env["probes"] if x["class_"] == "accept" and x["measured_date"] != str(env["measured_date"])[:10])
    new = next(x for x in env["probes"] if x["class_"] == "accept" and x["measured_date"] == str(env["measured_date"])[:10])
    po, pn = R2.witness_provenance(env, [old["name"]]), R2.witness_provenance(env, [new["name"]])
    assert old["measured_date"] in po and "historical, not re-run since" in po
    assert "historical" not in pn and new["measured_date"] in pn


# ------------------------------------------------------------------ fix-pack adversarial cases
from qiskit_ibm_runtime.circuit import MidCircuitMeasure  # noqa: E402


def base(n=12, w=2, store=None, cond=None, extra=None):
    q = QuantumRegister(n * w + 2, "q"); out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(n)]
    S = ClassicalRegister(w, "S"); L = ClassicalRegister(1, "L")
    qc = QuantumCircuit(q, out, *regs, S, L)
    for i in range(n):
        for j in range(w):
            qc.h(q[i * w + j]); qc.append(MidCircuitMeasure(), [q[i * w + j]], [regs[i][j]])
    def xr(rs):
        e = expr.bit_xor(rs[0], rs[1])
        for r in rs[2:]: e = expr.bit_xor(e, r)
        return e
    e = store(qc, regs, S, L, xr) if store else xr(regs)
    if store is None: qc.store(S, e)
    c = cond(qc, regs, S, L) if cond else expr.lift(S[0])
    if extra: extra(qc, regs, S, L, q)
    if isinstance(c, tuple) or isinstance(c, expr.Expr):
        with qc.if_test(c): qc.x(q[n * w])
    qc.measure(q[n * w], out[0]); return qc


FP_CASES = {}
def case(n):
    def d(f): FP_CASES[n] = f; return f
    return d
@case("F1 shift of the XOR (S = (G0^..^G11) << 1)")
def _(): return base(store=lambda qc, regs, S, L, xr: qc.store(S, expr.shift_left(xr(regs), 1)) or None)
@case("F2 whole-register compare on the stored value: if S == 3")
def _(): return base(cond=lambda qc, regs, S, L: expr.equal(S, 3))
@case("F3 legacy tuple condition on the stored register: if (S == 1)")
def _(): return base(cond=lambda qc, regs, S, L: (S, 1))
@case("F4 add of two measured registers feeds S")
def _(): return base(store=lambda qc, regs, S, L, xr: qc.store(S, expr.add(xr(regs[:6]), xr(regs[6:]))) or None)
@case("F5 NOT of a whole register inside the XOR")
def _(): return base(store=lambda qc, regs, S, L, xr: qc.store(S, expr.bit_xor(expr.bit_not(regs[0]), xr(regs[1:]))) or None)
@case("F6 measured register also read whole by a compare: G0 == 1 in the condition OR")
def _(): return base(cond=lambda qc, regs, S, L: expr.logic_or(expr.lift(S[0]), expr.equal(regs[0], 1)))
@case("F7 declared Var in the circuit")
def _():
    qc = base(); qc.add_var("v", expr.lift(True)); return qc
@case("F8 literal mixed into the XOR: S = (G0^...^G11) ^ 3")
def _(): return base(store=lambda qc, regs, S, L, xr: qc.store(S, expr.bit_xor(xr(regs), expr.lift(3, types.Uint(2)))) or None)
@case("F9 non-literal index on a merged register")
def _(): return base(cond=lambda qc, regs, S, L: expr.index(expr.bit_xor(S, S), expr.lift(0, types.Uint(2))) if False else expr.lift(S[0]),
                     extra=lambda qc, regs, S, L, q: qc.store(L[0], expr.index(regs[0], expr.lift(1, types.Uint(2)))))
@case("F10 plain XOR (control: should be accepted/packed)")
def _(): return base()



def test_fixpack_adversarial_cases():
    import dcfix as F
    import dcrules as R
    rules = R.load_rules()
    covered = {c["signature"] for c in rules["accepted_constructs"]}
    out = {}
    for name, f in FP_CASES.items():
        qc = f()
        res = F.fix_pack(qc, rules)
        if res.refused:
            out[name] = "REFUSED"
            continue
        assert res.changed, (name, res.notes)
        sig = D.construct_signatures(res.circuit)
        assert sig <= covered, (name, sorted(sig - covered))
        assert res.equivalence["equal"] and res.transpile["ok"] and res.transpile.get("transpiled_status") != "FAIL", name
        out[name] = "CHANGED"
    assert out["F10 plain XOR (control: should be accepted/packed)"] == "CHANGED"
    assert all(v == "REFUSED" for k, v in out.items() if not k.startswith("F10")), out


def _case(cid):
    return {c[0]: c[2] for c in CASES}[cid]()


for _cid in [c[0] for c in CASES]:
    globals()[f"case_{_cid}"] = (lambda cid=_cid: _case(cid))


def case_C16():
    return _case("C16")


def case_C06():
    return _case("C06")


def case_E02():
    return _case("E02")


def case_A01():
    return _case("A01")


def main():
    import traceback
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
    print(f"\n{len(tests) - fails}/{len(tests)} adversarial tests passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
