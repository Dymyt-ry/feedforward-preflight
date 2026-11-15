# SPDX-License-Identifier: Apache-2.0
"""Round-4 (p1) and v1 probe programs, copied unchanged from the conformance-suite tiers (recipes + circuit builders), so that dccheck can rebuild every p1 probe
and derive its resource metrics. Only the program/builders are copied (no submission code). 21 programs; the 4 legacy shapes (old T1, CONE, LUT85K8,
W512) need legacy builder code that is not part of this folder and are entered into the rules as curated observations with their reported metrics.
"""
from __future__ import annotations

import random

import numpy as np  # noqa: F401
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.circuit import Clbit  # noqa: F401
from qiskit.circuit.classical import expr, types

U = types.Uint

OPS = {"xor": expr.bit_xor, "and": expr.bit_and, "or": expr.bit_or, "add": expr.add, "mul": expr.mul}
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

