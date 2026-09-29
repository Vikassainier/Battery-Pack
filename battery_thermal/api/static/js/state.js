// Central project state. The browser owns the state; every analysis call posts it to the stateless API.
const KEY = 'battery_thermal_project_v1';

export const state = {
  project: { name: 'New project', customer: '', project_no: '', engineer: '', revision: 'A', notes: '' },
  cell: { confirmed: false },
  pack: { module_arrangement: 'series', soc_initial_pct: 90, soc_min_pct: 10, soc_max_pct: 100,
          t_initial_c: 25, t_target_max_c: 40, target_delta_t_k: 5, t_ambient_c: 25 },
  cycle: null,                 // parsed drive cycle arrays
  cycle_options: {},
  vehicle: null,
  crate_limits: {},
  crate_profile: null,
  resistance: {}, entropic: {}, thermal: {}, coolant: {}, cold_plate: null, pump: {}, radiator: {}, limits: {},
  installed_cooling_capacity_kw: null,
  provenance: {},              // path -> {source, confidence, note}
  require_cell_confirmation: true,
  // non-request UI state
  ui: { step: 'cell', defaultsLoaded: false, extraction: null, cycleMeta: null, result: null, sens: null, opt: null },
};

export function getPath(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
export function setPath(obj, path, value) {
  const ks = path.split('.');
  let o = obj;
  for (const k of ks.slice(0, -1)) { if (o[k] == null || typeof o[k] !== 'object') o[k] = {}; o = o[k]; }
  o[ks[ks.length - 1]] = value;
}

export function setValue(path, value, source = 'user', confidence = null, note = '') {
  setPath(state, path, value);
  state.provenance[path] = { source, confidence, note };
  if (path.startsWith('cell.') && path !== 'cell.confirmed') state.cell.confirmed = false; // any edit requires re-confirmation
  save();
}
export function prov(path) { return state.provenance[path] || null; }

export function mergeDefaults(defs) {
  // Server-side defaults for generic engineering settings; they are registered as *assumed* until edited.
  for (const [grp, vals] of Object.entries(defs)) {
    if (vals == null) continue;
    if (state[grp] == null || typeof state[grp] !== 'object') state[grp] = {};
    for (const [k, v] of Object.entries(vals)) if (state[grp][k] === undefined) state[grp][k] = v;
  }
}

function prune(o) {
  if (Array.isArray(o)) return o.map(prune);
  if (o && typeof o === 'object') {
    const r = {};
    for (const [k, v] of Object.entries(o)) {
      if (v === null || v === undefined || (typeof v === 'number' && !Number.isFinite(v)) || v === '') continue;
      r[k] = prune(v);
    }
    return r;
  }
  return o;
}

export function buildRequest() {
  const { ui, ...rest } = state;
  const req = prune(JSON.parse(JSON.stringify(rest)));
  if (!state.cold_plate) delete req.cold_plate;
  return req;
}

export function save() {
  try {
    const { ui, ...rest } = state;
    localStorage.setItem(KEY, JSON.stringify(rest));
  } catch (e) { /* storage unavailable or quota exceeded - state still lives in memory */ }
}
export function restore() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return false;
    const s = JSON.parse(raw);
    for (const [k, v] of Object.entries(s)) if (k !== 'ui') state[k] = v;
    return true;
  } catch (e) { return false; }
}
export function loadProject(obj) {
  for (const [k, v] of Object.entries(obj)) if (k !== 'ui') state[k] = v;
  state.ui.result = null; state.ui.sens = null; state.ui.opt = null;
  save();
}
export function exportProject() {
  const { ui, ...rest } = state;
  return JSON.stringify(rest, null, 1);
}
