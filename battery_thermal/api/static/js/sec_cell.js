// Steps 1-2 - Cell datasheet: ingestion (Phase 2) and the review/confirm table (Phase 1 baseline).
import { state, getPath, setPath, setValue, prov, save } from './state.js';
import { api } from './api.js';
import { go } from './app.js';
import { h, card, chip, toast, fmt, issuesList, plot, plotDiv, table } from './ui.js';
import { CELL_GROUPS, CELL_CURVES, CELL_MAPS, REQUIRED_CELL } from './schema_ui.js';

const hasResistance = () => getPath(state, 'cell.r_dc_mohm') != null || state.cell.r_vs_soc || state.cell.r_vs_temp || state.cell.r_map;

function sourceChip(path) {
  const p = prov(path);
  if (!p) return getPath(state, path) != null ? chip('user', 'user') : null;
  return chip(p.source, p.source);
}

function statusFor(spec) {
  const v = getPath(state, spec.path);
  const ex = state.ui.extraction && state.ui.extraction.fields && state.ui.extraction.fields[spec.path];
  if (v == null || v === '') {
    if (spec.required && !(spec.path === 'cell.r_dc_mohm' && hasResistance())) return chip('MISSING - required', 'missing');
    return chip('not available', 'na');
  }
  const p = prov(spec.path);
  if (p && p.source === 'datasheet') return chip(ex ? 'extracted - please verify' : 'datasheet', 'info');
  if (p && p.source === 'assumed') return chip('assumed', 'assumed');
  return chip('user-entered', 'user');
}

function paramRow(spec, rerender) {
  const v = getPath(state, spec.path);
  const ex = state.ui.extraction && state.ui.extraction.fields && state.ui.extraction.fields[spec.path];
  let input;
  if (spec.type === 'select') {
    input = h('select', { onchange: e => { setValue(spec.path, e.target.value || null, 'user'); rerender(); } },
      spec.options.map(o => h('option', { value: o, selected: o === (v || '') }, o || '—')));
  } else {
    input = h('input', { type: spec.type === 'text' ? 'text' : 'number', step: 'any', value: v ?? '',
      class: (spec.required && v == null && !(spec.path === 'cell.r_dc_mohm' && hasResistance())) ? 'missing' : (prov(spec.path)?.source === 'datasheet' ? 'datasheet' : (prov(spec.path)?.source === 'assumed' ? 'assumed' : '')),
      onchange: e => {
        let val = e.target.value;
        if (spec.type !== 'text') val = val === '' ? null : Number(val);
        const old = prov(spec.path);
        setValue(spec.path, val, 'user', 'high', old && old.source === 'datasheet' ? `edited by user (datasheet value: ${ex ? ex.value : old.note || ''})` : 'entered by user');
        rerender();
      } });
  }
  const conf = prov(spec.path)?.confidence || (ex && ex.confidence) || null;
  return [
    h('div', {}, spec.label, spec.required ? ' *' : '', spec.help ? h('div', { class: 'help' }, spec.help) : null),
    input, h('span', { class: 'mono muted' }, spec.unit || ''),
    sourceChip(spec.path), conf ? chip(conf, conf) : null,
    h('span', { class: 'muted', style: 'font-size:12px' }, ex ? ex.snippet || '' : (prov(spec.path)?.note || '')),
    statusFor(spec),
  ];
}

function curveEditor(def, rerender) {
  const cur = state.cell[def.key];
  const ta = h('textarea', { placeholder: `${def.xl}, ${def.yl}\n0, 0.5\n50, 0.4\n100, 0.45`, rows: 6 },
    cur ? cur.x.map((x, i) => `${x}, ${cur.y[i]}`).join('\n') : '');
  const pv = h('div', { class: 'plot', style: 'height:180px' });
  const draw = () => { const c = state.cell[def.key]; if (c && window.Plotly) plot(pv, [{ x: c.x, y: c.y, mode: 'lines+markers', name: def.label }], { margin: { l: 46, r: 8, t: 8, b: 34 }, xaxis: { title: def.xl }, yaxis: { title: def.yl }, showlegend: false }); else pv.replaceChildren(); };
  ta.addEventListener('change', () => {
    const pts = ta.value.split(/\n/).map(l => l.trim()).filter(Boolean).map(l => l.split(/[,;\t ]+/).map(Number)).filter(a => a.length >= 2 && a.every(Number.isFinite));
    if (!pts.length) { setValue(`cell.${def.key}`, null, 'user'); rerender(); return; }
    if (pts.length < 2) { toast('A curve needs at least 2 points', 'error'); return; }
    setValue(`cell.${def.key}`, { x: pts.map(p => p[0]), y: pts.map(p => p[1]), x_label: def.xl, y_label: def.yl }, 'user', 'high', 'entered by user');
    rerender();
  });
  setTimeout(draw, 0);
  const p = prov(`cell.${def.key}`);
  return h('div', {}, h('h3', {}, def.label, ' ', cur ? chip(p ? p.source : 'user', p ? p.source : 'user') : chip('not available', 'na'),
    cur ? h('span', { class: 'muted' }, `  ${cur.x.length} points, ${def.xl} ${fmt(cur.x[0])}…${fmt(cur.x[cur.x.length - 1])}`) : null),
    ta, pv);
}

function mapView(def) {
  const g = state.cell[def.key];
  const p = prov(`cell.${def.key}`);
  return h('div', {}, h('h3', {}, def.label, ' ', g ? chip(p ? p.source : 'datasheet', p ? p.source : 'datasheet') : chip('not available', 'na')),
    g ? table(['SOC [%] \\ T [°C]', ...g.y.map(y => fmt(y))], g.x.map((x, i) => [fmt(x), ...g.z[i].map(z => fmt(z, 4))]), { scroll: true }) : h('div', { class: 'help' }, 'Provide via an Excel/CSV datasheet section (see template).'));
}

export function renderCellConfirm(root) {
  const rerender = () => { root.replaceChildren(); renderCellConfirm(root); };
  const missing = REQUIRED_CELL.filter(p => getPath(state, p) == null);
  if (!hasResistance()) missing.push('cell.r_dc_mohm');

  root.append(h('h1', {}, '2 · Confirm cell parameters'));
  root.append(h('div', { class: 'banner' },
    'Extracted or entered values are never trusted blindly. Review each value against the source datasheet, edit where needed, then confirm. ',
    'Blue = extracted from a datasheet, amber = engineering assumption, red = required and missing.'));
  if (state.ui.extraction?.issues?.length) root.append(card('Extraction notes', null, issuesList(state.ui.extraction.issues)));

  for (const [group, specs] of CELL_GROUPS) {
    const rows = specs.map(s => paramRow(s, rerender));
    root.append(card(group, null, table(['Parameter', 'Value', 'Unit', 'Source', 'Confidence', 'Datasheet excerpt / note', 'Status'], rows)));
  }

  root.append(card('Curves & maps (optional but recommended)',
    'Resistance vs SOC / temperature, OCV vs SOC, entropic coefficient and capacity vs temperature. One "x, y" pair per line. Curves are interpolated; extrapolation is blocked unless explicitly enabled.',
    h('div', { class: 'row' }, CELL_CURVES.map(c => curveEditor(c, rerender))),
    h('div', { class: 'row', style: 'margin-top:12px' }, CELL_MAPS.map(mapView))));

  const ok = missing.length === 0;
  const box = h('div', { class: 'card' },
    h('h2', {}, 'Confirmation'),
    ok ? h('div', { class: 'banner' }, 'All required parameters are present.') :
      h('div', { class: 'banner error' }, 'Required parameters missing: ', missing.map(m => m.replace('cell.', '')).join(', '), '. The analysis will refuse to run until they are provided.'),
    h('label', { class: 'f', style: 'flex-direction:row;align-items:center;gap:8px' },
      h('input', { type: 'checkbox', style: 'width:auto', checked: !!state.cell.confirmed, onchange: e => { state.cell.confirmed = e.target.checked; save(); rerender(); } }),
      'I have reviewed these cell parameters against the source datasheet and confirm them for calculation.'),
    state.cell.confirmed ? h('div', { style: 'margin-top:8px' }, chip('CONFIRMED', 'pass'), ' Any later edit resets the confirmation.') : null);
  root.append(box);
}

/** Replace the cell with the parser's *proposal*. Values stay unconfirmed until the user reviews them (step 2). */
function applyExtraction(res) {
  state.ui.extraction = res;
  for (const k of Object.keys(state.provenance)) if (k.startsWith('cell.')) delete state.provenance[k];
  state.cell = { confirmed: false };
  for (const [path, f] of Object.entries(res.fields)) {
    setPath(state, path, f.value);
    state.provenance[path] = { source: 'datasheet', confidence: f.confidence, note: [f.note, f.location].filter(Boolean).join(' · ') };
  }
  for (const [key, c] of Object.entries(res.curves)) {
    state.cell[key] = { x: c.x, y: c.y, x_label: c.x_label, y_label: c.y_label };
    state.provenance[`cell.${key}`] = { source: 'datasheet', confidence: c.confidence, note: [c.note, c.location].filter(Boolean).join(' · ') };
  }
  for (const [key, m] of Object.entries(res.maps)) {
    state.cell[key] = { x: m.x, y: m.y, z: m.z, x_label: 'SOC [%]', y_label: 'T [°C]', z_label: m.z_label };
    state.provenance[`cell.${key}`] = { source: 'datasheet', confidence: m.confidence, note: [m.note, m.location].filter(Boolean).join(' · ') };
  }
  save();
}

function missingPanel(res, rerender) {
  if (!res.missing.length) return null;
  const rows = res.missing.map(m => {
    const s = m.suggestion;
    const scalar = m.path.split('.').length === 2 && !['cell.ocv_vs_soc', 'cell.dudt_vs_soc'].includes(m.path);
    return [
      h('div', {}, m.label, m.note ? h('div', { class: 'help' }, m.note) : null),
      m.required ? chip('required', 'missing') : chip('recommended', 'info'),
      s ? h('div', {}, h('b', {}, `${fmt(s.value)} ${s.unit}`), ' ', chip(s.confidence + ' confidence', s.confidence), h('div', { class: 'help' }, s.rationale))
        : h('span', { class: 'muted' }, m.path === 'cell.r_dc_mohm' ? 'No default offered - the tool never invents a resistance.' : 'Ask the customer / enter manually'),
      s && scalar ? h('button', { class: 'btn small', onclick: () => {
        setValue(m.path, s.value, 'assumed', s.confidence, `engineering assumption: ${s.rationale}`); toast('Assumption recorded (flagged in the assumptions register)'); rerender();
      } }, 'Accept as assumption') : null,
    ];
  });
  return card('Parameters not found in the datasheet',
    'Missing values are never filled in silently. Provide them on step 2, or explicitly accept an engineering assumption (it will be flagged as Assumed / low confidence).',
    table(['Parameter', 'Need', 'Available suggestion', ''], rows));
}

export function renderCellUpload(root) {
  const out = h('div');
  const rerender = () => { root.replaceChildren(); renderCellUpload(root); };

  const handle = async (promise, label) => {
    out.replaceChildren(h('span', { class: 'spinner' }), ` Parsing ${label}…`);
    try {
      const res = await promise;
      applyExtraction(res);
      toast(`Extracted ${Object.keys(res.fields).length} values, ${Object.keys(res.curves).length} curves, ${Object.keys(res.maps).length} maps - please review`);
      rerender();
    } catch (e) { out.replaceChildren(h('div', { class: 'banner error' }, `Could not read the file: ${e.message}`)); }
  };
  const upload = file => { const fd = new FormData(); fd.append('file', file); handle(api('/api/datasheet/parse', { form: fd }), file.name); };

  const fileInput = h('input', { type: 'file', accept: '.pdf,.xlsx,.csv,.txt', style: 'display:none', onchange: e => e.target.files[0] && upload(e.target.files[0]) });
  const drop = h('div', { class: 'drop', onclick: () => fileInput.click() },
    h('div', { style: 'font-size:16px;font-weight:600' }, 'Drop the cell datasheet here, or click to browse'),
    h('div', { class: 'muted' }, 'PDF (text-based), Excel (.xlsx) or CSV · values and curves are extracted as a proposal for you to review'));
  drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });

  root.append(h('h1', {}, '1 · Cell datasheet'),
    card('Project', null, h('div', { class: 'grid' },
      ...[['name', 'Project name'], ['customer', 'Customer'], ['project_no', 'Project no.'], ['engineer', 'Engineer'], ['revision', 'Revision']].map(([k, l]) =>
        h('label', { class: 'f' }, h('span', { class: 'lab' }, l),
          h('input', { value: state.project[k] || '', onchange: e => { state.project[k] = e.target.value; save(); } }))))),
    card('Upload datasheet', 'Nothing extracted is trusted blindly - every value is shown for review, editing and confirmation before any calculation.',
      drop, fileInput,
      h('div', { class: 'row', style: 'margin-top:10px;align-items:center' },
        h('div', {}, h('a', { href: '/api/templates/datasheet.csv' }, 'CSV template'), ' · ', h('a', { href: '/api/templates/datasheet.xlsx' }, 'Excel template'),
          ' · sample (synthetic test data): ',
          h('a', { href: '#', onclick: e => { e.preventDefault(); handle(api('/api/samples/cell_datasheet_LFP100Ah.csv/parse-datasheet', { json: {} }), 'sample CSV'); } }, 'CSV'), ' / ',
          h('a', { href: '#', onclick: e => { e.preventDefault(); handle(api('/api/samples/cell_datasheet_LFP100Ah.xlsx/parse-datasheet', { json: {} }), 'sample Excel'); } }, 'Excel'), ' / ',
          h('a', { href: '#', onclick: e => { e.preventDefault(); handle(api('/api/samples/cell_datasheet_LFP100Ah.pdf/parse-datasheet', { json: {} }), 'sample PDF'); } }, 'PDF')),
        h('div', { style: 'text-align:right' }, h('button', { class: 'btn', onclick: () => go('confirm') }, 'Skip: enter values manually →')))),
    out);

  const ex = state.ui.extraction;
  if (ex) {
    out.append(card(`Extraction result - ${ex.source_name}`, `${ex.source_type.toUpperCase()} · ${Object.keys(ex.fields).length} values, ${Object.keys(ex.curves).length} curves, ${Object.keys(ex.maps).length} maps`,
      h('div', { class: 'row' }, h('div', {}, h('h4', {}, 'Extracted'), table(['Parameter', 'Value', 'Confidence'],
        Object.entries(ex.fields).map(([p, f]) => [p.replace('cell.', ''), `${fmt(f.value, 5)} ${f.unit}`, chip(f.confidence, f.confidence)]), { scroll: true })),
        h('div', {}, h('h4', {}, 'Notes from the parser'), issuesList(ex.issues))),
      h('div', { style: 'margin-top:12px' }, h('button', { class: 'btn primary', onclick: () => go('confirm') }, 'Review & confirm the values →'))));
    const mp = missingPanel(ex, rerender);
    if (mp) out.append(mp);
  }
}
