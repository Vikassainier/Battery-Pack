// Step 3 - Battery configuration (Phase 1): inputs + live engine-derived pack quantities + validation.
import { state, buildRequest } from './state.js';
import { api } from './api.js';
import { h, card, fieldsGrid, kv, fmt, issuesList, debounce, field } from './ui.js';
import { PACK_FIELDS, PACK_STATED, PACK_OPERATING } from './schema_ui.js';

export function renderPack(root) {
  const out = h('div');
  const probe = { mode: 'i_pack', value: 200 };

  const refresh = debounce(async () => {
    const req = buildRequest();
    const p = req.pack || {};
    const needed = ['ns', 'np', 'n_modules', 'cells_per_module'].every(k => Number.isFinite(p[k]));
    out.replaceChildren();
    if (!needed) { out.append(h('div', { class: 'banner' }, 'Enter Ns, Np, number of modules and cells per module to see the derived pack quantities.')); return; }
    const body = { cell: req.cell || {}, pack: p, require_cell_confirmation: false };
    if (Number.isFinite(probe.value)) body[probe.mode === 'i_pack' ? 'probe_pack_current_a' : 'probe_c_rate'] = probe.value;
    let r;
    try { r = await api('/api/config/validate', { json: body }); }
    catch (e) { out.append(h('div', { class: 'banner error' }, `Configuration rejected: ${e.message}`)); return; }
    const d = r.derived;
    if (d) {
      out.append(card('Derived pack quantities', 'Computed by the calculation engine from the confirmed cell data and the configuration above.',
        h('div', { class: 'row' },
          kv([['Total cells N = Ns × Np', d.n_cells],
              ['Nominal pack voltage', `${fmt(d.v_nom)} V`],
              ['Max / min pack voltage', `${d.v_max ? fmt(d.v_max) : '–'} V / ${d.v_min ? fmt(d.v_min) : '–'} V`],
              ['Pack capacity Np × C_cell', `${fmt(d.capacity_ah)} Ah`],
              ['Pack energy', `${fmt(d.energy_kwh)} kWh`]]),
          kv([['Modules (series × parallel)', `${d.modules_in_series} × ${d.modules_in_parallel}`],
              ['Per module (Ns × Np)', `${d.ns_per_module}S${d.np_per_module}P`],
              ['Cells per module', d.cells_per_module]])),
        h('h4', {}, 'Current & C-rate calculator'),
        h('div', { class: 'row' },
          h('label', { class: 'f' }, h('span', { class: 'lab' }, 'Input'),
            (() => { const s = h('select', {}, h('option', { value: 'i_pack', selected: probe.mode === 'i_pack' }, 'Pack current [A]'),
              h('option', { value: 'c', selected: probe.mode === 'c' }, 'Cell C-rate [C]'));
              s.addEventListener('change', () => { probe.mode = s.value; refresh(); }); return s; })()),
          h('label', { class: 'f' }, h('span', { class: 'lab' }, 'Value'),
            (() => { const i = h('input', { type: 'number', value: probe.value, step: 'any' });
              i.addEventListener('input', () => { probe.value = i.value === '' ? NaN : Number(i.value); refresh(); }); return i; })())),
        r.probe ? kv([['Pack current', `${fmt(r.probe.i_pack_a)} A`], ['Module current', `${fmt(r.probe.i_module_a)} A`],
                      ['Cell current', `${fmt(r.probe.i_cell_a)} A`], ['C-rate', `${fmt(r.probe.c_rate)} C`]]) : null));
    } else {
      out.append(h('div', { class: 'banner warn' }, 'Pack quantities need confirmed cell capacity and nominal voltage (step 2).'));
    }
    out.append(card('Configuration validation', null, issuesList(r.issues.filter(i => i.code.startsWith('PACK') || i.code.startsWith('SOC') ||
      i.code.startsWith('TEMP') || i.code.startsWith('TARGET') || i.code.startsWith('AMBIENT') || i.code.startsWith('CELL_CAPACITY') || i.code.startsWith('CELL_VOLTAGE_MISSING')))));
  }, 250);

  root.append(
    h('h1', {}, '3 · Battery pack configuration'),
    card('Topology', 'Series / parallel counts and module definition. The cell count must agree with the module definition.',
      fieldsGrid(PACK_FIELDS.filter(f => f.path !== 'pack.modules_in_series' || state.pack.module_arrangement === 'series_parallel'), () => {
        if (state.pack.module_arrangement === 'series_parallel') { /* re-render to show modules_in_series */ }
        refresh();
      })),
    card('Stated pack values (optional consistency check)', 'If you know the pack voltage / capacity / energy from the customer, enter them - inconsistencies are flagged.',
      fieldsGrid(PACK_STATED, refresh)),
    card('Operating window & thermal targets', null, fieldsGrid(PACK_OPERATING, refresh)),
    out);
  refresh();
}
