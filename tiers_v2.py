# SPDX-License-Identifier: Apache-2.0
"""Round-4 redesign: prioritised tiers p1 (acceptance rules), p2 (latency methodology), p3 (cross-backend core).

Programs are defined once as recipes; every circuit is built from the recipe, evaluated by an independent python/numpy
reference, and its cone/liveness metrics are counted from the TRANSPILED circuit and asserted equal to the builder's.
Readback: each acceptance probe has several prepared input patterns (A, B, last-register-only C, middle-register-only D,
all-zero Z); the expected output of each comes from the reference; a pattern is `informative` iff its expected output
differs from the all-zero pattern's (so an implementation that ignores an operand cannot reproduce all patterns).
"""
from __future__ import annotations

import random
import sys

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, transpile
from qiskit.circuit import Clbit
from qiskit.circuit.classical import expr, types

import suite as S

sys.path.insert(0, S.os.path.join(S.EXP, "..", "public", "ibm-controller-conformance"))   # resources.py has ONE implementation, shared with the public checker

U = types.Uint
TIERS = ("p1", "p2", "p3", "v1", "e2")
SHOTS_ACC = 200
VERIFY_LB = 0.6          # an informative pattern is "responding" if its Wilson 95% lower bound exceeds this (chance = 0.5)


def _vn(v, node):
    """name of a register / standalone Var (QPY-loaded standalone Vars expose a UUID as `.var` and the name on the expression node)."""
    n = getattr(v, "name", None)
    return n if n is not None else (getattr(node, "name", None) or str(v))


# ------------------------------------------------------------------ full cone / liveness metrics from a circuit
def analyse_cone(qc):
    """Superset of suite.cone_metrics: measured registers in the cone, total register reads, peak live registers,
    unique (hash-consed) nodes, per-store ops/depth. Everything is counted on the circuit that is passed in."""
    measured = {}
    events = []   # (t, kind, payload)
    for t, i in enumerate(qc.data):
        op = i.operation
        if op.name in ("measure_2", "measure"):
            for cb in i.clbits:
                for reg, _ in qc.find_bit(cb).registers:
                    measured.setdefault(reg.name, t)
    stores = {}     # key -> (expr, t)
    reg_targets = set()
    for t, i in enumerate(qc.data):
        op = i.operation
        if op.name == "store":
            v = op.lvalue.var
            if isinstance(v, Clbit):
                k = ("bit", qc.find_bit(v).index)
            else:
                k = ("reg", _vn(v, op.lvalue))
                reg_targets.add(_vn(v, op.lvalue))
            stores[k] = (op.rvalue, t)

    def keys_of(e, acc):
        if isinstance(e, expr.Var):
            v = e.var
            if isinstance(v, Clbit):
                idx = qc.find_bit(v).index
                regs = qc.find_bit(v).registers
                if ("bit", idx) in stores:
                    acc.append(("bit", idx))
                elif regs and regs[0][0].name in reg_targets:
                    acc.append(("reg", regs[0][0].name))
                elif regs:
                    acc.append(("reg", regs[0][0].name))
                else:
                    acc.append(("bit", idx))
            else:
                acc.append(("reg", _vn(v, e)))
        for a in ("left", "right", "operand", "target", "index"):
            if hasattr(e, a):
                keys_of(getattr(e, a), acc)
        return acc

    # liveness intervals
    first_def, last_use = {}, {}
    for name, t in measured.items():
        first_def[("reg", name)] = t
    for t, i in enumerate(qc.data):
        op = i.operation
        if op.name == "store":
            for k in keys_of(op.rvalue, []):
                last_use[k] = t
            v = op.lvalue.var
            k = ("bit", qc.find_bit(v).index) if isinstance(v, Clbit) else ("reg", _vn(v, op.lvalue))
            first_def.setdefault(k, t)
        elif op.name == "if_else":
            for k in keys_of(op.condition, []):
                last_use[k] = t
    T = len(qc.data)
    peak = peak_bits = 0
    keys = list(first_def)
    for t in range(T):
        live_r = sum(1 for k in keys if k[0] == "reg" and first_def[k] <= t <= last_use.get(k, first_def[k]))
        live_b = sum(1 for k in keys if k[0] == "bit" and first_def[k] <= t <= last_use.get(k, first_def[k]))
        peak, peak_bits = max(peak, live_r), max(peak_bits, live_b)

    # cone from the first `if`
    cond = [i.operation.condition for i in qc.data if i.operation.name == "if_else"][-1]     # the final decision (staging ifs come earlier)
    seen, regs_in, ops_total, max_ops, max_depth, reads_total = set(), set(), 0, 0, 0, 0
    canon_seen = set()
    def canon(e):
        if isinstance(e, expr.Var):
            r = ("v", repr(e.var))
        elif isinstance(e, expr.Value):
            r = ("c", str(e.value))
        elif isinstance(e, expr.Cast):
            r = canon(e.operand)
        elif isinstance(e, expr.Unary):
            r = ("u", str(e.op), canon(e.operand))
        elif isinstance(e, expr.Binary):
            r = ("b", str(e.op), canon(e.left), canon(e.right))
        elif isinstance(e, expr.Index):
            r = ("i", canon(e.target), canon(e.index))
        else:
            r = ("?", repr(e))
        if r[0] in ("u", "b", "i"):
            canon_seen.add(r)
        return r

    def ershov(e):
        """Sethi-Ullman / Ershov number: registers needed to evaluate the tree without spilling (leaf = 1)."""
        if isinstance(e, expr.Binary):
            l, r = ershov(e.left), ershov(e.right)
            return l + 1 if l == r else max(l, r)
        if isinstance(e, expr.Unary):
            return ershov(e.operand)
        if isinstance(e, expr.Index):
            return max(ershov(e.target), ershov(e.index))
        if isinstance(e, expr.Cast):
            return ershov(e.operand)
        return 1
    def lr_need(e):
        """registers needed by a naive left-to-right evaluation that keeps the left operand while the right is evaluated (leaf = 1)."""
        if isinstance(e, expr.Binary):
            return max(lr_need(e.left), 1 + lr_need(e.right))
        if isinstance(e, expr.Unary):
            return lr_need(e.operand)
        if isinstance(e, expr.Index):
            return max(lr_need(e.target), 1 + lr_need(e.index))
        if isinstance(e, expr.Cast):
            return lr_need(e.operand)
        return 1
    temps_max = 0
    temps_lr_max = 0

    def visit(e):
        nonlocal ops_total, max_ops, max_depth, reads_total, temps_max, temps_lr_max
        ks = []
        keys_of(e, ks)
        distinct = set(ks)
        for k in distinct:
            if k in stores:
                if k not in seen:
                    seen.add(k)
                    se = stores[k][0]
                    o = S.tree_ops(se)
                    ops_total += o
                    max_ops = max(max_ops, o)
                    max_depth = max(max_depth, S.tree_depth(se))
                    temps_max = max(temps_max, ershov(se))
                    temps_lr_max = max(temps_lr_max, lr_need(se))
                    canon(se)
                    sks = set(keys_of(se, []))
                    reads_total += len(sks)
                    visit(se)
            elif k[0] == "reg" and k[1] in measured:
                regs_in.add(k[1])
    canon(cond)
    visit(cond)
    final_reads = len(set(keys_of(cond, [])))
    return dict(cone_regs=len(regs_in), peak_live=peak, peak_live_bits=peak_bits, total_reads=reads_total, stores_in_cone=len(seen), cone_ops=ops_total,
                max_store_ops=max_ops, depth=max_depth, unique_nodes=len(canon_seen), cond_reads=final_reads, temps=temps_max, temps_lr=temps_lr_max)


# ------------------------------------------------------------------ register programs (Uint registers)
OPS = {"xor": expr.bit_xor, "and": expr.bit_and, "or": expr.bit_or, "add": expr.add, "mul": expr.mul}


class UProg:
    def __init__(self, name, w, measured, stores, cond=("bit", 0), order=None, note="", seed=0, extra_sets=None, base_sets=None, value_patterns=None):
        self.name, self.w, self.measured, self.stores, self.cond, self.note = name, w, list(measured), stores, cond, note
        self.extra_sets, self.base_sets, self.value_patterns = extra_sets or {}, base_sets, value_patterns
        self.order = order or [("m", self.measured)] + [("s", i) for i in range(len(stores))]
        rng = random.Random(7000 + seed + len(name) * 13)
        self.ups = {r: [rng.randrange(2) for _ in range(w)] for r in self.measured}
        self.read = [r for r in self.measured if r in self._leaves()]
        self._patterns = None

    def _leaves(self):
        out = set()

        def walk(n):
            if n[0] == "r":
                out.add(n[1])
            elif n[0] != "l":
                walk(n[1]) if False else None
                walk(n[2]); walk(n[3])
        for _, node in self.stores:
            walk(node)
        return out

    # reference evaluation (python ints)
    def evaluate(self, vals):
        mask = (1 << self.w) - 1
        env = dict(vals)

        def ev(n):
            if n[0] == "r":
                return env[n[1]]
            if n[0] == "l":
                return n[1] & mask
            a, b = ev(n[2]), ev(n[3])
            return {"xor": a ^ b, "and": a & b, "or": a | b, "add": (a + b) & mask, "mul": (a * b) & mask}[n[1]]
        for tgt, node in self.stores:
            env[tgt] = ev(node)
        final = self.stores[-1][0]
        c = self.cond
        if c[0] == "bit":
            return (env[final] >> c[1]) & 1
        return int(env[final] > c[1])

    def lane(self):
        return self.cond[1] if self.cond[0] == "bit" else 0

    def value(self, reg, on, lanes=None):
        """register value: random fixed upper bits, with the controlled lane(s) set from `on` (bool) or the explicit `lanes` set."""
        up = self.ups[reg]
        lanes = lanes if lanes is not None else ({self.lane()} if on else set())
        return sum(((1 if j in lanes else (0 if j in (self.lane(), 0) else up[j])) << j) for j in range(self.w))

    def patterns(self):
        if self._patterns is not None:
            return self._patterns
        rr = self.read
        n = len(rr)
        def pat(ones):
            if ones and isinstance(next(iter(ones)), tuple):          # explicit (reg, lane) pairs
                by = {}
                for r, ln in ones:
                    by.setdefault(r, set()).add(ln)
                return {r: self.value(r, False, by.get(r, set())) for r in self.measured}
            return {r: self.value(r, r in ones) for r in self.measured}
        sets = {"Z": set()}
        if n >= 4:
            sets["A"], sets["B"] = set(rr[:3]), set(rr[:4])
        elif n == 3:
            sets["A"], sets["B"] = set(rr[:3]), set(rr[:2])
        else:
            sets["A"], sets["B"] = set(rr[:1]), set(rr[:2])
        sets["C"] = {rr[-1]}
        sets["D"] = {rr[n // 2]}
        if self.base_sets:
            sets.update(self.base_sets)
        sets.update(self.extra_sets)
        pats = {k: pat(v) for k, v in sets.items()}
        if self.value_patterns:
            pats = {k: {r: v.get(r, 0) for r in self.measured} for k, v in self.value_patterns.items()}
        if self.cond[0] == "gt" and self.cond[1] is None:
            vals = []
            for k, p in pats.items():
                env = dict(p)
                mask = (1 << self.w) - 1
                tmp = UProg.__new__(UProg)
                tmp.__dict__.update(self.__dict__)
                tmp.cond = ("bit", 0)
                # compute final S value
                def ev(nd):
                    if nd[0] == "r":
                        return env[nd[1]]
                    if nd[0] == "l":
                        return nd[1] & mask
                    a, b = ev(nd[2]), ev(nd[3])
                    return {"xor": a ^ b, "and": a & b, "or": a | b, "add": (a + b) & mask, "mul": (a * b) & mask}[nd[1]]
                for tgt, node in self.stores:
                    env[tgt] = ev(node)
                vals.append(env[self.stores[-1][0]])
            sv = sorted(set(vals))
            names = list(pats)
            def score(T):
                ex = {k: int(v > T) for k, v in zip(names, vals)}
                single = sum(ex[k] != ex["Z"] for k in ("C", "D", "E") if k in ex)
                return (single, sum(ex[k] != ex["Z"] for k in names), -abs(sum(ex.values()) - len(ex) / 2))
            cands = sorted(set([v - 1 for v in sv] + sv))
            self.cond = ("gt", max(cands, key=score))
        out = {}
        z = self.evaluate(pats["Z"])
        for k, p in pats.items():
            e = self.evaluate(p)
            out[k] = dict(values=p, expected=e, informative=(k != "Z" and e != z))
        self._patterns = out
        return out

    def circuit(self, pat, ibm=True):
        w = self.w
        vals = self.patterns()[pat]["values"]
        nq = len(self.measured) * w + 1
        q = QuantumRegister(nq, "q")
        out = ClassicalRegister(1, "out")
        regs = {r: ClassicalRegister(w, r) for r in self.measured}
        qc = QuantumCircuit(q, out, *regs.values(), name=f"{self.name}_{pat}")
        if ibm:
            from qiskit_ibm_runtime.circuit import MidCircuitMeasure
        base = {r: i * w for i, r in enumerate(self.measured)}
        last_store = None
        for kind, arg in self.order:
            if kind == "m":
                if last_store is not None:
                    # enforce staging: the transpiler would otherwise hoist these measurements above the earlier store (no data dependency).
                    # Two identical conditional X gates on the first qubit of the group (net identity) make the measurement depend on the store.
                    grp = [q[base[r] + j] for r in arg for j in range(w)]
                    for _ in range(2):
                        with qc.if_test(expr.lift(last_store[0])):
                            for qq in grp:
                                qc.x(qq)
                for r in arg:
                    for j in range(w):
                        if (vals[r] >> j) & 1:
                            qc.x(q[base[r] + j])
                        qc.append(MidCircuitMeasure(), [q[base[r] + j]], [regs[r][j]]) if ibm else qc.measure(q[base[r] + j], regs[r][j])
            else:
                tgt, node = self.stores[arg]
                t = ClassicalRegister(w, tgt)
                qc.add_register(t)
                regs[tgt] = t

                def ex(n):
                    if n[0] == "r":
                        return regs[n[1]]
                    if n[0] == "l":
                        return expr.lift(n[1], U(w))
                    return OPS[n[1]](ex(n[2]), ex(n[3]))
                qc.store(t, ex(node))
                last_store = t
        final = regs[self.stores[-1][0]]
        c = self.cond
        cexpr = expr.lift(final[c[1]]) if c[0] == "bit" else expr.greater(final, expr.lift(c[1], U(w)))
        with qc.if_test(cexpr):
            qc.x(q[nq - 1])
        qc.measure(q[nq - 1], out[0])
        return qc

    def circuits(self):
        self.patterns()
        return [self.circuit(k) for k in self.patterns()]

    def pattern_meta(self):
        return {k: dict(expected=v["expected"], informative=v["informative"]) for k, v in self.patterns().items()}


def X(a, b):
    return ("xor", None, a, b)  # placeholder (never used)


def node(op, a, b):
    return (op, op, a, b)


def chain(op, names):
    n = ("r", names[0])
    for nm in names[1:]:
        n = (op, op, n, ("r", nm)) if False else (op,) + (op, n, ("r", nm))
    return n


def _mk(op, a, b):
    """internal node: ("op", opname, a, b) is represented as (opname, opname, a, b); UProg.evaluate/ex read n[1], n[2], n[3]."""
    return (op, op, a, b)


def chain_op(op, names):
    n = ("r", names[0])
    for nm in names[1:]:
        n = _mk(op, n, ("r", nm))
    return n


def G(a, b):
    return [f"G{i}" for i in range(a, b)]


def xor_prog(name, nr, w, nread=None, note=""):
    nread = nread or nr
    return UProg(name, w, G(0, nr), [("S", chain_op("xor", G(0, nread)))], note=note)


def overlap_nf(name):
    """non-foldable overlap: S1 = xor(G0..5) bitwise, S2 = add(G4..9) (different op, cannot cancel), S = S1 ^ S2; cond on bit 1 so carries matter."""
    return UProg(name, 2, G(0, 10), [("S1", chain_op("xor", G(0, 6))), ("S2", chain_op("add", G(4, 10))), ("S", _mk("xor", ("r", "S1"), ("r", "S2")))],
                 cond=("bit", 1), note="cone 10, total reads 14, no cancellation (xor vs add)",
                 base_sets={"A": {("G0", 1), ("G1", 1), ("G2", 1)}, "B": {("G0", 1), ("G1", 1), ("G2", 1), ("G3", 1)}, "C": {("G9", 1)}, "D": {("G7", 1)}},
                 extra_sets={"E": {("G4", 0), ("G5", 0)}})   # carry through the add: only the non-foldable path sees G4/G5 lane 0


def split_prog(name, staged=False):
    stores = [("S1", chain_op("xor", G(0, 6))), ("S2", chain_op("xor", G(6, 12))), ("S", _mk("xor", ("r", "S1"), ("r", "S2")))]
    order = [("m", G(0, 6)), ("s", 0), ("m", G(6, 12)), ("s", 1), ("s", 2)] if staged else None
    return UProg(name, 2, G(0, 12), stores, order=order, note=("staged: G6..11 measured after S1 is stored" if staged else "12 registers measured first"))


def alloc_prog(name, k):
    rng = random.Random(900 + k)
    lits = [[rng.randrange(3, 1 << 16) | 1 for _ in range(4)] for _ in range(k)]
    stores = []
    for i in range(k):
        L, M, N, P = lits[i]
        left = _mk("add", _mk("xor", ("r", "A"), ("l", L)), _mk("and", ("r", "B"), ("l", M)))
        right = _mk("add", _mk("or", ("r", "C"), ("l", N)), ("l", P))
        stores.append((f"T{i}", _mk("xor", left, right)))
    fin = ("r", "T0")
    for i in range(1, k):
        fin = _mk("xor", fin, ("r", f"T{i}"))
    stores.append(("S", fin))
    r2 = random.Random(55 + k)
    rv = lambda: r2.randrange(1, 1 << 16)  # noqa: E731
    vp = {"Z": {"A": 0, "B": 0, "C": 0}, "A": {"A": rv(), "B": rv(), "C": rv()}, "B": {"A": rv(), "B": rv(), "C": rv()},
          "C": {"A": 0, "B": 0, "C": rv()}, "D": {"A": 0, "B": rv(), "C": 0}, "E": {"A": rv(), "B": 0, "C": 0}}
    return UProg(name, 16, ["A", "B", "C"], stores, cond=("gt", None), value_patterns=vp,
                 note=f"3 measured Uint16 registers, {k} stored intermediates with distinct literals/ops, then S = xor of all; patterns are full random values, threshold at the median S")


# ------------------------------------------------------------------ Boolean irreducible / DAG programs
class BProg:
    """Bool expression over distinct measured bits of 4 x Uint(32) registers; tree or DAG recipe."""
    def __init__(self, name, root, nleaves, note="", nreg=4, w=32, idx=None):
        self.name, self.root, self.nleaves, self.note, self.nreg, self.w = name, root, nleaves, note, nreg, w
        self.idx = list(idx) if idx is not None else list(range(nleaves))
        self._pats = None

    def evaluate(self, bits):
        memo = {}

        def ev(n):
            k = id(n)
            if k in memo:
                return memo[k]
            if n[0] == "v":
                r = bits[n[1]]
            else:
                a, b = ev(n[2]), ev(n[3])
                r = {"xor": a ^ b, "and": a & b, "or": a | b}[n[1]]
            memo[k] = r
            return r
        return ev(self.root)

    def patterns(self):
        if self._pats is not None:
            return self._pats
        rng = random.Random(4000 + self.nleaves)
        res = {}
        for tgt, w in ((1, "A"), (0, "B")):
            best, bs = None, -1
            for _ in range(60):
                a = [rng.randrange(2) for _ in range(self.nreg * self.w)]
                for _ in range(300):
                    if self.evaluate(a) == tgt:
                        break
                    a[rng.choice(self.idx)] ^= 1
                if self.evaluate(a) != tgt:
                    continue
                st = sum(self.evaluate([x ^ (1 if i == j else 0) for i, x in enumerate(a)]) == tgt for j in self.idx)
                if st > bs:
                    best, bs = list(a), st
            assert best is not None
            # hill-climb stability
            for _ in range(200):
                j = rng.choice(self.idx)
                c = list(best); c[j] ^= 1
                if self.evaluate(c) != tgt:
                    continue
                st = sum(self.evaluate([x ^ (1 if i == j2 else 0) for i, x in enumerate(c)]) == tgt for j2 in self.idx)
                if st >= bs:
                    best, bs = c, st
            res[w] = dict(values=best, expected=tgt, informative=True, stability=bs / len(self.idx))
        self._pats = res
        return res

    def circuit(self, pat, ibm=True):
        a = self.patterns()[pat]["values"]
        nb = self.nreg * self.w
        q = QuantumRegister(nb + 1, "q")
        out, L = ClassicalRegister(1, "out"), ClassicalRegister(1, "L")
        regs = [ClassicalRegister(self.w, f"X{i}") for i in range(self.nreg)]
        qc = QuantumCircuit(q, out, L, *regs, name=f"{self.name}_{pat}")
        if ibm:
            from qiskit_ibm_runtime.circuit import MidCircuitMeasure
        bits = []
        for i in range(nb):
            if a[i]:
                qc.x(q[i])
            qc.append(MidCircuitMeasure(), [q[i]], [regs[i // self.w][i % self.w]]) if ibm else qc.measure(q[i], regs[i // self.w][i % self.w])
            bits.append(regs[i // self.w][i % self.w])
        memo = {}

        def ex(n):
            k = id(n)
            if k in memo:
                return memo[k]
            r = expr.lift(bits[n[1]]) if n[0] == "v" else {"xor": expr.bit_xor, "and": expr.bit_and, "or": expr.bit_or}[n[1]](ex(n[2]), ex(n[3]))
            memo[k] = r
            return r
        qc.store(L[0], ex(self.root))
        with qc.if_test(expr.lift(L[0])):
            qc.x(q[nb])
        qc.measure(q[nb], out[0])
        return qc

    def circuits(self):
        return [self.circuit(k) for k in self.patterns()]

    def pattern_meta(self):
        return {k: dict(expected=v["expected"], informative=True, stability=round(v["stability"], 3)) for k, v in self.patterns().items()}


def _tree(leaves, rng, ops):
    """random binary tree over `leaves` (list of nodes) with exactly len(leaves)-1 ops from `ops`; no shared nodes."""
    nodes = list(leaves)
    while len(nodes) > 1:
        i = rng.randrange(len(nodes) - 1)
        a, b = nodes.pop(i), nodes.pop(i)
        nodes.insert(i, (rng.choice(ops), None, a, b)) if False else nodes.insert(i, ("op", rng.choice(ops), a, b))
    return nodes[0]


def _fix(n):
    """normalise 'op' nodes to (opname, opname, a, b) so evaluate/ex use n[1..3] uniformly; leaves stay ('v', i)."""
    return n


def ir_prog(ops):
    rng = random.Random(8100 + ops)
    leaves = [("v", i) for i in range(ops + 1)]
    rng.shuffle(leaves)
    root = _tree(leaves, rng, ["xor", "and", "or"])
    return BProg(f"P1_ir{ops}", root, ops + 1, note=f"{ops} ops, {ops + 1} DISTINCT measured bits (irreducible), tree = DAG")


def dag_prog(ops=104):
    """R = ((U & a) ^ (U | b)) ^ ((V | c) & (V ^ d)) ^ e with U, V random distinct-leaf trees SHARED (used twice): expanded ops = 2(u+v)+8 = ops."""
    uv = (ops - 8) // 2
    u = uv // 2
    v = uv - u
    rng = random.Random(8300 + ops)
    ids = list(range(128)); rng.shuffle(ids)
    take = lambda n: [("v", ids.pop()) for _ in range(n)]  # noqa: E731
    U_ = _tree(take(u + 1), rng, ["xor", "and", "or"])
    V_ = _tree(take(v + 1), rng, ["xor", "and", "or"])
    a, b, c, d, e = take(5)
    left = ("op", "xor", ("op", "and", U_, a), ("op", "or", U_, b))
    right = ("op", "and", ("op", "or", V_, c), ("op", "xor", V_, d))
    root = ("op", "xor", ("op", "xor", left, right), e)
    used = (u + 1) + (v + 1) + 5
    return BProg(f"P1_dag{ops}", root, 128, note=f"expanded {ops} ops, unique ~{u + v + 8} nodes, {used} distinct bits; U ({u} ops) and V ({v} ops) each used twice")


def _normalise(root):
    """convert ("op", name, a, b) nodes into (name, name, a, b) tuples in place-compatible form (keeps sharing)."""
    memo = {}

    def go(n):
        k = id(n)
        if k in memo:
            return memo[k]
        if n[0] == "v":
            r = n
        else:
            r = (n[1], n[1], go(n[2]), go(n[3]))
        memo[k] = r
        return r
    return go(root)


_orig_init = BProg.__init__


def _init(self, name, root, nleaves, note="", nreg=4, w=32, idx=None):
    _orig_init(self, name, _normalise(root), nleaves, note, nreg, w, idx)


BProg.__init__ = _init


def _bal(leaves, rng, ops):
    """perfectly balanced tree (levels pairwise) over leaves; ershov number = ceil(log2 n)."""
    lvl = list(leaves)
    while len(lvl) > 1:
        nxt = [("op", rng.choice(ops), lvl[i], lvl[i + 1]) for i in range(0, len(lvl) - 1, 2)]
        if len(lvl) % 2:
            nxt.append(lvl[-1])
        lvl = nxt
    return lvl[0]


def _comb(leaves, rng, ops):
    """left comb (chain): ershov number 2."""
    n = leaves[0]
    for lf in leaves[1:]:
        n = ("op", rng.choice(ops), n, lf)
    return n


def stack_prog(name, nreg, w, L, high=True, note=""):
    """Boolean expression with a controlled naive left-to-right stack need L over nreg measured registers (every register is read).
    high=True : root = op(left comb over max(nreg,2) leaves [need 2], right comb over L-1 leaves [need L-1])  ->  LR need exactly L
    high=False: the SAME leaves/ops as a plain left comb (LR need 2).  Leaf j -> register j % nreg, bit j // nreg (distinct bits)."""
    rng = random.Random(9100 + nreg * 31 + L * 7 + (1 if high else 0))
    nx = max(nreg, 2)
    total = nx + (L - 1)
    flat = [(j % nreg) * w + (j // nreg) for j in range(total)]
    ops_ = [rng.choice(["xor", "and", "or"]) for _ in range(total - 1)]
    leaves = [("v", f) for f in flat]
    if high:
        X_ = leaves[0]
        for k in range(1, nx):
            X_ = ("op", ops_[k - 1], X_, leaves[k])
        Y_ = leaves[nx + L - 2]
        for k in range(nx + L - 3, nx - 1, -1):
            Y_ = ("op", ops_[k], leaves[k], Y_)
        root = ("op", ops_[total - 2], X_, Y_)
    else:
        root = leaves[0]
        for k in range(1, total):
            root = ("op", ops_[k - 1], root, leaves[k])
    return BProg(name, root, total, note=note, nreg=nreg, w=w, idx=flat)


def shape_prog(name, shape, ops, nreg, w, note=""):
    """`ops` ops over ops+1 DISTINCT measured bits of nreg registers x w bits; shape 'bal' or 'chain' (same op multiset family, different temporaries)."""
    assert ops + 1 <= nreg * w
    rng = random.Random(8800 + ops + (3 if shape == "chain" else 0))
    leaves = [("v", i) for i in range(ops + 1)]
    rng.shuffle(leaves)
    root = (_bal if shape == "bal" else _comb)(leaves, rng, ["xor", "and", "or"])
    return BProg(name, root, ops + 1, note=note, nreg=nreg, w=w)


# ------------------------------------------------------------------ legacy exact builders (same-day replication of old failures)
def _legacy_paths():
    """Directories holding the original probe builders, from the environment variable CONFORMANCE_LEGACY_DIRS (os.pathsep-separated); unset = not available."""
    for p in [x for x in S.os.environ.get("CONFORMANCE_LEGACY_DIRS", "").split(S.os.pathsep) if x]:
        if p not in sys.path:
            sys.path.append(p)


def legacy_available():
    """The 2026-11-06 / 11-09 replication builders (ibm_6057_repro, simd_probe, qpu_probe, stopwatch) live in the original experiment folders and are NOT shipped
    with the public package; without them the p1 plan omits the four same-day replications of the old failures."""
    try:
        _legacy_paths()
        import ibm_6057_repro, simd_probe, qpu_probe, stopwatch  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def legacy_circuits(name):
    """returns (list of circuits, shots, layout_kind) ; layout_kind 'best' or 'lut85'"""
    _legacy_paths()
    if name == "P1_old_T1":          # 2026-11-06 103-op Shannon expression: ibm_6057_repro.case_T(1)  (random inputs: x(q0), x(q3) preparation as in the original)
        import ibm_6057_repro as RP
        return [RP.case_T(1)], 100, "best"
    if name == "P1_old_CONE":        # the SIMD probe simd_probe CONE (3 measured Uint16 registers, 6 stored Uint16 + R, 173-op cone)
        import simd_probe as SP
        qc, _ = SP.make("CONE", ibm=True)
        return [qc], 200, "best"
    if name == "P1_old_W512":        # the 2026-11-06 probes qpu_probe live(512)
        import qpu_probe as QP
        return [QP.VARIANTS["W512"]()], 100, "best"
    if name == "P1_old_LUT85K8":     # the legacy stopwatch isolated 84-op LUT, K=8, REF0..REF1000 + LUT85 circuits, same payload as the failing job
        import stopwatch as SWL
        names = SWL.circuit_names(("LUT85",))
        return [SWL.build(n, 8, 12, 12, n_src=7) for n in names], 100, "lut85"
    raise KeyError(name)


# ------------------------------------------------------------------ latency probes (methodology tier)
def _soup_probe(name, ops, nbits, seed, shape="tree", note=""):
    """random and/or/xor soup over `nbits` inputs; seed is advanced until the function is balanced (0.25-0.75) and depends on (almost) every input."""
    idx = np.arange(1 << nbits, dtype=np.uint64)
    bits = np.stack([((idx >> np.uint64(i)) & np.uint64(1)).astype(bool) for i in range(nbits)], 1)
    padded = np.concatenate([bits, np.zeros((len(idx), 16 - nbits), bool)], 1) if nbits < 16 else bits
    for k in range(500):
        rec = S.bool_soup(ops, nbits, random.Random(seed + 1000 * k), shape)
        t = S.to_np_wrap(rec, padded, {}).astype(bool)
        infl = sum(bool(np.any(t != t[np.arange(len(t)) ^ (1 << i)])) for i in range(nbits))
        if 0.25 < t.mean() < 0.75 and infl >= nbits - (0 if nbits <= 4 else 2):
            return S.P(name, rec, "bool", 1, note=note)
    raise RuntimeError(f"no balanced soup for {name}")


def lat_probes():
    cat = {p["name"]: p for p in S.catalogue()}
    L = {}
    L["XORC15"] = cat["XORC15"]
    L["S15_16"] = _soup_probe("S15_16", 15, 16, 311, note="15 ops over 16 distinct-ish leaves")
    L["S45_16"] = _soup_probe("S45_16", 45, 16, 312, note="45 ops over the same 16 inputs (leaves repeat)")
    L["S15_4"] = _soup_probe("S15_4", 15, 4, 313, note="15 ops over only 4 inputs")
    r = random.Random(1)
    L["AOC15"] = S.P("AOC15", S.bool_chain(15, 16, r, ("and", "or")), "bool", 1, note="non-reassociable and/or chain, 16 distinct leaves, 8 and + 7 or")
    L["AOT15"] = S.P("AOT15", S.bool_tree(15, 16, r, ("and", "or")), "bool", 1, note="balanced tree, same op multiset as AOC15")
    return L


P2_JOBS = [
    dict(name="P2_place", cases=["NULL", "XORC15"], ref_d=(0, 500, 1000, 2000), refb_d=(0, 500, 1000, 2000),
         note="null job (identical circuit to REF0) + REF-placement check (delay before an unconditional X vs after the conditional X)"),
]
# Deferred (dropped from the plan to fit the quota; README lists ops-vs-inputs as unresolved): kept for a later round.
P2_DEFERRED = [
    dict(name="P2_design", cases=["S15_16", "S45_16", "S15_4", "AOC15", "AOT15"], ref_d=(0, 500, 1000, 2000, 3000), refb_d=(),
         note="ops-vs-inputs (15/45 ops at 16 inputs; 15 ops at 4 vs 16 inputs) and non-reassociable chain vs tree, one calibration for all"),
]


def lat_job_names(j):
    return [f"REF{d}" for d in j["ref_d"]] + [f"REFB{d}" for d in j["refb_d"]] + list(j["cases"])


# ------------------------------------------------------------------ tier definitions
def p1_progs():
    pr = {}
    for suffix in ("a", "b", "c"):
        pr[f"P1_R11x2_{suffix}"] = xor_prog(f"P1_R11x2_{suffix}", 11, 2, note="11 registers x 2 bits, single store (theta between 10 and 12)")
    for suffix in ("a", "b"):
        pr[f"P1_R10x8_{suffix}"] = xor_prog(f"P1_R10x8_{suffix}", 10, 8, note="replicate of the accepted 10-register probe")
        pr[f"P1_R12x2_{suffix}"] = xor_prog(f"P1_R12x2_{suffix}", 12, 2, note="replicate of the rejected 12-register probe")
    pr["P1_overlapNF"] = overlap_nf("P1_overlapNF")
    pr["P1_split6"] = split_prog("P1_split6")
    pr["P1_staged"] = split_prog("P1_staged", staged=True)
    for k in (4, 8, 12):
        pr[f"P1_alloc{k}"] = alloc_prog(f"P1_alloc{k}", k)
    for ops in (96, 104, 126):
        pr[f"P1_ir{ops}"] = ir_prog(ops)
    pr["P1_dag104"] = dag_prog(104)
    # temporaries probes: same ops/leaves family, balanced tree (ershov ~7) vs left chain (ershov 2)
    pr["P1_bal104"] = shape_prog("P1_bal104", "bal", 104, 4, 32, note="104 ops, 105 distinct bits, perfectly balanced tree (many live temporaries)")
    pr["P1_chain104"] = shape_prog("P1_chain104", "chain", 104, 4, 32, note="104 ops, 105 distinct bits, left chain (2 temporaries)")
    # combined: 8 measured registers + a 63-op expression: each single-factor rule passes it, an allocation pool (registers + temporaries) may not
    pr["P1_C8bal"] = shape_prog("P1_C8bal", "bal", 63, 8, 8, note="8 measured registers x 8 bits, 63-op balanced tree over 64 distinct bits")
    pr["P1_C8chain"] = shape_prog("P1_C8chain", "chain", 63, 8, 8, note="same 8 registers, 63-op left chain (control: same registers and ops, few temporaries)")
    return pr


def p3_progs():
    return {"P3_R10x2": xor_prog("P3_R10x2", 10, 2, note="cross-backend: 10 registers (expect accept)"),
            "P3_R11x2": xor_prog("P3_R11x2", 11, 2, note="cross-backend: 11 registers"),
            "P3_R12x2": xor_prog("P3_R12x2", 12, 2, note="cross-backend: 12 registers (expect 6057)"),
            "P3_split6": split_prog("P3_split6"), "P3_overlapNF": overlap_nf("P3_overlapNF")}


P1_LEGACY = ["P1_old_T1", "P1_old_CONE", "P1_old_LUT85K8", "P1_old_W512"]
_CACHE = {}


def get_prog(name):
    if "p1" not in _CACHE:
        _CACHE["p1"] = p1_progs()
        _CACHE["p3"] = p3_progs()
        _CACHE["v1"] = v1_progs()
        _CACHE["e1"] = e1_progs()
        _CACHE["e2"] = e2_progs()
    return _CACHE["p1"].get(name) or _CACHE["p3"].get(name) or _CACHE["v1"].get(name) or _CACHE["e1"].get(name) or _CACHE["e2"][name]


def plan_jobs(tier):
    jobs = []
    if tier == "p1":
        for nm in get_prog("P1_R11x2_a") and _CACHE["p1"]:
            p = _CACHE["p1"][nm]
            jobs.append(dict(kind="PRG", name=nm, circuits=len(p.patterns()), K=1, shots=SHOTS_ACC, cost=S.ACC_COST, note=p.note))
        for nm in (P1_LEGACY if legacy_available() else []):
            jobs.append(dict(kind="LEG", name=nm, circuits=(5 if nm == "P1_old_LUT85K8" else 1), K=(8 if nm == "P1_old_LUT85K8" else 1), shots=100,
                             cost=(3.0 if nm == "P1_old_LUT85K8" else S.ACC_COST), note="same-day replication of an old failing shape"))
    elif tier == "e1":
        raise SystemExit("tier e1 was SUBMITTED as plan 7b05c7152abf; analyse it from out/manifest_7b05c7152abf.json (the current e1 builders are not what ran). Use tier e2 for new probes.")
    elif tier == "e2":
        get_prog("P1_R11x2_a")
        for nm, p in _CACHE["e2"].items():
            jobs.append(dict(kind="PRG", name=nm, circuits=len(p.patterns()), K=1, shots=SHOTS_ACC, cost=S.ACC_COST, note=p.note))
    elif tier == "v1":
        get_prog("P1_R11x2_a")
        for nm, p in _CACHE["v1"].items():
            jobs.append(dict(kind="PRG", name=nm, circuits=len(p.patterns()), K=1, shots=SHOTS_ACC, cost=S.ACC_COST, note=p.note))
    elif tier == "p3":
        get_prog("P1_R11x2_a")
        for nm, p in _CACHE["p3"].items():
            jobs.append(dict(kind="PRG", name=nm, circuits=len(p.patterns()), K=1, shots=SHOTS_ACC, cost=S.ACC_COST, note=p.note))
    elif tier == "p2":
        for j in P2_JOBS:
            n = len(lat_job_names(j))
            jobs.append(dict(kind="LATM", name=j["name"], circuits=n, K=16, shots=3000,
                             cost=S.lat_cost_s(16, 3000, len(j["cases"]), n_ref=len(j["ref_d"]) + len(j["refb_d"])), note=j["note"]))
    else:
        raise KeyError(tier)
    return jobs, float(sum(j["cost"] for j in jobs))


# ------------------------------------------------------------------ plan build (circuits, transpile, assertions, hash)
def _lat_circuit(jobspec, name, probes):
    if name.startswith("REF") or name == "NULL":
        return S.build_lat(None, K=16, n_z=S.LAT_NZ, n_x=S.LAT_NX, case=name)
    return S.build_lat(probes[name], K=16, n_z=S.LAT_NZ, n_x=S.LAT_NX, case="CASE")


def build(adapter, tier, shots=None):
    import hashlib
    import io
    from qiskit import qpy
    be = adapter.backend()
    jobs_cfg, _ = plan_jobs(tier)
    layout, info = (None, {})
    if tier == "p2":
        layout, info = S.SW.pick_layout(be, S.LAT_NZ, S.LAT_NX, 24)
    h = hashlib.sha256()
    h.update(__import__("json").dumps({"backend": getattr(adapter, "name", "?"), "layout": layout, "tier": tier, "shots": shots}, sort_keys=True).encode())
    probes = lat_probes() if tier == "p2" else None
    jobs = []
    for j in jobs_cfg:
        meta = {}
        if j["kind"] == "PRG":
            prog = get_prog(j["name"])
            circs = prog.circuits()
            gq = S.good_qubits(be, circs[0].num_qubits)
            lay = gq[1:] + [gq[0]]
            isas = [transpile(c, be, initial_layout=lay, optimization_level=1, seed_transpiler=7) for c in circs]
            names = [c.name for c in circs]
            nsh, jlay = j["shots"], lay
            meta = dict(patterns=prog.pattern_meta(), note=prog.note, kind=type(prog).__name__)
        elif j["kind"] == "LEG":
            circs, nsh0, lk = legacy_circuits(j["name"])
            nsh = nsh0
            for i_, c_ in enumerate(circs):          # auto-generated names ("circuit-N") come from a global counter and would make the plan hash non-deterministic
                if c_.name.startswith("circuit-") or not c_.name:
                    c_.name = f"{j['name']}_{i_}"
            if lk == "lut85":
                jlay, _inf = S.SW.pick_layout(be, 12, 12, 7)
                isas = [transpile(c, be, initial_layout=jlay, optimization_level=1, seed_transpiler=7) for c in circs]
            else:
                gq = S.good_qubits(be, circs[0].num_qubits)
                jlay = gq
                isas = [transpile(c, be, initial_layout=jlay, optimization_level=1, seed_transpiler=7) for c in circs]
            names = [c.name or f"{j['name']}_{i}" for i, c in enumerate(circs)]
            meta = dict(note=j["note"], source="legacy builder (see tiers_v2.legacy_circuits)")
        else:   # LATM
            spec = next(x for x in P2_JOBS if x["name"] == j["name"])
            names = lat_job_names(spec)
            circs = [_lat_circuit(spec, n, probes) for n in names]
            isas = [transpile(c, be, initial_layout=layout, optimization_level=1, seed_transpiler=7) for c in circs]
            nsh, jlay = (shots or j["shots"]), layout
            meta = dict(cases=spec["cases"], ref_d=list(spec["ref_d"]), refb_d=list(spec["refb_d"]), note=spec["note"],
                        structure={c: S.structure(probes[c]) for c in spec["cases"] if c in probes})
        blobs = []
        for c in isas:
            buf = io.BytesIO()
            qpy.dump(c, buf)
            blobs.append(buf.getvalue())
        metrics = None
        if j["kind"] == "PRG" and j["name"].startswith(("E1_", "E2_")):
            import resources as RS
            vt = [RS.resource_vector(c) for c in isas]
            assert all(x == vt[0] for x in vt), (j["name"], "patterns differ in resource vector after transpile")
            meta["vector"] = vt[0]
            meta["vector_builder"] = RS.resource_vector(circs[0])
            _E1_PLAN_VECTORS[j["name"]] = vt[0]
            metrics = analyse_cone(isas[0])
        elif j["kind"] in ("PRG", "LEG"):
            mb = analyse_cone(circs[-1] if j["kind"] == "LEG" else circs[0])
            for c in (isas if j["kind"] == "PRG" else isas[-1:]):
                mt = analyse_cone(c)
                assert mt == mb, (j["name"], "transpiled metrics differ from builder", mt, mb)
            metrics = mb
        h.update(__import__("json").dumps([j["name"], nsh, names]).encode())
        for c in isas:
            h.update(hashlib.sha256(S.canon(c).encode()).digest())
        jobs.append(dict(kind=j["kind"], name=j["name"], circuit_names=names, shots=nsh, K=j["K"], est_s=j["cost"], isas=isas, qpy=blobs, layout=jlay,
                         metrics=metrics, store_report=None, expected_p=None, meta=meta))
    return dict(tier=tier, backend=getattr(adapter, "name", "?"), layout=layout, probe_info=info, shots=shots or 0, jobs=jobs,
                est_total_s=float(sum(x["est_s"] for x in jobs)), hash=h.hexdigest()[:12])


# ------------------------------------------------------------------ predictions
_BRACKET = {}
_E1_PLAN_VECTORS = {}


def _bracket():
    """Brackets from the executed R10x8 (accepted) and R12x2 (rejected) probes, FROZEN as constants so that analysis never calls a builder
    (values computed once with analyse_cone on those circuits: peak live 11 / 13, total reads 10 / 12, pool = peak + Ershov temps 13 / 15)."""
    if not _BRACKET:
        _BRACKET.update(peak_pass=11, peak_fail=13, reads_pass=10, reads_fail=12, pool_pass=13, pool_fail=15)
    return _BRACKET


HYPS = ["H_cone", "H_peak_live", "H_alloc", "H_pool_LR", "H_total_reads", "H_cone+store_ops", "H_cone+cone_ops", "H_cone+unique_nodes", "H_cone+depth", "H_historic"]


def hyp_outcomes(m, legacy=False):
    b = _bracket()

    def reg(m):
        return "F" if m["cone_regs"] >= 12 else ("?" if m["cone_regs"] == 11 else "P")

    def comb(a, bb):
        if a == "F" or bb == "F":
            return "F"
        return "?" if "?" in (a, bb) else "P"
    r = reg(m)
    so = "F" if m["max_store_ops"] >= 103 else ("P" if m["max_store_ops"] <= 96 else "?")
    co = "F" if m["cone_ops"] >= 104 else ("P" if m["cone_ops"] <= 101 else "?")
    un = "F" if m["unique_nodes"] >= 103 else ("P" if m["unique_nodes"] <= 96 else "?")
    dp = "P" if m["depth"] <= 96 else "?"
    pk = "P" if m["peak_live"] <= b["peak_pass"] else ("F" if m["peak_live"] >= b["peak_fail"] else "?")
    tr = "P" if m["total_reads"] <= b["reads_pass"] else ("F" if m["total_reads"] >= b["reads_fail"] else "?")
    pool = m["peak_live"] + m["temps"]
    al_r = "P" if pool <= b["pool_pass"] else ("F" if pool >= b["pool_fail"] else "?")
    al_v = "P" if m["peak_live_bits"] <= 257 else ("F" if m["peak_live_bits"] >= 512 else "?")
    al = "F" if "F" in (al_r, al_v) else ("?" if "?" in (al_r, al_v) else "P")
    plr = m["peak_live"] + m["temps_lr"]
    pl_r = "P" if plr <= 13 else ("F" if plr >= 15 else "?")
    pl_v = "P" if m["peak_live_bits"] <= 257 else ("F" if m["peak_live_bits"] >= 512 else "?")
    pl = "F" if "F" in (pl_r, pl_v) else ("?" if "?" in (pl_r, pl_v) else "P")
    return {"H_cone": r, "H_peak_live": pk, "H_alloc": al, "H_pool_LR": pl, "H_total_reads": tr, "H_cone+store_ops": comb(r, so), "H_cone+cone_ops": comb(r, co),
            "H_cone+unique_nodes": comb(r, un), "H_cone+depth": comb(r, dp), "H_historic": ("F" if legacy else "-")}


def predictions_text(tier):
    if tier == "e2":
        return e2_predictions_text()
    if tier == "e1":
        return e1_predictions_text()
    if tier == "v1":
        return v1_predictions_text()
    b = _bracket()
    L = [f"Pre-registered predictions, tier {tier} (written to the manifest before submission; no external timestamp). P pass / F fail / ? threshold-dependent / - n.a.",
         f"Op definition: {S.OP_DEF}",
         "Hypotheses (each fails iff its metric reaches a threshold; thresholds bracketed by earlier data):",
         "  H_cone: >= 12 distinct measured registers in the cone of the `if` (10 pass, 12 fail, 11 untested).",
         f"  H_peak_live: registers (measured + register-valued stores) simultaneously live >= theta; bracket: R10x8 peak {b['peak_pass']} pass, R12x2 peak {b['peak_fail']} fail (register allocation pressure).",
         f"  H_alloc (a model of acceptance behaviour: one shared budget of registers, the reading suggested by IBM's 6057 text): register pool = peak live registers (measured + register-valued stores) + temporaries needed "
         f"by the largest stored expression (Ershov/Sethi-Ullman number of its tree: chain 2, balanced tree ~log2 leaves); fails iff pool >= theta, bracket {b['pool_pass']} pass (R10x8) / {b['pool_fail']} fail (R12x2); "
         "plus a separate pool of stored BIT variables: <= 257 pass, >= 512 fail (old W256/W512 jobs). Unifies the cone rule (measured registers live) and size failures only if long expressions "
         "need many temporaries; it does NOT by itself explain the old 103-op Shannon failure (small Ershov number), which this tier tests directly.",
         "  H_pool_LR (POST HOC, fitted on the first 25 p1 probes after seeing results; only these predictions are not blind): pool = peak live registers + left-to-right expression-stack need; "
         "pass <= 13, fail >= 15, 14 unresolved (R11x2 passed at 14; alloc8/ir104/dag104 failed at 14); bit-variable pool 257/512 as above. Known exceptions: old T1 and LUT85 (pool 12, failed) and W512 (bit pool).",
         f"  H_total_reads: sum of distinct registers read over all stores in the cone >= theta; bracket {b['reads_pass']} pass / {b['reads_fail']} fail.",
         "  H_cone+store_ops / +cone_ops / +unique_nodes / +depth: H_cone plus a second trigger on max ops of one store (theta in (96,103]), total ops in the cone (theta in (101,104]), unique (hash-consed) nodes "
         "(same bracket as store ops), or expression depth (known pass up to 96).",
         "  H_historic: the old failing shapes (2026-11-06/09) still fail today; only defined for same-day replications.",
         "Known facts: R10x8 pass, R12x8/R12x2 fail, S_12split6/chain/24split3x8 fail, S_10overlap pass (foldable), A96 single store pass, cone of 6 small stores (101 ops) pass; old failures: 103-op Shannon "
         "(1 register), 6 Shannon stores, CONE (3 registers, 173-op cone), W512 live bits, LUT85 K=8."]
    if tier in ("p1", "p3"):
        get_prog("P1_R11x2_a")
        progs = _CACHE["p1"] if tier == "p1" else _CACHE["p3"]
        L.append(f"{'probe':16s} {'regs':>4s} {'peak':>4s} {'tmp':>3s} {'reads':>5s} {'stores':>6s} {'maxst':>5s} {'coneops':>7s} {'uniq':>5s} {'depth':>5s} | " + " ".join(f"{h.replace('H_', ''):>9s}" for h in HYPS))
        items = [(nm, analyse_cone(p.circuits()[0]), False) for nm, p in progs.items()]
        if tier == "p1" and legacy_available():
            for nm in P1_LEGACY:
                cs, _, _ = legacy_circuits(nm)
                items.append((nm, analyse_cone(cs[-1]), True))
        for nm, m, leg in items:
            o = hyp_outcomes(m, leg)
            L.append(f"{nm:16s} {m['cone_regs']:4d} {m['peak_live']:4d} {m['temps']:3d} {m['total_reads']:5d} {m['stores_in_cone']:6d} {m['max_store_ops']:5d} {m['cone_ops']:7d} {m['unique_nodes']:5d} {m['depth']:5d} | " + " ".join(f"{o[h]:>9s}" for h in HYPS))
        L += ["Allocation-model discriminators: P1_C8bal (8 measured registers + 63-op balanced tree) vs P1_C8chain (same registers and ops, left chain): H_cone and every op-limit rule predict BOTH pass; H_alloc predicts bal fails "
              "and chain passes. P1_bal104 vs P1_chain104 (104 ops, 4 registers; modelled pool 11 vs 6, both below the allocation bracket): an op-count limit predicts both fail, H_alloc predicts both pass, so this pair separates H_alloc from an op limit; if only the balanced tree fails, temporaries matter beyond the modelled pool. "
              "Key discriminators: staged vs split6 (liveness vs distinct count: H_peak_live predicts staged pass, H_cone predicts fail); overlapNF (total reads vs cone, non-foldable); alloc4/8/12 (3 measured registers with "
              "many stored intermediates: H_cone passes all, H_peak_live fails alloc12, H_total_reads fails all); ir96/104/126 vs dag104 (op-count trigger and expanded vs unique counting); "
              "legacy replications (H_historic F, H_cone P for T1/LUT85/CONE/W512); R11x2 x3 (theta 11 vs 12, plus determinism of the outcome)."]
    elif tier == "p2":
        L += ["Latency methodology predictions (increment over a 1-bit if, Huber +/- jackknife from the round-1 estimator):",
              "  NULL (a case identical to REF0): increment = 0 +/- ~60 ns; spread across probes = false-increment noise floor.",
              "  placement: if REF-after (delay after the conditional X) and REFB-before agree, XORC15 increment estimated through both curves agrees within ~2 sigma for Z and X probes separately; "
              "if the X-probe excess (round 1: X median above Z in 4 of 5 jobs) is a calibration artefact it disappears with REFB.",
              "  design (round-1 slope 28 ns/op at 16 inputs): S45_16 - S15_16 = 30 ops -> ~+850 ns if ops drive latency at fixed inputs; S15_16 - S15_4 (12 more inputs at 15 ops) -> ~0 if only ops matter, "
              "~+100 ns if ~8 ns/input; AOC15 - AOT15 (non-reassociable chain vs tree, same op multiset) -> 0 if depth is irrelevant even without re-association, large positive if serial depth costs.",
              "  H_ops_only: lat ~ 293 + 28.3*ops (round 1). H_ops_plus_inputs: 180 + 28.5*ops + 8*inputs. Predictions for S15_16 / S45_16 / S15_4 under H_ops_only: "
              "720 / 1565 / 720 ns; under H_ops_plus_inputs: 735 / 1580 / 640 ns."]
    return "\n".join(L)


# ------------------------------------------------------------------ analysis
def _pattern_rows(rec, res_r, outdir):
    z = np.load(S.os.path.join(outdir, res_r["npz"]))
    rows = []
    for pat, pm in rec["meta"]["patterns"].items():
        o = z[f"out_{rec['name']}_{pat}"].astype(bool).ravel()
        exp = int(pm["expected"])
        k = int((o == bool(exp)).sum())
        lo, hi = S.wilson(k, len(o))
        rows.append(dict(pat=pat, expected=exp, informative=pm["informative"], correct=k / len(o), lo=lo, hi=hi, n=len(o)))
    return rows


def analyse_acc(man, res, outdir):
    tier = man["tier"]
    L = [f"# QPU results {tier}, plan {man['plan_hash']} on {man['backend']}", f"submitted {man['submitted_at']}; est {man['est_total_s']:.0f} QPU-s", "",
         f"Op definition: {S.OP_DEF}", "Metrics are counted on the TRANSPILED circuits (asserted equal to the builder at plan time).",
         "Backend info at submission: " + S.json.dumps(man.get("backend_info", {})), "",
         f"| probe | cone regs | peak live | temps | total reads | stores | max store ops | cone ops | unique nodes | result | code | informative patterns responding (CI lower > {VERIFY_LB}) | per-pattern correct [95% CI] |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    outcome, msgs = {}, {}
    for rec in man["jobs"]:
        r = res[rec["name"]]
        m = rec["metrics"] or {}
        acc = r["status"] == "DONE"
        rb, resp = "", ""
        if acc and rec["kind"] == "PRG":
            rows = _pattern_rows(rec, r, outdir)
            inf = [x for x in rows if x["informative"]]
            resp = f"{sum(x['lo'] > VERIFY_LB for x in inf)}/{len(inf)}"
            rb = "; ".join(f"{x['pat']}{'*' if x['informative'] else ''}:{x['correct']:.2f} [{x['lo']:.2f},{x['hi']:.2f}]" for x in rows)
        L.append(f"| {rec['name']} | {m.get('cone_regs', '')} | {m.get('peak_live', '')} | {m.get('temps', '')} | {m.get('total_reads', '')} | {m.get('stores_in_cone', '')} | {m.get('max_store_ops', '')} | "
                 f"{m.get('cone_ops', '')} | {m.get('unique_nodes', '')} | {'ACCEPTED' if acc else r['status']} | {r['code'] or ''} | {resp} | {rb} |")
        if acc or r["code"] == 6057:
            outcome[rec["name"]] = acc
        if not acc and r["message"]:
            msgs.setdefault(r["message"], []).append(rec["name"])
    L += ["", "(* = informative: its expected output differs from the all-zero pattern's, so ignoring operands cannot reproduce it)", "", "## Full error messages (verbatim)"]
    L += [f"- {', '.join(ns)}: `{t}`" for t, ns in msgs.items()] or ["- none (no failures)"]
    # scoreboard
    L += ["", "## Pre-registered predictions vs outcome", "| probe | observed | " + " | ".join(h.replace("H_", "") + (" (post hoc)" if h == "H_pool_LR" else "") for h in HYPS) + " |", "|---|---|" + "---|" * len(HYPS)]
    score = {h: [0, 0] for h in HYPS}
    for rec in man["jobs"]:
        if rec["name"] not in outcome:
            continue
        o = hyp_outcomes(rec["metrics"], rec["kind"] == "LEG")
        cells = []
        for h in HYPS:
            v = o[h]
            ok = (v == "P" and outcome[rec["name"]]) or (v == "F" and not outcome[rec["name"]])
            bad = (v == "P" and not outcome[rec["name"]]) or (v == "F" and outcome[rec["name"]])
            cells.append(f"{v}{' ok' if ok else (' WRONG' if bad else '')}")
            score[h][0] += int(ok)
            score[h][1] += int(bad)
        L.append(f"| {rec['name']} | {'pass' if outcome[rec['name']] else 'FAIL'} | " + " | ".join(cells) + " |")
    L.append("| score ok / wrong | | " + " | ".join(f"{score[h][0]} / {score[h][1]}" for h in HYPS) + " |")
    o = outcome
    L += ["", "## Verdict lines (rule-based; read against the tables)"]
    grp = lambda pref: {k: v for k, v in o.items() if k.startswith(pref)}  # noqa: E731
    r11 = grp("P1_R11x2_") or grp("P3_R11x2")
    if r11:
        vals = set(r11.values())
        L.append(f"- R11x2 ({len(r11)} runs): " + ("all ACCEPTED -> theta = 12" if vals == {True} else "all FAILED -> theta = 11" if vals == {False} else
                 "MIXED outcomes for an identical circuit: the limit is not a deterministic function of this program (treat all pass/fail boundaries as probabilistic)"))
    for pref, exp, lab in (("P1_R10x8_", True, "R10x8"), ("P1_R12x2_", False, "R12x2")):
        g = grp(pref)
        if g:
            L.append(f"- {lab} replicates: {sorted(g.values())} (expected {'accepted' if exp else '6057'}): " + ("as expected" if all(v == exp for v in g.values()) else "DEVIATES"))
    if "P1_overlapNF" in o or "P3_overlapNF" in o:
        v = o.get("P1_overlapNF", o.get("P3_overlapNF"))
        L.append(f"- non-foldable overlap (cone 10, total reads 14, xor vs add): {'ACCEPTED -> total reads is not the trigger' if v else 'FAILED -> total reads (not only the cone) matters'}")
    if "P1_staged" in o and "P1_split6" in o:
        L.append(f"- staged liveness: staged {'accepted' if o['P1_staged'] else 'FAILED'}, same stores unstaged {'accepted' if o['P1_split6'] else 'FAILED'} -> "
                 + ("distinct measured registers in the cone is not the trigger; simultaneous liveness is" if o["P1_staged"] and not o["P1_split6"] else
                    "staging does not help: distinct measured registers (cone) or total reads decide" if not o["P1_staged"] and not o["P1_split6"] else
                    "both accepted: the 6057 rule is not reproduced for split stores today (compare with round 3)"))
    al = {k: o[k] for k in ("P1_alloc4", "P1_alloc8", "P1_alloc12") if k in o}
    if al:
        L.append(f"- allocation pressure (3 measured registers, k stored Uint16 intermediates): {al} -> "
                 + ("all accepted: stored intermediates do not exhaust the modelled budget at 12 (peak live 15)" if all(al.values()) else
                    "failures grow with k: register-allocation pressure (stored registers) matters even with only 3 measured registers" if not al.get("P1_alloc12", True) else "irregular, see table"))
    ir = {k: o[k] for k in ("P1_ir96", "P1_ir104", "P1_ir126", "P1_dag104") if k in o}
    if ir:
        L.append(f"- irreducible op-count bisection (distinct leaves, 4 measured registers): {ir}")
        if "P1_dag104" in ir and "P1_ir104" in ir:
            L.append("- DAG vs tree at 104 expanded ops: " + ("same outcome -> the count is not on unique nodes" if ir["P1_dag104"] == ir["P1_ir104"] else
                     "DIFFERENT outcomes -> the controller counts " + ("unique (shared) nodes" if ir["P1_dag104"] and not ir["P1_ir104"] else "something else; inspect")))
        if all(ir.get(k, True) for k in ("P1_ir96", "P1_ir104", "P1_ir126")):
            L.append("- irreducible expressions accepted up to 126 ops: NOT reproduced as an op-count limit for this construction; the old 103-op failure is not explained by op count alone.")
    for nm in P1_LEGACY:
        if nm in o:
            L.append(f"- {nm} (old shape, replicated today): {'ACCEPTED -> the old failure did NOT reproduce (controller/compiler change or nondeterminism)' if o[nm] else 'FAILED again -> still present today'}")
    cb, cc, c8b, c8c = (o.get(k) for k in ("P1_bal104", "P1_chain104", "P1_C8bal", "P1_C8chain"))
    if cb is not None and cc is not None:
        L.append(f"- 104 ops, balanced tree vs left chain (4 registers): bal {'accepted' if cb else 'FAILED'}, chain {'accepted' if cc else 'FAILED'} -> "
                 + ("temporaries (tree shape), not op count, decide: allocation-pool behaviour" if (not cb and cc) else "shape does not matter" if cb == cc else "chain fails while the tree passes: unexpected, inspect"))
    if c8b is not None and c8c is not None:
        L.append(f"- 8 measured registers + 63 ops: balanced {'accepted' if c8b else 'FAILED'}, chain {'accepted' if c8c else 'FAILED'} -> "
                 + ("combined pressure (registers + temporaries) triggers 6057 although each single-factor rule predicts a pass: H_alloc supported" if (not c8b and c8c) else
                    "both accepted: no combined effect at this size" if (c8b and c8c) else "both failed: unexpected, inspect" if not c8b else "chain fails while the tree passes: unexpected, inspect"))
    codes = {}
    for rec in man["jobs"]:
        r = res[rec["name"]]
        if r["status"] != "DONE":
            codes.setdefault(r["code"], []).append(rec["name"])
    if codes:
        L.append("- failure codes observed: " + "; ".join(f"{c if c else 'none (no code in message)'}: {', '.join(v)}" for c, v in codes.items())
                 + ". IBM's registry: 6057 = more classical registers than the control hardware provides; 6073 = classical-control MEMORY limit; 6116 = control-system resources / amount of control flow. "
                 "A 6073 or 6116 here would point to a different resource than registers.")
    if not outcome:
        nq = sum(1 for rec in man["jobs"] if res[rec["name"]]["status"] not in ("DONE", "ERROR"))
        L.append(f"- no outcomes yet ({nq} of {len(man['jobs'])} jobs not finished); no hypothesis is evaluated.")
    else:
        live = [h for h in HYPS if score[h][1] == 0 and h != "H_historic"]
        L.append("- Hypotheses with no wrong prediction among this tier's outcomes: " + (", ".join(live) if live else "none"))
    txt = "\n".join(L) + "\n"
    open(S.os.path.join(outdir, f"RESULTS_QPU_{tier}.md"), "w").write(txt)
    return txt


def analyse_lat(man, res, outdir):
    import estimators as EST
    L = [f"# QPU results p2 (latency methodology), plan {man['plan_hash']} on {man['backend']}", f"submitted {man['submitted_at']}; est {man['est_total_s']:.0f} QPU-s; K={man['lat_k']}",
         "Backend info at submission: " + S.json.dumps(man.get("backend_info", {})), "", "## Jobs", "| job | status | code | message |", "|---|---|---|---|"]
    est = {}
    for rec in man["jobs"]:
        r = res[rec["name"]]
        L.append(f"| {rec['name']} | {r['status']} | {r['code'] or ''} | {(r['message'] or '')[:200].replace('|', '/')} |")
        if r["status"] != "DONE":
            continue
        z = np.load(S.os.path.join(outdir, r["npz"]))
        prs = {k[3:]: z[k] for k in z.files if k.startswith("pr_")}
        meta = rec["meta"]
        rd = tuple(meta["ref_d"])
        for case in meta["cases"]:
            rb = EST.analyse_robust(prs, man["n_z"], man["lat_k"], rd, case)
            est[(rec["name"], case, "after")] = rb
        if meta["refb_d"]:
            rbd = tuple(meta["refb_d"])
            for case in ["REF0"] + list(meta["cases"]):
                if case == "NULL":
                    continue
                est[(rec["name"], case, "before")] = EST.analyse_robust(prs, man["n_z"], man["lat_k"], rbd, case, ref_prefix="REFB")
    L += ["", "## Estimates (ns; Huber +/- jackknife, median, Z-probe / X-probe medians, probe chi2/dof)", "| job | case | calibration | Huber | median | Z median | X median | chi2/dof | usable |", "|---|---|---|---|---|---|---|---|---|"]
    for (job, case, cal), rb in est.items():
        if "error" in rb:
            L.append(f"| {job} | {case} | {cal} | {rb['error']} |||||")
            continue
        L.append(f"| {job} | {case} | {cal} | {rb['huber']:.0f} +/- {rb['jackknife_se_huber']:.0f} | {rb['median']:.0f} | {rb.get('median_Z', float('nan')):.0f} | {rb.get('median_X', float('nan')):.0f} | {rb['chi2_per_dof']:.1f} | {rb['n_probes_usable']}/{rb['n_probes_total']} |")
    def hv(job, case, cal="after"):
        rb = est.get((job, case, cal))
        return (rb["huber"], rb["jackknife_se_huber"]) if rb and "error" not in rb else None
    L += ["", "## Contrasts"]
    n = hv("P2_place", "NULL")
    if n:
        L.append(f"- NULL (identical to REF0) increment: {n[0]:+.0f} +/- {n[1]:.0f} ns -> {'consistent with 0' if abs(n[0]) < 2 * n[1] else 'NONZERO false increment: the noise floor/systematic is at least this large'}")
    a, bf, b0 = hv("P2_place", "XORC15"), hv("P2_place", "XORC15", "before"), hv("P2_place", "REF0", "before")
    if a and bf and b0:
        inc_b = bf[0] - b0[0]
        se = float(np.hypot(bf[1], b0[1]))
        L.append(f"- placement check, XORC15 increment over a 1-bit if: via REF-after {a[0]:.0f} +/- {a[1]:.0f}; via REFB-before {inc_b:.0f} +/- {se:.0f} (difference {a[0] - inc_b:+.0f} +/- {np.hypot(a[1], se):.0f} ns)")
        for lab in ("Z", "X"):
            ra, rbf, r0 = est[("P2_place", "XORC15", "after")], est[("P2_place", "XORC15", "before")], est[("P2_place", "REF0", "before")]
            if f"median_{lab}" in ra and f"median_{lab}" in rbf and f"median_{lab}" in r0:
                L.append(f"  - {lab}-type probes: after {ra[f'median_{lab}']:.0f}, before-minus-REF0 {rbf[f'median_{lab}'] - r0[f'median_{lab}']:.0f}")
        rz, rx = est[("P2_place", "XORC15", "after")].get("median_Z"), est[("P2_place", "XORC15", "after")].get("median_X")
        if rz is not None and rx is not None:
            L.append(f"- X-minus-Z median asymmetry with REF-after calibration: {rx - rz:+.0f} ns (round 1: +49, +98, -63, +48, +386)")
    s15, s45, s154 = hv("P2_design", "S15_16"), hv("P2_design", "S45_16"), hv("P2_design", "S15_4")
    st = {c: est_meta for c, est_meta in {}.items()}
    if s15 and s45:
        d = s45[0] - s15[0]
        se = float(np.hypot(s15[1], s45[1]))
        L.append(f"- ops at fixed 16 inputs: S45_16 - S15_16 = {d:+.0f} +/- {se:.0f} ns for 30 ops = {d / 30:.1f} +/- {se / 30:.1f} ns/op (round 1: 28.3 +/- 1.7)")
    if s15 and s154:
        d = s15[0] - s154[0]
        se = float(np.hypot(s15[1], s154[1]))
        L.append(f"- inputs at fixed 15 ops: S15_16 - S15_4 = {d:+.0f} +/- {se:.0f} ns (H_ops_only predicts ~0; 8 ns/input x actual input difference predicts ~+100)")
    ac, at = hv("P2_design", "AOC15"), hv("P2_design", "AOT15")
    if ac and at:
        d = ac[0] - at[0]
        se = float(np.hypot(ac[1], at[1]))
        L.append(f"- non-reassociable chain minus tree (same and/or multiset): {d:+.0f} +/- {se:.0f} ns -> {'no depth effect without re-association either' if abs(d) < 2 * se else 'DEPTH EFFECT: depth matters when the ops cannot be re-associated (the XOR result was hiding it)'}")
    txt = "\n".join(L) + "\n"
    open(S.os.path.join(outdir, "RESULTS_QPU_p2.md"), "w").write(txt)
    return txt


def analyse(man, res, outdir):
    if man["tier"] in ("e1", "e2"):
        return analyse_env(man, res, outdir)
    if man["tier"] == "v1":
        return analyse_v1(man, res, outdir)
    if man["tier"] == "p2":
        return analyse_lat(man, res, outdir)
    return analyse_acc(man, res, outdir)


# ------------------------------------------------------------------ local checks (semantics, guards, transpile, QPY, metrics)
def check_uprog(prog, qc, pat, n=20000, seed=5):
    """vectorised circuit expressions vs python reference on random register values (sampled) + the prepared pattern."""
    rng = np.random.default_rng(seed)
    w = prog.w
    vals = {r: rng.integers(0, 1 << w, n).astype(np.uint64) for r in prog.measured}
    env = dict(vals)
    sts = [x for x in S.exprs_of(qc) if x[0] == "store"]
    for kk, e, lv in sts:
        env[lv.var.name] = S.veval(e, env, {})
    final = prog.stores[-1][0]
    for j in range(w):
        env[next(r for r in qc.cregs if r.name == final)[j]] = ((env[final] >> np.uint64(j)) & np.uint64(1)).astype(bool)
    cond = [x for x in S.exprs_of(qc) if x[0] == "if_else"][-1][1]
    got = np.asarray(S.veval(cond, env, {})).astype(bool)
    ref = np.array([prog.evaluate({r: int(vals[r][i]) for r in prog.measured}) for i in range(n)], bool)
    bad = int(np.sum(got != ref))
    p = prog.patterns()[pat]
    env2 = {r: np.array([p["values"][r]], np.uint64) for r in prog.measured}
    for kk, e, lv in sts:
        env2[lv.var.name] = S.veval(e, env2, {})
    for j in range(w):
        env2[next(r for r in qc.cregs if r.name == final)[j]] = ((env2[final] >> np.uint64(j)) & np.uint64(1)).astype(bool)
    bad += int(bool(np.asarray(S.veval(cond, env2, {}))[0]) != bool(p["expected"]))
    return n + 1, bad


def check_bprog(prog, qc, pat, n=20000, seed=9):
    rng = np.random.default_rng(seed)
    nb = prog.nreg * prog.w
    bits = rng.integers(0, 2, (n, nb)).astype(bool)
    env = {}
    for i in range(nb):
        env[next(r for r in qc.cregs if r.name == f"X{i // prog.w}")[i % prog.w]] = bits[:, i]
    st = [x for x in S.exprs_of(qc) if x[0] == "store"][0]
    got = np.asarray(S.veval(st[1], env, {})).astype(bool)
    ref = np.array([bool(prog.evaluate(list(map(int, bits[k])))) for k in range(0, n, 20)])
    bad = int(np.sum(got[::20] != ref))
    p = prog.patterns()[pat]
    env2 = {}
    for i in range(nb):
        env2[next(r for r in qc.cregs if r.name == f"X{i // prog.w}")[i % prog.w]] = np.array([bool(p["values"][i])])
    bad += int(bool(np.asarray(S.veval(st[1], env2, {}))[0]) != bool(p["expected"]))
    return n // 20 + 1, bad


def check_mprog(prog, qc, pat, n=4000, seed=3):
    """vectorised circuit expressions vs the python reference on random register values (sampled) + the prepared pattern."""
    rng = np.random.default_rng(seed)
    vals = {r: rng.integers(0, 1 << w, n).astype(np.uint64) for r, w in prog.measured}
    regs = {r.name: r for r in qc.cregs}

    def run(vv):
        env = dict(vv)
        for r, w in prog.measured:
            for j in range(w):
                env[regs[r][j]] = ((np.asarray(vv[r]) >> np.uint64(j)) & np.uint64(1)).astype(bool)
        for kk, e, lv in S.exprs_of(qc):
            if kk == "store":
                var = lv.var
                val = S.veval(e, env, {})
                if isinstance(var, Clbit):
                    env[var] = np.asarray(val).astype(bool)
                else:
                    env[var.name] = val
                    reg = regs[var.name]
                    for j in range(len(reg)):
                        env[reg[j]] = ((np.asarray(val) >> np.uint64(j)) & np.uint64(1)).astype(bool)
        res = []
        for kk, e, lv in S.exprs_of(qc):
            if kk == "if_else":
                res.append(np.asarray(S.veval(e, env, {})).astype(bool))
        return res
    got = run(vals)
    exp = np.array([prog.evaluate({r: int(vals[r][i]) for r, _ in prog.measured}) for i in range(n)], bool)
    # transpiler may reorder ifs: compare as multisets of columns per sample
    bad = 0
    for i in range(n):
        bad += int(sorted(int(g[i]) for g in got) != sorted(int(x) for x in exp[i]))
    p = prog.patterns()[pat]
    one = run({r: np.array([p["values"][r]], np.uint64) for r, _ in prog.measured})
    bad += int(sorted(int(g[0]) for g in one) != sorted(int(x) for x in p["expected"]))
    return n + 1, bad


def local_checks(be, only):
    allok = True
    get_prog("P1_R11x2_a")
    progs = dict(_CACHE["p1"])
    progs.update(_CACHE["p3"])
    progs.update(_CACHE["v1"])
    for nm, prog in _CACHE["e2"].items():
        if only and nm not in only:
            continue
        for pat in prog.patterns():
            qc = prog.circuit(pat) if hasattr(prog, "circuit") else prog.circuits()[0 if pat == "A" else 1]
            isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
            rt = S.roundtrip(isa)
            if isinstance(prog, BProg):
                N, bad = check_bprog(prog, qc, pat)
            else:
                N, bad = S.check_shan(qc, prog.seeds, pat)
            ok = bad == 0 and rt and not S.guards(qc)
            allok &= ok
            if pat == "A" or not ok:
                print(f"{nm:15s} {pat} sampled-semantics {N - bad}/{N} | guards {S.guards(qc) or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | {'PASS' if ok else 'FAIL'}", flush=True)
    for nm, prog in _CACHE["e1"].items():
        if only and nm not in only:
            continue
        for pat in prog.patterns():
            qc = prog.circuit(pat)
            g = S.guards(qc)
            N, bad = check_mprog(prog, qc, pat)
            isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
            rt = S.roundtrip(isa)
            import resources as RS
            ok = not g and bad == 0 and rt
            allok &= ok
            if pat in ("A", "Z") or not ok:
                print(f"{nm:15s} {pat} sampled-semantics {N - bad}/{N} (expected {prog.patterns()[pat]['expected']}) | guards {g or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | {'PASS' if ok else 'FAIL'}", flush=True)
    for nm, prog in progs.items():
        if only and nm not in only:
            continue
        for pat in prog.patterns():
            qc = prog.circuit(pat)
            g = S.guards(qc)
            N, bad = (check_uprog(prog, qc, pat) if isinstance(prog, UProg) else check_bprog(prog, qc, pat))
            isa = transpile(qc, be, optimization_level=1, seed_transpiler=1)
            rt = S.roundtrip(isa)
            mb, mt = analyse_cone(qc), analyse_cone(isa)
            ok = not g and bad == 0 and rt and mb == mt
            allok &= ok
            if pat in ("A", "Z") or not ok:
                print(f"{nm:15s} {pat} regs={mt['cone_regs']} peak={mt['peak_live']} reads={mt['total_reads']} stores={mt['stores_in_cone']} maxst={mt['max_store_ops']} uniq={mt['unique_nodes']} "
                      f"| sampled-semantics {N - bad}/{N} (expected out {prog.patterns()[pat]['expected']}) | guards {g or 'ok'} | QPY {'OK' if rt else 'MISMATCH'} | transpiled==builder {'yes' if mb == mt else 'NO'} | {'PASS' if ok else 'FAIL'}", flush=True)
    for nm in (P1_LEGACY if legacy_available() else []):
        if only and nm not in only:
            continue
        circs, _, _ = legacy_circuits(nm)
        ok = True
        for c in circs:
            isa = transpile(c, be, optimization_level=1, seed_transpiler=1)
            ok &= S.roundtrip(isa)
        mt = analyse_cone(transpile(circs[-1], be, optimization_level=1, seed_transpiler=1))
        mb = analyse_cone(circs[-1])
        ok &= mt == mb
        allok &= ok
        print(f"{nm:15s} legacy builder, {len(circs)} circuits, regs={mt['cone_regs']} stores={mt['stores_in_cone']} cone_ops={mt['cone_ops']} | QPY+transpiled==builder {'PASS' if ok else 'FAIL'}", flush=True)
    probes = lat_probes()
    for j in P2_JOBS:
        if only and j["name"] not in only:
            continue
        ok = True
        for n in lat_job_names(j):
            c = _lat_circuit(j, n, probes)
            isa = transpile(c, be, optimization_level=1, seed_transpiler=1)
            ok &= S.roundtrip(isa)
            if n in probes:
                ok &= S.semantic_check(c, probes[n])[1] == 0 if probes[n]["sink"] != "uint" else True
        allok &= ok
        print(f"{j['name']:15s} LATM circuits={lat_job_names(j)} | semantics+QPY {'PASS' if ok else 'FAIL'}", flush=True)
    return allok


# ------------------------------------------------------------------ replicate pooling + post hoc pool model
def pool_lr(m):
    return m["peak_live"] + m["temps_lr"]


def combine_replicates(dirA, dirB, outdir, tier_label="p1"):
    """Two executions of the SAME plan (dirX/manifest.json + dirX/fetch_<hash>.json + npz): verify the mapping, pool the readback, run the tier analysis on the pooled data."""
    import glob
    import json as _j
    import os
    ms = {}
    for tag, d in (("A", dirA), ("B", dirB)):
        man = _j.load(open(os.path.join(d, "manifest.json")))
        fj = glob.glob(os.path.join(d, "fetch_*.json"))[0]
        ms[tag] = (man, _j.load(open(fj)), d)
    (manA, resA, dA), (manB, resB, dB) = ms["A"], ms["B"]
    os.makedirs(outdir, exist_ok=True)
    resC, rows, mismatches = {}, [], []
    for rec in manB["jobs"]:
        n = rec["name"]
        ra, rb = resA[n], resB[n]
        recA = next(x for x in manA["jobs"] if x["name"] == n)
        pubs_ok = recA["circuit_names"] == rec["circuit_names"]
        status_ok = ra["status"] == rb["status"] and ra["code"] == rb["code"]
        npz_ok = True
        if ra["status"] == "DONE" and rb["status"] == "DONE":
            za, zb = np.load(os.path.join(dA, ra["npz"])), np.load(os.path.join(dB, rb["npz"]))
            npz_ok = sorted(za.files) == sorted(zb.files)
        if not (pubs_ok and status_ok and npz_ok):
            mismatches.append(n)
        rows.append((n, ra, rb, pubs_ok, status_ok, npz_ok))
        if ra["status"] == "DONE" and rb["status"] == "DONE":
            pooled = {k: np.concatenate([za[k], zb[k]]) for k in za.files}
            fn = f"pooled_{n}.npz"
            np.savez_compressed(os.path.join(outdir, fn), **pooled)
            resC[n] = dict(rb, npz=fn)
        elif ra["status"] == rb["status"]:
            resC[n] = dict(rb)
        else:
            resC[n] = dict(rb, status="MIXED", code=None)
    get_prog("P1_ir96")
    jobsC = []
    for rec in manB["jobs"]:          # metrics are recomputed from the builders (the stored ones predate newer metric keys such as temps_lr)
        if rec["name"] in _CACHE["p1"]:
            m = analyse_cone(_CACHE["p1"][rec["name"]].circuits()[0])
        else:
            cs, _, _ = legacy_circuits(rec["name"])
            m = analyse_cone(cs[-1])
        jobsC.append(dict(rec, metrics=m))
    manC = dict(manB, jobs=jobsC, plan_hash=manB["plan_hash"] + "+A+B", tier="p1")
    txt = analyse_acc(manC, resC, outdir)
    # --- replicate consistency + readback per replicate vs pooled
    L = ["", "## Replicate consistency (run A = recovered duplicate submission, run B = manifest_<hash>.json)",
         f"Mapping check: same circuit names per probe, same status and error code in A and B, same result keys: **{'ALL CONSISTENT' if not mismatches else 'MISMATCH in ' + ', '.join(mismatches)}** "
         "(a mismatch would mean the recovered run-A job mapping is wrong).",
         "", "| probe | A | B | circuits match | outcome match | result keys match |", "|---|---|---|---|---|---|"]
    for n, ra, rb, p_ok, s_ok, k_ok in rows:
        L.append(f"| {n} | {ra['status']}{' ' + str(ra['code']) if ra['code'] else ''} | {rb['status']}{' ' + str(rb['code']) if rb['code'] else ''} | {'yes' if p_ok else 'NO'} | {'yes' if s_ok else 'NO'} | {'yes' if k_ok else 'NO'} |")
    L += ["", "## Per-replicate vs pooled correctness for informative patterns (pooled CI = both runs concatenated)", "| probe | pattern | A | B | pooled [95% CI] |", "|---|---|---|---|---|"]
    for rec in manB["jobs"]:
        n = rec["name"]
        if resC[n]["status"] != "DONE" or rec["kind"] != "PRG":
            continue
        rA = {x["pat"]: x for x in _pattern_rows(dict(rec, meta=rec["meta"]), dict(resA[n]), dA)}
        rB = {x["pat"]: x for x in _pattern_rows(rec, dict(resB[n]), dB)}
        rP = {x["pat"]: x for x in _pattern_rows(rec, resC[n], outdir)}
        for pat, x in rP.items():
            if x["informative"]:
                L.append(f"| {n} | {pat} | {rA[pat]['correct']:.2f} | {rB[pat]['correct']:.2f} | {x['correct']:.3f} [{x['lo']:.3f},{x['hi']:.3f}] |")
    # --- post hoc pool model
    L += ["", "## POST HOC register-pool model (fitted after seeing run A and B; the pre-registered H_alloc used the Ershov number and failed on several probes, see the scoreboard)",
          "pool = peak live registers (measured + register-valued stores) + naive left-to-right expression-stack need of the largest stored expression (left operand held while the right is evaluated; leaf = 1). "
          "Bit-level stored variables are a separate pool (W512 has 513 live bit variables).", "| probe | outcome | peak live | LR stack | pool | bit vars | Ershov temps |", "|---|---|---|---|---|---|---|"]
    items = []
    get_prog("P1_ir96")
    for rec in manB["jobs"]:
        n = rec["name"]
        if n in _CACHE["p1"]:
            m = analyse_cone(_CACHE["p1"][n].circuits()[0])
        else:
            cs, _, _ = legacy_circuits(n)
            m = analyse_cone(cs[-1])
        st = resC[n]["status"]
        out = "pass" if st == "DONE" else ("FAIL" if resC[n]["code"] == 6057 else st)
        items.append((n, out, m))
    for n, out, m in sorted(items, key=lambda r: (pool_lr(r[2]), r[0])):
        L.append(f"| {n} | {out} | {m['peak_live']} | {m['temps_lr']} | {pool_lr(m)} | {m['peak_live_bits']} | {m['temps']} |")
    passes = [pool_lr(m) for n, o, m in items if o == "pass"]
    fails = [(pool_lr(m), n) for n, o, m in items if o == "FAIL"]
    L += ["", f"Pass pools: max {max(passes)}. Fail pools: min {min(f for f, _ in fails)}. Failures at or below the highest pass: "
          + (", ".join(f"{n} (pool {f}, bit vars {m['peak_live_bits']})" for (f, n), (_, _, m) in zip(fails, [x for x in items if x[1] == 'FAIL']) if f <= max(passes)) or "none")
          + ". Treat the model as a description of these 25 probes, not a validated law: ties at pool 14 (R11x2 passes; alloc8, ir104, dag104 fail), and the two Shannon-form expressions (old T1, LUT85) fail at pool 12 "
          "(they contain NOT operators), so temporaries are not counted exactly right."]
    full = txt + "\n".join(L) + "\n"
    open(os.path.join(outdir, "RESULTS_QPU_p1.md"), "w").write(full)
    return full, mismatches


# ------------------------------------------------------------------ validation tier v1 (out-of-sample test of the fitted pool model)
V1_SPECS = [  # name, nreg, w, L, high
    ("V1_rc10_P3", 3, 8, 10, True),    # 3 registers, stack need 10 (tiny expression, 11 ops): weighted model fails it, every other model passes
    ("V1_lc10_P3", 3, 8, 10, False),   # same leaves and ops as a left comb (stack need 2): control, all models pass
    ("V1_rc4_P9", 9, 4, 4, True),      # near the boundary on the predicted-SAFE side (score 56 vs threshold 59)
    ("V1_rc4_P10", 10, 4, 4, True),    # near the boundary on the predicted-FAIL side (score 60 vs 59)
    ("V1_rc12_P1", 1, 32, 12, True),   # one register, stack need 12 (T1-like pressure without Shannon form): weighted model fails it
]


def v1_progs():
    return {n: stack_prog(n, nreg, w, L, high, note=f"{nreg} measured registers x {w} bits, naive left-to-right stack need {L if high else 2} ({'right-nested tail' if high else 'left comb control'})")
            for n, nreg, w, L, high in V1_SPECS}


MODEL_DEFS = {
    "M_w": ("weighted pool", lambda m: "F" if 4 * m["peak_live"] + 5 * m["temps_lr"] >= 59 else "P",
            "fail iff 4*peak_live_registers + 5*left_to_right_stack_need >= 59  (fitted on 80 probes, 2 errors; pass max 58, fail min 59)"),
    "M_u": ("unweighted pool", lambda m: "F" if m["peak_live"] + m["temps_lr"] >= 14 else "P", "fail iff peak_live + stack need >= 14 (best unweighted threshold, 9 errors)"),
    "M_peak": ("peak live registers", lambda m: "F" if m["peak_live"] >= 13 else "P", "fail iff peak live registers >= 13 (13 errors)"),
    "M_cone": ("measured registers in cone", lambda m: "F" if m["cone_regs"] >= 12 else "P", "fail iff distinct measured registers in the cone >= 12 (15 errors, falsified by staged)"),
    "M_SU": ("Sethi-Ullman pool", lambda m: "F" if m["peak_live"] + m["temps"] >= 15 else "P", "fail iff peak live + Ershov number >= 15 (12 errors)"),
    "M_ops": ("op limit", lambda m: "F" if (m["max_store_ops"] >= 103 or m["cone_ops"] >= 104) else "P", "fail iff a store has >= 103 ops or the cone >= 104 ops (falsified by bal104/chain104)"),
}


def v1_predictions_text():
    import hashlib
    import json as _j
    mf = S.os.path.join(S.OUTDIR, "modelfit.json")
    digest = hashlib.sha256(open(mf, "rb").read()).hexdigest()[:16] if S.os.path.exists(mf) else "n/a"
    L = ["Validation round v1: OUT-OF-SAMPLE test of resource models fitted on 80 probes (rounds 1-4 + reconstructable 2026-11-06 jobs; fit data sha256 prefix " + digest + ", modelfit.py).",
         "Registered before submission (no external timestamp). Outcome = accepted (P) or error 6057 (F). Counts are taken from the transpiled circuits (asserted equal to the builder).",
         "Frozen models:"]
    for k, (nm, _, d) in MODEL_DEFS.items():
        L.append(f"  {k} ({nm}): {d}")
    L.append("  Known misfits of M_w: old 2026-11-06 T3 (6 stored BDD expressions in bit variables) and T3m predicted pass, observed fail; W512 handled by a separate bit-variable pool (>= 513 live stored bits).")
    L.append(f"{'probe':12s} {'P':>3s} {'L':>3s} {'4P+5L':>6s} {'ops':>4s} | " + " ".join(f"{k:>7s}" for k in MODEL_DEFS))
    for n, p in v1_progs().items():
        m = analyse_cone(p.circuits()[0])
        L.append(f"{n:12s} {m['peak_live']:3d} {m['temps_lr']:3d} {4 * m['peak_live'] + 5 * m['temps_lr']:6d} {m['cone_ops']:4d} | " + " ".join(f"{f(m):>7s}" for _, f, _ in MODEL_DEFS.values()))
    L.append("Decisive: V1_rc10_P3 and V1_rc12_P1 (only M_w predicts F; op, depth, cone, peak and Sethi-Ullman models predict pass); V1_lc10_P3 is the control with identical leaves and ops (all P); "
             "V1_rc4_P9 (score 56, predicted safe) and V1_rc4_P10 (score 60, predicted fail, margin 1) probe the boundary. A model is validated only if it predicts all 5; M_w's one-unit margin makes V1_rc4_P10 the weakest prediction.")
    return "\n".join(L)


def analyse_v1(man, res, outdir):
    base = analyse_acc(man, res, outdir)
    L = ["", "## Validation scoreboard (out-of-sample; models frozen before the run)", "| probe | observed | " + " | ".join(MODEL_DEFS) + " |", "|---|---|" + "---|" * len(MODEL_DEFS)]
    sc = {k: [0, 0] for k in MODEL_DEFS}
    for rec in man["jobs"]:
        r = res[rec["name"]]
        if r["status"] != "DONE" and r["code"] != 6057:
            continue
        obs = "P" if r["status"] == "DONE" else "F"
        cells = []
        for k, (_, f, _) in MODEL_DEFS.items():
            v = f(rec["metrics"])
            sc[k][0 if v == obs else 1] += 1
            cells.append(f"{v}{' ok' if v == obs else ' WRONG'}")
        L.append(f"| {rec['name']} | {'pass' if obs == 'P' else 'FAIL'} | " + " | ".join(cells) + " |")
    L.append("| correct / wrong | | " + " | ".join(f"{sc[k][0]} / {sc[k][1]}" for k in MODEL_DEFS) + " |")
    win = [k for k, v in sc.items() if v[1] == 0]
    L.append("")
    if sum(sc[k][0] + sc[k][1] for k in sc) == 0:
        L.append("Verdict: no outcomes yet; no model is evaluated.")
    else:
        L.append("Verdict: " + (f"model(s) with no wrong prediction: {', '.join(win)}. " if win else "NO frozen model predicted all outcomes; the pool description is not validated. ")
                 + "A prediction that holds is evidence but not proof (5 probes, one-unit margin at the boundary); a failure falsifies that model as stated.")
    txt = base + "\n".join(L) + "\n"
    open(S.os.path.join(outdir, "RESULTS_QPU_v1.md"), "w").write(txt)
    return txt


# ------------------------------------------------------------------ realistic-pattern programs for the envelope validation tier (e1)
class MProg:
    """Measured registers -> stores (register-level or bit-level) -> several ifs, each applying X to its own output qubit; every output is read out.
    Node language: ("r", reg) register, ("b", reg, j) one bit of a register (Bool), ("ub", reg, j) bit j as Uint ((reg >> j) & 1), ("l", v) Uint literal of the register width,
    ("o", op, a, b) with op in xor/and/or/add/mul.  Conditions: ("bit", store, j) or ("gt", store, T)."""

    def __init__(self, name, measured, stores, conds, pattern_sets, note="", seed=0):
        self.name, self.measured, self.stores, self.conds, self.note = name, list(measured), list(stores), list(conds), note
        self.w = dict(self.measured)
        self.sets = pattern_sets
        rng = random.Random(6100 + seed + len(name))
        self._pats = None

    # python reference
    def _ev(self, n, env, widths):
        k = n[0]
        if k == "r":
            return env[n[1]], widths[n[1]]
        if k == "b":
            return (env[n[1]] >> n[2]) & 1, 0
        if k == "ub":
            return (env[n[1]] >> n[2]) & 1, widths[n[1]]
        if k == "l":
            return n[1], None
        a, wa = self._ev(n[2], env, widths)
        b, wb = self._ev(n[3], env, widths)
        w = wa if wa is not None else wb
        op = n[1]
        if w == 0:
            return {"xor": a ^ b, "and": a & b, "or": a | b}[op] & 1, 0
        m = (1 << w) - 1 if w else (1 << 64) - 1
        return {"xor": a ^ b, "and": a & b, "or": a | b, "add": (a + b) & m, "mul": (a * b) & m}[op] & m, w

    def evaluate(self, vals):
        env, widths = dict(vals), dict(self.w)
        for st in self.stores:
            v, w = self._ev(st["node"], env, widths)
            env[st["name"]] = v
            widths[st["name"]] = st["w"]
        out = []
        for c in self.conds:
            if c[0] == "bit":
                out.append((env[c[1]] >> c[2]) & 1)
            else:
                out.append(int(env[c[1]] > c[2]))
        return tuple(out)

    def value(self, reg, ones):
        return sum(((1 if (reg, j) in ones else 0) << j) for j in range(self.w[reg]))

    def patterns(self):
        if self._pats is not None:
            return self._pats
        sets = dict(self.sets)
        sets.setdefault("Z", set())
        if any(c[0] == "gt" and c[2] is None for c in self.conds):          # choose the threshold so that the informative patterns split
            best = None
            for T in range(0, 255):
                self.conds = [("gt", c[1], T) if c[0] == "gt" else c for c in self.conds]
                outs = {k: self.evaluate({r: self.value(r, s) for r, _ in self.measured}) for k, s in sets.items()}
                z = outs["Z"]
                score = (sum(o != z for k, o in outs.items() if k != "Z"), -abs(T - 100))
                if best is None or score > best[0]:
                    best = (score, T)
            self.conds = [("gt", c[1], best[1]) if c[0] == "gt" else c for c in self.conds]
        pats = {}
        z = None
        for k, s in sets.items():
            vals = {r: self.value(r, s) for r, _ in self.measured}
            pats[k] = dict(values=vals, expected=self.evaluate(vals))
        z = pats["Z"]["expected"]
        for k, p in pats.items():
            p["informative"] = (k != "Z" and p["expected"] != z)
        self._pats = pats
        return pats

    def circuit(self, pat, ibm=True):
        vals = self.patterns()[pat]["values"]
        nm = sum(w for _, w in self.measured)
        nout = len(self.conds)
        q = QuantumRegister(nm + nout, "q")
        out = ClassicalRegister(nout, "out")
        regs = {r: ClassicalRegister(w, r) for r, w in self.measured}
        qc = QuantumCircuit(q, out, *regs.values(), name=f"{self.name}_{pat}")
        if ibm:
            from qiskit_ibm_runtime.circuit import MidCircuitMeasure
        base, off = {}, 0
        for r, w in self.measured:
            base[r] = off
            off += w
        for r, w in self.measured:
            for j in range(w):
                if (vals[r] >> j) & 1:
                    qc.x(q[base[r] + j])
                qc.append(MidCircuitMeasure(), [q[base[r] + j]], [regs[r][j]]) if ibm else qc.measure(q[base[r] + j], regs[r][j])
        targets = {}

        def ex(n):
            k = n[0]
            if k == "r":
                return regs[n[1]] if n[1] in regs else targets[n[1]]
            if k == "b":
                return expr.lift(regs[n[1]][n[2]] if n[1] in regs else targets[n[1]][n[2]])
            if k == "ub":
                wr = self.w[n[1]]
                return expr.bit_and(expr.shift_right(regs[n[1]], expr.lift(n[2], U(wr))), expr.lift(1, U(wr)))
            if k == "l":
                return expr.lift(n[1], U(n[2]))
            return OPS[n[1]](ex(n[2]), ex(n[3]))
        for st in self.stores:
            t = ClassicalRegister(st["w"], st["name"])
            qc.add_register(t)
            targets[st["name"]] = t
            qc.store(t[0] if st["kind"] == "bit" else t, ex(st["node"]))
        for i, c in enumerate(self.conds):
            t = targets[c[1]]
            ce = expr.lift(t[c[2]]) if c[0] == "bit" else expr.greater(t, expr.lift(c[2], U(t.size)))
            with qc.if_test(ce):
                qc.x(q[nm + i])
        for i in range(nout):
            qc.measure(q[nm + i], out[i])
        return qc

    def circuits(self):
        self.patterns()
        return [self.circuit(k) for k in self.patterns()]

    def pattern_meta(self):
        return {k: dict(expected=list(v["expected"]), informative=v["informative"]) for k, v in self.patterns().items()}


def _xor_nodes(items):
    n = items[0]
    for x in items[1:]:
        n = ("o", "xor", n, x)
    return n


def e1_progs():
    pr = {}
    # (1) weighted 7-bit vote (Uint(8) shift/and/mul/add + compare): new weights, same structure as the accepted legacy 27-op vote
    w7 = [37, 29, 21, 33, 27, 41, 23]
    node = None
    for j, wj in enumerate(w7):
        t = ("o", "mul", ("ub", "dat", j), ("l", wj, 8))
        node = t if node is None else ("o", "add", node, t)
    pr["E1_wvote7"] = MProg("E1_wvote7", [("dat", 8)], [dict(name="S", kind="reg", w=8, node=node)], [("gt", "S", None)],
                            {"A": {("dat", 0), ("dat", 1), ("dat", 2)}, "B": {("dat", 0), ("dat", 1), ("dat", 2), ("dat", 3)}, "C": {("dat", 6)}, "D": {("dat", 3)}, "E": {("dat", 0)}, "F": {("dat", 5), ("dat", 4)}},
                            note="7-bit weighted vote, new weights, Uint(8) shift/and/mul/add + compare (realistic decoder readout)")
    # (2) syndrome -> correction decode: 6 measured syndrome registers kept live to the last store, 2 register-valued corrections (one reads all six), 3 ifs
    g = lambda i: ("r", f"s{i}")  # noqa: E731
    st = [dict(name="c0", kind="reg", w=2, node=_xor_nodes([g(0), g(1), g(2)])), dict(name="c1", kind="reg", w=2, node=_xor_nodes([g(i) for i in range(6)]))]
    pr["E1_syndrome6"] = MProg("E1_syndrome6", [(f"s{i}", 2) for i in range(6)], st, [("bit", "c0", 0), ("bit", "c1", 0), ("bit", "c1", 1)],
                               {"A": {("s0", 0)}, "B": {("s3", 0)}, "C": {("s5", 0)}, "D": {("s2", 0)}, "E": {("s1", 0)}, "F": {("s4", 0)}, "G": {("s5", 1)}},
                               note="syndrome decode: 6 measured syndrome registers all live until the last store, 2 correction registers (one xor of 3, one of all 6), 3 ifs")
    # (3) teleportation-style correction: parity of 14 bits from 2 wide registers, bit-level store, 2 ifs read the same flag
    bits = [("b", "X0", j) for j in range(8)] + [("b", "X1", j) for j in range(7)]
    pr["E1_tele15"] = MProg("E1_tele15", [("X0", 8), ("X1", 8)], [dict(name="p", kind="bit", w=1, node=_xor_nodes(bits))], [("bit", "p", 0), ("bit", "p", 0)],
                            {"A": {("X0", 0)}, "B": {("X1", 6)}, "C": {("X0", 3), ("X1", 3)}, "D": {("X0", 7)}, "E": {("X1", 0), ("X0", 1), ("X1", 2)}, "F": {("X1", 3)}},
                            note="multi-bit teleportation correction: parity of 15 bits of two 8-bit registers, one bit-level store, two ifs applying the correction to two qubits")
    # (4) two ifs with different, overlapping cones (4 and 3 registers share g3), same operation set as the accepted staged probe (no add)
    st4 = [dict(name="S1", kind="reg", w=2, node=_xor_nodes([("r", "g0"), ("r", "g1"), ("r", "g2"), ("r", "g3"), ("r", "g4")])), dict(name="S2", kind="reg", w=2, node=_xor_nodes([("r", "g3"), ("r", "g4"), ("r", "g5")]))]
    pr["E1_twoif"] = MProg("E1_twoif", [(f"g{i}", 2) for i in range(6)], st4, [("bit", "S1", 0), ("bit", "S2", 0)],
                           {"A": {("g0", 0)}, "B": {("g4", 0)}, "C": {("g5", 0)}, "D": {("g2", 0)}, "E": {("g3", 0)}, "F": {("g1", 0)}},
                           note="two ifs with different cones of 5 and 3 registers sharing two registers, register-valued stores")
    # (5) larger decode (predicted FAIL): 9 measured registers + 4 stored corrections, the last one reads all of them
    gg = lambda i: ("r", f"g{i}")  # noqa: E731
    st5 = [dict(name="c0", kind="reg", w=2, node=_xor_nodes([gg(0), gg(1), gg(2)])), dict(name="c1", kind="reg", w=2, node=_xor_nodes([gg(3), gg(4), gg(5)])),
           dict(name="c2", kind="reg", w=2, node=_xor_nodes([gg(6), gg(7), gg(8)])),
           dict(name="c3", kind="reg", w=2, node=_xor_nodes([gg(i) for i in range(9)] + [("r", "c0"), ("r", "c1"), ("r", "c2")]))]
    pr["E1_decode9"] = MProg("E1_decode9", [(f"g{i}", 2) for i in range(9)], st5, [("bit", f"c{k}", 0) for k in range(4)],
                             {"A": {("g0", 0)}, "B": {("g4", 0)}, "C": {("g8", 0)}, "D": {("g2", 0), ("g6", 0)}, "E": {("g5", 0)}, "F": {("g7", 0), ("g1", 0)}},
                             note="larger decode: 9 measured syndrome registers, 4 register-valued corrections (c3 reads all of them): peak 13 live registers, the shape of the rejected R12x2")
    return pr


def e1_envelope():
    """(accepted, rejected, pass_dims, fail_dims, raw) from the newest rules/envelope_*.json (marrakesh rows only). Frozen predictions live in each manifest."""
    import glob
    import json as _j
    cands = glob.glob(S.os.path.join(S.HERE, "rules", "envelope_*.json")) + glob.glob(S.os.path.join(S.EXP, "..", "public", "ibm-controller-conformance", "rules", "envelope_*.json"))
    d = _j.load(open(max(cands, key=S.os.path.getmtime)))
    acc = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "accept" and r["backend"] == "ibm_marrakesh"]
    rej = [(r["name"], r["vector"]) for r in d["probes"] if r["class_"] == "reject6057" and r["backend"] == "ibm_marrakesh"]
    d = dict(d, loo=d["leave_one_out"], loo_wrong=d["leave_one_out_wrong"])
    return acc, rej, d["pass_dims"], d["fail_dims"], d


class ShanProg:
    """Shannon-form (random_function_expr style) probe: m(6) measured, s[0] = shannon(seed), s[1] = s[0] ^ m[1], if s[1]; prepared inputs A (out 1) and B (out 0)."""
    def __init__(self, name, seeds, note=""):
        self.name, self.seeds, self.note = name, list(seeds), note

    def patterns(self):
        return {w: dict(values=a, expected=(tgt,), informative=True) for w, (a, tgt) in S.best_patterns_shan(self.seeds).items()}

    def circuits(self):
        return [S.build_shan(self.seeds, w, f"{self.name}_{w}") for w in ("A", "B")]

    def pattern_meta(self):
        return {k: dict(expected=list(v["expected"]), informative=True) for k, v in self.patterns().items()}


def e2_progs():
    """Probes chosen so that the dominance envelope and M_u DISAGREE (a disagreement is the only way a new round teaches something)."""
    pr = {}
    pr["E2_shan112"] = ShanProg("E2_shan112", [154], note="Shannon-form expression of 112 ops (stack need 11, many NOTs) over 6 bits of one register: envelope FAIL (dominates the rejected 103-op T1), M_u PASS (pool 12)")
    pr["E2_rc5_P9"] = stack_prog("E2_rc5_P9", 9, 4, 5, True, note="9 measured registers, right-nested tail of stack need 5: M_u FAIL (pool 14), envelope UNCERTAIN (between the accepted 9/4 and the rejected 10/4)")
    pr["E2_rc8_P5"] = stack_prog("E2_rc8_P5", 5, 8, 8, True, note="5 measured registers, stack need 8: M_u PASS (pool 13), envelope UNCERTAIN (no accepted probe has both peak >= 5 and stack >= 8)")
    return pr


def mu_verdict(v):
    """M_u (survived the frozen 5-probe v1 test; NOT 'the rule'): fail iff peak live registers + left-to-right stack need >= 14, or >= 513 live bit variables."""
    return "F" if (v["peak_live_regs"] + v["lr_stack"] >= 14 or v["peak_live_bitvars"] >= 513) else "P"


def env_vectors(progs):
    """vectors of the probes: from the TRANSPILED circuits of the current plan when one was built, else from the builders."""
    import resources as RS
    return {n: _E1_PLAN_VECTORS.get(n) or RS.resource_vector(p.circuits()[0]) for n, p in progs.items()}


def env_predictions_text(progs, title):
    import resources as RS
    acc, rej, pd, fd, d = e1_envelope()
    L = [f"{title} (registered before submission; no external timestamp).",
         f"Envelope data: {len(acc)} accepted and {len(rej)} rejected (6057) probes on ibm_marrakesh (out/envelope.json); vector = resources.resource_vector on the TRANSPILED circuit.",
         "Rule: PASS iff the vector is <= the vector of some ACCEPTED probe in ALL %d dimensions; FAIL iff it is >= the vector of some REJECTED probe in the failure dimensions %s; otherwise UNCERTAIN." % (len(pd), fd),
         f"Leave-one-out over the existing probes: {d['loo']}, wrong: {d['loo_wrong']}.",
         "Second, independent prediction: M_u = fail iff peak_live_regs + lr_stack >= 14 (or >= 513 bit variables); refit on all 85 probes it misclassifies 7; it survived the frozen 5-probe v1 round. A probe where the two models DISAGREE is marked.", "",
         f"{'probe':14s} {'envelope':9s} {'M_u':4s} witness"]
    for n, v in env_vectors(progs).items():
        verdict, w = RS.envelope_verdict(v, acc, rej, pd, fd)
        wv = dict(acc).get(w) if verdict == "PASS" else dict(rej).get(w) if verdict == "FAIL" else None
        edge = ""
        if wv:
            eq = [k for k in RS.DIMENSIONS if v[k] == wv[k] and v[k] > 0]
            edge = " | at the witness value in: " + ", ".join(eq)
        mu = mu_verdict(v)
        dis = "  <-- DISAGREE" if (verdict == "PASS") != (mu == "P") and verdict != "UNCERTAIN" or (verdict == "UNCERTAIN") else ""
        L.append(f"{n:14s} {verdict:9s} {mu:4s} {w}{edge}{dis}")
        L.append("     v = " + ", ".join(f"{k}={v[k]}" for k in RS.DIMENSIONS if v[k]))
    return "\n".join(L)


def e1_predictions_text():
    return env_predictions_text(e1_progs(), "Envelope validation round e1")


def e2_predictions_text():
    return env_predictions_text(e2_progs(), "Envelope vs M_u disagreement round e2")


def _parse_frozen(pred_text):
    """frozen envelope verdicts from a manifest's predictions text: {probe: verdict}"""
    import re
    out = {}
    for line in pred_text.split("\n"):
        m = re.match(r"^(E[12]_\w+)\s+(PASS|FAIL|UNCERTAIN)\b", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def analyse_env(man, res, outdir):
    """Analysis of an envelope validation round using ONLY what the manifest froze: transpiled vectors (meta.vector), patterns (meta.patterns) and the predictions text.
    M_u is computed from the stored vector (M_u was defined before e1 ran but is not in the e1 manifest text)."""
    tag = man["tier"]
    frozen = _parse_frozen(man["predictions"])
    L = [f"# QPU results {tag} (envelope vs M_u validation), plan {man['plan_hash']} on {man['backend']}", f"submitted {man['submitted_at']}; est {man['est_total_s']:.0f} QPU-s", "",
         "Everything below uses the circuits, vectors and predictions STORED in the manifest (not the current builders). Backend info at submission: " + S.json.dumps(man.get("backend_info", {})), "",
         f"| probe | envelope (frozen) | M_u | observed | code | informative patterns responding (all outputs correct, CI lower > {VERIFY_LB}) | per-pattern all-outputs-correct [95% CI] |", "|---|---|---|---|---|---|---|"]
    sc_e, sc_u = [0, 0], [0, 0]
    msgs, rows_out = {}, []
    for rec in man["jobs"]:
        n = rec["name"]
        r = res[n]
        v = rec["meta"]["vector"]
        env = frozen.get(n, "UNCERTAIN")
        mu = "PASS" if mu_verdict(v) == "P" else "FAIL"
        obs = "PASS" if r["status"] == "DONE" else ("FAIL" if r["code"] == 6057 else r["status"])
        rb, resp = "", ""
        if r["status"] == "DONE":
            z = np.load(S.os.path.join(outdir, r["npz"]))
            rr = []
            for pat, pm in rec["meta"]["patterns"].items():
                o = z[f"out_{n}_{pat}"].astype(bool)
                o = o.reshape(len(o), -1)
                exp = np.array(pm["expected"] if isinstance(pm["expected"], (list, tuple)) else [pm["expected"]], bool)
                ok = (o == exp[None, :]).all(1)
                k = int(ok.sum())
                lo, hi = S.wilson(k, len(ok))
                rr.append((pat, pm["informative"], k / len(ok), lo, hi))
            inf = [x for x in rr if x[1]]
            resp = f"{sum(x[3] > VERIFY_LB for x in inf)}/{len(inf)}"
            rb = "; ".join(f"{p}{'*' if i else ''}:{c:.2f} [{lo:.2f},{hi:.2f}]" for p, i, c, lo, hi in rr)
        elif r["message"]:
            msgs.setdefault(r["message"], []).append(n)
        if env in ("PASS", "FAIL"):
            sc_e[0 if env == obs else 1] += 1
        sc_u[0 if mu == obs else 1] += 1
        rows_out.append((n, env, mu, obs))
        L.append(f"| {n} | {env} | {mu} | {obs} | {r['code'] or ''} | {resp} | {rb} |")
    L += ["", "## Validation scoreboard", f"- Envelope PASS/FAIL predictions correct / wrong: {sc_e[0]} / {sc_e[1]} (UNCERTAIN predictions make no claim)",
          f"- M_u correct / wrong: {sc_u[0]} / {sc_u[1]}",
          "- Disagreements (envelope PASS or FAIL vs M_u, or envelope UNCERTAIN): " + (", ".join(f"{n} (env {e}, M_u {m}, observed {o})" for n, e, m, o in rows_out if (e == "UNCERTAIN" or (e == "PASS") != (m == "PASS"))) or "none"),
          "", "## Full error messages (verbatim)"] + ([f"- {', '.join(ns)}: `{t}`" for t, ns in msgs.items()] or ["- none"])
    wrong = [n for n, e, m, o in rows_out if e in ("PASS", "FAIL") and e != o]
    L += ["", "Verdict: " + ("every frozen envelope PASS/FAIL prediction held out-of-sample." if not wrong else f"frozen envelope prediction WRONG for {wrong}: the vector misses a dimension; add it (construction differences) and re-run build_envelope.py.")
          + (" M_u wrong for " + ", ".join(n for n, e, m, o in rows_out if m != o) + "." if sc_u[1] else " M_u predicted every outcome.")]
    txt = "\n".join(L) + "\n"
    open(S.os.path.join(outdir, f"RESULTS_QPU_{tag}.md"), "w").write(txt)
    return txt


analyse_e1 = analyse_env
