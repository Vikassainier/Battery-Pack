"""SciPy as an independent numerical reference for the hand-written engine numerics.

The engine deliberately uses small, transparent NumPy/stdlib routines (so that every number can be traced and the
extrapolation policy is explicit). These tests re-derive the same quantities with SciPy's general-purpose solvers.
"""
import math

import numpy as np
import pytest
from scipy.integrate import quad, solve_ivp
from scipy.interpolate import RegularGridInterpolator, interp1d
from scipy.optimize import brentq, minimize_scalar

from battery_thermal.engine.electrical import cell_current_from_power
from battery_thermal.engine.interp import Interp1D, Interp2D, OutOfRangeError
from battery_thermal.engine.thermal import ThermalNetwork, required_capacity_drive_cycle, thermal_step


# ------------------------------------------------------------------------------------------ thermal ODE
def test_exponential_integrator_matches_scipy_ode_solver_on_a_non_uniform_time_base():
    net = ThermalNetwork(246_000.0, g_cool_w_k=200.0, t_in_c=25.0, g_amb_w_k=20.0, t_amb_c=30.0)
    t = np.array([0.0, 1.0, 7.0, 8.0, 60.0, 200.0, 201.0, 600.0, 1500.0])
    q = np.array([4800.0, 0.0, 12000.0, 300.0, 2500.0, -800.0, 9000.0, 100.0, 0.0])          # W, sample-and-hold (last sample has no duration)
    temp = 30.0
    for k in range(len(t) - 1):
        dt = t[k + 1] - t[k]
        t_next, _, qc_mean, qa_mean = thermal_step(temp, q[k], dt, net)

        def rhs(_, y, qk=q[k]):
            return [(qk - net.g_cool_w_k * (y[0] - net.t_in_c) - net.g_amb_w_k * (y[0] - net.t_amb_c)) / net.c_pack_j_k]
        sol = solve_ivp(rhs, (t[k], t[k + 1]), [temp], method="DOP853", rtol=1e-12, atol=1e-13, dense_output=True)
        assert t_next == pytest.approx(sol.y[0, -1], abs=1e-8), k
        # the mean coolant removal / ambient loss reported for the step equals the time integral of G·(T(t) − T_ref)
        e_cool = quad(lambda s: net.g_cool_w_k * (sol.sol(s)[0] - net.t_in_c), t[k], t[k + 1], epsabs=1e-10, epsrel=1e-10)[0]
        e_amb = quad(lambda s: net.g_amb_w_k * (sol.sol(s)[0] - net.t_amb_c), t[k], t[k + 1], epsabs=1e-10, epsrel=1e-10)[0]
        assert qc_mean * dt == pytest.approx(e_cool, rel=1e-6, abs=1e-6) and qa_mean * dt == pytest.approx(e_amb, rel=1e-6, abs=1e-6)
        # energy conservation over the step: stored = generated − removed − lost
        assert net.c_pack_j_k * (t_next - temp) == pytest.approx(q[k] * dt - qc_mean * dt - qa_mean * dt, rel=1e-9, abs=1e-6)
        temp = t_next


def test_drive_cycle_capacity_bisection_agrees_with_a_brent_root_of_the_closed_form():
    c_pack, q_pack, secs, t_target = 246_000.0, 4800.0, 600.0, 35.0
    t = np.arange(0.0, secs + 1.0)
    q = np.full(t.size, q_pack)
    res = required_capacity_drive_cycle(t, q, c_pack, t0_c=25.0, t_target_c=t_target, t_floor_c=25.0, tol_w=0.01)
    # closed form (adiabatic with constant removal above the 25 °C floor): T_end(Q_c) = 25 + (Q − Q_c)·t/C
    root = brentq(lambda qc: 25.0 + (q_pack - qc) * secs / c_pack - t_target, 0.0, q_pack)
    assert root == pytest.approx(700.0) and res["q_cap_w"] == pytest.approx(root, abs=0.02) and res["feasible"]


# ------------------------------------------------------------------------------------------ interpolation
def test_bilinear_map_matches_scipy_regular_grid_inside_and_with_explicit_linear_extrapolation():
    rng = np.random.default_rng(7)
    xs, ys = [0.0, 10.0, 30.0, 60.0, 100.0], [-10.0, 0.0, 25.0, 45.0]
    z = rng.uniform(0.3, 2.0, (len(xs), len(ys)))
    ref = RegularGridInterpolator((xs, ys), z, method="linear")
    inner = Interp2D(xs, ys, z.tolist(), policy="block")
    for x, y in rng.uniform([0.0, -10.0], [100.0, 45.0], (400, 2)):
        assert inner(x, y) == pytest.approx(float(ref([[x, y]])[0]), rel=1e-12)
    ext = RegularGridInterpolator((xs, ys), z, method="linear", bounds_error=False, fill_value=None)      # SciPy extrapolates from the edge cell
    lin = Interp2D(xs, ys, z.tolist(), policy="linear")
    for x, y in rng.uniform([-30.0, -25.0], [130.0, 70.0], (400, 2)):
        assert lin(x, y) == pytest.approx(float(ext([[x, y]])[0]), rel=1e-9, abs=1e-12)
    clamp = Interp2D(xs, ys, z.tolist(), policy="clamp")
    for x, y in rng.uniform([-30.0, -25.0], [130.0, 70.0], (100, 2)):
        assert clamp(x, y) == pytest.approx(float(ref([[min(max(x, xs[0]), xs[-1]), min(max(y, ys[0]), ys[-1])]])[0]), rel=1e-12)
    with pytest.raises(OutOfRangeError):
        inner(101.0, 0.0)                                                                                 # default: never extrapolate silently


def test_curve_matches_scipy_interp1d_and_numpy_interp():
    x, y = [0.0, 10.0, 20.0, 50.0, 80.0, 100.0], [0.85, 0.66, 0.58, 0.52, 0.55, 0.62]
    ours = Interp1D(x, y)
    ref = interp1d(x, y, kind="linear")
    for v in np.linspace(0.0, 100.0, 211):
        assert ours(float(v)) == pytest.approx(float(ref(v)), rel=1e-12) == pytest.approx(float(np.interp(v, x, y)), rel=1e-12)
    ext_ours, ext_ref = Interp1D(x, y, policy="linear"), interp1d(x, y, kind="linear", fill_value="extrapolate", bounds_error=False)
    for v in (-25.0, -0.5, 100.5, 140.0):
        assert ext_ours(v) == pytest.approx(float(ext_ref(v)), rel=1e-12)


# ------------------------------------------------------------------------------------------ power -> current
def test_power_to_current_quadratic_matches_a_numerical_root_and_the_maximum_power_point():
    ocv, r = 3.2, 1e-3
    for p in (5.0, 60.0, 119.9, 400.0, 1500.0, 2500.0):                                                    # W per cell, up to close to the limit
        i, feasible = cell_current_from_power(p, ocv, r)
        assert feasible
        root = brentq(lambda cur: (ocv - cur * r) * cur - p, 0.0, ocv / (2 * r), xtol=1e-14, rtol=1e-14)   # the lower (stable) root
        assert i == pytest.approx(root, rel=1e-10)
    # the terminal power (OCV − I·R)·I has its maximum at I = OCV/(2R): beyond it the request is infeasible and the current is limited there
    best = minimize_scalar(lambda cur: -(ocv - cur * r) * cur, bounds=(0.0, ocv / r), method="bounded", options={"xatol": 1e-10})
    p_max = ocv ** 2 / (4 * r)
    assert best.x == pytest.approx(ocv / (2 * r), rel=1e-6) and -best.fun == pytest.approx(p_max, rel=1e-9)
    i, feasible = cell_current_from_power(p_max * 1.01, ocv, r)
    assert not feasible and i == pytest.approx(ocv / (2 * r))
    assert math.isclose(cell_current_from_power(p_max, ocv, r)[0], ocv / (2 * r), rel_tol=1e-9)              # the exact maximum is just feasible
