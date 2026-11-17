# SPDX-License-Identifier: Apache-2.0
"""dccheck --fix-pack (v2): repack measured registers into few wide registers using ONLY constructs that an accepted QPU probe used.

Why: the 6057 limit counts live registers (the rules' envelope); many small measured registers can be merged into a few wide ones without
changing any bit. v1 of this tool read the wide registers with shifts and narrowing casts; the QPU rejected those circuits (error 6066), so
v2 never emits shifts or casts. It REMAPS instead:
  * every measure keeps its clbit (same objects, same order); only the register structure changes: the merged registers are replaced by wide ones;
  * a whole-register Uint store S = f(G0, G1, ...) built from bitwise ops (xor/and/or/not) is EXPANDED into bit-level stores
    S[k] = f(G0[k], G1[k], ...) (Bool ops on single clbits, the construct of the accepted 14x3 probe) for exactly the bits k that something reads
    (`if S[0]` needs only lane 0); the unread bits of S are not written (a stored value is not returned in the job result on hardware, IBM docs);
  * Index(register, literal) becomes the clbit itself.
Anything else (a whole-register value used by a shift/add/compare/cast, non-literal index, legacy (register, value) condition, Vars, switch) is
refused with the reason; a measured register that is read as a whole value outside such a store simply stays as it is.
After the rewrite every construct that was not in the original circuit must appear in the rules' `accepted_constructs` (each backed by an accepted probe),
otherwise the fix is refused.

Equivalence proof = a local classical evaluator (independent of the rewriter, written against qiskit's expression semantics) runs the ORIGINAL and
the REWRITTEN circuit on the same measurement outcomes (exhaustive if <= 14 measurement events, otherwise structured + random patterns) and compares
the trace (every branch decision, every executed gate) and the final value of every MEASURED clbit.
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from qiskit import QuantumCircuit, qpy
from qiskit.circuit import (ClassicalRegister, Clbit, ControlFlowOp, ForLoopOp, IfElseOp, WhileLoopOp)
from qiskit.circuit.classical import expr, types

import dccheck as D
import dcrules as R
from dcrules import FAIL, PASS, UNCERTAIN

O_B = expr.Binary.Op
O_U = expr.Unary.Op


class Refuse(Exception):
    """The circuit contains a construct that cannot be rewritten safely."""


# ================================================================== evaluator
def _mask(t):
    return 1 if t.kind is types.Bool else (1 << t.width) - 1


class Sim:
    """Scalar classical evaluator. State: value of every top-level clbit and Var. Quantum ops are only recorded."""

    MAX_LOOP = 64
    keyed = False          # True: measurement outcomes are looked up by (clbit index, occurrence) instead of by event order

    def __init__(self, qc, inputs):
        self.qc, self.inputs = qc, inputs
        self.vals = {c: 0 for c in qc.clbits}
        self.vars = {}
        self.trace = []
        self.measured_bits = _measured_bits(qc)
        self.occ = {}
        self.n_events = 0
        self.qidx = {q: i for i, q in enumerate(qc.qubits)}

    # -- expressions
    def ev(self, e, cmap):
        if isinstance(e, expr.Value):
            return int(e.value)
        if isinstance(e, expr.Var):
            v = e.var
            if isinstance(v, Clbit):
                return self.vals[cmap[v]]
            if isinstance(v, ClassicalRegister):
                return sum(self.vals[cmap[b]] << i for i, b in enumerate(v))
            return self.vars.get(v, 0)
        if isinstance(e, expr.Cast):
            x = self.ev(e.operand, cmap)
            return int(x != 0) if e.type.kind is types.Bool else x & _mask(e.type)
        if isinstance(e, expr.Unary):
            x = self.ev(e.operand, cmap)
            if e.op is O_U.LOGIC_NOT:
                return int(x == 0)
            if e.op is O_U.BIT_NOT:
                return (~x) & _mask(e.type)
            raise Refuse(f"evaluator: unary op {e.op}")
        if isinstance(e, expr.Index):
            return (self.ev(e.target, cmap) >> self.ev(e.index, cmap)) & 1
        if isinstance(e, expr.Binary):
            a, b, m = self.ev(e.left, cmap), self.ev(e.right, cmap), _mask(e.type)
            op = e.op
            f = {O_B.BIT_AND: lambda: a & b, O_B.BIT_OR: lambda: a | b, O_B.BIT_XOR: lambda: a ^ b,
                 O_B.LOGIC_AND: lambda: int(bool(a) and bool(b)), O_B.LOGIC_OR: lambda: int(bool(a) or bool(b)),
                 O_B.EQUAL: lambda: int(a == b), O_B.NOT_EQUAL: lambda: int(a != b), O_B.LESS: lambda: int(a < b),
                 O_B.LESS_EQUAL: lambda: int(a <= b), O_B.GREATER: lambda: int(a > b), O_B.GREATER_EQUAL: lambda: int(a >= b),
                 O_B.SHIFT_LEFT: lambda: (a << b) & m, O_B.SHIFT_RIGHT: lambda: a >> b, O_B.ADD: lambda: (a + b) & m,
                 O_B.SUB: lambda: (a - b) & m, O_B.MUL: lambda: (a * b) & m, O_B.DIV: lambda: (a // b if b else 0) & m}
            if op not in f:
                raise Refuse(f"evaluator: binary op {op}")
            return f[op]() & m
        raise Refuse(f"evaluator: expression node {type(e).__name__}")

    def store(self, lv, v, cmap):
        if isinstance(lv, expr.Index):
            tgt, i = lv.target, self.ev(lv.index, cmap)
            self._set_bit(tgt, i, v & 1, cmap)
            return
        x = lv.var
        if isinstance(x, Clbit):
            self.vals[cmap[x]] = v & 1
        elif isinstance(x, ClassicalRegister):
            for i, b in enumerate(x):
                self.vals[cmap[b]] = (v >> i) & 1
        else:
            self.vars[x] = v & _mask(lv.type)

    def _set_bit(self, tgt, i, bit, cmap):
        x = tgt.var
        if isinstance(x, ClassicalRegister):
            self.vals[cmap[x[i]]] = bit
        elif isinstance(x, Clbit):
            self.vals[cmap[x]] = bit
        else:
            cur = self.vars.get(x, 0)
            self.vars[x] = (cur & ~(1 << i)) | (bit << i)

    def cond_value(self, cond, cmap):
        if isinstance(cond, tuple):
            tgt, val = cond
            cur = self.vals[cmap[tgt]] if isinstance(tgt, Clbit) else sum(self.vals[cmap[b]] << i for i, b in enumerate(tgt))
            return int(cur == int(val))
        return int(self.ev(cond, cmap) != 0)

    # -- instructions
    def run(self, circ, cmap, qmap):
        for inst in circ.data:
            op = inst.operation
            qs = tuple(self.qidx[qmap[q]] for q in inst.qubits)
            if op.name == "store":
                self.store(op.lvalue, self.ev(op.rvalue, cmap), cmap)
            elif op.name.startswith("measure"):
                for c in inst.clbits:
                    if self.keyed:
                        k = self.qc.find_bit(cmap[c]).index
                        occ = self.occ.get(k, 0)
                        self.occ[k] = occ + 1
                        self.vals[cmap[c]] = self.inputs((k, occ), 0)
                    else:
                        self.vals[cmap[c]] = self.inputs(self.n_events, len(self.trace))
                    self.n_events += 1
                self.trace.append(("measure", op.name, qs))
            elif isinstance(op, ControlFlowOp):
                self.control(inst, op, cmap, qmap, qs)
            elif op.name in ("barrier",):
                continue
            else:
                self.trace.append(("gate", op.name, qs, tuple(str(p) for p in op.params)))
        return self

    def control(self, inst, op, cmap, qmap, qs):
        def sub(blk):
            return ({bc: cmap[oc] for bc, oc in zip(blk.clbits, inst.clbits)}, {bq: qmap[oq] for bq, oq in zip(blk.qubits, inst.qubits)})
        k = op.name
        if k == "if_else":
            t = self.cond_value(op.condition, cmap)
            self.trace.append(("if", t))
            blk = op.blocks[0] if t else (op.blocks[1] if len(op.blocks) > 1 else None)
            if blk is not None:
                self.run(blk, *sub(blk))
        elif k == "while_loop":
            n = 0
            while True:
                t = self.cond_value(op.condition, cmap)
                self.trace.append(("while", t))
                if not t or n >= self.MAX_LOOP:
                    break
                self.run(op.blocks[0], *sub(op.blocks[0]))
                n += 1
        elif k == "for_loop":
            idx = op.params[0]
            n = len(idx) if idx is not None else 0
            self.trace.append(("for", n))
            for _ in range(n):
                self.run(op.blocks[0], *sub(op.blocks[0]))
        else:
            raise Refuse(f"evaluator: control-flow op {k}")

    def final(self):
        return tuple(self.vals[c] for c in self.qc.clbits)

    def final_measured(self):
        """values of the clbits that are measurement targets (the only classical values returned in a job result)"""
        return tuple(self.vals[c] for c in self.qc.clbits if c in self.measured_bits)


def _measured_bits(qc):
    out = set()

    def walk(circ, cmap):
        for inst in circ.data:
            op = inst.operation
            if op.name.startswith("measure"):
                out.update(cmap[c] for c in inst.clbits)
            elif isinstance(op, ControlFlowOp):
                for blk in op.blocks:
                    walk(blk, {bc: cmap[oc] for bc, oc in zip(blk.clbits, inst.clbits)})
    walk(qc, {c: c for c in qc.clbits})
    return out


def simulate(qc, inputs):
    top_c = {c: c for c in qc.clbits}
    top_q = {q: q for q in qc.qubits}
    s = Sim(qc, inputs)
    s.run(qc, top_c, top_q)
    return s


def classical_equivalent(a, b, n_random=300, seed=3):
    """Do two circuits make the same classical decisions? Measurement outcomes are keyed by (clbit, occurrence) so that a reordering of the
    measurements (a transpiler hoisting measure_2) does not matter; compared: every if/while decision in order, and the final value of every measured
    clbit. Quantum gates are ignored (a transpiled circuit has different gates and qubits). Random + structured patterns, not exhaustive."""
    import hashlib
    for pat in range(n_random):
        def inp(key, _t, pat=pat):
            return hashlib.sha256(f"{seed}/{pat}/{key}".encode()).digest()[0] & 1 if pat % 7 else (pat // 7) % 2

        def run(qc):
            s_ = Sim(qc, inp)
            s_.keyed = True
            s_.run(qc, {c: c for c in qc.clbits}, {q: q for q in qc.qubits})
            return [t for t in s_.trace if t[0] in ("if", "while", "for")], s_.final_measured()
        try:
            ra, rb = run(a), run(b)
        except Exception:  # noqa: BLE001
            return False
        if ra != rb:
            return False
    return True


def event_count(qc):
    return simulate(qc, lambda i, t: 0).n_events


def _patterns(n, n_random, rng):
    if n <= 14:
        for k in range(1 << n):
            yield [(k >> i) & 1 for i in range(n)], "exhaustive"
        return
    yield [0] * n, "structured+random"
    yield [1] * n, "structured+random"
    for i in range(n):
        p = [0] * n
        p[i] = 1
        yield p, "structured+random"
        p = [1] * n
        p[i] = 0
        yield p, "structured+random"
    for _ in range(n_random):
        dens = rng.choice([0.5, 0.5, 0.15, 0.85])
        yield [int(rng.random() < dens) for _ in range(n)], "structured+random"


def prove_equivalent(a, b, n_random=2000, seed=1):
    """Run both circuits on the same measurement outcomes; compare traces and final clbit values.
    Returns dict(equal, mode, n_events, n_patterns, first_mismatch)."""
    ea, eb = event_count(a), event_count(b)
    if ea != eb:
        return dict(equal=False, mode="-", n_events=ea, n_patterns=0, first_mismatch=f"measurement events differ: {ea} vs {eb}")
    rng = random.Random(seed)
    n = 0
    mode = "exhaustive"
    for pat, mode in _patterns(ea, n_random, rng):
        fa = lambda i, t, pat=pat: pat[i]  # noqa: E731
        sa, sb = simulate(a, fa), simulate(b, fa)
        n += 1
        if sa.trace != sb.trace or sa.final_measured() != sb.final_measured():
            return dict(equal=False, mode=mode, n_events=ea, n_patterns=n, first_mismatch=f"pattern {pat[:64]}")
    return dict(equal=True, mode=mode, n_events=ea, n_patterns=n, first_mismatch=None)


# ================================================================== rewrite
def _qubit_map_top(qc):
    return {q: q for q in qc.qubits}


@dataclass
class Group:
    reg: ClassicalRegister
    members: list = field(default_factory=list)      # (old register name, offset, width)


class Rewriter:
    def __init__(self, qc, groups_by_key, top_regs_by_key):
        self.qc = qc
        self.packed = groups_by_key                  # tuple(bits) -> (group reg, offset, width)
        self.top = top_regs_by_key                   # tuple(bits) -> top-level non-packed register (new circuit)

    # -- expressions (cmap: namespace clbit -> top-level clbit)
    def rw(self, e, cmap, used):
        if isinstance(e, expr.Value):
            return e
        if isinstance(e, expr.Var):
            v = e.var
            if isinstance(v, Clbit):
                return expr.lift(cmap[v])
            if isinstance(v, ClassicalRegister):
                key = tuple(cmap[b] for b in v)
                if key in self.packed:
                    raise Refuse(f"register {v.name!r} is read as a whole value where it cannot be expanded to bit level")
                reg = self.top.get(key)
                if reg is None:
                    raise Refuse(f"register {v.name!r} is referenced through a bit view that is not a top-level register")
                used.add(reg)
                return expr.lift(reg)
            return e
        if isinstance(e, expr.Index):
            t = e.target
            if isinstance(t, expr.Var) and isinstance(t.var, ClassicalRegister):
                key = tuple(cmap[b] for b in t.var)
                if key in self.packed:
                    if not isinstance(e.index, expr.Value):
                        raise Refuse("non-literal bit index on a merged register")
                    return expr.lift(key[int(e.index.value)])
            return expr.Index(self.rw(e.target, cmap, used), self.rw(e.index, cmap, used), e.type)
        if isinstance(e, expr.Binary):
            return expr.Binary(e.op, self.rw(e.left, cmap, used), self.rw(e.right, cmap, used), e.type)
        if isinstance(e, expr.Unary):
            return expr.Unary(e.op, self.rw(e.operand, cmap, used), e.type)
        if isinstance(e, expr.Cast):
            return expr.Cast(self.rw(e.operand, cmap, used), e.type, implicit=e.implicit)
        raise Refuse(f"unsupported expression node {type(e).__name__}")

    def rw_lvalue(self, lv, cmap, used):
        t = lv.target if isinstance(lv, expr.Index) else lv
        if isinstance(t, expr.Var) and isinstance(t.var, ClassicalRegister) and tuple(cmap[b] for b in t.var) in self.packed:
            raise Refuse("a merged register is a store target (internal: should have been rigid)")
        return self.rw(lv, cmap, used) if isinstance(lv, expr.Index) else (expr.lift(cmap[lv.var]) if isinstance(lv.var, Clbit) else self.rw(lv, cmap, used))

    # -- instruction lists. returns (items, regs_used, bits_needed)
    def body(self, circ, cmap, qmap, override=None):
        items, used, bits = [], set(), []

        def need(b):
            if b not in bits:
                bits.append(b)
        for idx, inst in enumerate(circ.data):
            op = inst.operation
            qs = [qmap[q] for q in inst.qubits]
            cs = [cmap[c] for c in inst.clbits]
            if override is not None and idx in override:             # whole-register store expanded to bit-level stores
                for lv, rv in override[idx]:
                    items.append(("store", lv, rv))
                    for b in _expr_bits(lv) + _expr_bits(rv):
                        need(b)
                continue
            if op.name == "store":
                lv = self.rw_lvalue(op.lvalue, cmap, used)
                rv = self.rw(op.rvalue, cmap, used)
                items.append(("store", lv, rv))
                for b in _expr_bits(lv) + _expr_bits(rv):
                    need(b)
            elif isinstance(op, ControlFlowOp):
                new_op, nq, nc = self.control(inst, op, cmap, qmap, used)
                items.append(("op", new_op, nq, nc))
                for b in nc:
                    need(b)
            else:
                items.append(("op", op, qs, cs))
                for b in cs:
                    need(b)
        return items, used, bits

    def control(self, inst, op, cmap, qmap, used_parent):
        k = op.name
        if k not in ("if_else", "while_loop", "for_loop"):
            raise Refuse(f"{k} cannot be rewritten (supported: if_else, while_loop, for_loop)")
        qlist = [qmap[q] for q in inst.qubits]
        clist = [cmap[c] for c in inst.clbits]
        cond = getattr(op, "condition", None)
        if cond is not None and not isinstance(cond, tuple):
            ncond = self.rw(cond, cmap, used_parent)
            extra = _expr_bits(ncond)
        else:
            if isinstance(cond, tuple):
                tgt = cond[0]
                if isinstance(tgt, ClassicalRegister) and tuple(cmap[b] for b in tgt) in self.packed:
                    raise Refuse("legacy (register, value) condition on a merged register")
                ncond = (cmap[tgt] if isinstance(tgt, Clbit) else self.top[tuple(cmap[b] for b in tgt)], cond[1])
                extra = [ncond[0]] if isinstance(tgt, Clbit) else list(ncond[0])
            else:
                ncond, extra = None, []
        parts = []
        for blk in op.blocks:
            bmap = {bc: cmap[oc] for bc, oc in zip(blk.clbits, inst.clbits)}
            qm = {bq: qmap[oq] for bq, oq in zip(blk.qubits, inst.qubits)}
            parts.append(self.body(blk, bmap, qm))
        bits = list(clist)
        for b in extra + [x for p in parts for x in p[2]]:
            if b not in bits:
                bits.append(b)
        blocks = []
        for items, used, _ in parts:
            B = QuantumCircuit(list(qlist), list(bits))
            for reg in sorted(used, key=lambda r: r.name):
                B.add_register(reg)
            for it in items:
                if it[0] == "store":
                    B.store(it[1], it[2])
                else:
                    B.append(it[1], it[2], it[3])
            blocks.append(B)
        if k == "if_else":
            new = IfElseOp(ncond, blocks[0], blocks[1] if len(blocks) > 1 else None, label=op.label)
        elif k == "while_loop":
            new = WhileLoopOp(ncond, blocks[0], label=op.label)
        else:
            new = ForLoopOp(op.params[0], op.params[1], blocks[0], label=op.label)
        if isinstance(ncond, expr.Expr):
            for reg in _expr_regs(ncond):
                used_parent.add(reg)
        return new, qlist, bits


def _expr_bits(e):
    out = []
    for n in D.expr_walk(e):
        if isinstance(n, expr.Var):
            v = n.var
            if isinstance(v, Clbit):
                out.append(v)
            elif isinstance(v, ClassicalRegister):
                out.extend(v)
    return out


def _expr_regs(e):
    return [n.var for n in D.expr_walk(e) if isinstance(n, expr.Var) and isinstance(n.var, ClassicalRegister)]


# ================================================================== analysis of register usage
def _walk_circuit(circ, cmap):
    """Yield (kind, payload) for every expression in the circuit and bodies: ('lvalue'|'rvalue'|'cond', expr, cmap), ('measure', clbits)."""
    for inst in circ.data:
        op = inst.operation
        if op.name == "store":
            yield "lvalue", op.lvalue, cmap
            ws = isinstance(op.lvalue, expr.Var) and isinstance(op.lvalue.var, ClassicalRegister) and op.rvalue.type.kind is types.Uint
            yield ("rvalue_ws" if ws else "rvalue"), op.rvalue, cmap
        elif op.name.startswith("measure"):
            yield "measure", [cmap[c] for c in inst.clbits], cmap
        elif isinstance(op, ControlFlowOp):
            cond = getattr(op, "condition", None)
            if cond is not None:
                yield ("tuplecond" if isinstance(cond, tuple) else "cond"), cond, cmap
            if op.name == "switch_case":
                raise Refuse("switch_case is not supported by --fix-pack")
            for blk in op.blocks:
                bm = {bc: cmap[oc] for bc, oc in zip(blk.clbits, inst.clbits)}
                yield from _walk_circuit(blk, bm)


def whole_reads(e):
    """registers read as a whole value (not through Index with a literal)"""
    if isinstance(e, expr.Index) and isinstance(e.target, expr.Var) and isinstance(e.target.var, ClassicalRegister) and isinstance(e.index, expr.Value):
        return []
    if isinstance(e, expr.Var):
        return [e.var] if isinstance(e.var, ClassicalRegister) else []
    return [r for c in D.expr_children(e) for r in whole_reads(c)]


_BIT_OPS = {O_B.BIT_AND: expr.bit_and, O_B.BIT_OR: expr.bit_or, O_B.BIT_XOR: expr.bit_xor}


def bit_expr(e, k, need):
    """Bool expression for bit k of a Uint expression built from bitwise ops over registers; need(register, k) records the bits read."""
    if isinstance(e, expr.Var) and isinstance(e.var, ClassicalRegister):
        if k >= len(e.var):
            raise Refuse(f"bit {k} is outside register {e.var.name!r}")
        need(e.var, k)
        return expr.lift(e.var[k])
    if isinstance(e, expr.Binary) and e.op in _BIT_OPS:
        return _BIT_OPS[e.op](bit_expr(e.left, k, need), bit_expr(e.right, k, need))
    if isinstance(e, expr.Unary) and e.op is O_U.BIT_NOT:
        return expr.bit_not(bit_expr(e.operand, k, need))
    what = type(e).__name__ + (f" {e.op.name}" if hasattr(e, "op") else "")
    raise Refuse(f"a whole-register value feeds {what}, which cannot be expanded to bit level without shifts/casts/arithmetic")


def plan_expansion(qc):
    """Top-level whole-register Uint stores -> (override {instruction index: [(lvalue, rvalue) bit stores]}, dropped dead stores).
    Needed bits of every store target come from literal-index / clbit reads and, transitively, from later expanded stores."""
    top = {tuple(r): r for r in qc.cregs}
    reg_of = {b: r for r in qc.cregs for b in r}
    needs = {}

    def mark(reg, k):
        needs.setdefault(reg.name, set()).add(k)

    def scan(e, cmap):
        for n in D.expr_walk(e):
            if isinstance(n, expr.Index) and isinstance(n.target, expr.Var) and isinstance(n.target.var, ClassicalRegister) and isinstance(n.index, expr.Value):
                mark(n.target.var, int(n.index.value))
            elif isinstance(n, expr.Var) and isinstance(n.var, Clbit):
                r = reg_of.get(cmap[n.var])
                if r is not None:
                    mark(r, list(r).index(cmap[n.var]))
    ws = {}
    ws_names = set()
    for inst in qc.data:
        o = inst.operation
        if o.name == "store" and isinstance(o.lvalue, expr.Var) and isinstance(o.lvalue.var, ClassicalRegister) and o.rvalue.type.kind is types.Uint:
            ws_names.add(o.lvalue.var.name)

    def opaque(e, where):
        for reg in whole_reads(e):
            if reg.name in ws_names:
                raise Refuse(f"the stored register {reg.name!r} is read as a whole value at {where}: expanding that to bit level would need compare/logic constructs "
                             f"that no accepted probe used, so it is refused")

    def walk(circ, cmap, level):
        for idx, inst in enumerate(circ.data):
            op = inst.operation
            if op.name == "store":
                is_ws = isinstance(op.lvalue, expr.Var) and isinstance(op.lvalue.var, ClassicalRegister) and op.rvalue.type.kind is types.Uint
                if is_ws:
                    if level > 0:
                        raise Refuse("a whole-register store inside a control-flow body cannot be expanded")
                    ws[idx] = (op.lvalue.var, op.rvalue)
                else:
                    opaque(op.rvalue, "a store")
                    scan(op.rvalue, cmap)
                    if isinstance(op.lvalue, expr.Index):
                        scan(op.lvalue.index, cmap)
            elif isinstance(op, ControlFlowOp):
                c = getattr(op, "condition", None)
                if isinstance(c, expr.Expr):
                    opaque(c, "a condition")
                    scan(c, cmap)
                elif isinstance(c, tuple) and isinstance(c[0], ClassicalRegister) and c[0].name in ws_names:
                    raise Refuse(f"the stored register {c[0].name!r} is used in a legacy (register, value) condition; refused")
                for blk in op.blocks:
                    walk(blk, {bc: cmap[oc] for bc, oc in zip(blk.clbits, inst.clbits)}, level + 1)
    walk(qc, {c: c for c in qc.clbits}, 0)
    for idx in sorted(ws, reverse=True):                       # later stores first: their reads create needs on earlier targets
        reg, rv = ws[idx]
        for k in sorted(needs.get(reg.name, ())):
            bit_expr(rv, k, mark)                              # raises Refuse when not expandable
    override, dropped = {}, []
    for idx in sorted(ws):
        reg, rv = ws[idx]
        ks = sorted(needs.get(reg.name, ()))
        if not ks:
            dropped.append(reg.name)
        override[idx] = [(expr.lift(reg[k]), bit_expr(rv, k, lambda r, kk: None)) for k in ks]
    return override, dropped


def register_usage(qc):
    """-> (measured: set of register keys, rigid: dict key -> reason). Key = tuple of top-level clbits."""
    measured_bits, rigid = set(), {}
    top = {tuple(r): r for r in qc.cregs}

    def mark(key, why):
        rigid.setdefault(key, why)
    for kind, payload, cmap in _walk_circuit(qc, {c: c for c in qc.clbits}):
        if kind == "measure":
            measured_bits.update(payload)
            continue
        if kind == "tuplecond":
            tgt = payload[0]
            if isinstance(tgt, ClassicalRegister):
                mark(tuple(cmap[b] for b in tgt), "used in a legacy (register, value) condition")
            continue
        if kind == "lvalue":
            t = payload.target if isinstance(payload, expr.Index) else payload
            if isinstance(t, expr.Var) and isinstance(t.var, ClassicalRegister):
                mark(tuple(cmap[b] for b in t.var), "is the target of a store")
            continue
        if kind != "rvalue_ws":
            for reg in whole_reads(payload):
                mark(tuple(cmap[b] for b in reg), "read as a whole value outside a bit-expandable store")
        for n in D.expr_walk(payload):
            if isinstance(n, expr.Index) and isinstance(n.target, expr.Var) and isinstance(n.target.var, ClassicalRegister) \
                    and not isinstance(n.index, expr.Value):
                mark(tuple(cmap[b] for b in n.target.var), "indexed with a non-literal")
            if isinstance(n, expr.Var) and isinstance(n.var, ClassicalRegister):
                if tuple(cmap[b] for b in n.var) not in top:
                    raise Refuse(f"expression uses register {n.var.name!r} as a view that is not a top-level register")
    measured = {k for k in top if any(b in measured_bits for b in k)}
    return measured, rigid


# ================================================================== fix_pack
@dataclass
class FixResult:
    circuit: object = None
    refused: str = ""
    changed: bool = False
    before: dict = None
    after: dict = None
    groups: list = field(default_factory=list)
    equivalence: dict = None
    transpile: dict = None
    notes: list = field(default_factory=list)

    def to_dict(self):
        return dict(refused=self.refused, changed=self.changed, before_status=self.before and self.before["status"],
                    after_status=self.after and self.after["status"], groups=self.groups, equivalence=self.equivalence,
                    transpile=self.transpile, notes=self.notes,
                    before=self.before, after=self.after)


def transpile_check(qc, backend=None):
    """Transpile exactly as dccheck.preflight does (bundled fake Heron r2 + measure_2, level 1, seed 7). Returns (dict, circuit|None)."""
    t, info = D.isa_circuit(qc, backend)
    if t is None:
        return dict(ok=False, target=info["target"] or "-", error=info["error"]), None
    return dict(ok=True, target=info["target"], n_ops=len(t.data)), t


def _group(cands, width):
    groups, cur, w = [], [], 0
    for r in cands:
        if cur and w + len(r) > width:
            groups.append(cur)
            cur, w = [], 0
        cur.append(r)
        w += len(r)
    if cur:
        groups.append(cur)
    return groups


def fix_pack(qc, rules=None, backend=None, today=None, max_age_days=60, group_width=32, n_random=2000, seed=1):
    rules = rules or R.load_rules()
    kw = dict(backend=backend, today=today, max_age_days=max_age_days)
    before = D.check_logical(qc, rules, **kw)
    res = FixResult(before=before)
    over = [i for i in before["ifs"] if i["status"] != PASS and len(i["cone_registers"]) >= 2]
    if not over:
        res.notes.append("no if with a non-PASS verdict and two or more measured registers in its cone: nothing to pack")
        res.circuit, res.after = qc, before
        return res
    try:
        if qc.num_vars or list(qc.iter_declared_vars()):
            raise Refuse("circuit declares Vars: not supported by --fix-pack (Vars need their own declaration rewrite)")
        if any(h["rule"] == "overlapping_registers" for h in before["hazards"]):
            raise Refuse("overlapping registers: fix the overlap first (it is its own rejection, 6327)")
        measured, rigid = register_usage(qc)
        names = {tuple(r): r for r in qc.cregs}
        want = set()
        for i in over:
            want.update(i["cone_registers"])
        cands = [r for r in qc.cregs if r.name in want and tuple(r) in measured and tuple(r) not in rigid and len(r) < group_width]
        skipped = [(r.name, rigid[tuple(r)]) for r in qc.cregs if r.name in want and tuple(r) in rigid]
        if len(cands) < 2:
            raise Refuse("fewer than two mergeable measured registers in the failing cone" +
                         (f"; not mergeable: {skipped[:4]}" if skipped else ""))
        groups = _group(cands, group_width)
        used_names = {r.name for r in qc.cregs}
        packed, glist = {}, []
        for gi, members in enumerate(groups):
            nm = f"pk{gi}"
            while nm in used_names:
                nm += "_"
            used_names.add(nm)
            bits = [b for r in members for b in r]
            greg = ClassicalRegister(bits=bits, name=nm)
            off = 0
            g = Group(greg)
            for r in members:
                packed[tuple(r)] = (greg, off, len(r))
                g.members.append((r.name, off, len(r)))
                off += len(r)
            glist.append(g)
        merged = {tuple(r) for r in cands}
        # new circuit: same bits in the same order; unmerged registers kept, merged ones replaced by the wide groups
        new = QuantumCircuit(list(qc.qubits), list(qc.clbits), name=qc.name, global_phase=qc.global_phase, metadata=qc.metadata)
        for qr in qc.qregs:
            new.add_register(qr)
        top = {}
        for r in qc.cregs:
            if tuple(r) in merged:
                continue
            new.add_register(r)
            top[tuple(r)] = r
        for g in glist:
            new.add_register(g.reg)
        rwr = Rewriter(qc, packed, top)
        override, dropped = plan_expansion(qc)
        if dropped:
            res.notes.append(f"unread whole-register store(s) to {', '.join(dropped)} dropped (never read; stored values are not returned in the job result)")
        items, _, _ = rwr.body(qc, {c: c for c in qc.clbits}, {q: q for q in qc.qubits}, override=override)
        for it in items:
            if it[0] == "store":
                new.store(it[1], it[2])
            else:
                new.append(it[1], it[2], it[3])
    except Refuse as ex:
        res.refused = str(ex)
        return res
    except Exception as ex:  # noqa: BLE001
        res.refused = f"rewrite failed ({type(ex).__name__}: {str(ex)[:200]}); the circuit was not modified"
        return res
    res.groups = [dict(name=g.reg.name, width=len(g.reg), members=[dict(register=m, offset=o, width=w) for m, o, w in g.members]) for g in glist]
    res_circuit = new
    after = D.check_logical(new, rules, **kw)
    res.after = after
    failing = [(i["where"], c["rule"]) for i in after["ifs"] for c in i["checks"] if c["status"] == FAIL] + \
        [(h["message"], h["rule"]) for h in after["hazards"] if h["status"] == FAIL]
    nb = {}
    for h in before["hazards"]:
        nb[h["rule"]] = nb.get(h["rule"], 0) + 1
    na = {}
    for h in after["hazards"]:
        na[h["rule"]] = na.get(h["rule"], 0) + 1
    introduced = [(r, na[r] - nb.get(r, 0)) for r in na if na[r] > nb.get(r, 0)]
    if introduced and not failing:      # the rewrite must not create a hazard (of any verdict, e.g. a copy store) that the original did not have
        res.refused = f"the rewritten circuit would introduce hazard(s) the original does not have ({introduced[:3]}); the circuit was not returned"
        return res
    if failing:
        res.refused = (f"the rewritten circuit would violate a hard rule ({failing[:3]}), e.g. a packed register wider than 32 bits "
                       f"used directly as an if operand; the circuit was not returned (try a smaller group_width)")
        return res
    covered = {c["signature"] for c in rules.get("accepted_constructs", [])}
    all_sig = D.construct_signatures(res_circuit)
    uncovered = sorted(all_sig - covered)
    if uncovered:
        carried = sorted(set(uncovered) & D.construct_signatures(qc))
        res.refused = (f"the rewritten circuit contains construct(s) that no accepted QPU probe used: {uncovered[:5]}"
                       + (f" (already in the input: {carried[:5]})" if carried else "") + "; the circuit was not returned")
        return res
    emitted = all_sig - D.construct_signatures(qc)
    res.notes.append(f"every construct of the result ({len(all_sig)}; newly emitted: {', '.join(sorted(emitted)) or 'none'}) appears in an accepted QPU probe (rules accepted_constructs)")
    pk_b = max(i["metrics"]["peak_live_regs"] for i in before["ifs"])
    pk_a = max(i["metrics"]["peak_live_regs"] for i in after["ifs"])
    if pk_a >= pk_b:
        res.refused = f"packing did not reduce the peak of live registers ({pk_b} -> {pk_a}); unmergeable registers: {skipped[:6]}"
        return res
    try:
        buf = __import__("io").BytesIO()
        qpy.dump(new, buf)
        buf.seek(0)
        back = qpy.load(buf)[0]
        if D.check_logical(back, rules, **kw)["ifs"] and [i["metrics"] for i in D.check_logical(back, rules, **kw)["ifs"]] != [i["metrics"] for i in after["ifs"]]:
            res.notes.append("QPY round trip changes the metrics")
    except Exception as ex:  # noqa: BLE001
        res.refused = f"rewritten circuit does not survive a QPY round trip ({type(ex).__name__}: {str(ex)[:160]})"
        return res
    eq = prove_equivalent(qc, new, n_random=n_random, seed=seed)
    res.equivalence = eq
    if not eq["equal"]:
        res.refused = f"equivalence check FAILED ({eq['first_mismatch']}); the circuit was not returned"
        return res
    tc, t = transpile_check(new, backend)
    res.transpile = tc
    if not tc["ok"]:
        res.refused = f"rewritten circuit does not transpile: {tc.get('error')}"
        return res
    if t is not None:
        ta = D.check_logical(t, rules, **kw)
        res.transpile["transpiled_status"] = ta["status"]
        res.transpile["transpiled_cone_registers"] = [i["metrics"]["cone_regs"] for i in ta["ifs"]]
        if ta["status"] in (FAIL, "ERROR"):
            why = "; ".join(c["reason"][:160] for i in ta["ifs"] for c in i["checks"] if c["status"] == FAIL) or ta.get("error", "")
            res.refused = f"the transpiled rewritten circuit (the one that would run) is {ta['status']}: {why}; the circuit was not returned"
            return res
        if R._ORDER[ta["status"]] > R._ORDER[after["status"]]:
            res.after = dict(after, status=ta["status"], status_note="worst of logical and transpiled")
    d_ops = max((i["metrics"]["cone_ops"] for i in after["ifs"]), default=0) - max((i["metrics"]["cone_ops"] for i in before["ifs"]), default=0)
    res.notes.append(f"cone ops change by {d_ops:+d} (latency model: about {d_ops * rules['latency']['slope_ns_per_op']:+.0f} ns)")
    res.notes.append("the rewritten circuit uses only constructs of accepted probes, but this exact circuit has not been run on the QPU; "
                     "run fixpack_qpu.py (v2 plan) to verify a packed circuit of this shape")
    res.circuit, res.changed = new, True
    return res


def format_fix(res):
    L = []
    b = res.before
    L.append(f"fix-pack for circuit {b['circuit']!r}: before {b['status']}")
    L += [f"WARNING: {w}" for w in b.get("warnings", [])]
    if res.refused:
        L.append(f"REFUSED: {res.refused}")
        return "\n".join(L)
    if not res.changed:
        L += [f"unchanged: {n}" for n in res.notes]
        return "\n".join(L)
    for g in res.groups:
        L.append(f"  {g['name']} ({g['width']} bits) <- " + ", ".join(f"{m['register']}@{m['offset']}+{m['width']}" for m in g["members"]))
    def mx(rep, k):
        return max(i["metrics"][k] for i in rep["ifs"])
    L.append("resources before -> after: " + "; ".join(f"{lab} {mx(b, k)} -> {mx(res.after, k)}" for lab, k in
             (("peak live registers", "peak_live_regs"), ("left-to-right stack need", "lr_stack"), ("measured registers in cone", "cone_regs"),
              ("largest store ops", "max_store_ops"), ("cone ops", "cone_ops"))) + f";  verdict {b['status']} -> {res.after['status']}")
    eq = res.equivalence
    L.append(f"equivalence: {eq['mode']}, {eq['n_patterns']} measurement-outcome patterns over {eq['n_events']} measurement events: "
             f"traces and final clbits identical = {eq['equal']}")
    tc = res.transpile
    L.append(f"transpile: ok={tc['ok']} target={tc['target']}; transpiled verdict {tc.get('transpiled_status')}")
    L += [f"note: {n}" for n in res.notes]
    return "\n".join(L)
