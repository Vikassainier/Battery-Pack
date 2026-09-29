"""Unit conversions and physical constants (SI unless the name says otherwise)."""
from __future__ import annotations

G = 9.80665                 # m/s^2 standard gravity
AIR_DENSITY_ISA = 1.225     # kg/m^3, ISA sea level 15 degC
AIR_CP = 1005.0             # J/(kg K)
KELVIN = 273.15


def c_to_k(t_c: float) -> float:
    return t_c + KELVIN


def kmh_to_ms(v_kmh):
    return v_kmh / 3.6


def ms_to_kmh(v_ms):
    return v_ms * 3.6


def mohm_to_ohm(r_mohm):
    return r_mohm * 1e-3


def ohm_to_mohm(r_ohm):
    return r_ohm * 1e3


def kw_to_w(p_kw):
    return p_kw * 1e3


def w_to_kw(p_w):
    return p_w * 1e-3


def j_to_kwh(e_j):
    return e_j / 3.6e6


def mm_to_m(x_mm):
    return x_mm * 1e-3


def mm2_to_m2(x):
    return x * 1e-6


def kgs_to_lpm(m_dot_kg_s: float, rho_kg_m3: float) -> float:
    """Mass flow [kg/s] -> volumetric flow [L/min]."""
    return m_dot_kg_s / rho_kg_m3 * 1000.0 * 60.0


def lpm_to_kgs(q_lpm: float, rho_kg_m3: float) -> float:
    """Volumetric flow [L/min] -> mass flow [kg/s]."""
    return q_lpm / 60.0 / 1000.0 * rho_kg_m3


def lpm_to_m3s(q_lpm: float) -> float:
    return q_lpm / 60.0 / 1000.0


def m3s_to_lpm(q_m3s: float) -> float:
    return q_m3s * 1000.0 * 60.0


def pa_to_bar(p_pa: float) -> float:
    return p_pa / 1e5


def pa_to_kpa(p_pa: float) -> float:
    return p_pa / 1e3
