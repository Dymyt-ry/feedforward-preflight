# SPDX-License-Identifier: Apache-2.0
"""dccheck: pre-flight checker for IBM dynamic circuits with classical expressions.

Predicts, BEFORE submission, from empirical rules measured on a real backend (rules/*.json, data not code):
  * whether the backend will reject the circuit (error 6057, plus a few documented rejection patterns);
  * the observed decision latency after measurement end.
Optionally rewrites the circuit (--fix-pack) so measurements into many small registers go into few wide registers.

CLI    python dccheck.py CIRCUIT [--transpiled ISA_CIRCUIT | --no-transpile] [--rules FILE] [--backend NAME] [--json] [--max-age-days 60] [--fix-pack [-o OUT.qpy]]
       CIRCUIT = file.qpy | path/to/file.py:func | package.module:func   (func takes no arguments, returns a QuantumCircuit)
API    from dccheck import preflight, check, load_rules, fix_pack
       rep = preflight(circuit, load_rules());  rep["status"] (worst of as-passed and transpiled; check() returns the same status); check(circuit) = the same verdict with the as-passed report; check_logical(circuit) = as passed only, no verdict; print(format_report(rep))

Exit status: 0 PASS, 1 FAIL, 2 UNCERTAIN (no FAIL), 3 internal error (no verdict), 4 NOT APPLICABLE (no classical feedforward), 64 usage / loading error.
The rules are empirical observations of one backend on one date. They are NOT a model of the controller firmware.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import importlib.util
import json
import os
import sys
import uuid
from dataclasses import dataclass

from qiskit import QuantumCircuit, qpy
from qiskit.circuit import Clbit, ClassicalRegister, ControlFlowOp
from qiskit.circuit.classical import expr, types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.setrecursionlimit(max(sys.getrecursionlimit(), 30000))   # deep expression chains; check() also runs on a large thread stack
import dcrules as R  # noqa: E402
import resources as RES  # noqa: E402  (single resource-vector implementation, shared with the conformance suite)
from dcrules import ERROR, FAIL, NOT_APPLICABLE, PASS, UNCERTAIN, load_rules  # noqa: E402,F401

O_B = expr.Binary.Op
O_U = expr.Unary.Op
_ARITH = {O_B.ADD, O_B.SUB, O_B.MUL, O_B.DIV}
_SHIFT = {O_B.SHIFT_LEFT, O_B.SHIFT_RIGHT}
_CMP = {O_B.EQUAL, O_B.NOT_EQUAL, O_B.LESS, O_B.LESS_EQUAL, O_B.GREATER, O_B.GREATER_EQUAL}


# ------------------------------------------------------------------ expression helpers
def expr_children(e):
    if isinstance(e, expr.Binary):
        return [e.left, e.right]
    if isinstance(e, expr.Unary):
        return [e.operand]
    if isinstance(e, expr.Cast):
        return [e.operand]
    if isinstance(e, expr.Index):
        return [e.target, e.index]
    return []


def expr_ops(e):
    """Binary + Unary + Index nodes (tree count, shared sub-expressions count at each use); casts and literals are free."""
    n = 1 if isinstance(e, (expr.Binary, expr.Unary, expr.Index)) else 0
    return n + sum(expr_ops(c) for c in expr_children(e))


def expr_depth(e):
    cs = expr_children(e)
    own = 1 if isinstance(e, (expr.Binary, expr.Unary, expr.Index)) else 0
    return own + (max(expr_depth(c) for c in cs) if cs else 0)


def expr_walk(e):
    yield e
    for c in expr_children(e):
        yield from expr_walk(c)


def expr_classes(e):
    """Counts by class + whether any op works on Uint (non-Bool) data."""
    c = dict(bitwise=0, arith=0, shift=0, compare=0, index=0, unary=0)
    uint = False
    for n in expr_walk(e):
        if isinstance(n, expr.Binary):
            if n.op in _ARITH:
                c["arith"] += 1
            elif n.op in _SHIFT:
                c["shift"] += 1
            elif n.op in _CMP:
                c["compare"] += 1
            else:
                c["bitwise"] += 1
            if n.left.type.kind is not types.Bool or n.right.type.kind is not types.Bool:
                uint = True
        elif isinstance(n, expr.Unary):
            c["unary"] += 1
            uint = uint or n.operand.type.kind is not types.Bool
        elif isinstance(n, expr.Index):
            c["index"] += 1
            uint = True
    return c, uint


def max_literal_width(e):
    w = 0
    for n in expr_walk(e):
        if isinstance(n, expr.Value) and n.type.kind is types.Uint:
            w = max(w, n.type.width)
    return w


def operand_bits(e):
    """Widest Uint value the condition operates on; a bit of a wide register (Index with a literal) counts as 1 bit."""
    if isinstance(e, expr.Index) and isinstance(e.target, expr.Var) and isinstance(e.index, expr.Value):
        return 1
    own = 0 if isinstance(e, expr.Value) else (e.type.width if e.type.kind is types.Uint else 1)
    return max([own] + [operand_bits(c) for c in expr_children(e)])


def _root_excluded_ops(cond):
    """ops of the `if` condition NOT counted for latency: a final compare against a literal or a final bit index, which the
    stopwatch probes also had (their `store ops` exclude that last op)."""
    if isinstance(cond, expr.Binary) and cond.op in _CMP and (isinstance(cond.right, expr.Value) or isinstance(cond.left, expr.Value)):
        return 1
    if isinstance(cond, expr.Index):
        return 1
    return 0


_SYM = {O_B.BIT_AND: "&", O_B.BIT_OR: "|", O_B.BIT_XOR: "^", O_B.LOGIC_AND: "&&", O_B.LOGIC_OR: "||", O_B.EQUAL: "==",
        O_B.NOT_EQUAL: "!=", O_B.LESS: "<", O_B.LESS_EQUAL: "<=", O_B.GREATER: ">", O_B.GREATER_EQUAL: ">=",
        O_B.SHIFT_LEFT: "<<", O_B.SHIFT_RIGHT: ">>", O_B.ADD: "+", O_B.SUB: "-", O_B.MUL: "*", O_B.DIV: "/"}


def fmt_expr(e, qc=None):
    """Compact human-readable expression text."""
    def bit(b):
        if qc is not None:
            loc = qc.find_bit(b)
            if loc.registers:
                return f"{loc.registers[0][0].name}[{loc.registers[0][1]}]"
            return f"bit{loc.index}"
        return "bit"

    def f(x):
        if isinstance(x, expr.Value):
            return str(int(x.value)) if x.type.kind is types.Uint else str(bool(x.value))
        if isinstance(x, expr.Var):
            v = x.var
            if isinstance(v, Clbit):
                return bit(v)
            return v.name if isinstance(v, ClassicalRegister) else (getattr(x, "name", None) or "var")
        if isinstance(x, expr.Cast):
            return f(x.operand)
        if isinstance(x, expr.Unary):
            return ("!" if x.op is O_U.LOGIC_NOT else "~") + f(x.operand)
        if isinstance(x, expr.Index):
            return f"{f(x.target)}[{f(x.index)}]"
        if isinstance(x, expr.Binary):
            return f"({f(x.left)} {_SYM.get(x.op, '?')} {f(x.right)})"
        return "?"
    return f(e)


def short(s, n=70):
    return s if len(s) <= n else s[: n - 3] + "..."


def natkey(name):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


# ------------------------------------------------------------------ dataflow
@dataclass(frozen=True)
class Def:
    regs: frozenset = frozenset()
    vars: frozenset = frozenset()
    stores: frozenset = frozenset()
    depth: int = 0


EMPTY = Def()


def _union(parts):
    parts = [p for p in parts if p is not None]
    if not parts:
        return EMPTY
    return Def(frozenset().union(*(p.regs for p in parts)), frozenset().union(*(p.vars for p in parts)),
               frozenset().union(*(p.stores for p in parts)), max(p.depth for p in parts))


class State:
    def __init__(self, bits=None, vars_=None):
        self.bits = dict(bits or {})
        self.vars = dict(vars_ or {})

    def copy(self):
        return State(self.bits, self.vars)


def join(states):
    out = State()
    keys_b = set().union(*(s.bits.keys() for s in states))
    keys_v = set().union(*(s.vars.keys() for s in states))
    for k in keys_b:
        out.bits[k] = _union([s.bits.get(k) for s in states])
    for k in keys_v:
        out.vars[k] = _union([s.vars.get(k) for s in states])
    return out


class Analyzer:
    def __init__(self, qc):
        self.qc = qc
        self.stores = []          # dict(ops, depth, klass_uint, where)
        self.entries = []
        self.var_names = {}
        self.unmeasured_reads = set()
        self.hazard_exprs = []    # (where, kind, expr) for static detectors
        self.ver = {}             # clbit/var -> write counter (a broadcast of an unchanged value counts once)
        self.broadcasts = {}      # (bit, version) tuple -> number of bits broadcast
        self.body_meas = []       # measure / reset inside the body of an if_else
        self.kind_stack = []      # kinds of the enclosing control-flow ops

    # -- naming
    def regs_of(self, bit):
        loc = self.qc.find_bit(bit)
        names = [r.name for r, _ in loc.registers]
        return frozenset(names) if names else frozenset({f"bit{loc.index}"})

    def _var_key(self, v):
        key = v.var
        self.var_names[key] = getattr(v, "name", None) or str(key)[:8]
        return key

    def written_bits(self, lv, cmap):
        t = lv.target if isinstance(lv, expr.Index) else lv
        v = t.var if isinstance(t, expr.Var) else None
        if isinstance(v, Clbit):
            return [cmap[v]]
        if isinstance(v, ClassicalRegister):
            if isinstance(lv, expr.Index) and isinstance(lv.index, expr.Value):
                return [cmap[v[int(lv.index.value)]]]
            return [cmap[b] for b in v]
        return [("var", self._var_key(t))] if isinstance(t, expr.Var) else []

    def target_name(self, lv):
        t = lv.target if isinstance(lv, expr.Index) else lv
        v = t.var
        if isinstance(v, ClassicalRegister):
            return v.name
        if isinstance(v, Clbit):
            return "/".join(sorted(self.regs_of(v)))
        return "var:" + (getattr(t, "name", None) or str(v)[:8])

    def cond_bits(self, cond, cmap):
        """Bits (or Var keys) the condition reads directly; what the controller has to broadcast to evaluate it."""
        out = []
        if isinstance(cond, tuple):
            tgt = cond[0]
            return [cmap[tgt]] if isinstance(tgt, Clbit) else [cmap[b] for b in tgt]

        def walk(x):
            if isinstance(x, expr.Index) and isinstance(x.target, expr.Var) and isinstance(x.target.var, ClassicalRegister) \
                    and isinstance(x.index, expr.Value):
                out.append(cmap[x.target.var[int(x.index.value)]])
                return
            if isinstance(x, expr.Var):
                v = x.var
                if isinstance(v, Clbit):
                    out.append(cmap[v])
                elif isinstance(v, ClassicalRegister):
                    out.extend(cmap[b] for b in v)
                else:
                    out.append(("var", self._var_key(x)))
                return
            for c in expr_children(x):
                walk(c)
        walk(cond)
        return out

    # -- reads
    def read_def(self, e, cmap, state):
        parts = []

        def bit_part(b):
            ob = cmap[b]
            d = state.bits.get(ob)
            if d is None:
                self.unmeasured_reads.add(ob)
            parts.append(d)

        def walk(x):
            if isinstance(x, expr.Index) and isinstance(x.target, expr.Var) and isinstance(x.target.var, ClassicalRegister) \
                    and isinstance(x.index, expr.Value):
                bit_part(x.target.var[int(x.index.value)])
                return
            if isinstance(x, expr.Var):
                v = x.var
                if isinstance(v, Clbit):
                    bit_part(v)
                elif isinstance(v, ClassicalRegister):
                    for b in v:
                        bit_part(b)
                else:
                    k = self._var_key(x)
                    d = state.vars.get(k, EMPTY)
                    parts.append(Def(d.regs, d.vars | {self.var_names[k]}, d.stores, d.depth))
                return
            for c in expr_children(x):
                walk(c)
        walk(e)
        return _union(parts)

    def read_condition(self, cond, cmap, state):
        if isinstance(cond, tuple):                    # legacy (Clbit|Register, int)
            tgt = cond[0]
            bits = [tgt] if isinstance(tgt, Clbit) else list(tgt)
            parts = []
            for b in bits:
                d = state.bits.get(cmap[b])
                if d is None:
                    self.unmeasured_reads.add(cmap[b])
                parts.append(d)
            return _union(parts), None
        return self.read_def(cond, cmap, state), cond

    # -- writes
    def assign(self, lv, new, cmap, state):
        if isinstance(lv, expr.Index) and isinstance(lv.target, expr.Var) and isinstance(lv.target.var, ClassicalRegister) \
                and isinstance(lv.index, expr.Value):
            state.bits[cmap[lv.target.var[int(lv.index.value)]]] = new
            return
        if isinstance(lv, expr.Index) and isinstance(lv.target, expr.Var) and isinstance(lv.target.var, ClassicalRegister):
            for b in lv.target.var:                    # dynamic index: any bit may be written
                ob = cmap[b]
                state.bits[ob] = _union([state.bits.get(ob), new])
            return
        v = lv.var if isinstance(lv, expr.Var) else None
        if isinstance(v, Clbit):
            state.bits[cmap[v]] = new
        elif isinstance(v, ClassicalRegister):
            for b in v:
                state.bits[cmap[b]] = new
        elif isinstance(lv, expr.Var):
            k = self._var_key(lv)
            state.vars[k] = Def(new.regs, new.vars | {self.var_names[k]}, new.stores, new.depth)

    # -- main walk
    def run(self, circ, cmap, state, ctrl, path, level):
        for idx, inst in enumerate(circ.data):
            op = inst.operation
            name = op.name
            here = f"{path}{name}[{idx}]"
            if name in ("measure", "measure_2") or name.startswith("measure"):
                if "if_else" in self.kind_stack:
                    self.body_meas.append(here)
                for c in inst.clbits:
                    ob = cmap[c]
                    state.bits[ob] = Def(regs=self.regs_of(ob))
                    self.ver[ob] = self.ver.get(ob, 0) + 1
            elif name == "reset" and "if_else" in self.kind_stack:
                self.body_meas.append(here)
            elif name == "store":
                rv, lv = op.rvalue, op.lvalue
                self.hazard_exprs.append((here, "store", rv, lv))
                rd = self.read_def(rv, cmap, state)
                sid = len(self.stores)
                _, uint = expr_classes(rv)
                self.stores.append(dict(ops=expr_ops(rv), depth=expr_depth(rv), uint=uint, where=here, ops_latency=expr_ops(rv),
                                        target=self.target_name(lv), lit_w=max_literal_width(rv), arith=expr_classes(rv)[0]["arith"] > 0))
                for wb in self.written_bits(lv, cmap):
                    self.ver[wb] = self.ver.get(wb, 0) + 1
                self.assign(lv, Def(rd.regs, rd.vars, rd.stores | {sid}, rd.depth + expr_depth(rv)), cmap, state)
            elif isinstance(op, ControlFlowOp):
                state = self.control(circ, inst, op, cmap, state, ctrl, here, level)
        return state

    def control(self, circ, inst, op, cmap, state, ctrl, here, level):
        kind = op.name
        cond = getattr(op, "condition", None)
        if kind == "switch_case":
            cond = op.target
        if isinstance(cond, (Clbit, ClassicalRegister)):
            cond = (cond, 0)                                   # a bare bit/register operand: treat like a legacy (target, value) condition
        d, cexpr = (EMPTY, None)
        if cond is not None:
            d, cexpr = self.read_condition(cond, cmap, state)
            if cexpr is not None:
                self.hazard_exprs.append((here, "condition", cexpr, None))
        ctrl_regs = frozenset().union(*(c.regs for c in ctrl)) if ctrl else frozenset()
        if cond is not None:
            bits = self.cond_bits(cond, cmap)
            key = tuple(sorted(((b, self.ver.get(b, 0)) for b in bits), key=repr))
            self.broadcasts.setdefault(key, len(bits))

        def body_map(blk):
            return {bc: cmap[oc] for bc, oc in zip(blk.clbits, inst.clbits)}
        if kind == "while_loop":                                   # loop-carried: analyse twice, report the joined cone
            s1 = state.copy()
            self.kind_stack.append(kind)
            self.run(op.blocks[0], body_map(op.blocks[0]), s1, ctrl + [d], here + " > body > ", level + 1)
            self.kind_stack.pop()
            joined = join([state, s1])
            d2, _ = self.read_condition(cond, cmap, joined)
            d = _union([d, d2])
            self.add_entry(kind, here, cexpr, cond, d, ctrl, ctrl_regs, level)
            return joined
        self.add_entry(kind, here, cexpr, cond, d, ctrl, ctrl_regs, level)
        outs = []
        for bi, blk in enumerate(op.blocks):
            s2 = state.copy()
            lab = {"if_else": ["true", "false"], "switch_case": [f"case{i}" for i in range(len(op.blocks))],
                   "for_loop": ["body"]}.get(kind, [f"b{bi}"])[bi]
            self.kind_stack.append(kind)
            outs.append(self.run(blk, body_map(blk), s2, ctrl + [d], f"{here} > {lab} > ", level + 1))
            self.kind_stack.pop()
        if kind == "if_else" and len(op.blocks) == 2:
            return join(outs)
        return join([state] + outs)

    def add_entry(self, kind, here, cexpr, cond, d, ctrl, ctrl_regs, level):
        sids = sorted(d.stores)
        sops = [self.stores[s]["ops"] for s in sids]
        cond_ops = expr_ops(cexpr) if cexpr is not None else 0
        lat_ops = sum(self.stores[s]["ops_latency"] for s in sids) + max(cond_ops - (_root_excluded_ops(cexpr) if cexpr is not None else 0), 0)
        uint = False
        for s in sids:
            uint = uint or self.stores[s]["uint"]
        targets = sorted({self.stores[s]["target"] for s in sids}, key=natkey)
        lit_w = max([self.stores[s]["lit_w"] for s in sids] + [max_literal_width(cexpr) if cexpr is not None else 0])
        arith = any(self.stores[s]["arith"] for s in sids) or (cexpr is not None and expr_classes(cexpr)[0]["arith"] > 0)
        if isinstance(cond, tuple):
            obits = len(cond[0]) if isinstance(cond[0], ClassicalRegister) else 1
        else:
            obits = operand_bits(cexpr) if cexpr is not None else 0
        if cexpr is not None:
            c, u = expr_classes(cexpr)
            uint = uint or u
        e = dict(kind=kind, where=here, level=level, in_if="if_else" in self.kind_stack,
                 in_loop=any(k in ("for_loop", "while_loop", "switch_case") for k in self.kind_stack), condition=short(fmt_expr(cexpr, self.qc)) if cexpr is not None else short(repr(cond)),
                 cone_registers=sorted(d.regs, key=natkey), cone_vars=sorted(d.vars), n_stores=len(sids), store_ops=sops,
                 cond_ops=cond_ops, latency_ops=lat_ops, depth=d.depth + (expr_depth(cexpr) if cexpr is not None else 0),
                 klass=("uint" if uint else "bool") if (sops or cond_ops) else "none",
                 control_registers=sorted(ctrl_regs - d.regs, key=natkey), stored_targets=targets, max_literal_width=lit_w,
                 has_arithmetic=arith, operand_bits=obits)
        self.entries.append(e)


# ------------------------------------------------------------------ static detectors (rules refer to them by name)
def _det_overlap(qc, an):
    seen = {}
    out = []
    for r in qc.cregs:
        for b in r:
            if b in seen and seen[b] != r.name:
                out.append(f"registers {seen[b]} and {r.name} share a bit")
            seen[b] = r.name
    return sorted(set(out))


def _declared_vars(qc):
    try:
        return list(qc.iter_declared_vars())
    except Exception:  # noqa: BLE001
        return []


def _det_declared_var(qc, an):
    out = []
    declared = {v.var: v for v in _declared_vars(qc)}
    if not declared:
        return out
    first = {}
    for inst in qc.data:
        op = inst.operation
        if op.name == "store" and isinstance(op.lvalue, expr.Var) and op.lvalue.var in declared and op.lvalue.var not in first:
            first[op.lvalue.var] = op.rvalue
    for k, v in declared.items():
        init = first.get(k)
        lit = isinstance(init, expr.Value)
        out.append(f"declared Var {v.name!r} " + ("with a literal initialiser" if lit else "(initialiser is an expression)"))
    return out


def _det_declared_var_literal(qc, an):
    return [m for m in _det_declared_var(qc, an) if "literal" in m]


def _det_shift_amount(qc, an):
    return sorted({f"{w}: shift by a non-literal amount" for w, kind, e, lv in an.hazard_exprs
                   for n in expr_walk(e) if isinstance(n, expr.Binary) and n.op in _SHIFT and not isinstance(n.right, expr.Value)})


def _det_dynamic_index(qc, an):
    return sorted({f"{w}: bit index is not a literal" for w, kind, e, lv in an.hazard_exprs
                   for n in expr_walk(e) if isinstance(n, expr.Index) and not isinstance(n.index, expr.Value)})


def _det_u32_literal(qc, an):
    return sorted({f"{w}: Uint literal >= 2^32" for w, kind, e, lv in an.hazard_exprs
                   for n in expr_walk(e) if isinstance(n, expr.Value) and n.type.kind is types.Uint and int(n.value) >= 1 << 32})


def _det_copy_store(qc, an):
    declared = {v.var for v in _declared_vars(qc)}
    seen_decl = set()
    out = []
    for w, kind, e, lv in an.hazard_exprs:
        if kind != "store":
            continue
        if isinstance(lv, expr.Var) and lv.var in declared and lv.var not in seen_decl:
            seen_decl.add(lv.var)               # this store is the Var declaration itself (handled by declared_var)
            continue
        if isinstance(e, expr.Var):
            out.append(f"{w}: store copies a bit/register/Var")
    return out


DETECTORS = dict(overlapping_registers=_det_overlap, declared_var=_det_declared_var_literal, register_shift_amount=_det_shift_amount,
                 dynamic_bit_index=_det_dynamic_index, uint_literal_u32=_det_u32_literal, copy_store=_det_copy_store)
# per-if detectors for the rules file's `uncertain`, `official_rules` and `info_notes` lists: f(entry, params) -> bool
def _arith_in_cone(e, p):
    return e["has_arithmetic"]


PER_IF = dict(
    wide_literals=lambda e, p: e["max_literal_width"] >= p.get("min_width", 16),
    non_if_control_flow=lambda e, p: e["kind"] != "if_else",
    var_in_cone=lambda e, p: bool(e["cone_vars"]),
    nested_conditionals=lambda e, p: e["kind"] == "if_else" and e["in_if"],          # IBM: "Nested conditionals are not allowed" = an if inside an if
    inside_loop=lambda e, p: e["in_loop"],
    operand_over_32_bits=lambda e, p: e["kind"] == "if_else" and e["operand_bits"] > 32,
    arithmetic_in_cone=_arith_in_cone,
)
CIRCUIT = dict(
    measure_in_conditional=lambda qc, an: [f"{w}: measure or reset inside the body of an if" for w in an.body_meas],
)


# ------------------------------------------------------------------ sources: every message traces to an official quote or a measured probe
def src_official(rules, keys):
    out = []
    for k in keys:
        t = rules["official_texts"][k]
        out.append(dict(kind="official", key=k, label=t.get("label", k), quote=t["quote"], url=t["url"]))
    return out


def src_measured(text, probes=(), reference=None):
    d = dict(kind="measured", text=text, probes=list(probes))
    if reference:
        d["reference"] = reference
    return d


def _prov(rules, rule=None):
    base = rules["provenance"]
    return base if rule is None else (rule.get("provenance") or base)


def analyze(qc):
    """Dataflow analysis of one circuit -> Analyzer. entries = one dict per control-flow op; e["metrics"] is the circuit's resource vector
    (resources.resource_vector, which normalises legacy tuple conditions and counts what it cannot follow as `unanalysed`; elementwise max over all ifs), the same dict for every if.
    If the vector cannot be computed, an.vector is all zeros and an.vector_error says why (the verdict is then UNCERTAIN)."""
    an = Analyzer(qc)
    an.run(qc, {c: c for c in qc.clbits}, State(), [], "", 0)
    an.vector_error = None
    try:
        an.vector = RES.resource_vector(qc)
    except Exception as ex:  # noqa: BLE001
        an.vector = {d: 0 for d in RES.DIMENSIONS}
        an.vector_error = f"{type(ex).__name__}: {str(ex)[:120]}"
    for e in an.entries:
        e["metrics"] = an.vector
    return an


def construct_signatures(qc):
    """Set of expression constructs used in stores and conditions (bodies included): store target type, Binary/Unary op with operand type,
    Cast from->to, Index target type. Literal values are not part of a signature."""
    sig = set()

    def walk_expr(e):
        for n in expr_walk(e):
            if isinstance(n, expr.Binary):
                sig.add(f"Binary:{n.op.name}:{n.left.type}")
            elif isinstance(n, expr.Unary):
                sig.add(f"Unary:{n.op.name}:{n.operand.type}")
            elif isinstance(n, expr.Cast):
                sig.add(f"Cast:{n.operand.type}->{n.type}")
            elif isinstance(n, expr.Index):
                sig.add(f"Index:{n.target.type}")

    def walk(circ):
        for inst in circ.data:
            op = inst.operation
            if op.name == "store":
                sig.add(f"store:{op.lvalue.type}")
                walk_expr(op.rvalue)
            elif isinstance(op, ControlFlowOp):
                c = getattr(op, "condition", None)
                if isinstance(c, expr.Expr):
                    walk_expr(c)
                for blk in op.blocks:
                    walk(blk)
    walk(qc)
    return sig


def _narrowing_cast_of_shift(qc):
    """stores whose right-hand side narrows a Uint with a Cast whose operand contains a shift (the construct of the rejected fix-pack v1 circuits)."""
    out = []

    def walk(circ, path):
        for idx, inst in enumerate(circ.data):
            op = inst.operation
            if op.name == "store":
                for n in expr_walk(op.rvalue):
                    if isinstance(n, expr.Cast) and n.type.kind is types.Uint and n.operand.type.kind is types.Uint and n.type.width < n.operand.type.width \
                            and any(isinstance(m, expr.Binary) and m.op in _SHIFT for m in expr_walk(n.operand)):
                        out.append(f"{path}store[{idx}]: narrowing Cast {n.operand.type}->{n.type} of a shifted register")
                        break
            elif isinstance(op, ControlFlowOp):
                for blk in op.blocks:
                    walk(blk, f"{path}{op.name}[{idx}] > ")
    walk(qc, "")
    return out


DETECTORS["narrowing_cast_of_shift"] = lambda qc, an: _narrowing_cast_of_shift(qc)


def broadcast_info(rules, n, total_bits):
    """Job-level information from IBM's per-job guidance (two quoted points, interpolated log-linearly between 1 and 60 bits)."""
    mean = total_bits / n if n else 0.0
    out = dict(n_broadcasts=n, total_bits=total_bits, mean_bits=round(mean, 2))
    if n and mean >= 1:
        import math
        bud = 2400.0 * (300.0 / 2400.0) ** (math.log(mean) / math.log(60.0))
        out.update(approx_budget=round(bud), above_guidance=bool(n > bud), extrapolated_bits=bool(mean > 60))
    return out


def _check_impl(qc, rules=None, backend=None, today=None, max_age_days=60):
    rules = rules or load_rules()
    an = analyze(qc)
    warnings = []
    age = R.age_days(rules, today)
    if age > max_age_days:
        warnings.append(f"rules file is {age} days old (limit {max_age_days}); firmware or backend changes can invalidate it. "
                        f"Re-run the suite and regenerate the rules.")
    if age < 0:
        warnings.append("rules measured_date is in the future")
    if backend and backend not in rules["backends"]:
        warnings.append(f"rules were measured on {', '.join(rules['backends'])}; target {backend} is not covered (other backends may differ)")
    emp = "measured empirically on " + ", ".join(rules["backends"]) + " " + rules["measured_date"] + ", not published by IBM"
    env = R.load_envelope(rules)
    env_res = env_src = None
    if env is not None and an.entries:
        if an.vector_error:
            env_res = dict(status=UNCERTAIN, witness=[], dims=[], reason=f"the resource vector could not be computed ({an.vector_error}); no verdict is possible")
        else:
            env_res = R.envelope_check(env, an.vector)
            bad = [e["where"] for e in an.entries
                   if sum(e["store_ops"]) > an.vector["cone_ops"] or max(e["store_ops"] + [0]) > an.vector["max_store_ops"] or e["cond_ops"] > an.vector["cond_ops"]]
            if bad and env_res["status"] != FAIL:
                env_res = dict(status=UNCERTAIN, witness=[], dims=[],
                               reason=f"the resource vector undercounts the cone that the dataflow analysis finds at {', '.join(bad[:3])} (it cannot follow this construct, "
                                      f"or a store is overwritten along the program order); no verdict is possible")
        env_src = src_measured(f"{R.witness_provenance(env, env_res['witness'])}; empirical monotone dominance envelope over {len(env['probes'])} probes, not published by IBM",
                               [w.split(":", 1)[-1] for w in env_res["witness"]])
    covered = {c["signature"]: c["evidence"] for c in rules.get("accepted_constructs", [])}
    cons_msg, cons_ev = "", []
    if covered and an.entries:
        unc = sorted(construct_signatures(qc) - set(covered))
        if unc:
            cons_msg = (f"{len(unc)} expression construct(s) never used by any accepted probe: {', '.join(unc[:6])}" + (" ..." if len(unc) > 6 else "")
                        + "; an uncovered construct can be rejected by the controller (fix-pack v1 circuits using shifts and narrowing casts were, error 6066)")
            cons_ev = sorted({p for c in covered.values() for p in c[:1]})[:6]
    ifs = []
    for e in an.entries:
        checks, notes = [], []

        def add(rule_id, st, reason, value, rule=None, error_code=None, official=(), extra_sources=(), diagnostic=False):
            checks.append(dict(rule=rule_id, status=st, reason=reason, value=value, error_code=error_code, diagnostic=diagnostic,
                               sources=src_official(rules, official) + list(extra_sources),
                               confirmed=rule["confirmed"] if rule else None,
                               pass_max=rule["pass_max"] if rule else None, fail_min=rule["fail_min"] if rule else None))
        v = e["metrics"]
        if env_res is not None:
            add("envelope", env_res["status"], env_res["reason"], {d: v[d] for d in (env["fail_dims"] if env else [])}, None,
                6057 if env_res["status"] == FAIL else None, ["err6057"], [env_src])
        if cons_msg:
            add("construct_coverage", UNCERTAIN, cons_msg, None, None, None, ["internal_error"],
                [src_measured("fix-pack v1 circuits using an uncovered construct were rejected on " + emp.split("on ")[1], ["FP_R12x8", "FP_S24"]),
                 src_measured("constructs seen in accepted probes (rules file accepted_constructs)", cons_ev)])
        for u in rules["uncertain"]:
            fn = PER_IF.get(u["detector"])
            if fn is None:
                warnings.append(f"rules file names detector {u['detector']!r} unknown to this dccheck version: skipped")
            elif fn(e, u["params"]):
                ev = [src_measured(f"{emp}" if x["reference"].startswith("data/") else "earlier-day observation, not published by IBM", [x["probe"]], x["reference"])
                      for x in u["evidence"]]
                add(u["id"], UNCERTAIN, u["description"], None, None, None, u.get("official", []), ev)
        for o in rules["official_rules"]:
            fn = PER_IF.get(o["detector"])
            if fn is not None and fn(e, {}):
                add(o["id"], FAIL, f"{o['description']} ({e['where']})", None, None, None, o["official"])
        for n in rules.get("info_notes", []):
            fn = PER_IF.get(n["detector"])
            if fn is not None and fn(e, {}):
                notes.append(dict(id=n["id"], text=n["text"], sources=src_official(rules, n["official"]) +
                                  ([src_measured(emp, n["measured_probes"])] if n.get("measured_probes") else [])))
        lat = R.latency_estimate(rules["latency"], e["latency_ops"])
        lat["sources"] = [src_measured(f"{rules['latency']['provenance']}; not published by IBM", [p["probe"] for p in rules["latency"]["points"]])]
        lat["excludes"] = rules["latency"].get("excludes", "")
        ifs.append(dict(e, checks=checks, notes=notes, status=R.worst(c["status"] for c in checks), latency=lat))
    hazards = []
    for h in rules.get("static_hazards", []):
        fn = DETECTORS.get(h["detector"])
        if fn is None:
            warnings.append(f"rules file names detector {h['detector']!r} unknown to this dccheck version: skipped")
            continue
        for msg in fn(qc, an):
            if h.get("evidence"):
                srcs = [src_measured(f"observed on {h.get('observed', rules['backends'][0])}; not published by IBM", h["evidence"])]
            else:
                srcs = [dict(kind="measured_earlier", text="earlier-day rejection, not re-measured in data/", reference=h["reference"], probes=[])]
            hazards.append(dict(rule=h["id"], status=h["verdict"], error_code=h.get("error_code"), message=msg, description=h.get("description", ""),
                                sources=src_official(rules, h.get("official", [])) + srcs))
    for o in rules["official_rules"]:
        fn = CIRCUIT.get(o["detector"])
        if fn is not None:
            for msg in fn(qc, an):
                hazards.append(dict(rule=o["id"], status=o["verdict"], error_code=None, message=msg, description=o["description"],
                                    sources=src_official(rules, o["official"])))
    if an.unmeasured_reads:
        warnings.append(f"{len(an.unmeasured_reads)} classical bit(s) are read by an expression but never measured or stored before "
                        f"(not counted as measured registers)")
    bc = broadcast_info(rules, len(an.broadcasts), sum(an.broadcasts.values()))
    bnote = next((n for n in rules.get("info_notes", []) if n["detector"] == "broadcast_budget"), None)
    bc["sources"] = src_official(rules, bnote["official"]) if bnote else []
    status = R.worst([i["status"] for i in ifs] + [h["status"] for h in hazards])
    if not ifs and not hazards:
        status = NOT_APPLICABLE
    return dict(circuit=qc.name, envelope=(dict(status=env_res["status"], reason=env_res["reason"], witness=env_res["witness"]) if env_res else None),
                vector=an.vector, n_qubits=qc.num_qubits, n_clbits=qc.num_clbits, n_registers=len(qc.cregs),
                n_vars=len(_declared_vars(qc)), n_control_flow=len(ifs),
                rules=dict(id=rules["rules_id"], file=rules.get("_path"), backends=rules["backends"],
                           measured_date=rules["measured_date"], age_days=age, provenance=rules["provenance"],
                           known_unmodeled=rules.get("known_unmodeled", [])),
                status=status, ifs=ifs, hazards=hazards, warnings=warnings, broadcast=bc,
                note=("NOT APPLICABLE: no if_else / while / for / switch, so the circuit has no classical feedforward and the limits measured here (which concern "
                      "classical decisions) do not apply; no QPU probe of this suite had no control flow, so this is not a measured PASS" if not ifs else ""))


def check_job(circuits, rules=None, **kw):
    """Several circuits submitted in one job: per-circuit preflight reports plus the job-level broadcast information (from the logical circuits)."""
    rules = rules or load_rules()
    reps = [preflight(c, rules, **kw) for c in circuits]
    n = sum(r["logical"]["broadcast"]["n_broadcasts"] for r in reps)
    tb = sum(r["logical"]["broadcast"]["total_bits"] for r in reps)
    bc = broadcast_info(rules, n, tb)
    bnote = next((x for x in rules.get("info_notes", []) if x["detector"] == "broadcast_budget"), None)
    bc["sources"] = src_official(rules, bnote["official"]) if bnote else []
    return dict(circuits=reps, broadcast=bc, status=R.worst(r["status"] for r in reps))


def check_pair(logical, transpiled, rules=None, **kw):
    """Check a logical and its transpiled circuit; flags verdict changes made by the transpiler."""
    rules = rules or load_rules()
    a, b = check_logical(logical, rules, **kw), check_logical(transpiled, rules, **kw)
    diff = []
    if len(a["ifs"]) != len(b["ifs"]):
        diff.append(f"transpilation changed the number of control-flow ops: {len(a['ifs'])} -> {len(b['ifs'])}")
    for i, (x, y) in enumerate(zip(a["ifs"], b["ifs"])):
        if x["status"] != y["status"] or x["metrics"] != y["metrics"]:
            diff.append(f"control-flow op #{i}: logical {x['status']} {x['metrics']} -> transpiled {y['status']} {y['metrics']}")
    return dict(logical=a, transpiled=b, differences=diff, status=R.worst([a["status"], b["status"]]))


def _run_big(fn, *a, **kw):
    """run fn on a thread with a large stack so that very deep expression chains do not overflow the C stack"""
    import threading
    box = {}

    def target():
        try:
            box["r"] = fn(*a, **kw)
        except BaseException as ex:  # noqa: BLE001
            box["e"] = ex
    old = threading.stack_size()
    try:
        threading.stack_size(512 * 1024 * 1024)
    except (ValueError, RuntimeError):
        return fn(*a, **kw)
    try:
        t = threading.Thread(target=target)
        t.start()
        t.join()
    finally:
        threading.stack_size(old)
    if "e" in box:
        raise box["e"]
    return box["r"]


def error_report(qc, ex, rules):
    """An analysis failure is never a verdict: status ERROR with the reason (CLI exit code 3)."""
    return dict(circuit=getattr(qc, "name", "?"), status=ERROR, error=f"{type(ex).__name__}: {str(ex)[:200]}", ifs=[], hazards=[], warnings=[],
                n_qubits=getattr(qc, "num_qubits", 0), n_clbits=getattr(qc, "num_clbits", 0), n_registers=0, n_vars=0, n_control_flow=0,
                rules=dict(id=rules["rules_id"], file=rules.get("_path"), backends=rules["backends"], measured_date=rules["measured_date"], age_days=0,
                           provenance=rules["provenance"], known_unmodeled=rules.get("known_unmodeled", [])),
                broadcast=dict(n_broadcasts=0, total_bits=0, mean_bits=0.0, sources=[]), vector=None, envelope=None, note="")


def check_logical(qc, rules=None, backend=None, today=None, max_age_days=60):
    """Analyse ONE circuit exactly as passed, WITHOUT transpiling. Its "status" is the status of this circuit form only and is NOT the verdict for what
    runs: use check() or preflight(). An internal failure gives status ERROR with the reason, never a misleading verdict."""
    rules = rules or load_rules()
    try:
        rep = _run_big(_check_impl, qc, rules, backend, today, max_age_days)
    except Exception as ex:  # noqa: BLE001
        rep = error_report(qc, ex, rules)
    rep["scope"] = "as passed only: NOT the verdict for the circuit that runs (use check() or preflight())"
    return rep


def check(qc, rules=None, backend=None, today=None, max_age_days=60, transpile=True):
    """THE verdict, identical to the CLI default: the circuit is checked as passed AND transpiled against a bundled fake Heron r2 (see isa_circuit), and
    rep["status"] is the worst of the two. The report is the as-passed analysis (rep["ifs"], rep["vector"], ...) plus rep["status_logical"],
    rep["transpiled"] (the same analysis of the transpiled circuit), rep["differences"], rep["isa"] and rep["preflight"] (the full preflight dict,
    which format_report prints). Use check_logical() for the as-passed analysis alone."""
    pf = preflight(qc, rules, backend=backend, today=today, max_age_days=max_age_days, transpile=transpile)
    rep = dict(pf["logical"])
    rep.update(status=pf["status"], status_logical=pf["logical"]["status"], transpiled=pf["transpiled"], differences=pf["differences"], isa=pf["isa"],
               preflight=pf)
    return rep


_FAKES = {"ibm_marrakesh": "FakeMarrakesh", "ibm_kingston": "FakeKingston", "ibm_fez": "FakeFez", "ibm_aachen": "FakeAachen", "ibm_torino": "FakeTorino",
          "ibm_pittsburgh": "FakePittsburgh"}
DEFAULT_FAKE = "FakeMarrakesh"
VEC_DIMS_COMPARED = ("peak_live_regs", "lr_stack", "cone_ops", "max_store_ops", "cone_regs", "unary_ops")


def _target(backend):
    import stopwatch_core as SW
    from qiskit_ibm_runtime import fake_provider as FP
    name = _FAKES.get(backend or "", DEFAULT_FAKE)
    be = getattr(FP, name)()
    SW.target_with_measure2(be, 1.884e-6)
    return name, be


def isa_problem(qc, backend=None):
    """Why `qc` is NOT an ISA circuit for the target (None if it is): it must carry a transpiler layout, have the width of the target, and use only
    operations the target supports (control-flow bodies included)."""
    try:
        name, be = _target(backend)
    except Exception as ex:  # noqa: BLE001
        return f"target unavailable ({type(ex).__name__})"
    if getattr(qc, "layout", None) is None:
        return "no transpiler layout (the circuit has not been transpiled)"
    if qc.num_qubits != be.num_qubits:
        return f"{qc.num_qubits} qubits, the target has {be.num_qubits}"
    ok = set(be.target.operation_names) | {"barrier", "delay", "store", "if_else", "while_loop", "for_loop", "switch_case", "measure", "reset"}
    bad = set()

    def walk(c):
        for i in c.data:
            if i.operation.name not in ok:
                bad.add(i.operation.name)
            if isinstance(i.operation, ControlFlowOp):
                for b in i.operation.blocks:
                    walk(b)
    walk(qc)
    return f"operations outside the target basis: {sorted(bad)[:6]}" if bad else None


def program_fingerprint(qc):
    """What a transpiler must preserve: number of measurements, stores, if_else ops, multiset of classical expression ops, clbits and registers."""
    from collections import Counter
    c = Counter()

    def walk_expr(e):
        for n in expr_walk(e):
            if isinstance(n, expr.Binary):
                c[f"Binary:{n.op.name}"] += 1
            elif isinstance(n, expr.Unary):
                c[f"Unary:{n.op.name}"] += 1
            elif isinstance(n, expr.Index):
                c["Index"] += 1
            elif isinstance(n, expr.Cast):
                c["Cast"] += 1

    def walk(circ):
        for inst in circ.data:
            op = inst.operation
            if op.name.startswith("measure"):
                c["#measure"] += len(inst.clbits)
            elif op.name == "store":
                c["#store"] += 1
                walk_expr(op.rvalue)
            elif isinstance(op, ControlFlowOp):
                if op.name == "if_else":
                    c["#if_else"] += 1
                cond = getattr(op, "condition", None)
                if isinstance(cond, expr.Expr):
                    walk_expr(cond)
                for blk in op.blocks:
                    walk(blk)
    walk(qc)
    c["#clbits"] = qc.num_clbits
    c["#cregs"] = len(qc.cregs)
    return c


def correspondence_problem(logical, isa):
    """None if `isa` can be the transpilation of `logical` (same measurement / store / if_else counts, expression op multiset, clbit and register counts)."""
    a, b = program_fingerprint(logical), program_fingerprint(isa)
    if a == b:
        return None
    keys = sorted(set(a) | set(b))
    diff = [f"{k}: {a.get(k, 0)} vs {b.get(k, 0)}" for k in keys if a.get(k, 0) != b.get(k, 0)]
    return "does not correspond to the logical circuit (" + "; ".join(diff[:5]) + ")"


def isa_circuit(qc, backend=None):
    """The circuit that actually runs: transpile `qc` against a bundled fake Heron r2 backend (the one named by `backend` if bundled, else FakeMarrakesh)
    with measure_2 added, optimisation level 1, seed 7 (the suite's settings). A circuit that already carries a transpiler layout is taken as ISA.
    Returns (circuit | None, info dict)."""
    import stopwatch_core as SW
    from qiskit import transpile
    from qiskit_ibm_runtime import fake_provider as FP
    info = dict(already_isa=False, target=None, error=None)
    if isa_problem(qc, backend) is None:
        info.update(already_isa=True, target="as passed (the circuit is already ISA for the bundled target: layout, width and basis checked)")
        return qc, info
    name = _FAKES.get(backend or "", DEFAULT_FAKE)
    try:
        be = getattr(FP, name)()
        SW.target_with_measure2(be, 1.884e-6)
        info["target"] = f"{name} (bundled fake, +measure_2), optimization_level=1, seed_transpiler=7" + \
            ("" if backend in (None, "") or name != DEFAULT_FAKE or backend == "ibm_marrakesh" else f"; {backend} has no bundled fake, {DEFAULT_FAKE} used")
        if qc.num_qubits > be.num_qubits:
            raise RuntimeError(f"circuit has {qc.num_qubits} qubits, the target {be.num_qubits}")
        return transpile(qc, be, optimization_level=1, seed_transpiler=7), info
    except Exception as ex:  # noqa: BLE001
        info["error"] = f"{type(ex).__name__}: {str(ex)[:160]}"
        return None, info


def preflight(qc, rules=None, backend=None, today=None, max_age_days=60, transpiled=None, transpile=True):
    """The verdict for the circuit that actually runs: the WORST of the circuit as passed, its OWN transpilation (always made here, against a bundled fake
    backend, see isa_circuit) and, if given, the `transpiled` circuit you supply. A supplied circuit can only make the verdict stricter, never more lenient.
    If the own transpilation is impossible (circuit too wide for the bundled target, ...) the verdict is ALWAYS capped at UNCERTAIN, whatever circuit you supply;
    a supplied circuit then only adds a note (whether it corresponds in counts, and whether dcfix.classical_equivalent found the same classical decisions on
    random outcomes - a sampling check, NOT a proof). transpile=False checks the circuit as passed only."""
    rules = rules or load_rules()
    kw = dict(backend=backend, today=today, max_age_days=max_age_days)
    logical = check_logical(qc, rules, **kw)
    out = dict(kind="preflight", logical=logical, transpiled=None, differences=[], isa=None, notes=[], supplied=None)
    if logical["status"] in (NOT_APPLICABLE, ERROR) and transpiled is None:
        out.update(status=logical["status"], notes=["no classical feedforward found (or an internal error): nothing to transpile for the check"] if logical["status"] == NOT_APPLICABLE else [])
        return out
    if not transpile and transpiled is None:
        out.update(status=logical["status"], notes=["checked as passed: the verdict is valid only if this is the circuit that runs (a transpiler can hoist measurements and change "
                                                    "register liveness); pass the transpiled circuit or omit --no-transpile"])
        return out
    statuses = [logical["status"]]
    t, info = (None, dict(already_isa=False, target=None, error="transpile disabled")) if not transpile else isa_circuit(qc, backend)
    out["isa"] = info
    if t is not None:
        tr = logical if t is qc else check_logical(t, rules, **kw)
        out["transpiled"] = tr
        statuses.append(tr["status"])
        if logical["vector"] and tr["vector"]:
            diff = [f"{d} {logical['vector'][d]} -> {tr['vector'][d]}" for d in VEC_DIMS_COMPARED if logical["vector"][d] != tr["vector"][d]]
            if diff:
                out["differences"].append("transpilation changes the resource vector: " + ", ".join(diff))
        if logical["status"] != tr["status"]:
            out["differences"].append(f"verdict changes under transpilation: logical {logical['status']} -> transpiled {tr['status']}")
    elif transpile:
        statuses.append(UNCERTAIN)
        out["notes"].append(f"could not transpile for the check ({info['error']}): the verdict is capped at UNCERTAIN")
    if transpiled is not None:
        sup = check_logical(transpiled, rules, **kw)
        out["supplied"] = sup
        statuses.append(sup["status"])                                   # a supplied circuit is only ever an additional constraint
        corr = correspondence_problem(qc, transpiled)
        isa_why = isa_problem(transpiled, backend)
        if isa_why:
            out["notes"].append(f"the supplied circuit is not ISA for the bundled target ({isa_why}); it was still checked, and can only make the verdict stricter")
        if corr:
            out["notes"].append(f"the supplied circuit {corr}; it was still checked, and can only make the verdict stricter")
        elif not isa_why:
            out["notes"].append("the supplied circuit corresponds to the logical circuit (same measurement, store, if_else and expression-op counts); it was checked in addition to the own transpilation")
        if t is None and transpile:                                       # own transpilation impossible: the UNCERTAIN cap above stays; only a note is added
            import dcfix
            sampled = corr is None and dcfix.classical_equivalent(qc, transpiled)
            out["notes"].append("the own transpilation is impossible, so the verdict stays capped at UNCERTAIN whatever circuit is supplied; the supplied circuit "
                                + ("corresponds in counts and made the same classical decisions on all sampled random outcomes (a sampling check, not a proof: programs that "
                                   "differ on a rare pattern pass it)" if sampled else "was not found to correspond (counts or sampled classical decisions differ)"))
    out["status"] = R.worst(statuses)
    return out


# ------------------------------------------------------------------ text report
def _regs_txt(regs, n=8):
    return ", ".join(regs) if len(regs) <= n else ", ".join(regs[:n]) + f", ... (+{len(regs) - n})"


def _fmt_sources(srcs, ind="      "):
    L = []
    for s in srcs:
        if s["kind"] == "official":
            L.append(f'{ind}IBM ({s["label"]}, {s["url"]}): "{s["quote"]}"')
        elif s["kind"] == "measured":
            pr = f" probes: {', '.join(s['probes'])}" if s["probes"] else ""
            ref = f" [{s['reference']}]" if s.get("reference") else ""
            L.append(f"{ind}Ours: {s['text']}.{pr}{ref}")
        else:
            L.append(f"{ind}Ours: {s['text']} [{s['reference']}]")
    return L


MONOTONICITY = ("PASS means: no larger, in every measured resource dimension, than a circuit that an accepted QPU probe ran, ASSUMING monotone resource demand "
                "(needing no more of every resource than an accepted circuit cannot get a circuit rejected). The assumption is consistent with the stored probes but unproven; "
                "one backend (ibm_marrakesh), one day; UNCERTAIN is common by design.")


def format_report(rep, header=True):
    if "preflight" in rep:
        return format_report(rep["preflight"], header)
    if rep.get("kind") == "preflight":
        L = []
        lg, tr = rep["logical"], rep["transpiled"]
        L.append(f"=== PREFLIGHT {lg['circuit']!r}: OVERALL {rep['status'].replace('_', ' ')} (worst of the circuit as passed and as transpiled) ===")
        L.append("=== as passed ===")
        L.append(format_report(lg))
        if tr is not None and tr is not lg:
            L.append(f"=== as transpiled (ISA), the circuit that actually runs: {rep['isa']['target']} ===")
            L.append(format_report(tr, header=False))
        elif rep["isa"] and rep["isa"].get("already_isa"):
            L.append(f"(the circuit was taken as ISA: {rep['isa']['target']})")
        if rep.get("supplied") is not None:
            L.append("=== as supplied by you (--transpiled): checked in addition, can only make the verdict stricter ===")
            L.append(format_report(rep["supplied"], header=False))
        for d in rep["differences"]:
            L.append(f"DIFFERENCE: {d}")
        for n in rep["notes"]:
            L.append(f"NOTE: {n}")
        L.append(f"OVERALL: {rep['status'].replace('_', ' ')}")
        if rep["status"] == PASS or (lg["status"] == PASS and tr is not None and tr["status"] == PASS):
            L.append(MONOTONICITY)
        return "\n".join(L)
    if rep["status"] == ERROR:
        return f"circuit {rep['circuit']!r}: ERROR (internal error, no verdict): {rep['error']}"
    if "logical" in rep:
        out = ["=== logical circuit ===", format_report(rep["logical"]), "=== transpiled circuit ===", format_report(rep["transpiled"])]
        out.append("transpiler vs logical: " + ("; ".join(rep["differences"]) if rep["differences"] else "no change in verdicts or metrics"))
        return "\n".join(out)
    if "circuits" in rep:
        out = [format_report(r) for r in rep["circuits"]]
        out.append(_fmt_broadcast(rep["broadcast"], "job"))
        return "\n\n".join(out)
    r = rep["rules"]
    L = [f"circuit {rep['circuit']!r}: {rep['n_qubits']} qubits, {rep['n_clbits']} clbits in {rep['n_registers']} registers, "
         f"{rep['n_control_flow']} control-flow op(s)  ->  {rep['status'].replace('_', ' ')}",
         f"rules: {r['id']} ({', '.join(r['backends'])}, measured {r['measured_date']}, {r['age_days']} days old). Thresholds marked 'Ours' are "
         f"measured empirically, not published by IBM."]
    for w in rep["warnings"]:
        L.append(f"WARNING: {w}")
    for u in (r.get("known_unmodeled", []) if header else []):
        L.append(f"not modelled: {u}")
    if rep["note"]:
        L.append(f"note: {rep['note']}")
    for h in rep["hazards"]:
        L.append(f"{h['status']:9s} {h['rule']}: {h['message']}" + (f" (error {h['error_code']})" if h["error_code"] else ""))
        L += _fmt_sources(h["sources"], "          ")
    for i in rep["ifs"]:
        L.append("")
        L.append(f"{i['kind']} at {i['where']}   {i['status']}   cond: {i['condition']}")
        m = i["metrics"]
        L.append(f"  resources: peak live registers {m['peak_live_regs']}; left-to-right stack need {m['lr_stack']} (Ershov {m['ershov']}); "
                 f"{m['cone_regs']} measured register(s) in the cone [{_regs_txt(i['cone_registers'])}]; "
                 f"{m['stored_count']} store(s) [{_regs_txt(i['stored_targets'])}]"
                 + (f"; Vars {i['cone_vars']}" if i["cone_vars"] else "") +
                 f"; ops: largest store {m['max_store_ops']}, whole cone {m['cone_ops']} (depth {m['depth']}, widest literal {m['max_literal_bits']} bits)")
        for c in i["checks"]:
            if c["status"] == PASS:
                L.append(f"  - PASS      {c['rule']}: {c['reason']}")
                continue
            tail = f" (error {c['error_code']})" if c["error_code"] else ""
            L.append(f"  - {c['status']:9s} {c['rule']}: {c['reason']}{tail}")
            L += _fmt_sources(c["sources"])
        for n in i["notes"]:
            L.append(f"  - NOTE      {n['id']}: {n['text']}")
            L += _fmt_sources(n["sources"])
        lat = i["latency"]
        L.append(f"  latency: central ~{lat['estimate_ns']:.0f} ns after measurement end, WIDE {lat['lo_ns']:.0f} to {lat['hi_ns']:.0f} ns for {lat['ops']} ops "
                 f"({lat['level']})" + ("; EXTRAPOLATION outside the 15-63 op range actually measured" if lat["extrapolated"] else "")
                 + f". About 25-30 ns/op, intercept unresolved; {lat['excludes']}")
        L += _fmt_sources(lat["sources"], "      ")
    L.append(_fmt_broadcast(rep["broadcast"], "circuit"))
    return "\n".join(L)


def _fmt_broadcast(b, scope):
    if not b["n_broadcasts"]:
        return f"broadcasts ({scope}): none"
    t = (f"broadcasts ({scope}): {b['n_broadcasts']} unique, {b['total_bits']} bits (mean {b['mean_bits']} bits)")
    if "approx_budget" in b:
        t += (f"; IBM's approximate per-job guidance at that size is about {b['approx_budget']} (interpolated between two quoted points"
              + ("; mean above 60 bits is extrapolated" if b["extrapolated_bits"] else "") + ")"
              + ("; ABOVE guidance (informational, not a verdict)" if b["above_guidance"] else "; within guidance (informational)"))
    for s in b.get("sources", [])[:1]:
        t += f'\n      IBM ({s["label"]}, {s["url"]}): "{s["quote"]}"'
    return t


# ------------------------------------------------------------------ loading
def load_circuits(spec):
    """file.qpy | file.py:func | module:func | module.func -> list[QuantumCircuit]"""
    if spec.endswith(".qpy") or (os.path.isfile(spec) and not spec.endswith(".py")):
        with open(spec, "rb") as f:
            return list(qpy.load(f))
    if ":" in spec:
        mod, fn = spec.split(":", 1)
    else:
        mod, fn = spec.rsplit(".", 1)
    if mod.endswith(".py") or os.path.isfile(mod):
        sp = importlib.util.spec_from_file_location("dccheck_user_module", mod)
        m = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m)
    else:
        sys.path.insert(0, os.getcwd())
        m = importlib.import_module(mod)
    out = getattr(m, fn)()
    return list(out) if isinstance(out, (list, tuple)) else [out]


def fix_pack(qc, rules=None, **kw):
    """see dcfix.fix_pack"""
    import dcfix
    return dcfix.fix_pack(qc, rules, **kw)


EXIT = {PASS: 0, FAIL: 1, UNCERTAIN: 2, ERROR: 3, NOT_APPLICABLE: 4}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("circuit", help="file.qpy | file.py:func | module:func")
    ap.add_argument("--transpiled", help="your own transpiled (ISA) version of the same circuit (qpy or import path); it is checked IN ADDITION to the circuit's own transpilation here and can only make the verdict stricter")
    ap.add_argument("--no-transpile", action="store_true", help="check the circuit exactly as passed (valid only if it is the ISA circuit)")
    ap.add_argument("--rules", help="rules file (default: newest in rules/)")
    ap.add_argument("--backend", help="target backend name: picks the bundled fake for the check and warns if the rules do not cover it")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-age-days", type=int, default=60)
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for reproducible output")
    ap.add_argument("--fix-pack", action="store_true", help="repack many small measured registers into few wide ones, bit-level only (writes -o)")
    ap.add_argument("-o", "--out", help="with --fix-pack: output .qpy path")
    a = ap.parse_args(argv)
    try:
        rules = load_rules(a.rules)
        circs = load_circuits(a.circuit)
        tcircs = load_circuits(a.transpiled) if a.transpiled else None
    except Exception as ex:  # noqa: BLE001
        print(f"dccheck: {type(ex).__name__}: {ex}", file=sys.stderr)
        return 64
    today = _dt.date.fromisoformat(a.today) if a.today else None
    kw = dict(backend=a.backend, today=today, max_age_days=a.max_age_days)
    worst_status = NOT_APPLICABLE
    reps = []
    if len(circs) > 1 and not a.fix_pack and not tcircs:
        job = check_job(circs, rules, transpile=not a.no_transpile, **kw)
        print(json.dumps(job, indent=1) if a.json else format_report(job))
        return EXIT[job["status"]]
    for i, qc in enumerate(circs):
        if a.fix_pack:
            from dcfix import fix_pack, format_fix
            try:
                res = _run_big(fix_pack, qc, rules, **kw)
            except Exception as ex:  # noqa: BLE001
                print(f"dccheck: fix-pack internal error {type(ex).__name__}: {str(ex)[:200]}", file=sys.stderr)
                return EXIT[ERROR]
            reps.append(dict(fix=res.to_dict()))
            print(json.dumps(reps[-1], indent=1) if a.json else format_fix(res))
            if res.circuit is not None and a.out:
                path = a.out if len(circs) == 1 else f"{os.path.splitext(a.out)[0]}_{i}.qpy"
                with open(path, "wb") as f:
                    qpy.dump(res.circuit, f)
                print(f"wrote {path}", file=sys.stderr)
            st = (res.after["status"] if res.after else res.before["status"])
            worst_status = R.worst([worst_status, FAIL if res.refused else st])
            continue
        rep = preflight(qc, rules, transpiled=(tcircs[i] if tcircs else None), transpile=not a.no_transpile, **kw)
        reps.append(rep)
        worst_status = R.worst([worst_status, rep["status"]])
        if not a.json:
            print(format_report(rep))
    if a.json and not a.fix_pack:
        print(json.dumps(reps if len(reps) != 1 else reps[0], indent=1))
    return EXIT[worst_status]


if __name__ == "__main__":
    sys.exit(main())
