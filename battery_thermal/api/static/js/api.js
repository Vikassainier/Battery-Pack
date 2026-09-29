export class ApiError extends Error {
  constructor(status, detail) {
    super(formatDetail(detail) || `HTTP ${status}`);
    this.status = status; this.detail = detail;
  }
}

function formatDetail(d) {
  if (!d) return '';
  if (typeof d === 'string') return d;
  if (d.detail) return formatDetail(d.detail);
  if (Array.isArray(d)) return d.map(e => `${(e.loc || []).filter(x => x !== 'body').join('.')}: ${e.msg}`).join('; ');
  return JSON.stringify(d);
}

export async function api(path, { json, form, raw } = {}) {
  const opt = { method: 'GET' };
  if (json !== undefined) { opt.method = 'POST'; opt.headers = { 'Content-Type': 'application/json' }; opt.body = JSON.stringify(json); }
  if (form) { opt.method = 'POST'; opt.body = form; }
  const res = await fetch(path, opt);
  if (!res.ok) {
    let d = null; try { d = await res.json(); } catch (e) { /* not json */ }
    throw new ApiError(res.status, d);
  }
  if (raw) return res;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res;
}

export async function download(path, json, filename) {
  const res = await api(path, { json, raw: true });
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = filename; document.body.append(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
