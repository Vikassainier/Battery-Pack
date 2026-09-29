// Sensitivity analysis: one-at-a-time perturbations, effect on heat, capacity, flow and cell temperature.
import { state, buildRequest } from './state.js';
import { api } from './api.js';
import { h, card, chip, fmt, table, plot, plotDiv, issuesList } from './ui.js';
import { needResult, staleBanner } from './common.js';

const later = fn => setTimeout(fn, 0);
const COL = { max_heat_kw: '#c0392b', q_required_kw: '#0b6fb8', flow_lpm: '#2a9d6f', t_hot_max_c: '#d9822b' };

export async function runSensitivity() {
  const s = await api('/api/sensitivity', { json: { request: buildRequest() } });
  state.ui.sens = s;
  return s;
}

function view(root, s) {
  if (!s.ok) { root.append(h('div', { class: 'banner error' }, s.message || 'The sensitivity analysis could not run.'), issuesList(s.issues || [])); return; }
  const outs = s.outputs;
  root.append(card('Base case', null, h('div', { class: 'kpis' }, outs.map(o => h('div', { class: 'kpi' }, h('div', { class: 'l' }, o.label), h('div', { class: 'v' }, s.base[o.key] == null ? '–' : fmt(s.base[o.key], 4), h('small', {}, o.unit)))))));
  const tor = h('div', { class: 'two' }, outs.map(o => {
    const el = plotDiv();
    const items = (s.tornado[o.key] || []).slice(0, 12).reverse();
    later(() => plot(el, [{ type: 'bar', orientation: 'h', y: items.map(i => i.label), x: items.map(i => i.low), name: 'decrease', marker: { color: '#9db3c9' }, hovertemplate: '%{y}: %{x:.3g}<extra></extra>' },
      { type: 'bar', orientation: 'h', y: items.map(i => i.label), x: items.map(i => i.high), name: 'increase', marker: { color: COL[o.key] }, hovertemplate: '%{y}: +%{x:.3g}<extra></extra>' }],
      { title: { text: `${o.label}: change from base [${o.unit}]`, font: { size: 13 } }, barmode: 'relative', margin: { l: 170, r: 16, t: 34, b: 36 }, showlegend: false, xaxis: { title: `Δ ${o.unit}` } }));
    return el;
  }));
  root.append(card('Tornado charts', 'Bars show the largest change of each output over the tested range of each parameter (parameters ranked by impact).', tor));
  const rows = [];
  for (const p of s.params) {
    if (p.skipped) { rows.push([h('b', {}, p.label), '', h('span', { class: 'muted' }, `skipped: ${p.skipped}`), '', '', '', '']); continue; }
    p.levels.forEach((lv, i) => {
      const cells = lv.status === 'ok' ? outs.map(o => { const d = lv.delta[o.key], dp = lv.delta_pct[o.key]; return d == null ? '–' : `${fmt(lv.metrics[o.key], 4)} (${Math.abs(dp) < 0.005 ? '±0' : (d >= 0 ? '+' : '') + fmt(dp, 3)} %)`; })
        : [h('span', { class: 'muted' }, `${lv.status}${lv.reason ? ': ' + lv.reason : ''}`), '', '', ''];
      rows.push([i === 0 ? h('b', {}, `${p.label}`) : '', lv.label, ...cells]);
    });
  }
  root.append(card('Results by parameter', 'Absolute output value and percentage change vs. the base case. Blocked / skipped levels are shown, not dropped.',
    table(['Parameter', 'Change', ...outs.map(o => `${o.label} [${o.unit}]`)], rows, { scroll: true })));
}

export async function renderSensitivity(root) {
  root.append(h('h1', {}, 'Sensitivity analysis'));
  const st = staleBanner(); if (st) root.append(st);
  root.append(h('div', { class: 'banner' }, 'Each parameter is perturbed one at a time (cell resistance, ambient temperature, coolant inlet temperature, coolant flow, TIM thickness and conductivity, C-rate, SOC, initial cell temperature, channel dimensions) and the whole analysis is re-run. Takes a few seconds.'));
  const out = h('div');
  const btn = h('button', { class: 'btn primary' }, '▶ Run sensitivity analysis');
  btn.onclick = async () => {
    btn.disabled = true; out.replaceChildren(h('span', { class: 'spinner' }), ' Running about 50 analyses…');
    try { const s = await runSensitivity(); out.replaceChildren(); view(out, s); } catch (e) { out.replaceChildren(h('div', { class: 'banner error' }, e.message)); }
    btn.disabled = false;
  };
  root.append(h('div', { style: 'margin:12px 0' }, btn), out);
  if (state.ui.sens) view(out, state.ui.sens);
}
