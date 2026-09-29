// Shared helpers for the results views: run the analysis, KPI tiles, calculation-trace drawer.
import { state, buildRequest, save } from './state.js';
import { api } from './api.js';
import { h, chip, fmt, toast, statusChip, kv } from './ui.js';

export async function runAnalysis() {
  const res = await api('/api/analyze', { json: buildRequest() });
  state.ui.result = res;
  state.ui.stale = false;
  return res;
}

export const kW = w => (w == null ? null : w / 1000);

export function staleBanner() {
  return state.ui.stale ? h('div', { class: 'banner warn' }, 'Inputs changed since the last run - the results below are out of date. ',
    h('button', { class: 'btn small', onclick: async () => { await runAnalysis(); window.go(state.ui.step); } }, 'Re-run analysis')) : null;
}

/** Returns the result, or renders a "run first" prompt into root and returns null. */
export function needResult(root, title) {
  const r = state.ui.result;
  if (r && r.status !== 'blocked') return r;
  root.append(h('h1', {}, title),
    h('div', { class: r ? 'banner error' : 'banner' }, r ? 'The last run was blocked by validation errors - fix them on the Run analysis step.' : 'No results yet - run the analysis first.'),
    h('button', { class: 'btn primary', onclick: () => window.go('run') }, 'Go to Run analysis'));
  return null;
}

export function kpiTile(k) {
  const v = k.value == null ? '–' : fmt(k.value, 4);
  const cls = k.status ? k.status.toLowerCase() : '';
  return h('div', { class: `kpi ${cls}`, title: k.trace ? 'Click to see how this value was calculated' : '', onclick: () => k.trace && openTrace(k.trace) },
    h('div', { class: 'l' }, k.label), h('div', { class: 'v' }, v, h('small', {}, k.unit)), k.sub ? h('div', { class: 's' }, k.sub) : null);
}

export function kpiGroups(kpis) {
  const names = { battery: 'Battery', thermal: 'Thermal', cooling: 'Cooling' };
  return h('div', {}, Object.entries(kpis).map(([g, items]) => h('div', {}, h('h4', { class: 'kpi-group' }, names[g] || g),
    h('div', { class: 'kpis' }, items.map(kpiTile)))));
}

// ------------------------------------------------------------------------------------------------------
// traceability drawer:  Input -> Formula -> Intermediate calculation -> Final result
// ------------------------------------------------------------------------------------------------------
export function derivation(trace, id) {
  const seen = new Set(); const order = [];
  const visit = n => { if (seen.has(n) || !trace[n]) return; seen.add(n); (trace[n].inputs || []).forEach(visit); order.push(trace[n]); };
  visit(id);
  return order;
}

const KIND = { input: ['INPUT', 'user'], assumption: ['ASSUMPTION', 'assumed'], intermediate: ['INTERMEDIATE', 'info'], result: ['RESULT', 'pass'] };

function traceNode(n, target) {
  const [lab, cls] = KIND[n.kind] || KIND.intermediate;
  return h('div', { class: `tnode ${n.kind}${n.id === target ? ' target' : ''}` },
    h('div', { class: 'top' }, h('span', {}, n.label), h('span', { class: 'mono' }, n.value == null ? '–' : `${fmt(n.value, 5)} ${n.unit || ''}`)),
    h('div', {}, chip(lab, cls), ' ', n.source ? chip(n.source, n.source) : null, ' ', h('span', { class: 'muted mono' }, n.id)),
    n.formula ? h('div', { class: 'formula' }, n.formula) : null,
    n.substitution ? h('div', { class: 'subs' }, '= ', n.substitution) : null,
    n.note ? h('div', { class: 'help' }, n.note) : null,
    n.inputs && n.inputs.length ? h('div', { class: 'deps' }, 'depends on: ', ...n.inputs.map(d => h('a', { onclick: () => openTrace(d) }, d))) : null);
}

export function openTrace(id) {
  const res = state.ui.result;
  const drawer = document.getElementById('drawer');
  const body = document.getElementById('drawer-body');
  body.replaceChildren();
  if (!res || !res.trace || !res.trace[id]) {
    body.append(h('div', { class: 'banner' }, 'No calculation trace is available for this value.'));
  } else {
    const t = res.trace;
    const top = t[id];
    document.getElementById('drawer-title').textContent = top.label;
    body.append(h('div', { class: 'banner' }, `${top.label} = ${top.value == null ? '–' : fmt(top.value, 5)} ${top.unit || ''}`),
      h('h4', {}, 'Derivation: inputs → formulas → intermediate results → final value'),
      ...derivation(t, id).map(n => traceNode(n, id)));
  }
  drawer.classList.add('open');
}

export function openDetails(title, ...content) {
  document.getElementById('drawer-title').textContent = title;
  const body = document.getElementById('drawer-body');
  body.replaceChildren(...content);
  document.getElementById('drawer').classList.add('open');
}

export function fmtKw(w, d = 3) { return w == null ? '–' : `${fmt(w / 1000, d)} kW`; }
export function fmtW(w, d = 3) { return w == null ? '–' : `${fmt(w, d)} W`; }
