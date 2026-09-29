"""Rectangular mini-channel geometry and single-phase correlations.

Laminar (Re < 2300), fully developed, rectangular duct with aspect ratio α = min(w,h)/max(w,h)   [Shah & London]:
    f_D·Re = 96·(1 − 1.3553α + 1.9467α² − 1.7012α³ + 0.9564α⁴ − 0.2537α⁵)          (Darcy friction factor)
    Nu_H1  = 8.235·(1 − 2.0421α + 3.0853α² − 2.4765α³ + 1.0578α⁴ − 0.1861α⁵)        (constant heat flux)
    Nu_T   = 7.541·(1 − 2.610α  + 4.970α²  − 5.119α³  + 2.702α⁴  − 0.548α⁵)         (constant wall temperature)
Turbulent (Re > 4000):
    f_D    = Haaland: 1/√f = −1.8·log10[(ε/D/3.7)^1.11 + 6.9/Re]
    Nu     = (f/8)(Re−1000)Pr / (1 + 12.7√(f/8)(Pr^(2/3) − 1)),   f = (0.79 ln Re − 1.64)⁻²   (Gnielinski / Petukhov)
Transition (2300-4000): linear blend in Re between the laminar value at 2300 and the turbulent value at 4000.

Fully-developed values are used everywhere; in short channels the thermal entrance region raises the real Nu, so the
results are conservative (this is reported through the thermal-entry-length check).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

RE_LAM, RE_TURB = 2300.0, 4000.0


@dataclass
class RectChannel:
    width_m: float
    height_m: float
    length_m: float

    @property
    def area(self) -> float:
        return self.width_m * self.height_m

    @property
    def perimeter(self) -> float:
        return 2.0 * (self.width_m + self.height_m)

    @property
    def dh(self) -> float:
        return 4.0 * self.area / self.perimeter

    @property
    def alpha(self) -> float:
        return min(self.width_m, self.height_m) / max(self.width_m, self.height_m)

    @property
    def wetted_area(self) -> float:
        return self.perimeter * self.length_m


def reynolds(rho: float, v: float, dh: float, mu: float) -> float:
    return rho * v * dh / mu


def flow_regime(re: float) -> str:
    return "laminar" if re < RE_LAM else ("transitional" if re < RE_TURB else "turbulent")


def _poly(a: float, c: tuple[float, ...]) -> float:
    return sum(ci * a ** i for i, ci in enumerate(c))


def fre_laminar(alpha: float) -> float:
    return 96.0 * _poly(alpha, (1.0, -1.3553, 1.9467, -1.7012, 0.9564, -0.2537))


def nu_laminar(alpha: float, boundary: str = "constant_heat_flux") -> float:
    if boundary == "constant_wall_temperature":
        return 7.541 * _poly(alpha, (1.0, -2.610, 4.970, -5.119, 2.702, -0.548))
    return 8.235 * _poly(alpha, (1.0, -2.0421, 3.0853, -2.4765, 1.0578, -0.1861))


def f_petukhov(re: float) -> float:
    return (0.79 * math.log(re) - 1.64) ** -2


def f_haaland(re: float, rel_rough: float) -> float:
    return (-1.8 * math.log10((rel_rough / 3.7) ** 1.11 + 6.9 / re)) ** -2


def nu_gnielinski(re: float, pr: float) -> float:
    f = f_petukhov(re)
    return (f / 8.0) * (re - 1000.0) * pr / (1.0 + 12.7 * math.sqrt(f / 8.0) * (pr ** (2.0 / 3.0) - 1.0))


def friction_factor(re: float, alpha: float, rel_rough: float = 0.0) -> float:
    """Darcy friction factor for the flow regime (linear blend in the transition region)."""
    lam = lambda r: fre_laminar(alpha) / r          # noqa: E731
    turb = lambda r: f_haaland(r, rel_rough)        # noqa: E731
    if re < RE_LAM:
        return lam(re)
    if re >= RE_TURB:
        return turb(re)
    g = (re - RE_LAM) / (RE_TURB - RE_LAM)
    return (1 - g) * lam(RE_LAM) + g * turb(RE_TURB)


def nusselt(re: float, pr: float, alpha: float, boundary: str = "constant_heat_flux") -> float:
    if re < RE_LAM:
        return nu_laminar(alpha, boundary)
    if re >= RE_TURB:
        return nu_gnielinski(re, pr)
    g = (re - RE_LAM) / (RE_TURB - RE_LAM)
    return (1 - g) * nu_laminar(alpha, boundary) + g * nu_gnielinski(RE_TURB, pr)


def thermal_entry_length(re: float, pr: float, dh: float) -> float:
    """L_th ≈ 0.05·Re·Pr·D_h (laminar)."""
    return 0.05 * re * pr * dh
