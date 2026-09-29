// Calculation traceability index: every key output, click to see Input → Formula → Intermediate → Result.
import { state } from './state.js';
import { h, card, chip, fmt, table } from './ui.js';
import { needResult, openTrace, derivation } from './common.js';

export function renderTraceIndex(root) {
  const res = needResult(root, 'Calculation traceability');
  if (!res) return;
  const t = res.trace;
  root.append(h('h1', {}, 'Calculation traceability'),
    h('div', { class: 'banner' }, 'Nothing here is a black box: each result records its formula, the numbers substituted, and the inputs it depends on - down to user / datasheet / assumed inputs. Click any row.'));
  const results = Object.values(t).filter(n => n.kind === 'result');
  const rows = results.map(n => [h('a', { onclick: () => openTrace(n.id), style: 'cursor:pointer;font-weight:600' }, n.label), `${n.value == null ? '–' : fmt(n.value, 5)} ${n.unit}`, h('span', { class: 'mono', style: 'font-size:12px' }, n.formula),
    `${derivation(t, n.id).length - 1} steps`]);
  root.append(card(`Key results (${results.length})`, null, table(['Result', 'Value', 'Formula', 'Depth'], rows, { rowClick: i => openTrace(results[i].id) })));
  const inputs = Object.values(t).filter(n => n.kind === 'input' || n.kind === 'assumption');
  root.append(card(`Inputs & assumptions used (${inputs.length})`, null, table(['Input', 'Value', 'Source'], inputs.map(n => [n.label, `${n.value == null ? '–' : fmt(n.value, 5)} ${n.unit}`, chip(n.source || n.kind, n.source || 'user')]))));
}
