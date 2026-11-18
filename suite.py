# SPDX-License-Identifier: Apache-2.0
"""Acceptance and latency conformance suite for IBM dynamic circuits with classical expressions (design + local validation).

NO QPU submission is implemented here on purpose (quota ~4 min). Modes:
  --local   build every probe, check semantics EXHAUSTIVELY (vectorised independent evaluator vs a numpy
            reference built from the probe's abstract recipe), known-failure guards (6060/6062/6066/6327),
            transpile against a target (FakeKingston + measure_2 by default; --target ibm_marrakesh does a
            READ-ONLY backend fetch), QPY round trip, structure (ops by class / depth / inputs).
  --plan    jobs, shots, estimated QPU seconds for --tier minimal|ideal, hypothesis predictions + design power.
  --fetch   stub: read-only status/error-code fetch of job ids in a manifest, no analysis side effects.

Two probe families
  LAT  (latency)   the legacy stopwatch stopwatch protocol: K reps of {H+measure_2 sources; store(expr); if/else X on probes}
                   + REF0..REF1000 calibration circuits + NOIF; ns/decision over a 1-bit `if`.
  ACC  (acceptance) tiny circuit sources -> store(s) -> if -> X(out) -> measure; result = accepted / error code
                   (6057, ...) + per-shot agreement. Used to bracket 6057 along ops / depth / cone / registers / K / PUBs.
usage (from this directory):  ../../.venv/bin/python suite.py --local [--only NAME,NAME] [--target ibm_marrakesh]
                              ../../.venv/bin/python suite.py --plan --tier minimal|ideal|both [--quota-left 240]
"""
from __future__ import annotations

import argparse
import io
import itertools
import json
import os
import random
import sys

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, qpy, transpile
from qiskit.circuit import Clbit
from qiskit.circuit.classical import expr, types

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import stopwatch_core as SW
import estimators as EST  # noqa: E402  (REF_D, NOIF_D, REP_OVERHEAD_S, qpu_seconds model, weighted_params)

sys.setrecursionlimit(20000)   # QPY dump/load recurse per expression depth (chain probes reach depth 128)
U = types.Uint
BOOL = types.Bool()
O = expr.Binary.Op
# ------------------------------------------------------------------ abstract recipes
# node = ("bit", i) | ("reg", name) | ("lit", v, w) | ("op", opname, a, b) | ("shr", a, k) | ("cmp", opname, a, b)
# Each recipe is compiled twice: to a qiskit expr (below) and to numpy (independent reference).
BIN = {"xor": expr.bit_xor, "and": expr.bit_and, "or": expr.bit_or, "add": expr.add, "mul": expr.mul,
       "gt": expr.greater, "bxor": expr.bit_xor}
NP = {"xor": np.bitwise_xor, "and": np.bitwise_and, "or": np.bitwise_or}


def to_expr(n, env):
    k = n[0]
    if k == "bit":
        return expr.lift(env["X"][n[1]])
    if k == "ubit":                      # bit j of Uint register r  ->  (r >> j) & 1
        _, r, j, w = n
        return expr.bit_and(expr.shift_right(env[r], expr.lift(j, U(w))), expr.lift(1, U(w)))
    if k == "reg":
        return env[n[1]]
    if k == "lit":
        return expr.lift(n[1], U(n[2]))
    if k == "op":
        return BIN[n[1]](to_expr(n[2], env), to_expr(n[3], env))
    if k == "cmpbit":                    # Bool: reg > literal
        return expr.greater(env[n[1]], expr.lift(n[2], U(n[3])))
    raise KeyError(k)


def to_np(n, bits, regs, widths):
    """bits: (2^s, nbits) bool; regs: name -> uint64 array; returns array (bool or uint64)."""
    k = n[0]
    if k == "bit":
        return bits[:, n[1]]
    if k == "ubit":
        return (regs[n[1]] >> np.uint64(n[2])) & np.uint64(1)
    if k == "reg":
        return regs[n[1]]
    if k == "lit":
        return np.uint64(n[1])
    if k == "cmpbit":
        return regs[n[1]] > np.uint64(n[2])
    a, b = to_np(n[2], bits, regs, widths), to_np(n[3], bits, regs, widths)
    m = np.uint64((1 << widths[n[4]]) - 1) if len(n) > 4 else None
    if n[1] in NP:
        return NP[n[1]](a, b)
    if n[1] == "add":
        return (a + b) & m
    if n[1] == "mul":
        return (a * b) & m
    raise KeyError(n[1])


def count(n, acc=None):
    """ops by class (the classes used in the latency and acceptance analysis)."""
    acc = acc if acc is not None else dict.fromkeys(("xor", "andor", "extract", "add", "mul", "cmp", "regbit"), 0)
    k = n[0]
    if k == "op":
        c = n[1]
        if len(n) > 5 and n[5] == "reg":
            acc["regbit"] += 1
        elif c == "xor":
            acc["xor"] += 1
        elif c in ("and", "or"):
            acc["andor"] += 1
        else:
            acc[c] += 1
        count(n[2], acc), count(n[3], acc)
    elif k == "ubit":
        acc["extract"] += 2              # shift + and
    elif k == "cmpbit":
        acc["cmp"] += 1
    return acc


def depth(n):
    if n[0] == "op":
        return 1 + max(depth(n[2]), depth(n[3]))
    if n[0] == "ubit":
        return 2
    if n[0] == "cmpbit":
        return 1
    return 0


def inputs(n, s=None):
    s = s if s is not None else set()
    if n[0] == "bit":
        s.add(("X", n[1]))
    elif n[0] in ("ubit",):
        s.add((n[1], n[2]))
    elif n[0] in ("cmpbit",):
        s |= {(n[1], b) for b in range(n[3])}
    elif n[0] == "reg":
        s |= {(n[1], b) for b in range(8)}
    elif n[0] == "op":
        inputs(n[2], s), inputs(n[3], s)
    return s


# ------------------------------------------------------------------ recipe generators
def op_node(name, a, b, w=None, kind=None):
    return ("op", name, a, b, w) if kind is None else ("op", name, a, b, w, kind)


def bool_chain(ops, nbits, rng, pat):
    """left comb over distinct-first bits; pat = list of op names cycled."""
    n = ("bit", 0)
    for i in range(ops):
        n = op_node(pat[i % len(pat)], n, ("bit", (i + 1) % nbits), "B")
    return n


def bool_tree(ops, nbits, rng, pat):
    leaves = [("bit", i % nbits) for i in range(ops + 1)]
    lvl, k = leaves, 0
    while len(lvl) > 1:
        nxt = []
        for i in range(0, len(lvl) - 1, 2):
            nxt.append(op_node(pat[k % len(pat)], lvl[i], lvl[i + 1], "B")), k
            k += 1
        if len(lvl) % 2:
            nxt.append(lvl[-1])
        lvl = nxt
    return lvl[0]


def bool_soup(ops, nbits, rng, shape):
    """random gate soup with exactly `ops` ops, leaves random bits, siblings never identical."""
    def build(n):
        if n == 0:
            return ("bit", rng.randrange(nbits))
        nl = {"tree": n // 2, "chain": n - 1}[shape] if n > 1 else 0
        nl = min(max(nl, 0), n - 1)
        l, r = build(nl), build(n - 1 - nl)
        while r == l:
            r = ("bit", rng.randrange(nbits))
            if n - 1 - nl:
                r = build(n - 1 - nl)
        return op_node(rng.choice(["xor", "and", "or", "xor"]), l, r, "B")
    return build(ops)


def pop(nb, w, weights=None):
    n = None
    for i in range(nb):
        t = ("ubit", "D", i, w)
        if weights:
            t = op_node("mul", t, ("lit", weights[i], w), w)
        n = t if n is None else op_node("add", n, t, w)
    return n


def shxor(nb, w):
    n = None
    for i in range(nb):
        t = ("ubit", "D", i, w)
        n = t if n is None else op_node("xor", n, t, w)
    return n


def cmpxor(nb, rng):
    ks = rng.sample(range(1, 255), nb)
    n = None
    for k in ks:
        t = ("cmpbit", "D", k, 8)
        n = t if n is None else op_node("xor", n, t, "B")
    return n


def simd(ops, rng, regs=("D", "X0", "X1")):
    def build(n):
        if n == 0:
            return ("reg", rng.choice(regs))
        nl = n // 2
        l, r = build(nl), build(n - 1 - nl)
        while r == l:
            r = ("reg", rng.choice(regs))
        return op_node(rng.choice(["xor", "and", "or", "xor"]), l, r, "R", "reg")
    return build(ops)


# ------------------------------------------------------------------ probe catalogue
# sink 'bool': store into 1-bit clbit L[0], cond = L[0].   sink 'uint': store Uint(8) S, cond = S > T.
# sink 'simd': store Uint(8) S (lane-wise), cond = S[0].
def P(name, recipe, sink, width, fam="LAT", tier=("minimal", "ideal"), note="", T=None):
    return dict(name=name, recipe=recipe, sink=sink, width=width, fam=fam, tier=tier, note=note, T=T)


def catalogue():
    r = random.Random(23)
    wv, wT, _ = SW.weighted_params("WVOTE")
    sp = [133, 107, 0, 0, 0, 0, 0]
    pr = {}
    L = []
    bx = ("xor",)
    L += [
        P("XORC15", bool_chain(15, 16, r, bx), "bool", 1, note="15 Bool xor, chain depth 15, 16 distinct bits"),
        P("XORT15", bool_tree(15, 16, r, bx), "bool", 1, note="same 15 xor, balanced tree depth 4"),
        P("AOC15", bool_chain(15, 16, r, ("and", "or")), "bool", 1, tier=("ideal",), note="and/or chain: gate type vs xor"),
        P("XORFOLD", bool_chain(15, 4, r, bx), "bool", 1, tier=("ideal",),
          note="x0^x1^x2^x3 repeated 4x == const 0; a latency near TRIV would mean the expression's cost does not depend on how it is written"),
        P("SOUP63", bool_soup(63, 16, random.Random(7), "tree"), "bool", 1,
          note="63-op random and/or/xor soup: extrapolation 2.3x beyond old range, below 83-op pass"),
        P("POP8", pop(8, 8), "uint", 8, T=3, note="8 extract pairs + 7 add (23 ops +1 cmp), no mul"),
        P("WSUM8", pop(8, 8, [30, 29, 28, 27, 26, 25, 24, 23]), "uint", 8, T=100, tier=("ideal",),
          note="POP8 + 8 mul: isolates mul"),
        P("WVOTE27", None, "uint", 8, note="legacy the legacy decoder fixture/the legacy stopwatch expression (27 ops +1 cmp), bridge to old 1032 ns"),
        P("SHXOR8", shxor(8, 8), "uint", 8, T=0, tier=("ideal",), note="8 extract pairs + 7 Uint xor: add vs xor"),
        P("CMP8", cmpxor(8, random.Random(5)), "bool", 1, tier=("ideal",), note="8 reg>literal compares xor-ed"),
        P("SIMD8_24", simd(24, random.Random(3)), "simd", 8, tier=("ideal",), note="24 Uint(8) register bitwise ops"),
        P("SIMD8_6", simd(6, random.Random(4)), "simd", 8, tier=("ideal",), note="6 register ops: count vs width"),
    ]
    # acceptance (6057 bracket). Bool expr over 8 bits, exact op count.
    def acc(name, ops, shape, K=1, tier=("ideal",), note="", nbits=8, seed=None):
        return P(name, bool_soup(ops, nbits, random.Random(seed or ops * 13 + (shape == "chain")), shape), "bool", 1,
                 fam="ACC", tier=tier, note=note) | dict(K=K)
    L += [
        acc("A84t_K1", 84, "tree", 1, ("minimal", "ideal"), "is 84 ops alone already >limit? (old LUT85 only tested at K=8)"),
        acc("A84t_K8", 84, "tree", 8, ("minimal", "ideal"), "same, 8 repeated independent stores (the legacy stopwatch protocol)"),
        acc("A96t_K1", 96, "tree", 1, ("minimal", "ideal"), "tree depth ~7"),
        acc("A96c_K1", 96, "chain", 1, ("minimal", "ideal"), "chain depth 95 : depth vs count"),
        acc("A72t_K8", 72, "tree", 8, ("ideal",), "K-accumulation below the single-store limit"),
        acc("A90t_K1", 90, "tree", 1, ("ideal",), "bisect"), acc("A93t_K1", 93, "tree", 1, ("ideal",), "bisect"),
        acc("A100t_K1", 100, "tree", 1, ("ideal",), "bisect"), acc("A128t_K1", 128, "tree", 1, ("ideal",)),
        acc("A96t_n16", 96, "tree", 1, ("ideal",), "16 input bits instead of 8", nbits=16),
    ]
    return L


# cone / register / PUB / SIMD acceptance probes need multi-store structure: separate spec builders
def cone_spec(k, m, seed):
    """k stored Bool exprs of m ops each (over 8 bits), XOR-ed by one consumer -> flattened cone k*m+k-1."""
    rng = random.Random(seed)
    return [bool_soup(m, 8, rng, "tree") for _ in range(k)]


CONES = [("C3x30", 3, 30, ("minimal", "ideal")), ("C6x16", 6, 16, ("minimal", "ideal")), ("C2x48", 2, 48, ("ideal",)),
         ("C6x40", 6, 40, ("ideal",)), ("C4x24", 4, 24, ("ideal",))]
REGS = [("R12x8", 12, 8, ("minimal", "ideal")), ("R6x16", 6, 16, ("ideal",)), ("R16x4", 16, 4, ("ideal",)),
        ("R3x32", 3, 32, ("ideal",))]  # all 96 measured bits (chip has 156 qubits; the SIMD probe CONE used 11x16)
SIMDACC = [("S57", 57, ("ideal",)), ("S96", 96, ("ideal",)), ("S128", 128, ("ideal",))]
PUB2 = ("PUB2x64", 64, ("ideal",))  # an earlier job ran 6 PUBs in one job (no per-job limit was seen)
LIVE = [("W384", 384, ("ideal",)), ("W320", 320, ("ideal",)), ("W448", 448, ("ideal",))]


# ------------------------------------------------------------------ circuits
def make_env(qc, spec, regs_needed):
    q = qc.qubits
    env = {}
    return env


def build_lat(p, K=16, n_z=12, n_x=12, ibm=True, case="CASE"):
    """the legacy stopwatch skeleton. Sources: D(8 bits, Uint) + X0,X1 (8+8, Uint) = 24 measured bits ('X' bits = X0 then X1).
    case: 'CASE' = probe expr; 'REF<d>'; 'NOIF'; returns circuit. Same measured layout in EVERY circuit."""
    before = case.startswith("REFB")          # delay placed BEFORE an unconditional X (no `if`): calibrates the position where the latency falls
    d = int(case[4:]) if before else (int(case[3:]) if case.startswith("REF") else (SW.NOIF_D if case == "NOIF" else 0))
    d_before, d_after = (d, 0) if before else (0, d)
    n_src, n_p = 24, n_z + n_x
    q = QuantumRegister(n_src + n_p + 1, "q")
    src, probes, flag = q[:n_src], q[n_src:n_src + n_p], q[n_src + n_p]
    pr, fl = ClassicalRegister(n_p, "pr"), ClassicalRegister(1, "fl")
    D, X0, X1 = ClassicalRegister(8, "D"), ClassicalRegister(8, "X0"), ClassicalRegister(8, "X1")
    qc = QuantumCircuit(q, pr, fl, D, X0, X1, name=(p["name"] if case == "CASE" else case))
    meas = list(D) + list(X0) + list(X1)
    X = list(X0) + list(X1)
    sink = None
    if case == "CASE":
        if p["sink"] == "bool":
            sink = ClassicalRegister(1, "L")
        else:
            sink = ClassicalRegister(8, "S")
        qc.add_register(sink)
    if ibm:
        from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    for z in probes[:n_z]:
        qc.x(z)
    for h in probes[n_z:]:
        qc.h(h)
    qc.barrier()
    for _ in range(K):
        for s, cb in zip(src, meas):
            qc.h(s)
            qc.append(MidCircuitMeasure(), [s], [cb]) if ibm else qc.measure(s, cb)
        if before:
            qc.barrier()
            if d_before:
                for z in probes:
                    qc.delay(d_before, z, unit="ns")
            for z in probes:
                qc.x(z)
        elif case == "NOIF":
            qc.barrier()
            for z in probes:
                qc.x(z)
        else:
            if case == "CASE":
                cond = emit_sink(qc, p, sink, {"D": D, "X0": X0, "X1": X1, "X": X})
            else:
                cond = expr.lift(D[0])
            with qc.if_test(cond) as else_:
                for z in probes:
                    qc.x(z)
                qc.x(flag)
            with else_:
                for z in probes:
                    qc.x(z)
        if d_after:
            for z in probes:
                qc.delay(d_after, z, unit="ns")
        qc.barrier()
    for h in probes[n_z:]:
        qc.h(h)
    qc.measure(probes, pr)
    qc.measure(flag, fl[0])
    return qc


def wsum_expr(dat, w):
    """Uint(8) weighted sum S = sum_j w_j * ((dat >> j) & 1) as a left-leaning add chain (legacy 27-op fixture)."""
    e = None
    for j, wj in enumerate(w):
        if wj == 0:
            continue
        bit = expr.bit_and(expr.shift_right(dat, j), expr.lift(1, U(8)))
        term = bit if wj == 1 else expr.mul(bit, expr.lift(int(wj), U(8)))
        e = term if e is None else expr.add(e, term)
    return e


def emit_sink(qc, p, sink, regs):
    if p["name"] == "WVOTE27":
        w, T, _ = SW.weighted_params("WVOTE")
        qc.store(sink, wsum_expr(regs["D"], w))
        return expr.greater(sink, expr.lift(T, U(8)))
    env = {"X": regs["X"], "D": regs["D"], "X0": regs["X0"], "X1": regs["X1"]}
    e = to_expr(p["recipe"], env)
    if p["sink"] == "bool":
        qc.store(sink[0], e)
        return expr.lift(sink[0])
    qc.store(sink, e)
    if p["sink"] == "uint":
        return expr.greater(sink, expr.lift(p["T"] if p["T"] is not None else 0, U(8)))
    return expr.lift(sink[0])


def build_acc(p, K=1, ibm=True):
    nb = 16 if p["name"].endswith("n16") else 8
    nout = 1
    q = QuantumRegister(nb + nout, "q")
    out = ClassicalRegister(nout, "out")
    X = ClassicalRegister(nb, "Xr")
    L = ClassicalRegister(1, "L")
    qc = QuantumCircuit(q, out, X, L, name=p["name"])
    if ibm:
        from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    for _ in range(K):
        for i in range(nb):
            qc.h(q[i])
            qc.append(MidCircuitMeasure(), [q[i]], [X[i]]) if ibm else qc.measure(q[i], X[i])
        qc.store(L[0], to_expr(p["recipe"], {"X": list(X)}))
        with qc.if_test(expr.lift(L[0])):
            qc.x(q[nb])
        if K > 1:
            qc.barrier()
    qc.measure(q[nb], out[0])
    return qc


# ------------------------------------------------------------------ vectorised independent evaluator (qiskit expr)
def veval(e, env, widths):
    """env: Clbit -> bool array, register name -> uint64 array. Independent of the lookup probe.evaluate (vectorised)."""
    if isinstance(e, expr.Var):
        v = e.var
        return env[v] if isinstance(v, Clbit) else env[v.name]
    if isinstance(e, expr.Value):
        return np.uint64(int(e.value)) if e.type.kind is types.Uint else bool(e.value)
    if isinstance(e, expr.Cast):
        x = veval(e.operand, env, widths)
        return x != 0 if e.type.kind is types.Bool else x & np.uint64((1 << e.type.width) - 1)
    if isinstance(e, expr.Unary):
        x = veval(e.operand, env, widths)
        return ~x if e.type.kind is types.Bool else (~x) & np.uint64((1 << e.type.width) - 1)
    if isinstance(e, expr.Index):
        return ((veval(e.target, env, widths) >> veval(e.index, env, widths)) & np.uint64(1)).astype(bool)
    a, b = veval(e.left, env, widths), veval(e.right, env, widths)
    m = np.uint64((1 << e.type.width) - 1) if e.type.kind is types.Uint else None
    f = {O.BIT_AND: lambda: a & b, O.BIT_OR: lambda: a | b, O.BIT_XOR: lambda: a ^ b, O.ADD: lambda: (a + b) & m,
         O.MUL: lambda: (a * b) & m, O.SUB: lambda: (a - b) & m, O.GREATER: lambda: a > b, O.LESS: lambda: a < b,
         O.GREATER_EQUAL: lambda: a >= b, O.EQUAL: lambda: a == b, O.NOT_EQUAL: lambda: a != b,
         O.SHIFT_RIGHT: lambda: a >> b, O.SHIFT_LEFT: lambda: (a << b) & m, O.LOGIC_AND: lambda: a & b,
         O.LOGIC_OR: lambda: a | b}[e.op]
    return f()


def n_ops(e):
    if isinstance(e, expr.Binary):
        return 1 + n_ops(e.left) + n_ops(e.right)
    if isinstance(e, expr.Unary):
        return 1 + n_ops(e.operand)
    if isinstance(e, expr.Index):
        return 1 + n_ops(e.target) + n_ops(e.index)
    if isinstance(e, expr.Cast):
        return n_ops(e.operand)
    return 0


def exprs_of(qc):
    return [(i.operation.name, i.operation.rvalue if i.operation.name == "store" else i.operation.condition,
             i.operation.lvalue if i.operation.name == "store" else None)
            for i in qc.data if i.operation.name in ("store", "if_else")]


def support_bits(qc, e):
    s = set()

    def walk(x):
        if isinstance(x, expr.Var):
            v = x.var
            if isinstance(v, Clbit):
                s.add(v)
            else:
                s.update(next(r for r in qc.cregs if r.name == v.name)) if hasattr(v, "name") else None
        for a in ("left", "right", "operand", "target", "index"):
            if hasattr(x, a):
                walk(getattr(x, a))
    walk(e)
    return s


def semantic_check(qc, p, max_bits=24):
    """Exhaustive over the support of the (first) store+if chain: circuit expressions (vectorised evaluator)
    vs numpy reference from the recipe. Returns (n_assignments, mismatches, support_bits)."""
    ex = exprs_of(qc)
    store = next(x for x in ex if x[0] == "store")
    ifs = [x for x in ex if x[0] == "if_else"]
    cond = ifs[0][1]
    # registers participating
    srcregs = {r.name: r for r in qc.cregs if r.name in ("D", "X0", "X1", "Xr")}
    sup = sorted(support_bits(qc, store[1]), key=lambda b: qc.find_bit(b).index)
    if len(sup) > max_bits:
        raise ValueError(f"support {len(sup)} bits > {max_bits}")
    N = 1 << len(sup)
    idx = np.arange(N, dtype=np.uint64)
    benv = {b: ((idx >> np.uint64(i)) & np.uint64(1)).astype(bool) for i, b in enumerate(sup)}
    zero = np.zeros(N, bool)
    renv, widths = {}, {}
    for name, r in srcregs.items():
        v = np.zeros(N, np.uint64)
        for j, b in enumerate(r):
            bit = benv.get(b, zero)
            benv.setdefault(b, zero)
            v |= bit.astype(np.uint64) << np.uint64(j)
        renv[name] = v
        widths[name] = len(r)
    env = {**benv, **renv}
    got_store = veval(store[1], env, widths)
    lv = store[2]
    if lv is not None:
        if isinstance(lv.var, Clbit):
            env[lv.var] = got_store
        else:
            env[lv.var.name] = got_store
            reg = next(r for r in qc.cregs if r.name == lv.var.name)
            for j, b in enumerate(reg):
                env[b] = ((got_store >> np.uint64(j)) & np.uint64(1)).astype(bool)
    got = veval(cond, env, widths)
    # reference
    if p["name"] == "WVOTE27":
        w, T, _ = SW.weighted_params("WVOTE")
        ref = sum(int(wj) * ((renv["D"] >> np.uint64(j)) & np.uint64(1)).astype(np.int64) for j, wj in enumerate(w)) > T
    else:
        bits = np.stack([benv.get(b, zero) for b in (list(srcregs["X0"]) + list(srcregs["X1"]))], 1) \
            if "X0" in srcregs else np.stack([benv.get(b, zero) for b in srcregs["Xr"]], 1)
        widthmap = {"B": 1, "R": 8, None: 8, 8: 8}
        r = to_np_wrap(p["recipe"], bits, renv)
        if p["sink"] == "bool":
            ref = r.astype(bool)
        elif p["sink"] == "uint":
            ref = r.astype(np.uint64) > np.uint64(p["T"] if p["T"] is not None else 0)
        else:
            ref = (r.astype(np.uint64) & np.uint64(1)).astype(bool)
    got = np.asarray(got).astype(bool)
    return N, int(np.sum(got != ref)), len(sup), float(got.mean())


def to_np_wrap(n, bits, regs):
    k = n[0]
    if k == "bit":
        return bits[:, n[1]]
    if k == "ubit":
        return (regs[n[1]] >> np.uint64(n[2])) & np.uint64(1)
    if k == "reg":
        return regs[n[1]]
    if k == "cmpbit":
        return regs[n[1]] > np.uint64(n[2])
    if k == "lit":
        return np.uint64(n[1])
    a, b = to_np_wrap(n[2], bits, regs), to_np_wrap(n[3], bits, regs)
    if n[1] in NP:
        return NP[n[1]](a, b)
    m = np.uint64(255)
    if n[1] == "add":
        return (a + b) & m
    if n[1] == "mul":
        return (a * b) & m
    raise KeyError(n[1])


# ------------------------------------------------------------------ known-failure guards
def guards(qc):
    """Patterns that IBM already rejected (see INVENTORY.md). Returns list of violations."""
    bad = []
    names = [r.name for r in qc.cregs]
    if len(set(names)) != len(names):
        bad.append("duplicate register names")
    bits_seen = {}
    for r in qc.cregs:                                      # 6327: overlapping registers
        for b in r:
            if b in bits_seen and bits_seen[b] != r.name:
                bad.append(f"6327 overlapping registers {bits_seen[b]}/{r.name}")
            bits_seen[b] = r.name
    for kind, e, lv in exprs_of(qc):
        if kind == "store" and isinstance(e, expr.Var):
            bad.append("6062 clbit/var copy store")
        if kind == "store" and isinstance(e, expr.Value):
            bad.append("6060/6062 constant store")

        def walk(x):
            if isinstance(x, expr.Value) and x.type.kind is types.Uint and int(x.value) >= 1 << 32:
                bad.append("literal > u32 (parser)")
            if isinstance(x, expr.Binary) and x.op in (O.SHIFT_RIGHT, O.SHIFT_LEFT) and not isinstance(x.right, expr.Value):
                bad.append("6060 register-amount shift")
            if isinstance(x, expr.Index) and not isinstance(x.index, expr.Value):
                bad.append("dynamic bit index (parser)")
            for a in ("left", "right", "operand", "target", "index"):
                if hasattr(x, a):
                    walk(getattr(x, a))
        walk(e)
    if getattr(qc, "num_vars", 0):
        bad.append("6066 declared Var (add_var) – avoided")
    return sorted(set(bad))


# ------------------------------------------------------------------ structure
def structure(p):
    if p["name"] == "WVOTE27":
        w, T, _ = SW.weighted_params("WVOTE")
        k = sum(1 for x in w if x)
        return dict(ops=27 + 1, classes=dict(extract=2 * k, mul=k, add=k - 1, cmp=1, xor=0, andor=0, regbit=0),
                    depth=k + 2 + 1, inputs=k)
    r = p["recipe"]
    c = count(r)
    ops = sum(c.values())
    if p["sink"] == "uint":
        c = dict(c)
        c["cmp"] += 1
        ops += 1
    dp = depth(r) + (1 if p["sink"] == "uint" else 0)
    return dict(ops=ops, classes=c, depth=dp, inputs=len(inputs(r)))


# ------------------------------------------------------------------ cost / design model
LAT_COST_FACTOR = 1.36   # observed: round-1 latency jobs (K=16, 3000 shots, 6 circuits) were charged 8 s vs 5.9 s modelled


def lat_cost_s(K, shots, n_cases, lat_ns=1500, n_ref=5, noif=False):
    ncirc = n_ref + n_cases + (1 if noif else 0)
    per = shots * (SW.REP_OVERHEAD_S + K * (1.884e-6 + (lat_ns + 60 + 300) * 1e-9) + 10e-6)
    return max(2.0, ncirc * per * LAT_COST_FACTOR)


SUITE_REF_D = (0, 500, 1000, 2000, 3000)   # per-rep delays [ns]; the legacy stopwatch used (0,250,500,1000), whose clip (1.5x max = 1500 ns/decision) would truncate SOUP63 (pred 2155)
ACC_COST = 2.0   # per job: IBM minimum charge (successful or 6057-rejected), the 2026-11-06 probes logs: usage=2


def sigma_model(K, shots, base=100.0, infl=1.6):
    """Assumed 1-sigma of one case increment [ns]. Calibrated on the legacy stopwatch: K=8, 4000 shots, bootstrap 49-151 ns
    (mean ~100); scales 1/K, 1/sqrt(shots); x1.6 for probe-scatter excess (chi2/dof 2-3.6 -> sqrt ~1.4-1.9)."""
    return base * (8.0 / K) * np.sqrt(4000.0 / shots) * infl / 1.6 * (infl / infl)


def plan(tier, quota_left):
    if tier in ("p1", "p2", "p3", "v1", "e1", "e2"):
        import tiers_v2 as T2
        return T2.plan_jobs(tier)
    if tier in ("size", "size_mid", "xrep"):
        jobs = []
        if tier == "size":
            for x in SIZE:
                jobs.append(dict(kind="SIZ", name=x["name"], circuits=2, K=1, shots=SPLIT_SHOTS, cost=ACC_COST, note=f"{x['kind']} size probe"))
            for n in SIZE_REG:
                jobs.append(dict(kind="REG", name=n, circuits=2, K=1, shots=SPLIT_SHOTS, cost=ACC_COST, note="register-threshold probe"))
        elif tier == "size_mid":
            for x in SIZE_MID:
                jobs.append(dict(kind="SIZ", name=x["name"], circuits=2, K=1, shots=SPLIT_SHOTS, cost=ACC_COST, note="midpoint probe (conditional batch)"))
        else:
            for n in XREP_REG:
                jobs.append(dict(kind="REG", name=n, circuits=2, K=1, shots=SPLIT_SHOTS, cost=ACC_COST, note="cross-backend replication"))
            for n in XREP_SPL:
                jobs.append(dict(kind="SPL", name=n, circuits=2, K=1, shots=SPLIT_SHOTS, cost=ACC_COST, note="cross-backend replication"))
            for n in XREP_LAT:
                jobs.append(dict(kind="LAT", name=n, circuits=len(SUITE_REF_D) + 1, K=16, shots=2000, cost=lat_cost_s(16, 2000, 1), note="latency pair, cross-backend"))
        return jobs, float(sum(j["cost"] for j in jobs))
    if tier == "split":
        jobs = [dict(kind="SPL", name=sp["name"], circuits=2, K=1, shots=SPLIT_SHOTS, cost=ACC_COST,
                     note=f"{len(sp['stores'])} stores, fan-in {split_metrics(sp)['fanin_max']}, cone {split_metrics(sp)['cone']} registers") for sp in SPLIT]
        return jobs, float(sum(j["cost"] for j in jobs))
    if tier == "regbisect":
        jobs = [dict(kind="REG", name=nm, circuits=2, K=1, shots=REGB_SHOTS, cost=ACC_COST,
                     note=f"{nr} regs x {w} bits, store reads {nread} (prepared patterns A/B, known output)") for nm, nr, w, nread in REGB]
        return jobs, float(sum(j["cost"] for j in jobs))
    cat = [p for p in catalogue() if tier in p["tier"]]
    lat = [p for p in cat if p["fam"] == "LAT"]
    acc = [p for p in cat if p["fam"] == "ACC"]
    K, shots = (16, 4000) if tier == "ideal" else (16, 3000)
    jobs = []
    # latency: isolated per case (the legacy stopwatch: combined payloads hit 6057, untested whether combos of SMALL cases pass).
    for p in lat:
        jobs.append(dict(kind="LAT", name=p["name"], circuits=len(SUITE_REF_D) + 1, K=K, shots=shots, cost=lat_cost_s(K, shots, 1),
                         note=p["note"]))
    if tier == "ideal":   # one flat reference job (NOIF) in the same layout, mirrors the legacy stopwatch
        jobs.append(dict(kind="LAT", name="NOIF", circuits=5, K=K, shots=shots, cost=lat_cost_s(K, shots, 0, noif=True),
                         note="flat 1-bit-if latency in the same layout"))
    for p in acc:
        jobs.append(dict(kind="ACC", name=p["name"], circuits=1, K=p.get("K", 1), shots=64, cost=ACC_COST, note=p["note"]))
    for nm, k, m, tr in CONES:
        if tier in tr:
            jobs.append(dict(kind="ACC", name=nm, circuits=1, K=1, shots=64, cost=ACC_COST,
                             note=f"{k} stored x {m}-op exprs XOR-ed (flattened cone {k * m + k - 1} ops)"))
    for nm, nr, w, tr in REGS:
        if tier in tr:
            jobs.append(dict(kind="ACC", name=nm, circuits=1, K=1, shots=64, cost=ACC_COST,
                             note=f"{nr} registers x {w} bits (register count vs bits), op count small"))
    for nm, nops, tr in SIMDACC:
        if tier in tr:
            jobs.append(dict(kind="ACC", name=nm, circuits=1, K=1, shots=64, cost=ACC_COST,
                             note=f"{nops} Uint(16) register ops in one store (the SIMD probe: 57 passed)"))
    for nm, nlive, tr in LIVE:
        if tier in tr:
            jobs.append(dict(kind="ACC", name=nm, circuits=1, K=1, shots=64, cost=ACC_COST,
                             note=f"{nlive} live 1-op values (the 2026-11-06 probes: 256 pass, 512 fail)"))
    if tier in PUB2[2]:
        jobs.append(dict(kind="ACC", name=PUB2[0], circuits=2, K=1, shots=64, cost=ACC_COST,
                         note="two PUBs of 64 ops in ONE job: is the limit per circuit or per job?"))
    tot = sum(j["cost"] for j in jobs)
    return jobs, tot


def design_power(tier):
    """Predictions under three hypotheses fitted to the 3 old points, and OLS design for per-class slopes."""
    lat = [p for p in catalogue() if p["fam"] == "LAT" and tier in p["tier"]]
    st = {p["name"]: structure(p) for p in lat}
    # old points (the legacy stopwatch): (ops, inputs, depth, ns) -- depth counts of those expressions from reanalyse_stopwatch.py
    old = np.array([[7, 2, 4, 408.5], [19, 5, 7, 785.9], [27, 7, 9, 1031.6]])
    def fit(col):
        b, a = np.polyfit(old[:, col], old[:, 3], 1)
        return a, b
    hyp = {"H_ops": (0, fit(0)), "H_inputs": (1, fit(1)), "H_depth": (2, fit(2))}
    # NB: old ops/inputs/depth are exactly collinear, so the three fits give identical lines on the old data.
    K, shots = (16, 4000)
    sig = sigma_model(K, shots)
    rows = []
    for p in lat:
        s = st[p["name"]]
        so = s["ops"] - (1 if p["sink"] == "uint" else 0)      # old ladder counted store ops only (cmp excluded)
        sd_ = s["depth"] - (1 if p["sink"] == "uint" else 0)
        pred = {h: a + b * [so, s["inputs"], sd_][c]
                for h, (c, (a, b)) in hyp.items()}
        rows.append((p["name"], s, pred))
    # per-class regression design (classes with counts), intercept included
    cls = ["xor", "andor", "extract", "add", "mul", "cmp", "regbit"]
    Xm = np.array([[1.0] + [st[p["name"]]["classes"][c] for c in cls] for p in lat])
    return rows, cls, Xm, sig


def print_plan(tier, quota_left):
    jobs, tot = plan(tier, quota_left)
    print(f"\n=== plan: {tier} ===")
    print(f"{'kind':4s} {'job':10s} {'circ':>4s} {'K':>3s} {'shots':>5s} {'est QPU s':>9s}  note")
    for j in jobs:
        print(f"{j['kind']:4s} {j['name']:10s} {j['circuits']:4d} {j['K']:3d} {j['shots']:5d} {j['cost']:9.1f}  {j['note'][:78]}")
    lat_s = sum(j['cost'] for j in jobs if j['kind'] == 'LAT')
    acc_s = tot - lat_s
    print(f"TOTAL {len(jobs)} jobs: latency {lat_s:.0f} s + acceptance {acc_s:.0f} s = {tot:.0f} QPU-s "
          f"(quota left {quota_left:.0f} s -> {'FITS' if tot <= quota_left else 'DOES NOT FIT'}, margin {quota_left - tot:+.0f} s)")
    rows, cls, Xm, sig = design_power(tier)
    print(f"\nPre-registered latency predictions [ns] (increment over 1-bit if). Assumed sigma/case ~{sig:.0f} ns "
          f"(K=16, 4000 shots; scaled from the legacy stopwatch bootstrap)")
    print(f"{'probe':9s} {'ops':>4s} {'inp':>4s} {'dep':>4s} | {'H_ops':>7s} {'H_inputs':>8s} {'H_depth':>8s} | H_ops vs nearest alt (sigma)")
    for name, s, pr in rows:
        sep = min(abs(pr["H_ops"] - pr["H_inputs"]), abs(pr["H_ops"] - pr["H_depth"])) / sig
        print(f"{name:9s} {s['ops']:4d} {s['inputs']:4d} {s['depth']:4d} | {pr['H_ops']:7.0f} {pr['H_inputs']:8.0f} {pr['H_depth']:8.0f} | {sep:5.1f}")
    n, p = Xm.shape
    rank = np.linalg.matrix_rank(Xm)
    print(f"\nclass-regression design: {n} probes x {p} params (intercept + {len(cls)} classes), rank {rank}")
    if rank == p and n > p - 1:
        cov = np.linalg.inv(Xm.T @ Xm) * sig ** 2
        print("  per-class slope sigma [ns/op]: " + ", ".join(f"{c} {np.sqrt(cov[i + 1, i + 1]):.1f}" for i, c in enumerate(cls)))
        print(f"  intercept sigma {np.sqrt(cov[0, 0]):.0f} ns")
    else:
        # report which classes are identifiable: drop columns until full rank
        cols = [0]
        for i in range(1, p):
            if np.linalg.matrix_rank(Xm[:, cols + [i]]) > len(cols):
                cols.append(i)
        names = ["intercept"] + cls
        print(f"  RANK-DEFICIENT: identifiable only {[names[c] for c in cols]}; not separable: {[names[i] for i in range(p) if i not in cols]}")
        if len(cols) < n:
            cov = np.linalg.inv(Xm[:, cols].T @ Xm[:, cols]) * sig ** 2
            print("  sigma of identifiable terms [ns/op]: " + ", ".join(f"{names[c]} {np.sqrt(cov[i, i]):.1f}" for i, c in enumerate(cols)))


# ------------------------------------------------------------------ local
def get_target(name):
    if name == "fake":
        from qiskit_ibm_runtime.fake_provider import FakeKingston
        be = FakeKingston()
        SW.target_with_measure2(be, 1.884e-6)
        return be, "FakeKingston(+measure_2)"
    from qiskit_ibm_runtime import QiskitRuntimeService   # read-only fetch of the real target
    be = QiskitRuntimeService().backend(name)
    return be, f"{name} (live target)"


def roundtrip(isa):
    buf = io.BytesIO()
    qpy.dump(isa, buf)
    buf.seek(0)
    back = qpy.load(buf)[0]
    a = [(i.operation.name, str(getattr(i.operation, "rvalue", None) or getattr(i.operation, "condition", None))) for i in isa.data]
    b = [(i.operation.name, str(getattr(i.operation, "rvalue", None) or getattr(i.operation, "condition", None))) for i in back.data]
    return a == b


def local(only, target):
    be, tname = get_target(target)
    cat = catalogue()
    print(f"target: {tname}")
    allok = True
    for p in cat:
        if only and p["name"] not in only:
            continue
        K = 2 if p["fam"] == "LAT" else p.get("K", 1)
        qc = build_lat(p, K=2) if p["fam"] == "LAT" else build_acc(p, K=min(K, 2))
        g = guards(qc)
        N, bad, nb, pt = semantic_check(qc, p)
        st = structure(p)
        isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
        rt = roundtrip(isa)
        n_st = [n_ops(e) for k, e, _ in exprs_of(qc) if k == "store"][:1]
        ok = not g and bad == 0 and rt and (not n_st or True)
        allok &= ok
        print(f"{p['name']:9s} {p['fam']} ops={st['ops']:3d} depth={st['depth']:3d} inputs={st['inputs']:2d} "
              f"store_ops={n_st} | exhaustive {N - bad}/{N} ({nb} bits, P(cond)={pt:.2f}) | guards {g or 'ok'} | "
              f"transpile depth={isa.depth()} | QPY {'OK' if rt else 'MISMATCH'} | {'PASS' if ok else 'FAIL'}", flush=True)
        # recipe op count must equal what the circuit actually stores
        if n_st and p["name"] != "WVOTE27":
            exp = sum(count(p["recipe"]).values())
            assert n_st[0] == exp, (p["name"], n_st, exp)
    # multi-store acceptance probes
    for nm, k, m, _ in CONES:
        if only and nm not in only:
            continue
        qc, N, bad = build_cone(k, m)
        g = guards(qc)
        isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
        rt = roundtrip(isa)
        ok = not g and bad == 0 and rt
        allok &= ok
        print(f"{nm:9s} ACC cone {k}x{m}: stores={sum(1 for x in exprs_of(qc) if x[0]=='store')} "
              f"max_store_ops={max(n_ops(e) for kk,e,_ in exprs_of(qc) if kk=='store')} | exhaustive {N - bad}/{N} | guards {g or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | {'PASS' if ok else 'FAIL'}")
    for nm, nr, w, _ in REGS:
        if only and nm not in only:
            continue
        qc, N, bad = build_regs(nr, w)
        g = guards(qc)
        isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
        rt = roundtrip(isa)
        ok = not g and bad == 0 and rt
        allok &= ok
        print(f"{nm:9s} ACC regs {nr}x{w}: exhaustive {N - bad}/{N} | guards {g or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | {'PASS' if ok else 'FAIL'}")
    for nm, nops, _ in SIMDACC:
        if only and nm not in only:
            continue
        qc, N, bad = build_simd_acc(nops)
        g = guards(qc)
        isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
        rt = roundtrip(isa)
        ok = not g and bad == 0 and rt
        allok &= ok
        print(f"{nm:9s} ACC simd {nops} reg ops (Uint16): exhaustive-lane0 {N - bad}/{N} | guards {g or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | {'PASS' if ok else 'FAIL'}")
    for nm, nr, w, nread in REGB:
        if only and nm not in only:
            continue
        for wh in ("A", "B"):
            qc = regb_circuits(nm)[0 if wh == "A" else 1]
            g = guards(qc)
            N, bad = check_regs_det(qc, nr, w, nread, wh)
            isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
            rt = roundtrip(isa)
            ok = not g and bad == 0 and rt
            allok &= ok
            info = regb_info(nr, w, nread)
            print(f"{nm:9s} REG{wh} read_regs={info['read_regs']} read_bits={info['read_bits']} declared={info['declared_regs']} total_bits={info['total_bits']} "
                  f"| exhaustive lane0 + pattern {N - bad}/{N} (expect out={reg_patterns(nr, nread)[wh][1]}) | guards {g or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | {'PASS' if ok else 'FAIL'}")
    for sp in SIZE + SIZE_MID:
        if only and sp["name"] not in only:
            continue
        for wh in ("A", "B"):
            qc = size_circuits(sp["name"])[0 if wh == "A" else 1]
            g = guards(qc)
            N, bad = check_soup(qc, sp["ops"], sp["shape"], wh) if sp["kind"] == "soup" else check_shan(qc, sp["seeds"], wh)
            isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
            rt = roundtrip(isa)
            mb, mt = cone_metrics(qc), cone_metrics(isa)
            ok = not g and bad == 0 and rt and mb == mt
            allok &= ok
            print(f"{sp['name']:11s} SIZ{wh} regs={mt['cone_regs']} stores={mt['stores_in_cone']} max_store_ops={mt['max_store_ops']} cone_ops={mt['cone_ops']} depth={mt['depth']} "
                  f"| exhaustive {N - bad}/{N} | guards {g or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | transpiled==builder {'yes' if mb == mt else 'NO'} | {'PASS' if ok else 'FAIL'}", flush=True)
    for nm, nr, w, nread in REGX:
        if only and nm not in only:
            continue
        for wh in ("A", "B"):
            qc = regb_circuits(nm)[0 if wh == "A" else 1]
            g = guards(qc)
            N, bad = check_regs_det(qc, nr, w, nread, wh)
            isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
            rt = roundtrip(isa)
            mt = cone_metrics(isa)
            ok = not g and bad == 0 and rt and mt["cone_regs"] == nread
            allok &= ok
            print(f"{nm:11s} REG{wh} cone_regs={mt['cone_regs']} | exhaustive {N - bad}/{N} | guards {g or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | {'PASS' if ok else 'FAIL'}", flush=True)
    for sp in SPLIT:
        if only and sp["name"] not in only:
            continue
        for wh in ("A", "B"):
            qc = build_split(sp, wh)
            g = guards(qc)
            N, bad = check_split(qc, sp, wh)
            isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
            rt = roundtrip(isa)
            rep = store_report(isa)
            merged = sorted(rep) != sorted([(t, len(set(o)), len(o) - 1) for t, o in sp["stores"]])
            ok = not g and bad == 0 and rt and not merged
            allok &= ok
            m = split_metrics(sp)
            print(f"{sp['name']:13s} SPL{wh} stores(transpiled)={len(rep)} {[(t, n) for t, n, _ in rep]} fanin_max={m['fanin_max']} cone={m['cone']} | exhaustive lane0 2^{sp['nr']} + pattern {N - bad}/{N} "
                  f"| guards {g or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | stores {'MERGED' if merged else 'intact'} | {'PASS' if ok else 'FAIL'}", flush=True)
    import tiers_v2 as T2
    allok &= T2.local_checks(be, only)
    print("ALL PASS" if allok else "FAILURES PRESENT")
    return allok


def acc_circuit(nbits, name):
    q = QuantumRegister(nbits + 1, "q")
    out, X, L = ClassicalRegister(1, "out"), ClassicalRegister(nbits, "Xr"), ClassicalRegister(1, "L")
    qc = QuantumCircuit(q, out, X, L, name=name)
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    for i in range(nbits):
        qc.h(q[i])
        qc.append(MidCircuitMeasure(), [q[i]], [X[i]])
    return qc, q, out, X, L


def build_cone(k, m):
    recs = cone_spec(k, m, 1000 + k * 100 + m)
    qc, q, out, X, L = acc_circuit(8, f"C{k}x{m}")
    regs = []
    for i in range(k):
        r = ClassicalRegister(1, f"s{i}")
        qc.add_register(r)
        qc.store(r[0], to_expr(recs[i], {"X": list(X)}))
        regs.append(r)
    e = expr.lift(regs[0][0])
    for r in regs[1:]:
        e = expr.bit_xor(e, expr.lift(r[0]))
    qc.store(L[0], e)
    with qc.if_test(expr.lift(L[0])):
        qc.x(q[8])
    qc.measure(q[8], out[0])
    # exhaustive: 2^8 inputs, reference = xor of numpy-evaluated recipes
    N = 256
    idx = np.arange(N, dtype=np.uint64)
    bits = np.stack([((idx >> np.uint64(i)) & np.uint64(1)).astype(bool) for i in range(8)], 1)
    ref = np.zeros(N, bool)
    for rc in recs:
        ref ^= to_np_wrap(rc, bits, {}).astype(bool)
    benv = {X[i]: bits[:, i] for i in range(8)}
    env = dict(benv)
    for kk, e, lv in exprs_of(qc):
        if kk == "store":
            env[lv.var] = veval(e, env, {})
        else:
            got = veval(e, env, {})
    got = np.asarray(got).astype(bool)
    return qc, N, int(np.sum(got != ref))


def build_regs(nr, w):
    """nr registers of w bits, each measured; one store sums parity of their lane-0 bits via xor of registers."""
    nb = nr * w
    q = QuantumRegister(nb + 1, "q")
    out, L = ClassicalRegister(1, "out"), ClassicalRegister(1, "L")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(nr)]
    qc = QuantumCircuit(q, out, L, *regs, name=f"R{nr}x{w}")
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    for i, r in enumerate(regs):
        for j in range(w):
            qc.h(q[i * w + j])
            qc.append(MidCircuitMeasure(), [q[i * w + j]], [r[j]])
    e = expr.bit_xor(regs[0], regs[1]) if nr > 1 else expr.lift(regs[0])
    for r in regs[2:]:
        e = expr.bit_xor(e, r)
    S = ClassicalRegister(w, "S")
    qc.add_register(S)
    qc.store(S, e)
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[nb])
    qc.measure(q[nb], out[0])
    # semantic: random + corner sampling is not exhaustive for nr*w>24 bits -> check lane 0 over its 2^nr inputs
    N = 1 << nr
    idx = np.arange(N, dtype=np.uint64)
    env, ref = {}, np.zeros(N, bool)
    for i, r in enumerate(regs):
        b0 = ((idx >> np.uint64(i)) & np.uint64(1)).astype(bool)
        ref ^= b0
        env[r.name] = b0.astype(np.uint64)               # lane 0 only; upper lanes are independent in XOR
    st = [x for x in exprs_of(qc) if x[0] == "store"][0]
    s = veval(st[1], env, {})
    got = (np.asarray(s) & np.uint64(1)).astype(bool)
    return qc, N, int(np.sum(got != ref))


def build_simd_acc(nops):
    rng = random.Random(nops)
    rec = simd(nops, rng, regs=("A", "B", "C"))
    qc, q, out, _, _ = None, None, None, None, None
    q = QuantumRegister(48 + 1, "q")
    out = ClassicalRegister(1, "out")
    regs = {n: ClassicalRegister(16, n) for n in "ABC"}
    S = ClassicalRegister(16, "S")
    qc = QuantumCircuit(q, out, *regs.values(), S, name=f"S{nops}")
    from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    for i, n in enumerate("ABC"):
        for j in range(16):
            qc.h(q[i * 16 + j])
            qc.append(MidCircuitMeasure(), [q[i * 16 + j]], [regs[n][j]])
    qc.store(S, to_expr(rec, regs))
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[48])
    qc.measure(q[48], out[0])
    # lanes independent: lane-0 truth table over A0,B0,C0 (8 assignments) is exhaustive for lane 0
    idx = np.arange(8, dtype=np.uint64)
    env = {n: ((idx >> np.uint64(i)) & np.uint64(1)) for i, n in enumerate("ABC")}
    st = [x for x in exprs_of(qc) if x[0] == "store"][0]
    got = (np.asarray(veval(st[1], env, {})) & np.uint64(1)).astype(bool)
    ref = to_np_wrap(rec, None, env).astype(np.uint64) & np.uint64(1)
    return qc, 8, int(np.sum(got != ref.astype(bool)))


# ------------------------------------------------------------------ regbisect tier (round 2)
REGB = [("B_R4x8", 4, 8, 4), ("B_R6x8", 6, 8, 6), ("B_R8x8", 8, 8, 8), ("B_R10x8", 10, 8, 10),
        ("B_R12x8c", 12, 8, 12),      # positive control (R12x8 failed 6057 in round 1)
        ("B_R6x16", 6, 16, 6), ("B_R3x32", 3, 32, 3),     # same 96 bits as R12x8, fewer registers
        ("B_R12x2", 12, 2, 12),       # 12 registers, only 24 bits
        ("B_R12x8u", 12, 8, 2)]       # 12 registers declared+measured, store reads only 2
REGB_SHOTS = 200
# facts from earlier jobs used to bracket thresholds: (label, regs read by the store, bits read, outcome)
REG_PRIOR = [("F1_regs150 (150 1-bit regs declared, store reads 1)", 1, 1, True), ("A96t_n16 (one 16-bit reg)", 1, 16, True),
             ("C6x16 (consumer reads 6 1-bit regs)", 6, 6, True), ("R12x8 round 1 (12 x 8-bit regs read)", 12, 96, False)]


def regb_info(nr, w, nread):
    return dict(read_regs=nread, read_bits=nread * w, declared_regs=nr + 3, total_bits=nr * w + w + 2,
                measured_bits=nr * w, measured_regs=nr)


def reg_patterns(nr, nread):
    """lane-0 bit per register for pattern A (expected out=1) and B (expected out=0), parity over the registers the store reads."""
    if nread >= 4:
        a, b = 3, 4
    elif nread == 3:
        a, b = 3, 2
    else:
        a, b = 1, 2
    mk = lambda k: [1 if i < k else 0 for i in range(nr)]  # noqa: E731
    return {"A": (mk(a), 1), "B": (mk(b), 0)}


def reg_upper_bits(nr, w):
    r = random.Random(1000 + nr * 37 + w)
    return [[0] + [r.randrange(2) for _ in range(w - 1)] for _ in range(nr)]


def build_regs_det(nr, w, nread, which, ibm=True, name=None):
    """Like build_regs (R12x8) but sources are PREPARED basis states (X then measure_2), so the store has a known value:
    out = XOR of lane-0 bits of the first `nread` registers. Classical bits: nr*w measured + S(w) + L(1, unused, as in R12x8) + out(1)."""
    pat, _ = reg_patterns(nr, nread)[which]
    up = reg_upper_bits(nr, w)
    nb = nr * w
    q = QuantumRegister(nb + 1, "q")
    out, L = ClassicalRegister(1, "out"), ClassicalRegister(1, "L")
    regs = [ClassicalRegister(w, f"G{i}") for i in range(nr)]
    qc = QuantumCircuit(q, out, L, *regs, name=name or f"B_R{nr}x{w}_{which}")
    if ibm:
        from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    for i, r in enumerate(regs):
        for j in range(w):
            if (pat[i] if j == 0 else up[i][j]):
                qc.x(q[i * w + j])
            qc.append(MidCircuitMeasure(), [q[i * w + j]], [r[j]]) if ibm else qc.measure(q[i * w + j], r[j])
    e = expr.bit_xor(regs[0], regs[1])
    for r in regs[2:nread]:
        e = expr.bit_xor(e, r)
    S = ClassicalRegister(w, "S")
    qc.add_register(S)
    qc.store(S, e)
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[nb])
    qc.measure(q[nb], out[0])
    return qc


def check_regs_det(qc, nr, w, nread, which):
    """(a) exhaustive over lane-0 bits of the registers read (2^nread) vs parity; (b) the prepared pattern gives the expected out."""
    st = [x for x in exprs_of(qc) if x[0] == "store"][0]
    N = 1 << nread
    idx = np.arange(N, dtype=np.uint64)
    env, ref = {}, np.zeros(N, bool)
    for i in range(nr):
        b0 = ((idx >> np.uint64(i)) & np.uint64(1)).astype(bool) if i < nread else np.zeros(N, bool)
        ref ^= b0 if i < nread else np.zeros(N, bool)
        env[f"G{i}"] = b0.astype(np.uint64)
    bad = int(np.sum((np.asarray(veval(st[1], env, {})) & np.uint64(1)).astype(bool) != ref))
    pat, exp_out = reg_patterns(nr, nread)[which]
    up = reg_upper_bits(nr, w)
    env2 = {f"G{i}": np.array([sum((pat[i] if j == 0 else up[i][j]) << j for j in range(w))], np.uint64) for i in range(nr)}
    got = int(np.asarray(veval(st[1], env2, {}))[0]) & 1
    bad += int(got != exp_out)
    return N + 1, bad


def good_qubits(be, n):
    """n lowest-readout-error operational qubits (live properties, read-only); [0] is the best (used for `out`)."""
    props = be.properties()
    cand = []
    for i in range(be.num_qubits):
        try:
            if not props.is_qubit_operational(i):
                continue
            ro = props.readout_error(i)
            if ro is None or not np.isfinite(ro):
                continue
        except Exception:  # noqa: BLE001
            continue
        cand.append((ro, i))
    cand.sort()
    if len(cand) < n:
        raise RuntimeError(f"only {len(cand)} operational qubits for {n}")
    return [i for _, i in cand[:n]]


def regb_circuits(name):
    spec = next(x for x in list(REGB) + list(REGX) if x[0] == name)
    _, nr, w, nread = spec
    return [build_regs_det(nr, w, nread, wh, name=f"{name}_{wh}") for wh in ("A", "B")]


def regb_predictions():
    """Outcome predicted by each hypothesis, given prior pass/fail facts: P / F / ? (threshold not pinned by data)."""
    hyps = {"H_read_regs": lambda r: r["read_regs"], "H_read_bits": lambda r: r["read_bits"],
            "H_total_bits": lambda r: r["total_bits"], "H_declared_regs": lambda r: r["declared_regs"],
            "H_measured_regs": lambda r: r["measured_regs"]}
    prior = {"H_read_regs": [(a, o) for _, a, _, o in REG_PRIOR], "H_read_bits": [(b, o) for _, _, b, o in REG_PRIOR],
             "H_measured_regs": [(1, True), (12, False)]}
    # total-bits / declared-regs facts: F1_regs150 declares 150 regs/>=150 bits and passed (H_total_bits, H_declared_regs refuted
    # for a plain threshold: R12x8 fails with 106 bits / 15 regs but F1 passes with >=150 / 153).
    rows = []
    for nm, nr, w, nread in REGB:
        info = regb_info(nr, w, nread)
        pr = {}
        for h, f in hyps.items():
            if h in prior:
                m = f(info)
                pmax = max(a for a, o in prior[h] if o)
                fmin = min(a for a, o in prior[h] if not o)
                pr[h] = "P" if m <= pmax else ("F" if m >= fmin else "?")
            else:
                pr[h] = "(refuted by F1_regs150)"
        rows.append((nm, info, pr))
    return rows


def regb_predictions_text():
    L = ["Pre-registered predictions for regbisect (P pass / F fail / ? threshold unknown; monotone threshold per hypothesis).",
         "Known facts used: " + "; ".join(f"{l}: {'pass' if o else 'FAIL'}" for l, _, _, o in REG_PRIOR),
         "H_total_bits and H_declared_regs are already contradicted by F1_regs150 (150 declared registers passed) unless 1-bit registers are special;",
         "kept for completeness. 'read' = registers/bits operated on by the single store; 'declared' = ClassicalRegisters in the circuit (G..., S, L, out).",
         f"{'probe':9s} {'read_regs':>9s} {'read_bits':>9s} {'declared':>8s} {'tot_bits':>8s} | {'H_read_regs':>11s} {'H_read_bits':>11s} {'H_meas_regs':>11s}"]
    for nm, i, pr in regb_predictions():
        L.append(f"{nm:9s} {i['read_regs']:9d} {i['read_bits']:9d} {i['declared_regs']:8d} {i['total_bits']:8d} | {pr['H_read_regs']:>11s} {pr['H_read_bits']:>11s} {pr['H_measured_regs']:>11s}")
    L.append("Discriminating outcomes: R12x2 F => count of registers read (not bits); R6x16/R3x32 F => bits read (not count); R12x8u F => declared/measured "
             "registers matter even if unread; R4..R10 give the threshold under H_read_regs.")
    return "\n".join(L)


# ------------------------------------------------------------------ split tier (round 3): per-store vs per-cone register fan-in
def _G(a, b):
    return [f"G{i}" for i in range(a, b)]


SPLIT = [
    dict(name="S_12split6", nr=12, w=2, stores=[("S1", _G(0, 6)), ("S2", _G(6, 12)), ("S", ["S1", "S2"])], A=[0, 1, 6], B=[0, 1, 6, 7]),
    dict(name="S_24split3x8", nr=24, w=2, stores=[("S1", _G(0, 8)), ("S2", _G(8, 16)), ("S3", _G(16, 24)), ("S", ["S1", "S2", "S3"])],
         A=[0, 8, 16], B=[0, 1, 8, 16]),
    dict(name="S_12chain", nr=12, w=2, stores=[("S1", _G(0, 6)), ("S", ["S1"] + _G(6, 12))], A=[0, 1, 6], B=[0, 1, 6, 7]),
    dict(name="S_10overlap", nr=10, w=2, stores=[("S1", _G(0, 6)), ("S2", _G(4, 10)), ("S", ["S1", "S2"])], A=[0, 1, 6], B=[0, 1, 6, 7]),
    dict(name="S_R12x2c", nr=12, w=2, stores=[("S", _G(0, 12))], A=[0, 1, 2], B=[0, 1, 2, 3]),   # control: single store, must fail 6057
]
SPLIT_SHOTS = 200


def split_metrics(sp):
    """fanin_max: distinct registers read by the widest store; cone: distinct MEASURED registers reachable from the final store;
    total_reads: sum of distinct registers read over all stores."""
    by = dict(sp["stores"])

    def reach(n, seen):
        if n.startswith("G"):
            seen.add(n)
        else:
            for m in by[n]:
                reach(m, seen)
        return seen
    return dict(fanin_max=max(len(set(ops)) for _, ops in sp["stores"]), cone=len(reach("S", set())),
                total_reads=sum(len(set(ops)) for _, ops in sp["stores"]), n_stores=len(sp["stores"]),
                ops=sum(len(ops) - 1 for _, ops in sp["stores"]), measured_regs=sp["nr"], total_bits=sp["nr"] * sp["w"] + sp["w"] * len(sp["stores"]) + 2)


def split_expected(sp, ones):
    """expected out from the recipe: evaluate stores on lane-0 bits (python ints)."""
    val = {f"G{i}": int(i in ones) for i in range(sp["nr"])}
    for tgt, ops in sp["stores"]:
        v = 0
        for o in ops:
            v ^= val[o]
        val[tgt] = v
    return val["S"]


def build_split(sp, which, ibm=True):
    ones = set(sp[which])
    nr, w = sp["nr"], sp["w"]
    up = reg_upper_bits(nr, w)
    nb = nr * w
    q = QuantumRegister(nb + 1, "q")
    out, L = ClassicalRegister(1, "out"), ClassicalRegister(1, "L")
    regs = {f"G{i}": ClassicalRegister(w, f"G{i}") for i in range(nr)}
    qc = QuantumCircuit(q, out, L, *regs.values(), name=f"{sp['name']}_{which}")
    if ibm:
        from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    for i in range(nr):
        for j in range(w):
            if ((1 if i in ones else 0) if j == 0 else up[i][j]):
                qc.x(q[i * w + j])
            qc.append(MidCircuitMeasure(), [q[i * w + j]], [regs[f"G{i}"][j]]) if ibm else qc.measure(q[i * w + j], regs[f"G{i}"][j])
    for tgt, ops in sp["stores"]:
        r = ClassicalRegister(w, tgt)
        qc.add_register(r)
        regs[tgt] = r
        e = expr.bit_xor(regs[ops[0]], regs[ops[1]])
        for o in ops[2:]:
            e = expr.bit_xor(e, regs[o])
        qc.store(r, e)
    with qc.if_test(expr.lift(regs["S"][0])):
        qc.x(q[nb])
    qc.measure(q[nb], out[0])
    return qc


def store_report(qc):
    """[(target, distinct registers read, ops)] for every store actually present in `qc` (works on transpiled circuits)."""
    rep = []
    for i in qc.data:
        op = i.operation
        if op.name != "store":
            continue
        names = set()

        def walk(x):
            if isinstance(x, expr.Var):
                v = x.var
                names.add(getattr(v, "name", None) or str(v))
            for a in ("left", "right", "operand", "target", "index"):
                if hasattr(x, a):
                    walk(getattr(x, a))
        walk(op.rvalue)
        tgt = getattr(op.lvalue.var, "name", str(op.lvalue.var))
        rep.append((tgt, len(names), n_ops(op.rvalue)))
    return rep


def check_split(qc, sp, which, chunk=1 << 20):
    """Exhaustive over lane-0 bits of all registers (chunked) with the actual circuit expressions vs recipe parity, plus the prepared pattern."""
    nr = sp["nr"]
    N = 1 << nr
    bad = 0
    st = [x for x in exprs_of(qc) if x[0] == "store"]
    gmask = [(i, np.uint64(1 << i)) for i in range(nr)]
    for base in range(0, N, chunk):
        idx = np.arange(base, min(N, base + chunk), dtype=np.uint64)
        env = {f"G{i}": ((idx >> np.uint64(i)) & np.uint64(1)) for i in range(nr)}
        for kk, e, lv in st:
            env[lv.var.name] = veval(e, env, {})
        got = (np.asarray(env["S"]) & np.uint64(1)).astype(bool)
        ref = np.zeros(len(idx), bool)
        by = dict(sp["stores"])
        val = {k: v.astype(bool) for k, v in env.items() if k.startswith("G")}
        for tgt, ops in sp["stores"]:
            v = np.zeros(len(idx), bool)
            for o in ops:
                v ^= val[o]
            val[tgt] = v
        bad += int(np.sum(got != val["S"]))
    # pattern
    ones = set(sp[which])
    up = reg_upper_bits(nr, sp["w"])
    env = {f"G{i}": np.array([sum(((1 if i in ones else 0) if j == 0 else up[i][j]) << j for j in range(sp["w"]))], np.uint64) for i in range(nr)}
    for kk, e, lv in st:
        env[lv.var.name] = veval(e, env, {})
    bad += int((int(np.asarray(env["S"])[0]) & 1) != split_expected(sp, ones))
    exp_out = 1 if which == "A" else 0
    assert split_expected(sp, ones) == exp_out, (sp["name"], which)
    return N + 1, bad


def split_circuits(name):
    sp = next(x for x in SPLIT if x["name"] == name)
    return [build_split(sp, "A"), build_split(sp, "B")]


SPLIT_PRIOR = [  # (label, fanin_max, cone, total_reads, accepted)  from round 2 and earlier
    ("R10x8", 10, 10, 10, True), ("R8x8", 8, 8, 8, True), ("R12x8u (reads 2)", 2, 2, 2, True),
    ("R12x2", 12, 12, 12, False), ("R12x8c", 12, 12, 12, False), ("R12x8 round 1", 12, 12, 12, False)]


def split_predictions():
    rows = []
    for sp in SPLIT:
        m = split_metrics(sp)
        pr = {}
        for h, key in (("H_store", "fanin_max"), ("H_cone", "cone"), ("H_store_cone", "total_reads")):
            pmax = max(p[{"fanin_max": 1, "cone": 2, "total_reads": 3}[key]] for p in SPLIT_PRIOR if p[4])
            fmin = min(p[{"fanin_max": 1, "cone": 2, "total_reads": 3}[key]] for p in SPLIT_PRIOR if not p[4])
            v = m[key]
            pr[h] = "P" if v <= pmax else ("F" if v >= fmin else "?")
        rows.append((sp["name"], m, pr))
    return rows


def split_predictions_text():
    L = ["Pre-registered predictions for split tier. Known: pass up to 10 registers read by one store (R10x8), fail at 12; theta in (10, 12].",
         "H_store: 6057 iff some single store reads >= theta distinct registers (fan-in).",
         "H_cone: iff the dependency cone of the `if` reaches >= theta distinct MEASURED registers (stores flattened).",
         "H_store_cone: iff the SUM of distinct registers read over all stores >= theta (every read counts, incl. stores reading stores).",
         f"{'probe':13s} {'stores':>6s} {'fanin_max':>9s} {'cone':>5s} {'total_reads':>11s} | {'H_store':>8s} {'H_cone':>7s} {'H_store_cone':>12s}"]
    for nm, m, pr in split_predictions():
        L.append(f"{nm:13s} {m['n_stores']:6d} {m['fanin_max']:9d} {m['cone']:5d} {m['total_reads']:11d} | {pr['H_store']:>8s} {pr['H_cone']:>7s} {pr['H_store_cone']:>12s}")
    L.append("Decisive: S_12split6 / S_12chain / S_24split3x8 accepted => limit is per store (bypass works, scales to 24 registers); S_10overlap F with "
             "split6 accepted would point to total reads; S_10overlap P + split6 F => cone. S_R12x2c is the control (must fail 6057).")
    return "\n".join(L)


def analyse_split(man, res, outdir):
    L = [f"# QPU results split, plan {man['plan_hash']} on {man['backend']}",
         f"submitted {man['submitted_at']}; est {man['est_total_s']:.0f} QPU-s; {SPLIT_SHOTS} shots per pattern circuit", "",
         "Probe = G registers (2 bits, prepared basis states, measure_2) -> several stores of XOR over registers -> final `S` -> `if S[0]` -> X on out qubit. "
         "Pattern A expects out=1, B expects out=0. fanin_max = distinct registers read by the widest store; cone = measured registers reachable from the final store; "
         "total_reads = sum of fan-ins. Stores per probe as TRANSPILED are in the manifest (`store_report`).", "",
         "| probe | stores | fanin_max | cone | total_reads | result | code | A: P(out=1) [CI] | B: P(out=0) [CI] | verified |", "|---|---|---|---|---|---|---|---|---|---|"]
    keys = {"H_store": "fanin_max", "H_cone": "cone", "H_store_cone": "total_reads"}
    pts = {h: [(p[{"fanin_max": 1, "cone": 2, "total_reads": 3}[k]], p[4]) for p in SPLIT_PRIOR] for h, k in keys.items()}
    outcome = {}
    for rec in man["jobs"]:
        sp = next(x for x in SPLIT if x["name"] == rec["name"])
        m = split_metrics(sp)
        r = res[rec["name"]]
        acc = r["status"] == "DONE"
        ca = cb = ver = ""
        if acc:
            z = np.load(os.path.join(outdir, r["npz"]))
            oa, ob = z[f"out_{rec['name']}_A"].astype(bool).ravel(), z[f"out_{rec['name']}_B"].astype(bool).ravel()
            ka, kb = int(oa.sum()), int((~ob).sum())
            la, lb = wilson(ka, len(oa)), wilson(kb, len(ob))
            ca, cb = f"{ka / len(oa):.3f} [{la[0]:.3f},{la[1]:.3f}]", f"{kb / len(ob):.3f} [{lb[0]:.3f},{lb[1]:.3f}]"
            ver = "YES" if (la[0] > 0.7 and lb[0] > 0.7) else "NO"
        L.append(f"| {rec['name']} | {m['n_stores']} | {m['fanin_max']} | {m['cone']} | {m['total_reads']} | {'ACCEPTED' if acc else r['status']} | {r['code'] or ''} | {ca} | {cb} | {ver} |")
        if acc or r["code"] == 6057:
            outcome[rec["name"]] = acc
            for h, k in keys.items():
                pts[h].append((m[k], acc))
    L += ["", "## Threshold consistency per hypothesis (fail iff metric >= theta)", "| hypothesis | consistent with all data | theta in (pass max, fail min] |", "|---|---|---|"]
    live = []
    for h, v in pts.items():
        pmax = max([a for a, o in v if o], default=-1)
        fmin = min([a for a, o in v if not o], default=1e9)
        ok = pmax < fmin
        L.append(f"| {h} | {'yes' if ok else 'NO'} | {f'({pmax}, {fmin}]' if ok else f'pass at {pmax} >= fail at {fmin}'} |")
        if ok:
            live.append(h)
    L += ["", "### Verdict"]
    ctrl = outcome.get("S_R12x2c")
    if ctrl is True:
        L.append("- CONTROL PASSED (S_R12x2c accepted): the round-2 failure did not reproduce; treat this round as void/needs rerun.")
    s6, ch, s24, ov = (outcome.get(k) for k in ("S_12split6", "S_12chain", "S_24split3x8", "S_10overlap"))
    if s6 is True and ch is True:
        L.append("- Splitting works: the limit is PER STORE (fan-in), not per cone. " + ("It scales: 24 registers via 3 stores of 8 accepted." if s24 else
                 "24-register 3x8 variant " + ("FAILED (another limit: total registers/cone at larger scale)." if s24 is False else "has no result.")))
    elif s6 is False:
        L.append("- S_12split6 FAILED: splitting does not bypass; limit follows the cone or total reads (see consistency table).")
    if ch is False and s6 is True:
        L.append("- Chain failed while 2-way split passed: a store reading a store + 6 registers (7 operands) is already too many? check fan-in 7 vs theta.")
    if ov is not None:
        L.append(f"- S_10overlap (cone 10 registers, total reads 14): {'ACCEPTED' if ov else 'FAILED'}. G4 and G5 cancel algebraically under XOR, so a folding compiler sees an 8-register cone; "
                 "this probe therefore cannot separate cone from total reads unless the compiler is known not to fold (see the non-foldable overlap probe in the later tiers).")
    L.append("- Hypotheses still consistent with all data: " + (", ".join(live) if live else "none"))
    txt = "\n".join(L) + "\n"
    with open(os.path.join(outdir, "RESULTS_QPU_split.md"), "w") as f:
        f.write(txt)
    return txt


# ------------------------------------------------------------------ size tier (round 4): second 6057 trigger + replication
OP_DEF = ("op = one Binary, Unary or Index node of a store/if expression tree, counted on the TRANSPILED circuit; casts and literal "
          "nodes count 0; each use of a sub-expression counts again (tree, not DAG); depth = longest root-to-leaf chain of such nodes.")


def tree_ops(e):
    if isinstance(e, expr.Binary):
        return 1 + tree_ops(e.left) + tree_ops(e.right)
    if isinstance(e, expr.Unary):
        return 1 + tree_ops(e.operand)
    if isinstance(e, expr.Index):
        return 1 + tree_ops(e.target) + tree_ops(e.index)
    if isinstance(e, expr.Cast):
        return tree_ops(e.operand)
    return 0


def tree_depth(e):
    if isinstance(e, expr.Binary):
        return 1 + max(tree_depth(e.left), tree_depth(e.right))
    if isinstance(e, expr.Unary):
        return 1 + tree_depth(e.operand)
    if isinstance(e, expr.Index):
        return 1 + max(tree_depth(e.target), tree_depth(e.index))
    if isinstance(e, expr.Cast):
        return tree_depth(e.operand)
    return 0


def cone_metrics(qc):
    """Counted from the (transpiled) circuit: measured registers, stores and ops in the dependency cone of the first `if`."""
    measured = set()
    for i in qc.data:
        if i.operation.name in ("measure_2", "measure"):
            for cb in i.clbits:
                for reg, _ in qc.find_bit(cb).registers:
                    measured.add(reg.name)

    def key_of(var, as_store):
        if isinstance(var, Clbit):
            idx = qc.find_bit(var).index
            regs = qc.find_bit(var).registers
            if as_store or not regs:
                return ("bit", idx)
            return ("bit", idx), ("reg", regs[0][0].name)
        return ("reg", getattr(var, "name", str(var)))

    stores = {}
    for i in qc.data:
        op = i.operation
        if op.name == "store":
            stores[key_of(op.lvalue.var, True)] = op.rvalue

    def leaves(e, acc):
        if isinstance(e, expr.Var):
            acc.append(key_of(e.var, False))
        for a in ("left", "right", "operand", "target", "index"):
            if hasattr(e, a):
                leaves(getattr(e, a), acc)
        return acc

    conds = [i.operation.condition for i in qc.data if i.operation.name == "if_else"]
    seen, regs_in_cone, ops_total, max_ops, max_depth = set(), set(), 0, 0, 0
    leaf_refs = 0

    def visit(e):
        nonlocal ops_total, max_ops, max_depth, leaf_refs
        for k in leaves(e, []):
            ks = k if isinstance(k[0], tuple) else (k,)
            hit = next((x for x in ks if x in stores), None)
            if hit is not None:
                if hit not in seen:
                    seen.add(hit)
                    se = stores[hit]
                    o = tree_ops(se)
                    ops_total += o
                    max_ops = max(max_ops, o)
                    max_depth = max(max_depth, tree_depth(se))
                    visit(se)
            else:
                leaf_refs += 1
                for x in ks:
                    if x[0] == "reg" and x[1] in measured:
                        regs_in_cone.add(x[1])
    visit(conds[0])
    return dict(cone_regs=len(regs_in_cone), cone_ops=ops_total, max_store_ops=max_ops, depth=max_depth,
                stores_in_cone=len(seen), leaf_refs=leaf_refs, cone_reg_names=sorted(regs_in_cone))


def shannon_expr(bits, seed):
    """Shannon-expansion random function of 6 bits, identical construction to the 2026-11-06 failing 103-op job."""
    rng = random.Random(seed)
    table = [rng.random() < 0.5 for _ in range(64)]

    def build(tbl, level):
        if all(tbl):
            return True
        if not any(tbl):
            return False
        half = len(tbl) // 2
        lo, hi = build(tbl[:half], level + 1), build(tbl[half:], level + 1)
        x = bits[level]
        if lo is False and hi is True:
            return x
        if lo is True and hi is False:
            return expr.bit_not(x)
        if lo is False:
            return expr.bit_and(x, hi)
        if hi is False:
            return expr.bit_and(expr.bit_not(x), lo)
        if lo is True:
            return expr.bit_or(expr.bit_not(x), hi)
        if hi is True:
            return expr.bit_or(x, lo)
        return expr.bit_or(expr.bit_and(x, hi), expr.bit_and(expr.bit_not(x), lo))
    e = build(table, 0)
    assert not isinstance(e, bool)
    return e, table


def shannon_ref(table, assign):
    """independent reference: value of the seeded table at a 6-bit assignment (bit 0 = most significant)."""
    return table[sum(b << (5 - l) for l, b in enumerate(assign))]


def best_patterns_shan(seeds):
    """Assignments (6 bits) giving final=1 (A) and 0 (B), maximising stability against single-bit readout flips."""
    tabs = [shannon_ref.__defaults__ and random.Random(s) for s in seeds]
    res = {}
    vals = {}
    for a in range(64):
        assign = [(a >> (5 - l)) & 1 for l in range(6)]
        f = assign[1]            # the trailing `^ m[1]` of the original construction
        for s in seeds:
            rng = random.Random(s)
            table = [rng.random() < 0.5 for _ in range(64)]
            f ^= int(shannon_ref(table, assign))
        vals[a] = f
    def stab(a):
        return sum(vals[a ^ (1 << j)] == vals[a] for j in range(6))
    for tgt, w in ((1, "A"), (0, "B")):
        cand = [a for a in range(64) if vals[a] == tgt]
        a = max(cand, key=lambda x: (stab(x), -x))
        res[w] = ([(a >> (5 - l)) & 1 for l in range(6)], tgt)
    return res


def build_shan(seeds, which, name, ibm=True):
    """2026-11-06 construction (ibm_6057_repro.case_T): m(6) measured; slots s[0..n]; s[k] = shannon(seed k) for k<n, s[n] = s0 ^ m1 ^ s1 ^ ...; if s[n].
    Inputs are PREPARED (X on chosen qubits) so the output is known; n = len(seeds)."""
    n = len(seeds)
    assign, _ = best_patterns_shan(seeds)[which]
    q = QuantumRegister(7, "q")
    m = ClassicalRegister(6, "m")
    out = ClassicalRegister(1, "out")
    qc = QuantumCircuit(q, m, out, name=name)
    S = ClassicalRegister(n + 1, "s")
    qc.add_register(S)
    if ibm:
        from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    for i in range(6):
        if assign[i]:
            qc.x(q[i])
        qc.append(MidCircuitMeasure(), [q[i]], [m[i]]) if ibm else qc.measure(q[i], m[i])
    bits = [expr.lift(m[i]) for i in range(6)]
    for k, sd in enumerate(seeds):
        qc.store(S[k], shannon_expr(bits, sd)[0])
    acc = expr.bit_xor(S[0], m[1])
    for k in range(1, n):
        acc = expr.bit_xor(acc, S[k])
    qc.store(S[n], acc)
    with qc.if_test(expr.lift(S[n])):
        qc.x(q[6])
    qc.measure(q[6], out[0])
    return qc


def check_shan(qc, seeds, which):
    """exhaustive over all 64 assignments: circuit expressions (vectorised) vs seeded-table reference; plus prepared pattern."""
    n = len(seeds)
    idx = np.arange(64, dtype=np.uint64)
    m = next(r for r in qc.cregs if r.name == "m")
    env = {m[i]: (((idx >> np.uint64(5 - i)) & np.uint64(1)).astype(bool) if False else ((idx >> np.uint64(5 - i)) & np.uint64(1)).astype(bool)) for i in range(6)}
    # idx bit (5-i) holds assignment bit i  ->  assignment value a = idx, bit i = (a >> (5-i)) & 1
    S = next(r for r in qc.cregs if r.name == "s")
    for kk, e, lv in exprs_of(qc):
        if kk == "store":
            env[lv.var] = veval(e, env, {})
        else:
            got = np.asarray(veval(e, env, {})).astype(bool)
    ref = np.zeros(64, bool)
    for a in range(64):
        assign = [(a >> (5 - l)) & 1 for l in range(6)]
        f = assign[1]
        for s in seeds:
            rng = random.Random(s)
            table = [rng.random() < 0.5 for _ in range(64)]
            f ^= int(shannon_ref(table, assign))
        ref[a] = bool(f)
    bad = int(np.sum(got != ref))
    assign, tgt = best_patterns_shan(seeds)[which]
    a = sum(b << (5 - l) for l, b in enumerate(assign))
    bad += int(bool(ref[a]) != bool(tgt))
    return 65, bad


def soup_recipe(ops, shape):
    rng = random.Random(5000 + ops + (7 if shape == "chain" else 0))
    r = bool_soup(ops, 16, rng, shape)
    # non-degenerate: truth table balance and influence of every bit
    idx = np.arange(1 << 16, dtype=np.uint64)
    bits = np.stack([((idx >> np.uint64(i)) & np.uint64(1)).astype(bool) for i in range(16)], 1)
    t = to_np_wrap(r, bits, {}).astype(bool)
    assert 0.1 < t.mean() < 0.9, (ops, shape, t.mean())
    return r, t


def soup_patterns(t):
    """assignments (16 bits as int) with f=1 (A) and f=0 (B) maximising stability against single-bit flips."""
    idx = np.arange(1 << 16)
    stab = np.zeros(1 << 16, int)
    for j in range(16):
        stab += (t[idx ^ (1 << j)] == t)
    pa = int(np.argmax(np.where(t, stab, -1)))
    pb = int(np.argmax(np.where(~t, stab, -1)))
    return {"A": (pa, 1), "B": (pb, 0)}


def build_soup(ops, shape, which, ibm=True):
    r, t = soup_recipe(ops, shape)
    a, _ = soup_patterns(t)[which]
    q = QuantumRegister(17, "q")
    out, L = ClassicalRegister(1, "out"), ClassicalRegister(1, "L")
    X0, X1 = ClassicalRegister(8, "X0"), ClassicalRegister(8, "X1")
    qc = QuantumCircuit(q, out, L, X0, X1, name=f"Z_{shape}{ops}_{which}")
    if ibm:
        from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    bits = list(X0) + list(X1)
    for i in range(16):
        if (a >> i) & 1:
            qc.x(q[i])
        qc.append(MidCircuitMeasure(), [q[i]], [bits[i]]) if ibm else qc.measure(q[i], bits[i])
    qc.store(L[0], to_expr(r, {"X": bits}))
    with qc.if_test(expr.lift(L[0])):
        qc.x(q[16])
    qc.measure(q[16], out[0])
    return qc


def check_soup(qc, ops, shape, which):
    r, t = soup_recipe(ops, shape)
    idx = np.arange(1 << 16, dtype=np.uint64)
    benv = {}
    for rn in ("X0", "X1"):
        reg = next(x for x in qc.cregs if x.name == rn)
        for j, b in enumerate(reg):
            i = j + (0 if rn == "X0" else 8)
            benv[b] = ((idx >> np.uint64(i)) & np.uint64(1)).astype(bool)
    st = [x for x in exprs_of(qc) if x[0] == "store"][0]
    got = np.asarray(veval(st[1], benv, {})).astype(bool)
    bad = int(np.sum(got != t))
    a, tgt = soup_patterns(t)[which]
    bad += int(bool(t[a]) != bool(tgt))
    return (1 << 16) + 1, bad


SIZE = [  # name, kind, params
    dict(name="Z_tree104", kind="soup", ops=104, shape="tree"), dict(name="Z_tree128", kind="soup", ops=128, shape="tree"),
    dict(name="Z_chain104", kind="soup", ops=104, shape="chain"), dict(name="Z_chain128", kind="soup", ops=128, shape="chain"),
    dict(name="Z_shan84", kind="shan", seeds=[11]),             # Shannon form, 84 ops (28 NOT)
    dict(name="Z_shan96", kind="shan", seeds=[47]),             # 96 ops
    dict(name="Z_shan103", kind="shan", seeds=[10]),            # exact 2026-11-06 failing construction (case_T(1), seed 10, 103 ops)
    dict(name="Z_T3r2", kind="shan", seeds=[13, 11]),           # 2 stores of 79 + 84 ops
    dict(name="Z_T3r3", kind="shan", seeds=[11, 13, 15]),       # 3 stores 84, 79, 89
    dict(name="Z_T3r6", kind="shan", seeds=[11, 13, 15, 19, 32, 52]),   # 6 stores 79-89 (every store below 96; original T3 had 103/100/99)
]
SIZE_MID = [dict(name="Z_tree112", kind="soup", ops=112, shape="tree"), dict(name="Z_chain112", kind="soup", ops=112, shape="chain")]
REGX = [("B_R11x2", 11, 2, 11), ("B_R10x8r", 10, 8, 10), ("B_R12x2r", 12, 2, 12), ("X_R10x2", 10, 2, 10), ("X_R12x2", 12, 2, 12)]
SIZE_REG = ["B_R11x2", "B_R10x8r", "B_R12x2r"]
XREP_REG = ["X_R10x2", "X_R12x2"]
XREP_SPL = ["S_12split6", "S_10overlap"]
XREP_LAT = ["XORC15", "XORT15"]
SIZE_ORIGINALS = {"failing_103op_T1": "job id not published (T1_103op_rejected_6057), builder ibm_6057_repro.py case_T(1) seed 10",
                  "passing_83op": "BDD-based `_big_lut_expr` (qpu_probe.py T1_big_read_once), different construction"}


def size_circuits(name):
    sp = next(x for x in SIZE + SIZE_MID if x["name"] == name)
    if sp["kind"] == "soup":
        return [build_soup(sp["ops"], sp["shape"], w) for w in ("A", "B")]
    return [build_shan(sp["seeds"], w, f"{name}_{w}") for w in ("A", "B")]


SIZE_PRIOR = [  # label, metrics, accepted (all from earlier jobs; T1 replicated-or-not is the key unknown)
    ("A96t (single store, 96 ops, 1 reg)", dict(cone_regs=1, max_store_ops=96, cone_ops=96, depth=8), True),
    ("A96c (96-op chain)", dict(cone_regs=1, max_store_ops=96, cone_ops=96, depth=96), True),
    ("C6x16 (6 stores of 16 ops, cone 101)", dict(cone_regs=1, max_store_ops=16, cone_ops=101, depth=7), True),
    ("T1 103-op Shannon (2026-11-06)", dict(cone_regs=1, max_store_ops=103, cone_ops=104, depth=12), False),
    ("R10x8", dict(cone_regs=10, max_store_ops=9, cone_ops=9, depth=1), True),
    ("R12x2", dict(cone_regs=12, max_store_ops=11, cone_ops=11, depth=1), False),
]


def size_hyp_outcome(m):
    """predicted outcome per hypothesis ('P','F','?') for metrics m; registers rule applies to all hypotheses."""
    def reg(m):
        return "F" if m["cone_regs"] >= 12 else ("?" if m["cone_regs"] == 11 else "P")
    r = reg(m)
    def combine(extra):
        if r == "F" or extra == "F":
            return "F"
        return "?" if (r == "?" or extra == "?") else "P"
    so = "F" if m["max_store_ops"] >= 103 else ("P" if m["max_store_ops"] <= 96 else "?")
    co = "F" if m["cone_ops"] >= 104 else ("P" if m["cone_ops"] <= 101 else "?")
    dp = "P" if m["depth"] <= 96 else "?"
    return {"H_cone": combine("P"), "H_cone+store_ops": combine(so), "H_cone+cone_ops": combine(co), "H_cone+depth": combine(dp)}


def size_probe_metrics(name):
    """metrics of a probe from its BUILT circuit (then asserted equal to the transpiled circuit in build_plan)."""
    if name in SIZE_REG or name in XREP_REG:
        spec = next(x for x in REGX if x[0] == name)
        return cone_metrics(build_regs_det(spec[1], spec[2], spec[3], "A", name=name + "_A"))
    if name in XREP_SPL:
        return cone_metrics(build_split(next(x for x in SPLIT if x["name"] == name), "A"))
    return cone_metrics(size_circuits(name)[0])


def size_predictions_text(tier):
    names = {"size": [x["name"] for x in SIZE] + SIZE_REG, "size_mid": [x["name"] for x in SIZE_MID]}[tier]
    L = [f"Pre-registered predictions, tier {tier}. P pass / F fail / ? threshold unknown. Op definition: {OP_DEF}",
         "Rule 1 (measured, rounds 2-3): 6057 iff >= 12 distinct measured ClassicalRegisters in the cone of the `if` (10 passes; 11 untested).",
         "Prior facts for the second trigger: A96 single-store (tree depth ~8 and chain depth 96) pass; cone of 6 small stores (101 ops) passes; the 2026-11-06 T1 "
         "(Shannon form, seed 10, 103 ops, 1 register) was REJECTED; the 2026-11-06 T3 (6 Shannon stores of 79-103 ops, 1 register) was REJECTED.",
         "H_cone: no second trigger (then the historic T1/T3 rejections must NOT replicate). H_cone+store_ops: also fail iff some single store has >= theta ops, theta in (96,103]. "
         "H_cone+cone_ops: fail iff total ops in the cone >= theta, theta in (101,104]. H_cone+depth: per-expression depth (known pass up to 96, so unconstrained above).",
         f"{'probe':11s} {'regs':>4s} {'stores':>6s} {'max_st':>6s} {'cone_ops':>8s} {'depth':>5s} | {'H_cone':>6s} {'+store_ops':>10s} {'+cone_ops':>9s} {'+depth':>6s}"]
    for n in names:
        m = size_probe_metrics(n)
        o = size_hyp_outcome(m)
        L.append(f"{n:11s} {m['cone_regs']:4d} {m['stores_in_cone']:6d} {m['max_store_ops']:6d} {m['cone_ops']:8d} {m['depth']:5d} | {o['H_cone']:>6s} {o['H_cone+store_ops']:>10s} {o['H_cone+cone_ops']:>9s} {o['H_cone+depth']:>6s}")
    L.append("Decisive: Z_shan103 (exact T1 construction) must fail again (it contradicts H_cone if it does, with 1 register); Z_tree/chain104/128 separate op-count from construction "
             "(no NOT ops); Z_T3r2/r3/r6 have every store <= 89 ops: pass => per-store ops (not cone ops), fail => cone ops; B_R11x2 pins theta_regs (11 vs 12); "
             "B_R10x8r/B_R12x2r replicate round 2.")
    return "\n".join(L)


def xrep_predictions_text():
    return ("Cross-backend replication (second backend; compare with ibm_marrakesh round 2/3 and round 1 latency):\n"
            "X_R10x2 P, X_R12x2 F(6057), S_12split6 F(6057), S_10overlap P; latency pair XORC15/XORT15: depth contrast chain-tree = 0 +/- ~75 ns "
            "(marrakesh: -5 +/- 61); absolute values need not match (different device): replicate means the SIGN/ZERO of the contrast and the same op-linear scale.")


# ------------------------------------------------------------------ guarded submit / fetch / analysis
import hashlib
import re
import time

OUTDIR = os.path.join(HERE, "out")
QUOTA_FRACTION = 0.60
LAT_K, LAT_NZ, LAT_NX = 16, 12, 12


def _plan_shots(tier):
    return {"minimal": 3000, "xrep": 2000}.get(tier, 4000)


def acc_expected_p(name):
    """Expected P(out=1) of an ACC probe (exact, from the truth table); K>1 = parity of K iid decisions."""
    cat = {p["name"]: p for p in catalogue()}
    if name in cat:
        p = cat[name]
        pc = semantic_check(build_acc(p, K=1), p)[3]
        K = p.get("K", 1)
        return (1 - (1 - 2 * pc) ** K) / 2
    for nm, k, m, _ in CONES:
        if nm == name:
            recs = cone_spec(k, m, 1000 + k * 100 + m)
            idx = np.arange(256, dtype=np.uint64)
            bits = np.stack([((idx >> np.uint64(i)) & np.uint64(1)).astype(bool) for i in range(8)], 1)
            ref = np.zeros(256, bool)
            for rc in recs:
                ref ^= to_np_wrap(rc, bits, {}).astype(bool)
            return float(ref.mean())
    return 0.5   # register-lane parity / SIMD lane 0 of random-ish bits: ~0.5 (informational only)


def acc_circuit_by_name(name):
    cat = {p["name"]: p for p in catalogue()}
    if name in cat:
        p = cat[name]
        return build_acc(p, K=p.get("K", 1))
    for nm, k, m, _ in CONES:
        if nm == name:
            return build_cone(k, m)[0]
    for nm, nr, w, _ in REGS:
        if nm == name:
            return build_regs(nr, w)[0]
    for nm, nops, _ in SIMDACC:
        if nm == name:
            return build_simd_acc(nops)[0]
    raise KeyError(name)


class RealAdapter:
    """Thin wrapper over QiskitRuntimeService (job mode: Open plan has no session/batch; the legacy stopwatch did the same)."""
    def __init__(self, backend):
        from qiskit_ibm_runtime import QiskitRuntimeService
        self.svc = QiskitRuntimeService()
        self.name = backend
        self.be = self.svc.backend(backend)

    def backend(self):
        return self.be

    def info(self):
        be = self.be
        st = be.status()
        try:
            cal = str(be.properties().last_update_date)
        except Exception:  # noqa: BLE001
            cal = None
        return dict(name=self.name, processor_type=getattr(be, "processor_type", None), backend_version=getattr(be, "backend_version", None),
                    status=st.status_msg, operational=st.operational, pending_jobs=st.pending_jobs, properties_last_update=cal,
                    read_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def remaining_s(self):
        u = self.svc.usage()
        return float(u["usage_remaining_seconds"]), float(u.get("usage_limit_seconds", float("nan"))), \
            u.get("usage_period", {}).get("end_time")

    def run(self, isas, shots):
        from qiskit_ibm_runtime.executor_sampler import Sampler
        return Sampler(self.be).run(isas, shots=shots).job_id()

    def get(self, jid):
        return self.svc.job(jid)


def canon(qc):
    """Deterministic structural serialisation (QPY bytes embed non-deterministic ids, so they are not hashed)."""
    out = [qc.name, qc.num_qubits, [(r.name, len(r)) for r in qc.cregs]]

    def ins_list(c):
        res = []
        for i in c.data:
            op = i.operation
            extra = ""
            if op.name == "store":
                extra = f"{op.lvalue!r}<-{op.rvalue!r}"
            elif op.name == "if_else":
                extra = repr(op.condition) + "|" + "||".join(json.dumps(ins_list(b)) for b in op.blocks)
            res.append([op.name, [c.find_bit(q).index for q in i.qubits], [c.find_bit(b).index for b in i.clbits],
                        [str(p) for p in op.params if not hasattr(p, "data")], extra])
        return res
    out.append(ins_list(qc))
    return json.dumps(out, sort_keys=True, default=str)


def build_plan(adapter, tier, shots=None):
    """Build + transpile every circuit of a tier against the adapter's backend. Returns plan dict (no submission)."""
    if tier in ("p1", "p2", "p3", "v1", "e1", "e2"):
        import tiers_v2 as T2
        return T2.build(adapter, tier, shots)
    be = adapter.backend()
    n_src = 24
    layout, info = (SW.pick_layout(be, LAT_NZ, LAT_NX, n_src) if tier in ("minimal", "ideal", "xrep") else (None, {}))
    shots = shots or (REGB_SHOTS if tier in ("regbisect", "split", "size", "size_mid") else _plan_shots(tier))
    jobs_cfg, est_total = plan(tier, 1e9)
    cat = {p["name"]: p for p in catalogue()}
    jobs = []
    h = hashlib.sha256()
    h.update(json.dumps({"backend": getattr(adapter, "name", "?"), "layout": layout, "tier": tier,
                         "lat_k": LAT_K, "shots": shots}, sort_keys=True).encode())
    for j in jobs_cfg:
        if j["kind"] == "SPL":
            sp = next(x for x in SPLIT if x["name"] == j["name"])
            circs = split_circuits(j["name"])
            gq = good_qubits(be, sp["nr"] * sp["w"] + 1)
            lay = gq[1:] + [gq[0]]
            isas = [transpile(c, be, initial_layout=lay, optimization_level=1, seed_transpiler=7) for c in circs]
            exp_rep = [(t, len(set(o)), len(o) - 1) for t, o in sp["stores"]]
            for c in isas:     # the transpiler must not merge or rewrite the stores
                got = [(t, n, k) for t, n, k in store_report(c)]
                assert sorted(got) == sorted(exp_rep), (j["name"], got, exp_rep)
            names = [c.name for c in circs]
            nsh = j["shots"]
            jlay = lay
        elif j["kind"] == "SIZ":
            circs = size_circuits(j["name"])
            nq = circs[0].num_qubits
            gq = good_qubits(be, nq)
            lay = gq[1:] + [gq[0]]
            isas = [transpile(c, be, initial_layout=lay, optimization_level=1, seed_transpiler=7) for c in circs]
            names = [c.name for c in circs]
            nsh = j["shots"]
            jlay = lay
        elif j["kind"] == "REG":
            _, nr, w, nread = next(x for x in list(REGB) + list(REGX) if x[0] == j["name"])
            circs = regb_circuits(j["name"])
            gq = good_qubits(be, nr * w + 1)          # best-readout qubits; gq[0] (best) = out qubit
            lay = gq[1:] + [gq[0]]                    # sources first, out qubit last (circuit order q[0..nb-1], q[nb])
            isas = [transpile(c, be, initial_layout=lay, optimization_level=1, seed_transpiler=7) for c in circs]
            names = [c.name for c in circs]
            nsh = j["shots"]
            jlay = lay
        elif j["kind"] == "LAT":
            p = cat[j["name"]]
            names = [f"REF{d}" for d in SUITE_REF_D] + [j["name"]]
            circs = [build_lat(p, K=LAT_K, n_z=LAT_NZ, n_x=LAT_NX, case=("CASE" if n == j["name"] else n)) for n in names]
            isas = [transpile(c, be, initial_layout=layout, optimization_level=1, seed_transpiler=7) for c in circs]
            nsh = shots
            jlay = layout
        else:
            names = [j["name"]]
            isas = [transpile(acc_circuit_by_name(j["name"]), be, optimization_level=1, seed_transpiler=7)]
            nsh = j["shots"]
            jlay = None
        metrics = None
        if j["kind"] in ("SIZ", "REG", "SPL"):
            builder = {"SIZ": lambda: size_circuits(j["name"])[0], "REG": lambda: regb_circuits(j["name"])[0],
                       "SPL": lambda: split_circuits(j["name"])[0]}[j["kind"]]()
            mb = cone_metrics(builder)
            for c in isas:
                mt = cone_metrics(c)
                assert mt == mb, (j["name"], "transpiled cone metrics differ from builder", mt, mb)
            metrics = mb
        blobs = []
        for c in isas:
            buf = io.BytesIO()
            qpy.dump(c, buf)
            blobs.append(buf.getvalue())
        h.update(json.dumps([j["name"], nsh, names]).encode())
        for c in isas:
            h.update(hashlib.sha256(canon(c).encode()).digest())
        jobs.append(dict(kind=j["kind"], name=j["name"], circuit_names=names, shots=nsh, K=(LAT_K if j["kind"] == "LAT" else j["K"]),
                         est_s=j["cost"], isas=isas, qpy=blobs, layout=jlay, metrics=metrics,
                         store_report=(store_report(isas[0]) if j["kind"] == "SPL" else None),
                         expected_p=(acc_expected_p(j["name"]) if j["kind"] == "ACC" else None)))
    return dict(tier=tier, backend=getattr(adapter, "name", "?"), layout=layout, probe_info=info, shots=shots,
                jobs=jobs, est_total_s=float(sum(j["est_s"] for j in jobs)), hash=h.hexdigest()[:12])


def predictions_text(tier):
    if tier in ("p1", "p2", "p3", "v1", "e1", "e2"):
        import tiers_v2 as T2
        return T2.predictions_text(tier)
    if tier == "regbisect":
        return regb_predictions_text()
    if tier == "split":
        return split_predictions_text()
    if tier in ("size", "size_mid"):
        return size_predictions_text(tier)
    if tier == "xrep":
        return xrep_predictions_text()
    rows, cls, Xm, sig = design_power(tier)
    out = [f"Pre-registered predictions (assumed sigma {sig:.0f} ns/case), ns over a 1-bit if:",
           "probe     ops inp dep |  H_ops H_inputs H_depth"]
    for name, s, pr in rows:
        out.append(f"{name:9s} {s['ops']:3d} {s['inputs']:3d} {s['depth']:3d} | {pr['H_ops']:6.0f} {pr['H_inputs']:8.0f} {pr['H_depth']:7.0f}")
    return "\n".join(out)


def atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def submit(adapter, tier, execute=False, confirm=None, outdir=OUTDIR, quiet=False, resubmit=False):
    """Dry run unless execute and confirm == plan hash and quota guard passes. Returns (plan, manifest_path|None)."""
    pl = build_plan(adapter, tier)
    say = (lambda *a: None) if quiet else print
    if pl["layout"]:
        say(f"backend {pl['backend']}  tier {tier}  layout probes={pl['layout'][24:-1]} flag={pl['layout'][-1]} "
            f"sources={pl['layout'][:24]}  info={ {k: v for k, v in pl['probe_info'].items() if k != 'probe_readout'} }")
    else:
        say(f"backend {pl['backend']}  tier {tier}  (per-job layouts = best-readout qubits, out qubit last)")
        for j in pl["jobs"]:
            say(f"  layout {j['name']}: out={j['layout'][-1]} sources={j['layout'][:-1]}")
    say(f"{'kind':4s} {'job':10s} {'circ':>4s} {'K':>3s} {'shots':>5s} {'est s':>6s}")
    for j in pl["jobs"]:
        say(f"{j['kind']:4s} {j['name']:10s} {len(j['isas']):4d} {j['K']:3d} {j['shots']:5d} {j['est_s']:6.1f}")
    rem, lim, end = adapter.remaining_s()
    ratio = pl["est_total_s"] / rem if rem > 0 else float("inf")
    say(f"estimated QPU seconds {pl['est_total_s']:.1f}; remaining quota {rem:.0f} s of {lim:.0f} (period end {end}); "
        f"estimate = {100 * ratio:.0f}% of remaining (limit {100 * QUOTA_FRACTION:.0f}%)")
    say(f"PLAN HASH {pl['hash']}")
    if not execute:
        say("DRY RUN: nothing submitted. To submit: --execute --confirm " + pl["hash"])
        return pl, None
    if confirm != pl["hash"]:
        raise SystemExit(f"REFUSED: --confirm {confirm!r} does not match plan hash {pl['hash']} (plan changed or confirm missing)")
    if ratio > QUOTA_FRACTION:
        raise SystemExit(f"REFUSED: estimate {pl['est_total_s']:.1f} s is {100 * ratio:.0f}% of remaining {rem:.0f} s (> {100 * QUOTA_FRACTION:.0f}%)")
    if os.path.exists(os.path.join(outdir, f"manifest_{pl['hash']}.json")) and not resubmit:
        raise SystemExit(f"REFUSED: out/manifest_{pl['hash']}.json already exists, so this plan was already submitted (jobs may still be queued). "
                         "Fetch it, or pass --resubmit to submit the same plan again deliberately.")
    pdir = os.path.join(outdir, f"plan_{pl['hash']}")
    os.makedirs(pdir, exist_ok=True)
    man = dict(plan_hash=pl["hash"], tier=tier, backend=pl["backend"], layout=pl["layout"], probe_info=pl["probe_info"],
               n_z=LAT_NZ, n_x=LAT_NX, lat_k=LAT_K, shots=pl["shots"], ref_D=list(SUITE_REF_D),
               est_total_s=pl["est_total_s"], quota_remaining_s=rem, submitted_at=time.strftime("%Y-%m-%d %H:%M:%S"),
               predictions=predictions_text(tier), backend_info=(adapter.info() if hasattr(adapter, "info") else {}), jobs=[])
    mpath = os.path.join(outdir, f"manifest_{pl['hash']}.json")
    atomic_json(mpath, man)
    for j in pl["jobs"]:
        qp = os.path.join(pdir, f"{j['name']}.qpy.bin")
        with open(qp, "wb") as f:
            f.write(b"".join(len(b).to_bytes(8, "little") + b for b in j["qpy"]))
        rec = dict(name=j["name"], kind=j["kind"], layout=j["layout"], meta=j.get("meta"), metrics=j.get("metrics"), op_definition=OP_DEF, store_report=j.get("store_report"), circuit_names=j["circuit_names"], shots=j["shots"], K=j["K"],
                   est_s=j["est_s"], expected_p=j["expected_p"], qpy_file=os.path.relpath(qp, outdir), job_id=None, error=None)
        man["jobs"].append(rec)
        try:
            rec["job_id"] = adapter.run(j["isas"], j["shots"])
            say(f"submitted {j['name']:10s} {rec['job_id']}")
        except Exception as ex:  # noqa: BLE001
            rec["error"] = f"{type(ex).__name__}: {str(ex)[-300:]}"
            say(f"SUBMIT FAILED {j['name']}: {rec['error']}")
        atomic_json(mpath, man)          # after every submit: a crash never loses a job id
    return pl, mpath


# ---------------- fetch + analysis
def err_code(msg):
    m = re.search(r"[Ee]rror code (\d+)", msg or "") or re.search(r"error_codes#(\d+)", msg or "")
    return int(m.group(1)) if m else None


def _status(job):
    st = job.status()
    return getattr(st, "name", str(st)).split(".")[-1]


def fetch_jobs(adapter, manifest_path, wait=False, timeout=3 * 3600, poll=20, outdir=OUTDIR):
    man = json.load(open(manifest_path))
    t0 = time.time()
    res = {}
    for rec in man["jobs"]:
        r = dict(name=rec["name"], kind=rec["kind"], status="NOT_SUBMITTED", code=None, message=None, qs=None)
        res[rec["name"]] = r
        if not rec.get("job_id"):
            r["message"] = rec.get("error")
            continue
        try:
            job = adapter.get(rec["job_id"])
            while True:
                st = _status(job)
                if st in ("DONE", "ERROR", "CANCELLED") or not wait or time.time() - t0 > timeout:
                    break
                time.sleep(poll)
            r["status"] = st
            try:
                r["qs"] = job.metrics().get("usage", {}).get("quantum_seconds")
            except Exception:  # noqa: BLE001
                pass
            if st == "DONE":
                out = job.result()
                arrs = {}
                for cname, pub in zip(rec["circuit_names"], out):
                    d = pub.data
                    for reg in ("pr", "fl", "out"):
                        if hasattr(d, reg):
                            arrs[f"{reg}_{cname}"] = getattr(d, reg).to_bool_array(order="little")
                os.makedirs(outdir, exist_ok=True)
                np.savez_compressed(os.path.join(outdir, f"qpu_{man['plan_hash']}_{rec['name']}.npz"), **arrs)
                r["npz"] = f"qpu_{man['plan_hash']}_{rec['name']}.npz"
            elif st in ("ERROR", "CANCELLED"):
                try:
                    r["message"] = str(job.error_message())
                except Exception as ex:  # noqa: BLE001
                    r["message"] = f"error_message unavailable: {type(ex).__name__}"
                r["code"] = err_code(r["message"])
        except Exception as ex:  # noqa: BLE001
            r["status"] = "FETCH_FAILED"
            r["message"] = f"{type(ex).__name__}: {str(ex)[-300:]}"
            r["code"] = err_code(r["message"])
    atomic_json(os.path.join(outdir, f"fetch_{man['plan_hash']}.json"), res)
    return man, res


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return float(c - h), float(c + h)


REG_PRIOR_ALL = [  # label, metrics, accepted?
    ("F1_regs150", dict(read_regs=1, read_bits=1, declared_regs=153, total_bits=152, measured_regs=1), True),
    ("A96t_n16", dict(read_regs=1, read_bits=16, declared_regs=3, total_bits=18, measured_regs=1), True),
    ("C6x16", dict(read_regs=6, read_bits=6, declared_regs=9, total_bits=16, measured_regs=1), True),
    ("R12x8 (round 1)", dict(read_regs=12, read_bits=96, declared_regs=15, total_bits=106, measured_regs=12), False),
]


def analyse_regbisect(man, res, outdir):
    L = [f"# QPU results regbisect, plan {man['plan_hash']} on {man['backend']}",
         f"submitted {man['submitted_at']}; est {man['est_total_s']:.0f} QPU-s; {REGB_SHOTS} shots per pattern circuit (2 circuits per job)", "",
         "## What 'register' means in these probes",
         "A classical register = one `ClassicalRegister` in the circuit. Each probe declares R registers G0..G(R-1) of w bits (every bit is the target of a mid-circuit "
         "`measure_2` on a prepared qubit), one store target S (w bits), the unused 1-bit L (as in R12x8) and the 1-bit `out`. ONE `store` computes "
         "S = G0 ^ ... ^ G(nread-1) (nread-1 Uint ops) and ONE `if S[0]` drives X on the output qubit. There are no Vars. "
         "Classical bits are not added for readback: the check uses the existing `out` bit, with the inputs PREPARED (X then measure) so the expected value is known: "
         "pattern A expects out=1, pattern B expects out=0 (dead or wrong computation would give 0 on A / 1 on B).", "",
         "## Jobs and readback"]
    L += ["| probe | read regs | read bits | declared regs | total bits | result | code | A: P(out=1) [95% CI] | B: P(out=0) [95% CI] | verified |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    pts = {h: [(m[h], o) for _, m, o in REG_PRIOR_ALL] for h in ("read_regs", "read_bits", "declared_regs", "total_bits", "measured_regs")}
    for rec in man["jobs"]:
        spec = next(x for x in REGB if x[0] == rec["name"])
        info = regb_info(spec[1], spec[2], spec[3])
        r = res[rec["name"]]
        acc = r["status"] == "DONE"
        ca = cb = ""
        ver = ""
        if acc:
            z = np.load(os.path.join(outdir, r["npz"]))
            oa, ob = z[f"out_{rec['name']}_A"].astype(bool).ravel(), z[f"out_{rec['name']}_B"].astype(bool).ravel()
            ka, kb = int(oa.sum()), int((~ob).sum())
            la, lb = wilson(ka, len(oa)), wilson(kb, len(ob))
            ca = f"{ka / len(oa):.3f} [{la[0]:.3f},{la[1]:.3f}]"
            cb = f"{kb / len(ob):.3f} [{lb[0]:.3f},{lb[1]:.3f}]"
            ver = "YES" if (la[0] > 0.7 and lb[0] > 0.7) else "NO (accepted but not verified)"
        L.append(f"| {rec['name']} | {info['read_regs']} | {info['read_bits']} | {info['declared_regs']} | {info['total_bits']} | "
                 f"{'ACCEPTED' if acc else r['status']} | {r['code'] or ''} | {ca} | {cb} | {ver} |")
        if acc or r["code"] == 6057:
            for h in pts:
                pts[h].append((info[h], acc))
    L += ["", "## Threshold consistency (monotone: fail iff metric >= theta). Prior points: F1_regs150, A96t_n16, C6x16 accepted; round-1 R12x8 failed 6057",
          "| metric | consistent | theta lies in (pass max, fail min] |", "|---|---|---|"]
    verdict = []
    for h, v in pts.items():
        pmax = max([m for m, o in v if o], default=-1)
        fmin = min([m for m, o in v if not o], default=1e9)
        ok = pmax < fmin
        L.append(f"| {h} | {'yes' if ok else 'NO (a pass at >= a fail)'} | ({pmax}, {fmin}] |" if ok else f"| {h} | NO (pass at {pmax} >= fail at {fmin}) | |")
        if ok:
            verdict.append(f"{h}: theta in ({pmax}, {fmin}]")
    L += ["", "### Interpretation", "- Metrics still consistent with all data: " + ("; ".join(verdict) if verdict else "none (re-examine: 6057 may depend on something else, e.g. measured-bit count per register or qubit placement)"),
          "- Compare with the pre-registered table in the manifest (`predictions`). Decisive probes: B_R12x2 (count without bits), B_R6x16/B_R3x32 (bits without count), "
          "B_R12x8u (declared but unread), B_R12x8c (control must fail again; if it passes, the round-1 failure was not about this structure and the plan is void)."]
    txt = "\n".join(L) + "\n"
    with open(os.path.join(outdir, "RESULTS_QPU_regbisect.md"), "w") as f:
        f.write(txt)
    return txt


def _readback(res_r, rec, outdir):
    z = np.load(os.path.join(outdir, res_r["npz"]))
    oa, ob = z[f"out_{rec['name']}_A"].astype(bool).ravel(), z[f"out_{rec['name']}_B"].astype(bool).ravel()
    ka, kb = int(oa.sum()), int((~ob).sum())
    la, lb = wilson(ka, len(oa)), wilson(kb, len(ob))
    return (f"{ka / len(oa):.3f} [{la[0]:.3f},{la[1]:.3f}]", f"{kb / len(ob):.3f} [{lb[0]:.3f},{lb[1]:.3f}]",
            "YES" if (la[0] > 0.7 and lb[0] > 0.7) else "NO")


def analyse_size(man, res, outdir):
    L = [f"# QPU results {man['tier']}, plan {man['plan_hash']} on {man['backend']}",
         f"submitted {man['submitted_at']}; est {man['est_total_s']:.0f} QPU-s; {SPLIT_SHOTS} shots per prepared pattern (A expects out=1, B expects out=0)", "",
         f"Op definition: {OP_DEF}", "Metrics below are counted on the TRANSPILED circuits (asserted equal to the builder at plan time).", "",
         "| probe | cone regs | stores in cone | max store ops | cone ops | depth | result | code | A: P(out=1) [CI] | B: P(out=0) [CI] | verified |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    obs = [(m, o) for _, m, o in SIZE_PRIOR]
    msgs = {}
    outcome = {}
    for rec in man["jobs"]:
        r = res[rec["name"]]
        m = rec["metrics"]
        acc = r["status"] == "DONE"
        ca = cb = ver = ""
        if acc:
            ca, cb, ver = _readback(r, rec, outdir)
        L.append(f"| {rec['name']} | {m['cone_regs']} | {m['stores_in_cone']} | {m['max_store_ops']} | {m['cone_ops']} | {m['depth']} | "
                 f"{'ACCEPTED' if acc else r['status']} | {r['code'] or ''} | {ca} | {cb} | {ver} |")
        if acc or r["code"] == 6057:
            outcome[rec["name"]] = acc
            obs.append((m, acc))
        if not acc and r["message"]:
            msgs.setdefault(r["message"], []).append(rec["name"])
    L += ["", "## Full error messages (verbatim)"]
    for t, ns in msgs.items():
        L.append(f"- {', '.join(ns)}: `{t}`")
    if not msgs:
        L.append("- none (no failures)")
    # predictions vs observed
    L += ["", "## Pre-registered predictions vs outcome (match = P/F agrees; '?' neither)", "| probe | observed | H_cone | H_cone+store_ops | H_cone+cone_ops | H_cone+depth |", "|---|---|---|---|---|---|"]
    score = {h: [0, 0] for h in ("H_cone", "H_cone+store_ops", "H_cone+cone_ops", "H_cone+depth")}
    for rec in man["jobs"]:
        if rec["name"] not in outcome:
            continue
        o = size_hyp_outcome(rec["metrics"])
        cells = []
        for h in score:
            v = o[h]
            ok = (v == "P" and outcome[rec["name"]]) or (v == "F" and not outcome[rec["name"]])
            wrong = (v == "P" and not outcome[rec["name"]]) or (v == "F" and outcome[rec["name"]])
            cells.append(f"{v}{' ok' if ok else (' WRONG' if wrong else '')}")
            score[h][0] += int(ok)
            score[h][1] += int(wrong)
        L.append(f"| {rec['name']} | {'pass' if outcome[rec['name']] else 'FAIL'} | " + " | ".join(cells) + " |")
    L.append("| score (ok / wrong) | | " + " | ".join(f"{score[h][0]} / {score[h][1]}" for h in score) + " |")
    # threshold consistency among observations with <= 10 cone registers (register-rule failures excluded)
    L += ["", "## Threshold fits for the second trigger (observations with cone registers <= 10, incl. historic points)",
          "| metric | pass max | fail min | consistent | theta in |", "|---|---|---|---|---|"]
    live = []
    base = [(m, o) for m, o in obs if m["cone_regs"] <= 10]
    for key in ("max_store_ops", "cone_ops", "depth"):
        pm = max([m[key] for m, o in base if o], default=-1)
        fm = min([m[key] for m, o in base if not o], default=10 ** 9)
        ok = pm < fm
        L.append(f"| {key} | {pm} | {fm if fm < 10 ** 9 else 'none'} | {'yes' if ok else 'NO'} | {f'({pm}, {fm}]' if ok and fm < 10 ** 9 else ('no failing point' if ok else '')} |")
        if ok and fm < 10 ** 9:
            live.append(key)
    fails_small = [m for m, o in base if not o]
    L += ["", "### Verdict"]
    s103 = outcome.get("Z_shan103")
    if s103 is False:
        L.append("- Z_shan103 (exact 2026-11-06 construction) FAILED AGAIN with 1 measured register: H_cone-only is refuted, a second trigger exists.")
    elif s103 is True:
        L.append("- Z_shan103 ACCEPTED: the 2026-11-06 103-op rejection did NOT replicate today (firmware/compiler change, or a nondeterministic limit). H_cone-only survives for this point; treat the historic rejection as unexplained.")
    t = [outcome.get(k) for k in ("Z_tree104", "Z_tree128", "Z_chain104", "Z_chain128")]
    if all(v is not None for v in t):
        L.append(f"- NOT-free soups: tree104 {t[0]}, tree128 {t[1]}, chain104 {t[2]}, chain128 {t[3]} (True = accepted). "
                 + ("All accepted: raw op count / depth up to 128 is not a trigger for this construction; the Shannon form (NOT-heavy, shared structure) is what differs." if all(t) else
                    "Failures at these sizes mean an op-count limit above 96 exists even without NOT ops." if not all(t) else ""))
    r2, r3, r6 = (outcome.get(k) for k in ("Z_T3r2", "Z_T3r3", "Z_T3r6"))
    if None not in (r2, r3, r6):
        L.append(f"- Many small-ish stores (each <= 89 ops): 2 stores {r2}, 3 stores {r3}, 6 stores {r6} (True = accepted). "
                 + ("Cone ops up to 515 accepted: the limit is not total ops in the cone." if r6 else
                    "A failure here with every store <= 89 ops points at cone size (total ops/leaf references), not per-store ops."))
    r11 = outcome.get("B_R11x2")
    if r11 is not None:
        L.append(f"- B_R11x2 {'ACCEPTED: theta_regs = 12 (fail iff >= 12 distinct measured registers in the cone)' if r11 else 'FAILED: theta_regs = 11'}.")
    rr = [outcome.get(k) for k in ("B_R10x8r", "B_R12x2r")]
    if None not in rr:
        L.append(f"- Replication of round 2: R10x8 {'accepted' if rr[0] else 'FAILED'} (expected accepted), R12x2 {'accepted' if rr[1] else 'failed 6057'} (expected failed): "
                 + ("replicated." if rr[0] and not rr[1] else "NOT replicated, investigate."))
    L.append("- Hypotheses with a consistent threshold among non-register data: " + (", ".join(live) if live else "none (no consistent single metric)"))
    txt = "\n".join(L) + "\n"
    with open(os.path.join(outdir, f"RESULTS_QPU_{man['tier']}.md"), "w") as f:
        f.write(txt)
    return txt


def analyse_xrep(man, res, outdir):
    expect = {"X_R10x2": True, "X_R12x2": False, "S_12split6": False, "S_10overlap": True}
    L = [f"# Cross-backend replication, plan {man['plan_hash']} on {man['backend']}",
         f"submitted {man['submitted_at']}; est {man['est_total_s']:.0f} QPU-s", "", "| probe | expected (from ibm_marrakesh) | observed | code | readback A / B | match |", "|---|---|---|---|---|---|"]
    n_ok = n = 0
    msgs = {}
    meas = {}
    for rec in man["jobs"]:
        r = res[rec["name"]]
        if rec["kind"] == "LAT":
            if r["status"] == "DONE":
                z = np.load(os.path.join(outdir, r["npz"]))
                prs = {k[3:]: z[k] for k in z.files if k.startswith("pr_")}
                try:
                    rb = EST.analyse_robust(prs, man["n_z"], man["lat_k"], tuple(man["ref_D"]), rec["name"])
                    meas[rec["name"]] = (rb["headline"], rb["headline_se"])
                except Exception as ex:  # noqa: BLE001
                    L.append(f"| {rec['name']} | latency | analysis failed: {ex} | | | |")
            else:
                L.append(f"| {rec['name']} | latency | {r['status']} | {r['code'] or ''} | | |")
                if r["message"]:
                    msgs.setdefault(r["message"], []).append(rec["name"])
            continue
        acc = r["status"] == "DONE"
        rb = ""
        if acc:
            ca, cb, ver = _readback(r, rec, outdir)
            rb = f"{ca} / {cb} ({ver})"
        m = acc == expect[rec["name"]]
        n += 1
        n_ok += int(m)
        L.append(f"| {rec['name']} | {'accept' if expect[rec['name']] else '6057'} | {'ACCEPTED' if acc else r['status']} | {r['code'] or ''} | {rb} | {'yes' if m else 'NO'} |")
        if not acc and r["message"]:
            msgs.setdefault(r["message"], []).append(rec["name"])
    L += ["", f"Acceptance matches: {n_ok}/{n}", "", "## Latency pair (increment over a 1-bit if, ns)"]
    for k, (v, s) in meas.items():
        L.append(f"- {k}: {v:.0f} +/- {s:.0f}")
    if len(meas) == 2:
        d = meas["XORC15"][0] - meas["XORT15"][0]
        sg = float(np.hypot(meas["XORC15"][1], meas["XORT15"][1]))
        L.append(f"- chain - tree = {d:+.0f} +/- {sg:.0f} ns ({d / sg:+.1f} sigma); ibm_marrakesh: -5 +/- 61. Depth-independence {'replicated' if abs(d) < 2 * sg else 'NOT replicated'}.")
    L += ["", "## Full error messages (verbatim)"] + ([f"- {', '.join(ns)}: `{t}`" for t, ns in msgs.items()] or ["- none"])
    txt = "\n".join(L) + "\n"
    with open(os.path.join(outdir, "RESULTS_QPU_xrep.md"), "w") as f:
        f.write(txt)
    return txt


def analyse_manifest(man, res, outdir=OUTDIR):
    """Latency model + ACC verdicts -> markdown string (and writes out/RESULTS_QPU.md)."""
    if man["tier"] in ("p1", "p2", "p3", "v1", "e1", "e2"):
        import tiers_v2 as T2
        return T2.analyse(man, res, outdir)
    if man["tier"] == "regbisect":
        return analyse_regbisect(man, res, outdir)
    if man["tier"] == "split":
        return analyse_split(man, res, outdir)
    if man["tier"] in ("size", "size_mid"):
        return analyse_size(man, res, outdir)
    if man["tier"] == "xrep":
        return analyse_xrep(man, res, outdir)
    R15 = SW
    L = [f"# QPU results, plan {man['plan_hash']} ({man['tier']}) on {man['backend']}",
         f"submitted {man['submitted_at']}; est {man['est_total_s']:.0f} QPU-s; K={man['lat_k']}, shots/latency circuit {man['shots']}", ""]
    L.append("## Jobs")
    L.append("| job | kind | status | error code | quantum s | note |")
    L.append("|---|---|---|---|---|---|")
    for rec in man["jobs"]:
        r = res[rec["name"]]
        L.append(f"| {rec['name']} | {rec['kind']} | {r['status']} | {r['code'] or ''} | {r['qs'] if r['qs'] is not None else ''} | "
                 f"{(r['message'] or '')[-110:].replace('|', '/')} |")
    # ---- latency
    n_z, K, ref_D = man["n_z"], man["lat_k"], tuple(man["ref_D"])
    meas, rows, loaded, robust = {}, [], {}, {}
    for rec in man["jobs"]:
        r = res[rec["name"]]
        if rec["kind"] != "LAT" or r["status"] != "DONE":
            continue
        z = np.load(os.path.join(OUTDIR if outdir is None else outdir, r["npz"]))
        prs = {k[3:]: z[k] for k in z.files if k.startswith("pr_")}
        try:
            rb = EST.analyse_robust(prs, n_z, K, ref_D, rec["name"])
            if "error" in rb:
                raise RuntimeError(rb["error"])
            legacy = SW.analyse_data(prs, n_z, K, ref_D, (rec["name"],), verbose=False)[rec["name"]]
        except Exception as ex:  # noqa: BLE001
            L.append(f"\nLAT {rec['name']}: analysis failed: {ex}")
            continue
        robust[rec["name"]] = (rb, legacy)
        meas[rec["name"]] = (rb["headline"], rb["headline_se"])
        meta = dict(K=K, n_z=n_z, ref_D=list(ref_D), shots=len(prs["REF0"]), backend=man["backend"], layout=man["layout"],
                    cases=[rec["name"]], n_x=man["n_x"], n_src=24, measure_2_s=1.884e-6, submitted=man["submitted_at"])
        loaded[rec["name"]] = {"id": rec["name"], "meta": meta, "raw": {f"pr_{k}": v2 for k, v2 in prs.items()}}
    L += ["", "## Estimators side by side (ns over a 1-bit if; headline = Huber +/- leave-one-probe-out jackknife)",
          "| probe | published-style (clipped, shot sigma) | weighted unclipped | median | Huber +/- jackknife | Z median | X median | probe chi2/dof | usable | clip kept |", "|---|---|---|---|---|---|---|---|---|---|"]
    for nm, (rb, lg) in robust.items():
        L.append(f"| {nm} | {lg[0]:.0f} +/- {lg[1]:.0f} | {rb['wmean_unclipped']:.0f} | {rb['median']:.0f} | {rb['huber']:.0f} +/- {rb['jackknife_se_huber']:.0f} | "
                 f"{rb.get('median_Z', float('nan')):.0f} | {rb.get('median_X', float('nan')):.0f} | {rb['chi2_per_dof']:.1f} | {rb['n_probes_usable']}/{rb['n_probes_total']} | {rb['clip_kept']} |")
    pre = {name: (s, pr) for name, s, pr in design_power(man["tier"])[0]}
    L += ["", "## Latency increments over a 1-bit if (ns)", "| probe | ops | depth | inputs | measured +/- boot | H_ops | H_inputs | H_depth |", "|---|---|---|---|---|---|---|---|"]
    for name, (v, s) in meas.items():
        st, pr = pre[name]
        L.append(f"| {name} | {st['ops']} | {st['depth']} | {st['inputs']} | {v:.0f} +/- {s:.0f} | {pr['H_ops']:.0f} | {pr['H_inputs']:.0f} | {pr['H_depth']:.0f} |")
    jsd = 0.0
    if len(loaded) >= 2:
        offs = []
        names = list(loaded)
        for a, b in itertools.combinations(names, 2):
            try:
                d = R15.reference_drift_ns(loaded[a], loaded[b])
                offs.append(max(abs(x) for x in d["cross_offsets_ns"]))
            except Exception:  # noqa: BLE001
                pass
        jsd = float(np.sqrt(np.mean(np.square(offs)))) if offs else 0.0
        L.append(f"\nInter-job REF drift (RMS of max cross-offset over {len(offs)} pairs): {jsd:.0f} ns (used as job-level random effect)")
    if len(meas) >= 3:
        names = list(meas)
        y = np.array([meas[n][0] for n in names])
        s_stat = np.array([meas[n][1] for n in names])
        feats = {}
        for n in names:
            st, _ = pre[n]
            so = st["ops"] - (1 if n in ("POP8", "WVOTE27", "WSUM8", "SHXOR8") else 0)
            sd = st["depth"] - (1 if n in ("POP8", "WVOTE27", "WSUM8", "SHXOR8") else 0)
            feats[n] = dict(H_ops=so, H_inputs=st["inputs"], H_depth=sd)
        L += ["", "## Hypothesis tests (3 hypotheses; pre-registered = old 3-point line, refit = free 2-parameter WLS)",
              "| hypothesis | chi2 pre-registered (n dof) | refit intercept | refit slope /unit | chi2 refit (n-2 dof) | chi2 refit incl. job-level |", "|---|---|---|---|---|---|"]
        for h in ("H_ops", "H_inputs", "H_depth"):
            x = np.array([feats[n][h] for n in names], float)
            pred = np.array([pre[n][1][h] for n in names])
            c_pre = float(np.sum(((y - pred) / s_stat) ** 2))
            row = []
            for s in (s_stat, np.hypot(s_stat, jsd)):
                A = np.vstack([np.ones_like(x), x]).T
                if np.linalg.matrix_rank(A) < 2:
                    row.append((np.nan, np.nan, np.nan))
                    continue
                Wm = np.diag(1 / s ** 2)
                beta = np.linalg.solve(A.T @ Wm @ A, A.T @ Wm @ y)
                row.append((beta[0], beta[1], float(np.sum(((y - A @ beta) / s) ** 2))))
            L.append(f"| {h} | {c_pre:.1f} ({len(names)}) | {row[0][0]:.0f} | {row[0][1]:.1f} | {row[0][2]:.1f} ({len(names) - 2}) | {row[1][2]:.1f} |")
    def contrast(a, b, label):
        if a in meas and b in meas:
            d = meas[a][0] - meas[b][0]
            s = float(np.hypot(meas[a][1], meas[b][1]))
            L.append(f"- {label}: {d:+.0f} +/- {s:.0f} ns ({d / s:+.1f} sigma stat; job-level {np.hypot(s, jsd):.0f} ns)")
    L += ["", "## Pre-registered contrasts"]
    contrast("XORC15", "XORT15", "XORC15 - XORT15 (same 15 xor, depth 15 vs 4; H_ops predicts 0, H_depth +1372)")
    if "SOUP63" in meas:
        v, s = meas["SOUP63"]
        L.append(f"- SOUP63 vs extrapolation 2155 ns: {v - 2155:+.0f} ns = {(v - 2155) / s:+.1f} sigma (63 ops, depth 6, 16 inputs)")
    if "WVOTE27" in meas:
        v, s = meas["WVOTE27"]
        L.append(f"- WVOTE27 reproduction of old 1032 +/- 151: {v:.0f} +/- {s:.0f} ns, z = {(v - 1032) / np.hypot(s, 151):+.1f}")
    contrast("WVOTE27", "POP8", "WVOTE27 - POP8 (7 mul vs none; ops differ by 4)")
    # ---- acceptance
    L += ["", "## Acceptance probes (pass/fail; failures are data)", "| probe | result | code | P(out=1) obs vs expected |", "|---|---|---|---|"]
    acc = {}
    for rec in man["jobs"]:
        if rec["kind"] != "ACC":
            continue
        r = res[rec["name"]]
        ok = r["status"] == "DONE"
        acc[rec["name"]] = ok
        pobs = ""
        if ok:
            z = np.load(os.path.join(outdir, r["npz"]))
            o = z[f"out_{rec['name']}"].astype(float)
            p, n = rec["expected_p"], len(o)
            sg = max(np.sqrt(p * (1 - p) / n), 1e-3)
            pobs = f"{o.mean():.2f} vs {p:.2f} ({(o.mean() - p) / sg:+.1f} sigma, readout-limited)"
        L.append(f"| {rec['name']} | {'ACCEPTED' if ok else r['status']} | {r['code'] or ''} | {pobs} |")
    def g(n):
        return acc.get(n)
    L += ["", "### Interpretation (rule-based; check against the table)"]
    a1, a8 = g("A84t_K1"), g("A84t_K8")
    if a1 is not None and a8 is not None:
        L.append("- 84 ops: " + {(True, True): "K=1 and K=8 both ACCEPTED: 84 ops is under the limit and K=8 repetition is fine; the old LUT85 failure came from that expression's structure or the 83-103 limit sits above 84 for this shape (LUT85 had a different, wider-support structure).",
                                  (True, False): "K=1 ACCEPTED, K=8 FAILED: repetitions accumulate (K x ops or live-state), the limit is not per store.",
                                  (False, False): "both FAILED: the per-store limit is below 84 ops for this shape (the 83-pass/103-fail bracket tightens to <= 83).",
                                  (False, True): "K=1 failed but K=8 accepted: anomalous, rerun."}[(a1, a8)])
    t, c = g("A96t_K1"), g("A96c_K1")
    if t is not None and c is not None:
        L.append("- 96 ops tree vs chain: " + {(True, True): "both ACCEPTED: depth is not the constraint; limit > 96 for this shape.",
                                                (True, False): "tree ACCEPTED, chain FAILED: depth (or chain temporaries) matters, not only count.",
                                                (False, True): "tree FAILED, chain ACCEPTED: width/live temporaries matter more than depth.",
                                                (False, False): "both FAILED at 96: count limit <= 96; compare with A84."}[(t, c)])
    c3, c6 = g("C3x30"), g("C6x16")
    if c3 is not None and c6 is not None:
        L.append(f"- cones (flattened 92 and 101 ops from several small stores): C3x30 {'OK' if c3 else 'FAIL'}, C6x16 {'OK' if c6 else 'FAIL'}: "
                 + ("total cone size not limiting at ~100 ops with small stores." if (c3 and c6) else
                    "cone total is the limiting quantity." if (not c3 or not c6) else ""))
    r12 = g("R12x8")
    if r12 is not None:
        L.append(f"- R12x8 (12 registers x 8 bits, small ops): {'ACCEPTED, 12 registers alone do not trigger 6057 (the old 11x16 failure needs another cause)' if r12 else 'FAILED, register count is limiting'}")
    txt = "\n".join(L) + "\n"
    with open(os.path.join(outdir, "RESULTS_QPU.md"), "w") as f:
        f.write(txt)
    return txt


def fetch(adapter, manifest_path, wait=False, outdir=OUTDIR):
    man, res = fetch_jobs(adapter, manifest_path, wait=wait, outdir=outdir)
    txt = analyse_manifest(man, res, outdir=outdir)
    print(txt)
    return man, res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--submit", action="store_true", help="dry run unless --execute --confirm HASH")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--combine-p1", nargs=2, metavar=("DIR_A", "DIR_B"), help="pool two executions of the same p1 plan (each dir has manifest.json, fetch_<hash>.json, qpu_*.npz)")
    ap.add_argument("--export-rules", nargs="+", metavar="INPUT",
                    help="build a dccheck rules file from fetched manifests (out/manifest_*.json, needs the sibling fetch_*.json) and/or "
                         "probe_outcomes-format json files; needs --curation and --rules-out (see export_rules.py)")
    ap.add_argument("--curation", default=None, help="with --export-rules: curation file (rules/curation_*.json)")
    ap.add_argument("--rules-out", default=None, help="with --export-rules: output rules file")
    ap.add_argument("--resubmit", action="store_true", help="allow --execute although a manifest for this plan hash already exists (duplicate submission)")
    ap.add_argument("--backend", default="ibm_marrakesh")
    ap.add_argument("--fetch", default=None, help="manifest json")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--tier", default="both")
    ap.add_argument("--only", default="")
    ap.add_argument("--target", default="fake")
    ap.add_argument("--quota-left", type=float, default=240.0)
    a = ap.parse_args()
    if a.export_rules:
        if not (a.curation and a.rules_out):
            raise SystemExit("--export-rules needs --curation FILE and --rules-out FILE")
        import export_rules
        sys.exit(export_rules.main(list(a.export_rules) + ["--curation", a.curation, "--out", a.rules_out]))
    if a.local:
        sys.exit(0 if local(set(filter(None, a.only.split(","))), a.target) else 1)
    if a.plan:
        for t in (["minimal", "ideal"] if a.tier == "both" else [a.tier]):
            print_plan(t, a.quota_left)
    if a.submit:
        t = "minimal" if a.tier == "both" else a.tier
        submit(RealAdapter(a.backend), t, execute=a.execute, confirm=a.confirm, resubmit=a.resubmit)
    if a.fetch:
        fetch(RealAdapter(a.backend), a.fetch, wait=a.wait)
    if a.combine_p1:
        import tiers_v2 as T2
        txt, mm = T2.combine_replicates(a.combine_p1[0], a.combine_p1[1], os.path.join(OUTDIR, "p1comb"))
        print(txt)
        print("MAPPING", "CONSISTENT" if not mm else f"MISMATCH {mm}")
