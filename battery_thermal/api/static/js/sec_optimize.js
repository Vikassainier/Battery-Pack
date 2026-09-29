// Step 10 - Optimise the cooling design.
import { state, buildRequest, setValue } from './state.js';
import { api } from './api.js';
import { go } from './app.js';
import { h, card, chip, fmt, table, toast, issuesList } from './ui.js';
import { runAnalysis, staleBanner } from './common.js';

const VAR_LABEL = { flow_lpm: 'Coolant flow', channel_height_mm: 'Channel height', channel_width_mm: 'Channel width', n_channels: 'Number of channels', tim_thickness_mm: 'TIM thickness' };

function designCell(d) {
  return h('div', { style: 'font-size:12.5px;line-height:1.35' }, `${fmt(d.flow_lpm, 3)} L/min`, h('br'), `${fmt(d.channel_width_mm, 3)}×${fmt(d.channel_height_mm, 3)} mm × ${d.n_channels}`, h('br'), `TIM ${fmt(d.tim_thickness_mm, 3)} mm`);
}

function apply(d, rerender) {
  setValue('cold_plate.flow_lpm', Number(d.flow_lpm.toPrecision(4)), 'user', 'high', 'from the design optimiser');
  setValue('cold_plate.channel_height_mm', Number(d.channel_height_mm.toPrecision(4)), 'user', 'high', 'from the design optimiser');
  setValue('cold_plate.channel_width_mm', Number(d.channel_width_mm.toPrecision(4)), 'user', 'high', 'from the design optimiser');
  setValue('cold_plate.n_channels', d.n_channels, 'user', 'high', 'from the design optimiser');
  setValue('cold_plate.tim_thickness_mm', Number(d.tim_thickness_mm.toPrecision(4)), 'user', 'high', 'from the design optimiser');
  toast('Design applied to the cold-plate inputs - re-run the analysis to confirm');
  runAnalysis().then(() => { state.ui.opt = null; rerender(); }).catch(e => toast(e.message, 'error'));
}

function view(root, o, rerender) {
  if (!o.ok) { root.append(h('div', { class: 'banner error' }, o.message), issuesList(o.issues || [])); return; }
  const b = o.base;
  const row = (label, m, cls) => [label, designCell(m.design), fmt(m.t_hot_max_c, 4), fmt(m.dt_pack_max_k, 3), fmt(m.dp_total_kpa, 4), fmt(m.velocity_m_s, 3), `${fmt(m.reynolds, 4)} (${m.regime})`, fmt(m.p_elec_w, 3),
    m.feasible ? chip('feasible', 'ok') : chip(Object.entries(m.violations).filter(([, v]) => v > 1e-9).map(([k]) => k).join(', ') || 'violates', 'fail')];
  const heads = ['', 'Design', 'T_hot,max [°C]', 'ΔT cell-cell [K]', 'ΔP total [kPa]', 'v [m/s]', 'Re', 'Pump el. [W]', 'Status'];
  root.append(card(`Search result - ${o.n_evaluated} designs evaluated, ${o.n_feasible} feasible`, `${o.objective}. Variables: ${Object.values(o.variables).join(', ')}. ${o.note}`,
    table(heads, [row('Current design', b), ...o.best.map((m, i) => row(`#${i + 1}`, m)), ...(o.closest_infeasible || []).map((m, i) => row(`closest #${i + 1}`, m))]),
    o.best.length ? h('div', { class: 'pill-row' }, ...o.best.slice(0, 3).map((m, i) => h('button', { class: 'btn', onclick: () => apply(m.design, rerender) }, `Apply design #${i + 1}`))) : h('div', { class: 'banner warn' }, 'No candidate meets all constraints: the closest designs are listed above. Consider a lower coolant inlet temperature, a larger safety margin or a different plate concept.')));
}

export async function renderOptimize(root) {
  const rerender = () => { root.replaceChildren(); renderOptimize(root); };
  root.append(h('h1', {}, '10 · Optimise cooling design'));
  const st = staleBanner(); if (st) root.append(st);
  if (!state.cold_plate) { root.append(h('div', { class: 'banner' }, 'Define a cold plate first (step 6).'), h('button', { class: 'btn primary', onclick: () => go('cooling') }, 'Open thermal & cooling parameters')); return; }
  const sel = new Set(state.ui.optVars || Object.keys(VAR_LABEL));
  root.append(card('What to optimise', 'Finds the lowest pump power that still meets the target cell temperature, cell-to-cell ΔT, coolant outlet, pressure-drop and velocity limits. The heat generation from the last run is held fixed.',
    h('div', { class: 'pill-row' }, ...Object.entries(VAR_LABEL).map(([k, l]) => h('label', { class: 'f', style: 'flex-direction:row;gap:6px;align-items:center' },
      h('input', { type: 'checkbox', style: 'width:auto', checked: sel.has(k), onchange: e => { e.target.checked ? sel.add(k) : sel.delete(k); state.ui.optVars = [...sel]; } }), l)))));
  const out = h('div');
  const btn = h('button', { class: 'btn primary' }, '▶ Search for better designs');
  btn.onclick = async () => {
    btn.disabled = true; out.replaceChildren(h('span', { class: 'spinner' }), ' Searching…');
    try { const o = await api('/api/optimize', { json: { request: buildRequest(), variables: [...sel] } }); state.ui.opt = o; out.replaceChildren(); view(out, o, rerender); }
    catch (e) { out.replaceChildren(h('div', { class: 'banner error' }, e.message)); }
    btn.disabled = false;
  };
  root.append(h('div', { style: 'margin:12px 0' }, btn), out);
  if (state.ui.opt) view(out, state.ui.opt, rerender);
  root.append(h('div', { style: 'margin-top:14px' }, h('button', { class: 'btn primary', onclick: () => go('report') }, 'Next: generate the engineering report →')));
}
