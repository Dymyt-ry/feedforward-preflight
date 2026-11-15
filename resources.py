# SPDX-License-Identifier: Apache-2.0
"""Resource vector of a dynamic circuit, counted on the circuit that is passed in (pass the TRANSPILED circuit).

One implementation shared by the conformance suite and the pre-flight checker. Every dimension is a non-negative number where MORE means MORE
of a resource that could plausibly matter for job acceptance (error 6057 "more classical registers than the control hardware provides");
the envelope verdict (resources.envelope_verdict) assumes monotonicity: a circuit that uses no more of EVERY dimension than an accepted circuit is
predicted to be accepted; a circuit that uses at least as much (in the failure-relevant dimensions) as a rejected one is predicted to be rejected.

Definitions (see DIMENSIONS for one-line texts):
  register          a ClassicalRegister (or standalone Var); a stored/measured Clbit counts as its register unless it is the target of a bit-level store (then it is a "bit variable").
  measured          target of a mid-circuit measure / measure_2.
  cone (of an if)   everything reachable from the if condition by following stores backwards; vectors take the elementwise MAX over all ifs (nested ifs included).
  live              a register is live from its measurement / first store until the last expression that reads it; peak = maximum number simultaneously live over the whole circuit.
  op                Binary, Unary or Index node of an expression tree; casts and literals count 0; shared sub-trees count at every use (expanded count).
  stack need        registers needed to evaluate a tree left-to-right while holding the left operand (Binary: max(left, 1+right); leaf = 1).
  Ershov number     registers needed by the best evaluation order (Sethi-Ullman).
"""
from __future__ import annotations

import sys

from qiskit.circuit import ClassicalRegister, Clbit
from qiskit.circuit.classical import expr, types

DIMENSIONS = {
    "peak_live_regs": "registers (measured + register-valued stores) simultaneously live, maximum over the circuit",
    "peak_live_bitvars": "bit-level stored variables simultaneously live, maximum over the circuit",
    "cone_regs": "distinct measured registers in the cone",
    "total_reads": "sum over stores in the cone of the distinct registers each reads (plus the if condition)",
    "stored_count": "stores in the cone",
    "stored_bits": "total width in bits of the store targets in the cone",
    "max_store_ops": "ops of the largest single stored expression in the cone (expanded)",
    "cond_ops": "ops of the largest if-condition expression (expanded)",
    "cone_ops": "total ops over the stores in the cone (expanded)",
    "depth": "longest root-to-leaf chain of ops in a stored expression",
    "ershov": "Sethi-Ullman register need of the largest stored expression",
    "lr_stack": "left-to-right stack need of the largest stored expression",
    "distinct_leaf_bits": "distinct measured bits / registers read by the cone's expressions",
    "leaf_refs": "total leaf references in the cone's expressions",
    "unique_nodes": "hash-consed (distinct) op nodes in the cone",
    "unary_ops": "NOT operations in the cone",
    "literal_count": "literal operands in the cone",
    "max_literal_bits": "largest literal width in bits",
    "n_ifs": "if_else operations (nested included)",
    "has_shift": "1 if a shift appears in a stored expression or condition",
    "has_cast": "1 if a cast appears",
    "has_compare": "1 if a comparison (==, <, >, ...) appears",
    "has_add": "1 if an add or subtract appears",
    "has_mul": "1 if a multiply appears",
    "has_index": "1 if a bit index appears",
    "unanalysed": "count of conditions, stores or control-flow constructs the analysis could NOT fully follow (unknown condition form, unsupported lvalue, loops/switch/box, analysis error); any value > 0 can never be dominated by an accepted probe, so the verdict is never PASS",
}
FLAG_DIMS = ("has_shift", "has_cast", "has_compare", "has_add", "has_mul", "has_index")
_OPS = expr.Binary.Op


def _walk_children(e):
    for a in ("left", "right", "operand", "target", "index"):
        if hasattr(e, a):
            yield getattr(e, a)


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


def ershov(e):
    if isinstance(e, expr.Binary):
        a, b = ershov(e.left), ershov(e.right)
        return a + 1 if a == b else max(a, b)
    if isinstance(e, expr.Unary):
        return ershov(e.operand)
    if isinstance(e, expr.Index):
        return max(ershov(e.target), ershov(e.index))
    if isinstance(e, expr.Cast):
        return ershov(e.operand)
    return 1


def lr_need(e):
    if isinstance(e, expr.Binary):
        return max(lr_need(e.left), 1 + lr_need(e.right))
    if isinstance(e, expr.Unary):
        return lr_need(e.operand)
    if isinstance(e, expr.Index):
        return max(lr_need(e.target), 1 + lr_need(e.index))
    if isinstance(e, expr.Cast):
        return lr_need(e.operand)
    return 1


def _canon(e, seen):
    if isinstance(e, expr.Var):
        return ("v", repr(e.var))
    if isinstance(e, expr.Value):
        return ("c", str(e.value))
    if isinstance(e, expr.Cast):
        return _canon(e.operand, seen)
    if isinstance(e, expr.Unary):
        r = ("u", str(e.op), _canon(e.operand, seen))
    elif isinstance(e, expr.Binary):
        r = ("b", str(e.op), _canon(e.left, seen), _canon(e.right, seen))
    elif isinstance(e, expr.Index):
        r = ("i", _canon(e.target, seen), _canon(e.index, seen))
    else:
        return ("?", repr(e))
    seen.add(r)
    return r


def _vname(v, node):
    n = getattr(v, "name", None)
    return n if n is not None else (getattr(node, "name", None) or str(v))


_UNSUPPORTED_FLOW = ("while_loop", "for_loop", "switch_case", "box")


def _flatten(qc):
    """time-ordered instruction list including the bodies of nested if/else blocks."""
    out = []

    def go(circ):
        for ins in circ.data:
            out.append(ins)
            if ins.operation.name == "if_else":
                for blk in ins.operation.blocks:
                    go(blk)
    go(qc)
    return out


def normalise_condition(cond):
    """Every form of an if condition as an `expr` tree, or None if it cannot be understood.
    expr trees / Vars pass through; the legacy tuple (Clbit | ClassicalRegister, int) becomes bit / not bit / register == value."""
    if isinstance(cond, expr.Expr):
        return cond
    if isinstance(cond, (tuple, list)) and len(cond) == 2:
        target, val = cond
        if isinstance(target, Clbit):
            e = expr.lift(target)
            return e if val else expr.logic_not(e)
        if isinstance(target, ClassicalRegister):
            return expr.equal(expr.lift(target), expr.lift(int(val), types.Uint(len(target))))
    return None


def _store_key(qc, lv):
    """(key, width) of a store target in every supported lvalue form, or None."""
    if isinstance(lv, expr.Var):
        v = lv.var
        if isinstance(v, Clbit):
            return ("bit", qc.find_bit(v).index), 1
        nm = _vname(v, lv)
        return ("reg", nm), (getattr(getattr(lv, "type", None), "width", 1) or 1)
    if isinstance(lv, expr.Index) and isinstance(lv.target, expr.Var) and isinstance(lv.index, expr.Value):
        base = lv.target.var
        if isinstance(base, ClassicalRegister):
            return ("bit", qc.find_bit(base[int(lv.index.value)]).index), 1
    return None


def resource_vector(qc):
    """dict {dimension: number} for the circuit `qc` (see module docstring)."""
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 50000))
    try:
        return _resource_vector(qc)
    finally:
        sys.setrecursionlimit(old_limit)


def _resource_vector(qc):
    instrs = _flatten(qc)
    unanalysed = sum(1 for i in instrs if i.operation.name in _UNSUPPORTED_FLOW)
    measured, stores, reg_targets, widths, ifs = {}, {}, set(), {}, []
    for t, ins in enumerate(instrs):
        op = ins.operation
        if op.name in ("measure_2", "measure"):
            for cb in ins.clbits:
                try:
                    for reg, _ in qc.find_bit(cb).registers:
                        measured.setdefault(reg.name, t)
                except Exception:  # noqa: BLE001
                    pass
        elif op.name == "store":
            sk = _store_key(qc, op.lvalue)
            if sk is None:
                unanalysed += 1
                continue
            k, wdt = sk
            if k[0] == "reg":
                reg_targets.add(k[1])
            widths[k] = wdt
            stores[k] = (op.rvalue, t)
        elif op.name == "if_else":
            ce = normalise_condition(op.condition)
            if ce is None:
                unanalysed += 1
            else:
                ifs.append((ce, t))

    reg_bit_stores = {}
    for reg in qc.cregs:
        ks = [("bit", qc.find_bit(b).index) for b in reg if ("bit", qc.find_bit(b).index) in stores]
        if ks:
            reg_bit_stores[reg.name] = ks

    def keys_of(e, acc):
        if isinstance(e, expr.Var):
            v = e.var
            if isinstance(v, Clbit):
                idx = qc.find_bit(v).index
                regs = qc.find_bit(v).registers
                if ("bit", idx) in stores:
                    acc.append(("bit", idx))
                elif regs:
                    acc.append(("reg", regs[0][0].name))
                else:
                    acc.append(("bit", idx))
            else:
                nm = _vname(v, e)
                if ("reg", nm) not in stores and nm in reg_bit_stores:
                    acc.extend(reg_bit_stores[nm])         # a whole-register read of a register written bit by bit: follow every bit store
                else:
                    acc.append(("reg", nm))
        for c in _walk_children(e):
            keys_of(c, acc)
        return acc

    # liveness over the whole circuit
    first, last = {}, {}
    for nm, t in measured.items():
        first[("reg", nm)] = t
    for t, ins in enumerate(instrs):
        op = ins.operation
        if op.name == "store":
            for k in keys_of(op.rvalue, []):
                last[k] = t
            sk = _store_key(qc, op.lvalue)
            if sk is not None:
                first.setdefault(sk[0], t)
        elif op.name == "if_else":
            ce = normalise_condition(op.condition)
            for k in (keys_of(ce, []) if ce is not None else []):
                last[k] = t
    peak_r = peak_b = 0
    keys = list(first)
    for t in range(len(instrs)):
        peak_r = max(peak_r, sum(1 for k in keys if k[0] == "reg" and first[k] <= t <= last.get(k, first[k])))
        peak_b = max(peak_b, sum(1 for k in keys if k[0] == "bit" and first[k] <= t <= last.get(k, first[k])))

    flags = dict.fromkeys(FLAG_DIMS, 0)
    lits = dict(count=0, maxbits=0)

    def scan(e):
        if isinstance(e, expr.Cast):
            flags["has_cast"] = 1
        elif isinstance(e, expr.Index):
            flags["has_index"] = 1
        elif isinstance(e, expr.Value):
            lits["count"] += 1
            w = getattr(getattr(e, "type", None), "width", None)
            if w is None:
                w = int(e.value).bit_length() if not isinstance(e.value, bool) else 1
            lits["maxbits"] = max(lits["maxbits"], w)
        elif isinstance(e, expr.Binary):
            o = e.op
            if o in (_OPS.SHIFT_LEFT, _OPS.SHIFT_RIGHT):
                flags["has_shift"] = 1
            elif o in (_OPS.EQUAL, _OPS.NOT_EQUAL, _OPS.LESS, _OPS.LESS_EQUAL, _OPS.GREATER, _OPS.GREATER_EQUAL):
                flags["has_compare"] = 1
            elif o in (_OPS.ADD, _OPS.SUB):
                flags["has_add"] = 1
            elif o == _OPS.MUL:
                flags["has_mul"] = 1
        for c in _walk_children(e):
            scan(c)

    best = dict.fromkeys(("cone_regs", "total_reads", "stored_count", "stored_bits", "max_store_ops", "cond_ops", "cone_ops", "depth", "ershov", "lr_stack",
                          "distinct_leaf_bits", "leaf_refs", "unique_nodes", "unary_ops", "literal_count", "max_literal_bits"), 0)
    for cond, tc in ifs:
        try:
            seen, regs_in, leaves, refs = set(), set(), set(), 0
            acc = dict.fromkeys(best, 0)
            canon_seen = set()

            def visit(e):
                nonlocal refs
                ks = keys_of(e, [])
                for k in set(ks):
                    if k in stores:
                        if k not in seen:
                            seen.add(k)
                            se = stores[k][0]
                            o = tree_ops(se)
                            acc["cone_ops"] += o
                            acc["max_store_ops"] = max(acc["max_store_ops"], o)
                            acc["depth"] = max(acc["depth"], tree_depth(se))
                            acc["ershov"] = max(acc["ershov"], ershov(se))
                            acc["lr_stack"] = max(acc["lr_stack"], lr_need(se))
                            acc["stored_bits"] += widths.get(k, 1)
                            acc["total_reads"] += len(set(keys_of(se, [])))
                            _canon(se, canon_seen)
                            scan(se)
                            visit(se)
                    elif k[0] == "reg" and k[1] in measured:
                        regs_in.add(k[1])
                for x in ks:
                    leaves.add(x)
                refs += len(ks)
            _canon(cond, canon_seen)
            scan(cond)
            visit(cond)
            acc["cone_regs"] = len(regs_in)
            acc["stored_count"] = len(seen)
            acc["distinct_leaf_bits"] = len(leaves)
            acc["leaf_refs"] = refs
            acc["unique_nodes"] = len(canon_seen)
            cone_lr = lr_need(cond)
            acc["lr_stack"] = max(acc["lr_stack"], cone_lr)
            acc["cond_ops"] = tree_ops(cond)
            acc["depth"] = max(acc["depth"], tree_depth(cond))
            acc["ershov"] = max(acc["ershov"], ershov(cond))
            acc["total_reads"] += len(set(keys_of(cond, [])))
            for k in best:
                if k in ("unary_ops", "literal_count", "max_literal_bits"):
                    continue
                best[k] = max(best[k], acc[k])
        except Exception:  # noqa: BLE001  (RecursionError, unknown node types ...): never silently report zeros
            unanalysed += 1
    # NOT / literal counts over the stores and conditions of all cones (circuit-wide, simplest conservative count)
    un = 0

    def count_unary(e):
        nonlocal un
        if isinstance(e, expr.Unary):
            un += 1
        for c in _walk_children(e):
            count_unary(c)
    for k, (se, _t) in stores.items():
        count_unary(se)
    for cond, _t in ifs:
        count_unary(cond)
    best["unary_ops"] = un
    best["literal_count"], best["max_literal_bits"] = lits["count"], lits["maxbits"]
    v = dict(best)
    v.update(peak_live_regs=peak_r, peak_live_bitvars=peak_b, n_ifs=sum(1 for i in instrs if i.operation.name == "if_else"), unanalysed=unanalysed, **flags)
    return {k: int(v[k]) for k in DIMENSIONS}


# ---------------------------------------------------------------- envelope
def dominates(a, b, dims):
    """a >= b in every listed dimension."""
    return all(a[d] >= b[d] for d in dims)


def envelope_verdict(v, accepted, rejected, pass_dims=None, fail_dims=None):
    """accepted / rejected: lists of (name, vector). PASS if v <= some accepted vector in all pass_dims (default: all dimensions);
    FAIL if v >= some rejected vector in all fail_dims; both -> CONTRADICTION; neither -> UNCERTAIN. Returns (verdict, witness name or None)."""
    pass_dims = list(pass_dims or DIMENSIONS)
    fail_dims = list(fail_dims or DIMENSIONS)
    # never PASS for something the analysis could not fully follow, or for a circuit without any decision
    can_pass = v.get("unanalysed", 0) == 0 and v.get("n_ifs", 1) >= 1
    p = next((n for n, a in accepted if dominates({**a, "unanalysed": a.get("unanalysed", 0)}, v, pass_dims)), None) if can_pass else None
    f = next((n for n, r in rejected if dominates(v, r, fail_dims)), None)
    if p and f:
        return "CONTRADICTION", (p, f)
    if p:
        return "PASS", p
    if f:
        return "FAIL", f
    return "UNCERTAIN", None
