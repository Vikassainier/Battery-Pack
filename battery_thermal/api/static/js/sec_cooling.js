// Step 6 - Thermal & cooling parameters: design philosophy, heat model, coolant, cold plate, pump, limits.
import { state, getPath, setPath, setValue, save } from './state.js';
import { api } from './api.js';
import { go } from './app.js';
import { h, card, chip, toast, fmt, field, fieldsGrid, kv, table, debounce } from './ui.js';

const PHILO = [
  { v: 'peak', l: 'Peak heat load' }, { v: 'moving_average', l: 'Moving-average heat load' },
  { v: 'sustained', l: 'Sustained heat load' }, { v: 'drive_cycle', l: 'Drive-cycle thermal load' }];

const COOLANT_TYPES = [{ v: 'eg_water', l: 'Ethylene glycol / water' }, { v: 'pg_water', l: 'Propylene glycol / water' }, { v: 'water', l: 'Water' }, { v: 'custom', l: 'Custom (enter all properties)' }];
const MATERIALS = [{ v: 'aluminium_6061', l: 'Aluminium 6061 (167 W/mK)' }, { v: 'aluminium_3003', l: 'Aluminium 3003 (160 W/mK)' }, { v: 'copper', l: 'Copper (390 W/mK)' }, { v: 'stainless_304', l: 'Stainless 304 (16 W/mK)' }, { v: 'custom', l: 'Custom (enter conductivity)' }];

function levelsSupported() {
  const c = state.cell; const lv = [];
  if (c.r_dc_mohm != null) lv.push(1);
  if (c.r_vs_soc || c.r_map) lv.push(2);
  if (c.r_vs_temp || c.r_map) lv.push(3);
  if (c.r_map || (c.r_vs_soc && c.r_vs_temp)) lv.push(4);
  return lv;
}

function entropicStatus() {
  const c = state.cell;
  if (c.dudt_vs_soc) return ['Data available: dU/dT table vs SOC (mode "auto" will use it).', 'ok'];
  if (c.ocv_map && c.ocv_map.y.length >= 2) return ['Data available: OCV map at several temperatures - dU/dT derived by finite difference.', 'ok'];
  if (state.entropic.constant_mv_per_k != null) return [`Using your estimate ${state.entropic.constant_mv_per_k} mV/K (low confidence).`, 'warning'];
  return ['No dU/dT data. Reversible heat cannot be calculated accurately - enter an estimated coefficient below, or explicitly exclude it. It will NOT be silently ignored: the result carries a warning and a bound on what is left out.', 'error'];
}

export function renderCooling(root) {
  const rerender = () => { const y = window.scrollY; root.replaceChildren(); renderCooling(root); window.scrollTo(0, y); };
  const th = state.thermal;

  root.append(h('h1', {}, '6 · Thermal & cooling parameters'),
    h('div', { class: 'banner' }, 'Amber fields are engineering defaults (assumptions) - they are listed with source and confidence in "Assumptions & data quality". Editing a value marks it as user-provided.'));

  // ---- A. design philosophy ---------------------------------------------------------------------------
  const philoText = h('div', { class: 'banner' });
  const setPhiloText = () => { const d = state.ui.philosophy; philoText.textContent = d ? d.texts[state.thermal.design_philosophy] : ''; };
  root.append(card('Design heat-load philosophy & margin',
    'The maximum instantaneous heat is not automatically the required cooling capacity: short peaks are absorbed by thermal mass, while the sustained / thermally-equivalent load must be rejected continuously. All four values are always reported; the philosophy chosen here governs the sizing.',
    h('div', { class: 'grid' },
      field({ path: 'thermal.design_philosophy', label: 'Design philosophy', type: 'select', options: PHILO }, () => { setPhiloText(); rerender(); }),
      field({ path: 'thermal.safety_factor', label: 'Thermal safety factor SF', unit: '-', help: 'Q_design = (Q_relevant + Q_ambient) × SF' }),
      th.design_philosophy === 'moving_average' ? field({ path: 'thermal.moving_avg_window_s', label: 'Moving-average window', unit: 's', placeholder: '300 (default)', help: 'Comparable to the pack thermal time constant τ (shown in the results).' }) : null,
      field({ path: 'installed_cooling_capacity_kw', label: 'Installed cooling capacity (if known)', unit: 'kW', help: 'Blank: margin is computed against the calculated cold-plate capability.' })),
    philoText,
    h('details', { class: 'fold' }, h('summary', {}, 'Peak thermal load vs sustained cooling requirement'), h('p', {}, state.ui.philosophy ? state.ui.philosophy.peak_vs_sustained : ''))));
  setPhiloText();

  // ---- B. heat model ----------------------------------------------------------------------------------------
  const lv = levelsSupported();
  const [entText, entCls] = entropicStatus();
  root.append(card('Heat-generation model',
    'Joule heat I²R plus reversible (entropic) heat −I·T·dU/dT, evaluated at every time step.',
    h('h4', {}, 'Resistance model'),
    h('div', { class: 'banner' }, `Confirmed cell data supports level(s): ${lv.length ? lv.map(l => `L${l}`).join(', ') : 'none - resistance data missing'}. `,
      'L1 constant · L2 R(SOC) · L3 R(T) · L4 R(SOC,T) (bilinear map, or separable from the 1-D tables). "Auto" picks the highest level supported.'),
    h('div', { class: 'grid' },
      field({ path: 'resistance.level', label: 'Resistance model level', type: 'select', options: [{ v: 'auto', l: 'Auto (highest supported)' }, { v: 1, l: 'Level 1 - constant' }, { v: 2, l: 'Level 2 - R(SOC)' }, { v: 3, l: 'Level 3 - R(T)' }, { v: 4, l: 'Level 4 - R(SOC,T)' }] }),
      field({ path: 'resistance.extrapolation', label: 'Extrapolation outside the data', type: 'select', options: [{ v: 'block', l: 'Blocked (error) - default' }, { v: 'clamp', l: 'Clamp to boundary value (explicit)' }, { v: 'linear', l: 'Linear extrapolation (explicit)' }] }),
      field({ path: 'resistance.scale', label: 'Resistance scale (BOL → EOL)', unit: '-', help: '1.0 = datasheet; EOL typically 1.3-2.0' }),
      field({ path: 'resistance.charge_factor', label: 'Charge / discharge resistance ratio', unit: '-' }),
      field({ path: 'thermal.couple_resistance_to_temperature', label: 'Couple R(T) to the simulated temperature', type: 'checkbox' })),
    h('h4', { style: 'margin-top:16px' }, 'Entropic (reversible) heat'),
    h('div', { class: `banner ${entCls === 'ok' ? '' : entCls === 'warning' ? 'warn' : 'error'}` }, entText),
    h('div', { class: 'grid' },
      field({ path: 'entropic.mode', label: 'Entropic heat mode', type: 'select', options: [{ v: 'auto', l: 'Auto (table → OCV map → estimate → excluded with warning)' }, { v: 'table', l: 'Table vs SOC' }, { v: 'map', l: 'From OCV map' }, { v: 'constant', l: 'Constant estimate' }, { v: 'excluded', l: 'Explicitly exclude' }] }, rerender),
      field({ path: 'entropic.constant_mv_per_k', label: 'Estimated dU/dT (user estimate)', unit: 'mV/K', help: 'Typical |dU/dT|: 0.05-0.3 mV/K. Sign: + means endothermic on discharge.' }))));

  // ---- C. thermal model ----------------------------------------------------------------------------------------
  root.append(card('Thermal model & ambient coupling', 'Lumped pack thermal mass with exact transient integration; screening-level cell-to-cell estimate.',
    fieldsGrid([
      { path: 'thermal.extra_thermal_mass_j_k', label: 'Extra thermal mass (housing, busbars, plates)', unit: 'J/K' },
      { path: 'thermal.ambient_ua_w_k', label: 'Pack-to-ambient conductance UA', unit: 'W/K', help: 'Blank = estimated from cell volume, fill factor 0.4 and h_ext' },
      { path: 'thermal.ambient_h_w_m2k', label: 'External h for the UA estimate', unit: 'W/m²K' },
      { path: 'thermal.cell_heat_spread_pct', label: 'Cell-to-cell heat spread', unit: '%', help: 'Resistance / current-sharing variation' },
      { path: 'thermal.flow_maldistribution_pct', label: 'Flow maldistribution (parallel plates)', unit: '%' }])));

  // ---- D. coolant --------------------------------------------------------------------------------------------------
  const propsBox = h('div');
  const refreshProps = debounce(async () => {
    try {
      const r = await api('/api/coolant/properties', { json: { coolant: JSON.parse(JSON.stringify(state.coolant, (k, v) => (v === null || v === '' ? undefined : v))) } });
      propsBox.replaceChildren();
      if (!r.ok) { propsBox.append(h('div', { class: 'banner error' }, r.message)); return; }
      const p = r.props;
      propsBox.append(h('div', { class: 'banner' }, `Properties at ${fmt(p.t_eval_c, 3)} °C (mean coolant temperature): ${p.description}`),
        kv([['Density ρ', `${fmt(p.rho, 5)} kg/m³`], ['Specific heat cp', `${fmt(p.cp, 5)} J/(kg·K)`], ['Conductivity k', `${fmt(p.k, 4)} W/(m·K)`],
          ['Viscosity μ', `${fmt(p.mu * 1000, 4)} mPa·s`], ['Prandtl number', fmt(p.pr, 4)], ['Coolant ΔT used', r.dt_k != null ? `${fmt(r.dt_k)} K (${r.dt_basis})` : r.dt_basis]]));
    } catch (e) { propsBox.replaceChildren(h('div', { class: 'banner error' }, e.message)); }
  }, 300);
  root.append(card('Coolant', 'Correlation properties are screening-level (ρ,cp ±2 %, k ±5-10 %, μ ±10-15 %); enter supplier data in the override fields for final design.',
    h('div', { class: 'grid' },
      field({ path: 'coolant.type', label: 'Coolant type', type: 'select', options: COOLANT_TYPES }, () => { refreshProps(); rerender(); }),
      state.coolant.type === 'eg_water' || state.coolant.type === 'pg_water' ? field({ path: 'coolant.concentration_pct', label: 'Glycol concentration', unit: '%' }, refreshProps) : null,
      state.coolant.type === 'eg_water' || state.coolant.type === 'pg_water' ? field({ path: 'coolant.concentration_basis', label: 'Concentration basis', type: 'select', options: [{ v: 'volume', l: 'by volume' }, { v: 'mass', l: 'by mass' }] }, refreshProps) : null,
      field({ path: 'coolant.inlet_c', label: 'Coolant inlet temperature', unit: '°C' }, refreshProps),
      field({ path: 'coolant.max_outlet_c', label: 'Max coolant outlet temperature', unit: '°C' }, refreshProps),
      field({ path: 'coolant.allowable_dt_k', label: 'Allowable coolant ΔT', unit: 'K', help: 'If both ΔT and max outlet are given, the more restrictive governs.' }, refreshProps)),
    h('details', { class: 'fold', open: state.coolant.type === 'custom' }, h('summary', {}, 'Property overrides (density, specific heat, conductivity, viscosity)'),
      fieldsGrid([
        { path: 'coolant.density_kg_m3', label: 'Density', unit: 'kg/m³' }, { path: 'coolant.cp_j_kg_k', label: 'Specific heat', unit: 'J/(kg·K)' },
        { path: 'coolant.k_w_mk', label: 'Thermal conductivity', unit: 'W/(m·K)' }, { path: 'coolant.mu_pa_s', label: 'Dynamic viscosity', unit: 'Pa·s' }], refreshProps)),
    propsBox));
  refreshProps();

  // ---- E. cold plate ---------------------------------------------------------------------------------------------------
  const cp = state.cold_plate;
  const cpCard = card('Cold plate / cooling plate', 'Thermal resistance chain R_total = R_contact + R_TIM + R_plate + R_convection, channel hydraulics and pump sizing. Without a cold plate only the heat load, required flow and capacity are calculated.');
  if (!cp) {
    cpCard.append(h('button', { class: 'btn primary', onclick: () => {
      state.cold_plate = { ...state.ui.coldPlateDefaults };
      for (const k of Object.keys(state.cold_plate)) if (k !== 'flow_lpm') state.provenance[`cold_plate.${k}`] = { source: 'assumed', confidence: 'low', note: 'placeholder default - replace with the actual plate design' };
      save(); rerender();
    } }, 'Define a cold plate (placeholder geometry - edit it)'));
  } else {
    const A = (dims) => {
      const c = state.cell; const out = [];
      if (c.length_mm && c.width_mm) out.push(['bottom face L×W', c.length_mm * c.width_mm * 1e-6]);
      if (c.length_mm && c.height_mm) out.push(['large side face L×H', c.length_mm * c.height_mm * 1e-6]);
      if (c.width_mm && c.height_mm) out.push(['small side face W×H', c.width_mm * c.height_mm * 1e-6]);
      if (c.diameter_mm && c.height_mm) out.push(['¼ wrap of the can (π·D·H/4)', Math.PI * c.diameter_mm * c.height_mm * 1e-6 / 4]);
      return out;
    };
    cpCard.append(
      h('div', { class: 'grid' }, ...[
        field({ path: 'cold_plate.material', label: 'Plate material', type: 'select', options: MATERIALS }, rerender),
        field({ path: 'cold_plate.k_plate_w_mk', label: 'Plate conductivity (override)', unit: 'W/mK' }),
        field({ path: 'cold_plate.thickness_mm', label: 'Plate thickness (conduction path)', unit: 'mm' }),
        field({ path: 'cold_plate.channel_width_mm', label: 'Channel width', unit: 'mm' }),
        field({ path: 'cold_plate.channel_height_mm', label: 'Channel height', unit: 'mm' }),
        field({ path: 'cold_plate.n_channels', label: 'Number of channels (per plate)', step: 1, min: 1 }),
        field({ path: 'cold_plate.channel_length_mm', label: 'Channel length', unit: 'mm' }),
        field({ path: 'cold_plate.cooling_area_m2', label: 'Cooling (footprint) area per plate', unit: 'm²' }),
        field({ path: 'cold_plate.n_plates', label: 'Number of plates', step: 1, min: 1 }),
        field({ path: 'cold_plate.plate_arrangement', label: 'Plate flow arrangement', type: 'select', options: [{ v: 'parallel', l: 'Parallel' }, { v: 'series', l: 'Series' }] }),
        field({ path: 'cold_plate.tim_thickness_mm', label: 'TIM thickness', unit: 'mm' }),
        field({ path: 'cold_plate.tim_k_w_mk', label: 'TIM conductivity', unit: 'W/mK' }),
        field({ path: 'cold_plate.contact_resistance_m2k_w', label: 'Cell/TIM contact resistance', unit: 'm²K/W' }),
        field({ path: 'cold_plate.cell_contact_area_m2', label: 'Cell contact area (per cell)', unit: 'm²' }),
        field({ path: 'cold_plate.fin_efficiency', label: 'Channel-wall (fin) efficiency', unit: '-' }),
        field({ path: 'cold_plate.roughness_um', label: 'Wall roughness', unit: 'µm' }),
        field({ path: 'cold_plate.nu_boundary', label: 'Laminar Nu boundary condition', type: 'select', options: [{ v: 'constant_heat_flux', l: 'Constant heat flux (H1)' }, { v: 'constant_wall_temperature', l: 'Constant wall temperature (T)' }] }),
        field({ path: 'cold_plate.minor_loss_k', label: 'Minor-loss coefficient K', unit: '-' }),
        field({ path: 'cold_plate.external_dp_kpa', label: 'External loop pressure drop', unit: 'kPa', help: 'hoses, chiller, radiator, fittings' }),
        field({ path: 'cold_plate.flow_lpm', label: 'Actual pack coolant flow', unit: 'L/min', help: 'Blank = use the required flow from the sizing calculation.' })]),
      A().length ? h('div', { style: 'margin-top:10px' }, h('span', { class: 'help' }, 'Cell contact area from the cell dimensions (assumption - confirm): '),
        ...A().map(([n, a]) => h('button', { class: 'btn small', style: 'margin-right:6px', onclick: () => { setValue('cold_plate.cell_contact_area_m2', Number(a.toPrecision(4)), 'assumed', 'low', `derived from cell dimensions: ${n}`); rerender(); } }, `${n}: ${fmt(a, 3)} m²`))) : null,
      h('div', { style: 'margin-top:10px' }, h('button', { class: 'btn small danger', onclick: () => { state.cold_plate = null; save(); rerender(); } }, 'Remove cold plate')));
  }
  root.append(cpCard);

  // ---- F/G. pump, radiator, limits ------------------------------------------------------------------------------------------
  root.append(card('Pump & radiator assumptions', null, fieldsGrid([
    { path: 'pump.overall_efficiency', label: 'Pump overall efficiency (hydraulic → electrical)', unit: '-' },
    { path: 'radiator.air_dt_k', label: 'Assumed air-side temperature rise', unit: 'K' }])));
  root.append(card('Design margins & check limits (configurable)', 'Margin classes: <0 % insufficient · 0…warning % warning · warning…target % moderate · ≥ target % adequate.', fieldsGrid([
    { path: 'limits.cooling_margin_warn_pct', label: 'Cooling margin - warning below', unit: '%' }, { path: 'limits.cooling_margin_target_pct', label: 'Cooling margin - engineering target', unit: '%' },
    { path: 'limits.thermal_margin_warn_k', label: 'Thermal margin - warning below', unit: 'K' }, { path: 'limits.dt_warn_fraction', label: 'ΔT warning at fraction of target', unit: '-' },
    { path: 'limits.max_velocity_warn_m_s', label: 'Channel velocity - warning', unit: 'm/s' }, { path: 'limits.max_velocity_fail_m_s', label: 'Channel velocity - fail', unit: 'm/s' },
    { path: 'limits.plate_dp_warn_kpa', label: 'Plate ΔP - warning', unit: 'kPa' }, { path: 'limits.plate_dp_fail_kpa', label: 'Plate ΔP - fail', unit: 'kPa' },
    { path: 'limits.loop_dp_warn_kpa', label: 'Loop ΔP - warning', unit: 'kPa' }, { path: 'limits.loop_dp_fail_kpa', label: 'Loop ΔP - fail', unit: 'kPa' },
    { path: 'limits.flow_warn_lpm', label: 'Pack flow - warning', unit: 'L/min' }, { path: 'limits.flow_fail_lpm', label: 'Pack flow - fail', unit: 'L/min' },
    { path: 'limits.flow_min_lpm', label: 'Minimum practical flow', unit: 'L/min' }])));
  root.append(h('div', { style: 'margin-top:14px' }, h('button', { class: 'btn primary', onclick: () => go('run') }, 'Next: run the analysis →')));
}
