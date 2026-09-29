"""Static charts for the PDF report (matplotlib, Agg backend). Each function returns PNG bytes or None if the data is absent."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

BLUE, ORANGE, GREEN, RED, PURPLE, GREY = "#0b6fb8", "#d9822b", "#2a9d6f", "#c0392b", "#a0459b", "#6b7a8f"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 7.5, "axes.titlesize": 8.5, "axes.titleweight": "bold", "axes.labelsize": 7.5,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": "#e2e7ee", "grid.linewidth": 0.6,
    "axes.edgecolor": "#8b97a8", "legend.fontsize": 6.8, "legend.frameon": False, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "figure.dpi": 100, "lines.linewidth": 1.1,
})


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=190, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def _has(res, *keys):
    s = res.get("series", {})
    return all(k in s and s[k] for k in keys)


def fig_electrical(res: dict):
    s = res["series"]
    t = np.array(s["t"])
    fig, ax = plt.subplots(2, 2, figsize=(7.4, 4.6))
    a = ax[0, 0]
    a.plot(t, s["i_pack_a"], color=BLUE, label="pack")
    a.plot(t, s["i_module_a"], color=GREEN, label="module", lw=0.9)
    a.plot(t, s["i_cell_a"], color=ORANGE, label="cell", lw=0.9)
    a.set(title="Graph 1 · Battery current vs time", xlabel="time [s]", ylabel="current [A]")
    a.legend(ncol=3)
    ax[0, 1].plot(t, s["soc_pct"], color=BLUE)
    ax[0, 1].set(title="Graph 2 · SOC vs time", xlabel="time [s]", ylabel="SOC [%]")
    ax[1, 0].plot(t, s["c_rate"], color=PURPLE)
    ax[1, 0].axhline(0, color="#8b97a8", lw=0.6)
    ax[1, 0].set(title="Graph 3 · C-rate vs time", xlabel="time [s]", ylabel="cell C-rate [C]")
    a = ax[1, 1]
    a.plot(t, s["p_batt_kw"], color=BLUE, label="battery terminal power")
    if s.get("p_aux_kw"):
        a.plot(t, s["p_aux_kw"], color=ORANGE, lw=0.9, label="auxiliary")
    a.axhline(0, color="#8b97a8", lw=0.6)
    a.set(title="Graph 7 · Battery power vs time", xlabel="time [s]", ylabel="power [kW]")
    a.legend()
    fig.tight_layout(h_pad=1.6, w_pad=1.6)
    return _png(fig)


def fig_heat(res: dict):
    s = res["series"]
    t = np.array(s["t"])
    d = res["design"]
    fig, ax = plt.subplots(2, 2, figsize=(7.4, 4.6))
    a = ax[0, 0]
    a.plot(t, s["q_cell_w"], color=RED, label="total")
    a.plot(t, s["q_joule_cell_w"], color=BLUE, lw=0.9, label="Joule I²R")
    a.plot(t, s["q_rev_cell_w"], color=GREEN, lw=0.9, label="entropic")
    a.set(title="Graph 4 · Cell heat generation vs time", xlabel="time [s]", ylabel="heat per cell [W]")
    a.legend(ncol=3)
    a = ax[0, 1]
    a.plot(t, s["q_pack_kw"], color=RED, lw=0.9, label="pack heat")
    c = d["candidates"]
    lines = [(c["peak"]["value_w"], "peak", RED, ":"), (d["q_design_w"], "design (×SF)", BLUE, "-")]
    if c["moving_average"]["available"]:
        lines.append((c["moving_average"]["value_w"], "moving avg", ORANGE, "--"))
    if c["sustained"]["available"]:
        lines.append((c["sustained"]["value_w"], "sustained", PURPLE, "--"))
    for v, name, col, ls in lines:
        a.axhline(v / 1000, color=col, ls=ls, lw=1.0, label=f"{name} {v / 1000:.2f} kW")
    a.set(title="Graph 5 · Pack heat generation vs time", xlabel="time [s]", ylabel="pack heat [kW]")
    a.legend(ncol=1, loc="upper right")
    a = ax[1, 0]
    a.plot(t, s["cum_heat_kwh"], color=RED, label="total heat")
    a.plot(t, s["cum_joule_kwh"], color=BLUE, lw=0.9, label="Joule part")
    a.set(title="Graph 6 · Cumulative heat generation", xlabel="time [s]", ylabel="energy [kWh]")
    a.legend()
    a = ax[1, 1]
    if s.get("q_cool_kw"):
        a.plot(t, s["q_pack_kw"], color=RED, lw=0.9, label="heat generated")
        a.plot(t, s["q_cool_kw"], color=BLUE, lw=0.9, label="removed by coolant")
        if s.get("q_amb_kw"):
            a.plot(t, s["q_amb_kw"], color=GREEN, lw=0.8, label="lost to ambient")
        a.set(title="Generation vs removal (thermal accumulation)", xlabel="time [s]", ylabel="power [kW]")
        a.legend()
    else:
        a.axis("off")
    fig.tight_layout(h_pad=1.6, w_pad=1.6)
    return _png(fig)


def fig_temperature(res: dict, target_c: float | None = None):
    s = res["series"]
    if not (s.get("t_cell_c") and (s.get("t_hot_c") or s.get("t_uncooled_c"))):
        return None
    t = np.array(s["t"])
    fig, a = plt.subplots(figsize=(7.4, 2.8))
    if s.get("t_hot_c"):
        a.plot(t, s["t_hot_c"], color=RED, label="hottest cell (predicted)")
        a.plot(t, s["t_cell_c"], color=BLUE, label="average cell")
        if s.get("t_coolant_out_c") and any(v is not None for v in s["t_coolant_out_c"]):
            a.plot(t, [np.nan if v is None else v for v in s["t_coolant_out_c"]], color=GREEN, ls=":", label="coolant outlet")
    if s.get("t_uncooled_c"):
        a.plot(t, s["t_uncooled_c"], color=GREY, ls="--", label="without active cooling")
    if target_c is not None:
        a.axhline(target_c, color=ORANGE, ls="--", lw=1.0, label=f"target {target_c:g} °C")
    a.set(title="Predicted temperatures", xlabel="time [s]", ylabel="temperature [°C]")
    a.legend(ncol=3)
    fig.tight_layout()
    return _png(fig)


def fig_design_levels(res: dict):
    d = res["design"]
    c = d["candidates"]
    names = {"peak": "Peak heat load", "moving_average": "Moving-average", "sustained": "Sustained", "drive_cycle": "Drive-cycle"}
    items = [(names[k], c[k]["value_w"] / 1000, k == d["philosophy"]) for k in names if c[k]["available"]]
    items.append(("Design load (× SF)", d["q_design_w"] / 1000, None))
    fig, a = plt.subplots(figsize=(7.4, 2.2))
    y = np.arange(len(items))
    cols = [GREEN if sel is None else (BLUE if sel else "#9db3c9") for _, _, sel in items]
    a.barh(y, [v for _, v, _ in items], color=cols)
    a.set_yticks(y, [n for n, _, _ in items])
    for yi, (_, v, _) in zip(y, items):
        a.text(v, yi, f" {v:.3g} kW", va="center", fontsize=7)
    a.invert_yaxis()
    a.set(title="Design heat-load candidates", xlabel="kW")
    fig.tight_layout()
    return _png(fig)


def fig_resistance_chain(res: dict):
    cp = res.get("cold_plate")
    if not cp:
        return None
    parts = [("Contact", cp["r_contact"], ORANGE), ("TIM", cp["r_tim"], PURPLE), ("Plate", cp["r_plate"], GREY), ("Convection", cp["r_conv"], BLUE)]
    fig, a = plt.subplots(figsize=(7.4, 1.5))
    left = 0.0
    for n, r, c in parts:
        a.barh([0], [r], left=left, color=c, label=f"{n} {r:.4f} K/W ({r / cp['r_total'] * 100:.1f} %)")
        left += r
    a.set_yticks([])
    a.set(title=f"Thermal resistance chain per cell - R_total = {cp['r_total']:.4f} K/W", xlabel="K/W")
    a.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.45))
    a.grid(axis="y", visible=False)
    fig.tight_layout()
    return _png(fig)


def fig_cell_curves(cell: dict):
    panels = []
    for key, title, xl, yl in (("r_vs_soc", "Resistance vs SOC", "SOC [%]", "R [mΩ]"), ("r_vs_temp", "Resistance vs temperature", "T [°C]", "R [mΩ]"),
                               ("ocv_vs_soc", "OCV vs SOC", "SOC [%]", "OCV [V]"), ("dudt_vs_soc", "dU/dT vs SOC", "SOC [%]", "dU/dT [mV/K]"),
                               ("capacity_vs_temp", "Capacity vs temperature", "T [°C]", "capacity [% nominal]")):
        c = cell.get(key)
        if c:
            panels.append((title, xl, yl, c["x"], c["y"]))
    rmap = cell.get("r_map")
    if not panels and not rmap:
        return None
    n = len(panels) + (1 if rmap else 0)
    cols = 3 if n > 2 else n
    rows = int(np.ceil(n / cols))
    fig, axs = plt.subplots(rows, cols, figsize=(7.4, 2.3 * rows), squeeze=False)
    axs = axs.ravel()
    for a, (title, xl, yl, x, y) in zip(axs, panels):
        a.plot(x, y, "o-", color=BLUE, ms=2.5)
        a.set(title=title, xlabel=xl, ylabel=yl)
    if rmap:
        a = axs[len(panels)]
        z = np.array(rmap["z"])
        im = a.imshow(z, aspect="auto", origin="lower", cmap="viridis", extent=[rmap["y"][0], rmap["y"][-1], rmap["x"][0], rmap["x"][-1]])
        a.set(title="Resistance map R(SOC,T) [mΩ]", xlabel="T [°C]", ylabel="SOC [%]")
        a.grid(False)
        fig.colorbar(im, ax=a, fraction=0.05)
    for a in axs[n:]:
        a.axis("off")
    fig.tight_layout()
    return _png(fig)


def fig_cycle(load: dict | None):
    if not load:
        return None
    t = np.array(load["t"])
    fig, ax = plt.subplots(2 if load.get("speed_kmh") else 1, 1, figsize=(7.4, 3.6 if load.get("speed_kmh") else 2.2), squeeze=False)
    ax = ax.ravel()
    i = 0
    if load.get("speed_kmh"):
        ax[0].plot(t, load["speed_kmh"], color=GREY)
        ax[0].set(title="Vehicle speed", ylabel="km/h")
        i = 1
    if load["kind"] == "power":
        ax[i].fill_between(t, load["p_discharge_kw"], color=BLUE, alpha=0.7, label="discharge")
        ax[i].fill_between(t, load["p_regen_kw"], color=GREEN, alpha=0.7, label="charge / regen")
        if load.get("p_aux_kw"):
            ax[i].plot(t, load["p_aux_kw"], color=ORANGE, lw=0.9, label="auxiliary")
        ax[i].set(title="Battery power", ylabel="kW", xlabel="time [s]")
    else:
        ax[i].plot(t, load["i_pack_a"], color=BLUE, label="pack current")
        ax[i].set(title="Pack current", ylabel="A", xlabel="time [s]")
    ax[i].legend(ncol=3)
    fig.tight_layout()
    return _png(fig)


def fig_tornado(sens: dict):
    if not sens or not sens.get("ok"):
        return None
    outs = sens["outputs"]
    fig, axs = plt.subplots(2, 2, figsize=(7.4, 5.4))
    for a, o in zip(axs.ravel(), outs):
        items = sens["tornado"][o["key"]][:10][::-1]
        y = np.arange(len(items))
        a.barh(y, [i["high"] for i in items], color=BLUE)
        a.barh(y, [i["low"] for i in items], color="#9db3c9")
        a.set_yticks(y, [i["label"] for i in items], fontsize=6.5)
        a.axvline(0, color="#8b97a8", lw=0.7)
        a.set(title=f"{o['label']} [{o['unit']}]", xlabel=f"Δ {o['unit']} vs base")
    fig.tight_layout(h_pad=1.5, w_pad=1.2)
    return _png(fig)
