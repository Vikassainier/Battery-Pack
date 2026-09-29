"""Cold-plate hydraulics: velocity, Reynolds number, friction factor, pressure drop and pumping power.

    ΔP_channel = f_D · (L/D_h) · ½ρv²          ΔP_minor = K · ½ρv²          ΔP_plate = ΔP_channel + ΔP_minor
    parallel plates: pack ΔP = ΔP_plate (each plate sees ṁ/n) ;  series plates: pack ΔP = n_plates · ΔP_plate (each sees ṁ)
    ΔP_total = ΔP_plates + ΔP_external (hoses, chiller, radiator, fittings)
    P_hyd = ΔP_total · V̇ ;      P_el = P_hyd / η_pump
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .channel import RectChannel, flow_regime, friction_factor, reynolds
from .coolant import CoolantProps
from .schemas import ColdPlateSpec, LimitSettings
from .trace import TraceLog, fmt
from .units import kgs_to_lpm, lpm_to_m3s


@dataclass
class Hydraulics:
    velocity_m_s: float
    dh_m: float
    reynolds: float
    regime: str
    f_darcy: float
    dp_channel_pa: float
    dp_minor_pa: float
    dp_plate_pa: float
    dp_plates_total_pa: float
    dp_external_pa: float
    dp_total_pa: float
    q_pack_lpm: float
    q_plate_lpm: float
    p_hyd_w: float
    p_elec_w: float
    head_m: float
    flags: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(dp_plate_kpa=self.dp_plate_pa / 1e3, dp_total_kpa=self.dp_total_pa / 1e3, dp_total_bar=self.dp_total_pa / 1e5)
        return d


def hydraulics(plate: ColdPlateSpec, props: CoolantProps, m_dot_pack: float, pump_eff: float, limits: LimitSettings,
               tr: TraceLog | None = None) -> Hydraulics:
    ch = RectChannel(plate.channel_width_mm * 1e-3, plate.channel_height_mm * 1e-3, plate.channel_length_mm * 1e-3)
    m_plate = m_dot_pack / plate.n_plates if plate.plate_arrangement == "parallel" else m_dot_pack
    m_ch = m_plate / plate.n_channels
    v = m_ch / (props.rho * ch.area)
    re = reynolds(props.rho, v, ch.dh, props.mu)
    f = friction_factor(re, ch.alpha, plate.roughness_um * 1e-6 / ch.dh)
    q_dyn = 0.5 * props.rho * v * v
    dp_ch = f * ch.length_m / ch.dh * q_dyn
    dp_minor = plate.minor_loss_k * q_dyn
    dp_plate = dp_ch + dp_minor
    dp_plates = dp_plate * (plate.n_plates if plate.plate_arrangement == "series" else 1)
    dp_ext = plate.external_dp_kpa * 1e3
    dp_tot = dp_plates + dp_ext
    q_pack_lpm = kgs_to_lpm(m_dot_pack, props.rho)
    q_plate_lpm = kgs_to_lpm(m_plate, props.rho)
    p_hyd = dp_tot * lpm_to_m3s(q_pack_lpm)
    p_el = p_hyd / pump_eff
    head = dp_tot / (props.rho * 9.80665)

    flags: list[dict] = []
    fl = lambda code, sev, msg: flags.append({"code": code, "severity": sev, "message": msg})  # noqa: E731
    if v > limits.max_velocity_fail_m_s:
        fl("HYD_VELOCITY_EXCESSIVE", "fail", f"Channel velocity {v:.2f} m/s exceeds {limits.max_velocity_fail_m_s:g} m/s (erosion, noise, pressure drop).")
    elif v > limits.max_velocity_warn_m_s:
        fl("HYD_VELOCITY_HIGH", "warning", f"Channel velocity {v:.2f} m/s exceeds {limits.max_velocity_warn_m_s:g} m/s.")
    elif v < 0.05:
        fl("HYD_VELOCITY_LOW", "warning", f"Channel velocity {v:.3f} m/s is very low: risk of air entrapment and poor distribution.")
    if dp_plates / 1e3 > limits.plate_dp_fail_kpa:
        fl("HYD_PLATE_DP_EXCESSIVE", "fail", f"Cold-plate pressure drop {dp_plates / 1e3:.1f} kPa exceeds {limits.plate_dp_fail_kpa:g} kPa.")
    elif dp_plates / 1e3 > limits.plate_dp_warn_kpa:
        fl("HYD_PLATE_DP_HIGH", "warning", f"Cold-plate pressure drop {dp_plates / 1e3:.1f} kPa exceeds {limits.plate_dp_warn_kpa:g} kPa.")
    if dp_tot / 1e3 > limits.loop_dp_fail_kpa:
        fl("HYD_LOOP_DP_EXCESSIVE", "fail", f"Total loop pressure drop {dp_tot / 1e3:.0f} kPa exceeds {limits.loop_dp_fail_kpa:g} kPa: unrealistic for an automotive coolant pump.")
    elif dp_tot / 1e3 > limits.loop_dp_warn_kpa:
        fl("HYD_LOOP_DP_HIGH", "warning", f"Total loop pressure drop {dp_tot / 1e3:.0f} kPa exceeds {limits.loop_dp_warn_kpa:g} kPa.")
    if q_pack_lpm > limits.flow_fail_lpm:
        fl("HYD_FLOW_EXCESSIVE", "fail", f"Pack coolant flow {q_pack_lpm:.1f} L/min exceeds {limits.flow_fail_lpm:g} L/min - unrealistic.")
    elif q_pack_lpm > limits.flow_warn_lpm:
        fl("HYD_FLOW_HIGH", "warning", f"Pack coolant flow {q_pack_lpm:.1f} L/min exceeds {limits.flow_warn_lpm:g} L/min.")
    elif q_pack_lpm < limits.flow_min_lpm:
        fl("HYD_FLOW_LOW", "warning", f"Pack coolant flow {q_pack_lpm:.2f} L/min is below {limits.flow_min_lpm:g} L/min - unrealistically small.")
    regime = flow_regime(re)
    if regime == "transitional":
        fl("HYD_TRANSITION", "warning", f"Re = {re:.0f} lies in the laminar-turbulent transition (2300-4000): unstable, correlations uncertain.")
    if regime == "laminar" and plate.n_channels >= 6:
        fl("HYD_MALDISTRIBUTION_RISK", "warning",
           f"Laminar flow in {plate.n_channels} parallel channels: flow is sensitive to viscosity and manifold design - cooling distribution may be non-uniform; verify with a 1-D network or CFD model.")
    elif plate.n_channels >= 8 and dp_plate < 5e3:
        fl("HYD_MALDISTRIBUTION_RISK", "warning",
           f"Channel pressure drop ({dp_plate / 1e3:.1f} kPa) is small compared with typical manifold losses: cooling distribution may be non-uniform.")
    if plate.n_plates > 1 and plate.plate_arrangement == "parallel":
        fl("HYD_PLATE_BALANCE", "info", f"{plate.n_plates} plates in parallel: flow balance between plates depends on the manifold; a {plate.n_plates}-way split is assumed even.")

    h = Hydraulics(v, ch.dh, re, regime, f, dp_ch, dp_minor, dp_plate, dp_plates, dp_ext, dp_tot, q_pack_lpm, q_plate_lpm, p_hyd, p_el, head, flags)
    if tr is not None:
        tr.calc("hyd.f", "Darcy friction factor", f, "-", "f·Re = 96(1−1.3553α+…) laminar ; Haaland turbulent ; blend in transition", f"Re = {fmt(re)}, α = {fmt(ch.alpha)}", ["cp.re"] if "cp.re" in tr.nodes else [])
        tr.calc("hyd.dp_ch", "Channel pressure drop", dp_ch / 1e3, "kPa", "ΔP = f·(L/D_h)·½ρv²", f"{fmt(f)} × {fmt(ch.length_m / ch.dh)} × {fmt(q_dyn)} Pa", ["hyd.f", "cp.v"] if "cp.v" in tr.nodes else ["hyd.f"])
        tr.calc("hyd.dp_minor", "Minor losses", dp_minor / 1e3, "kPa", "ΔP = K·½ρv²", f"{fmt(plate.minor_loss_k)} × {fmt(q_dyn)} Pa", [])
        tr.calc("hyd.dp_plates", "Cold-plate pressure drop", dp_plates / 1e3, "kPa", "ΔP_plates = (ΔP_channel + ΔP_minor) × (n_plates if series)", f"({fmt(dp_ch)} + {fmt(dp_minor)}) Pa", ["hyd.dp_ch", "hyd.dp_minor"])
        tr.input("hyd.dp_ext", "External loop pressure drop", plate.external_dp_kpa, "kPa", "assumed", "hoses, chiller, radiator, fittings")
        tr.result("hyd.dp_total", "Total pressure drop", dp_tot / 1e5, "bar", "ΔP_total = ΔP_plates + ΔP_external", f"{fmt(dp_plates / 1e3)} + {fmt(plate.external_dp_kpa)} kPa", ["hyd.dp_plates", "hyd.dp_ext"])
        tr.calc("hyd.p_hyd", "Hydraulic power", p_hyd, "W", "P_hyd = ΔP·V̇", f"{fmt(dp_tot)} Pa × {fmt(q_pack_lpm / 60000)} m³/s", ["hyd.dp_total"])
        tr.result("hyd.p_el", "Estimated pump electrical power", p_el, "W", "P_el = P_hyd/η_pump", f"{fmt(p_hyd)} / {fmt(pump_eff)}", ["hyd.p_hyd"])
    return h
