// Step 11 - engineering report (PDF / Excel). Implemented fully in Phase 9.
import { state, buildRequest } from './state.js';
import { download } from './api.js';
import { h, card, toast, field } from './ui.js';
import { staleBanner } from './common.js';

export function renderReport(root) {
  root.append(h('h1', {}, '11 · Engineering report'));
  const st = staleBanner(); if (st) root.append(st);
  root.append(card('Project information', 'Shown on the report cover.', h('div', { class: 'grid' },
    ...[['name', 'Project name'], ['customer', 'Customer'], ['project_no', 'Project no.'], ['engineer', 'Engineer'], ['revision', 'Revision']].map(([k, l]) =>
      h('label', { class: 'f' }, h('span', { class: 'lab' }, l), h('input', { value: state.project[k] || '', onchange: e => { state.project[k] = e.target.value; } }))),
    h('label', { class: 'f' }, h('span', { class: 'lab' }, 'Notes'), h('textarea', { rows: 3, onchange: e => { state.project.notes = e.target.value; } }, state.project.notes || '')))));
  const withSens = h('input', { type: 'checkbox', style: 'width:auto', checked: true });
  const busy = h('span');
  const gen = async (kind) => {
    busy.replaceChildren(h('span', { class: 'spinner' }), ' Generating…');
    try {
      const name = (state.project.name || 'report').replace(/\W+/g, '_');
      await download(`/api/report/${kind}`, { request: buildRequest(), include_sensitivity: withSens.checked }, `${name}_thermal_report.${kind === 'pdf' ? 'pdf' : 'xlsx'}`);
      busy.replaceChildren(); toast('Report generated');
    } catch (e) { busy.replaceChildren(h('div', { class: 'banner error' }, e.message)); }
  };
  root.append(card('Generate', 'The report recomputes the analysis from the current inputs: 17 sections including assumptions, methodology, traceability appendix and sensitivity.',
    h('label', { class: 'f', style: 'flex-direction:row;gap:8px;align-items:center;margin-bottom:10px' }, withSens, 'Include sensitivity analysis (adds a few seconds)'),
    h('div', { class: 'pill-row' }, h('button', { class: 'btn primary', onclick: () => gen('pdf') }, '⬇ PDF report'), h('button', { class: 'btn', onclick: () => gen('xlsx') }, '⬇ Excel workbook'), busy)));
}
