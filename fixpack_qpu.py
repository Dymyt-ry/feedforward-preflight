# SPDX-License-Identifier: Apache-2.0
"""QPU verification of `dccheck --fix-pack` (v2, bit-level remap): are PACKED circuits accepted by the backend, and do they compute the right answer?
v1 (shift + narrowing-cast reads) was rejected by the QPU with 6066 for the 8-bit and 2-bit whole-register cases; see data/fixpack_candidates_v1.

For each source circuit (many small measured registers, XOR into a store, `if S[0]`, X on an out qubit) the original is rejected (6057)
or sits in the uncertain zone; the packed version (dcfix.fix_pack) is built here with PREPARED basis-state inputs (X + measure_2), so the
expected output is known. Three readback patterns per probe, chosen so that EVERY original register influences the answer:
  last  only the last register's lane-0 bit is 1   -> out = 1   (a packed register read at the highest offset)
  mid   only a middle register's bit is 1           -> out = 1
  all   every register's bit is 1                   -> out = parity of the register count (0 for the even counts used here)
If any packed lane were mis-addressed or mis-sliced in the executed circuit, at least one of the three outputs would flip. One job per probe,
3 circuits (PUBs) of 200 shots each.

    python fixpack_qpu.py --local                       # offline: build, prove packing (dcfix), expected outputs, sensitivity, write QPY candidates
    python fixpack_qpu.py --plan --backend ibm_marrakesh   # = dry run: transpile on the live target (read-only), layout, estimate, PLAN HASH
    python fixpack_qpu.py --submit --backend B --execute --confirm HASH [--resubmit]
    python fixpack_qpu.py --fetch out/manifest_HASH.json [--wait] --backend B     # writes out/RESULTS_QPU_fixpack.md

Guards (mirroring suite.py): dry run by default; --execute needs --confirm equal to the plan hash (hash of the transpiled circuits, shots,
layout); refuses if the estimate exceeds 60% of the remaining quota; refuses if out/manifest_<hash>.json already exists unless --resubmit;
manifest written before the submit loop and after every submit.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import sys
import time

import numpy as np
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, qpy, transpile
from qiskit.circuit.classical import expr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dccheck as D  # noqa: E402
import dcfix as F  # noqa: E402
import dcrules as R  # noqa: E402

OUTDIR = os.path.join(HERE, "out")
CAND_DIR = os.path.join(HERE, "data", "fixpack_candidates")
SHOTS = 200
JOB_COST_S = 2.0               # billing floor per job (suite.py ACC_COST)
QUOTA_FRACTION = 0.60

PROBES = [   # priority order. v2 = bit-level remap only (no shifts, no casts); names FP2_* so that outcomes never collide with the rejected v1 circuits FP_*
    dict(name="FP2_R12x8", kind="regs", nr=12, w=8, note="packed B_R12x8c, v2: 12 x 8-bit whole-register XOR expanded to a bit-level store of lane 0 -> 3 x 32-bit registers (rejected 6057 before packing)"),
    dict(name="FP2_S24", kind="split", note="packed S_24split3x8, v2: 24 x 2-bit registers, 3 stores + final, expanded to bit-level stores of lane 0 -> 2 registers (rejected 6057 before packing)"),
    dict(name="FP2_R12x2", kind="regs", nr=12, w=2, note="packed B_R12x2, v2: 12 x 2-bit whole-register XOR -> 1 register of 24 bits (rejected 6057 before packing)"),
]


# ------------------------------------------------------------------ builders (original circuits, prepared inputs, measure_2)
def patterns(nr):
    return {"last": [nr - 1], "mid": [nr // 2], "all": list(range(nr))}


def _upper(nr, w, salt):
    r = random.Random(7000 + salt)
    return [[0] + [r.randrange(2) for _ in range(w - 1)] for _ in range(nr)]


def _prep_measure(qc, q, reg_of, nr, w, ones, lane, up, ibm):
    if ibm:
        from qiskit_ibm_runtime.circuit import MidCircuitMeasure
    for i in range(nr):
        for j in range(w):
            bit = (1 if i in ones else 0) if j == lane else up[i][j]
            if bit:
                qc.x(q[i * w + j])
            qc.append(MidCircuitMeasure(), [q[i * w + j]], [reg_of(i)[j]]) if ibm else qc.measure(q[i * w + j], reg_of(i)[j])


def build_original(probe, pat, ibm=True):
    """-> (circuit, expected out). Qubit order: sources first, out last (layout convention of suite.py)."""
    ones = set(patterns(probe_nr(probe))[pat])
    if probe["kind"] == "regs":
        nr, w = probe["nr"], probe["w"]
        q = QuantumRegister(nr * w + 1, "q")
        out, L = ClassicalRegister(1, "out"), ClassicalRegister(1, "L")
        regs = [ClassicalRegister(w, f"G{i}") for i in range(nr)]
        qc = QuantumCircuit(q, out, L, *regs, name=f"{probe['name']}_{pat}")
        _prep_measure(qc, q, lambda i: regs[i], nr, w, ones, 0, _upper(nr, w, nr * w), ibm)
        e = expr.bit_xor(regs[0], regs[1])
        for r in regs[2:]:
            e = expr.bit_xor(e, r)
        S = ClassicalRegister(w, "S")
        qc.add_register(S)
        qc.store(S, e)
        with qc.if_test(expr.lift(S[0])):
            qc.x(q[nr * w])
        qc.measure(q[nr * w], out[0])
        return qc, len(ones) % 2
    if probe["kind"] == "bit":
        nr, w = probe["nr"], probe["w"]
        q = QuantumRegister(nr * w + 1, "q")
        out, L = ClassicalRegister(1, "out"), ClassicalRegister(1, "L")
        regs = [ClassicalRegister(w, f"G{i}") for i in range(nr)]
        qc = QuantumCircuit(q, out, L, *regs, name=f"{probe['name']}_{pat}")
        _prep_measure(qc, q, lambda i: regs[i], nr, w, ones, 1, _upper(nr, w, nr * w + 1), ibm)   # the XOR reads lane 1
        e = expr.bit_xor(expr.lift(regs[0][1]), expr.lift(regs[1][1]))
        for r in regs[2:]:
            e = expr.bit_xor(e, expr.lift(r[1]))
        qc.store(L[0], e)
        with qc.if_test(expr.lift(L[0])):
            qc.x(q[nr * w])
        qc.measure(q[nr * w], out[0])
        return qc, len(ones) % 2
    import suite as S
    sp = dict(next(x for x in S.SPLIT if x["name"] == "S_24split3x8"), X=sorted(ones))
    qc = S.build_split(sp, "X", ibm=ibm)
    qc.name = f"{probe['name']}_{pat}"
    return qc, S.split_expected(sp, ones)


def probe_nr(probe):
    return probe.get("nr", 24)


def prepared_inputs(qc):
    """measurement outcomes implied by the X gates before each measure (event order)."""
    flipped, vals = set(), []
    for inst in qc.data:
        n = inst.operation.name
        if n == "x":
            flipped ^= {qc.find_bit(inst.qubits[0]).index}
        elif n.startswith("measure") and not (inst.qubits and qc.find_bit(inst.qubits[0]).index == qc.num_qubits - 1):
            vals.append(int(qc.find_bit(inst.qubits[0]).index in flipped))
    return vals


def out_bit(qc, sim):
    """value the out qubit would be measured in: parity of the X gates that executed on it (the evaluator only records quantum ops)."""
    last = qc.num_qubits - 1
    return sum(1 for t in sim.trace if t[0] == "gate" and t[1] == "x" and t[2] == (last,)) % 2


def _in(ins):
    return lambda i, t: ins[i] if i < len(ins) else 0


def build_probe(probe, rules):
    """-> dict(pattern -> dict(orig, packed, expected, fix)) with all offline checks done (raises on any failure)."""
    res = {}
    for pat in ("last", "mid", "all"):
        orig, exp = build_original(probe, pat)
        fx = F.fix_pack(orig, rules)
        if fx.refused or not fx.changed:
            raise RuntimeError(f"{probe['name']}/{pat}: fix_pack refused or unchanged: {fx.refused or fx.notes}")
        bad_h = [h["rule"] for h in fx.after["hazards"]]
        if bad_h:
            raise RuntimeError(f"{probe['name']}/{pat}: packed circuit trips hazard detectors {bad_h}")
        ins = prepared_inputs(orig)
        so, sp = F.simulate(orig, _in(ins)), F.simulate(fx.circuit, _in(ins))
        if out_bit(orig, so) != exp or out_bit(fx.circuit, sp) != exp:
            raise RuntimeError(f"{probe['name']}/{pat}: simulated out {out_bit(orig, so)}/{out_bit(fx.circuit, sp)} != expected {exp}")
        # sensitivity: flipping any single register's relevant bit flips the packed circuit's output (every register matters)
        res[pat] = dict(orig=orig, packed=fx.circuit, expected=exp, fix=fx, inputs=ins)
    return res


def sensitivity(probe, built):
    """for the 'all' pattern: flip each source register's relevant lane bit in turn; the packed circuit's out must flip every time."""
    nr = probe_nr(probe)
    b = built["all"]
    packed, ins = b["packed"], list(b["inputs"])
    base = out_bit(packed, F.simulate(packed, _in(ins)))
    flips = 0
    w = probe.get("w", 2)
    lane = 0
    for r in range(nr):
        e = ins.copy()
        k = r * w + lane
        e[k] ^= 1
        got = out_bit(packed, F.simulate(packed, _in(e)))
        flips += int(got != base)
    return flips, nr


# ------------------------------------------------------------------ candidates + local
def write_candidates(allbuilt, rules):
    os.makedirs(CAND_DIR, exist_ok=True)
    idx = []
    for probe in PROBES:
        for pat, b in allbuilt[probe["name"]].items():
            fn = f"{probe['name']}_{pat}_packed.qpy"
            with open(os.path.join(CAND_DIR, fn), "wb") as f:
                qpy.dump(b["packed"], f)
            idx.append(dict(file=fn, probe=probe["name"], pattern=pat, expected_out=b["expected"], note=probe["note"],
                            groups=b["fix"].groups, before=b["fix"].before["status"], after=b["fix"].after["status"],
                            cone_registers_after=max(i["metrics"]["cone_regs"] for i in b["fix"].after["ifs"]),
                            max_register_bits=max(g["width"] for g in b["fix"].groups)))
    json.dump(dict(note="Fix-pack v2 packed circuits (bit-level remap only: no shifts, no casts) written by fixpack_qpu.py --local (logical, before transpilation; prepared inputs, measure_2). "
                        "expected_out is the value of the `out` register. Originals are the suite probes B_R12x8c / S_24split3x8 / B_R12x2 with prepared inputs. The rejected v1 circuits (shift/cast reads) are in data/fixpack_candidates_v1/.",
                   rules_id=rules["rules_id"], candidates=idx), open(os.path.join(CAND_DIR, "index.json"), "w"), indent=1)
    return idx


def local(rules):
    ok = True
    allbuilt = {}
    for probe in PROBES:
        built = build_probe(probe, rules)
        allbuilt[probe["name"]] = built
        fl, n = sensitivity(probe, built)
        b = built["all"]
        widths = [g["width"] for g in b["fix"].groups]
        status = "ok" if fl == n and max(widths) <= 32 else "PROBLEM"
        ok &= status == "ok"
        print(f"{probe['name']:9s} before {b['fix'].before['status']} cone {max(i['metrics']['cone_regs'] for i in b['fix'].before['ifs'])} -> "
              f"after {b['fix'].after['status']} cone {max(i['metrics']['cone_regs'] for i in b['fix'].after['ifs'])}; packed widths {widths} (<= 32 bits); "
              f"equivalence {b['fix'].equivalence['n_patterns']} patterns; sensitivity {fl}/{n} registers flip the output; "
              f"expected out last/mid/all = {built['last']['expected']}/{built['mid']['expected']}/{built['all']['expected']}  [{status}]")
    idx = write_candidates(allbuilt, rules)
    print(f"wrote {len(idx)} packed QPY files + index.json to data/fixpack_candidates/")
    return ok


# ------------------------------------------------------------------ plan / submit / fetch
def canon(qc):
    import suite as S
    return S.canon(qc)


def build_plan(adapter, rules, shots=SHOTS):
    import suite as S
    be = adapter.backend()
    jobs, h = [], hashlib.sha256()
    h.update(json.dumps({"backend": getattr(adapter, "name", "?"), "shots": shots, "probes": [p["name"] for p in PROBES]}, sort_keys=True).encode())
    for probe in PROBES:
        built = build_probe(probe, rules)
        nsrc = built["last"]["orig"].num_qubits - 1
        gq = S.good_qubits(be, nsrc + 1)
        lay = gq[1:] + [gq[0]]                                     # sources first, best-readout qubit = out (suite.py convention)
        isas, names, blobs = [], [], []
        for pat in ("last", "mid", "all"):
            c = built[pat]["packed"]
            t = transpile(c, be, initial_layout=lay, optimization_level=1, seed_transpiler=7)
            isas.append(t)
            names.append(c.name)
            buf = io.BytesIO()
            qpy.dump(t, buf)
            blobs.append(buf.getvalue())
            h.update(hashlib.sha256(canon(t).encode()).digest())
        h.update(json.dumps([probe["name"], shots, names, lay]).encode())
        jobs.append(dict(name=probe["name"], circuit_names=names, expected=[built[p]["expected"] for p in ("last", "mid", "all")],
                         patterns=["last", "mid", "all"], shots=shots, est_s=JOB_COST_S, layout=lay, isas=isas, qpy=blobs,
                         groups=built["last"]["fix"].groups, after_status=built["last"]["fix"].after["status"]))
    return dict(backend=getattr(adapter, "name", "?"), shots=shots, jobs=jobs, est_total_s=float(sum(j["est_s"] for j in jobs)),
                hash=h.hexdigest()[:12], rules_id=rules["rules_id"])


def atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + f".tmp{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def submit(adapter, rules, execute=False, confirm=None, resubmit=False, outdir=OUTDIR, quiet=False):
    pl = build_plan(adapter, rules)
    say = (lambda *a: None) if quiet else print
    say(f"backend {pl['backend']}  FIX-PACK v2 verification: {len(pl['jobs'])} jobs x 3 circuits x {pl['shots']} shots (priority order)")
    say(f"{'job':10s} {'circ':>4s} {'shots':>5s} {'est s':>6s}  packed registers / expected out (last, mid, all)")
    for j in pl["jobs"]:
        say(f"{j['name']:10s} {len(j['isas']):4d} {j['shots']:5d} {j['est_s']:6.1f}  "
            f"{[(g['name'], g['width']) for g in j['groups']]} / {j['expected']}   dccheck after packing: {j['after_status']}")
    rem, lim, end = adapter.remaining_s()
    ratio = pl["est_total_s"] / rem if rem > 0 else float("inf")
    say(f"estimated QPU seconds {pl['est_total_s']:.1f} (2 s billing floor per job); remaining quota {rem:.0f} s of {lim:.0f} (period end {end}); "
        f"estimate = {100 * ratio:.0f}% of remaining (limit {100 * QUOTA_FRACTION:.0f}%)")
    say(f"PLAN HASH {pl['hash']}")
    mpath = os.path.join(outdir, f"manifest_{pl['hash']}.json")
    if not execute:
        say("DRY RUN: nothing submitted. To submit: --execute --confirm " + pl["hash"])
        return pl, None
    if confirm != pl["hash"]:
        raise SystemExit(f"REFUSED: --confirm {confirm!r} does not match plan hash {pl['hash']} (plan changed or confirm missing)")
    if ratio > QUOTA_FRACTION:
        raise SystemExit(f"REFUSED: estimate {pl['est_total_s']:.1f} s is {100 * ratio:.0f}% of remaining {rem:.0f} s (> {100 * QUOTA_FRACTION:.0f}%)")
    if os.path.exists(mpath) and not resubmit:
        raise SystemExit(f"REFUSED: {mpath} already exists (this plan was submitted before); pass --resubmit to submit it again")
    pdir = os.path.join(outdir, f"plan_{pl['hash']}")
    os.makedirs(pdir, exist_ok=True)
    man = dict(plan_hash=pl["hash"], kind="fixpack", backend=pl["backend"], shots=pl["shots"], est_total_s=pl["est_total_s"],
               quota_remaining_s=rem, submitted_at=time.strftime("%Y-%m-%d %H:%M:%S"), rules_id=pl["rules_id"], resubmit=bool(resubmit), jobs=[])
    atomic_json(mpath, man)
    for j in pl["jobs"]:
        qp = os.path.join(pdir, f"{j['name']}.qpy.bin")
        with open(qp, "wb") as f:
            f.write(b"".join(len(b).to_bytes(8, "little") + b for b in j["qpy"]))
        rec = dict(name=j["name"], circuit_names=j["circuit_names"], patterns=j["patterns"], expected=j["expected"], shots=j["shots"], layout=j["layout"],
                   est_s=j["est_s"], groups=j["groups"], qpy_file=os.path.relpath(qp, outdir), job_id=None, error=None)
        man["jobs"].append(rec)
        try:
            rec["job_id"] = adapter.run(j["isas"], j["shots"])
            say(f"submitted {j['name']:10s} {rec['job_id']}")
        except Exception as ex:  # noqa: BLE001
            rec["error"] = f"{type(ex).__name__}: {str(ex)[-300:]}"
            say(f"SUBMIT FAILED {j['name']}: {rec['error']}")
        atomic_json(mpath, man)
    return pl, mpath


def fetch(adapter, manifest_path, wait=False, timeout=3600, poll=20, outdir=OUTDIR):
    import suite as S
    man = json.load(open(manifest_path))
    t0 = time.time()
    L = [f"# QPU results: packed circuits (dccheck --fix-pack), plan {man['plan_hash']} on {man['backend']}",
         f"submitted {man['submitted_at']}; est {man['est_total_s']:.0f} QPU-s; {man['shots']} shots per pattern circuit", "",
         "Probe = many small measured registers packed into few wide ones, prepared basis-state inputs, XOR store, `if S[0]`, X on out. "
         "Patterns: last = only the last register's bit is 1, mid = only a middle one, all = every register's bit is 1; expected out in the table. "
         "Wilson 95% intervals; chance is 0.5.", "",
         "| probe | packed registers | pattern | expected out | P(correct) [CI] | status |", "|---|---|---|---|---|---|"]
    verdicts = {}
    for rec in man["jobs"]:
        status, msg, code = "NOT_SUBMITTED", rec.get("error"), None
        res = None
        if rec.get("job_id"):
            job = adapter.get(rec["job_id"])
            while True:
                st = S._status(job)
                if st in ("DONE", "ERROR", "CANCELLED") or not wait or time.time() - t0 > timeout:
                    break
                time.sleep(poll)
            status = st
            if st == "DONE":
                res = job.result()
            elif st in ("ERROR", "CANCELLED"):
                try:
                    msg = str(job.error_message())[-400:]
                except Exception as ex:  # noqa: BLE001
                    msg = f"error_message unavailable: {type(ex).__name__}"
                code = S.err_code(msg)
        grp = ", ".join(f"{g['name']}:{g['width']}b" for g in rec["groups"])
        if res is None:
            L.append(f"| {rec['name']} | {grp} | all | | | **{status}** {('error ' + str(code)) if code else ''} {(msg or '')[-120:]} |".replace("\n", " "))
            verdicts[rec["name"]] = f"{status} {code or ''}".strip()
            continue
        ok = True
        for pat, exp, pub in zip(rec["patterns"], rec["expected"], res):
            o = pub.data.out.to_bool_array(order="little").astype(int).ravel()
            k, n = int((o == exp).sum()), len(o)
            lo, hi = S.wilson(k, n)
            good = lo > 0.5
            ok &= good
            L.append(f"| {rec['name']} | {grp} | {pat} | {exp} | {k / n:.3f} [{lo:.3f}, {hi:.3f}] | {'above chance' if good else 'NOT above chance'} |")
        verdicts[rec["name"]] = "ACCEPTED, computes correctly (all three patterns above chance)" if ok else "ACCEPTED, but a pattern is not above chance"
    L += ["", "## Verdict", ""] + [f"- {k}: {v}" for k, v in verdicts.items()]
    L += ["", "A pattern above chance for last, mid and all means each packed lane influences the output as the original registers did (an ignored or "
              "mis-sliced lane flips at least one pattern). Readout error limits P(correct) well below 1 (see round 2 of the suite: 0.75 to 0.99)."]
    txt = "\n".join(L) + "\n"
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "RESULTS_QPU_fixpack.md"), "w") as f:
        f.write(txt)
    print(txt)
    return man, verdicts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--plan", action="store_true", help="dry run against the live target (read-only), prints PLAN HASH and estimate")
    ap.add_argument("--submit", action="store_true", help="dry run unless --execute --confirm HASH")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm", default=None)
    ap.add_argument("--resubmit", action="store_true", help="allow submitting a plan whose out/manifest_<hash>.json already exists")
    ap.add_argument("--fetch", default=None)
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--backend", default="ibm_marrakesh")
    ap.add_argument("--rules", default=None)
    a = ap.parse_args(argv)
    rules = R.load_rules(a.rules)
    if a.local:
        return 0 if local(rules) else 1
    import suite as S
    if a.plan or a.submit:
        submit(S.RealAdapter(a.backend), rules, execute=a.execute and a.submit, confirm=a.confirm, resubmit=a.resubmit)
    if a.fetch:
        fetch(S.RealAdapter(a.backend), a.fetch, wait=a.wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
