"""Coolant properties.

Built-in correlations for water, ethylene-glycol/water and propylene-glycol/water mixtures (screening accuracy:
ρ, cp ≈ ±2 %, k ≈ ±5-10 %, μ ≈ ±10-15 %; replace with supplier data for final design - every property can be
overridden by the user, which is then recorded as a user-provided value).

Pure-component fits (T in °C):
  water   ρ = Kell,  cp = 4th-order polynomial (steam-table fit),  k = 0.5636 + 1.946e-3 T − 8.151e-6 T²,  μ = 2.414e-5·10^(247.8/(T+133.15))
  EG      ρ = 1125.4 − 0.61 T,  cp = 2310 + 3.7 T,  k = 0.254 + 2e-4 T,  μ = 6.25e-5·exp(709.7/(T+102.05))
  PG      ρ = 1051 − 0.75 T,    cp = 2400 + 4 T,    k = 0.200 − 1e-4 (T−20),  μ = 8.36e-5·exp(610/(T+73.75))
Mixing (w = glycol mass fraction):
  1/ρ = (w/ρ_g + (1−w)/ρ_w)⁻¹·(1 + κ·w(1−w))  (κ = 0.048 EG, 0.03 PG: excess-volume correction)
  cp  = w·cp_g + (1−w)·cp_w
  k   = w·k_g + (1−w)·k_w − c_k·w(1−w)|k_w − k_g|          (Filippov-type; c_k = 0.5 EG, 0.3 PG, fitted to typical 50/50 values)
  ln μ = w ln μ_g + (1−w) ln μ_w − 0.55·w(1−w)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from .schemas import CoolantSpec


@dataclass
class CoolantProps:
    rho: float
    cp: float
    k: float
    mu: float
    t_eval_c: float
    description: str
    overrides: list[str] = field(default_factory=list)
    from_correlation: list[str] = field(default_factory=list)
    mass_fraction: float = 0.0

    @property
    def pr(self) -> float:
        return self.mu * self.cp / self.k

    @property
    def nu(self) -> float:
        return self.mu / self.rho

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pr"] = self.pr
        return d


def _water(t: float) -> tuple[float, float, float, float]:
    rho = 1000.0 * (1.0 - (t + 288.9414) / (508929.2 * (t + 68.12963)) * (t - 3.9863) ** 2)
    cp = 4216.928 - 3.050938 * t + 0.0798109 * t ** 2 - 8.352564e-4 * t ** 3 + 3.417832e-6 * t ** 4      # LSQ fit to steam tables, 0-100 °C (±1.5 J/kgK)
    k = 0.5636 + 1.946e-3 * t - 8.151e-6 * t ** 2
    mu = 2.414e-5 * 10.0 ** (247.8 / (t + 273.15 - 140.0))
    return rho, cp, k, mu


def _glycol(kind: str, t: float) -> tuple[float, float, float, float]:
    if kind == "eg":
        return (1125.4 - 0.61 * t, 2310.0 + 3.7 * t, 0.254 + 2.0e-4 * t, 6.25e-5 * math.exp(709.7 / (t + 273.15 - 171.1)))
    return (1051.0 - 0.75 * t, 2400.0 + 4.0 * t, 0.200 - 1.0e-4 * (t - 20.0), 8.36e-5 * math.exp(610.0 / (t + 273.15 - 199.4)))


def mass_fraction_from_volume(kind: str, vol_frac: float) -> float:
    rho_w, rho_g = _water(20.0)[0], _glycol(kind, 20.0)[0]
    return vol_frac * rho_g / (vol_frac * rho_g + (1.0 - vol_frac) * rho_w)


def mixture(kind: str, w: float, t: float) -> tuple[float, float, float, float]:
    rw, cw, kw, mw = _water(t)
    rg, cg, kg, mg = _glycol(kind, t)
    kappa = 0.048 if kind == "eg" else 0.03
    rho = 1.0 / (w / rg + (1.0 - w) / rw) * (1.0 + kappa * w * (1.0 - w))
    cp = w * cg + (1.0 - w) * cw
    ck = 0.5 if kind == "eg" else 0.3
    k = w * kg + (1.0 - w) * kw - ck * w * (1.0 - w) * abs(kw - kg)
    mu = math.exp(w * math.log(mg) + (1.0 - w) * math.log(mw) - 0.55 * w * (1.0 - w))
    return rho, cp, k, mu


class CoolantError(ValueError):
    pass


def coolant_properties(spec: CoolantSpec, t_c: float) -> CoolantProps:
    """Properties at ``t_c`` (evaluate at the mean coolant temperature). User overrides always win."""
    if spec.type == "custom":
        missing = [n for n, v in (("density_kg_m3", spec.density_kg_m3), ("cp_j_kg_k", spec.cp_j_kg_k),
                                  ("k_w_mk", spec.k_w_mk), ("mu_pa_s", spec.mu_pa_s)) if v is None]
        if missing:
            raise CoolantError(f"Custom coolant needs all four properties; missing: {', '.join(missing)}.")
        base, desc, w = (spec.density_kg_m3, spec.cp_j_kg_k, spec.k_w_mk, spec.mu_pa_s), "custom coolant (user-supplied properties)", 0.0
    elif spec.type == "water":
        base, desc, w = _water(t_c), "water (built-in correlation)", 0.0
    else:
        kind = "eg" if spec.type == "eg_water" else "pg"
        x = spec.concentration_pct / 100.0
        if not (0.0 <= x <= 0.7):
            raise CoolantError(f"Glycol concentration {spec.concentration_pct:g} % is outside the supported 0-70 % range.")
        w = x if spec.concentration_basis == "mass" else mass_fraction_from_volume(kind, x)
        base = mixture(kind, w, t_c)
        name = "ethylene glycol" if kind == "eg" else "propylene glycol"
        desc = f"{spec.concentration_pct:g} % {'by mass' if spec.concentration_basis == 'mass' else 'by volume'} {name}/water (built-in correlation, w = {w:.3f} mass fraction)"
    rho, cp, k, mu = base
    over, corr = [], []
    for name, val, attr in (("density", spec.density_kg_m3, "rho"), ("specific heat", spec.cp_j_kg_k, "cp"),
                            ("conductivity", spec.k_w_mk, "k"), ("viscosity", spec.mu_pa_s, "mu")):
        if val is not None and spec.type != "custom":
            if attr == "rho":
                rho = val
            elif attr == "cp":
                cp = val
            elif attr == "k":
                k = val
            else:
                mu = val
            over.append(name)
        else:
            (corr if spec.type != "custom" else over).append(name)
    return CoolantProps(rho, cp, k, mu, t_c, desc, over, corr, w)
