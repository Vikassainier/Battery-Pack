# EV Battery Pack Thermal Analysis & Cooling-System Sizing Tool — Design

Status: living document. Sections 1–7 correspond to the seven design deliverables
requested before implementation.

---------------------------------------------------------------------------

## 1. System architecture

```
                      ┌─────────────────────────── Web dashboard (vanilla JS + Plotly.js) ───────────────────────────┐
                      │ Workflow: Cell datasheet → Confirm → Pack → Drive cycle → C-rates → Thermal/Cooling → Run →   │
                      │ Heat load → Cooling → Optimise → Report        (+ Assumptions, Traceability, Validation)      │
                      └───────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                                                  │ JSON / multipart
                      ┌───────────────────────────────────────────▼───────────────────────────────────────────────────┐
                      │ api/  FastAPI  (thin: parse request → call engine → serialise)                                │
                      └──────┬───────────────────┬───────────────────────────────┬──────────────────────┬─────────────┘
                             │                   │                               │                      │
              ┌──────────────▼───────┐  ┌────────▼────────────┐        ┌─────────▼───────────┐  ┌───────▼─────────┐
              │ ingestion/           │  │ engine/  (pure)     │        │ reporting/          │  │ validation_cases│
              │  datasheet (CSV/XLSX/│  │  validation         │        │  charts (matplotlib)│  │ built-in hand-  │
              │  PDF) + review model │  │  pack   electrical  │        │  pdf_report         │  │ calc cases      │
              │  drive_cycle parser  │  │  vehicle resistance │        │  excel_report       │  └─────────────────┘
              └──────────────────────┘  │  heat   simulation  │        └─────────────────────┘
                                        │  thermal coolant    │
                                        │  coldplate pressure │
                                        │  sizing checks      │
                                        │  sensitivity optim. │
                                        │  assumptions trace  │
                                        │  pipeline           │
                                        └─────────────────────┘
```

Principles

* **The engine never imports FastAPI, pandas-IO, or anything UI related.** It takes typed
  Pydantic inputs and returns plain, JSON-ready results, so it can be embedded in another
  application or exposed through another API later.
* **Stateless API.** The browser holds the project state; every analysis call posts the full
  request. Results are reproducible from the request JSON alone (which is also what the
  report embeds).
* **No black boxes.** Every calculation registers a `TraceNode` (inputs → formula →
  substitution → result). The UI renders the trace graph when a KPI is clicked.
* **No silent assumptions.** Every non-user/non-datasheet parameter is registered in the
  assumptions register with source and confidence; missing data raises validation issues.
* **Human-in-the-loop datasheet ingestion.** Parsers only *propose* values with provenance;
  the analysis is blocked until the cell dataset is marked `confirmed`.

Twelve requested modules → code

| # | Module                    | Location                                        |
|---|---------------------------|-------------------------------------------------|
| 1 | Input / data ingestion    | `ingestion/common.py`, `api/`                   |
| 2 | Datasheet parser          | `ingestion/datasheet.py`                        |
| 3 | Driving-cycle parser      | `ingestion/drive_cycle.py`, `engine/vehicle.py` |
| 4 | Electrical model          | `engine/pack.py`, `electrical.py`, `resistance.py` |
| 5 | Heat-generation model     | `engine/heat.py`, `simulation.py`               |
| 6 | Thermal model             | `engine/thermal.py`                             |
| 7 | Cooling model             | `engine/coolant.py`, `cooling.py`, `coldplate.py` |
| 8 | Pressure-drop model       | `engine/pressure_drop.py`                       |
| 9 | Sizing engine             | `engine/sizing.py`, `checks.py`, `optimizer.py` |
| 10| Validation engine         | `engine/validation.py`                          |
| 11| Dashboard                 | `api/static/`                                   |
| 12| Report generator          | `reporting/`                                    |

---------------------------------------------------------------------------

## 2. Engineering calculation flow

```
 ELECTRICAL LOAD                     HEAT GENERATION                THERMAL ACCUMULATION           COOLING REQUIREMENT → SIZING
 ───────────────                     ────────────────               ────────────────────           ─────────────────────────────
 drive cycle / C-rate profile        per timestep, per cell:        lumped pack thermal mass       design philosophy picks Q_relevant
   │ (speed → road load → P_batt)      R = f(SOC,T)  [L1–L4]          C·dT/dt = Q_gen                Q_req = Q_relevant + Q_ambient-gain
   ▼                                   Q_joule = I²R                      − Q_coolant − Q_ambient    Q_design = Q_req × SF
 P_batt(t) or I_pack(t)                Q_rev  = −I·T·dU/dT                                          ṁ = Q_design/(cp·ΔT_cool) → L/min
   │                                   Q_cell = Q_joule + Q_rev         exact exponential step       cold-plate R-chain, h, U, ΔP, pump
   ▼                                   Q_module, Q_pack (N·Q_cell)      → T_cell(t), T_coolant,out   pump / radiator estimate
 solve cell current                    ≠ electrical energy              → hot-cell / ΔT estimate     PASS / WARNING / FAIL checks,
 (quadratic in I with OCV,R)                                                                         margins, sensitivity, report
 integrate SOC (ZOH)
```

Ordering rules (all enforced in `engine/pipeline.py`):

1. **Validate** cell / pack / cycle / cooling inputs → `ERROR` issues block the run.
2. **Load construction**: driving-cycle current > battery power > motor power > speed
   (vehicle model) > constant-C-rate duty profile. The actual cycle always wins over a
   constant C-rate.
3. **Simulation** at every sample (sample-and-hold; last sample has zero duration).
4. **Design load** is chosen by the consultant's philosophy — *peak*, *moving-average*,
   *sustained*, or *drive-cycle* — all four are always reported side by side.
5. **Flow** is either the user's actual flow or the required flow; when it is the required
   flow the pipeline iterates (max 5 passes) until the temperature-coupled heat load and the
   flow requirement agree within 0.5 %.
6. **Checks** are evaluated on the final coupled simulation; nothing is filtered out.

Peak vs sustained cooling requirement (explained to the user in the UI and the report):
peak heat is a short transient that the pack's thermal mass can absorb (`ΔT = Q·t/C`);
sizing the heat exchanger for the instantaneous maximum over-sizes it. The cooling system
must, however, reject the *sustained* / thermally-equivalent load or the pack will
ratchet up in temperature. The drive-cycle philosophy finds the smallest constant
heat-removal capacity that keeps the transient thermal model below the target temperature.

---------------------------------------------------------------------------

## 3. Required input-data schema (summary; authoritative source: `engine/schemas.py`)

| Group | Fields |
|-------|--------|
| **Project** | name, customer, project no., engineer, revision, notes |
| **Cell (`CellSpec`)** | chemistry, form factor, `capacity_ah`, `v_nom`, `v_max`, `v_min`, `r_dc_mohm`, `r_ac_mohm`, reference SOC/T of R, `r_vs_soc`, `r_vs_temp`, `r_map` (SOC×T), `ocv_vs_soc`, `ocv_map`, `dudt_vs_soc`, `capacity_vs_temp`, max cont. charge / discharge C, pulse C + duration, dimensions, mass, `cp`, operating T range, recommended T range, `confirmed` |
| **Pack (`PackConfig`)** | Ns, Np, n_modules, cells_per_module, module arrangement (+ modules in series), user-stated pack V/Ah/kWh (for consistency check), SOC initial/min/max, T initial, target max cell T, target cell-to-cell ΔT, ambient T |
| **Drive cycle (`DriveCycle`)** | time, speed, acceleration, motor power, battery power, battery current, SOC, (grade) — availability auto-detected; sign conventions; repeats |
| **Vehicle (`VehicleParams`)** | mass, Crr, Cd, frontal area, wheel radius, drivetrain η, aux load, road grade, air density, rotational-inertia factor, regen fraction, max regen power |
| **C-rates (`CRateLimits`, `CRateProfile`)** | continuous / peak discharge C + peak duration; charge C; regen C; peak regen C + duration; optional segment duty profile |
| **Heat model** | resistance level (auto/1–4), extrapolation policy (block/clamp/linear), resistance scale (BOL→EOL), charge factor; entropic mode (auto/table/constant/excluded) + coefficient |
| **Thermal** | extra thermal mass, ambient UA (or estimated), repeats, design philosophy, safety factor, moving-average window |
| **Coolant** | type, concentration (+basis), inlet T, max outlet T, allowable ΔT, ρ / cp / k / μ overrides |
| **Cold plate** | material (+k override), plate thickness, channel w/h/count/length, cooling area, number of plates & arrangement, TIM thickness & k, contact resistance, cell contact area, fin efficiency, roughness, minor-loss K, external loop ΔP, actual flow, maldistribution |
| **Pump / HX** | pump overall efficiency; air ΔT assumption |
| **Limits** | margin thresholds, ΔP/velocity/flow limits, thermal-margin warning, ΔT warning fraction |
| **Provenance** | per-parameter `datasheet / user / assumed / calculated` + confidence |

---------------------------------------------------------------------------

## 4. Calculation equations

Sign convention: **I > 0 discharge**, I < 0 charge/regen; P_batt > 0 discharge.

### 4.1 Pack (electrical)
```
V_pack = Ns·V_cell            C_pack = Np·C_cell           E_pack = Ns·Np·C_cell·V_nom
I_cell = I_pack / Np          I_module = I_pack / Mp        C-rate = I_cell / C_cell
N_cells = Ns·Np = n_modules·cells_per_module
```
(Mp = modules in parallel; series arrangement → Mp = 1.)

### 4.2 Road load → battery power
```
F_accel = m(1+ε)·a       F_roll = Crr·m·g·cosθ  (v>0)       F_aero = ½ρ·Cd·A·v|v|      F_grade = m·g·sinθ
F_tractive = F_accel + F_roll + F_aero + F_grade            P_wheel = F_tractive·v
P_traction,batt = P_wheel/η          (P_wheel ≥ 0)
                = P_wheel·η·f_regen (P_wheel < 0, limited by P_regen,max)
P_batt = P_traction,batt + P_aux     (aux always drawn from the battery)
```

### 4.3 Power → cell current (terminal-power balance)
```
p = P_batt/(Ns·Np)  (per cell)      p = (OCV − I·R)·I   ⇒   I = 2p / (OCV + √(OCV² − 4·R·p))
```
Infeasible when OCV² < 4Rp (request beyond maximum power transfer) → clipped and flagged.
`SOC_{k+1} = SOC_k − I_k·Δt_k / (3600·C_eff)`

### 4.4 Resistance model
```
L1  R = R_dc         L2  R = f(SOC)         L3  R = f(T)         L4  R = f(SOC,T)  (bilinear map, or separable
                                                                    f(SOC)·f(T)/f(T_ref) if only 1-D tables exist)
R_used = R × scale × (charge_factor if I<0)
```
Out-of-range queries: `block` (default: error naming variable & range), `clamp`, `linear` (explicit opt-in, always reported).

### 4.5 Heat generation
```
Q_joule,cell = I_cell²·R                       Q_rev,cell = −I_cell·T[K]·dU/dT
Q_cell = Q_joule + Q_rev                        Q_module = cells_per_module·Q_cell
Q_pack = Ns·Np·Q_cell  ( = I_pack²·R·Ns/Np  for the Joule part )
E_heat = Σ Q_pack,k·Δt_k          (≠ electrical energy throughput)
```
dU/dT sources (in priority): table vs SOC → finite difference of OCV(SOC,T) map → user
constant → *explicitly excluded* (never silent; the result carries a bounding estimate).

### 4.5.1 Design heat-load philosophies
```
peak         Q_rel = max_t Q_pack(t)
moving-avg   Q_rel = max_t  (1/W)∫_{t−W}^{t} Q_pack dτ          (W default = pack thermal time constant)
sustained    Q_rel = max( N·I_cont,dis²·R_worst , N·I_chg²·R_worst ) at target T (steady state)
drive-cycle  Q_rel = min constant removal capacity such that T_cell ≤ T_target over the (repeated) cycle
Q_req = Q_rel + Q_ambient-gain(design)          Q_design = Q_req × SF
```

### 4.6 Lumped thermal model (exact exponential step, sample-and-hold heat)
```
C dT/dt = Q_pack − G_c (T − T_in) − G_a (T − T_amb)         C = N·m_cell·cp + C_extra
G_c = ε·ṁ·cp,cool     ε = 1 − exp(−NTU)     NTU = G_plate/(ṁ·cp,cool)    G_plate = N_cells / R_total,cell
G_a = UA_ambient
T_{k+1} = T_eq + (T_k − T_eq)·exp(−Δt/τ)    T_eq = (Q + G_c·T_in + G_a·T_amb)/(G_c+G_a)     τ = C/(G_c+G_a)
T_coolant,out = T_in + ε·(T − T_in)
```
Screening-level non-uniformity:
```
ΔT_coolant,path = Q_removed/(ṁ·cp)                  (series: full pack; module = /n_modules)
ΔT_c2c ≈ ΔT_coolant,path·f_maldist + 2·δ_Q·Q_cell·R_total,cell        T_hot = T_avg + ΔT_c2c/2
```

### 4.7 Cooling requirement
```
ṁ = Q/(cp·ΔT)          V̇[L/min] = ṁ/ρ·1000·60          ΔT = min(ΔT_allow, T_out,max − T_in)
```
evaluated for cell, module and pack.

### 4.8 Cold-plate resistance chain (per cell)
```
R_contact = R''_c/A_cell         R_TIM = t_TIM/(k_TIM·A_cell)         R_plate = t_p/(k_p·A_cell)
R_conv = cells_per_plate / (h·η_fin·A_wet,plate)     A_wet = n_ch·2(w+h)·L
R_total = R_contact + R_TIM + R_plate + R_conv     [K/W]           U = 1/(R_total·A_ref)   [W/m²K]
```
R [K/W] describes a specific part of a specific geometry; U [W/m²K] normalises by an area, so
it depends on the reference area chosen and is used to compare technologies / layers.

### 4.9 Channel hydraulics
```
D_h = 2wh/(w+h)   v = ṁ_ch/(ρ·w·h)   Re = ρvD_h/μ   Pr = μcp/k    α = min(w,h)/max(w,h)
laminar (Re<2300):  f_D·Re = 96(1−1.3553α+1.9467α²−1.7012α³+0.9564α⁴−0.2537α⁵)   (Shah & London)
                    Nu_H1 = 8.235(1−2.0421α+3.0853α²−2.4765α³+1.0578α⁴−0.1861α⁵)
                    Nu_T  = 7.541(1−2.610α+4.970α²−5.119α³+2.702α⁴−0.548α⁵)
turbulent (Re>4000): f_D from Haaland (roughness ε);  Nu = (f/8)(Re−1000)Pr/(1+12.7√(f/8)(Pr^{2/3}−1))  (Gnielinski, Petukhov f)
transition: linear blend in Re between the two regimes
h = Nu·k/D_h     ΔP_ch = f_D·(L/D_h)·½ρv²     ΔP_minor = K·½ρv²     P_hyd = ΔP·V̇     P_el = P_hyd/η_pump
```

### 4.10 Margins
```
Thermal margin = T_allow,max − T_pred,max          Cooling margin = Q_installed / Q_required
class: < 0 insufficient | 0…warn% warning | warn%…target% moderate | ≥ target% adequate   (limits configurable)
```

---------------------------------------------------------------------------

## 5. Technology stack

| Concern | Choice | Why |
|---------|--------|-----|
| Language | Python 3.11 | scientific stack, typed |
| Calc | NumPy (engine), pandas (file ingestion); SciPy as an independent numerical reference in the test-suite (ODE solver, interpolators, root finders) | the engine's interpolation, integrator and solvers are small hand-written routines so every number is traceable and the extrapolation policy is explicit; SciPy re-derives the same numbers in `tests/test_scipy_crosschecks.py` |
| API | FastAPI + Pydantic v2 | typed schemas = data schema documentation, OpenAPI docs free |
| Datasheet PDF | pdfplumber (text + tables), regex synonym library | no OCR dependency |
| Excel | openpyxl | read + write, live formulas in the validation sheet |
| Charts | Plotly.js (served locally, no CDN) in the UI; matplotlib for PDF | offline-capable |
| PDF report | ReportLab platypus | OEM-style layout, headers/footers, tables |
| Frontend | dependency-free ES modules + CSS (no build step) | easy to deploy/maintain |
| Tests | pytest, FastAPI TestClient, Playwright (UI smoke) | |
| Deployment | `uvicorn` / Dockerfile | single container |

---------------------------------------------------------------------------

## 6. Folder / file structure

```
Battery-Pack/
├── README.md  pyproject.toml  requirements.txt  requirements-dev.txt  Dockerfile
├── docs/            00_DESIGN.md  01_PHASE_LOG.md (hand calcs + verification per phase)
├── sample_data/     cell datasheets (CSV/XLSX/PDF, synthetic), drive cycles
├── scripts/         make_sample_data.py  ui_smoke.py + ui_flow_*.py (Playwright)  check_js.sh
├── battery_thermal/
│   ├── engine/      schemas units interp trace validation pack vehicle load resistance electrical heat
│   │                simulation summary thermal coolant cooling coldplate channel pressure_drop sizing
│   │                checks sensitivity optimizer assumptions pipeline
│   ├── ingestion/   common datasheet drive_cycle
│   ├── reporting/   charts labels text pdf_report excel_report
│   ├── validation_cases/  cases.py (built-in hand-calculation cases)  sample_project.py (demo project)
│   └── api/         main.py routes_ingest routes_analysis routes_report routes_validation
│                    static/{index.html, css/, js/}
└── tests/           one test module per phase + pipeline / report / validation-case / SciPy cross-check tests
```

---------------------------------------------------------------------------

## 7. Validation strategy

1. **Hand calculations per phase** (documented in `docs/01_PHASE_LOG.md`, asserted in tests):
   e.g. 120S1P, 100 Ah, 1 mΩ, 200 A → Q_cell = 40 W, Q_pack = 4.8 kW.
2. **Analytic solutions**: lumped thermal step response `T(t)=T_eq+(T0−T_eq)e^{−t/τ}` must match
   the simulation to round-off; constant-load cases match closed forms.
3. **Conservation identities**: `Σ I·Δt = ΔSOC·C`; `E_gen = C·ΔT + E_removed + E_ambient`;
   `V·I = P` after the power→current solve; electrical energy ≠ heat (efficiency check).
4. **Literature anchors** for the correlations (square duct Nu_H1 = 3.61, f·Re = 56.9;
   parallel plates 8.235 / 96; smooth-pipe Petukhov f at Re=10⁵; water properties at 20 °C).
5. **Independent implementations** of the second validation case (time-varying cycle) in
   plain Python loops inside the tests, compared to the vectorised engine.
6. **Negative tests** for every error class in the specification (missing R, invalid C-rate,
   duplicate timestamps, gaps, out-of-range SOC, extrapolation, …).
7. **Round-trip tests**: datasheet export → parse → identical values; report PDF/XLSX contain the
   key numbers.
8. **Built-in validation cases** are exposed in the UI (`Validation` tab) and re-run on demand,
   showing hand-calc vs software side by side.
