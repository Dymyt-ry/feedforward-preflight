# SPDX-License-Identifier: Apache-2.0
"""Offline tests for the guarded submit / fetch path (fake adapter, no IBM access, no QPU).
run:  ../../.venv/bin/python test_suite.py
"""
import json
import zlib
import os
import shutil
import tempfile
import types as _t

import numpy as np

import suite as S

HERE = os.path.dirname(os.path.abspath(__file__))


class FakeResultData:
    def __init__(self, **regs):
        for k, v in regs.items():
            setattr(self, k, _t.SimpleNamespace(to_bool_array=(lambda order="little", v=v: v)))


class FakeJob:
    def __init__(self, status, result=None, msg=""):
        self._s, self._r, self._m = status, result, msg

    def status(self):
        return self._s

    def error_message(self):
        return self._m

    def result(self):
        return self._r

    def metrics(self):
        return {"usage": {"quantum_seconds": 2}}


class FakeAdapter:
    name = "fake_marrakesh"

    def __init__(self, remaining=271.0, fail=("A96c_K1", "C6x16")):
        self.be, _ = S.get_target("fake")
        self.remaining, self.calls, self.fail = remaining, [], set(fail)
        self.jobs, self.meta = {}, {}
        self.regfail = {"B_R12x8c", "B_R12x2", "B_R12x8u", "S_R12x2c", "Z_shan103", "Z_tree128", "Z_chain128", "B_R12x2r", "X_R12x2", "P1_R12x2_a", "P1_R12x2_b", "P1_split6", "P1_alloc12", "P1_ir126", "P1_bal104", "P1_C8bal", "P3_R12x2", "P3_split6", "V1_rc10_P3", "V1_rc12_P1", "V1_rc4_P10", "E1_decode9", "E2_shan112"}
        self.expected = {}
        self.latinc = {"XORC15": 700.0, "XORT15": 650.0, "SOUP63": 1900.0, "POP8": 900.0, "WVOTE27": 1050.0, "S15_16": 700.0, "S45_16": 1550.0, "S15_4": 650.0, "AOC15": 720.0, "AOT15": 700.0, "NULL": 0.0}

    def info(self):
        return dict(name=self.name, processor_type={"family": "Heron", "revision": "2"}, status="active", pending_jobs=0, properties_last_update="2026-11-15T00:00:00+00:00")

    def backend(self):
        return self.be

    def remaining_s(self):
        return self.remaining, 600.0, "2026-12-13T00:00:00Z"

    def run(self, isas, shots):
        jid = f"fakejob{len(self.calls):03d}"
        self.calls.append((jid, len(isas), shots))
        self.meta[jid] = ([c.name for c in isas], shots)
        return jid

    def get(self, jid):
        names, shots = self.meta[jid]
        if names[0].startswith(("P1_", "P3_", "V1_", "E1_", "E2_")) and names[0][-2] == "_" and names[0][-1] in "ZABCDE":
            base = names[0][:-2]
            if base in self.regfail:
                return FakeJob("ERROR", msg="Error code 6057; The job requires more classical registers than the control hardware provides.")
            rng = np.random.default_rng(zlib.crc32(jid.encode()))
            def col(nm):
                e = self.expected.get(nm, 0)
                e = list(e) if isinstance(e, (list, tuple)) else [e]
                return np.stack([rng.random(shots) < (0.9 if x else 0.1) for x in e], 1)
            return FakeJob("DONE", result=[types_pub(out=col(nm)) for nm in names])
        if names[0].startswith(("B_R", "S_", "Z_", "X_")):
            base = names[0][:-2]
            if base in self.regfail:
                return FakeJob("ERROR", msg="Error code 6057; The job requires more classical registers than the control hardware provides.")
            rng = np.random.default_rng(zlib.crc32(jid.encode()))
            return FakeJob("DONE", result=[types_pub(out=(rng.random((shots, 1)) < (0.93 if nm.endswith("_A") else 0.06))) for nm in names])
        case = names[-1]
        if case in self.fail:
            return FakeJob("ERROR", msg="Unable to retrieve job result. Error code 6057; The job requires more classical registers "
                                        "than the control hardware provides. -- https://ibm.biz/error_codes#6057")
        rng = np.random.default_rng(zlib.crc32(jid.encode()))
        out = []
        if names[0].startswith("REF"):      # LAT: REF.. + case
            K, nz, n_p = S.LAT_K, S.LAT_NZ, S.LAT_NZ + S.LAT_NX
            s0 = rng.uniform(0.9, 0.97, n_p)
            rate = rng.uniform(3e-6, 6e-6, n_p)
            for nm in names:
                d = int(nm[4:]) if nm.startswith("REFB") else (int(nm[3:]) if nm.startswith("REF") else 0)
                lat = (450.0 + d if nm.startswith("REFB") else 600.0 + (self.latinc[nm] if nm in self.latinc else 0.0))
                d = 0 if nm.startswith("REFB") else d
                sc = s0 - rate * K * (1884 + lat + d)
                succ = rng.random((shots, n_p)) < sc
                want = np.zeros(n_p, bool)
                want[:nz] = True
                bits = np.where(want, succ, ~succ)
                out.append(types_pub(pr=bits, fl=rng.random((shots, 1)) < 0.5))
        else:
            out.append(types_pub(out=rng.random((shots, 1)) < 0.5))
        return FakeJob("DONE", result=out)


def types_pub(**regs):
    return _t.SimpleNamespace(data=FakeResultData(**regs))


def test_guards_and_mutations():
    """guards() must flag known-bad constructs; the semantic checkers must catch a mutated expression."""
    from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
    from qiskit.circuit.classical import expr, types
    U = types.Uint
    def base():
        q = QuantumRegister(2, "q"); m = ClassicalRegister(8, "m"); Sr = ClassicalRegister(8, "S")
        return QuantumCircuit(q, m, Sr), m, Sr
    qc, m, Sr = base(); qc.store(Sr[0], expr.lift(m[0]))
    assert any("6062" in g for g in S.guards(qc)), "copy store not flagged"
    qc, m, Sr = base(); qc.store(Sr, expr.shift_right(m, Sr))
    assert any("6060" in g for g in S.guards(qc)), "register-amount shift not flagged"
    qc, m, Sr = base(); qc.store(Sr, expr.bit_xor(expr.lift(1 << 33, U(64)), expr.cast(m, U(64))) if False else expr.bit_xor(m, m)); qc.store(Sr, expr.lift(5, U(8)))
    assert any("constant store" in g for g in S.guards(qc)), "constant store not flagged"
    qc, m, Sr = base(); W = ClassicalRegister(64, "W"); qc.add_register(W); qc.store(W, expr.bit_and(expr.cast(m, U(64)), expr.lift(1 << 40, U(64))))
    assert any("u32" in g for g in S.guards(qc)), "wide literal not flagged"
    qc, m, Sr = base(); qc.add_var("v", False)
    assert any("6066" in g for g in S.guards(qc)), "declared Var not flagged"
    q = QuantumRegister(2, "q"); a = ClassicalRegister(4, "a"); qc = QuantumCircuit(q, a); b = ClassicalRegister(bits=[a[0], a[1]], name="b"); qc.add_register(b)
    assert any("6327" in g for g in S.guards(qc)), "overlapping registers not flagged"
    # a good circuit has no violations
    qc = S.build_acc(next(p for p in S.catalogue() if p["name"] == "A84t_K1"), K=1)
    assert S.guards(qc) == [], S.guards(qc)
    # mutation: swap one xor for or in the recipe -> exhaustive checker must report mismatches against the unmutated recipe
    p = next(x for x in S.catalogue() if x["name"] == "XORC15")
    good = S.build_lat(p, K=2)
    assert S.semantic_check(good, p)[1] == 0
    import copy
    def mutate(n):
        if n[0] == "op" and n[2][0] == "bit":
            return ("op", "or") + n[2:]
        if n[0] == "op":
            return n[:2] + (mutate(n[2]),) + n[3:]
        return n
    pm = dict(p); pm["recipe"] = mutate(p["recipe"])
    bad = S.build_lat(pm, K=2)
    assert S.semantic_check(bad, p)[1] > 0, "mutated expression not detected by semantic_check"
    sp = S.SPLIT[0]
    qm = S.build_split(dict(sp, stores=[("S1", S._G(0, 6)), ("S2", S._G(6, 11)), ("S", ["S1", "S2"])]), "A")   # drops G11
    assert S.check_split(qm, sp, "A")[1] > 0, "mutated split not detected"
    import tiers_v2 as T2
    pr_ = T2.xor_prog("mut", 12, 2)
    ok_ = pr_.circuit("A")
    assert T2.check_uprog(pr_, ok_, "A")[1] == 0
    bad_ = T2.UProg("mut2", 2, T2.G(0, 12), [("S", T2.chain_op("xor", T2.G(0, 11)))])         # drops G11 relative to the reference program
    assert T2.check_uprog(pr_, bad_.circuit("A"), "A")[1] > 0, "mutated UProg not detected"
    ir_ = T2.ir_prog(96)
    assert T2.check_bprog(ir_, ir_.circuit("A"), "A")[1] == 0
    ir2 = T2.BProg("mut3", T2.ir_prog(96).root, 97)
    ir2.root = (ir2.root[0], "or" if ir2.root[1] != "or" else "xor") + ir2.root[2:]
    assert T2.check_bprog(ir_, ir2.circuit("A"), "A")[1] > 0, "mutated BProg not detected"
    rm = S.build_regs_det(12, 2, 11, "A")
    assert S.check_regs_det(rm, 12, 2, 12, "A")[1] > 0, "mutated register probe not detected"


def test_hash_invalidation():
    a = FakeAdapter()
    h0 = S.build_plan(a, "minimal")["hash"]
    assert S.build_plan(a, "minimal", shots=2999)["hash"] != h0, "hash ignores shots"
    old = S.LAT_K
    try:
        S.LAT_K = 14
        assert S.build_plan(a, "minimal")["hash"] != h0, "hash ignores K"
    finally:
        S.LAT_K = old
    assert S.build_plan(a, "minimal")["hash"] == h0


def main():
    test_guards_and_mutations()
    test_hash_invalidation()
    tmp = tempfile.mkdtemp()
    try:
        ad = FakeAdapter()
        # 1. dry run never submits
        pl, mp = S.submit(ad, "minimal", execute=False, outdir=tmp, quiet=True)
        assert mp is None and ad.calls == [], "dry run submitted"
        assert len(pl["jobs"]) == 12 and 30 < pl["est_total_s"] < 70, pl["est_total_s"]
        h = pl["hash"]
        # plan hash is deterministic across rebuilds
        assert S.build_plan(ad, "minimal")["hash"] == h
        # 2. --execute without confirm refuses
        for bad in (None, "deadbeef0000"):
            try:
                S.submit(ad, "minimal", execute=True, confirm=bad, outdir=tmp, quiet=True)
                raise AssertionError("submitted with bad confirm")
            except SystemExit as e:
                assert "REFUSED" in str(e)
        assert ad.calls == [], "bad confirm still submitted"
        # 3. quota guard: estimate > 60% of remaining
        low = FakeAdapter(remaining=50.0)
        try:
            S.submit(low, "minimal", execute=True, confirm=h, outdir=tmp, quiet=True)
            raise AssertionError("quota guard failed")
        except SystemExit as e:
            assert "REFUSED" in str(e) and "remaining" in str(e)
        assert low.calls == []
        # 4. correct confirm submits all 12 and writes an atomic manifest
        pl2, mp = S.submit(ad, "minimal", execute=True, confirm=h, outdir=tmp, quiet=True)
        assert len(ad.calls) == 12 and mp and os.path.exists(mp)
        man = json.load(open(mp))
        assert man["plan_hash"] == h and all(j["job_id"] for j in man["jobs"]) and "H_ops" in man["predictions"]
        assert not [f for f in os.listdir(tmp) if ".tmp" in f]
        # 4b. duplicate-submission guard: same plan hash, manifest exists -> refuse unless resubmit
        n_before = len(ad.calls)
        try:
            S.submit(ad, "minimal", execute=True, confirm=h, outdir=tmp, quiet=True)
            raise AssertionError("duplicate submission was not refused")
        except SystemExit as e:
            assert "already exists" in str(e), e
        assert len(ad.calls) == n_before, "duplicate guard still submitted jobs"
        # 5. fetch on synthetic results incl. failed jobs (6057 is data)
        man, res = S.fetch_jobs(ad, mp, outdir=tmp)
        assert res["A96c_K1"]["code"] == 6057 and res["C6x16"]["status"] == "ERROR"
        assert res["XORC15"]["status"] == "DONE"
        txt = S.analyse_manifest(man, res, outdir=tmp)
        assert os.path.exists(os.path.join(tmp, "RESULTS_QPU.md"))
        for key in ("Latency increments", "Hypothesis tests", "XORC15 - XORT15", "Acceptance probes", "6057" if False else "A96c_K1"):
            assert key in txt, key
        # injected increments recovered (within 5 sigma)
        sw = [l for l in txt.splitlines() if l.startswith("| XORC15 |") and "+/-" in l][0]
        val = float(sw.split("|")[5].split("+/-")[0])
        assert abs(val - 700) < 200, (val, sw)
        sp = [l for l in txt.splitlines() if l.startswith("| SOUP63 |") and "+/-" in l][0]
        assert abs(float(sp.split("|")[5].split("+/-")[0]) - 1900) < 350, sp   # beyond old REF range: must not clip
        print(txt.split("## Pre-registered")[0][-1100:])
        # 6. regbisect tier: dry run, guards, execute, fetch + analysis
        rb = FakeAdapter()
        plr, mp0 = S.submit(rb, "regbisect", execute=False, outdir=tmp, quiet=True)
        assert mp0 is None and rb.calls == [] and len(plr["jobs"]) == 9 and plr["est_total_s"] <= 20, plr["est_total_s"]
        assert all(len(j["isas"]) == 2 and j["layout"] for j in plr["jobs"])
        try:
            S.submit(rb, "regbisect", execute=True, confirm="000000000000", outdir=tmp, quiet=True)
            raise AssertionError("regbisect submitted with bad hash")
        except SystemExit:
            pass
        _, mpr = S.submit(rb, "regbisect", execute=True, confirm=plr["hash"], outdir=tmp, quiet=True)
        assert len(rb.calls) == 9 and "H_read_regs" in json.load(open(mpr))["predictions"]
        manr, resr = S.fetch_jobs(rb, mpr, outdir=tmp)
        txtr = S.analyse_manifest(manr, resr, outdir=tmp)
        assert resr["B_R12x2"]["code"] == 6057 and "Threshold consistency" in txtr and "B_R4x8" in txtr and "YES" in txtr
        assert os.path.exists(os.path.join(tmp, "RESULTS_QPU_regbisect.md"))
        print(txtr[-1500:])
        # 7. split tier
        sa = FakeAdapter()
        pls, m0 = S.submit(sa, "split", execute=False, outdir=tmp, quiet=True)
        assert m0 is None and sa.calls == [] and len(pls["jobs"]) == 5 and pls["est_total_s"] <= 12, pls["est_total_s"]
        assert [j["store_report"] for j in pls["jobs"] if j["name"] == "S_12split6"][0] == [("S1", 6, 5), ("S2", 6, 5), ("S", 2, 1)]
        try:
            S.submit(sa, "split", execute=True, confirm=None, outdir=tmp, quiet=True)
            raise AssertionError("split submitted without confirm")
        except SystemExit:
            pass
        _, mps = S.submit(sa, "split", execute=True, confirm=pls["hash"], outdir=tmp, quiet=True)
        assert len(sa.calls) == 5 and "H_cone" in json.load(open(mps))["predictions"]
        mans, ress = S.fetch_jobs(sa, mps, outdir=tmp)
        txts = S.analyse_manifest(mans, ress, outdir=tmp)
        assert "Verdict" in txts and "PER STORE" in txts and os.path.exists(os.path.join(tmp, "RESULTS_QPU_split.md")), txts
        print(txts[-300:])
        # 8. size tier (+ size_mid, xrep): dry run <= budget, guards, manifest predictions, analysis with full messages
        za = FakeAdapter()
        plz, z0 = S.submit(za, "size", execute=False, outdir=tmp, quiet=True)
        assert z0 is None and za.calls == [] and len(plz["jobs"]) == 13 and plz["est_total_s"] <= 28, plz["est_total_s"]
        assert all(j["metrics"] and j["layout"] for j in plz["jobs"])
        try:
            S.submit(za, "size", execute=True, confirm="bad", outdir=tmp, quiet=True)
            raise AssertionError("size submitted with bad hash")
        except SystemExit:
            pass
        _, mz = S.submit(za, "size", execute=True, confirm=plz["hash"], outdir=tmp, quiet=True)
        assert len(za.calls) == 13 and "Op definition" in json.load(open(mz))["predictions"]
        manz, resz = S.fetch_jobs(za, mz, outdir=tmp)
        assert "more classical registers" in resz["Z_shan103"]["message"]
        tz = S.analyse_manifest(manz, resz, outdir=tmp)
        for key in ("Full error messages", "Pre-registered predictions vs outcome", "Threshold fits", "FAILED AGAIN", "Z_T3r6"):
            assert key in tz, key
        pm, zm = S.submit(FakeAdapter(), "size_mid", execute=False, outdir=tmp, quiet=True)
        assert len(pm["jobs"]) == 2 and pm["est_total_s"] <= 4.0
        xa = FakeAdapter()
        xa.regfail = xa.regfail | {"S_12split6"}
        plx, x0 = S.submit(xa, "xrep", execute=False, outdir=tmp, quiet=True)
        assert x0 is None and len(plx["jobs"]) == 6 and plx["est_total_s"] <= 22, plx["est_total_s"]
        _, mx = S.submit(xa, "xrep", execute=True, confirm=plx["hash"], outdir=tmp, quiet=True)
        manx, resx = S.fetch_jobs(xa, mx, outdir=tmp)
        tx = S.analyse_manifest(manx, resx, outdir=tmp)
        assert "Acceptance matches: 4/4" in tx and "chain - tree" in tx, tx
        print(tz[-1400:])
        # 9. p1 / p2 / p3 tiers (dry run, guards, manifest metadata, synthetic fetch, verdict lines)
        import tiers_v2 as T2L
        for tier, n_jobs, cap in (("p1", 25 if T2L.legacy_available() else 21, 52.0), ("p2", 1, 15.0), ("p3", 5, 10.5), ("v1", 5, 10.5), ("e2", 3, 6.5)):
            fa = FakeAdapter()
            pl_, m_ = S.submit(fa, tier, execute=False, outdir=tmp, quiet=True)
            assert m_ is None and fa.calls == [] and len(pl_["jobs"]) == n_jobs and pl_["est_total_s"] <= cap, (tier, pl_["est_total_s"])
            try:
                S.submit(fa, tier, execute=True, confirm="nope", outdir=tmp, quiet=True)
                raise AssertionError(tier + " submitted with a bad hash")
            except SystemExit:
                pass
            for j in pl_["jobs"]:
                if j["kind"] == "PRG":
                    for cn, pm in j["meta"]["patterns"].items():
                        fa.expected[f"{j['name']}_{cn}"] = pm["expected"]
            _, mp_ = S.submit(fa, tier, execute=True, confirm=pl_["hash"], outdir=tmp, quiet=True)
            mm = json.load(open(mp_))
            assert mm["backend_info"]["processor_type"]["family"] == "Heron" and ("H_peak_live" in mm["predictions"] or tier in ("p2", "v1", "e2"))
            man_, res_ = S.fetch_jobs(fa, mp_, outdir=tmp)
            # analysis must use ONLY the manifest/results: poison every builder, then analyse
            import tiers_v2 as _T2
            saved = {k: getattr(_T2, k) for k in ("e1_progs", "e2_progs", "v1_progs", "p1_progs", "p3_progs", "get_prog", "legacy_circuits", "stack_prog", "xor_prog", "shape_prog", "ir_prog", "dag_prog", "alloc_prog", "split_prog", "overlap_nf")}
            def _boom(*a, **k):
                raise AssertionError("analysis called a builder")
            for k in saved:
                setattr(_T2, k, _boom)
            try:
                tt = S.analyse_manifest(man_, res_, outdir=tmp)
            finally:
                for k, v in saved.items():
                    setattr(_T2, k, v)
            if tier == "e2":
                assert "Validation scoreboard" in tt and "E2_shan112" in tt and "DISAGREE" in mm["predictions"] and "STORED in the manifest" in tt, tt[-500:]
            if tier == "v1":
                assert "Validation scoreboard" in tt and "M_w" in tt and "correct / wrong" in tt, tt[-600:]
                assert "M_w (weighted pool)" in mm["predictions"] and "V1_rc10_P3" in mm["predictions"]
            if tier == "p1":
                assert "Verdict lines" in tt and "R11x2" in tt and "6057" in tt and "peak live" in tt, tt[:400]
            if tier == "p2":
                assert "NULL (identical to REF0)" in tt and "placement check" in tt, tt
            print(tt[-700:])
        test_unresolved_tier_gives_no_verdict()
        print("ALL TESTS PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unresolved_tier_gives_no_verdict():
    """All jobs QUEUED -> 'no outcomes yet', no hypothesis/model evaluated (regression: vacuous 'no wrong prediction')."""
    import tiers_v2 as T2
    tmp = tempfile.mkdtemp()
    try:
        for tier, sub in (("v1", "v1"),):
            mf = [f for f in os.listdir(os.path.join(HERE, "data", sub)) if f.startswith("manifest_")][0]
            man = json.load(open(os.path.join(HERE, "data", sub, mf)))
            res = {r["name"]: {"status": "QUEUED", "code": None, "message": ""} for r in man["jobs"]}
            txt = T2.analyse_acc(man, res, tmp)
            assert "no outcomes yet" in txt and "Hypotheses with no wrong prediction" not in txt, txt[-400:]
            txt = T2.analyse_v1(man, res, tmp)
            assert "no outcomes yet" in txt and "model(s) with no wrong prediction" not in txt and "NO frozen model" not in txt, txt[-400:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
