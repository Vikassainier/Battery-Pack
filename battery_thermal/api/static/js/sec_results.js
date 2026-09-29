// Steps 7-9 - Run analysis + dashboard, heat-load review, cooling-requirement review.
import { state, getPath } from './state.js';
import { go } from './app.js';
import { h, card, chip, statusChip, toast, fmt, table, kv, issuesList, plot, plotDiv, tabs } from './ui.js';
import { runAnalysis, needResult, staleBanner, kpiGroups, openTrace, openDetails, fmtKw } from './common.js';

const later = fn => setTimeout(fn, 0);
const T = (s, key) => (s[key] || []);

// --------------------------------------------------------------------------------------------------------------------
// Step 7 - run
// --------------------------------------------------------------------------------------------------------------------
function readiness() {
  const c = state.cell, p = state.pack;
  const rows = [
    ['Cell parameters confirmed', !!c.confirmed, 'confirm', 'Review and confirm the cell parameters (step 2).'],
    ['Cell capacity, voltage, resistance present', c.capacity_ah != null && c.v_nom != null && (c.r_dc_mohm != null || c.r_vs_soc || c.r_vs_temp || c.r_map), 'confirm', 'Capacity, nominal voltage and a resistance value/table are required.'],
    ['Battery configuration complete', ['ns', 'np', 'n_modules', 'cells_per_module'].every(k => Number.isFinite(p[k])), 'pack', 'Ns, Np, modules and cells per module.'],
    ['Electrical load defined', !!state.cycle || !!state.crate_profile, 'cycle', 'Upload a driving cycle (step 4) or define a C-rate duty profile (step 5).'],
    ['Thermal mass known (mass & cp)', c.mass_kg != null && c.cp_j_kg_k != null, 'confirm', 'Needed for temperature prediction; heat/flow sizing works without it.', true],
    ['Cold plate defined', !!state.cold_plate, 'cooling', 'Needed for temperature, pressure drop and pump sizing.', true],
  ];
  return table(['Requirement', 'Status', ''], rows.map(([l, ok, step, help, optional]) => [
    h('div', {}, l, h('div', { class: 'help' }, help)), ok ? chip('ready', 'ok') : chip(optional ? 'optional' : 'missing', optional ? 'warning' : 'error'),
    ok ? '' : h('button', { class: 'btn small', onclick: () => go(step) }, 'Fix →')]));
}

function checksTable(res, includeSupp = true) {
  const mk = list => table(['#', 'Check', 'Status', 'Predicted / actual', 'Limit', 'Assessment'],
    list.map(c => [c.id, h('b', {}, c.name), statusChip(c.status), c.value, c.limit, h('span', { style: 'font-size:12.5px' }, c.message)]), { rowClick: i => showCheck(list[i]) });
  const core = res.checks.filter(c => !c.supplementary), supp = res.checks.filter(c => c.supplementary);
  return h('div', {}, mk(core), includeSupp && supp.length ? h('details', { class: 'fold' }, h('summary', {}, `Supplementary checks (${supp.length}) - ${supp.filter(c => c.status === 'FAIL').length} fail, ${supp.filter(c => c.status === 'WARNING').length} warning`), mk(supp)) : null);
}

function showCheck(c) {
  const body = [h('div', { class: 'banner' }, `${c.name}: `, statusChip(c.status), ` ${c.value} (limit ${c.limit || '–'})`), h('p', {}, c.message)];
  if (c.details && c.details.assessments) body.push(table(['Direction', 'Max C', 'Continuous', 'Peak', 'Pulse duration', 'Result'], c.details.assessments.map(a => [a.label, fmt(a.max_c, 3), a.cont ?? '–', a.peak ?? '–', a.peak_duration_s ?? '–', a.message])));
  if (c.details && c.details.flags) body.push(issuesList(c.details.flags.map(f => ({ severity: f.severity === 'fail' ? 'error' : f.severity, code: f.code, field: '', message: f.message }))));
  if (c.details && c.details.components) body.push(kv(Object.entries(c.details.components).map(([k, v]) => [k.replace(/_/g, ' '), `${fmt(v)} K`])));
  if (c.trace_id) body.push(h('button', { class: 'btn', onclick: () => openTrace(c.trace_id) }, 'Show calculation trace'));
  openDetails(`Check ${c.id}`, ...body);
}

function tempChart(res) {
  const s = res.series;
  if (!s.t_cell_c && !s.t_uncooled_c) return null;
  const el = plotDiv('tall');
  const tr = [];
  if (s.t_hot_c) tr.push({ x: s.t, y: s.t_hot_c, name: 'Hottest cell (predicted)', line: { color: '#c0392b', width: 2 } });
  if (s.t_hot_c) tr.push({ x: s.t, y: s.t_cell_c, name: 'Average cell', line: { color: '#0b6fb8', width: 2 } });
  if (s.t_coolant_out_c && s.t_coolant_out_c.some(v => v != null)) tr.push({ x: s.t, y: s.t_coolant_out_c, name: 'Coolant outlet', line: { color: '#2a9d6f', width: 1.5, dash: 'dot' } });
  if (s.t_uncooled_c) tr.push({ x: s.t, y: s.t_uncooled_c, name: 'Without active cooling', line: { color: '#6b7a8f', width: 1.5, dash: 'dash' } });
  const tgt = getPath(state, 'pack.t_target_max_c');
  later(() => plot(el, tr, { title: { text: 'Predicted temperatures [°C]', font: { size: 13 } }, xaxis: { title: 'time [s]' }, yaxis: { title: '°C' },
    shapes: [{ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: tgt, y1: tgt, line: { color: '#a86400', width: 1.5, dash: 'dash' } }],
    annotations: [{ xref: 'paper', x: 1, y: tgt, text: 'target', showarrow: false, yshift: 10, font: { color: '#a86400', size: 11 } }] }));
  return el;
}

function designLines(res) {
  const d = res.design, lines = [];
  const add = (v, name, color, dash) => { if (v != null) lines.push({ type: 'line', xref: 'paper', x0: 0, x1: 1, y0: v / 1000, y1: v / 1000, line: { color, width: 1.3, dash } }); };
  const c = d.candidates;
  add(c.peak?.value_w, 'peak', '#c0392b', 'dot');
  if (c.moving_average?.available) add(c.moving_average.value_w, 'moving avg', '#d9822b', 'dash');
  if (c.sustained?.available) add(c.sustained.value_w, 'sustained', '#a0459b', 'dash');
  add(d.q_design_w, 'design', '#0b6fb8', 'solid');
  return lines;
}

function designNotes(res) {
  const d = res.design, c = d.candidates, out = [];
  const add = (v, txt, color) => { if (v != null) out.push({ xref: 'paper', x: 0.995, xanchor: 'right', y: v / 1000, yanchor: 'bottom', text: txt, showarrow: false, font: { size: 10, color } }); };
  add(c.peak?.value_w, 'peak', '#c0392b');
  if (c.moving_average?.available) add(c.moving_average.value_w, 'moving avg', '#d9822b');
  if (c.sustained?.available) add(c.sustained.value_w, 'sustained', '#a0459b');
  add(d.q_design_w, 'design (×SF)', '#0b6fb8');
  return out;
}

function dashboard(res) {
  const box = h('div', {});
  const bad = res.status === 'blocked';
  box.append(h('div', { class: `banner ${bad ? 'error' : res.status === 'completed_with_errors' ? 'warn' : ''}` },
    bad ? 'The analysis was blocked by validation errors. Fix them and run again.' : res.status === 'completed_with_errors' ? 'The analysis completed, but errors were found in the results (see below).' : 'Analysis completed.',
    !bad && res.data_quality.n_assumed ? ` ${res.data_quality.n_assumed} parameter(s) are unconfirmed engineering assumptions (${res.data_quality.n_low} low confidence) - see "Assumptions & data quality".` : ''));
  if (res.issues && res.issues.length) {
    const errs = res.issues.filter(i => i.severity !== 'info');
    box.append(card(`Validation & model notes (${errs.length} error/warning${errs.length === 1 ? '' : 's'}, ${res.issues.length - errs.length} info)`, null, issuesList(res.issues)));
  }
  if (bad) return box;
  box.append(card('Key results', 'Click any value to see exactly how it was calculated (input → formula → intermediate → result).', kpiGroups(res.kpis)));
  box.append(card('Thermal performance checks', 'Every check is shown - failed and not-evaluated checks are never hidden. Click a row for details.', checksTable(res)));
  const tc = tempChart(res);
  const hp = plotDiv('tall');
  const s = res.series;
  const pl = [{ x: s.t, y: s.q_pack_kw, name: 'Pack heat generation', line: { color: '#0b6fb8', width: 1.5 } }];
  later(() => plot(hp, pl, { title: { text: 'Pack heat generation [kW] and design levels', font: { size: 13 } }, xaxis: { title: 'time [s]' }, yaxis: { title: 'kW' }, shapes: designLines(res), annotations: designNotes(res) }));
  box.append(card('Transient overview', null, h('div', { class: 'two' }, hp, tc || h('div', { class: 'banner' }, `Temperature prediction not available: ${res.thermal.no_temperature_reason || ''}.`))));
  box.append(h('div', { class: 'pill-row' }, h('button', { class: 'btn primary', onclick: () => go('heat') }, 'Review heat load →'), h('button', { class: 'btn primary', onclick: () => go('coolreq') }, 'Review cooling requirement →'),
    h('button', { class: 'btn', onclick: () => go('optimize') }, 'Optimise cooling design →'), h('button', { class: 'btn', onclick: () => go('report') }, 'Generate report →')));
  return box;
}

export async function renderRun(root) {
  root.append(h('h1', {}, '7 · Run analysis'), card('Readiness', 'The analysis refuses to run on missing or inconsistent inputs and lists every problem.', readiness()));
  const out = h('div');
  const btn = h('button', { class: 'btn primary', style: 'font-size:15px;padding:10px 22px' }, '▶ Run analysis');
  const doRun = async () => {
    btn.disabled = true; out.replaceChildren(h('span', { class: 'spinner' }), ' Running analysis…');
    try { await runAnalysis(); out.replaceChildren(dashboard(state.ui.result)); } catch (e) { out.replaceChildren(h('div', { class: 'banner error' }, `The request was rejected: ${e.message}`, h('div', { class: 'help' }, 'Some required inputs are still missing - see the readiness list above.'))); }
    btn.disabled = false;
  };
  btn.onclick = doRun;
  root.append(h('div', { style: 'margin:14px 0' }, btn), out);
  if (state.ui.result) { const st = staleBanner(); if (st) out.append(st); out.append(dashboard(state.ui.result)); }
}

// --------------------------------------------------------------------------------------------------------------------
// Step 8 - heat load
// --------------------------------------------------------------------------------------------------------------------
function graphs(res) {
  const s = res.series, t = s.t;
  const G = [
    ['Graph 1 · Battery current vs time', [['Pack current [A]', s.i_pack_a, '#0b6fb8'], ['Module current [A]', s.i_module_a, '#2a9d6f'], ['Cell current [A]', s.i_cell_a, '#d9822b']], 'A'],
    ['Graph 2 · SOC vs time', [['SOC [%]', s.soc_pct, '#0b6fb8']], '%'],
    ['Graph 3 · C-rate vs time', [['C-rate [C]', s.c_rate, '#a0459b']], 'C'],
    ['Graph 4 · Cell heat generation vs time', [['Total cell heat [W]', s.q_cell_w, '#c0392b'], ['Joule I²R [W]', s.q_joule_cell_w, '#0b6fb8'], ['Entropic [W]', s.q_rev_cell_w, '#2a9d6f']], 'W per cell'],
    ['Graph 5 · Pack heat generation vs time', [['Pack heat [kW]', s.q_pack_kw, '#c0392b']], 'kW'],
    ['Graph 6 · Cumulative heat generation', [['Total heat [kWh]', s.cum_heat_kwh, '#c0392b'], ['Joule part [kWh]', s.cum_joule_kwh, '#0b6fb8']], 'kWh'],
    ['Graph 7 · Battery power vs time', [['Battery terminal power [kW]', s.p_batt_kw, '#0b6fb8'], ...(s.p_aux_kw ? [['Auxiliary power [kW]', s.p_aux_kw, '#d9822b']] : [])], 'kW'],
  ];
  return h('div', { class: 'two' }, G.map(([title, lines, unit], i) => {
    const el = plotDiv();
    later(() => plot(el, lines.map(([n, y, c]) => ({ x: t, y, name: n, line: { color: c, width: 1.4 } })),
      { title: { text: title, font: { size: 13 } }, xaxis: { title: 'time [s]' }, yaxis: { title: unit }, shapes: i === 4 ? designLines(res) : [], annotations: i === 4 ? designNotes(res) : [] }));
    return el;
  }));
}

function downloadCsv(res) {
  const s = res.series;
  const cols = [['time_s', 't'], ['soc_pct', 'soc_pct'], ['i_pack_a', 'i_pack_a'], ['i_module_a', 'i_module_a'], ['i_cell_a', 'i_cell_a'], ['c_rate', 'c_rate'], ['r_cell_mohm', 'r_cell_mohm'], ['q_joule_cell_w', 'q_joule_cell_w'],
    ['q_entropic_cell_w', 'q_rev_cell_w'], ['q_cell_w', 'q_cell_w'], ['q_module_w', 'q_module_w'], ['q_pack_kw', 'q_pack_kw'], ['battery_power_kw', 'p_batt_kw'], ['state', 'state'], ['t_cell_c', 't_cell_c'], ['t_hot_c', 't_hot_c']].filter(([, k]) => s[k]);
  const lines = [cols.map(c => c[0]).join(',')];
  for (let i = 0; i < s.t.length; i++) lines.push(cols.map(([, k]) => s[k][i]).join(','));
  const a = h('a', { href: URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' })), download: 'timestep_results.csv' });
  document.body.append(a); a.click(); a.remove();
}

function stepTable(res) {
  const s = res.series, n = s.t.length, per = 60;
  let page = 0;
  const box = h('div');
  const draw = () => {
    box.replaceChildren();
    const from = page * per, to = Math.min(n, from + per);
    const rows = [];
    for (let i = from; i < to; i++) rows.push([fmt(s.t[i], 5), fmt(s.soc_pct[i], 5), fmt(s.i_pack_a[i], 4), fmt(s.c_rate[i], 3), fmt(s.r_cell_mohm[i], 4), fmt(s.q_joule_cell_w[i], 4), fmt(s.q_rev_cell_w[i], 3), fmt(s.q_cell_w[i], 4), fmt(s.q_module_w[i], 4), fmt(s.q_pack_kw[i], 4), fmt(s.p_batt_kw[i], 4), chip(s.state[i], s.state[i] === 'discharge' ? 'info' : s.state[i] === 'rest' ? 'na' : 'ok')]);
    box.append(table(['Time [s]', 'SOC [%]', 'I_pack [A]', 'C-rate', 'R_cell [mΩ]', 'Q_joule [W]', 'Q_entropic [W]', 'Q_cell [W]', 'Q_module [W]', 'Q_pack [kW]', 'P_batt [kW]', 'State'], rows, { scroll: true, num: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10] }),
      h('div', { class: 'row', style: 'align-items:center;margin-top:8px' }, h('span', { class: 'muted' }, `rows ${from + 1}-${to} of ${n}${s.step_decimation > 1 ? ` (decimated ×${s.step_decimation} for display; full resolution used in the calculation)` : ''}`),
        h('div', {}, h('button', { class: 'btn small', disabled: page === 0, onclick: () => { page--; draw(); } }, '‹ prev'), ' ', h('button', { class: 'btn small', disabled: to >= n, onclick: () => { page++; draw(); } }, 'next ›'), ' ',
          h('button', { class: 'btn small', onclick: () => downloadCsv(res) }, 'Download CSV'))));
  };
  draw();
  return box;
}

export function renderHeat(root) {
  const res = needResult(root, '8 · Review heat load');
  if (!res) return;
  const H = res.heat, D = res.design, P = res.pack;
  root.append(h('h1', {}, '8 · Review heat load'));
  const st = staleBanner(); if (st) root.append(st);
  root.append(h('div', { class: 'flow' }, ...res.explanations.chain.flatMap((c, i) => [h('span', { class: 'node' }, c.replace(' →', '')), i < res.explanations.chain.length - 1 ? h('span', { class: 'arrow' }, '→') : null])));

  root.append(card('Transient results - heat generation at every time step', `${H.n_samples} samples · sample-and-hold integration · resistance: ${res.models.resistance.name}`, graphs(res)));

  const trow = (label, cell, mod, pack, id) => [label, cell, mod, h('a', { onclick: () => openTrace(id), style: 'cursor:pointer' }, pack)];
  root.append(card('Thermal load results', 'Instantaneous, average and energy.',
    table(['', 'Per cell', 'Per module', 'Pack'], [
      trow('Maximum (instantaneous)', `${fmt(H.max_cell_heat_w)} W`, `${fmt(H.max_module_heat_w)} W`, `${fmt(H.max_pack_heat_kw)} kW`, 'heat.q_pack_pk'),
      trow('Average (time-weighted)', `${fmt(H.avg_cell_heat_w)} W`, `${fmt(H.avg_module_heat_w)} W`, `${fmt(H.avg_pack_heat_kw)} kW`, 'heat.q_pack_avg'),
      trow('Total heat generated over the cycle', '', '', `${fmt(H.total_heat_kwh, 4)} kWh  (Joule ${fmt(H.joule_heat_kwh, 4)} · reversible ${fmt(H.reversible_heat_kwh, 4)})`, 'heat.e_total'),
    ]),
    h('div', { class: 'banner', style: 'margin-top:10px' }, res.explanations.electrical_vs_heat,
      ` Electrical energy delivered: ${fmt(H.electrical.terminal_discharge_kwh, 4)} kWh; discharge efficiency ${fmt(H.electrical.discharge_efficiency_pct, 4)} %.`),
    H.entropic.included ? null : h('div', { class: 'banner warn' }, h('b', {}, 'Entropic heat: '), H.entropic.status)));

  // design load philosophies
  const cand = D.candidates, names = { peak: 'Peak heat load', moving_average: 'Moving-average heat load', sustained: 'Sustained heat load', drive_cycle: 'Drive-cycle thermal load' };
  const rows = Object.entries(names).map(([k, l]) => {
    const c = cand[k]; const sel = k === D.philosophy;
    return [h('b', {}, l, sel ? ' ◀ selected' : ''), c.available ? `${fmt(c.value_w / 1000, 4)} kW` : h('span', { class: 'muted' }, 'n/a'), c.available ? `${fmt(c.value_w / P.n_cells, 4)} W/cell` : '',
      h('span', { class: 'muted', style: 'font-size:12.5px' }, c.available ? (c.substitution || '') : c.note)];
  });
  const bar = plotDiv();
  const vals = Object.entries(names).filter(([k]) => cand[k].available).map(([k, l]) => [l, cand[k].value_w / 1000, k === D.philosophy]);
  later(() => plot(bar, [{ type: 'bar', orientation: 'h', y: vals.map(v => v[0]), x: vals.map(v => v[1]), marker: { color: vals.map(v => (v[2] ? '#0b6fb8' : '#9db3c9')) }, text: vals.map(v => `${fmt(v[1], 3)} kW`), textposition: 'auto' },
    { type: 'bar', orientation: 'h', y: ['Design load (× SF)'], x: [D.q_design_w / 1000], marker: { color: '#2a9d6f' }, text: [`${fmt(D.q_design_w / 1000, 3)} kW`], textposition: 'auto' }],
    { title: { text: 'Design heat-load candidates [kW]', font: { size: 13 } }, showlegend: false, margin: { l: 190, r: 20, t: 34, b: 30 }, xaxis: { title: 'kW' } }));
  root.append(card('Design heat load', `Q_design = (Q_relevant + Q_ambient) × SF = (${fmt(D.q_relevant_w / 1000, 4)} + ${fmt(D.q_ambient_gain_w / 1000, 3)}) × ${fmt(D.safety_factor)} = ${fmt(D.q_design_w / 1000, 4)} kW`,
    h('div', { class: 'two' }, table(['Philosophy', 'Q [kW]', 'Per cell', 'Basis'], rows), bar),
    h('div', { class: 'banner', style: 'margin-top:10px' }, h('b', {}, `${D.label}: `), D.explanation),
    h('details', { class: 'fold', open: true }, h('summary', {}, 'Peak thermal load vs sustained cooling requirement'), h('p', {}, D.peak_vs_sustained)),
    h('div', { class: 'row', style: 'align-items:center' }, h('span', { class: 'muted' }, 'Change the philosophy on step 6 and re-run to compare.'), h('button', { class: 'btn small', onclick: () => go('cooling') }, 'Open thermal & cooling parameters'))));

  // models used
  const M = res.models;
  root.append(card('Models used', 'Which resistance model, OCV model and entropic treatment produced these numbers.',
    kv([['Resistance model', `${M.resistance.name}`], ['Description', M.resistance.description], ['Extrapolation policy', M.resistance.extrapolation_policy], ['Resistance scale / charge factor', `${M.resistance.scale} / ${M.resistance.charge_factor}`],
      ['OCV model', M.ocv.description], ['Entropic heat', M.entropic.status], ['Load source', `${M.load.source} (${M.load.kind})`], ['Load notes', M.load.notes.join(' ')], ['SOC source', M.soc_source], ['Time integration', M.time_integration]]),
    M.resistance.usage.length ? h('div', { style: 'margin-top:10px' }, table(['Table', 'Axis', 'Data range', 'Used range', 'Evaluations', 'Outside data'], M.resistance.usage.map(u => [u.table, u.axis, `${fmt(u.data_range[0])} … ${fmt(u.data_range[1])}`, u.used_range[0] == null ? '–' : `${fmt(u.used_range[0])} … ${fmt(u.used_range[1])}`, u.evaluations, u.outside_data_range]))) : null));

  root.append(card('Time-step results', 'Time, SOC, current, C-rate, cell resistance, Joule heat, entropic heat, total cell / module / pack heat, battery power and charge/discharge state at each step.', stepTable(res)));
  root.append(h('div', { style: 'margin-top:14px' }, h('button', { class: 'btn primary', onclick: () => go('coolreq') }, 'Next: review cooling requirement →')));
}

// --------------------------------------------------------------------------------------------------------------------
// Step 9 - cooling requirement
// --------------------------------------------------------------------------------------------------------------------
export function renderCoolingReq(root) {
  const res = needResult(root, '9 · Review cooling requirement');
  if (!res) return;
  const C = res.cooling, S = res.sizing, D = res.design, cp = res.cold_plate, hy = res.hydraulics, TH = res.thermal;
  root.append(h('h1', {}, '9 · Review cooling requirement'));
  const st = staleBanner(); if (st) root.append(st);

  const cap = S.capacity;
  root.append(card('Cooling system sizing', 'Electrical load → heat generation → thermal accumulation → cooling requirement → sizing.',
    h('div', { class: 'row' },
      h('div', {}, h('h4', {}, 'Cooling capacity'), h('div', { class: 'big' }, `${fmt(cap.required_kw, 4)} kW`), h('div', { class: 'muted' }, `required (${cap.philosophy})`),
        h('div', { style: 'margin-top:6px' }, `Design capacity = ${fmt(cap.required_kw, 4)} × ${fmt(cap.safety_factor)} = `, h('b', {}, `${fmt(cap.design_kw, 4)} kW`), ` → recommended ≥ `, h('b', {}, `${fmt(cap.recommended_kw, 3)} kW`))),
      h('div', {}, h('h4', {}, 'Coolant flow'), h('div', { class: 'big' }, `${fmt(S.flow.required_lpm, 4)} L/min`), h('div', { class: 'muted' }, `required at ΔT = ${fmt(S.flow.dt_cool_k)} K (${S.flow.dt_basis})`),
        S.flow.actual_lpm != null ? h('div', { style: 'margin-top:6px' }, `Flow used for the plate & hydraulics: ${fmt(S.flow.actual_lpm, 4)} L/min (${S.flow.flow_source})`) : null),
      S.inlet_temperature ? h('div', {}, h('h4', {}, 'Coolant temperature'), h('div', { class: 'big' }, `${fmt(S.inlet_temperature.t_in_recommended_c, 3)} °C`), h('div', { class: 'muted' }, `recommended max. inlet (specified ${fmt(S.inlet_temperature.specified_c)} °C)`),
        S.inlet_temperature.chiller_required ? h('div', { class: 'banner warn', style: 'margin-top:6px' }, S.inlet_temperature.below_ambient ? 'This supply temperature is below the ambient: a radiator alone cannot deliver it - active refrigeration (chiller) is required.' : 'This supply temperature is within 5 K of the ambient: a radiator alone is marginal - an active refrigeration circuit (chiller) is likely required.') : null) : null)));

  // flows for cell/module/pack
  root.append(card('Required coolant flow - cell, module, pack', `ṁ = Q / (cp·ΔT) with cp = ${fmt(C.props.cp, 5)} J/(kg·K), ρ = ${fmt(C.props.rho, 5)} kg/m³ at ${fmt(C.props.t_eval_c, 3)} °C; ${C.props.description}`,
    table(['Level', 'Design heat', 'Mass flow [kg/s]', 'Volume flow [L/min]', ''], ['cell', 'module', 'pack'].map(l => [l[0].toUpperCase() + l.slice(1), `${fmt(C[l].q_w, 4)} W`, fmt(C[l].m_dot_kg_s, 4), `${fmt(C[l].lpm, 4)}${l === 'cell' ? ` (${fmt(C[l].ml_min, 4)} mL/min)` : ''}`,
      h('a', { onclick: () => openTrace(`cool.lpm_${l}`), style: 'cursor:pointer' }, 'how?')]))));

  if (cp) {
    const rs = [['Cell contact (interface)', cp.r_contact, '#d9822b'], ['TIM', cp.r_tim, '#a0459b'], ['Plate conduction', cp.r_plate, '#6b7a8f'], ['Coolant convection', cp.r_conv, '#0b6fb8']];
    const tot = cp.r_total;
    root.append(card('Cold-plate thermal resistance chain (per cell)', 'R_total = R_contact + R_TIM + R_plate + R_convection',
      h('div', { class: 'row' },
        h('div', {}, table(['Element', 'R [K/W]', 'Share', ''], [...rs.map(([n, r, c]) => [n, fmt(r, 4), `${fmt(r / tot * 100, 3)} %`, h('span', { class: 'bar', style: `width:${Math.max(2, r / tot * 140)}px;background:${c}` })]),
          [h('b', {}, 'Overall R_total'), h('b', {}, fmt(tot, 4)), '100 %', h('a', { onclick: () => openTrace('cp.r_total'), style: 'cursor:pointer' }, 'how?')]])),
        h('div', {}, kv([['Overall U (per cell contact area)', `${fmt(cp.u_cell_w_m2k, 4)} W/(m²·K)`], ['U (plate footprint area)', `${fmt(cp.u_plate_w_m2k, 4)} W/(m²·K)`], ['Convective coefficient h', `${fmt(cp.h_w_m2k, 4)} W/(m²·K)`],
          ['Nusselt number', fmt(cp.nusselt, 4)], ['Cell-to-coolant ΔT at design heat', `${fmt(res.design.q_design_w / res.pack.n_cells * cp.r_total, 3)} K`], ['NTU · effectiveness ε', `${fmt(cp.ntu, 3)} · ${fmt(cp.effectiveness, 3)}`], ['Plate conductivity', `${fmt(cp.k_plate_w_mk)} W/(m·K)`]]))),
      h('details', { class: 'fold' }, h('summary', {}, 'Thermal resistance [K/W] vs overall heat-transfer coefficient U [W/(m²·K)]'),
        h('p', {}, 'Thermal resistance R [K/W] is the absolute temperature rise per watt through one specific part of a specific geometry; resistances in series simply add. The overall heat-transfer coefficient U [W/(m²·K)] = 1/(R·A_ref) normalises the same resistance by a reference area, so it depends on which area is chosen (here the cell contact area or the plate footprint). U is used to compare layers, materials and technologies independent of size; R is used to compute temperatures for the actual design.')),
      cp.warnings.length ? issuesList(cp.warnings.map(w => ({ severity: 'warning', code: 'COLDPLATE_NOTE', field: '', message: w }))) : null));
  } else root.append(card('Cold plate', null, h('div', { class: 'banner' }, 'No cold plate defined: define one on step 6 to obtain temperatures, thermal resistance chain, pressure drop and pump sizing.')));

  if (hy) root.append(card('Coolant flow & pressure drop (per plate channel)', 'Correlations: Shah-London (laminar), Haaland/Gnielinski (turbulent), linear blend in the transition region.',
    h('div', { class: 'row' },
      kv([['Flow velocity', `${fmt(hy.velocity_m_s, 4)} m/s`], ['Hydraulic diameter', `${fmt(hy.dh_m * 1000, 4)} mm`], ['Reynolds number', `${fmt(hy.reynolds, 5)} (${hy.regime})`], ['Friction factor (Darcy)', fmt(hy.f_darcy, 4)]]),
      kv([['Channel ΔP', `${fmt(hy.dp_channel_pa / 1000, 4)} kPa`], ['Minor losses', `${fmt(hy.dp_minor_pa / 1000, 4)} kPa`], ['Cold-plate ΔP', `${fmt(hy.dp_plates_total_pa / 1000, 4)} kPa`], ['External loop ΔP', `${fmt(hy.dp_external_pa / 1000, 4)} kPa`],
        ['Total ΔP', `${fmt(hy.dp_total_pa / 1e5, 4)} bar`]]),
      kv([['Pack flow', `${fmt(hy.q_pack_lpm, 4)} L/min`], ['Hydraulic power', `${fmt(hy.p_hyd_w, 4)} W`], ['Pump electrical power', `${fmt(hy.p_elec_w, 4)} W`], ['Head', `${fmt(hy.head_m, 4)} m`]])),
    hy.flags.length ? h('div', { style: 'margin-top:10px' }, issuesList(hy.flags.map(f => ({ severity: f.severity === 'fail' ? 'error' : f.severity, code: f.code, field: '', message: f.message })))) : null));

  if (S.pump) {
    const p = S.pump, r = S.radiator;
    root.append(card('Pump and heat-exchanger estimates', null, h('div', { class: 'two' },
      h('div', {}, h('h4', {}, 'Pump'), kv([['Flow', `${fmt(p.flow_lpm, 4)} L/min`], ['Pressure', `${fmt(p.dp_bar, 3)} bar`], ['Hydraulic power', `${fmt(p.p_hydraulic_w, 4)} W`], ['Estimated electrical power', `${fmt(p.p_electrical_w, 4)} W`], ['Suggested duty point', `${fmt(p.duty_flow_lpm, 4)} L/min @ ${fmt(p.duty_dp_bar, 3)} bar`]]), h('div', { class: 'help' }, p.allowances)),
      h('div', {}, h('h4', {}, 'Heat exchanger / radiator (indicative)'), kv([['Required heat rejection capacity', `${fmt(r.q_reject_w / 1000, 4)} kW (design load + pump heat)`], ['Coolant-side flow', `${fmt(r.coolant_flow_lpm, 4)} L/min`],
        ['Coolant into / out of radiator', `${fmt(r.coolant_inlet_to_radiator_c, 3)} / ${fmt(r.coolant_outlet_from_radiator_c, 3)} °C`], ['Ambient air inlet', `${fmt(r.air_inlet_c)} °C (ITD ${fmt(r.itd_k, 3)} K)`],
        ['Capacity per K of ITD', r.ua_required_w_k != null ? `${fmt(r.ua_required_w_k, 4)} W/K` : 'n/a - no positive temperature difference'], ['Air flow (assumed ΔT_air ' + fmt(r.air_dt_assumed_k) + ' K)', `${fmt(r.air_volume_flow_m3_s, 3)} m³/s`]]),
        ...r.notes.map(n => h('div', { class: 'banner warn', style: 'margin-top:6px' }, n))))));
  }

  const m = res.margins;
  root.append(card('Design margin', null, h('div', { class: 'row' },
    h('div', {}, h('h4', {}, 'Cooling margin = installed / required'), m.cooling.available ? h('div', {}, h('span', { class: 'big' }, m.cooling.margin_pct == null ? '∞' : (m.cooling.margin_pct > 999 ? '> 999 %' : `${fmt(m.cooling.margin_pct, 3)} %`)), ' ', chip(m.cooling.class, m.cooling.class.toLowerCase()),
      h('div', { class: 'help' }, `installed ${fmt(m.cooling.installed_w / 1000, 4)} kW / required ${fmt(m.cooling.required_w / 1000, 4)} kW · limits: warning < ${m.cooling.warn_pct} %, target ≥ ${m.cooling.target_pct} % (configurable)`)) : h('span', { class: 'muted' }, 'n/a')),
    h('div', {}, h('h4', {}, 'Thermal margin = allowed − predicted max T'), m.thermal.available ? h('div', {}, h('span', { class: 'big' }, `${fmt(m.thermal.margin_k, 3)} K`), ' ', chip(m.thermal.class, m.thermal.class.toLowerCase()),
      h('div', { class: 'help' }, `${fmt(m.thermal.t_allow_c)} °C allowed − ${fmt(m.thermal.t_pred_c, 4)} °C predicted`)) : h('span', { class: 'muted' }, 'n/a - no temperature prediction')))));
  root.append(card('Thermal performance checks', null, checksTable(res)));
  if (TH.has_temperature) root.append(card('Thermal accumulation', `C_pack = ${fmt(TH.c_pack_j_k, 5)} J/K · time constant τ = ${fmt(TH.tau_s, 4)} s · peak stored heat ${fmt(TH.stored_heat_kwh, 3)} kWh · ambient UA ${fmt(TH.ambient_ua_w_k, 3)} W/K (${TH.ambient_ua_source})`, tempChart(res)));
  root.append(h('div', { style: 'margin-top:14px' }, h('button', { class: 'btn primary', onclick: () => go('optimize') }, 'Next: optimise the cooling design →')));
}
