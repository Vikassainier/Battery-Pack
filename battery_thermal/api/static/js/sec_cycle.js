// Steps 4-5 - Driving cycle (Phase 3) and charge/discharge C-rate definition.
import { state, getPath, setPath, setValue, save, prov } from './state.js';
import { api } from './api.js';
import { go } from './app.js';
import { h, card, chip, toast, fmt, issuesList, plot, plotDiv, table, field, fieldsGrid, kv, debounce } from './ui.js';

const PARAM_LABEL = { time_s: 'Time', speed_kmh: 'Vehicle speed', accel_ms2: 'Acceleration', motor_power_kw: 'Motor power',
  battery_power_kw: 'Battery power', battery_current_a: 'Battery current', soc_pct: 'SOC', grade_pct: 'Road grade' };
const PARAM_UNIT = { time_s: 's', speed_kmh: 'km/h', accel_ms2: 'm/s²', motor_power_kw: 'kW', battery_power_kw: 'kW', battery_current_a: 'A', soc_pct: '%', grade_pct: '%' };

let lastFile = null;   // kept in memory so the column mapping / repair options can re-parse without another upload

const VEHICLE_FIELDS = [
  { path: 'vehicle.mass_kg', label: 'Vehicle mass (incl. payload)', unit: 'kg', required: true },
  { path: 'vehicle.crr', label: 'Rolling resistance Crr', unit: '-', required: true },
  { path: 'vehicle.cd', label: 'Drag coefficient Cd', unit: '-', required: true },
  { path: 'vehicle.frontal_area_m2', label: 'Frontal area', unit: 'm²', required: true },
  { path: 'vehicle.wheel_radius_m', label: 'Wheel radius', unit: 'm', required: true },
  { path: 'vehicle.drivetrain_eff', label: 'Drivetrain efficiency (battery→wheel)', unit: '-', required: true, help: 'Motor + inverter + gearbox.' },
  { path: 'vehicle.aux_load_kw', label: 'Auxiliary electrical load', unit: 'kW', help: 'HVAC, pumps, DC/DC … drawn from the battery.' },
  { path: 'vehicle.gradient_pct', label: 'Road gradient', unit: '%' },
  { path: 'vehicle.air_density', label: 'Air density', unit: 'kg/m³' },
  { path: 'vehicle.rotational_inertia_factor', label: 'Rotational inertia factor ε', unit: '-', help: 'm_eff = m(1+ε); 0 = neglected.' },
  { path: 'vehicle.regen_fraction', label: 'Regen recovery fraction', unit: '-', help: 'Share of braking energy taken by the motor.' },
  { path: 'vehicle.max_regen_kw', label: 'Max regen power (battery side)', unit: 'kW' },
];
const VEHICLE_DEFAULTS = { aux_load_kw: 0, gradient_pct: 0, air_density: 1.225, rotational_inertia_factor: 0, regen_fraction: 1 };

async function parseCycle(file, opts, view) {
  const fd = new FormData();
  fd.append('file', file);
  for (const [k, v] of Object.entries(opts)) if (v !== undefined && v !== null && v !== '' && v !== false) fd.append(k, typeof v === 'object' ? JSON.stringify(v) : v);
  const res = await api('/api/drivecycle/parse', { form: fd });
  state.ui.cycleMeta = res;
  if (!res.has_errors) {
    state.cycle = res.cycle;
    state.cycle.name = file.name;
    const cur = state.cycle_options.source;
    if (cur && cur !== 'auto' && !res.sources.includes(cur)) state.cycle_options.source = 'auto';   // choice no longer exists in this file
  } else state.cycle = null;
  save();
  return res;
}

function detectedTable(meta, remap) {
  const rows = Object.keys(PARAM_LABEL).map(k => {
    const col = meta.mapping[k];
    const st = meta.stats[k];
    const sel = h('select', { onchange: e => remap(k, e.target.value) },
      h('option', { value: '' }, '— not used —'), ...meta.headers.filter(x => x).map(x => h('option', { value: x, selected: x === col }, x)));
    return [PARAM_LABEL[k], sel, col ? (meta.units[k] || PARAM_UNIT[k]) : '', meta.available[k] ? chip('available', 'ok') : chip('not found', 'na'),
      st ? `${fmt(st.min)} … ${fmt(st.max)} (mean ${fmt(st.mean)})` : ''];
  });
  return table(['Parameter', 'File column', 'Unit used', 'Status', 'Range in file'], rows);
}

function previewPlots(load, box) {
  box.replaceChildren();
  const t = load.t;
  if (load.kind === 'power') {
    const d = plotDiv(); const s = plotDiv();
    box.append(h('div', { class: 'row' }, d, s));
    const tr = [{ x: t, y: load.p_discharge_kw, name: 'Battery discharge power', fill: 'tozeroy', line: { color: '#0b6fb8', width: 1 } },
      { x: t, y: load.p_regen_kw, name: 'Charge / regenerative power', fill: 'tozeroy', line: { color: '#2a9d6f', width: 1 } }];
    if (load.p_aux_kw) tr.push({ x: t, y: load.p_aux_kw, name: 'Auxiliary power', line: { color: '#d9822b', width: 1.5, dash: 'dot' } });
    plot(d, tr, { title: { text: 'Battery power [kW] (+ discharge, − charge/regen)', font: { size: 13 } }, xaxis: { title: 'time [s]' } });
    if (load.speed_kmh) plot(s, [{ x: t, y: load.speed_kmh, name: 'speed', line: { color: '#6b7a8f', width: 1 } }], { title: { text: 'Vehicle speed [km/h]', font: { size: 13 } }, xaxis: { title: 'time [s]' } });
    else s.remove();
  } else {
    const d = plotDiv(); box.append(d);
    plot(d, [{ x: t, y: load.i_pack_a, name: 'pack current', line: { color: '#0b6fb8', width: 1 } }], { title: { text: 'Pack current [A] (+ discharge)', font: { size: 13 } }, xaxis: { title: 'time [s]' } });
  }
}

export async function renderCycle(root) {
  const meta = state.ui.cycleMeta;
  const out = h('div');
  const rerender = () => { root.replaceChildren(); renderCycle(root); };
  const co = state.cycle_options;
  const optsNow = () => ({ repair: !!state.ui.cycleRepair, resample_dt_s: state.ui.cycleResample || undefined, sheet: state.ui.cycleSheet || undefined, mapping: state.ui.cycleMapping || undefined });

  const handle = async (promise, label) => {
    out.replaceChildren(h('span', { class: 'spinner' }), ` Reading ${label}…`);
    try { await promise; rerender(); } catch (e) { out.replaceChildren(h('div', { class: 'banner error' }, `Could not read the file: ${e.message}`)); }
  };
  const reparse = () => handle(parseCycle(lastFile, optsNow(), root), lastFile.name);
  const upload = (file, keepMapping = false) => { lastFile = file; if (!keepMapping) state.ui.cycleMapping = null; handle(parseCycle(file, optsNow(), root), file.name); };
  const fileInput = h('input', { type: 'file', accept: '.csv,.xlsx,.txt', style: 'display:none', onchange: e => e.target.files[0] && upload(e.target.files[0]) });
  const drop = h('div', { class: 'drop', onclick: () => fileInput.click() },
    h('div', { style: 'font-size:16px;font-weight:600' }, 'Drop the driving-cycle file here, or click to browse'),
    h('div', { class: 'muted' }, 'CSV or Excel · columns such as Time [s], Speed [km/h], Acceleration [m/s²], Motor power [kW], Battery power [kW], Battery current [A], SOC [%]'));
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });
  const sample = (name, label) => h('a', { href: '#', onclick: async e => {
    e.preventDefault();
    const blob = await (await fetch(`/api/samples/${name}`)).blob();
    upload(new File([blob], name));
  } }, label);

  root.append(h('h1', {}, '3 · Drive cycle'),
    card('Driving-cycle file', 'The actual cycle is used for the transient heat calculation - a constant C-rate is only a fallback when no cycle is supplied.',
      drop, fileInput,
      h('div', { style: 'margin-top:10px' }, 'Synthetic samples (test data): ', sample('drive_cycle_speed_only.csv', 'speed only'), ' · ', sample('drive_cycle_battery.csv', 'battery power/current/SOC'),
        state.cycle ? h('span', {}, '  ·  ', h('a', { href: '#', onclick: e => { e.preventDefault(); state.cycle = null; state.ui.cycleMeta = null; save(); rerender(); } }, 'remove cycle')) : null),
      h('div', { class: 'row', style: 'margin-top:12px' },
        h('label', { class: 'f', style: 'flex-direction:row;gap:8px;align-items:center' },
          h('input', { type: 'checkbox', style: 'width:auto', checked: !!state.ui.cycleRepair, onchange: e => { state.ui.cycleRepair = e.target.checked; if (lastFile) reparse(); } }),
          'Auto-repair (sort, drop duplicates, fill gaps, interpolate ≤5 missing values) - every change is listed'),
        h('label', { class: 'f' }, h('span', { class: 'lab' }, 'Resample to uniform step', h('span', { class: 'unit' }, 's')),
          h('input', { type: 'number', step: 'any', value: state.ui.cycleResample || '', placeholder: 'keep original',
            onchange: e => { state.ui.cycleResample = Number(e.target.value) || null; if (lastFile) reparse(); } })),
        meta && meta.sheets.length > 1 ? h('label', { class: 'f' }, h('span', { class: 'lab' }, 'Excel sheet'),
          h('select', { onchange: e => { state.ui.cycleSheet = e.target.value; state.ui.cycleMapping = null; if (lastFile) reparse(); } },
            meta.sheets.map(s => h('option', { value: s, selected: s === meta.sheet }, s)))) : null)),
    out);

  if (meta) {
    const remap = (canon, header) => {
      if (!lastFile) { toast('Re-upload the file to change the column mapping', 'error'); return; }
      state.ui.cycleMapping = { ...meta.mapping, [canon]: header };
      reparse();
    };
    out.append(card(`Detected parameters - ${meta.name}`,
      `${meta.stats.n_samples} samples · ${fmt(meta.stats.duration_s, 5)} s · Δt median ${fmt(meta.stats.dt_median_s)} s (min ${fmt(meta.stats.dt_min_s)}, max ${fmt(meta.stats.dt_max_s)})`,
      detectedTable(meta, remap),
      meta.repairs.length ? h('div', { class: 'banner warn', style: 'margin-top:10px' }, h('b', {}, 'Repairs applied: '), meta.repairs.join('; ')) : null,
      h('h4', { style: 'margin-top:14px' }, 'Data-quality checks'), issuesList(meta.issues),
      meta.has_errors ? h('div', { class: 'banner error', style: 'margin-top:10px' }, 'The file has blocking errors and is NOT loaded. Fix the file or tick auto-repair above.') : null));
  }

  if (state.cycle) {
    const avail = meta ? meta.sources : ['battery_current', 'battery_power', 'motor_power', 'vehicle_speed'].filter(s => ({ battery_current: 'battery_current_a', battery_power: 'battery_power_kw', motor_power: 'motor_power_kw', vehicle_speed: 'speed_kmh' })[s] in state.cycle);
    const src = co.source || 'auto';
    const eff = src === 'auto' ? avail[0] : src;
    const needsVehicle = eff === 'vehicle_speed' || (eff === 'motor_power' && co.motor_power_basis !== 'electrical') || (eff === 'battery_power' && co.battery_power_includes_aux === false);
    const opt = (path, label, opts2, help) => field({ path, label, type: 'select', options: opts2, help }, rerender);
    out.append(card('Load source & conventions', 'Choose which signal drives the heat calculation. Positive current/power = discharge.',
      h('div', { class: 'grid' },
        opt('cycle_options.source', 'Load source', [{ v: 'auto', l: `Auto (priority: current > battery power > motor power > speed) → ${eff}` }, ...avail.map(s => ({ v: s, l: s.replace('_', ' ') }))]),
        opt('cycle_options.current_sign', 'Current sign convention', [{ v: 1, l: 'positive = discharge' }, { v: -1, l: 'positive = charge (invert)' }]),
        opt('cycle_options.power_sign', 'Power sign convention', [{ v: 1, l: 'positive = discharge' }, { v: -1, l: 'positive = charge (invert)' }]),
        eff === 'motor_power' ? opt('cycle_options.motor_power_basis', 'Motor power is', [{ v: 'mechanical', l: 'mechanical (apply drivetrain efficiency)' }, { v: 'electrical', l: 'electrical input to the motor' }]) : null,
        eff === 'battery_power' ? field({ path: 'cycle_options.battery_power_includes_aux', label: 'Battery power already includes auxiliaries', type: 'checkbox' }, rerender) : null,
        field({ path: 'cycle_options.repeats', label: 'Repeat the cycle (consecutive runs)', step: 1, min: 1, help: 'Thermal soak builds up over repeated cycles - 1 is optimistic for cooling sizing.' }),
        opt('cycle_options.soc_mode', 'SOC source', [{ v: 'auto', l: 'Auto (file if present, else integrate)' }, { v: 'integrate', l: 'Always integrate current' }, { v: 'file', l: 'Use SOC column from file' }]))));

    if (needsVehicle || state.vehicle) {
      const isNew = !state.vehicle;
      out.append(card('Vehicle parameters - battery power from road load',
        'F_tractive = F_acc + F_roll + F_aero + F_grade ;  P_wheel = F·v ;  P_batt = P_wheel/η (+ aux); regenerative braking returns P_wheel·η·f_regen.',
        isNew ? h('div', {}, h('div', { class: 'banner warn' }, `The selected source (${eff.replace('_', ' ')}) needs vehicle parameters.`),
          h('button', { class: 'btn primary', onclick: () => { state.vehicle = { ...VEHICLE_DEFAULTS }; for (const k of Object.keys(VEHICLE_DEFAULTS)) state.provenance[`vehicle.${k}`] = { source: 'assumed', confidence: 'low', note: 'default - confirm' }; save(); rerender(); } }, 'Define vehicle parameters'),
          ' ', h('button', { class: 'btn', onclick: () => {
            state.vehicle = { mass_kg: 1800, crr: 0.009, cd: 0.28, frontal_area_m2: 2.2, wheel_radius_m: 0.32, drivetrain_eff: 0.9, aux_load_kw: 0.5, gradient_pct: 0, air_density: 1.225, rotational_inertia_factor: 0, regen_fraction: 1 };
            for (const k of Object.keys(state.vehicle)) state.provenance[`vehicle.${k}`] = { source: 'assumed', confidence: 'low', note: 'example vehicle - replace with customer data' };
            save(); rerender();
          } }, 'Fill example vehicle (assumed values)'))
          : h('div', {}, fieldsGrid(VEHICLE_FIELDS, debounce(() => refreshPreview(), 400)),
            h('div', { style: 'margin-top:8px' }, h('a', { href: '#', onclick: e => { e.preventDefault(); state.vehicle = null; save(); rerender(); } }, 'remove vehicle parameters')))));
    }

    const box = h('div');
    out.append(card('Battery power / current preview', 'Constructed exactly as the analysis will use it. Discharge, charge/regenerative and auxiliary power are shown separately.', box));
    const refreshPreview = async () => {
      box.replaceChildren(h('span', { class: 'spinner' }));
      const body = { cell: state.cell, pack: Number.isFinite(state.pack.ns) ? state.pack : undefined, cycle: state.cycle, cycle_options: state.cycle_options, vehicle: state.vehicle || undefined };
      try {
        const r = await api('/api/load/preview', { json: JSON.parse(JSON.stringify(body, (k, v) => (v === null || Number.isNaN(v) ? undefined : v))) });
        box.replaceChildren();
        if (!r.ok) { box.append(issuesList(r.issues)); return; }
        const L = r.load;
        box.append(h('div', { class: 'banner' }, L.notes.join(' ')));
        if (L.energy) box.append(kv([['Discharge energy', `${fmt(L.energy.discharge_kwh)} kWh`], ['Charge / regen energy', `${fmt(L.energy.regen_kwh)} kWh`],
          ['Net battery energy', `${fmt(L.energy.net_kwh)} kWh`], ['Auxiliary energy', L.energy.aux_kwh != null ? `${fmt(L.energy.aux_kwh)} kWh` : '–'],
          ['Peak discharge / regen power', `${fmt(L.peak_discharge_kw)} kW / ${fmt(L.peak_regen_kw)} kW`]]));
        const pb = h('div'); box.append(pb); previewPlots(L, pb);
        if (L.road) {
          const rd = plotDiv(); box.append(rd);
          plot(rd, [['f_accel_n', 'F acceleration'], ['f_roll_n', 'F rolling'], ['f_aero_n', 'F aerodynamic'], ['f_grade_n', 'F grade'], ['f_tractive_n', 'F tractive']].map(([k, n]) =>
            ({ x: L.t, y: L.road[k], name: n, line: { width: n === 'F tractive' ? 2 : 1 } })), { title: { text: 'Road-load forces [N]', font: { size: 13 } }, xaxis: { title: 'time [s]' } });
        }
      } catch (e) { box.replaceChildren(h('div', { class: 'banner error' }, e.message)); }
    };
    refreshPreview();
  } else if (!meta) {
    out.append(h('div', { class: 'banner' }, 'No driving cycle loaded. You can instead define a constant C-rate duty profile in step 5 (charging & discharging parameters).'));
  }
  root.append(h('div', { style: 'margin-top:14px' }, h('button', { class: 'btn primary', onclick: () => go('cooling') }, 'Next: cooling parameters →')));
}

// ------------------------------------------------------------------------------------------------------
// Step 5 - charging & discharging parameters (C-rates)
// ------------------------------------------------------------------------------------------------------
const CRATE_FIELDS = [
  ['Discharge', [
    { path: 'crate_limits.cont_discharge_c', label: 'Continuous discharge C-rate', unit: 'C' },
    { path: 'crate_limits.peak_discharge_c', label: 'Peak discharge C-rate', unit: 'C' },
    { path: 'crate_limits.peak_discharge_duration_s', label: 'Peak discharge duration', unit: 's' }]],
  ['Charging / regeneration', [
    { path: 'crate_limits.charge_c', label: 'Charging C-rate', unit: 'C' },
    { path: 'crate_limits.regen_c', label: 'Regenerative braking C-rate', unit: 'C' },
    { path: 'crate_limits.peak_regen_c', label: 'Peak regenerative C-rate', unit: 'C' },
    { path: 'crate_limits.peak_regen_duration_s', label: 'Peak regen duration', unit: 's' }]],
];

export function renderCRate(root) {
  const rerender = () => { root.replaceChildren(); renderCRate(root); };
  const c = state.cell;
  root.append(h('h1', {}, '5 · Charging & discharging parameters'),
    h('div', { class: 'banner' }, state.cycle
      ? 'A driving cycle is loaded: the actual cycle drives the heat calculation. These C-rates are used as limits for the C-rate check (Check 7) and for the sustained-load sizing philosophy.'
      : 'No driving cycle is loaded: the constant-C-rate duty profile below drives the calculation. These limits also feed Check 7 and the sustained-load philosophy.'),
    card('C-rate limits', 'Prefilled values must come from the datasheet or the customer. Peak = allowed for the stated duration; continuous = allowed indefinitely.',
      h('button', { class: 'btn small', onclick: () => {
        const cp = (p, v) => { if (v != null) setValue(p, v, 'datasheet', 'medium', 'copied from confirmed cell datasheet values'); };
        cp('crate_limits.cont_discharge_c', c.max_discharge_c); cp('crate_limits.peak_discharge_c', c.pulse_discharge_c);
        cp('crate_limits.peak_discharge_duration_s', c.pulse_duration_s); cp('crate_limits.charge_c', c.max_charge_c);
        cp('crate_limits.peak_regen_c', c.pulse_charge_c); cp('crate_limits.peak_regen_duration_s', c.pulse_duration_s);
        toast('Copied datasheet C-rates - review them'); rerender();
      } }, 'Use cell datasheet values'),
      ...CRATE_FIELDS.map(([t, fs]) => h('div', {}, h('h4', { style: 'margin-top:12px' }, t), fieldsGrid(fs)))));

  // duty profile
  if (!state.crate_profile) state.crate_profile = null;
  const prof = state.crate_profile;
  const segTable = () => {
    const rows = prof.segments.map((s, i) => [
      i + 1,
      h('select', { onchange: e => { s.kind = e.target.value; save(); } }, ['discharge', 'charge', 'regen', 'rest'].map(k => h('option', { value: k, selected: k === s.kind }, k))),
      h('input', { type: 'number', step: 'any', value: s.c_rate, onchange: e => { s.c_rate = Number(e.target.value); save(); } }),
      h('input', { type: 'number', step: 'any', value: s.duration_s, onchange: e => { s.duration_s = Number(e.target.value); save(); } }),
      h('button', { class: 'btn small danger', onclick: () => { prof.segments.splice(i, 1); save(); rerender(); } }, 'remove')]);
    return table(['#', 'Segment', 'C-rate (magnitude)', 'Duration [s]', ''], rows);
  };
  const lim = state.crate_limits;
  root.append(card('Constant-C-rate duty profile (used only without a driving cycle)',
    'Each segment holds a cell C-rate for its duration (1C = nominal capacity per hour). Example: peak → continuous discharge → rest → charge.',
    prof ? h('div', {}, segTable(),
      h('div', { class: 'row', style: 'margin-top:10px;align-items:center' },
        h('button', { class: 'btn small', onclick: () => { prof.segments.push({ kind: 'discharge', c_rate: 1, duration_s: 60 }); save(); rerender(); } }, '+ add segment'),
        field({ path: 'crate_profile.dt_s', label: 'Time step', unit: 's' }, null),
        h('button', { class: 'btn small danger', onclick: () => { state.crate_profile = null; save(); rerender(); } }, 'delete profile')))
      : h('div', {}, h('button', { class: 'btn', onclick: () => {
        const segs = [];
        if (lim.peak_discharge_c && lim.peak_discharge_duration_s) segs.push({ kind: 'discharge', c_rate: lim.peak_discharge_c, duration_s: lim.peak_discharge_duration_s });
        segs.push({ kind: 'discharge', c_rate: lim.cont_discharge_c || 1, duration_s: 1800 });
        segs.push({ kind: 'rest', c_rate: 0, duration_s: 300 });
        if (lim.charge_c) segs.push({ kind: 'charge', c_rate: lim.charge_c, duration_s: 1800 });
        state.crate_profile = { segments: segs, dt_s: 1 }; save(); rerender();
      } }, 'Create profile from the limits above'))));
  root.append(h('div', { style: 'margin-top:14px' }, h('button', { class: 'btn primary', onclick: () => go('run') }, 'Next: run the analysis →')));
}
