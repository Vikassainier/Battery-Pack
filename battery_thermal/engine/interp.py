"""Interpolation with an explicit extrapolation policy.

Datasheet curves and maps are interpolated linearly (bilinear for 2-D). Queries outside the
tabulated range are **never extrapolated silently**:

* ``block``  (default) - raise :class:`OutOfRangeError` with the variable, range and value.
* ``clamp``  - hold the nearest boundary value (explicit opt-in, counted and reported).
* ``linear`` - linear extrapolation from the end segment (explicit opt-in, counted and reported).
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Sequence


class OutOfRangeError(ValueError):
    def __init__(self, name: str, axis: str, value: float, lo: float, hi: float):
        self.name, self.axis, self.value, self.lo, self.hi = name, axis, value, lo, hi
        super().__init__(
            f"{name}: requested {axis} = {value:.4g} is outside the available data range "
            f"[{lo:.4g}, {hi:.4g}]. Extrapolation is disabled - supply data covering this range or "
            f"explicitly enable clamp/linear extrapolation."
        )


@dataclass
class RangeUse:
    """Bookkeeping of how a table was used (for data-quality reporting)."""
    name: str
    axis: str
    data_lo: float
    data_hi: float
    used_lo: float = float("inf")
    used_hi: float = float("-inf")
    n_evals: int = 0
    n_outside: int = 0

    def note(self, v: float) -> None:
        self.n_evals += 1
        if v < self.used_lo:
            self.used_lo = v
        if v > self.used_hi:
            self.used_hi = v
        if v < self.data_lo or v > self.data_hi:
            self.n_outside += 1

    def to_dict(self) -> dict:
        return {
            "table": self.name, "axis": self.axis,
            "data_range": [self.data_lo, self.data_hi],
            "used_range": [self.used_lo if self.n_evals else None, self.used_hi if self.n_evals else None],
            "evaluations": self.n_evals, "outside_data_range": self.n_outside,
        }


def _axis_eval(xs: list[float], x: float, policy: str, name: str, axis: str, use: RangeUse) -> tuple[int, float]:
    """Return (lower index, fraction) for interpolation on ``xs`` honouring the policy.

    Fraction is in [0,1] inside the range; for linear extrapolation it may fall outside [0,1].
    """
    lo, hi = xs[0], xs[-1]
    use.note(x)
    if x < lo or x > hi:
        if policy == "block":
            raise OutOfRangeError(name, axis, x, lo, hi)
        if policy == "clamp":
            x = lo if x < lo else hi
    n = len(xs)
    i = bisect_right(xs, x) - 1
    if i < 0:
        i = 0
    if i > n - 2:
        i = n - 2
    frac = (x - xs[i]) / (xs[i + 1] - xs[i])
    return i, frac


@dataclass
class Interp1D:
    x: Sequence[float]
    y: Sequence[float]
    name: str = "table"
    axis: str = "x"
    policy: str = "block"
    _x: list[float] = field(init=False, repr=False)
    _y: list[float] = field(init=False, repr=False)
    use: RangeUse = field(init=False)

    def __post_init__(self) -> None:
        if len(self.x) != len(self.y) or len(self.x) < 2:
            raise ValueError(f"{self.name}: need >=2 points with equal-length x and y")
        pairs = sorted(zip(map(float, self.x), map(float, self.y)))
        self._x = [p[0] for p in pairs]
        self._y = [p[1] for p in pairs]
        if any(b <= a for a, b in zip(self._x, self._x[1:])):
            raise ValueError(f"{self.name}: x values must be strictly increasing (duplicate x found)")
        self.use = RangeUse(self.name, self.axis, self._x[0], self._x[-1])

    @property
    def range(self) -> tuple[float, float]:
        return self._x[0], self._x[-1]

    def __call__(self, x: float) -> float:
        i, f = _axis_eval(self._x, x, self.policy, self.name, self.axis, self.use)
        return self._y[i] + f * (self._y[i + 1] - self._y[i])


@dataclass
class Interp2D:
    """Bilinear interpolation on a rectilinear grid ``z[i][j]`` at (x[i], y[j])."""
    x: Sequence[float]
    y: Sequence[float]
    z: Sequence[Sequence[float]]
    name: str = "map"
    x_axis: str = "x"
    y_axis: str = "y"
    policy: str = "block"
    _x: list[float] = field(init=False, repr=False)
    _y: list[float] = field(init=False, repr=False)
    _z: list[list[float]] = field(init=False, repr=False)
    use_x: RangeUse = field(init=False)
    use_y: RangeUse = field(init=False)

    def __post_init__(self) -> None:
        nx, ny = len(self.x), len(self.y)
        if nx < 2 or ny < 2:
            raise ValueError(f"{self.name}: 2-D map needs at least 2 points on each axis")
        if len(self.z) != nx or any(len(r) != ny for r in self.z):
            raise ValueError(f"{self.name}: z must be shaped [len(x)={nx}][len(y)={ny}]")
        self._x = [float(v) for v in self.x]
        self._y = [float(v) for v in self.y]
        if any(b <= a for a, b in zip(self._x, self._x[1:])) or any(b <= a for a, b in zip(self._y, self._y[1:])):
            raise ValueError(f"{self.name}: axes must be strictly increasing")
        self._z = [[float(v) for v in row] for row in self.z]
        self.use_x = RangeUse(self.name, self.x_axis, self._x[0], self._x[-1])
        self.use_y = RangeUse(self.name, self.y_axis, self._y[0], self._y[-1])

    def __call__(self, x: float, y: float) -> float:
        i, fx = _axis_eval(self._x, x, self.policy, self.name, self.x_axis, self.use_x)
        j, fy = _axis_eval(self._y, y, self.policy, self.name, self.y_axis, self.use_y)
        z = self._z
        z0 = z[i][j] + fx * (z[i + 1][j] - z[i][j])
        z1 = z[i][j + 1] + fx * (z[i + 1][j + 1] - z[i][j + 1])
        return z0 + fy * (z1 - z0)

    def ddy(self, x: float, y: float) -> float:
        """Partial derivative dz/dy of the bilinear surface (slope of the y-cell containing y)."""
        i, fx = _axis_eval(self._x, x, self.policy, self.name, self.x_axis, self.use_x)
        j, _ = _axis_eval(self._y, y, self.policy, self.name, self.y_axis, self.use_y)
        z = self._z
        z0 = z[i][j] + fx * (z[i + 1][j] - z[i][j])
        z1 = z[i][j + 1] + fx * (z[i + 1][j + 1] - z[i][j + 1])
        return (z1 - z0) / (self._y[j + 1] - self._y[j])
