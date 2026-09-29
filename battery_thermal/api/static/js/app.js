// Application shell: workflow navigation, top bar, project save/load. Sections are lazy-loaded modules.
import { state, restore, save, mergeDefaults, loadProject, exportProject } from './state.js';
import { api } from './api.js';
import { h, toast } from './ui.js';

const STEPS = [
  { g: 'Inputs' },
  { id: 'cell', n: 1, t: 'Cell datasheet' },
  { id: 'confirm', n: 2, t: 'Confirm cell parameters' },
  { id: 'pack', n: 3, t: 'Battery configuration' },
  { id: 'cycle', n: 4, t: 'Driving cycle' },
  { id: 'crate', n: 5, t: 'Charge / discharge C-rate' },
  { id: 'cooling', n: 6, t: 'Thermal & cooling parameters' },
  { id: 'assump', n: '•', t: 'Assumptions & data quality' },
  { g: 'Analysis' },
  { id: 'run', n: 7, t: 'Run analysis' },
  { id: 'heat', n: 8, t: 'Review heat load' },
  { id: 'coolreq', n: 9, t: 'Review cooling requirement' },
  { id: 'optimize', n: 10, t: 'Optimise cooling design' },
  { id: 'sens', n: '•', t: 'Sensitivity analysis' },
  { id: 'trace', n: '•', t: 'Calculation traceability' },
  { g: 'Deliverables' },
  { id: 'report', n: 11, t: 'Engineering report' },
  { id: 'validation', n: '•', t: 'Validation cases' },
];

const SECTIONS = {
  cell: ['./sec_cell.js', 'renderCellUpload'], confirm: ['./sec_cell.js', 'renderCellConfirm'],
  pack: ['./sec_pack.js', 'renderPack'], cycle: ['./sec_cycle.js', 'renderCycle'], crate: ['./sec_cycle.js', 'renderCRate'],
  cooling: ['./sec_cooling.js', 'renderCooling'], assump: ['./sec_assumptions.js', 'renderAssumptions'],
  run: ['./sec_results.js', 'renderRun'], heat: ['./sec_results.js', 'renderHeat'], coolreq: ['./sec_results.js', 'renderCoolingReq'],
  optimize: ['./sec_optimize.js', 'renderOptimize'], sens: ['./sec_sens.js', 'renderSensitivity'],
  trace: ['./sec_trace.js', 'renderTraceIndex'], report: ['./sec_report.js', 'renderReport'],
  validation: ['./sec_validation.js', 'renderValidation'],
};

const done = {
  cell: () => !!state.ui.extraction || state.cell.capacity_ah != null, confirm: () => !!state.cell.confirmed,
  pack: () => Number.isFinite(state.pack.ns) && Number.isFinite(state.pack.np), cycle: () => !!state.cycle || !!state.crate_profile,
  run: () => !!state.ui.result,
};

function renderNav() {
  const nav = document.getElementById('nav');
  nav.replaceChildren(h('div', { class: 'brand' }, h('b', {}, 'EV Battery Thermal Studio'), h('span', {}, 'Pack thermal analysis & cooling sizing')));
  for (const s of STEPS) {
    if (s.g) { nav.append(h('div', { class: 'grp' }, s.g)); continue; }
    nav.append(h('button', { class: `step ${state.ui.step === s.id ? 'active' : ''} ${done[s.id] && done[s.id]() ? 'done' : ''}`, 'data-step': s.id, onclick: () => go(s.id) },
      h('span', { class: 'n' }, done[s.id] && done[s.id]() ? '✓' : s.n), s.t));
  }
  nav.append(h('div', { class: 'foot' }, 'Screening-level engineering tool. Results must be verified by test / detailed CFD before release.'));
}

function renderTopbar() {
  const bar = document.getElementById('topbar');
  const name = h('input', { value: state.project.name, style: 'max-width:280px;font-weight:600', onchange: e => { state.project.name = e.target.value; save(); } });
  const file = h('input', { type: 'file', accept: '.json', style: 'display:none', onchange: async e => {
    const f = e.target.files[0]; if (!f) return;
    try { loadProject(JSON.parse(await f.text())); toast('Project loaded'); go(state.ui.step); } catch (err) { toast('Invalid project file', 'error'); }
  } });
  bar.replaceChildren(h('span', { class: 'title' }, 'Project'), name, h('span', { class: 'spacer' }),
    h('button', { class: 'btn small', onclick: () => file.click() }, 'Open project…'), file,
    h('button', { class: 'btn small', onclick: () => {
      const a = h('a', { href: URL.createObjectURL(new Blob([exportProject()], { type: 'application/json' })), download: `${state.project.name.replace(/\W+/g, '_')}.json` });
      document.body.append(a); a.click(); a.remove();
    } }, 'Save project'),
    h('button', { class: 'btn primary', onclick: () => go('run') }, 'Run analysis ▸'));
}

export async function go(id) {
  state.ui.step = id;
  renderNav();
  const view = document.getElementById('view');
  view.replaceChildren(h('span', { class: 'spinner' }));
  const [mod, fn] = SECTIONS[id] || [];
  try {
    const m = await import(mod);
    view.replaceChildren();
    await m[fn](view);
  } catch (e) {
    console.error(e);
    view.replaceChildren(h('div', { class: 'banner warn' }, `This section is not available yet (${e.message}).`));
  }
  window.scrollTo(0, 0);
}
window.go = go;

async function boot() {
  restore();
  try {
    const d = await api('/api/defaults');
    mergeDefaults(d.groups);
    state.ui.assumedPaths = d.assumed_paths; state.ui.catalog = d.catalog; state.ui.coldPlateDefaults = d.cold_plate;
    for (const p of d.assumed_paths) if (!state.provenance[p] && p.split('.').reduce((o, k) => (o == null ? undefined : o[k]), state) != null) state.provenance[p] = { source: 'assumed' };
    state.ui.defaultsLoaded = true;
  } catch (e) { toast(`Could not load defaults: ${e.message}`, 'error'); }
  save();
  renderTopbar();
  document.getElementById('drawer-close').onclick = () => document.getElementById('drawer').classList.remove('open');
  go(state.ui.step || 'cell');
}
boot();
