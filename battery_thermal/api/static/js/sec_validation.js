// Built-in validation cases: hand calculation vs software, side by side. (Backend cases arrive in Phase 10.)
import { api } from './api.js';
import { h, card, chip, fmt, table, toast } from './ui.js';

export async function renderValidation(root) {
  root.append(h('h1', {}, 'Validation cases'),
    h('div', { class: 'banner' }, 'Built-in cases reproduce closed-form hand calculations with the same engine that runs your project. A case PASSES only if the software matches the hand value within its tolerance.'));
  const out = h('div', {}, h('span', { class: 'spinner' }));
  root.append(out);
  try {
    const cases = await api('/api/validation/cases');
    out.replaceChildren();
    const all = h('button', { class: 'btn primary' }, '▶ Re-run all validation cases');
    const draw = (res) => {
      out.replaceChildren(h('div', { class: 'pill-row' }, all, res ? chip(`${res.filter(c => c.passed).length}/${res.length} cases pass`, res.every(c => c.passed) ? 'pass' : 'fail') : null));
      for (const c of (res || cases)) {
        out.append(card(c.title, c.description,
          c.hand_calc ? h('pre', { class: 'mono', style: 'white-space:pre-wrap;background:var(--surface-2);padding:10px;border-radius:6px' }, c.hand_calc) : null,
          c.rows ? table(['Quantity', 'Hand calculation', 'Software', 'Δ (rel.)', 'Tolerance', ''], c.rows.map(r => [r.name, `${fmt(r.hand, 6)} ${r.unit}`, `${fmt(r.software, 6)} ${r.unit}`, r.rel_err != null ? r.rel_err.toExponential(1) : '', `${r.tol}`, r.passed ? chip('PASS', 'pass') : chip('FAIL', 'fail')])) : null,
          c.passed != null ? h('div', {}, c.passed ? chip('CASE PASSES', 'pass') : chip('CASE FAILS', 'fail')) : null));
      }
    };
    all.onclick = async () => { all.disabled = true; try { draw(await api('/api/validation/run', { json: {} })); } catch (e) { toast(e.message, 'error'); } };
    draw(null);
    all.click();
  } catch (e) { out.replaceChildren(h('div', { class: 'banner warn' }, `Validation cases not available yet (${e.message}).`)); }
}
