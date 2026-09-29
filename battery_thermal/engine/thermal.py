"""Lumped transient thermal model of the pack (Phase 5).

    C · dT/dt = Q_gen − G_c·(T − T_in) − G_a·(T − T_amb)

    C   = N·m_cell·cp + C_extra                thermal capacity of the pack (uniform temperature)
    G_c = ε·ṁ·cp,cool                          effective coolant conductance (ε-NTU with an isothermal wall)
    G_a = UA to ambient

With sample-and-hold heat over a step the equation is linear with constant coefficients, so it is integrated
*exactly*:   T(t+Δt) = T_eq + (T − T_eq)·exp(−Δt/τ),   T_eq = (Q + G_c·T_in + G_a·T_amb)/(G_c + G_a),   τ = C/(G_c+G_a)
and the step-mean temperature (used for removed-heat bookkeeping, so energy closes to round-off) is
    T̄ = T_eq + (T − T_eq)·(τ/Δt)·(1 − exp(−Δt/τ))
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ThermalNetwork:
    c_pack_j_k: float                       # total thermal capacity [J/K]
    g_cool_w_k: float = 0.0                 # ε·ṁ·cp (effective coolant conductance) [W/K]
    t_in_c: float = 25.0                    # coolant inlet temperature
    g_amb_w_k: float = 0.0                  # pack-to-ambient conductance [W/K]
    t_amb_c: float = 25.0
    eps: float = 0.0                        # effectiveness (for the coolant outlet temperature)
    m_cp_w_k: float = 0.0                   # ṁ·cp,cool [W/K]

    @property
    def g_total(self) -> float:
        return self.g_cool_w_k + self.g_amb_w_k

    @property
    def tau_s(self) -> float:
        return self.c_pack_j_k / self.g_total if self.g_total > 0 else math.inf


def thermal_step(t_c: float, q_w: float, dt: float, net: ThermalNetwork) -> tuple[float, float, float, float]:
    """Advance the pack temperature one step (exact for constant heat over the step).

    Returns (T_next, T_mean_over_step, mean coolant removal [W], mean ambient loss [W]).
    """
    g = net.g_total
    if dt <= 0.0:
        return t_c, t_c, net.g_cool_w_k * (t_c - net.t_in_c), net.g_amb_w_k * (t_c - net.t_amb_c)
    if g <= 0.0:
        t_next = t_c + q_w * dt / net.c_pack_j_k
        return t_next, 0.5 * (t_c + t_next), 0.0, 0.0
    t_eq = (q_w + net.g_cool_w_k * net.t_in_c + net.g_amb_w_k * net.t_amb_c) / g
    tau = net.c_pack_j_k / g
    x = dt / tau
    e = math.exp(-x)
    t_next = t_eq + (t_c - t_eq) * e
    t_mean = t_eq + (t_c - t_eq) * ((1.0 - e) / x if x > 1e-12 else 1.0)
    return t_next, t_mean, net.g_cool_w_k * (t_mean - net.t_in_c), net.g_amb_w_k * (t_mean - net.t_amb_c)
