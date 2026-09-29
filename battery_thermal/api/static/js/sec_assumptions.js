// Assumptions & data quality: every parameter with value, unit, source class, confidence - editable.
import { state, getPath, setValue, prov } from './state.js';
import { go } from './app.js';
import { h, card, chip, fmt, table, toast } from './ui.js';

const CLS = { 'User-provided': 'user', Datasheet: 'datasheet', Calculated: 'calculated', Assumed: 'assumed' };

/** Client-side view of the register (before a run) built from the catalogue, the state and the provenance. */
function localRows() {
  const cat = state.ui.catalog || {};
  const rows = [];
  for (const [path, m] of Object.entries(cat)) {
    let v = getPath(state, path);
    if (v === undefined || v === null) { if (path.startsWith('cold_plate.') && !state.cold_plate) continue; if (path.startsWith('vehicle.') && !state.vehicle) continue; }
    const pv = prov(path);
    const cls = pv ? { user: 'User-provided', datasheet: 'Datasheet', assumed: 'Assumed', calculated: 'Calculated' }[pv.source] : ({ assumed: 'Assumed', user: 'User-provided', datasheet: 'Datasheet', calculated: 'Calculated' }[m.default_source === 'calculated' ? 'calculated' : m.default_source]);
    rows.push({ path, parameter: m.label, value: v, unit: m.unit, source_class: cls, source: pv?.note || (pv ? '' : (m.default_source === 'assumed' ? 'engineering default - not confirmed' : '')), confidence: (pv?.confidence ? pv.confidence[0].toUpperCase() + pv.confidence.slice(1) : m.confidence[0].toUpperCase() + m.confidence.slice(1)), group: m.group, note: m.rationale });
  }
  return rows;
}

export function renderAssumptions(root) {
  const res = state.ui.result && state.ui.result.assumptions ? state.ui.result : null;
  const rows = res ? res.assumptions : localRows();
  root.append(h('h1', {}, 'Assumptions & data quality'),
    h('div', { class: 'banner' }, res ? `Register from the last analysis run (${rows.length} parameters). ` : 'Register built from the current inputs (run the analysis to include calculated values). ',
      'Anything that is not user-provided or from the datasheet is an engineering assumption; edit any value here - it then becomes user-provided.'));
  const counts = {}; rows.forEach(r => { counts[r.source_class] = (counts[r.source_class] || 0) + 1; });
  root.append(h('div', { class: 'pill-row' }, ...Object.entries(counts).map(([k, n]) => h('span', {}, chip(k, CLS[k] || 'info'), ` ${n}  `)),
    h('span', {}, chip('Low confidence', 'low'), ` ${rows.filter(r => r.confidence === 'Low').length}`)));

  const groups = {}; rows.forEach(r => (groups[r.group] = groups[r.group] || []).push(r));
  for (const [g, items] of Object.entries(groups)) {
    const trs = items.map(r => {
      const editable = r.value === null || ['number', 'string'].includes(typeof r.value);
      const inp = h('input', { type: typeof r.value === 'string' ? 'text' : 'number', step: 'any', value: r.value ?? '', style: 'width:110px', disabled: !editable || r.value === 'table',
        onchange: e => {
          const raw = e.target.value; const val = typeof r.value === 'string' ? raw : (raw === '' ? null : Number(raw));
          setValue(r.path, val, 'user', 'high', 'edited in the assumptions register'); toast(`${r.parameter} updated - re-run the analysis`);
          root.replaceChildren(); renderAssumptions(root);
        } });
      return [h('div', {}, r.parameter, r.note ? h('div', { class: 'help' }, r.note) : null), inp, h('span', { class: 'mono muted' }, r.unit), chip(r.source_class, CLS[r.source_class] || 'info'), chip(r.confidence, r.confidence.toLowerCase()),
        h('span', { class: 'muted', style: 'font-size:12.5px' }, r.source)];
    });
    root.append(card(g, null, table(['Parameter', 'Value', 'Unit', 'Source class', 'Confidence', 'Source / basis'], trs)));
  }
  root.append(h('div', { class: 'pill-row' }, h('button', { class: 'btn primary', onclick: () => go('run') }, 'Run analysis →')));
}
