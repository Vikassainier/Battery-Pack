# Phase log - hand calculations and verification per phase

Every phase was built bottom-up (engine → tests against hand calculations → API → UI), verified, and committed before the next one
started. The hand calculations below are the ones asserted in the tests (`tests/test_phase*.py`, `tests/test_validation_cases.py`);
the module docstrings carry the same derivations.

| Phase | Scope | Tests |
|------:|-------|------:|
| 1 | Pack configuration engine, validation engine, traceability, API + dashboard shell | 25 |
| 2 | Cell datasheet ingestion (CSV / XLSX / PDF), review-and-confirm workflow | 26 |
| 3 | Driving-cycle ingestion, data-quality checks, road-load model, load construction | 35 |
| 4 | Electrical model + heat generation (resistance levels 1-4, entropic heat) | 26 |
| 5 | Lumped transient thermal model, design heat-load philosophies, capacity sizing | 22 |
| 6-7 | Coolant properties, flow requirement, cold-plate resistance chain, channel hydraulics, pump | 25 |
| - | Pipeline orchestration, sizing, 8 checks, margins, assumptions register, traceability (32); sensitivity, optimiser, analysis API (9) | 41 |
| 8 | Results dashboard (KPIs, checks, 7 graphs, cooling review, sensitivity, optimiser, trace drawer) | browser flows |
| 9 | PDF + Excel engineering reports, report API | 28 |
| 10 | Built-in validation cases + API (14), SciPy cross-checks (5), documentation | 19 |
|   | **Total** | **247** |

---------------------------------------------------------------------------

## Phase 1 - Pack configuration, validation, traceability

* `engine/schemas.py` (Pydantic v2, `extra="forbid"`): every input model; `engine/validation.py`: an `Issue` (code, severity, field, message, hint)
  for every rejected or suspicious input; `engine/trace.py`: `TraceNode` (id, label, value, unit, kind, formula, substitution, inputs, source, note).
* Pack: `V_pack = Ns·V_cell`, `C_pack = Np·C_cell`, `E_pack = Ns·Np·C_cell·V_nom`, `I_cell = I_pack/Np`, `I_module = I_pack/Mp`, `C-rate = I_cell/C_cell`.
  120S1P of a 100 Ah / 3.2 V cell: 384 V, 100 Ah, 38.4 kWh, 12 cells per module in 10 modules. The sample 120S2P: 384 V, 200 Ah, 76.8 kWh.
* Configuration is cross-checked: a stated pack voltage, capacity or energy that differs from the value derived from the cell and `Ns`, `Np` by more than 1 % is a *warning*,
  by more than 5 % an *error*.
* Design decision: the API is **stateless** - the browser holds the project and posts the whole `AnalysisRequest`.

## Phase 2 - Datasheet ingestion

* Parsers only **propose**; the cell must be `confirmed` before the engine will run (`CELL_NOT_CONFIRMED`).
* Synonym library with unit conversion (Ω/mΩ/µΩ, K/°C, Wh/Ah, mm/cm/m, …), column-aware key-value handling, curve/map block classification
  (R vs SOC/T, R map, OCV vs SOC, dU/dT, capacity vs T).
* PDF glyph fallbacks found while verifying with the synthetic PDF: `Ω` extracted as `mW`, `≤` as `£`, `≥` as `³`.
* Round trip: template export → parse → identical values.

## Phase 3 - Driving cycle and road load

Hand calculation (m = 1800 kg, Crr = 0.009, Cd = 0.28, A = 2.2 m², ρ = 1.225, η = 0.90, aux = 0.5 kW):

```
k_aero = ½ρCdA = 0.3773 kg/m ;  F_roll = 0.009·1800·9.80665 = 158.868 N
(a) 100 km/h cruise, v = 27.7778 m/s : F_aero = 291.127 N ; F = 449.995 N ; P_wheel = 12.4999 kW ; P_batt = 12.4999/0.9 + 0.5 = 14.3888 kW
(b) v = 10 m/s, a = +1 m/s²          : F = 1996.598 N ; P_wheel = 19.966 kW ; P_batt = 22.684 kW
(c) v = 10 m/s, a = −1 m/s²          : F = −1603.402 N ; P_wheel = −16.034 kW ; regen f = 1: −13.931 kW ; f = 0.5: −6.715 kW
```

Ingestion: column detection, unit heuristics, gap / duplicate / non-uniform-step / missing-signal / SOC-range / no-load checks; repairs
(sort, dedupe, gap fill, short-run interpolation, resample) are **opt-in and logged**. Discharge, regen and auxiliary power are kept apart.

## Phase 4 - Electrical model and heat generation

```
VC1  100 Ah, 3.2 V, 1 mΩ, 120S1P, 200 A :  I_cell = 200 A ; C-rate = 2 C ; Q_cell = I²R = 40 W ; Q_module = 480 W ; Q_pack = 120·40 = 4800 W
     V_cell = 3.2 − 0.2 = 3.0 V ; V_pack = 360 V ; P_terminal = 72 kW ; P_chemical = 76.8 kW  ->  heat is 6.25 % of the chemical power
VC2  100 A×100 s | 300 A×100 s | −150 A×50 s | 0 A×50 s :  Q_pack = 1200 | 10 800 | 2700 | 0 W
     E = 1 335 000 J = 0.370833 kWh ; peak 10.8 kW ; average 4450 W ; ΔAh = 9.02778 -> SOC_end = 80.9722 %
```

* Resistance levels: 1 constant · 2 `R(SOC)` · 3 `R(T)` · 4 `R(SOC,T)` (bilinear map, or separable `f(SOC)·f(T)/f(T_ref)`).
  *Interpretation:* the specification's "Level 3 = f(SOC,T)" was read as `f(T)` (Level 4 is the SOC-and-temperature level).
  Extrapolation policy `block` (default) / `clamp` / `linear`; every use is counted and reported.
* Entropic heat: `auto | table | map | constant | excluded`; when it cannot be calculated the result says so and bounds the omitted term (±0.2 mV/K).
* Independent check: a plain-Python loop with `R = f(SOC)`, an OCV curve and power-driven load reproduces the vectorised engine.

## Phase 5 - Thermal model and design heat-load philosophies

```
C = 120·2.05·1000 = 246 000 J/K ;  Q = 4800 W
adiabatic 600 s          : T = 25 + 4800·600/246000 = 36.7073 °C
G_c = 200 W/K            : T_eq = 49 °C, τ = 1230 s, T(600 s) = 49 − 24·exp(−600/1230) = 34.2646 °C
min. constant removal for T ≤ 35 °C over 600 s : 25 + (4800 − Q_c)·600/246000 = 35  ->  Q_c = 700 W
4800 W pulse for 300 s, T_target 28 °C         : Q_c = 2340 W
10 kW × 100 s pulse, trailing average          : window 100 s -> 10 kW ; 200 s -> 5 kW ; 400 s -> 2.5 kW
```

* Exact exponential integrator per step with the step-mean temperature, so energy closes exactly (`E_gen = C·ΔT + E_removed + E_ambient`).
* Philosophies: peak · trailing moving average (default 300 s) · sustained (continuous C-rate at the target temperature, evaluated at the worst SOC of the window -
  the SOC where `I²R` is highest, which a test showed is not always the lowest SOC) · drive-cycle (bisection on the constant removal capacity, floor = coolant inlet).
* `Q_required = Q_relevant + Q_ambient` (no ambient term for the drive-cycle philosophy, which already includes it); `Q_design = Q_required·SF`.

## Phases 6-7 - Cooling, cold plate, hydraulics

```
ṁ_pack = 4800/(3400·5) = 0.282353 kg/s = 15.832 L/min  (ρ 1070, cp 3400, ΔT 5 K) ;  module 1.5832 L/min ; cell 0.1319 L/min
Cold plate (8×2 mm, α = 0.25, 5 channels, L = 1 m, 10 parallel plates, 12 cells/plate):
  D_h = 3.2 mm ; v = 0.32985 m/s ; Re = 282.4 (laminar) ; Pr = 34.87 ; Nu_H1 = 8.235·0.647561 = 5.3327 ; h = 649.9 W/m²K
  R_contact 0.0050 + R_TIM 0.0125 + R_plate 0.0005 + R_conv 0.18464 = R_total 0.20264 K/W ; U_cell = 246.74 W/m²K
  G_pack = 120/0.20264 = 592.2 W/K ; ṁcp = 960 W/K ; NTU = 0.6169 ; ε = 0.4604
  f·Re = 72.936 ; f = 0.25832 ; ΔP_channel = 4699 Pa ; ΔP_minor = 87.3 Pa ; ΔP_total = 34.786 kPa ; P_hyd = 9.18 W ; P_el = 22.95 W (η 0.4)
```

Literature anchors asserted: square duct `f·Re = 56.91`, `Nu_H1 = 3.61`, `Nu_T = 2.98`; parallel plates 96 / 8.235 / 7.541; Petukhov `f(Re = 1e5) = 0.0180`;
Gnielinski `Nu(Re = 1e4, Pr = 7) = 79.5`; water at 20 °C and 60 °C. Regime boundaries (2300 / 4000) are continuous (linear blend).
Coolant: water (steam-table fits), ethylene / propylene glycol mixtures (mixing rules), user overrides, custom coolant - every override is recorded.

## Pipeline, checks, sizing, assumptions, sensitivity, optimiser

* `run_analysis(req)` iterates the temperature-coupled loop (resistance ↔ temperature, ≤ 4 passes, 0.5 % tolerance) and returns results, series, trace and register.
* Eight checks - max cell temperature, cell-to-cell ΔT, module ΔT, coolant outlet, cooling margin, absolute cell limit, C-rate limit, pressure drop -
  plus supplementary S1-S7 (entropic source, SOC window, power feasibility, voltage window, flow adequacy, data range, data quality);
  PASS / WARNING / FAIL / N/A, never hidden. Margin classes and limits come from `LimitSettings`, nothing is hard-coded.
* Full chain for validation case 1 with SF 1.2: `Q_design = 5.76 kW`, `ṁ = 0.338824 kg/s = 19.0 L/min`.
* **Trace integrity** is tested for every design philosophy: every dependency named by a trace node exists, every KPI and check points at an existing node, and the selected
  philosophy's value chains back to genuine inputs (not merely to the peak instant).
* Load definitions that would need more than 500 000 time steps (cycle samples × repeats, or a C-rate profile at a tiny step) are refused with advice (`CYCLE_TOO_LONG`);
  the engine runs at roughly 20 000 steps per second (10 000 steps: 0.6 s, 400 000 steps: 21 s).
* Sensitivity: one-at-a-time on 12 parameters (uses `cycle_options.load_scale`). Optimiser: grid search for minimum pump power under all constraints with
  a fast decoupled evaluator that reproduces the full pipeline exactly for constant-resistance cells.

## Phase 8 - Dashboard

Vanilla ES modules, no build step; Plotly.js served locally (`/vendor/plotly.min.js`, no CDN). Verified end to end in headless Chromium with the sample project
(`scripts/ui_flow_*.py`): dashboard, heat load, cooling requirement, sensitivity, optimiser, parameters, trace drawer, report, validation. Polish found from screenshots:
wrong result key (`dt_k` → `dt_cool_k`), tiny percentages shown as ±0, limits blank after loading the sample, radiator/chiller wording.

## Phase 9 - Reports

* PDF (ReportLab + matplotlib): cover, TOC, executive summary, 17 sections, colour-coded statuses, PDF bookmarks; Excel (openpyxl): 12 sheets, live-formula **Hand calcs**,
  clickable **Traceability**, native charts, complete **Timeseries** (the dashboard series is thinned; the workbook is built from a full-resolution run).
* Verification by inspection of rendered pages found and fixed: a literal `<br/>` in the traceability table, "Contents" listing itself, orphaned headings, wrapped
  column headers ("Confidenc e"), raw field names in the input tables (now labelled with units), a `> 999 %` cap that contradicted the summary (`+1528 %`),
  percentage changes of Celsius temperatures in the sensitivity table (now kelvin), a nearly empty page caused by keep-with-next on a long table, mid-word truncation
  in the executive summary.
* Verification by tests: all 17 headings present; **TOC page numbers equal the pages the headings are actually on** (geometry-aware extraction); hand-calc numbers
  (`4.8 kW`, `40 W`, `5.76 kW`) in the PDF; every trace node in the appendix; failing designs reported prominently; workbook formulas evaluated with an in-repo evaluator
  (`tests/xlsx_eval.py`) and compared with the engine to ~1e-16; a corrupted engine value makes the check column read `CHECK`; every traceability hyperlink lands on the row of the
  quantity it names; a 12 601-sample cycle produces 12 601 rows.
* **Security finding by test:** a project name such as `=1+1` was written as a live formula in a sheet subtitle (spreadsheet formula injection). All user-derived text is now
  forced to text, and a final pass turns any stray formula back into text (only the deliberate formula columns of *Hand calcs* remain).

## Phase 10 - Validation cases and SciPy cross-checks

Five built-in cases (`battery_thermal/validation_cases/cases.py`) - the hand value is computed in the module with plain arithmetic, the request runs through `run_analysis`, and each row
carries its tolerance (1e-6 for exact analytics, 1e-4 where rounded constants are involved):

| Case | Content | Rows |
|------|---------|-----:|
| VC1 | constant current 120S1P / 200 A: 40 W, 4.8 kW, 72 kW terminal, 93.75 %, SOC, `Q_design`, flow | 13 |
| VC2 | time-varying with regen and rest: 10.8 kW peak, 4.45 kW mean, 0.370833 kWh, SOC 80.9722 %, moving-average 6.375 kW | 10 |
| VC3 | road-load cruise at 100 km/h: forces, 14.3888 kW battery power, 37.92 A per cell, 172.6 W pack heat | 7 |
| VC4 | cold plate + hydraulics, closed form (Shah-London polynomials): R chain, U, NTU, ε, f, ΔP, pump power | 19 |
| VC5 | lumped thermal accumulation: `C`, adiabatic 36.7073 °C, `G_c = ε·ṁ·cp`, τ, cooled temperature 32.016 °C | 5 |

The tests pin the hand values as typed constants, and prove the machinery can fail (a 1 % wrong hand value, a missing software value and a blocked request each fail the case).
`tests/test_scipy_crosschecks.py` re-derives the engine's numerics with SciPy: `solve_ivp` (DOP853) versus the exponential integrator on a non-uniform time base
(temperature 1e-8 K, removed energy, energy conservation), `RegularGridInterpolator` / `interp1d` versus the interpolators (including explicit linear extrapolation),
`brentq` versus the drive-cycle bisection and the power → current quadratic, `minimize_scalar` for the maximum-power point.

---------------------------------------------------------------------------

## Interpretations, deviations and things that were *not* verified

* **"Level 3 = f(SOC,T)"** in the specification was read as `f(T)`; see Phase 4.
* **SciPy** is not used inside the engine (the interpolation with its explicit extrapolation policy, the exact integrator and the solvers are small transparent routines);
  it is the independent numerical reference in the tests. The design document was corrected accordingly.
* **Sample data are synthetic** and labelled so; no real supplier data are bundled.
* **Not opened in Microsoft Excel or LibreOffice.** The workbook's formulas were verified with an in-repo evaluator and its structure (sheets, charts, hyperlinks) with openpyxl; charts are
  native Excel chart parts (9 in the sample) but their rendering was not inspected in a spreadsheet application.
* **The Dockerfile was not built** in the authoring environment (no container daemon). Its steps were reproduced instead: a clean virtual environment with only `requirements.txt`, and the
  application started from a directory containing only the files the image copies - health, static UI, plotly, sample project, PDF, Excel and validation endpoints all worked.
* **Screening-level physics:** see the limitations in the README and report Section 16.
