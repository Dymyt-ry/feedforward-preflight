# SPDX-License-Identifier: Apache-2.0
"""Small example circuits for `python dccheck.py dccheck_examples:<name>` (self-contained: plain qiskit, no suite needed)."""
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit.circuit.classical import expr


def many_small_registers(n_regs=12, width=2):
    """n_regs measured registers of `width` bits, XOR-ed into S, one `if S[0]`. Rejected (6057) on ibm_marrakesh for n_regs = 12 (13 live registers); the verdict comes from the dominance envelope."""
    q = QuantumRegister(n_regs * width + 1, "q")
    out = ClassicalRegister(1, "out")
    regs = [ClassicalRegister(width, f"G{i}") for i in range(n_regs)]
    S = ClassicalRegister(width, "S")
    qc = QuantumCircuit(q, out, *regs, S, name=f"R{n_regs}x{width}")
    for i, r in enumerate(regs):
        for j in range(width):
            qc.h(q[i * width + j])
            qc.measure(q[i * width + j], r[j])
    e = expr.bit_xor(regs[0], regs[1])
    for r in regs[2:]:
        e = expr.bit_xor(e, r)
    qc.store(S, e)
    with qc.if_test(expr.lift(S[0])):
        qc.x(q[n_regs * width])
    qc.measure(q[n_regs * width], out[0])
    return qc


def ten_registers():
    """Same shape with 10 registers (accepted on ibm_marrakesh, round 4)."""
    return many_small_registers(10, 2)


def eleven_registers():
    """11 registers (accepted 3/3 on ibm_marrakesh, round 4)."""
    return many_small_registers(11, 2)
