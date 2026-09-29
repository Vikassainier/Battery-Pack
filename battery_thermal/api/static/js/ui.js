import { state, getPath, setValue, prov } from './state.js';

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export const fmt = (v, d = 3) => {
  if (v == null || Number.isNaN(v)) return '–';
  if (typeof v === 'string') return v;
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e6 || a < 1e-3)) return v.toExponential(d - 1);
  return Number(v.toPrecision(d)).toLocaleString(undefined, { maximumFractionDigits: 6 });
};

export function chip(text, cls = '') { return h('span', { class: `chip ${String(cls || text).toLowerCase()}` }, text); }
export function statusChip(s) { return chip(s, String(s).toLowerCase().replace('n/a', 'na')); }

export function toast(msg, kind = '') {
  const t = h('div', { class: `toast ${kind}` }, msg);
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), kind === 'error' ? 7000 : 3200);
}

export function card(title, sub, ...body) {
  return h('div', { class: 'card' }, title ? h('h2', {}, title) : null, sub ? h('div', { class: 'sub' }, sub) : null, ...body);
}

export function table(headers, rows, opts = {}) {
  const num = opts.num || [];
  const t = h('table', { class: 't' },
    h('thead', {}, h('tr', {}, headers.map((c, i) => h('th', { class: num.includes(i) ? 'num' : '' }, c)))),
    h('tbody', {}, rows.map(r => h('tr', {}, r.map((c, i) => h('td', { class: num.includes(i) ? 'num' : '' }, c))))));
  return h('div', { class: opts.scroll ? 'scroll' : '' }, t);
}

export function kv(pairs) {
  return h('dl', { class: 'kv' }, pairs.map(([k, v]) => [h('dt', {}, k), h('dd', {}, v)]));
}

export function issuesList(issues) {
  if (!issues || !issues.length) return h('div', { class: 'banner' }, 'No validation issues.');
  const order = { error: 0, warning: 1, info: 2 };
  return h('div', { class: 'issues' }, [...issues].sort((a, b) => order[a.severity] - order[b.severity]).map(i =>
    h('div', { class: `issue ${i.severity}` }, chip(i.severity.toUpperCase(), i.severity),
      h('div', { class: 'msg' }, i.message, i.hint ? h('div', { class: 'hint' }, i.hint) : null,
        h('code', {}, i.code + (i.field ? ` · ${i.field}` : ''))))));
}

/** Input bound to a state path. Editing marks the value as user-provided. */
export function field(spec, onChange) {
  const { path, label, unit, type = 'number', step = 'any', options, help, min, max, placeholder, required } = spec;
  const val = getPath(state, path);
  let input;
  const cls = () => {
    const pv = prov(path); const v = getPath(state, path);
    if (required && (v == null || v === '')) return 'missing';
    return pv && (pv.source === 'assumed' || pv.source === 'datasheet') ? pv.source : '';
  };
  if (type === 'select') {
    input = h('select', {}, options.map(o => {
      const v = typeof o === 'object' ? o.v : o; const l = typeof o === 'object' ? o.l : o;
      return h('option', { value: String(v), selected: String(v) === String(val) }, l);
    }));
    input.addEventListener('change', () => {
      const raw = input.value;
      const opt = options.find(o => String(typeof o === 'object' ? o.v : o) === raw);
      setValue(path, typeof opt === 'object' ? opt.v : raw, 'user'); onChange && onChange();
    });
  } else if (type === 'checkbox') {
    input = h('input', { type: 'checkbox', checked: !!val, style: 'width:auto' });
    input.addEventListener('change', () => { setValue(path, input.checked, 'user'); onChange && onChange(); });
  } else {
    input = h('input', { type: type === 'number' ? 'number' : 'text', step: type === 'number' ? step : null, min, max,
      value: val ?? '', placeholder: placeholder || '' });
    input.className = cls();
    input.addEventListener('input', () => {
      let v = input.value;
      if (type === 'number') v = v === '' ? null : Number(v);
      setValue(path, v, 'user'); input.className = cls(); onChange && onChange();
    });
  }
  return h('label', { class: 'f' },
    h('span', { class: 'lab' }, h('span', {}, label, required ? ' *' : ''), unit ? h('span', { class: 'unit' }, unit) : null),
    input, help ? h('span', { class: 'help' }, help) : null);
}

export function fieldsGrid(specs, onChange, cls = 'grid') {
  return h('div', { class: cls }, specs.map(s => field(s, onChange)));
}

export function tabs(items, active, onSelect) {
  const bar = h('div', { class: 'tabs' });
  items.forEach(([id, label]) => {
    bar.append(h('button', { class: id === active ? 'active' : '', onclick: () => onSelect(id) }, label));
  });
  return bar;
}

export function debounce(fn, ms = 300) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function plot(el, traces, layout = {}, config = {}) {
  const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const fg = dark ? '#aab6c6' : '#4a5a6e', grid = dark ? '#263243' : '#e4e9ef';
  const base = {
    margin: { l: 58, r: 18, t: 34, b: 44 }, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    font: { family: 'system-ui, sans-serif', size: 12, color: fg }, legend: { orientation: 'h', y: -0.22 },
    colorway: ['#0b6fb8', '#d9822b', '#2a9d6f', '#a0459b', '#c0392b', '#6b7a8f'],
  };
  const merged = { ...base, ...layout,
    xaxis: { gridcolor: grid, zerolinecolor: grid, ...(layout.xaxis || {}) },
    yaxis: { gridcolor: grid, zerolinecolor: grid, ...(layout.yaxis || {}) } };
  if (layout.yaxis2) merged.yaxis2 = { gridcolor: grid, ...layout.yaxis2 };
  Plotly.react(el, traces, merged, { displaylogo: false, responsive: true, ...config });
}

export function plotDiv(cls = '') { return h('div', { class: `plot ${cls}` }); }
