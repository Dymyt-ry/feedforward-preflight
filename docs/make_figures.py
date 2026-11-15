# SPDX-License-Identifier: Apache-2.0
"""Regenerate docs/img/*.svg and *.png from the shipped data files (no IBM access).

    python docs/make_figures.py

Every plotted number is read from: data/latency_fit.json, data/p2/RESULTS_QPU_p2.md, rules/envelope_*.json,
data/probe_outcomes_fixpack_v*.json. Each figure is written in a light and a dark variant (transparent background) as
SVG (+ PNG fallback), for use with <picture> and prefers-color-scheme. Output is deterministic apart from SVG metadata (stripped).
"""
import glob
import json
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMG = os.path.join(HERE, "img")

THEMES = {
    "light": dict(fg="#24292f", mute="#6e7781", acc="#0969da", rej="#cf222e", oos="#8250df", ok="#1a7f37", warn="#9a6700", box="#ddf4ff", grid="#d0d7de"),
    "dark": dict(fg="#e6edf3", mute="#8b949e", acc="#58a6ff", rej="#ff7b72", oos="#d2a8ff", ok="#56d364", warn="#e3b341", box="#12304d", grid="#30363d"),
}


def J(*p):
    return json.load(open(os.path.join(ROOT, *p)))


def rules():
    return J("rules", sorted(os.path.basename(f) for f in glob.glob(os.path.join(ROOT, "rules", "envelope_*.json")))[-1])


def style(c):
    plt.rcParams.update({
        "svg.hashsalt": "feedforward-preflight", "svg.fonttype": "path", "font.family": "DejaVu Sans", "font.size": 10,
        "text.color": c["fg"], "axes.labelcolor": c["fg"], "axes.edgecolor": c["mute"], "xtick.color": c["mute"], "ytick.color": c["mute"],
        "axes.facecolor": "none", "figure.facecolor": "none", "savefig.facecolor": "none", "savefig.transparent": True,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": c["grid"], "grid.linewidth": 0.6,
    })


def save(fig, name, theme):
    fig.savefig(os.path.join(IMG, f"{name}-{theme}.svg"), format="svg", metadata={"Date": None, "Creator": None}, bbox_inches="tight")
    fig.savefig(os.path.join(IMG, f"{name}-{theme}.png"), format="png", dpi=160, metadata={"Software": None}, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------- (a) latency
def fig_latency(theme):
    c = THEMES[theme]
    style(c)
    fit = J("data", "latency_fit.json")
    rj = rules()
    ops = {p["name"].split(":")[1]: p["vector"]["max_store_ops"] for p in rj["probes"] if p["name"].startswith("r1:")}
    pts = fit["points"]
    b, a = fit["fits"][0]["coef"][0], fit["fits"][0]["coef"][1]          # model "ops": [intercept, slope]
    b_se, a_se = fit["fits"][0]["se_scaled"][0], fit["fits"][0]["se_scaled"][1]
    p2 = open(os.path.join(ROOT, "data", "p2", "RESULTS_QPU_p2.md")).read()
    nul = re.search(r"NULL \(identical to REF0\) increment: \+(\d+) \+/- (\d+) ns", p2)
    null_v, null_e = int(nul.group(1)), int(nul.group(2))
    lo, hi = min(ops[k] for k in pts), max(ops[k] for k in pts)
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.axvspan(lo, hi, color=c["acc"], alpha=0.08, lw=0)
    ax.text(lo + 0.6, 60, f"measured range: {lo} to {hi} ops", ha="left", color=c["mute"], fontsize=9)
    xs = np.array([0, 70])
    ax.plot(xs, b + a * xs, ls=(0, (4, 3)), color=c["mute"], lw=1.2, label=f"fit: {b:.0f} + {a:.1f} ns x ops")
    nudge = {"XORC15": -0.9, "XORT15": 0.9}
    for k, v in pts.items():
        ax.errorbar(ops[k] + nudge.get(k, 0), v["huber"], yerr=v["se"], fmt="o", color=c["acc"], ms=6, capsize=3, lw=1.4)
        ax.annotate(k, (ops[k] + nudge.get(k, 0), v["huber"]), textcoords="offset points", xytext=(6, -12 if k == "XORT15" else 6), fontsize=8, color=c["fg"])
    ax.set_xlim(0, 70)
    ax.set_ylim(0, 2400)
    ax.set_xlabel("operations in the stored expression")
    ax.set_ylabel("extra latency per decision (ns)")
    ax.text(0.03, 0.95, f"about {a:.0f} ns per op (slope {a:.1f} +/- {a_se:.1f} in this fit), not a law\nnull-circuit offset: +{null_v} +/- {null_e} ns",
            transform=ax.transAxes, va="top", fontsize=9, color=c["fg"])
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    ax.set_title("Latency of a classical expression on ibm_marrakesh", loc="left", fontsize=11)
    fig.text(0.01, -0.02, "Points: Huber estimate over probe qubits; bars: leave-one-probe-out jackknife (probe scatter). Chain/tree XOR nudged apart in x.", fontsize=7.5, color=c["mute"])
    save(fig, "latency", theme)


# ---------------------------------------------------------------- (b) 6057 map
def fig_map(theme):
    c = THEMES[theme]
    style(c)
    rj = rules()
    rng = np.random.default_rng(0)
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    groups = {"accept": [], "reject6057": [], "other6066": []}
    oos = {"accept": [], "reject6057": []}
    named = {}
    for p in rj["probes"]:
        x, y = p["vector"]["peak_live_regs"], p["vector"]["lr_stack"]
        named[p["name"]] = (x, y)
        pref = p["name"].split(":")[0]
        if p["class_"] in oos and pref in ("e1", "e2", "v1"):
            oos[p["class_"]].append((x, y))
        else:
            groups[p["class_"]].append((x, y))

    def jit(pts):
        a = np.array(pts, float)
        return a + rng.uniform(-0.17, 0.17, a.shape)
    ax.scatter(*jit(groups["accept"]).T, s=26, color=c["acc"], alpha=0.75, lw=0, label=f"accepted ({len(groups['accept'])})")
    ax.scatter(*jit(groups["reject6057"]).T, s=26, color=c["rej"], alpha=0.8, lw=0, marker="s", label=f"rejected, error 6057 ({len(groups['reject6057'])})")
    if groups["other6066"]:
        ax.scatter(*jit(groups["other6066"]).T, s=34, color=c["warn"], marker="x", lw=1.5, label=f"rejected, error 6066 ({len(groups['other6066'])})")
    ax.scatter(*jit(oos["accept"]).T, s=70, facecolor="none", edgecolor=c["oos"], marker="D", lw=1.6, label=f"out-of-sample (e1/e2/v1), accepted ({len(oos['accept'])})")
    ax.scatter(*jit(oos["reject6057"]).T, s=70, facecolor="none", edgecolor=c["rej"], marker="D", lw=1.6, label=f"out-of-sample (e1/e2/v1), rejected ({len(oos['reject6057'])})")
    sx, sy = named["r4:P1_staged"]
    rx, ry = named["v1:V1_rc4_P10"]
    ax.annotate("P1_staged: 12-register cone, accepted\n(measured in two stages)", (sx, sy), xytext=(15.5, 5.0), fontsize=8, color=c["fg"],
                arrowprops=dict(arrowstyle="-", color=c["mute"], lw=0.8), ha="left")
    ax.annotate("V1_rc4_P10: 10 registers, rejected", (rx, ry), xytext=(16.5, 7.4), fontsize=8, color=c["fg"],
                arrowprops=dict(arrowstyle="-", color=c["mute"], lw=0.8))
    ax.set_xlabel("peak live registers")
    ax.set_ylabel("left-to-right stack need")
    ax.set_title("Where error 6057 appears (two of the envelope's dimensions)", loc="left", fontsize=11)
    ax.legend(frameon=False, fontsize=7.5, loc="upper right")
    n_dim = len(rj["dimensions"])
    fig.text(0.01, -0.02, f"Projection of {len(rj['probes'])} probes onto 2 of {n_dim} dimensions; points jittered by up to 0.17 so that overlapping probes show. The verdict uses all {n_dim} vectors.",
             fontsize=7.5, color=c["mute"])
    save(fig, "map6057", theme)


# ---------------------------------------------------------------- (c) fix-pack
def fig_fixpack(theme):
    c = THEMES[theme]
    style(c)
    v2 = [r for r in J("data", "probe_outcomes_fixpack_v2.json")["rounds"]["fixpack_v2"]]
    v1 = [r for r in J("data", "probe_outcomes_fixpack_v1.json")["rounds"]["fixpack_v1"]]
    n2, k2 = len(v2), sum(r["status"] == "DONE" for r in v2)
    bad1 = [r for r in v1 if r["error_code"] == 6066]
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    ax.axis("off")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 50)
    for i in range(12):
        x = 1 + (i % 6) * 6.2
        y = 33 - (i // 6) * 9
        ax.add_patch(FancyBboxPatch((x, y), 5.4, 6.5, boxstyle="round,pad=0.1,rounding_size=0.8", fc="none", ec=c["rej"], lw=1.3))
        ax.text(x + 2.7, y + 3.25, f"G{i}", ha="center", va="center", fontsize=7.5)
    ax.text(19, 45.5, "12 registers of 8 bits", ha="center", fontsize=10)
    ax.text(19, 17, "FAIL: error 6057 (B_R12x8c)", ha="center", fontsize=9.5, color=c["rej"])
    ax.add_patch(FancyArrowPatch((41, 36), (55, 36), arrowstyle="-|>", mutation_scale=16, color=c["fg"], lw=1.4))
    ax.text(48, 39, "dccheck --fix-pack", ha="center", fontsize=8)
    for k in range(3):
        y = 38 - k * 10.5
        ax.add_patch(FancyBboxPatch((60, y - 3.5), 38, 7.4, boxstyle="round,pad=0.1,rounding_size=0.8", fc="none", ec=c["acc"], lw=1.5))
        for j in range(4):
            ax.add_patch(Rectangle((60.6 + j * 9.35, y - 2.9), 9.0, 6.2, fc=c["box"], ec=c["acc"], lw=0.6))
            ax.text(65.1 + j * 9.35, y + 0.2, f"G{k * 4 + j}", ha="center", va="center", fontsize=7)
        ax.text(59, y + 0.2, f"pk{k}", ha="right", va="center", fontsize=8)
    ax.text(79, 45.5, "3 registers of 32 bits", ha="center", fontsize=10)
    ax.text(79, 6.5, f"packed circuits accepted on the QPU: {k2} of {n2} (v2, bit-level remap)", ha="center", fontsize=8.8, color=c["ok"])
    ax.text(79, 1.5, f"v1 (shift + narrowing cast): {len(bad1)} of {len(v1)} circuits got error 6066", ha="center", fontsize=8.5, color=c["warn"])
    ax.set_title("Fix-pack: many small measured registers into a few wide ones", loc="left", fontsize=11)
    save(fig, "fixpack", theme)


# ---------------------------------------------------------------- (d) pipeline
def fig_pipeline(theme):
    c = THEMES[theme]
    style(c)
    rj = rules()
    n_dim, n_pr = len(rj["dimensions"]), len(rj["probes"])
    fig, ax = plt.subplots(figsize=(11.5, 3.3))
    ax.axis("off")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 33)
    steps = [("Your circuit", "QPY file or\nmodule:func"), ("Self-transpile", "bundled FakeMarrakesh,\nopt. level 1"),
             ("Resource vector", f"{n_dim} dimensions\n(registers, stack, ops, ...)"), ("Rules", f"envelope of {n_pr} QPU\nprobes + IBM rules")]
    w, g = 15.5, 4.6
    for i, (t, s) in enumerate(steps):
        x = 1 + i * (w + g)
        ax.add_patch(FancyBboxPatch((x, 13), w, 14, boxstyle="round,pad=0.1,rounding_size=1.2", fc=c["box"], ec=c["acc"], lw=1.4))
        ax.text(x + w / 2, 23.3, t, ha="center", va="center", fontsize=9, fontweight="bold")
        ax.text(x + w / 2, 17.4, s, ha="center", va="center", fontsize=7, color=c["fg"])
        if i < 3:
            ax.add_patch(FancyArrowPatch((x + w + 0.4, 20), (x + w + g - 0.4, 20), arrowstyle="-|>", mutation_scale=14, color=c["mute"], lw=1.3))
    xr = 1 + 3 * (w + g) + w + 0.5
    ax.add_patch(FancyArrowPatch((xr, 20), (xr + 3.6, 20), arrowstyle="-|>", mutation_scale=14, color=c["mute"], lw=1.3))
    for lab, col, y in (("PASS", c["ok"], 27), ("UNCERTAIN", c["warn"], 20), ("FAIL", c["rej"], 13)):
        ax.add_patch(FancyBboxPatch((xr + 4.4, y - 2.6), 12.6, 5.2, boxstyle="round,pad=0.05,rounding_size=1", fc="none", ec=col, lw=1.6))
        ax.text(xr + 10.7, y, lab, ha="center", va="center", fontsize=8.8, color=col, fontweight="bold")
    ax.text(50, 6.5, "Every verdict names its witness probe (the measured circuit it rests on) and, for FAIL and UNCERTAIN, quotes IBM's error text.", ha="center", fontsize=9, color=c["fg"])
    ax.text(50, 2.5, "PASS = dominated by an accepted probe in every dimension; FAIL = at least as demanding as a rejected probe; otherwise UNCERTAIN.", ha="center", fontsize=8, color=c["mute"])
    save(fig, "pipeline", theme)


def main():
    os.makedirs(IMG, exist_ok=True)
    for theme in THEMES:
        fig_latency(theme)
        fig_map(theme)
        fig_fixpack(theme)
        fig_pipeline(theme)
    print("wrote", len(os.listdir(IMG)), "files to", IMG)


if __name__ == "__main__":
    main()
