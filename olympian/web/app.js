'use strict';
/* Olympian NMS Save Editor – interfaz (vanilla JS, sin dependencias) */
const TOKEN = new URLSearchParams(location.hash.slice(1)).get('t') || '';
const S = {
  state: null, dict: {}, terms: {}, galaxies: [], glyphNames: [],
  slots: [], points: [],
  sess: null,            // sesión de edición abierta: {slot, files, dirty, changes}
  view: 'summary',
  travel: { galaxy: null, mode: 'current', glyphs: '', galactic: '', x: 0, y: 0, z: 0, system: 1, planet: 0, spawn: 'ship', q: '' },
  order: null,           // orden de naves en edición (arrastrar)
};

const $ = (sel, el = document) => el.querySelector(sel);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fill = (tpl, p = {}) => tpl.replace(/\{(\w+)\}/g, (_, k) => esc(Array.isArray(p[k]) ? p[k].join(', ') : p[k]));
const t = (key, p) => fill(S.dict[key] ?? key, p);
const tt = (key, p) => fill(S.terms[key] ?? S.dict[key] ?? key, p);   // términos del juego (idioma del contenido)
const baseId = id => String(id || '').replace(/^\^/, '').split('#')[0];
const isBinaryId = id => /[^\x20-\x7e]/.test(baseId(id));
/* rid = ID real que manda el servidor (los módulos guardados tienen ID binario); null = no se pudo descifrar */
const realId = (id, rid) => rid !== undefined ? rid : (isBinaryId(id) ? null : baseId(id));
const itemName = (id, rid) => { const r = realId(id, rid); return r ? ((S.names && S.names[r]) || r) : t('item.procedural'); };
const catText = (grp, kind) => grp ? `${rawT('inv.category')}: ${rawT('inv.grp.' + grp)}`
  : (kind === 'Technology' || kind === true || kind === false) ? `${rawT('inv.category')}: ${rawT('inv.grp.installed')}` : '';
const itemIcon = (id, rid, extra = '') => { const r = realId(id, rid);
  return r ? `<img src="/icons/${encodeURIComponent(r)}" alt="" draggable="false" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'noico',textContent:'${esc(r).slice(0, 3)}'}))">${extra}`
    : `<span class="noico">✦</span>${extra}`; };
const LANG_NAMES = { es: 'Español (Latinoamérica)', 'es-ES': 'Español (España)', en: 'English' };

let busyN = 0, busyT = null;
function busy(on) {
  busyN += on ? 1 : -1;
  clearTimeout(busyT);
  if (busyN > 0) busyT = setTimeout(() => { $('#busy').hidden = false; }, 150);
  else $('#busy').hidden = true;
}
async function api(path, body, opts = {}) {
  const quiet = opts.quiet || path === '/api/ping' || path === '/api/state' || path === '/api/bye';
  if (!quiet) busy(true);
  try { return await apiRaw(path, body); } finally { if (!quiet) busy(false); }
}
async function apiRaw(path, body) {
  const r = await fetch(path, { method: body ? 'POST' : 'GET', headers: { 'X-Olympian-Token': TOKEN, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({ error: 'generic' }));
  if (!r.ok) throw new Error(j.error || 'generic');
  return j;
}
function errText(e) { const k = 'err.' + String(e.message).split(':')[0]; return S.dict[k] ? t(k) : `${t('err.generic')}: ${esc(e.message)}`; }
function toast(text, err = false) {
  const d = document.createElement('div'); d.className = 'toast' + (err ? ' err' : ''); d.innerHTML = text;
  document.body.appendChild(d); setTimeout(() => d.remove(), err ? 7000 : 4500);
}
async function loadLang(ui, term) {
  S.dict = await fetch(`i18n/${ui}.json`).then(r => r.json());
  const tf = String(term).startsWith('es') ? 'es' : 'en';
  S.terms = tf === ui ? S.dict : await fetch(`i18n/${tf}.json`).then(r => r.json());
  try { const g = await api('/api/gamedata'); S.names = g.names || {}; S.namesAlt = g.names_alt || {}; S.tips = g.tips || {}; S.gd = g.status; } catch (_) { S.names = {}; S.namesAlt = {}; S.tips = {}; S.gd = null; }
  document.documentElement.lang = ui;
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
}
const fmtDate = ts => ts ? new Date(ts * 1000).toLocaleString(S.state?.settings?.ui_lang || 'es', { dateStyle: 'short', timeStyle: 'short' }) : t('common.none');
const fmtHours = s => s == null ? t('common.none') : `${Math.floor(s / 3600)} h`;
const galLabel = ri => { const g = S.galaxies[ri]; return g ? `${g.n}. ${g.name}` : `#${ri + 1}`; };
const newest = d => d && (d.files.find(f => f.newest) || d.files[0]);
const slotName = d => { const f = newest(d); return f && f.name ? f.name : t('common.unnamed'); };

/* ------------------------------------------------------------------ modal */
function showModal(title, bodyHtml, actions, wide = false) {
  $('#modal-title').textContent = title; $('#modal-body').innerHTML = bodyHtml;
  $('.modal-card').classList.toggle('wide', wide);
  const box = $('#modal-actions'); box.innerHTML = '';
  for (const a of actions) { const b = document.createElement('button'); b.className = 'btn' + (a.primary ? ' primary' : '') + (a.danger ? ' danger' : '');
    b.textContent = a.label; b.disabled = !!a.disabled; b.onclick = a.onClick; box.appendChild(b); }
  $('#modal').hidden = false;
}
const closeModal = () => { $('#modal').hidden = true; };
function lockModal() {
  document.querySelectorAll('#modal-actions button').forEach(b => { b.disabled = true; });
  $('#modal-body').insertAdjacentHTML('beforeend', `<p class="muted">${t('common.working')}</p>`);
}
function messagesHtml(msgs) {
  const order = { block: 0, danger: 1, warn: 2, info: 3, ok: 4 };
  return [...msgs].sort((a, b) => order[a.level] - order[b.level]).map(m =>
    `<div class="msg ${m.level}"><span class="lv">${t('level.' + m.level)}</span><span>${m.code === 'revert_item' ? '↶ ' + t('msg.' + m.p.code_, shapeParams(m.p)) : t('msg.' + m.code, shapeParams(m.p))}</span></div>`).join('');
}
function shapeParams(p = {}) {
  const o = { ...p };
  for (const k of Object.keys(o)) {
    if (k.startsWith('shape_')) o[k] = t('sc.' + o[k]);
    else if (k === 'when' && typeof o[k] === 'number') o[k] = fmtDate(o[k]);
    else if ((k === 'item' || k === 'other') && typeof o[k] === 'string') o[k] = itemName(o[k]);
    else if (k === 'grid') o[k] = t('grid.' + o[k]);
    else if (k === 'charges' && Array.isArray(o[k])) o[k] = o[k].map(([id, cell, a, m]) => `${itemName(id)} (${cell}: ${a}/${m} → ${m})`).join(', ');
    else if (o[k] && typeof o[k] === 'object' && 'inv' in o[k]) o[k] = invLabel(o[k]);
    else if (k === 'level') o[k] = t('inv.level.' + o[k]);
    else if (k === 'group' && typeof o[k] === 'string') o[k] = t('inv.grp.' + o[k]);
    else if (k === 'key' && typeof o[k] === 'string') o[k] = t('inv.sort.' + o[k]);
    else if (k === 'dir' && typeof o[k] === 'string') o[k] = t('inv.dir.' + o[k]);
    else if (k === 'stat' && typeof o[k] === 'string') o[k] = t('set.stat.' + o[k]);
    else if (k === 'note' && typeof o[k] === 'string') o[k] = t('jv.n.' + o[k]);
  }
  return o;
}
function scName(sh) {
  if (!sh || sh.code === 'none') return t('sc.none');
  return sh.code === 'split' ? t('sc.split', { parts: sh.parts.join('+') }) : t('sc.' + sh.code);
}
function freighterHtml(fr) {
  if (!fr) return t('slots.no_freighter');
  return `<b>${esc(fr.name || t('common.unnamed'))}</b><br><span class="muted">${esc(tt('term.freighter.' + fr.type))} · ${t('ship.class')} ${esc(fr.cls)} · ${t('slots.freighter_cells', { n: fr.cells, t: fr.tech_cells })}${fr.galaxy != null ? ' · ' + esc(galLabel(fr.galaxy)) : ''}</span>`;
}
function backupChanges(p) {
  if (p.kind === 'point') return '';
  const ch = (p.changes || []).map(c => t('msg.' + c.code, shapeParams(c.p)));
  if (!p.changes) return `<div class="muted small">${t('start.changes_unknown')}</div>`;
  if (!ch.length) return '';
  return `<details class="bchanges"><summary>${t('start.changes_n', { n: ch.length })}</summary>
    <ul>${ch.map(x => `<li>${x}</li>`).join('')}</ul><div class="muted small">${t('start.changes_undo')}</div></details>`;
}
function statsHtml(st, compact) {
  if (!st || !Object.keys(st).length) return '';
  const order = ['damage', 'shield', 'hyperdrive', 'agile'];
  if (compact) return `<span class="stats small">${order.filter(k => k in st).map(k => `<span title="${t('stat.' + k)}">${STAT_ICON[k]} ${st[k]}</span>`).join('')}</span>`;
  return `<div class="statbox"><b>${t('stat.title')}</b>${order.filter(k => k in st).map(k => `
    <div class="stat"><span>${t('stat.' + k)}</span><span class="bar"><i style="width:${Math.min(100, st[k])}%"></i></span><b>${st[k]}</b></div>`).join('')}
    <span class="muted small">${t('stat.note')}</span></div>`;
}
const STAT_ICON = { damage: '⚔', shield: '🛡', hyperdrive: '⇢', agile: '↻' };
function historyHtml(h) {
  if (!h || !h.length) return `<p class="muted small">${t('start.no_history')}</p>`;
  return `<ul class="history">${h.map(e => `<li><span class="muted">${fmtDate(e.time)}</span> ${e.changes.map(c => t('msg.' + c.code, shapeParams(c.p))).join(' · ') || t('start.saved_generic')}</li>`).join('')}</ul>`;
}
function resultHtml(rep) {
  const ok = (rep.verified || []).length && rep.verified.every(v => v.ok);
  return `${ok ? `<div class="msg ok"><span class="lv">${t('level.ok')}</span><span>${t('result.verified')}</span></div>` : ''}
    <p class="muted">${t('result.backup')}:<br><code>${esc(rep.backup || '')}</code></p>
    <div class="msg info"><span class="lv">Steam</span><span>${t('result.cloud')}</span></div>`;
}

/* Cambios pequeños (mover en la cuadrícula): directo a pendientes, sin ventana; se escriben al Guardar */
async function quickStage(action, params) {
  try {
    const q = { quiet: true };
    const plan = await api('/api/plan', { action, params }, q);
    if (plan.blocked) {
      const m = plan.messages.find(x => x.level === 'block');
      toast(m ? t('msg.' + m.code, shapeParams(m.p)) : t('preview.blocked'), true);
    } else {
      await api('/api/apply', { id: plan.id }, q);
    }
    S.sess = await api('/api/session', null, q); S.order = null;   // only notes the change; nothing is written
    renderEditor();
  } catch (e) { toast(errText(e), true); S.sess = await api('/api/session', null, { quiet: true }); renderEditor(); }
}
/* mueve los iconos en pantalla al instante (luego el servidor lo confirma) */
function swapCells(grid, a, b) {
  const ca = grid.querySelector(`[data-cell="${a}"]`), cb = grid.querySelector(`[data-cell="${b}"]`);
  if (!ca || !cb) return;
  const [ia, ib] = [ca.innerHTML, cb.innerHTML], [ta, tb] = [ca.title, cb.title];
  ca.innerHTML = ib; cb.innerHTML = ia;
  ca.title = ta.split(' — ')[0] + (tb.includes(' — ') ? ' — ' + tb.split(' — ').slice(1).join(' — ') : '');
  cb.title = tb.split(' — ')[0] + (ta.includes(' — ') ? ' — ' + ta.split(' — ').slice(1).join(' — ') : '');
  const hasA = ca.classList.contains('has'), hasB = cb.classList.contains('has');
  ca.classList.toggle('has', hasB); cb.classList.toggle('has', hasA);
}

/* Vista previa → aplicar. Los cambios "staged" van a la copia de trabajo (se escriben al Guardar). */
async function previewAndApply(action, params, after) {
  let plan;
  try { plan = await api('/api/plan', { action, params }); } catch (e) { return toast(errText(e), true); }
  const files = !plan.staged && plan.files.length ? `<p class="muted">${t(plan.action === 'delete_slot' ? 'preview.files_deleted' : 'preview.files')}: <code>${plan.files.map(esc).join(', ')}</code></p>` : '';
  const note = plan.staged ? `<div class="msg info"><span class="lv">${t('level.info')}</span><span>${t('preview.staged_note')}</span></div>` : '';
  const blocked = plan.blocked ? `<p class="msg block"><span>${t('preview.blocked')}</span></p>` : '';
  const code = plan.needs_code && !plan.blocked ? `<label class="codeask">🔐 ${t('lock.code_again')}<input id="pv-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000"></label>` : '';
  showModal(t('preview.title'), messagesHtml(plan.messages) + note + files + blocked + code, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: plan.staged ? t('preview.add') : t('common.apply'), primary: true, disabled: plan.blocked, onClick: async () => {
      const typed = plan.needs_code ? ($('#pv-code').value || '').trim() : undefined;
      if (plan.needs_code && typed.length !== 6) return toast(t('err.lock_bad_code'), true);
      if (plan.needs_code) {
        try { const rep = await api('/api/apply', { id: plan.id, code: typed });
          closeModal();
          if (rep.staged) { toast(t('editor.staged_toast')); after && await after(); return; }
          showModal(t('result.title'), resultHtml(rep), [{ label: t('common.close'), primary: true, onClick: async () => { closeModal(); after && await after(); } }]);
        } catch (e) { toast(errText(e), true); if (String(e.message) !== 'lock_bad_code') closeModal(); else $('#pv-code').select(); }
        return;
      }
      lockModal();
      try {
        const rep = await api('/api/apply', { id: plan.id });
        if (rep.staged) { closeModal(); toast(t('editor.staged_toast')); after && await after(); return; }
        showModal(t('result.title'), resultHtml(rep), [{ label: t('common.close'), primary: true, onClick: async () => { closeModal(); after && await after(); } }]);
      } catch (e) { closeModal(); toast(errText(e), true); }
    } }]);
}

/* ------------------------------------------------------------------ idioma (centrado, dos listas) */
function langModal(first) {
  const st = S.state.settings;
  const opts = sel => `<option value="es" ${sel === 'es' ? 'selected' : ''}>Español</option><option value="en" ${sel === 'en' ? 'selected' : ''}>English</option>`;
  const topts = sel => Object.entries(LANG_NAMES).map(([k, v]) => `<option value="${k}" ${sel === k ? 'selected' : ''}>${v}</option>`).join('');
  return new Promise(resolve => {
    showModal('Idioma · Language', `<div class="stack">
      <label>${t('settings.ui_lang')} · App language<select id="l-ui">${opts(st.ui_lang || 'es')}</select></label>
      <label>${t('settings.term_lang')} · Game content language<select id="l-term">${topts(st.term_lang || 'es')}</select></label>
      <p class="muted" style="margin:0">${t('settings.term_note')}</p></div>`, [
      ...(first ? [] : [{ label: t('common.cancel'), onClick: () => { closeModal(); resolve(false); } }]),
      { label: t('common.continue'), primary: true, onClick: async () => {
        const ui = $('#l-ui').value, term = $('#l-term').value;
        await api('/api/settings', { ui_lang: ui, term_lang: term, lang_chosen: true });
        await refreshState(); await loadLang(ui, term); closeModal(); resolve(true);
      } }]);
  });
}

/* ------------------------------------------------------------------ estado */
async function refreshState() {
  S.state = await api('/api/state');
  const run = S.state.game_running;
  const pill = `<span class="pill ${run ? 'bad' : run === false ? 'ok' : 'warn'}">● ${t(run ? 'status.game_running' : run === false ? 'status.game_closed' : 'status.game_unknown')}</span>`;
  $('#status').innerHTML = `${pill}<span>${t('status.folder')}:<br><code>${esc(S.state.folder || t('status.no_folder'))}</code></span><span>v${esc(S.state.version)}</span>`;
  $('#start-status').innerHTML = pill;
}
setInterval(() => api('/api/ping').catch(() => {}), 5000);
setInterval(() => refreshState().catch(() => {}), 20000);
window.addEventListener('beforeunload', e => { if (S.sess?.dirty && !S.quitting) { e.preventDefault(); e.returnValue = ''; } });
window.addEventListener('pagehide', () => { try { navigator.sendBeacon(`/api/bye?t=${encodeURIComponent(TOKEN)}`, '{}'); } catch (_) {} });
try { new EventSource(`/api/live?t=${encodeURIComponent(TOKEN)}`); } catch (_) {}   // el servidor sabe si la ventana sigue abierta

/* ================================================================== PANTALLA DE INICIO */
async function showStart() {
  $('#editor').hidden = true; $('#start').hidden = false;
  const main = $('#start-main');
  main.innerHTML = `<p class="muted">${t('common.loading')}</p>`;
  try { S.slots = (await api('/api/slots')).slots; } catch (e) {
    main.innerHTML = `<div class="card stack" style="max-width:640px"><p>${errText(e)}</p>${folderForm()}</div>`; bindFolderForm(); return;
  }
  try { S.points = (await api('/api/points')).points; } catch (e) { S.points = []; }
  const cards = S.slots.filter(s => s.files).map(s => {
    const f = newest(s), ships = f.ships.filter(x => x.model).length;
    return `<div class="card slotcard" data-slot="${s.slot}">
      <div class="row between"><h2>${t('common.slot')} ${s.slot} · ${esc(slotName(s))}</h2></div>
      ${f.name_ok === false ? `<p class="msg warn"><span>${t('slots.name_lost', { name: esc(f.name) })}</span></p>` : ''}
      <div class="kv">
        <span>${t('slots.location')}</span><span>${esc(galLabel(f.galaxy))} · ${t('common.system')} ${f.address.system}${f.summary ? `<br><span class="muted">${esc(f.summary)}</span>` : ''}</span>
        <span>${t('slots.state')}</span><span>${esc(t('state.' + f.state))}</span>
        <span>${t('slots.ships')}</span><span>${ships}</span>
        <span>${t('slots.playtime')}</span><span>${fmtHours(f.playtime)}</span>
        <span>${t('slots.saved')}</span><span>${fmtDate(f.mtime)}</span>
        <span>${t('slots.freighter')}</span><span>${freighterHtml(f.freighter)}</span>
      </div>
      <div class="row">
        <button class="btn primary" data-act="edit">${t('start.edit')}</button>
        <button class="btn" data-act="backup">${t('start.backup')}</button>
        <button class="btn" data-act="copy">${t('slots.copy')}</button>
        <button class="btn danger" data-act="delete" ${protectedOk() ? '' : `disabled title="${esc(rawT('start.delete_needs_lock'))}"`}>${t('start.delete')}</button>
      </div></div>`;
  }).join('');
  const kind = p => p.kind === 'point' ? t('points.kind.point') : t('points.kind.auto');
  const rows = S.points.map(p => `<tr>
      <td><b>${esc(p.save_name || t('common.unnamed'))}</b>${p.label ? `<br><span class="muted">${esc(p.label)}</span>` : p.action ? `<br><span class="muted">${esc(t('action.' + p.action))}</span>` : ''}
        ${backupChanges(p)}</td>
      <td>${p.from_slot ?? p.slot ?? '—'}</td>
      <td>${esc(p.summary || '—')}</td>
      <td>${fmtHours(p.playtime)}</td>
      <td>${fmtDate(p.created)}</td>
      <td><span class="tag ${p.kind === 'point' ? 'new' : ''}">${kind(p)}</span>${p.extra ? `<br><span class="muted small">💾 ${t('points.extra')}</span>` : ''}</td>
      <td class="right"><button class="btn" data-restore="${esc(p.id)}">${t('start.restore_to')}</button></td></tr>`).join('');
  main.innerHTML = `
    ${S.gd && S.gd.up_to_date === false ? `<p class="msg warn"><span>${t('gd.old_help')}</span></p>` : ''}
    <h1>${t('start.title')}</h1><p class="help">${t('start.help')}</p>
    <div class="grid">${cards || `<p class="muted">${t('status.no_folder')}</p>`}</div>
    <h2 style="margin-top:34px">${t('start.backups')}</h2><p class="help">${t('start.backups_help')}</p>
    <div id="bk-usage"></div>
    ${rows ? `<div class="card tablewrap"><table class="table"><thead><tr>
      <th>${t('start.col_save')}</th><th>${t('common.slot')}</th><th>${t('slots.location')}</th><th>${t('slots.playtime')}</th>
      <th>${t('start.col_date')}</th><th>${t('start.col_kind')}</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`
      : `<p class="muted">${t('points.empty')}</p>`}`;
  main.querySelectorAll('[data-act]').forEach(b => b.onclick = () => startAction(+b.closest('[data-slot]').dataset.slot, b.dataset.act));
  main.querySelectorAll('[data-restore]').forEach(b => b.onclick = () => restoreTo(b.dataset.restore));
  backupUsage($('#bk-usage'), showStart);
}

/* espacio que ocupan los respaldos y purga (se conservan siempre los más recientes) */
const fmtBytes = n => n >= 1073741824 ? `${(n / 1073741824).toFixed(2)} GB` : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(0, Math.round(n / 1024))} KB`;
async function backupUsage(box, after) {
  if (!box) return;
  let d;
  try { d = await api('/api/backups', null, { quiet: true }); } catch (e) { box.innerHTML = ''; return; }
  const line = (k, u) => `<span>${t('bk.' + k)}: <b>${fmtBytes(u.backups + u.points)}</b> <span class="muted small">(${t('bk.detail', { a: u.n_backups, ab: fmtBytes(u.backups), p: u.n_points, pb: fmtBytes(u.points) })})</span></span>`;
  const rm = Object.values(d.purge).reduce((n, x) => n + x.remove.length, 0), free = Object.values(d.purge).reduce((n, x) => n + x.bytes, 0);
  box.innerHTML = `<div class="card row bkusage">${Object.entries(d.usage).map(([k, u]) => line(k, u)).join('')}
    <button class="btn small" id="bk-purge" ${rm ? '' : 'disabled'} title="${esc(rawT('bk.purge_tip', { keep: d.keep }))}">🧹 ${t('bk.purge', { n: rm, size: fmtBytes(free) })}</button></div>`;
  $('#bk-purge').onclick = () => {
    const code = d.needs_code ? `<label class="codeask">🔐 ${t('lock.code_again')}<input id="bk-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000"></label>` : '';
    showModal(t('bk.purge_title'), `<div class="stack"><p>${t('bk.purge_help', { n: rm, size: fmtBytes(free), keep: d.keep })}</p>
      <div class="msg ok"><span class="lv">${t('level.ok')}</span><span>${t('bk.purge_safe', { keep: d.keep })}</span></div>${code}</div>`, [
      { label: t('common.cancel'), onClick: closeModal },
      { label: t('bk.purge_ok'), primary: true, danger: true, onClick: async () => {
        try {
          const r = await api('/api/backups/purge', { code: d.needs_code ? $('#bk-code').value.trim() : '' });
          closeModal();
          const n = Object.values(r.done).reduce((a, x) => a + x.removed, 0), f = Object.values(r.done).reduce((a, x) => a + x.freed, 0);
          toast(t('bk.purged', { n, size: fmtBytes(f) }));
          after ? after() : backupUsage(box);
        } catch (e) { toast(errText(e), true); }
      } }]);
  };
}

function slotOptions(exclude) {
  const used = Object.fromEntries(S.slots.filter(s => s.files).map(s => [s.slot, slotName(s)]));
  return Array.from({ length: 15 }, (_, i) => i + 1).filter(n => n !== exclude)
    .map(n => `<option value="${n}">${t('common.slot')} ${n} · ${used[n] ? esc(used[n]) : t('start.empty_slot')}</option>`).join('');
}

function startAction(slot, act) {
  const s = S.slots.find(x => x.slot === slot);
  if (act === 'edit') return openEditor(slot);
  if (act === 'backup') {
    showModal(t('start.backup'), `<p>${t('start.backup_help', { slot, name: slotName(s) })}</p>
      <label>${t('slots.point_label')}<input id="f-label" maxlength="60"></label>`, [
      { label: t('common.cancel'), onClick: closeModal },
      { label: t('start.backup'), primary: true, onClick: async () => {
        try { await api('/api/points/create', { slot, label: $('#f-label').value || slotName(s) }); closeModal(); toast(t('slots.point_done')); showStart(); }
        catch (e) { toast(errText(e), true); } } }]);
  } else if (act === 'delete') {
    previewAndApply('delete_slot', { slot }, showStart);
  } else if (act === 'copy') {
    const srcs = s.files.map(f => `<option value="${f.file_index}" ${f.newest ? 'selected' : ''}>${esc(f.file)} · ${fmtDate(f.mtime)}</option>`).join('');
    showModal(t('slots.copy'), `<div class="stack">
      <label>${t('slots.copy_src')}<select id="f-src">${srcs}</select></label>
      <label>${t('slots.copy_dest')}<select id="f-dest">${slotOptions(slot)}</select></label>
      <label>${t('slots.copy_name')}<input id="f-name" maxlength="60"></label></div>`, [
      { label: t('common.cancel'), onClick: closeModal },
      { label: t('common.preview'), primary: true, onClick: () => previewAndApply('copy_slot',
        { src_file: +$('#f-src').value, dest_slot: +$('#f-dest').value, name: $('#f-name').value }, showStart) }]);
  }
}

function restoreTo(pointId) {
  const p = S.points.find(x => x.id === pointId);
  showModal(t('start.restore_to'), `<p>${t('start.restore_help', { name: p.save_name || t('common.unnamed'), date: fmtDate(p.created) })}</p>
    <label>${t('start.restore_dest')}<select id="f-dest">${slotOptions(null)}</select></label>`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('common.preview'), primary: true, onClick: () => previewAndApply('restore_to', { point: pointId, dest_slot: +$('#f-dest').value }, showStart) }]);
  const from = p.from_slot ?? p.slot; if (from) $('#f-dest').value = String(from);
}

function folderForm() {
  return `<label>${t('settings.folder')}<input id="s-folder" value="${esc(S.state.folder || (S.state.roots || [])[0] || '')}"></label>
    <div class="row end"><button class="btn" id="s-folder-save">${t('settings.folder_save')}</button></div>`;
}
function bindFolderForm(after) {
  $('#s-folder-save').onclick = async () => {
    try { await api('/api/folder', { path: $('#s-folder').value }); await refreshState(); toast(t('settings.folder_ok')); (after || showStart)(); }
    catch (e) { toast(errText(e), true); }
  };
}

/* ================================================================== EDITOR */
async function openEditor(slot) {
  try { S.sess = await api('/api/session/open', { slot }); } catch (e) { return toast(errText(e), true); }
  S.order = null; S.travel.galaxy = null; S.view = 'summary';
  $('#start').hidden = true; $('#editor').hidden = false;
  renderEditor();
}
async function reloadSession() { S.sess = await api('/api/session'); S.order = null; renderEditor(); }

function renderTopbar() {
  const s = S.sess;
  $('#editing-name').textContent = `${t('common.slot')} ${s.slot} · ${slotName(s)}`;
  $('#btn-home').title = rawT('editor.home_tip'); $('#btn-home').setAttribute('aria-label', rawT('editor.home_tip'));
  const save = $('#btn-save');
  save.textContent = s.dirty ? t('editor.save_n', { n: s.changes.length }) : t('editor.save');
  save.disabled = !s.dirty; $('#btn-discard').disabled = !s.dirty;
  const others = S.slots.filter(x => x.files && x.slot !== s.slot)
    .map(x => `<option value="${x.slot}">${t('editor.switch_to', { slot: x.slot, name: esc(slotName(x)) })}</option>`).join('');
  $('#slot-switch').innerHTML = `<option value="${s.slot}" selected>${t('common.slot')} ${s.slot} · ${esc(slotName(s))}</option>${others}
    <option value="home">${t('editor.home')}</option>`;
  document.querySelectorAll('#nav button').forEach(b => b.classList.toggle('active', b.dataset.view === S.view));
}

function renderEditor() {
  renderTopbar();
  try {
    ({ summary: viewSummary, travel: viewTravel, ships: viewShips, inventories: viewInventories, missions: viewMissions, settlements: viewSettlements, json: viewJson, settings: viewSettings })[S.view]();
  } catch (e) { console.error(e); $('#main').innerHTML = `<p class="msg block"><span>${esc(e.message)}</span></p>`; }
}
document.getElementById('nav').addEventListener('click', e => {
  const b = e.target.closest('button[data-view]'); if (!b) return;
  S.view = b.dataset.view; renderEditor();
});

function changesHtml() {
  const c = S.sess.changes;
  if (!c.length) return `<p class="muted">${t('editor.no_changes')}</p>`;
  return `<ol class="changes">${c.map(x => `<li>${x.summary ? t('msg.' + x.summary.code, shapeParams(x.summary.p)) : esc(x.action)}</li>`).join('')}</ol>`;
}

/* cambiar de partida / volver al inicio, con lista de cambios pendientes */
$('#slot-switch').onchange = e => {
  const target = e.target.value;
  e.target.value = String(S.sess.slot);
  switchTo(target);
};
$('#btn-home').onclick = () => switchTo('home');
function switchTo(target) {
  const cur = S.sess.slot;
  if (target === String(cur)) return;
  const go = async () => {
    if (target === 'home') { await api('/api/session/close', {}); S.sess = null; return showStart(); }
    return openEditor(+target);
  };
  if (!S.sess.dirty) return go();
  const dest = target === 'home' ? t('editor.home') : `${t('common.slot')} ${target}`;
  showModal(t('editor.unsaved_title'), `<p>${t('editor.unsaved_switch', { dest: esc(dest) })}</p>${changesHtml()}`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('editor.discard_go'), danger: true, onClick: async () => { await api('/api/session/discard', {}); closeModal(); go(); } },
    { label: t('editor.save_go'), primary: true, onClick: () => doSave(go) }]);
}

async function doSave(after) {
  let plan;
  try { plan = await api('/api/session/save_plan', {}); } catch (e) { return toast(errText(e), true); }
  showModal(t('editor.save_title'), `${changesHtml()}${messagesHtml(plan.messages)}
    ${plan.blocked ? `<p class="msg block"><span>${t('preview.blocked')}</span></p>` : ''}`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('editor.save_now'), primary: true, disabled: plan.blocked, onClick: async () => {
      lockModal();
      try {
        const rep = await api('/api/apply', { id: plan.id });
        S.sess = await api('/api/session'); S.order = null;
        S.slots = (await api('/api/slots')).slots;
        showModal(t('result.title'), resultHtml(rep), [{ label: t('common.close'), primary: true,
          onClick: () => { closeModal(); after ? after() : renderEditor(); } }]);
      } catch (e) { closeModal(); toast(errText(e), true); }
    } }]);
}
$('#btn-save').onclick = () => doSave();
$('#btn-discard').onclick = () => showModal(t('editor.discard'), `<p>${t('editor.discard_q')}</p>${changesHtml()}`, [
  { label: t('common.cancel'), onClick: closeModal },
  { label: t('editor.discard'), danger: true, onClick: async () => { await api('/api/session/discard', {}); closeModal(); await reloadSession(); toast(t('editor.discarded')); } }]);

/* -- Salir: pregunta si guardar o descartar -- */
function quitApp() {
  S.quitting = true;
  api('/api/bye', {}).catch(() => {});
  window.close();
  setTimeout(() => { document.body.innerHTML = `<div class="bye"><h1>Olympian NMS Save Editor</h1><p>${t('editor.closed')}</p></div>`; }, 400);
}
function exitApp() {
  if (!S.sess?.dirty) return quitApp();
  showModal(t('editor.unsaved_title'), `<p>${t('editor.exit_q')}</p>${changesHtml()}`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('editor.exit_discard'), danger: true, onClick: async () => { await api('/api/session/discard', {}); S.sess.dirty = false; closeModal(); quitApp(); } },
    { label: t('editor.exit_save'), primary: true, onClick: () => doSave(quitApp) }]);
}
$('#btn-exit').onclick = exitApp;
$('#btn-exit2').onclick = exitApp;

function changelogHtml() {
  const log = S.sess.changelog || [];
  if (!log.length) return `<p class="muted small">${t('log.empty')}</p>`;
  return `<p class="muted small">${t('log.help')}</p><ul class="changelog">${log.map(e => `<li>
    <div><span class="muted small">${fmtDate(e.time)}</span><br>${t('msg.' + e.change.code, shapeParams(e.change.p))}
      ${e.session_size > 1 ? `<br><span class="muted small">${t('log.same_session', { n: e.session_size - 1 })}</span>` : ''}</div>
    <button class="btn small" data-revert="${esc(e.point)}">${t('log.revert')}</button></li>`).join('')}</ul>`;
}

/* -- Resumen -- */
function viewSummary() {
  const f = newest(S.sess);
  const row = (img, label, value) => `<div class="ovli"><span class="ovlimg">${img}</span><span class="ovlab">${label}</span><span class="ovval">${value}</span></div>`;
  const emo = e => `<span class="ovemo">${e}</span>`;
  $('#main').innerHTML = `<h1>${t('summary.title')}</h1><p class="help">${t('summary.help')}</p>
    <div class="grid">
      <div class="card"><h2>${esc(slotName(S.sess))}</h2>
        <div class="ovlist" id="ov-list">
          ${row(emo('📍'), t('slots.location'), `${esc(galLabel(f.galaxy))} · ${t('common.system')} ${f.address.system}`)}
          ${row(emo('🧍'), t('slots.state'), esc(t('state.' + f.state)))}
          ${row(emo('🚀'), t('slots.ships'), f.ships.filter(x => x.model).length)}
          ${row(emo('⏱'), t('slots.playtime'), fmtHours(f.playtime))}
          <div id="ov-more"></div>
        </div>
        ${f.name_ok === false ? `<p class="msg warn"><span>${t('slots.name_lost', { name: esc(f.name) })}</span></p>` : ''}
        <button class="btn" id="b-rename">${t('slots.rename')}</button></div>
      <div class="card"><h2>${t('editor.pending')}</h2>${changesHtml()}
        <h2 style="margin-top:22px">${t('log.title')}</h2>${changelogHtml()}</div></div>
    <div id="ov" style="margin-top:18px"></div>
    <div id="bk-usage-sum" style="margin-top:18px"></div>`;
  overviewCards(row);
  backupUsage($('#bk-usage-sum'));
  document.querySelectorAll('[data-revert]').forEach(b => b.onclick = () => previewAndApply('revert', { point: b.dataset.revert }, reloadSession));
  $('#b-rename').onclick = () => showModal(t('slots.rename'), `<label>${t('common.name')}<input id="f-name" maxlength="60" value="${esc(f.name || '')}"></label>`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('common.preview'), primary: true, onClick: () => previewAndApply('rename', { name: $('#f-name').value }, reloadSession) }]);
}

/* -- Viaje -- */
/* resumen ampliado: economía, reputación, naves y multiherramienta; cada dato abre su ventana si existe */
async function overviewCards(row) {
  const box = $('#ov'); if (!box) return;
  let o; try { o = await api('/api/session/overview', null, { quiet: true }); } catch (e) { box.innerHTML = `<p class="muted small">${t('ov.unavailable')}</p>`; return; }
  if (S.view !== 'summary' || !$('#ov')) return;
  const f = newest(S.sess), num = v => v == null ? '—' : fmtN(v);
  const ui = n => `<img class="ovi" src="/uiicon/${n}" alt="" draggable="false" onerror="this.style.visibility='hidden'">`;
  const tile = (img, label, value, sub, attrs, cls = '') => `<${attrs ? 'a href="#"' : 'div'} class="ovt ${cls} ${attrs ? 'link' : ''}" ${attrs || ''}>
      <div class="ovimg">${img}</div><div class="ovtx"><span class="ovl">${label}</span><b class="ovv">${value}</b>${sub ? `<span class="ovs">${sub}</span>` : ''}</div>${attrs ? '<span class="ovgo">›</span>' : ''}</${attrs ? 'a' : 'div'}>`;
  const cls = c => c ? `<span class="ovclass c${esc(c)}">${esc(c)}</span>` : '';
  const RACEICON = { gek: 'gek', korvax: 'korvax', vykeen: 'vykeen', autophage: 'builder' };
  const raceImg = o.race && RACEICON[o.race.id] ? ui(RACEICON[o.race.id]) : '<span class="ovemo">👤</span>';
  const raceName = o.race ? (o.race.id ? t('ov.race.' + o.race.id) : esc(o.race.raw)) : '—';
  const REP = { TRA_STANDING: 'gek', EXP_STANDING: 'korvax', WAR_STANDING: 'vykeen', BUI_STANDING: 'builder',
                TGUILD_STAND: 'tradeguild', EGUILD_STAND: 'explorerguild', WGUILD_STAND: 'warriorguild', PIRATE_STAND: 'pirate' };
  const more = $('#ov-more');
  if (more) more.innerHTML = row(raceImg, t('ov.race'), raceName)
    + row(ui('units'), t('ov.units'), num(o.units)) + row(ui('nanites'), t('ov.nanites'), num(o.nanites))
    + row(ui('quicksilver'), t('ov.quicksilver'), num(o.quicksilver))
    + `<div class="ovsub">${t('ov.reputation')}</div>`
    + o.reputation.map(r => row(ui(REP[r.id]), t('ov.rep.' + r.id), num(r.value))).join('');
  const shipImg = sh => `<img class="ovi ship" src="/shipicon/${sh.type}" alt="" draggable="false" onerror="this.outerHTML=svgIcon('${sh.type}')">`;
  const shipTile = (label, sh) => tile(shipImg(sh), label, `${esc(sh.name || tt('term.unnamed_ship', { type: tt('term.shiptype.' + sh.type) }))} ${cls(sh.class)}`,
    esc(tt('term.shiptype.' + sh.type)), `data-goship="${sh.index}"`);
  box.innerHTML = `
    <h2 class="ovh">${t('ov.fleet')}</h2>
    <div class="ovrow">
      ${o.primary_ship ? shipTile(t('ov.primary_ship'), o.primary_ship) : ''}
      ${f.freighter ? tile(ui('freighter'), t('slots.freighter'), esc(f.freighter.name || t('slots.freighter')), '', 'data-goinv="freighter"') : ''}
      ${o.vehicle ? tile(ui('exocraft'), t('ov.vehicle'), esc(o.vehicle.name || o.vehicle.type), o.vehicle.name ? esc(o.vehicle.type) : '', `data-goinv="vehicle${o.vehicle.index}"`) : ''}
      ${o.corvettes.map(c => shipTile(t('ov.corvette'), c)).join('')}
      ${o.multitool ? tile(ui('weapon'), t('ov.weapon'), `${esc(o.multitool.name || t('inv.mt', { n: o.multitool.index + 1 }))} ${cls(o.multitool.class)}`, t('ov.no_view_yet'), null) : ''}
    </div>`;
  box.querySelectorAll('[data-goship]').forEach(a => a.onclick = e => { e.preventDefault(); S.openShip = +a.dataset.goship; S.view = 'ships'; renderEditor(); });
  box.querySelectorAll('[data-goinv]').forEach(a => a.onclick = async e => {
    e.preventDefault(); const id = a.dataset.goinv;
    S.invOpen = S.invOpen || new Set(['suit', 'freighter']); S.invOpen.add(id); S.view = 'inventories'; renderTopbar(); await viewInventories();
    const el = document.querySelector(`.invbox[data-inv="${id}"]`); el && el.scrollIntoView({ block: 'start', behavior: 'smooth' });
  });
}
function viewTravel() {
  const T = S.travel, cur = newest(S.sess);
  if (T.galaxy == null) T.galaxy = cur.galaxy;
  const q = T.q.toLowerCase();
  const list = S.galaxies.filter(g => !q || g.name.toLowerCase().includes(q) || String(g.n) === q)
    .map(g => `<div data-ri="${g.ri}" class="${g.ri === T.galaxy ? 'sel' : ''}"><span>${g.n}. ${esc(g.name)}</span><span class="t-${g.type}">${t('travel.type.' + g.type)}</span></div>`).join('');
  const modes = ['current', 'glyphs', 'galactic', 'manual'].map(m => `<button data-mode="${m}" class="${T.mode === m ? 'on' : ''}">${t('travel.mode_' + m)}</button>`).join('');
  let modeHtml = '';
  if (T.mode === 'glyphs') {
    const pad = S.glyphNames.map((n, i) => `<button data-g="${i.toString(16).toUpperCase()}"><b>${i.toString(16).toUpperCase()}</b>${esc(n)}</button>`).join('');
    modeHtml = `<div class="glyphline">${esc(T.glyphs.padEnd(12, '·'))}</div><div class="glyphpad">${pad}</div>
      <div class="row"><button class="btn" id="g-back">${t('travel.glyph_back')}</button><button class="btn" id="g-clear">${t('travel.glyph_clear')}</button></div>`;
  } else if (T.mode === 'galactic') {
    modeHtml = `<label>${t('travel.mode_galactic')}<input id="f-gal" placeholder="${t('travel.galactic_ph')}" value="${esc(T.galactic)}"></label>`;
  } else if (T.mode === 'manual') {
    modeHtml = `<div class="row">${['x', 'y', 'z'].map(a => `<label>Voxel ${a.toUpperCase()}<input type="number" data-ax="${a}" value="${T[a]}" style="width:110px"></label>`).join('')}</div>`;
  } else {
    modeHtml = `<p class="muted">X ${cur.address.x} · Y ${cur.address.y} · Z ${cur.address.z}</p>`;
  }
  const sysHtml = T.mode === 'glyphs' ? '' : `<div class="row"><label>${t('travel.system')}<input type="number" id="f-sys" min="0" max="4095" value="${T.system}" style="width:120px"></label>
      <label>${t('travel.planet')}<input type="number" id="f-planet" min="0" max="15" value="${T.planet}" style="width:90px"></label></div>
      <p class="muted" style="margin:0">${t('travel.system_hint')}</p>`;
  $('#main').innerHTML = `<h1>${t('travel.title')}</h1><p class="help">${t('travel.help')}</p>
    <p class="muted">${t('travel.now')}: <b>${esc(galLabel(cur.galaxy))}</b> · ${t('common.system')} ${cur.address.system}</p>
    <div class="grid" style="grid-template-columns:minmax(300px,1fr) minmax(360px,1.3fr)">
      <div class="card stack">
        <label>${t('travel.galaxy')}<input id="f-q" placeholder="${t('travel.search')}" value="${esc(T.q)}"></label>
        <div class="galaxies" id="gal-list">${list}</div></div>
      <div class="card stack"><div class="seg" id="modes">${modes}</div>${modeHtml}${sysHtml}
        <label>${t('travel.spawn')}</label>
        <label class="radio"><input type="radio" name="spawn" value="ship" ${T.spawn === 'ship' ? 'checked' : ''}>${t('travel.spawn_ship')}</label>
        <label class="radio"><input type="radio" name="spawn" value="keep" ${T.spawn === 'keep' ? 'checked' : ''}>${t('travel.spawn_keep')}</label>
        <p><b>${t('travel.summary')}:</b> ${esc(galLabel(T.galaxy))}</p>
        <div class="row end"><button class="btn primary" id="go">${t('common.preview')}</button></div></div></div>`;
  $('#f-q').oninput = e => { T.q = e.target.value; const pos = e.target.selectionStart; viewTravel(); const i = $('#f-q'); i.focus(); i.setSelectionRange(pos, pos); };
  $('#gal-list').onclick = e => { const d = e.target.closest('[data-ri]'); if (d) { T.galaxy = +d.dataset.ri; viewTravel(); } };
  $('#modes').onclick = e => { const b = e.target.closest('[data-mode]'); if (b) { T.mode = b.dataset.mode;
    if (T.mode === 'manual') Object.assign(T, { x: cur.address.x, y: cur.address.y, z: cur.address.z }); viewTravel(); } };
  document.querySelectorAll('[data-g]').forEach(b => b.onclick = () => { if (T.glyphs.length < 12) { T.glyphs += b.dataset.g; viewTravel(); } });
  $('#g-back') && ($('#g-back').onclick = () => { T.glyphs = T.glyphs.slice(0, -1); viewTravel(); });
  $('#g-clear') && ($('#g-clear').onclick = () => { T.glyphs = ''; viewTravel(); });
  $('#f-gal') && ($('#f-gal').oninput = e => { T.galactic = e.target.value; });
  document.querySelectorAll('[data-ax]').forEach(i => i.oninput = () => { T[i.dataset.ax] = +i.value; });
  $('#f-sys') && ($('#f-sys').oninput = e => { T.system = +e.target.value; });
  $('#f-planet') && ($('#f-planet').oninput = e => { T.planet = +e.target.value; });
  document.querySelectorAll('input[name=spawn]').forEach(r => r.onchange = () => { T.spawn = r.value; });
  $('#go').onclick = goTravel;
}
async function goTravel() {
  const T = S.travel, cur = newest(S.sess);
  let a;
  try {
    if (T.mode === 'glyphs') a = (await api('/api/convert', { glyphs: T.glyphs, galaxy: T.galaxy })).address;
    else if (T.mode === 'galactic') a = (await api('/api/convert', { galactic: T.galactic, galaxy: T.galaxy, planet: T.planet })).address;
    else if (T.mode === 'manual') a = { galaxy: T.galaxy, x: T.x, y: T.y, z: T.z, system: T.system, planet: T.planet };
    else a = { galaxy: T.galaxy, x: cur.address.x, y: cur.address.y, z: cur.address.z, system: T.system, planet: T.planet };
  } catch (e) { return toast(errText(e), true); }
  previewAndApply('travel', { ...a, spawn: T.spawn }, reloadSession);
}

/* -- Naves: iconos arrastrables -- */
const ICONS = {
  interceptor: '<path d="M32 4 L40 22 L60 30 L40 32 L36 40 L28 40 L24 32 L4 30 L24 22 Z"/><path d="M32 12 L32 30"/>',
  living: '<path d="M10 22 C14 8 50 8 54 22 C50 34 14 34 10 22 Z"/><path d="M14 28 C8 36 4 36 2 34 M50 28 C56 36 60 36 62 34 M24 31 C22 38 20 40 18 40 M40 31 C42 38 44 40 46 40"/><circle cx="40" cy="20" r="3"/>',
  corvette: '<rect x="8" y="12" width="40" height="18" rx="3"/><path d="M48 14 L60 18 L60 24 L48 28 M14 30 L14 36 M42 30 L42 36 M14 12 L20 6 L36 6 L42 12"/>',
  exotic: '<circle cx="32" cy="16" r="10"/><path d="M24 24 C22 32 18 36 14 38 M29 26 C29 33 27 38 25 40 M35 26 C35 33 37 38 39 40 M40 24 C42 32 46 36 50 38"/>',
  fighter: '<path d="M32 4 L38 20 L60 34 L38 30 L34 40 L30 40 L26 30 L4 34 L26 20 Z"/>',
  hauler: '<path d="M14 10 L50 10 L58 20 L50 32 L14 32 L6 20 Z"/><path d="M20 10 L20 32 M44 10 L44 32"/>',
  explorer: '<path d="M6 22 L40 18 L58 22 L40 26 Z"/><path d="M40 18 L44 6 M40 26 L44 38 M14 21 L10 12 M14 23 L10 32"/>',
  shuttle: '<path d="M12 26 C12 12 52 12 52 26 L46 32 L18 32 Z"/><path d="M24 16 L40 16"/>',
  solar: '<path d="M22 24 L42 24 L46 30 L18 30 Z"/><path d="M32 24 L12 6 M32 24 L22 4 M32 24 L32 2 M32 24 L42 4 M32 24 L52 6"/>',
  ship: '<path d="M32 6 L54 34 L32 28 L10 34 Z"/>',
};
const svgIcon = type => `<svg viewBox="0 0 64 44" class="shipicon" aria-hidden="true">${ICONS[type] || ICONS.ship}</svg>`;
/* emblema oficial del juego para el tipo de nave; si no está, el dibujo genérico */
const icon = type => `<img class="shipemblem" src="/shipicon/${type}" alt="" draggable="false" onerror="this.outerHTML=svgIcon('${type}')">`;
const shipLabel = sh => sh.name || tt('term.unnamed_ship', { type: tt('term.shiptype.' + sh.type) });

/* adjacency frames: technologies of the same family that touch get a frame of one colour, as in the game;
   two different groups that touch (even at a corner) never share a colour */
const ADJ_COLORS = ['#3fd0ff', '#4ee08a', '#e05cff', '#5b8cff', '#ff6fae', '#2fe0c8', '#b8f03a', '#9b7bff', '#ff9f43', '#f5f5f5'];
function adjFrames(items) {
  const fam = {};
  items.forEach(it => { if (it[9]) fam[`${it[0]},${it[1]}`] = it[9]; });
  const comp = {}, comps = [];
  for (const k0 of Object.keys(fam)) {
    if (comp[k0] != null) continue;
    const id = comps.length, cells = [], stack = [k0]; comp[k0] = id;
    while (stack.length) {
      const k = stack.pop(); cells.push(k);
      const [x, y] = k.split(',').map(Number);
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const n = `${x + dx},${y + dy}`;
        if (fam[n] === fam[k0] && comp[n] == null) { comp[n] = id; stack.push(n); }
      }
    }
    comps.push(cells);
  }
  const color = {};
  comps.forEach((cells, id) => {
    if (cells.length < 2) return;
    const near = new Set();
    for (const k of cells) {
      const [x, y] = k.split(',').map(Number);
      for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
        const c = comp[`${x + dx},${y + dy}`];
        if (c != null && c !== id && color[c] != null) near.add(color[c]);
      }
    }
    let i = 0; while (near.has(i) && i < ADJ_COLORS.length - 1) i++;
    color[id] = i;
  });
  const out = {};
  for (const [k, id] of Object.entries(comp)) {
    if (color[id] == null) continue;
    const [x, y] = k.split(',').map(Number), same = (dx, dy) => comp[`${x + dx},${y + dy}`] === id;
    out[k] = { c: ADJ_COLORS[color[id]], e: (same(0, -1) ? '' : 't') + (same(1, 0) ? '' : 'r') + (same(0, 1) ? '' : 'b') + (same(-1, 0) ? '' : 'l') };
  }
  return out;
}
/* tooltip of a technology, from the game's own data: subtitle, class, description and, for upgrades, the
   range of each stat (the exact roll depends on each module and is not stored in the save) */
function fmtRange(stat, a, b) {
  const one = v => (v > 0 ? '+' : '') + (+v.toFixed(2)).toLocaleString();
  let f;
  if (stat === 'Ship_Cargo_Slots' || a > 2 || b > 2) f = v => one(v);
  else if (a >= 0.5) f = v => one((v - 1) * 100) + '%';
  else f = v => one(v * 100) + '%';
  if (a === 1 && b === 1 && stat !== 'Ship_Cargo_Slots') return '';
  return a === b ? f(a) : `${f(a)} … ${f(b)}`;
}
function techTip(id, rid) {
  const r = realId(id, rid), tp = r && S.tips && S.tips[r];
  if (!tp) return '';
  let out = '';
  if (tp.s) out += '\n' + tp.s;
  if (tp.c) out += '\n' + rawT('tip.class', { c: tp.c });
  if (tp.d) out += '\n\n' + tp.d;
  if (tp.r && tp.r.length) out += '\n\n' + rawT('tip.ranges') + tp.r.map(([st, a, b]) => {
    const v = fmtRange(st, a, b); return `\n• ${rawT('statn.' + st)}${v ? ': ' + v : ''}`; }).join('');
  return out;
}
function invGrid(ship, grid, inv, items, withSc) {
  const adj = grid === 'tech' ? adjFrames(items) : {};
  const sc = new Set((withSc ? inv.sc : []).map(c => c.join(','))), valid = new Set(inv.valid_cells.map(c => c.join(',')));
  const at = Object.fromEntries(items.map(it => [`${it[0]},${it[1]}`, it]));
  let h = `<div class="igrid fluid" data-ship="${ship}" data-grid="${grid}" style="grid-template-columns:repeat(${inv.w},minmax(0,1fr))">`;
  for (let y = 0; y < inv.h; y++) for (let x = 0; x < inv.w; x++) {
    const k = `${x},${y}`, it = at[k];
    const grp = it && grid === 'cargo' ? it[7] : undefined, dmg = !!(it && (grid === 'tech' ? it[7] : it[8]));
    const low = !!(it && grid === 'tech' && it[8]);
    const a = adj[k];
    const cls = `c ${sc.has(k) ? 'sc' : ''} ${valid.has(k) ? '' : 'locked'} ${it ? 'has' : ''} ${dmg ? 'dmg' : ''} ${low ? 'low' : ''} ${a ? 'adj ' + [...a.e].map(e => 'a' + e).join(' ') : ''}`;
    let inner = '', chg = '';
    if (it) {
      const [, , id, a, m, kind, rid] = it;
      const qty = grid === 'cargo' && kind !== 'Technology' && a > 1 ? `<b class="qty">${a}</b>` : '';
      const pct = grid === 'tech' && kind === true && m > 0 ? Math.max(0, Math.min(100, Math.round(a / m * 100))) : null;
      const bar = pct != null ? `<i class="chg" style="width:${pct}%"></i>${pct < 100 ? `<b class="pct">${pct}%</b>` : ''}` : '';
      if (pct != null) chg = '\n' + rawT('grid.charge', { a: Math.max(0, a), m, p: pct });
      inner = itemIcon(id, rid, qty + bar + (dmg ? '<b class="dmgi">!</b>' : ''));
    }
    const cat = it ? catText(grp, grid === 'tech' ? 'Technology' : it[5]) : '';
    h += `<span class="${cls}" data-cell="${k}" ${a ? `style="--adj:${a.c}"` : ''} ${it ? 'draggable="true"' : ''} title="${x + 1}·${y + 1}${it ? ' — ' + esc(itemName(it[2], it[6])) + (grid === 'cargo' && it[3] > 1 ? ' ×' + it[3] : '') + (dmg ? '\n' + esc(rawT('grid.damaged')) : '') + esc(chg) + (grid === 'tech' ? esc(techTip(it[2], it[6])) : '') + (low ? '\n' + esc(rawT('grid.low')) : '') + (cat ? '\n' + esc(cat) : '') : ''}${sc.has(k) ? ' ★' : ''}">${inner}</span>`;
  }
  return h + '</div>';
}
function scGrid(inv, editable, techItems) {
  const names = Object.fromEntries((techItems || []).map(([x, y, id, , , , rid]) => [`${x},${y}`, itemName(id, rid)]));
  const sc = new Set(inv.sc.map(c => c.join(','))), used = new Set(inv.cells.map(c => c.join(','))),
    valid = new Set(inv.valid_cells.map(c => c.join(',')));
  let h = `<div class="scgrid ${editable ? 'edit' : ''}" style="grid-template-columns:repeat(${inv.w},1fr)">`;
  for (let y = 0; y < inv.h; y++) for (let x = 0; x < inv.w; x++) {
    const k = `${x},${y}`;
    h += `<span class="c ${sc.has(k) ? 'sc' : ''} ${used.has(k) ? 'used' : ''} ${valid.has(k) ? '' : 'locked'}" data-cell="${k}" title="${x + 1}·${y + 1}${names[k] ? ' — ' + esc(names[k]) : ''}"></span>`;
  }
  return h + '</div>';
}
function gameDataHtml() {
  const g = S.gd;
  if (!g || !g.available) return `<p class="msg warn"><span>${t('gd.none')}</span></p>`;
  const state = g.up_to_date === true ? `<span class="pill ok">${t('gd.ok')}</span>` : g.up_to_date === false
    ? `<span class="pill bad">${t('gd.old')}</span>` : `<span class="pill warn">${t('gd.unknown')}</span>`;
  return `<div class="kv small"><span>${t('gd.title')}</span><span>${state}<br>${t('gd.detail', { n: g.items, date: fmtDate(g.built), compiler: esc(g.compiler || '') })}</span></div>
    ${g.up_to_date === false ? `<p class="msg warn"><span>${t('gd.old_help')}</span></p>` : ''}
    <p class="muted small">${esc(g.source || '')}</p>`;
}
function modulesHtml(I) {
  const items = [...I.tech_items].sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  return `<details class="mods"><summary>${t('ship.modules', { n: items.length })}</summary><ul>${items.map(([x, y, id, a, m, ch, rid]) =>
    `<li><span>${esc(itemName(id, rid))}</span>${ch ? `<span class="muted small">${a < 0 ? 0 : a}/${m}</span>` : ''}</li>`).join('')}</ul></details>`;
}
function shipPanel(sh, f) {
  const I = sh.info, can = op => I && I.allowed.includes(op);
  const lowNames = x => x === I.Inventory_TechOnly ? I.tech_items.filter(it => it[8]).map(it => itemName(it[2], it[6])).join(', ') : '';
  const cellsInfo = x => `<span>${t('ship.cells', { used: x.used, valid: x.valid, total: x.w * x.h })}</span>${x.damaged ? `<span class="bad">${t('ship.damaged', { n: x.damaged })}</span>` : ''}${x.low ? `<span class="warnc">${t('ship.low', { n: x.low })}</span>` : ''}`;
  const g = (x, lbl, note = '') => `<div class="gridstat"><b>${lbl}</b><span>${t('ship.cells', { used: x.used, valid: x.valid, total: x.w * x.h })}</span>${note}
    ${x.damaged ? `<span class="bad">${t('ship.damaged', { n: x.damaged })}</span>` : ''}${x.low ? `<span class="warnc" title="${esc(lowNames(x))}">${t('ship.low', { n: x.low })}: ${esc(lowNames(x))}</span>` : ''}
    </div>`;
  const others = f.ships.filter(o => o.model && o.index !== sh.index && o.info && o.info.kind === 'regular')
    .map(o => `<option value="${o.index}">${o.index + 1}. ${esc(shipLabel(o))} (${o.info.modules.length})</option>`).join('');
  const act = (op, label, extra = '', cls = '', off = false) => can(op)
    ? `<div class="act ${cls} ${op === 'expand' ? 'below' : ''}"><button class="btn small ${cls === 'danger' ? 'danger' : ''}" data-op="${op}" ${off ? 'disabled' : ''}>${label}</button>${extra}</div>` : '';
  const prim = sh.index === f.primary_ship;
  const sameModel = f.ships.filter(o => o.model && o.model === sh.model && o.index !== sh.index && o.seed && o.seed !== I.seed)
    .map(o => `<option value="${esc(o.seed)}">${o.index + 1}. ${esc(shipLabel(o))} · ${esc(o.seed)}</option>`).join('');
  const nDmg = (I.Inventory.damaged || 0) + (I.Inventory_TechOnly.damaged || 0);
  const nClosed = I.kind === 'corvette' ? 0 : [I.Inventory, I.Inventory_TechOnly].reduce((n, x) => n + Math.max(0, x.w * x.h - x.valid), 0);
  const why = I.kind === 'corvette' ? t('ship.protect_corvette') : I.kind === 'living' ? t('ship.protect_living') : '';
  const full = I.Inventory.valid >= 120 && I.Inventory.w * I.Inventory.h >= 120 && (!I.sc_fixed || (I.Inventory_TechOnly.valid >= 60 && I.Inventory_TechOnly.w * I.Inventory_TechOnly.h >= 60));
  const ib = (op, icon, tip, off = false) => can(op)
    ? `<button class="ibtn" data-op="${op}" title="${esc(tip)}" aria-label="${esc(tip)}" ${off ? 'disabled' : ''}>${icon}</button>` : '';
  const head = `<div class="shiphead">${icon(sh.type)}<div class="shipttl"><span class="shipline"><b class="shipname ${sh.name ? '' : 'muted'}">${esc(shipLabel(sh))}</b>
      ${can('rename') ? `<button class="pencil" data-renameship title="${esc(rawT('ship.rename'))}" aria-label="${esc(rawT('ship.rename'))}">✎</button>` : ''}
      ${ib('repair', '🔧', rawT('ship.repair', { n: nDmg }), !nDmg)}
      ${ib('unlock', '🔓', rawT('ship.unlock', { n: nClosed }), !nClosed)}
      ${ib('recharge', '⚡', rawT('ship.recharge') + (I.Inventory_TechOnly.low ? ` (${I.Inventory_TechOnly.low})` : ''), !(I.Inventory.low || I.Inventory_TechOnly.low))}
      ${ib('expand', '⤢', rawT('ship.expand') + ' — ' + (full ? rawT('ship.expand_max') : I.class === 'S' ? rawT(I.sc_fixed ? 'ship.expand_note_both' : 'ship.expand_note') : rawT('ship.expand_needs_s')), full)}</span>
      <span class="muted small">${esc(tt('term.shiptype.' + sh.type))} · ${t('ship.class')} ${esc(I.class)}${nDmg ? ` · <span class="bad">⚠ ${nDmg}</span>` : ''}</span></div>
    </div>
    <div class="renamebar" data-shiprenamebar hidden><input data-in="name" maxlength="60" value="${esc(sh.name)}" placeholder="${esc(shipLabel(sh))}">
      <button class="btn small primary" data-op="rename">${t('inv.rename_ok')}</button><button class="btn small" data-renamecancel>${t('common.cancel')}</button></div>`;
  return `<div class="panel" data-panel="${sh.index}" data-scmax="${I.sc_max}">
    ${head}
    <div class="kv small">
      <span>${t('ship.type')}</span><span>${esc(tt('term.shiptype.' + sh.type))}</span>
      <span>${t('ship.class')}</span><span>${esc(I.class)}</span>
      <span>Seed</span><span><code>${esc(I.seed || '—')}</code></span>
      <span>${t('ship.model')}</span><span><code>${esc(sh.model)}</code></span></div>
    ${statsHtml(I.stats, false)}
    <div class="grids" style="--tw:${I.Inventory_TechOnly.w}fr;--cw:${I.Inventory.w}fr"><div class="scbox"><b>${t('ship.sc_title')}</b>
      <span>${t('ship.sc_desc', { n: I.Inventory_TechOnly.super, max: I.sc_max, cls: esc(I.Inventory_TechOnly.cls), shape: scName(I.sc_shape) })}</span>
      ${invGrid(sh.index, 'tech', I.Inventory_TechOnly, I.tech_items, true)}
      ${g(I.Inventory_TechOnly, t('ship.tech'), `<span class="muted small">${t('ship.class_note')}</span>${modulesHtml(I)}`)}</div>
    <div class="scbox"><b>${t('grid.cargo_title')}</b>${cellsInfo(I.Inventory)}<span class="muted small">${t('grid.drag_hint')}</span>
      ${invGrid(sh.index, 'cargo', I.Inventory, I.cargo_items, false)}</div></div>
    ${why ? `<p class="muted small">🛡 ${why}</p>` : ''}
    <div class="acts">
      ${prim ? '' : `<div class="act"><button class="btn small" data-op="primary">★ ${t('ships.set_primary')}</button></div>`}
      ${act('seed', t('ship.seed'), `<div class="seedbox"><input data-in="seed" value="${esc(I.seed || '')}" placeholder="0x…" spellcheck="false">${sameModel ? `<select data-seedfrom title="${esc(rawT('ship.seed_from_tip'))}"><option value="">${t('ship.seed_from')}</option>${sameModel}</select>` : ''}<div class="seedhist" data-sh="${sh.index}"></div><span class="muted small">${t('ship.seed_note')}</span></div>`, 'wide')}
      ${act('copy_tech', t('ship.copy_tech'), others ? `<select data-in="from">${others}</select>` : `<span class="muted small">${t('ship.copy_none')}</span>`)}
      ${act('duplicate', t('ship.duplicate'))}
      ${I.kind !== 'corvette' ? `<div class="act wide xfer" data-xfer><span class="xlabel">📦 ${t('ship.transfer')}</span>
        <select data-xferdest><option value="">${t('common.loading')}</option></select>
        <span class="xmode"><label class="chk"><input type="radio" name="xm${sh.index}" value="copy" checked> ${t('ship.transfer_copy_short')}</label>
        <label class="chk"><input type="radio" name="xm${sh.index}" value="move"> ${t('ship.transfer_move_short')}</label></span>
        <button class="btn small" data-xfergo disabled>${t('common.preview')}</button>
        <span class="muted small">${t('ship.transfer_note')}</span></div>` : ''}
      ${act('delete', t('ship.delete'), '', 'danger')}
    </div></div>`;
}

/* historial de seeds aplicados a cada nave: volver al original o rotar con un clic */
async function fillSeedHistory() {
  const boxes = document.querySelectorAll('.seedhist[data-sh]'); if (!boxes.length) return;
  let h; try { h = (await api('/api/session/seeds', null, { quiet: true })).history; } catch (_) { return; }
  const f = newest(S.sess);
  boxes.forEach(box => {
    const idx = +box.dataset.sh, list = h[idx], sh = f.ships.find(x => x.index === idx);
    if (!list || !sh || !sh.info) { box.innerHTML = ''; return; }
    const cur = sh.info.seed, pos = list.findIndex(x => x.seed === cur);
    const go = d => list[((pos < 0 ? 0 : pos) + d + list.length) % list.length].seed;
    box.innerHTML = `<div class="sh-head"><b>${t('ship.seed_hist')}</b>
        <button class="btn small" data-seedgo="${esc(go(-1))}" title="${esc(rawT('ship.seed_prev'))}">◀</button>
        <button class="btn small" data-seedgo="${esc(go(1))}" title="${esc(rawT('ship.seed_next'))}">▶</button></div>
      <div class="sh-list">${list.map(x => `<button class="sh-chip ${x.seed === cur ? 'cur' : ''}" data-seedgo="${esc(x.seed)}" ${x.seed === cur ? 'disabled' : ''}>
        ${x.original ? `<span class="sh-orig">${t('ship.seed_original')}</span>` : ''}<code>${esc(x.seed)}</code></button>`).join('')}</div>`;
    box.querySelectorAll('[data-seedgo]').forEach(b => b.onclick = async () => {
      if (b.dataset.seedgo === cur) return;
      await quickStage('ship', { ship: idx, op: 'seed', seed: b.dataset.seedgo });
    });
  });
}
/* copiar o mover una nave, con todo lo que lleva, a otras partidas */
async function transferBox(box, ship) {
  const sel = box.querySelector('[data-xferdest]'), go = box.querySelector('[data-xfergo]');
  let slots; try { slots = (await api('/api/slots', null, { quiet: true })).slots; } catch (e) { sel.innerHTML = `<option value="">${esc(errText(e))}</option>`; return; }
  const dest = slots.filter(s => s.files && s.slot !== S.sess.slot);
  if (!dest.length) { sel.innerHTML = `<option value="">${t('ship.transfer_none')}</option>`; sel.disabled = true; return; }
  sel.innerHTML = `<option value="">${t('ship.transfer_pick_dest')}</option>`
    + dest.map(s => `<option value="${s.slot}">Slot ${s.slot} · ${esc(slotName(s))}</option>`).join('')
    + (dest.length > 1 ? `<option value="${dest.map(s => s.slot).join(',')}">${t('ship.transfer_all')}</option>` : '');
  sel.onchange = () => { go.disabled = !sel.value; };
  go.onclick = () => {
    if (!sel.value) return;
    const move = box.querySelector('input[type=radio]:checked').value === 'move';
    previewAndApply('ship_transfer', { ship, dest_slots: sel.value.split(',').map(Number), move }, reloadSession);
  };
}
function viewShips() {
  const f = newest(S.sess);
  const filled = f.ships.filter(x => x.model);
  if (!S.order) S.order = filled.map(x => x.index);
  const byIndex = Object.fromEntries(f.ships.map(x => [x.index, x]));
  const moved = S.order.join() !== filled.map(x => x.index).join();
  const tiles = S.order.map((idx, pos) => {
    const sh = byIndex[idx], prim = idx === f.primary_ship, open = S.openShip === idx && sh.info;
    const I = sh.info, alert = I && (I.Inventory.damaged + I.Inventory_TechOnly.damaged);
    return `<div class="tile ${prim ? 'primary' : ''} ${open ? 'open' : ''}" draggable="${open ? 'false' : 'true'}" data-idx="${idx}">
      <span class="pos">${pos + 1}</span>${prim ? `<span class="tag new star">★ ${t('ships.primary')}</span>` : ''}
      ${open ? '' : `<div class="tilehead">${icon(sh.type)}<div>
        <b class="${sh.name ? '' : 'muted'}">${esc(shipLabel(sh))}</b>
        <span class="muted small">${esc(tt('term.shiptype.' + sh.type))}${I ? ` · ${t('ship.class')} ${esc(I.class)}` : ''}${alert ? ` · <span class="bad">⚠ ${alert}</span>` : ''}</span>
        ${I ? statsHtml(I.stats, true) : ''}</div></div>`}
      ${I ? `<button class="btn small" data-toggle="${idx}">${open ? t('ship.close') : t('ship.options')}</button>` : ''}
      ${open ? shipPanel(sh, f) : ''}</div>`;
  }).join('');
  $('#main').innerHTML = `<h1>${t('ships.title')}</h1><p class="help">${t('ships.help')}</p>
    ${moved ? `<div class="card orderbar row between"><span>${t('ships.order_changed')}</span>
      <span class="row"><button class="btn" id="o-undo">${t('ships.order_undo')}</button><button class="btn primary" id="o-apply">${t('ships.order_apply')}</button></span></div>` : ''}
    <div class="tiles" id="tiles">${tiles}</div>
    <p class="muted">${t('ships.icons_note')}</p>`;
  let dragIdx = null;
  document.querySelectorAll('.tile[draggable=true]').forEach(el => {
    el.addEventListener('dragstart', e => { dragIdx = +el.dataset.idx; el.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', el.dataset.idx); });
    el.addEventListener('dragend', () => el.classList.remove('dragging'));
  });
  document.querySelectorAll('.tile').forEach(el => {
    el.addEventListener('dragover', e => { e.preventDefault(); el.classList.add('over'); });
    el.addEventListener('dragleave', () => el.classList.remove('over'));
    el.addEventListener('drop', e => {
      e.preventDefault(); el.classList.remove('over');
      const target = +el.dataset.idx; if (dragIdx == null || dragIdx === target) return;
      const o = S.order.filter(x => x !== dragIdx); o.splice(o.indexOf(target) + (S.order.indexOf(dragIdx) < S.order.indexOf(target) ? 1 : 0), 0, dragIdx);
      S.order = o; viewShips();
    });
  });
  let dragFrom = null;
  document.querySelectorAll('.igrid .c[draggable=true]').forEach(c => {
    c.addEventListener('dragstart', e => { e.stopPropagation(); const g = c.closest('.igrid');
      dragFrom = { ship: +g.dataset.ship, grid: g.dataset.grid, cell: c.dataset.cell }; c.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', 'cell'); });
    c.addEventListener('dragend', () => c.classList.remove('dragging'));
  });
  document.querySelectorAll('.igrid .c:not(.locked)').forEach(c => {
    c.addEventListener('dragover', e => { const g = c.closest('.igrid');
      if (dragFrom && dragFrom.grid === g.dataset.grid && dragFrom.ship === +g.dataset.ship) { e.preventDefault(); e.stopPropagation(); c.classList.add('over'); } });
    c.addEventListener('dragleave', () => c.classList.remove('over'));
    c.addEventListener('drop', async e => {
      e.preventDefault(); e.stopPropagation(); c.classList.remove('over');
      const g = c.closest('.igrid'), from = dragFrom; dragFrom = null;
      if (!from || from.cell === c.dataset.cell) return;
      swapCells(g, from.cell, c.dataset.cell);
      await quickStage('ship', { ship: from.ship, op: 'move', grid: from.grid, from: from.cell.split(',').map(Number), to: c.dataset.cell.split(',').map(Number) });
    });
  });
  document.querySelectorAll('.igrid[data-ship] .c.has').forEach(c => c.addEventListener('contextmenu', e => itemMenu(e, c)));
  document.querySelectorAll('[data-toggle]').forEach(b => b.onclick = () => { const i = +b.dataset.toggle; S.openShip = S.openShip === i ? null : i; viewShips(); });
  document.querySelectorAll('[data-panel] [data-seedfrom]').forEach(sel => sel.onchange = () => {
    const inp = sel.closest('.act').querySelector('[data-in="seed"]'); if (sel.value && inp) inp.value = sel.value; });
  document.querySelectorAll('[data-panel] [data-op]').forEach(b => b.onclick = () => {
    const panel = b.closest('[data-panel]'), ship = +panel.dataset.panel, op = b.dataset.op;
    const val = name => { const el = panel.querySelector(`[data-in="${name}"]`); return el ? el.value : undefined; };
    if (op === 'primary') return previewAndApply('set_primary', { ship }, reloadSession);
    previewAndApply('ship', { ship, op, name: val('name'), cls: val('cls'), seed: val('seed'), from: val('from') != null ? +val('from') : undefined }, reloadSession);
  });
  document.querySelectorAll('[data-panel] [data-renameship]').forEach(b => b.onclick = () => {
    const bar = b.closest('[data-panel]').querySelector('[data-shiprenamebar]'); bar.hidden = !bar.hidden;
    if (!bar.hidden) { const i = bar.querySelector('input'); i.focus(); i.select(); } });
  document.querySelectorAll('[data-panel] [data-renamecancel]').forEach(b => b.onclick = () => { b.closest('[data-shiprenamebar]').hidden = true; });
  document.querySelectorAll('[data-shiprenamebar] input').forEach(i => i.addEventListener('keydown', e => {
    if (e.key === 'Enter') i.closest('[data-shiprenamebar]').querySelector('[data-op="rename"]').click();
    if (e.key === 'Escape') i.closest('[data-shiprenamebar]').hidden = true; }));
  document.querySelectorAll('[data-panel] [data-xfer]').forEach(box => transferBox(box, +box.closest('[data-panel]').dataset.panel));
  fillSeedHistory();
  $('#o-undo') && ($('#o-undo').onclick = () => { S.order = null; viewShips(); });
  $('#o-apply') && ($('#o-apply').onclick = () => {
    const empties = f.ships.filter(x => !x.model).map(x => x.index);
    previewAndApply('reorder', { order: [...S.order, ...empties] }, reloadSession);
  });
}

/* -- Inventarios (fase 2c): exotraje, carguero, contenedores, almacén de corbeta y carga de naves -- */
const rawT = (key, p = {}) => (S.dict[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => p[k] ?? '');   // texto plano (sin escapar)
const INV_GROUPS = ['gases', 'minerals', 'fuel', 'metal', 'catalyst', 'stellar', 'precious', 'flora', 'special',
  'components', 'trade', 'consumables', 'curiosities', 'building', 'salvage', 'treasure', 'creatures', 'custom',
  'tech_items', 'mods_cb', 'mods_a', 'mods_sx', 'other'];
const INV_SCOPES = { chests: ['chest'], storage: ['chest', 'freighter', 'corvette'], all: ['chest', 'freighter', 'corvette', 'suit', 'ship', 'vehicle'] };
const TECH_KINDS = new Set(['shiptech', 'suittech', 'freightertech', 'mttech']);
function invLabel(c) {
  const id = c.inv ?? c.id;
  if (c.kind === 'suit') return rawT('inv.suit');
  if (c.kind === 'freighter') return rawT('inv.freighter');
  if (c.kind === 'corvette') return rawT('inv.corvette');
  if (c.kind === 'suittech') return rawT('inv.suittech');
  if (c.kind === 'freightertech') return rawT('inv.freightertech');
  if (c.kind === 'vehicle') { const v = c.vehicle ?? +String(id).slice(7);
    return rawT('inv.vehicle', { name: c.vname || tt('term.vehicle.' + v) }); }
  if (c.kind === 'mttech') { const n = c.n ?? (c.mt ?? 0) + 1;
    return c.name ? rawT('inv.mt_named', { n, name: c.name }) : rawT('inv.mt', { n }); }
  if (c.kind === 'chest') {
    const n = +String(id).slice(5), named = c.name && c.name !== 'BLD_STORAGE_NAME';
    return named ? rawT('inv.chest_named', { n, name: c.name }) : rawT('inv.chest', { n });
  }
  const n = c.n ?? (c.ship ?? 0) + 1;
  return c.name ? rawT('inv.ship', { n, name: c.name }) : rawT('inv.ship_unnamed', { n });
}
const fold = v => String(v ?? '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
const fmtN = n => Number(n).toLocaleString(S.state && S.state.settings.ui_lang === 'en' ? 'en-US' : 'es-ES');
function itemKeys(id, rid) {
  const r = realId(id, rid);
  if (!r) return fold(t('item.procedural'));
  return fold(`${(S.names || {})[r] || ''} ${(S.namesAlt || {})[r] || ''} ${r}`);
}

async function viewInventories() {
  const key = `${S.sess.slot}:${S.sess.changes.length}:${S.sess.dirty}`;
  if (!S.inv || S.inv.key !== key) {
    if (!S.inv) $('#main').innerHTML = `<h1>${t('inv.title')}</h1><p class="muted">${t('inv.loading')}</p>`;
    try { S.inv = { key, data: await api('/api/session/inventories', null, { quiet: true }) }; S.invSel = {}; }   // celdas nuevas: selección limpia
    catch (e) { S.inv = null; $('#main').innerHTML = `<p class="msg block"><span>${esc(errText(e))}</span></p>`; return; }
    if (S.view !== 'inventories') return;
  }
  const d = S.inv.data, main = $('#main'), top = main.scrollTop;
  S.invOpen = S.invOpen || new Set(['suit', 'freighter']);
  const fixed = d.containers.filter(c => ['suit', 'freighter', 'chest', 'corvette'].includes(c.kind)),
    shipsC = d.containers.filter(c => c.kind === 'ship'), vehC = d.containers.filter(c => c.kind === 'vehicle'),
    techC = d.containers.filter(c => TECH_KINDS.has(c.kind));
  const box = c => {
    const label = invLabel(c), used = c.items.length, chest = c.kind === 'chest', gatherable = ['chest', 'corvette', 'freighter', 'vehicle'].includes(c.kind);
    const tech = TECH_KINDS.has(c.kind);
    const emb = c.kind === 'ship' ? icon(c.ship_type || 'ship') : '';
    const flag = c.kind === 'mttech' && c.active ? ` <span class="pill blue small">${t('inv.mt_active')}</span>` : '';
    if (tech) return `<details class="card invbox tech" data-inv="${c.id}" ${S.invOpen.has(c.id) ? 'open' : ''}>
      <summary><span class="ttl"><span>${esc(label)}</span>${flag}</span>
        <span class="muted small">${t('inv.cells', { used, valid: c.valid.length })}</span></summary>
      ${c.blocked ? `<p class="msg warn"><span>${t('msg.' + c.blocked)}</span></p>` : `<p class="muted small">${t('inv.tech_hint')}</p>`}
      ${boxGrid(c)}</details>`;
    return `<details class="card invbox" data-inv="${c.id}" ${S.invOpen.has(c.id) ? 'open' : ''}>
      <summary><span class="ttl">${emb}<span>${esc(label)}</span>${chest ? `<button class="pencil" data-rename="${c.id}" title="${esc(rawT('inv.rename'))}" aria-label="${esc(rawT('inv.rename'))}">✎</button>` : ''}</span>
        <span class="muted small">${t('inv.cells', { used, valid: c.valid.length })}</span></summary>
      <div class="renamebar" data-renamebar="${c.id}" hidden><input maxlength="40" placeholder="${esc(rawT('inv.chest', { n: +c.id.slice(5) }))}">
        <button class="btn small primary" data-rename-ok="${c.id}">${t('inv.rename_ok')}</button><button class="btn small" data-rename-cancel="${c.id}">${t('common.cancel')}</button></div>
      <div class="row end invbar">
        ${gatherable ? `<button class="btn small" data-gather="${c.id}" title="${esc(rawT('inv.gather_hint'))}">${t('inv.gather')}</button>` : ''}
        <button class="btn small" data-sort="${c.id}" title="${esc(rawT('inv.sort_hint'))}" ${used ? '' : 'disabled'}>${t('inv.sort')}</button>
        <button class="btn small" data-movesel="${c.id}" title="${esc(rawT('inv.movesel_hint'))}" ${selCount(c.id) ? '' : 'disabled'}>${t('inv.movesel')}${selCount(c.id) ? ` (${selCount(c.id)})` : ''}</button></div>
      ${boxGrid(c)}</details>`;
  };
  const level = d.level ? t('inv.level', { level: rawT('inv.level.' + d.level) }) : t('inv.no_level');
  main.innerHTML = `<h1>${t('inv.title')}</h1><p class="help">${t('inv.help')}</p>
    <p class="muted small">${level}</p>
    <div class="invsearch"><input id="inv-q" type="search" autocomplete="off" placeholder="${esc(rawT('inv.search'))}" value="${esc(S.invQ || '')}"><div id="inv-found"></div></div>
    <div class="invlist">${fixed.map(box).join('')}</div>
    ${vehC.length ? `<h2>${t('inv.group_vehicles')}</h2><div class="invlist">${vehC.map(box).join('')}</div>` : ''}
    ${shipsC.length ? `<h2>${t('inv.group_ships')}</h2><div class="invlist">${shipsC.map(box).join('')}</div>` : ''}
    ${techC.length ? `<h2>${t('inv.group_tech')}</h2><p class="muted small">${t('inv.group_tech_help')}</p><div class="invlist">${techC.map(box).join('')}</div>` : ''}`;
  main.scrollTop = top;
  bindInventories();
  applyInvSearch();
}
function boxGrid(c) {
  const valid = new Set(c.valid.map(v => v.join(','))), at = Object.fromEntries(c.items.map(it => [`${it[0]},${it[1]}`, it]));
  let h = `<div class="igrid fluid" data-inv="${c.id}" style="grid-template-columns:repeat(${c.w},minmax(0,1fr))">`;
  for (let y = 0; y < c.h; y++) for (let x = 0; x < c.w; x++) {
    const k = `${x},${y}`, it = at[k];
    let inner = '', title = `${x + 1}·${y + 1}`;
    if (it) {
      const [, , id, a, m, kind, rid, grp, dmg] = it;
      if (kind === 'Technology') {
        const bar = m > 0 && a >= 0 ? `<i class="chg" style="width:${Math.max(0, Math.min(100, a / m * 100))}%"></i>` : '';
        inner = itemIcon(id, rid, (realId(id, rid) ? bar : '') + (dmg ? '<b class="dmgi">!</b>' : ''));
        title += ` — ${itemName(id, rid)}${m > 0 && a >= 0 ? ` (${fmtN(a)}/${fmtN(m)})` : ''}${dmg ? '\n' + rawT('grid.damaged') : ''}`;
      } else {
        inner = itemIcon(id, rid, a > 1 ? `<b class="qty">${a > 9999 ? fmtN(a) : a}</b>` : '');
        title += ` — ${itemName(id, rid)} ×${fmtN(a)} (${rawT('inv.limit_tip', { mx: fmtN(m) })})`;
      }
      const cat = catText(grp, kind);
      if (cat) title += `\n${cat}`;
    }
    h += `<span class="c ${valid.has(k) ? '' : 'locked'} ${it ? 'has' : ''} ${it && it[8] ? 'dmg' : ''} ${it && S.invSel?.[c.id]?.has(k) ? 'sel' : ''}" data-cell="${k}" ${it ? `draggable="true" data-k="${esc(itemKeys(it[2], it[6]))}" data-id="${esc(it[2])}"` : ''} title="${esc(title)}">${inner}</span>`;
  }
  return h + '</div>';
}
function applyInvSearch() {
  const q = fold(S.invQ || '').trim(), out = $('#inv-found');
  document.querySelectorAll('.invbox .c.has').forEach(c => { const hit = q && c.dataset.k.includes(q);
    c.classList.toggle('hit', !!hit); c.classList.toggle('dim', !!q && !hit); });
  if (!out) return;
  if (!q) { out.innerHTML = ''; return; }
  const tot = new Map();
  for (const c of S.inv.data.containers) for (const it of c.items) {
    if (!itemKeys(it[2], it[6]).includes(q)) continue;
    const b = realId(it[2], it[6]) || '✦';
    const e = tot.get(b) || { id: it[2], rid: it[6], total: 0, where: new Map() };
    e.total += it[3]; e.where.set(c.id, (e.where.get(c.id) || 0) + it[3]); tot.set(b, e);
  }
  if (!tot.size) { out.innerHTML = `<p class="muted small">${t('inv.found_none', { q: S.invQ })}</p>`; return; }
  const byId = Object.fromEntries(S.inv.data.containers.map(c => [c.id, c]));
  const rows = [...tot.values()].sort((a, b) => b.total - a.total);
  out.innerHTML = `<ul class="found">${rows.slice(0, 12).map(e => `<li>${t('inv.found', { name: itemName(e.id, e.rid), total: fmtN(e.total),
    where: [...e.where].map(([cid, n]) => `${invLabel(byId[cid])} ×${fmtN(n)}`).join(' · ') })}</li>`).join('')}</ul>
    ${rows.length > 12 ? `<p class="muted small">${t('inv.found_more', { n: rows.length - 12 })}</p>` : ''}`;
  document.querySelectorAll('.invbox').forEach(b => { if (b.querySelector('.c.hit')) b.open = true; });
}

/* «Ordenar por…»: criterio y sentido; se recuerda la última elección */
function sortDialog(cid) {
  const p = S.sortPref || (S.sortPref = { key: 'name', dir: 'asc' });
  const c = S.inv.data.containers.find(x => x.id === cid);
  showModal(t('inv.sort_title', { inv: invLabel(c) }), `<div class="stack">
      <label>${t('inv.sort_by')}<select id="s-key">${['name', 'rarity', 'value'].map(k => `<option value="${k}" ${p.key === k ? 'selected' : ''}>${t('inv.sort.' + k)}</option>`).join('')}</select></label>
      <label>${t('inv.sort_dir')}<select id="s-dir">${['asc', 'desc'].map(k => `<option value="${k}" ${p.dir === k ? 'selected' : ''}>${t('inv.dir.' + k)}</option>`).join('')}</select></label>
      <p class="muted small" id="s-help"></p></div>`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('common.preview'), primary: true, onClick: () => {
      S.sortPref = { key: $('#s-key').value, dir: $('#s-dir').value };
      previewAndApply('inv', { op: 'sort', inv: cid, ...S.sortPref }, reloadSession); } }]);
  const help = () => { $('#s-help').textContent = rawT('inv.sort_help.' + $('#s-key').value + '.' + $('#s-dir').value); };
  $('#s-key').onchange = help; $('#s-dir').onchange = help; help();
}

/* «Mover aquí todos los…» (contenedores, almacén de la corbeta, carguero y exovehículos): por tipo y, si se quiere, objeto por objeto */
function gatherItems(cid, scope, grp) {
  const kinds = INV_SCOPES[scope], rows = new Map();
  for (const c of S.inv.data.containers) {
    if (c.id === cid || !kinds.includes(c.kind)) continue;
    for (const it of c.items) {
      const [, , id, a, , , rid, g] = it;
      if (!g || !rid || (grp !== 'any' && g !== grp)) continue;
      const r = rows.get(rid) || { rid, id, g, units: 0, stacks: 0, value: (S.inv.data.meta[rid] || [0])[0] };
      r.units += a; r.stacks += 1; rows.set(rid, r);
    }
  }
  return [...rows.values()].sort((a, b) => b.value - a.value || itemName(a.id, a.rid).localeCompare(itemName(b.id, b.rid)));
}
function gatherCounts(cid, scope) {
  const n = {};
  for (const r of gatherItems(cid, scope, 'any')) n[r.g] = (n[r.g] || 0) + 1;     // objetos distintos, como la lista
  return n;
}
function gatherDialog(cid) {
  const c = S.inv.data.containers.find(x => x.id === cid);
  const p = S.gatherPref || (S.gatherPref = { group: 'gases', from: 'storage' });
  let rows = [], sel = new Set();
  showModal(t('inv.gather_title', { inv: invLabel(c) }), `<div class="stack gather">
      <label>${t('inv.gather_what')}<select id="g-grp"></select></label>
      <label>${t('inv.gather_from')}<select id="g-from">${Object.keys(INV_SCOPES).map(k => `<option value="${k}" ${p.from === k ? 'selected' : ''}>${t('inv.scope.' + k)}</option>`).join('')}</select></label>
      <div class="gpick"><div class="row between"><input id="g-q" type="search" autocomplete="off" placeholder="${esc(rawT('inv.gather_search'))}">
        <span class="row"><button class="btn small" id="g-all">${t('inv.pick_all')}</button><button class="btn small" id="g-none">${t('inv.pick_none')}</button></span></div>
        <div id="g-list" class="glist"></div><p class="muted small" id="g-count"></p></div>
      <p class="muted small">${t('inv.gather_note')}</p></div>`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('common.preview'), primary: true, onClick: () => {
      if (!sel.size) return;
      S.gatherPref = { group: $('#g-grp').value, from: $('#g-from').value };
      const all = rows.length === sel.size && $('#g-grp').value !== 'any';
      previewAndApply('inv', { op: 'gather', inv: cid, ...S.gatherPref, ...(all ? {} : { ids: [...sel] }) }, reloadSession); } }], true);
  const okBtn = () => document.querySelector('#modal-actions button.primary');
  const visible = () => { const q = fold($('#g-q').value).trim();
    return rows.filter(r => !q || itemKeys(r.id, r.rid).includes(q)); };
  const drawList = () => {
    const vis = visible();
    $('#g-list').innerHTML = vis.length ? vis.map(r => `<label class="grow"><input type="checkbox" data-rid="${esc(r.rid)}" ${sel.has(r.rid) ? 'checked' : ''}>
        <span class="gico">${itemIcon(r.id, r.rid)}</span><span class="gname">${esc(itemName(r.id, r.rid))}</span>
        <span class="muted small">${t('inv.pick_line', { units: fmtN(r.units), stacks: r.stacks, value: fmtN(Math.round(r.value)) })}</span></label>`).join('')
      : `<p class="muted small">${t('inv.pick_empty')}</p>`;
    $('#g-list').querySelectorAll('input[data-rid]').forEach(cb => cb.onchange = () => {
      if (cb.checked) sel.add(cb.dataset.rid); else sel.delete(cb.dataset.rid); count(); });
    count();
  };
  const count = () => {
    $('#g-count').textContent = rawT('inv.pick_count', { n: sel.size, of: rows.length });
    if (okBtn()) okBtn().disabled = !sel.size;
  };
  const load = () => {
    const grp = $('#g-grp').value;
    rows = gatherItems(cid, $('#g-from').value, grp);
    sel = new Set(grp === 'any' ? [] : rows.map(r => r.rid));     // por tipo: todo marcado; «cualquier objeto»: lo eliges tú
    drawList();
  };
  const fill = () => {
    const n = gatherCounts(cid, $('#g-from').value), cur = $('#g-grp').value || p.group;
    $('#g-grp').innerHTML = `<option value="any" ${cur === 'any' ? 'selected' : ''}>${t('inv.grp.any')}</option>` +
      INV_GROUPS.map(g => `<option value="${g}" ${g === cur ? 'selected' : ''} ${n[g] ? '' : 'disabled'}>${t('inv.grp.' + g)} (${n[g] || 0})</option>`).join('');
    if ($('#g-grp').selectedOptions[0]?.disabled) { const first = INV_GROUPS.find(g => n[g]); $('#g-grp').value = first || 'any'; }
    load();
  };
  $('#g-from').onchange = fill; $('#g-grp').onchange = load;
  $('#g-q').oninput = drawList;
  $('#g-all').onclick = () => { visible().forEach(r => sel.add(r.rid)); drawList(); };
  $('#g-none').onclick = () => { visible().forEach(r => sel.delete(r.rid)); drawList(); };
  fill();
}

/* selección (clic) y menú del objeto (clic derecho) */
const selCount = cid => (S.invSel && S.invSel[cid] ? S.invSel[cid].size : 0);
function refreshSelUi(cid) {
  const b = document.querySelector(`[data-movesel="${cid}"]`), n = selCount(cid);
  if (b) { b.disabled = !n; b.textContent = t('inv.movesel') + (n ? ` (${n})` : ''); }
}
function clearSelection() {
  S.invSel = {};
  document.querySelectorAll('.invbox .c.sel').forEach(c => c.classList.remove('sel'));
  document.querySelectorAll('[data-movesel]').forEach(b => refreshSelUi(b.dataset.movesel));
}
function moveSelDialog(cid) {
  const src = S.inv.data.containers.find(x => x.id === cid), cells = [...(S.invSel[cid] || [])].map(k => k.split(',').map(Number));
  const opts = S.inv.data.containers.filter(c => c.id !== cid && !TECH_KINDS.has(c.kind)).map(c => {
    const free = c.valid.length - c.items.length;
    return `<option value="${c.id}" ${S.moveSelPref === c.id ? 'selected' : ''}>${esc(invLabel(c))} — ${t('inv.free_cells', { n: free })}</option>`; }).join('');
  showModal(t('inv.movesel_title', { n: cells.length, inv: invLabel(src) }), `<div class="stack">
      <label>${t('inv.movesel_to')}<select id="ms-dst">${opts}</select></label>
      <p class="muted small">${t('inv.movesel_note')}</p></div>`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('common.preview'), primary: true, onClick: () => {
      S.moveSelPref = $('#ms-dst').value;
      previewAndApply('inv', { op: 'move_many', src: cid, cells, dst: $('#ms-dst').value }, reloadSession); } }]);
}
function closeCtx() { const m = $('#ctxmenu'); if (m) m.remove(); }
function shipContainer(g) {
  const i = +g.dataset.ship, sh = newest(S.sess).ships.find(s => s.index === i), I = sh && sh.info;
  if (!I) return null;
  if (g.dataset.grid === 'tech')
    return { id: 'shiptech' + i, kind: 'shiptech', blocked: I.kind === 'corvette', valid: I.Inventory_TechOnly.valid_cells,
             items: I.tech_items.map(([x, y, id, a, m, , rid, dmg]) => [x, y, id, a, m, 'Technology', rid, null, dmg]) };
  return { id: 'ship' + i, kind: 'ship', blocked: false, valid: I.Inventory.valid_cells, items: I.cargo_items };
}
function itemMenu(ev, cell) {
  ev.preventDefault(); closeCtx();
  const g = cell.closest('.igrid'), cid = cellTarget(g);
  const c = g.dataset.inv ? S.inv.data.containers.find(x => x.id === cid) : shipContainer(g);
  if (!c) return;
  const [x, y] = cell.dataset.cell.split(',').map(Number), it = c.items.find(i => i[0] === x && i[1] === y);
  if (!it) return;
  const [, , id, a, m, kind, rid] = it, movable = kind === 'Substance' || kind === 'Product';
  const free = c.valid.length - c.items.length;
  if (TECH_KINDS.has(c.kind) && !movable) {                       // installed technology: only uninstall
    const menu = document.createElement('div');
    menu.id = 'ctxmenu'; menu.className = 'ctxmenu';
    const dmg = !!it[8];
    menu.innerHTML = `<div class="ctxhead">${esc(itemName(id, rid))}${dmg ? ` <b class="dmgtag">${t('grid.damaged_short')}</b>` : ''}</div>
      ${dmg ? `<button data-op="repair">🔧 ${t('inv.op.repair')}</button>` : ''}
      <button data-op="delete" class="danger" ${c.blocked ? 'disabled' : ''}>${t('inv.op.uninstall')}</button>
      <div class="muted small ctxnote">${t('inv.op.uninstall_note')}</div>`;
    document.body.appendChild(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.min(ev.clientX, innerWidth - r.width - 8) + 'px';
    menu.style.top = Math.min(ev.clientY, innerHeight - r.height - 8) + 'px';
    menu.querySelectorAll('[data-op]').forEach(b => b.onclick = () => { closeCtx();
      const op = b.dataset.op;
      previewAndApply('inv', { op, inv: cid, cell: [x, y] }, reloadSession); });
    return;
  }
  const opts = [
    ['split', t('inv.op.split'), movable && a > 1 && free > 0],
    ['fill', t('inv.op.fill', { mx: fmtN(m) }), movable && a < m],
    ['dup', t('inv.op.dup'), movable && free > 0],
    ['delete', t('inv.op.delete'), movable]];
  const menu = document.createElement('div');
  menu.id = 'ctxmenu'; menu.className = 'ctxmenu';
  menu.innerHTML = `<div class="ctxhead">${esc(itemName(id, rid))} ×${fmtN(a)}</div>` +
    opts.map(([op, label, ok]) => `<button data-op="${op}" ${ok ? '' : 'disabled'} class="${op === 'delete' ? 'danger' : ''}">${label}</button>`).join('') +
    (movable ? '' : `<div class="muted small ctxnote">${t('inv.op.tech_note')}</div>`);
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.min(ev.clientX, innerWidth - r.width - 8) + 'px';
  menu.style.top = Math.min(ev.clientY, innerHeight - r.height - 8) + 'px';
  const base = { inv: cid, cell: [x, y] };
  menu.querySelectorAll('button[data-op]').forEach(b => b.onclick = () => {
    closeCtx();
    const op = b.dataset.op, name = itemName(id, rid);
    if (op === 'fill' || op === 'dup') return quickStage('inv', { op, ...base });
    if (op === 'delete') return previewAndApply('inv', { op: 'delete', ...base }, reloadSession);
    showModal(t('inv.split_title', { item: name }), `<div class="stack">
        <label>${t('inv.split_n', { a: fmtN(a) })}<input id="sp-n" type="number" min="1" max="${a - 1}" value="${Math.floor(a / 2)}"></label>
        <p class="muted small" id="sp-help"></p></div>`, [
      { label: t('common.cancel'), onClick: closeModal },
      { label: t('inv.split_ok'), primary: true, onClick: () => { const n = +$('#sp-n').value;
        if (!(n >= 1 && n < a)) return; closeModal(); quickStage('inv', { op: 'split', ...base, n }); } }]);
    const help = () => { const n = +$('#sp-n').value;
      $('#sp-help').textContent = n >= 1 && n < a ? rawT('inv.split_help', { n: fmtN(n), left: fmtN(a - n) }) : rawT('inv.split_range', { mx: fmtN(a - 1) }); };
    $('#sp-n').oninput = help; help(); $('#sp-n').select();
  });
}
/* clic derecho en una celda vacía (Inventarios y panel de naves): agregar un objeto nuevo */
function cellTarget(g) {
  if (g.dataset.inv) return g.dataset.inv;
  return (g.dataset.grid === 'tech' ? 'shiptech' : 'ship') + g.dataset.ship;
}
function emptyMenu(ev, cell) {
  ev.preventDefault(); closeCtx();
  const g = cell.closest('.igrid'), cid = cellTarget(g), [x, y] = cell.dataset.cell.split(',').map(Number);
  const menu = document.createElement('div');
  menu.id = 'ctxmenu'; menu.className = 'ctxmenu';
  menu.innerHTML = `<div class="ctxhead">${t('inv.cell_at', { at: `${x + 1}·${y + 1}` })}</div><button data-op="add">${t('inv.op.add')}</button>`;
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.min(ev.clientX, innerWidth - r.width - 8) + 'px';
  menu.style.top = Math.min(ev.clientY, innerHeight - r.height - 8) + 'px';
  const kindOf = (S.inv && S.inv.data.containers.find(c => c.id === cid) || {}).kind;
  menu.querySelector('[data-op="add"]').onclick = () => { closeCtx(); addDialog(cid, [x, y], g.dataset.grid === 'tech' || TECH_KINDS.has(kindOf)); };
}
async function addDialog(cid, cell, tech) {
  const key = `${cid}:${S.sess.changes.length}:${S.sess.dirty}`;
  S.addCache = S.addCache || {};
  if (!S.addCache[key]) {
    try { S.addCache[key] = await api('/api/session/addable', { inv: cid }); } catch (e) { return toast(errText(e), true); }
  }
  const data = S.addCache[key], KIND = { substance: 'Substance', product: 'Product', tech: 'Technology', proc: 'Technology' };
  const rows = data.items.map(([rid, kind, mx]) => ({ rid, kind, mx, name: itemName(rid, rid), keys: itemKeys(rid, rid) }))
    .sort((a, b) => a.name.localeCompare(b.name));
  let pick = null;
  showModal(t(tech ? 'inv.add_title_tech' : 'inv.add_title', { at: `${cell[0] + 1}·${cell[1] + 1}` }), `<div class="stack gather">
      <p class="muted small">${t(data.kind === 'shiptech' ? 'inv.add_rules_tech' : TECH_KINDS.has(data.kind) ? 'inv.add_rules_grid.' + data.kind : 'inv.add_rules_cargo')}</p>
      ${rows.length ? `<div class="gpick"><input id="a-q" type="search" autocomplete="off" placeholder="${esc(rawT('inv.gather_search'))}">
        <div id="a-list" class="glist"></div><p class="muted small" id="a-count"></p></div>
      <label id="a-nwrap" hidden>${t('inv.add_amount')}<input id="a-n" type="number" min="1" value="1"></label>
      <p class="msg warn"><span>${t('inv.add_note')}</span></p>` : `<p class="msg block"><span>${t('inv.add_none')}</span></p>`}</div>`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('inv.add_ok'), primary: true, disabled: true, onClick: () => {
      if (!pick) return;
      const n = +($('#a-n') ? $('#a-n').value : 1);
      if (!tech && !(n >= 1 && n <= pick.mx)) return;
      closeModal(); quickStage('inv', { op: 'add', inv: cid, cell, id: pick.rid, ...(tech ? {} : { n }) }); } }], true);
  if (!rows.length) return;
  const ok = () => document.querySelector('#modal-actions button.primary');
  const draw = () => {
    const q = fold($('#a-q').value).trim(), vis = rows.filter(r => !q || r.keys.includes(q)), shown = vis.slice(0, 80);
    $('#a-list').innerHTML = shown.map(r => `<label class="grow"><input type="radio" name="a-pick" value="${esc(r.rid)}" ${pick && pick.rid === r.rid ? 'checked' : ''}>
        <span class="gico">${itemIcon(r.rid, r.rid)}</span><span class="gname">${esc(r.name)}</span>
        <span class="muted small">${t('inv.kind.' + KIND[r.kind])}${tech ? '' : ' · ' + t('inv.limit_tip', { mx: fmtN(r.mx) })}</span></label>`).join('')
      || `<p class="muted small">${t('inv.pick_empty')}</p>`;
    $('#a-count').textContent = rawT(vis.length > shown.length ? 'inv.add_count_more' : 'inv.add_count', { n: vis.length, shown: shown.length });
    $('#a-list').querySelectorAll('input[name="a-pick"]').forEach(rb => rb.onchange = () => {
      pick = rows.find(r => r.rid === rb.value); ok().disabled = false;
      if (!tech) { $('#a-nwrap').hidden = false; $('#a-n').max = pick.mx; $('#a-n').value = pick.mx; }
    });
  };
  $('#a-q').oninput = draw; draw(); $('#a-q').focus();
}
document.addEventListener('contextmenu', e => {
  const c = e.target.closest('.igrid .c');
  if (!c || c.classList.contains('has') || c.classList.contains('locked') || !S.sess) return;
  emptyMenu(e, c);
});
document.addEventListener('click', e => { if (!e.target.closest('#ctxmenu')) closeCtx(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeCtx(); if (S.view === 'inventories' && $('#modal').hidden) clearSelection(); } });
window.addEventListener('scroll', closeCtx, true);

function bindInventories() {
  const q = $('#inv-q');
  q.oninput = () => { S.invQ = q.value; applyInvSearch(); };
  document.querySelectorAll('.invbox').forEach(b => b.addEventListener('toggle', () => {
    if (b.open) S.invOpen.add(b.dataset.inv); else S.invOpen.delete(b.dataset.inv); }));
  document.querySelectorAll('[data-sort]').forEach(b => b.onclick = () => sortDialog(b.dataset.sort));
  document.querySelectorAll('[data-gather]').forEach(b => b.onclick = () => gatherDialog(b.dataset.gather));
  document.querySelectorAll('[data-movesel]').forEach(b => b.onclick = () => moveSelDialog(b.dataset.movesel));
  document.querySelectorAll('.invbox .igrid .c.has').forEach(c => {
    if (c.closest('.invbox.tech')) { c.addEventListener('contextmenu', e => itemMenu(e, c)); return; }
    c.addEventListener('click', () => {
      const cid = c.closest('.igrid').dataset.inv, set = (S.invSel[cid] = S.invSel[cid] || new Set());
      if (set.has(c.dataset.cell)) set.delete(c.dataset.cell); else set.add(c.dataset.cell);
      c.classList.toggle('sel', set.has(c.dataset.cell)); refreshSelUi(cid);
    });
    c.addEventListener('contextmenu', e => itemMenu(e, c));
  });
  // lápiz: abre la barra para escribir el nombre (fuera del <summary> para que las teclas no plieguen la caja)
  document.querySelectorAll('[data-rename]').forEach(b => b.onclick = e => {
    e.preventDefault(); e.stopPropagation();
    const cid = b.dataset.rename, box = b.closest('.invbox'), bar = box.querySelector('[data-renamebar]'), inp = bar.querySelector('input');
    const c = S.inv.data.containers.find(x => x.id === cid);
    box.open = true; bar.hidden = false; inp.value = c.name && c.name !== 'BLD_STORAGE_NAME' ? c.name : ''; inp.focus(); inp.select();
  });
  const doRename = async cid => {
    const bar = document.querySelector(`[data-renamebar="${cid}"]`), name = bar.querySelector('input').value;
    bar.hidden = true;
    await quickStage('inv', { op: 'rename', inv: cid, name });
  };
  document.querySelectorAll('[data-rename-ok]').forEach(b => b.onclick = () => doRename(b.dataset.renameOk));
  document.querySelectorAll('[data-rename-cancel]').forEach(b => b.onclick = () => { b.closest('[data-renamebar]').hidden = true; });
  document.querySelectorAll('[data-renamebar] input').forEach(inp => inp.addEventListener('keydown', e => {
    const cid = inp.closest('[data-renamebar]').dataset.renamebar;
    if (e.key === 'Enter') { e.preventDefault(); doRename(cid); }
    else if (e.key === 'Escape') { e.preventDefault(); inp.closest('[data-renamebar]').hidden = true; }
  }));
  let from = null;
  document.querySelectorAll('.invbox .igrid .c[draggable=true]').forEach(c => {
    c.addEventListener('dragstart', e => { from = { inv: c.closest('.igrid').dataset.inv, cell: c.dataset.cell, id: c.dataset.id };
      c.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', 'cell'); });
    c.addEventListener('dragend', () => { c.classList.remove('dragging'); });
  });
  document.querySelectorAll('.invbox .igrid .c:not(.locked)').forEach(c => {
    c.addEventListener('dragover', e => { if (from) { e.preventDefault(); c.classList.add('over'); } });
    c.addEventListener('dragleave', () => c.classList.remove('over'));
    c.addEventListener('drop', async e => {
      e.preventDefault(); c.classList.remove('over');
      const g = c.closest('.igrid'), f = from; from = null;
      if (!f || (f.inv === g.dataset.inv && f.cell === c.dataset.cell)) return;
      // vista inmediata solo para mover/intercambiar en el mismo inventario; juntar pilas o pasar a otro lo confirma el servidor
      if (f.inv === g.dataset.inv && c.dataset.id !== f.id) swapCells(g, f.cell, c.dataset.cell);
      else c.classList.add('pending');
      await quickStage('inv', { op: 'move', src: f.inv, from: f.cell.split(',').map(Number), dst: g.dataset.inv, to: c.dataset.cell.split(',').map(Number) });
    });
  });
}

/* -- Misiones (fase 4): listar, reiniciar, borrar; cambiar etapa (experimental) -- */
const MIS_GROUPS = ['story', 'atlas', 'tutorial', 'base', 'base_npc', 'water', 'expedition', 'nexus', 'settlement',
  'procedural', 'fleet', 'pirate', 'corvette', 'multiplayer', 'stats', 'swarm', 'items', 'catalogue', 'other', 'old', 'unknown'];
async function viewMissions() {
  const key = `${S.sess.slot}:${S.sess.changes.length}:${S.sess.dirty}`;
  if (!S.mis || S.mis.key !== key) {
    const prev = S.mis || {};
    if (!S.mis) $('#main').innerHTML = `<h1>${t('mis.title')}</h1><p class="muted">${t('inv.loading')}</p>`;
    let data;
    try { data = await api('/api/session/missions', null, { quiet: true }); }
    catch (e) { $('#main').innerHTML = `<p class="msg block"><span>${esc(errText(e))}</span></p>`; return; }
    S.mis = { key, data, sel: new Set(), q: prev.q || '', group: prev.group || '', st: prev.st || '', dup: !!prev.dup,
      focus: prev.focus ?? null };
    if (S.view !== 'missions') return;
  }
  const d = S.mis.data, main = $('#main'), top = main.scrollTop;
  const cur = d.missions.find(m => m.current), prev = d.missions.find(m => m.previous);
  const counts = {}; d.missions.forEach(m => { counts[m.group] = (counts[m.group] || 0) + 1; });
  main.innerHTML = `<h1>${t('mis.title')}</h1><p class="help">${t('mis.help')}</p>
    <div class="row misinfo">
      <span>${t('mis.current')}: <b>${esc(cur ? (cur.title || cur.id) : (d.current || '—'))}</b> <code>${esc(d.current || '')}</code></span>
      <span class="muted">${t('mis.previous')}: ${esc(prev ? (prev.title || prev.id) : (d.previous || '—'))}</span>
      <span class="muted">${t('mis.total', { n: d.missions.length })}</span></div>
    <div class="row misbar">
      <input id="mis-q" type="search" placeholder="${esc(rawT('mis.search'))}" value="${esc(S.mis.q)}">
      <select id="mis-g"><option value="">${t('mis.all_groups')}</option>${MIS_GROUPS.filter(g => counts[g]).map(g =>
        `<option value="${g}" ${S.mis.group === g ? 'selected' : ''}>${t('mis.g.' + g)} (${counts[g]})</option>`).join('')}</select>
      <select id="mis-s"><option value="">${t('mis.all_states')}</option>${['stage', 'inactive', 'mark'].map(x =>
        `<option value="${x}" ${S.mis.st === x ? 'selected' : ''}>${t('mis.state.' + x)}</option>`).join('')}</select>
      <label class="inline"><input type="checkbox" id="mis-dup" ${S.mis.dup ? 'checked' : ''}> ${t('mis.only_dup')}</label>
    </div>
    <div class="row misacts"><span id="mis-count" class="muted small"></span>
      <button class="btn small" id="mis-reset">${t('mis.reset')}</button>
      <button class="btn small" id="mis-del">${t('mis.delete')}</button>
      <button class="btn small" id="mis-stage">${t('mis.stage')}</button>
      <button class="btn small" id="mis-clear">${t('mis.clear')}</button></div>
    <div class="miswrap"><div class="card mistable"><table><thead><tr><th><input type="checkbox" id="mis-all"></th><th>${t('mis.col.mission')}</th><th>${t('mis.col.group')}</th><th>${t('mis.col.state')}</th><th>${t('mis.col.flags')}</th></tr></thead>
      <tbody id="mis-body"></tbody></table></div><div class="card misside" id="mis-side"></div></div>`;
  main.scrollTop = top;
  misDraw();
  misSide();
  const re = () => { S.mis.q = $('#mis-q').value; S.mis.group = $('#mis-g').value; S.mis.st = $('#mis-s').value; S.mis.dup = $('#mis-dup').checked; misDraw(); };
  $('#mis-q').oninput = re; $('#mis-g').onchange = re; $('#mis-s').onchange = re; $('#mis-dup').onchange = re;
  $('#mis-all').onchange = () => { misVisible().forEach(m => $('#mis-all').checked ? S.mis.sel.add(m.i) : S.mis.sel.delete(m.i)); misDraw(); };
  $('#mis-clear').onclick = () => { S.mis.sel.clear(); misDraw(); };
  $('#mis-reset').onclick = misResetDialog;
  $('#mis-del').onclick = () => previewAndApply('mis', { op: 'delete', sel: misSel() }, reloadSession);
  $('#mis-stage').onclick = misStageDialog;
}
function misVisible() {
  const q = fold(S.mis.q).trim();
  return S.mis.data.missions.filter(m => (!S.mis.group || m.group === S.mis.group) && (!S.mis.st || m.state === S.mis.st)
    && (!S.mis.dup || m.dup) && (!q || fold(`${m.title} ${m.sub} ${m.id}`).includes(q)));
}
const misSel = () => S.mis.data.missions.filter(m => S.mis.sel.has(m.i)).map(m => [m.id, m.copy]);
function misStateText(m) {
  if (m.state === 'inactive') return `<span class="pill gray">${t('mis.state.inactive')}</span>`;
  if (m.state === 'mark') return `<span class="pill violet" title="${esc(rawT('mis.mark_tip'))}">${t('mis.state.mark')}</span>`;
  // the game leaves a finished mission at the step where its branch ended, so "X of N" is not progress
  return `<span class="pill blue" title="${esc(rawT('mis.stage_tip'))}">${t('mis.stage_saved', { p: m.progress })}</span>`;
}
/* misiones distintas con el mismo título (p. ej. las etapas de un capítulo): juntas, bajo un encabezado */
function misGrouped(list) {
  const by = new Map();
  list.forEach(m => { const k = m.title ? fold(m.title) : '#' + m.i; if (!by.has(k)) by.set(k, []); by.get(k).push(m); });
  const out = [];
  by.forEach(ms => {
    const ids = new Set(ms.map(m => m.id));
    if (ids.size > 1) out.push({ head: ms[0].title, n: ids.size });
    const order = [...ids];
    ms.forEach(m => out.push(ids.size > 1 ? { ...m, part: order.indexOf(m.id) + 1, parts: ids.size } : m));
  });
  return out;
}
function misRow(m) {
  return `<tr class="${S.mis.sel.has(m.i) ? 'sel' : ''} ${m.part ? 'msub' : ''}" data-i="${m.i}">
      <td><input type="checkbox" ${S.mis.sel.has(m.i) ? 'checked' : ''}></td>
      <td><div class="mname">${m.part ? `<span class="muted">↳ ${t('mis.part', { k: m.part, n: m.parts })}</span>` : m.title ? esc(m.title) : `<code class="mid">${esc(m.id)}</code>`}</div>${m.title ? `<div class="muted small">${m.sub ? esc(m.sub) + ' · ' : ''}<code>${esc(m.id)}</code></div>` : ''}</td>
      <td class="small">${t('mis.g.' + m.group)}</td><td>${misStateText(m)}</td>
      <td class="small">${[m.current ? `<span class="pill orange">${t('mis.f.current')}</span>` : '', m.previous ? `<span class="pill gray">${t('mis.f.previous')}</span>` : '',
        m.crit ? `<span class="pill red" title="${esc(rawT('mis.crit_tip'))}">${t('mis.f.crit')}</span>` : '', m.dup ? `<span class="pill gray" title="${esc(rawT('mis.copy_tip'))}">${t('mis.f.copy', { n: m.copy })}</span>` : ''].join(' ')}</td></tr>`;
}
function misDraw() {
  const vis = misVisible(), shown = misGrouped(vis.slice(0, 400));
  $('#mis-body').innerHTML = shown.map(m => m.head
      ? `<tr class="mgroup"><td></td><td colspan="4"><b>${esc(m.head)}</b> <span class="muted small">· ${t('mis.same_title', { n: m.n })}</span></td></tr>`
      : misRow(m)).join('')
    + (vis.length > 400 ? `<tr><td colspan="5" class="muted small">${t('mis.more', { n: vis.length - 400 })}</td></tr>` : '')
    + (vis.length ? '' : `<tr><td colspan="5" class="muted small">${t('mis.none')}</td></tr>`);
  $('#mis-body').querySelectorAll('tr[data-i]').forEach(tr => tr.onclick = e => {
    const i = +tr.dataset.i; if (S.mis.sel.has(i)) S.mis.sel.delete(i); else S.mis.sel.add(i);
    tr.classList.toggle('sel', S.mis.sel.has(i)); tr.querySelector('input').checked = S.mis.sel.has(i); misCount();
    S.mis.focus = i; misSide(); });
  misCount();
}
async function misSide() {
  const box = $('#mis-side');
  if (!box) return;
  const m = S.mis.focus == null ? null : S.mis.data.missions.find(x => x.i === S.mis.focus);
  if (!m) { box.innerHTML = `<p class="muted">${t('mis.side_pick')}</p>`; return; }
  const head = `<h3 class="mside-t">${esc(m.title || m.id)}</h3>${m.sub ? `<p class="muted small">${esc(m.sub)}</p>` : ''}
    <p class="small"><code>${esc(m.id)}</code> · ${t('mis.g.' + m.group)}</p><p>${misStateText(m)}</p>`;
  box.innerHTML = head + `<p class="muted small">${t('inv.loading')}</p>`;
  let st;
  try { st = await api('/api/session/missions/steps', { id: m.id, progress: m.progress }, { quiet: true }); }
  catch (e) { box.innerHTML = head + `<p class="msg block"><span>${esc(errText(e))}</span></p>`; return; }
  if (S.mis.focus !== m.i) return;
  const block = (label, o) => o ? `<div class="mstep"><div class="mstep-l">${label}</div><div class="mstep-m">${esc(o.main)}</div>
      ${o.more.length ? `<ul>${o.more.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</div>` : '';
  let body;
  if (m.state === 'mark') body = `<p class="muted small">${t('mis.side_mark')}</p>`;
  else if (!st.now && !st.next) body = `<p class="muted small">${t('mis.side_none')}</p>`;
  else body = block(m.state === 'inactive' ? t('mis.side_start') : t('mis.side_now'), m.state === 'inactive' ? st.next : st.now)
    + (m.state === 'inactive' ? '' : block(t('mis.side_next'), st.next));
  const hasSteps = body.includes('class="mstep"');
  box.innerHTML = head + body + (hasSteps ? `<p class="muted small mside-note">${t('mis.side_note')}</p>` : '');
}
function misCount() {
  const n = S.mis.sel.size;
  $('#mis-count').textContent = rawT('mis.selected', { n });
  $('#mis-reset').disabled = $('#mis-del').disabled = $('#mis-clear').disabled = !n;
  $('#mis-stage').disabled = n !== 1;
}
function misResetDialog() {
  showModal(t('mis.reset_title', { n: S.mis.sel.size }), `<div class="stack"><p>${t('mis.reset_explain')}</p>
      <label class="inline"><input type="checkbox" id="mr-chain" checked> ${t('mis.reset_chain')}</label>
      <p class="muted small">${t('mis.reset_chain_help')}</p></div>`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('common.preview'), primary: true, onClick: () => previewAndApply('mis', { op: 'reset', sel: misSel(), chain: $('#mr-chain').checked }, reloadSession) }]);
}
function misStageDialog() {
  const m = S.mis.data.missions.find(x => S.mis.sel.has(x.i));
  if (!m) return;
  showModal(t('mis.stage_title', { name: m.title || m.id }), `<div class="stack">
      <div class="msg warn"><span>${t('mis.stage_warn')}</span></div>
      <p>${t('mis.stage_now', { p: m.progress, n: m.n ?? '?' })}</p>
      <label>${t('mis.stage_to', { n: m.n ?? 0 })}<input id="ms-to" type="number" min="0" max="${m.n ?? 0}" value="${Math.max(0, m.progress)}"></label></div>`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('common.preview'), primary: true, disabled: m.n == null, onClick: () => previewAndApply('mis', { op: 'stage', sel: [[m.id, m.copy]], to: +$('#ms-to').value }, reloadSession) }]);
}

/* -- Asentamientos (fase 5): ver, renombrar, quitar perks, descartar la decisión pendiente; asesor -- */
const SET_STATS = ['Happiness', 'Production', 'Upkeep', 'Sentinels', 'Debt', 'Alert', 'BugAttack'];
const fmtSigned = n => (n > 0 ? '+' : '') + fmtN(n);
async function viewSettlements() {
  const key = `${S.sess.slot}:${S.sess.changes.length}:${S.sess.dirty}`;
  if (!S.set || S.set.key !== key) {
    if (!S.set) $('#main').innerHTML = `<h1>${t('set.title')}</h1><p class="muted">${t('inv.loading')}</p>`;
    let data;
    try { data = await api('/api/session/settlements', null, { quiet: true }); }
    catch (e) { $('#main').innerHTML = `<p class="msg block"><span>${esc(errText(e))}</span></p>`; return; }
    S.set = { key, data, sel: {} };
    if (S.view !== 'settlements') return;
  }
  const d = S.set.data, main = $('#main'), top = main.scrollTop;
  let body;
  if (!d.available) body = `<p class="msg warn"><span>${t('set.no_data')}</span></p>`;
  else if (!d.owner_found) body = `<p class="msg warn"><span>${t('set.no_owner')}</span></p>`;
  else if (!d.settlements.length) body = `<p class="msg info"><span>${t('set.none')}</span></p>`;
  else body = d.settlements.map(setCard).join('');
  main.innerHTML = `<h1>${t('set.title')}</h1><p class="help">${t('set.help')}</p>${body}${d.available ? setRoute(d.route) : ''}`;
  main.scrollTop = top;
  setBind();
}
function setStat(x) {
  const pct = Math.round(x.norm * 100), cls = x.state === 'bad' ? 'bad' : x.state === 'good' ? 'good' : 'mid';
  const val = x.key === 'Debt' ? t('set.units', { n: fmtN(x.v) }) : fmtN(x.v);
  return `<div class="sstat"><span>${t('set.stat.' + x.key)}</span><span class="bar ${cls}" title="${esc(rawT('set.bar_tip', { min: fmtN(x.min), max: fmtN(x.max) }))}"><i style="width:${Math.max(2, pct)}%"></i></span>
    <b>${val}</b><span class="pill ${cls === 'bad' ? 'red' : cls === 'good' ? 'green' : 'gray'} small">${t('set.state.' + x.state)}</span></div>`;
}
function setPerkFx(fx) {
  return fx.map(([stat, strength, lo, hi]) => {
    const good = strength.startsWith('Positive'), range = lo == null ? '' : ` ${fmtSigned(lo)}…${fmtSigned(hi)}`;
    return `<span class="fx ${good ? 'pos' : 'neg'}">${good ? '▲' : '▼'} ${t('set.stat.' + stat)}${range}</span>`;
  }).join(' ');
}
/* los 48 lotes del asentamiento: edificio presente, mejora en curso (engranaje dorado) o aún no activado */
const LOT_ICON = { Settlement_Hub: '🏛', Settlement_LandingZone: '🛬', Settlement_Bar: '🍸', Settlement_Tower: '🗼', Settlement_Market: '🏪',
  Settlement_Small: '🏠', Settlement_SmallIndustrial: '🛠', Settlement_Medium: '🏘', Settlement_Large: '🏬', Settlement_Monument: '🗿',
  Settlement_SheriffsOffice: '🛡', Settlement_Double: '🏢', Settlement_Farm: '🌾', Settlement_Factory: '🏭', Settlement_Clump: '🏚',
  Settlement_Hub_Builders: '🛸', Settlement_Builders_RoboArm: '🦾' };
function lotGrid(st) {
  if (!st.lots) return '';
  const cells = st.lots.map(l => {
    const name = l.type ? rawT('bld.' + l.type) : rawT('lot.unknown_type');
    let tip, cls = 'lot', inner;
    if (l.built) {
      tip = `${rawT('lot.n', { n: l.i + 1 })} · ${name} · ${rawT('lot.class', { c: l.cls })}`;
      if (l.up) tip += l.ready ? ` · ${rawT('lot.ready', { to: l.up })}`
        : ` · ${rawT('lot.in_progress', { to: l.up })}${l.since ? ' ' + rawT('lot.since', { when: fmtDate(l.since) }) : ''}`;
      cls += ' on' + (l.up ? (l.ready ? ' ready' : ' up') : '') + (l.type ? '' : ' unk');
      inner = `<span class="li">${LOT_ICON[l.type] || '🏢'}</span><b class="lc c${l.cls}">${l.cls}</b>${l.up ? (l.ready ? '<span class="rdy">✔</span>' : '<span class="cog">⚙</span>') : ''}`;
    } else if (l.building) {
      tip = `${rawT('lot.n', { n: l.i + 1 })} · ${name} · ${rawT('lot.building')}${l.since ? ' ' + rawT('lot.since', { when: fmtDate(l.since) }) : ''}`;
      cls += ' on up build' + (l.type ? '' : ' unk');
      inner = `<span class="li">${LOT_ICON[l.type] || '🏗'}</span><span class="cog">⚙</span>`;
    } else {
      tip = `${rawT('lot.n', { n: l.i + 1 })} · ${rawT('lot.not_active')}${l.offered && l.type ? ' · ' + rawT('lot.offered', { name }) : ''}`;
      cls += ' off'; inner = '<span class="li">⬚</span>';
    }
    return `<div class="${cls}" title="${esc(tip)}">${inner}</div>`;
  }).join('');
  const n = st.lots.filter(l => l.built).length, up = st.lots.filter(l => (l.up && !l.ready) || l.building).length, rdy = st.lots.filter(l => l.ready).length, known = st.lots.filter(l => l.built && l.type).length;
  return `<div class="lotgrid">${cells}</div><p class="muted small">${t('lot.legend', { n, up, rdy, known })}</p>`;
}
function setCard(st) {
  const sel = S.set.sel[st.uid] || new Set();
  const stats = st.stats.filter(x => SET_STATS.includes(x.key));
  const pend = st.pending ? `<div class="msg warn setpend"><span><b>${esc(st.pending.title || st.pending.type)}</b>
      ${st.pending.sub ? `<br>${esc(st.pending.sub)}` : ''}${st.pending.objective ? `<br><span class="muted small">${esc(st.pending.objective)}</span>` : ''}
      <br><span class="small">${t('set.pending_help')}</span>
      <br><button class="btn small" data-skip="${esc(st.uid)}">${t('set.skip')}</button></span></div>`
    : `<p class="muted small">${t('set.no_pending')}</p>`;
  const build = st.building ? `<p class="small">🏗 ${t('set.building', { name: esc(st.building.name || st.building.cls) })}</p>` : '';
  const prod = st.production.length ? `<ul class="setprod">${st.production.map(p => `<li><span class="gico">${itemIcon(p.id, p.id)}</span>
      <span>${esc(itemName(p.id, p.id))}</span><span class="muted small">${t('set.cap', { n: fmtN(p.cap || 0) })}</span></li>`).join('')}</ul>
      <p class="muted small">${t('set.prod_note')}</p>` : `<p class="muted small">${t('set.no_prod')}</p>`;
  const perks = st.perks.map(p => `<tr class="${sel.has(p.i) ? 'sel' : ''}" data-perk="${p.i}">
      <td><input type="checkbox" ${sel.has(p.i) ? 'checked' : ''}></td>
      <td><div>${p.name ? esc(p.name) : `<span class="muted">${t('set.perk_generated')}</span>`}</div>${p.desc ? `<div class="muted small">${esc(p.desc)}</div>` : ''}</td>
      <td><span class="pill ${p.neg ? 'red' : p.neg === false ? 'green' : 'gray'} small">${t(p.neg ? 'set.neg' : p.neg === false ? 'set.pos' : 'set.unknown')}</span>
        <span class="muted small">${t('set.kind.' + p.kind)}</span></td><td class="small">${setPerkFx(p.fx)}</td></tr>`).join('');
  const adv = st.advice.map(a => `<li class="${a.level}">${t('adv.' + a.code.slice(4), shapeParams({ ...a.p,
    ...(a.p.debt != null ? { debt: fmtN(a.p.debt) } : {}), ...(a.p.prod != null ? { prod: fmtN(a.p.prod) } : {}),
    ...(a.p.cost != null ? { cost: fmtN(a.p.cost) } : {}), ...(a.p.v != null ? { v: fmtN(a.p.v) } : {}),
    ...(a.p.max != null ? { max: fmtN(a.p.max) } : {}) }))}</li>`).join('');
  return `<div class="card setcard" data-uid="${esc(st.uid)}">
    <div class="row between sethead"><span class="ttl"><b>${esc(st.name || t('set.unnamed'))}</b>
        <button class="pencil" data-setrename="${esc(st.uid)}" title="${esc(rawT('set.rename'))}" aria-label="${esc(rawT('set.rename'))}">✎</button></span>
      <span class="row small"><span class="pill blue">${t('set.race.' + st.race)}</span>
        <span>${t('set.population', { n: st.population ?? '—', max: S.set.data.max_population ?? '—' })}</span>
        ${st.claimed ? `<span class="muted">${t('set.claimed', { when: fmtDate(st.claimed) })}</span>` : ''}
        ${st.judgements_settled != null ? `<span class="muted">${t('set.settled', { n: st.judgements_settled })}</span>` : ''}</span></div>
    <div class="renamebar" data-setrenamebar="${esc(st.uid)}" hidden><input maxlength="40" value="${esc(st.name)}">
      <button class="btn small primary" data-setrename-ok="${esc(st.uid)}">${t('inv.rename_ok')}</button><button class="btn small" data-setrename-cancel="${esc(st.uid)}">${t('common.cancel')}</button></div>
    <div class="setgrid">
      <div><h3>${t('set.stats')}</h3>${stats.map(setStat).join('')}<p class="muted small">${t('set.stats_note')}</p>
        <h3>${t('set.pending')}</h3>${pend}${build}<h3>${t('set.production')}</h3>${prod}</div>
      <div><h3>${t('lot.title')}</h3>${lotGrid(st)}<h3>${t('set.advisor')}</h3><ul class="advice">${adv}</ul></div>
    </div>
    <details class="setperks" ${sel.size ? 'open' : ''}><summary>${t('set.perks', { n: st.perks.length, max: S.set.data.max_perks ?? '—' })}</summary>
      <table><thead><tr><th></th><th>${t('set.perk')}</th><th>${t('set.perk_kind')}</th><th>${t('set.perk_fx')}</th></tr></thead><tbody>${perks}</tbody></table>
      <div class="row end"><span class="muted small">${t('set.perks_help')}</span>
        <button class="btn small danger" data-perkdel="${esc(st.uid)}" ${sel.size ? '' : 'disabled'}>${t('set.perks_remove', { n: sel.size })}</button></div></details>
  </div>`;
}
function setRoute(route) {
  const pill = r => r.state === 'stage' ? `<span class="pill blue" title="${esc(rawT('mis.stage_tip'))}">${t('mis.stage_saved', { p: r.progress })}</span>`
    : `<span class="pill ${r.state === 'end' ? 'green' : 'gray'}">${t('set.route.' + r.state)}</span>`;
  const step = o => o ? `<div class="small">${esc(o.main)}</div>` : '';
  return `<h2>${t('set.routes')}</h2><p class="help">${t('set.routes_help')}</p><div class="card"><table class="setroute"><tbody>
    ${route.map(r => `<tr><td><div>${esc(r.title)}${r.current ? ` <span class="pill orange small">${t('mis.f.current')}</span>` : ''}</div>
        <div class="muted small">${r.sub ? esc(r.sub) + ' · ' : ''}<code>${esc(r.id)}</code></div>
        ${r.reward ? `<div class="small">🎁 ${t('set.reward.' + r.reward)}</div>` : ''}</td>
      <td>${pill(r)}</td><td>${r.steps ? (r.steps.now ? `<div class="muted small">${t('mis.side_now')}</div>${step(r.steps.now)}` : '')
        + (r.steps.next ? `<div class="muted small">${t('mis.side_next')}</div>${step(r.steps.next)}` : '') : ''}</td></tr>`).join('')}
    </tbody></table><p class="muted small">${t('set.routes_note')}</p></div>`;
}
function setBind() {
  document.querySelectorAll('tr[data-perk]').forEach(tr => tr.onclick = () => {
    const uid = tr.closest('[data-uid]').dataset.uid, i = +tr.dataset.perk, set = (S.set.sel[uid] = S.set.sel[uid] || new Set());
    if (set.has(i)) set.delete(i); else set.add(i);
    tr.classList.toggle('sel', set.has(i)); tr.querySelector('input').checked = set.has(i);
    const b = document.querySelector(`[data-perkdel="${CSS.escape(uid)}"]`);
    b.disabled = !set.size; b.textContent = rawT('set.perks_remove', { n: set.size });
  });
  document.querySelectorAll('[data-perkdel]').forEach(b => b.onclick = () => {
    const uid = b.dataset.perkdel, st = S.set.data.settlements.find(x => x.uid === uid), sel = [...(S.set.sel[uid] || [])].sort((a, c) => a - c);
    if (!sel.length) return;
    previewAndApply('settle', { op: 'perk_remove', uid, perks: sel, ids: sel.map(i => st.perks[i].id) }, reloadSession);
  });
  document.querySelectorAll('[data-skip]').forEach(b => b.onclick = () => previewAndApply('settle', { op: 'skip_decision', uid: b.dataset.skip }, reloadSession));
  document.querySelectorAll('[data-setrename]').forEach(b => b.onclick = () => {
    const bar = document.querySelector(`[data-setrenamebar="${CSS.escape(b.dataset.setrename)}"]`);
    bar.hidden = false; const inp = bar.querySelector('input'); inp.focus(); inp.select(); });
  const doRename = uid => { const bar = document.querySelector(`[data-setrenamebar="${CSS.escape(uid)}"]`);
    bar.hidden = true; quickStage('settle', { op: 'rename', uid, name: bar.querySelector('input').value }); };
  document.querySelectorAll('[data-setrename-ok]').forEach(b => b.onclick = () => doRename(b.dataset.setrenameOk));
  document.querySelectorAll('[data-setrename-cancel]').forEach(b => b.onclick = () => { b.closest('[data-setrenamebar]').hidden = true; });
  document.querySelectorAll('[data-setrenamebar] input').forEach(inp => inp.addEventListener('keydown', e => {
    const uid = inp.closest('[data-setrenamebar]').dataset.setrenamebar;
    if (e.key === 'Enter') { e.preventDefault(); doRename(uid); }
    else if (e.key === 'Escape') { e.preventDefault(); inp.closest('[data-setrenamebar]').hidden = true; }
  }));
}

/* -- Visor JSON (fase 3; modo experto: fase 5): nombres legibles, notas, dependencias -- */
const pkey = p => JSON.stringify(p);
async function jvFetch(path, offset = 0) {
  return api('/api/session/json', { path, offset, file: S.jv.file }, { quiet: true });
}
async function jvEnsure(path) {          // carga el nodo (y páginas siguientes si hace falta)
  const k = pkey(path);
  if (!S.jv.nodes[k]) S.jv.nodes[k] = await jvFetch(path);
  return S.jv.nodes[k];
}
async function jvMore(path) {
  const n = S.jv.nodes[pkey(path)], more = await jvFetch(path, n.children.length);
  n.children = n.children.concat(more.children);
}
async function viewJson() {
  const key = `${S.sess.slot}:${S.sess.changes.length}:${S.sess.dirty}`;
  if (!S.jv || S.jv.key !== key) {
    const prev = S.jv || {};
    S.jv = { key, file: prev.file || 0, nodes: {}, open: prev.open || new Set([pkey([])]), sel: prev.sel || null,
      raw: !!prev.raw, q: prev.q || '', results: null, expert: !!prev.expert };
    try {
      await jvEnsure([]);
      for (const k of [...S.jv.open].sort((a, b) => a.length - b.length)) {      // vuelve a abrir lo que estaba abierto
        try { await jvEnsure(JSON.parse(k)); } catch (_) { S.jv.open.delete(k); }
      }
    } catch (e) { $('#main').innerHTML = `<p class="msg block"><span>${esc(errText(e))}</span></p>`; return; }
    if (S.view !== 'json') return;
  }
  const root = S.jv.nodes[pkey([])], files = root.files || [];
  const main = $('#main'), top = main.scrollTop;
  main.innerHTML = `<h1>${t('jv.title')}</h1><p class="help">${t('jv.help')}</p>
    <div class="row jvbar">
      ${files.length > 1 ? `<label class="inline">${t('jv.file')} <select id="jv-file">${files.map((f, i) => `<option value="${i}" ${i === S.jv.file ? 'selected' : ''}>${esc(f)}</option>`).join('')}</select></label>` : ''}
      <label class="inline"><input type="checkbox" id="jv-raw" ${S.jv.raw ? 'checked' : ''}> ${t('jv.raw_keys')}</label>
      <label class="inline expert"><input type="checkbox" id="jv-expert" ${S.jv.expert ? 'checked' : ''}> ${t('jv.expert')}</label>
      <input id="jv-q" type="search" placeholder="${esc(rawT('jv.search'))}" value="${esc(S.jv.q)}"><button class="btn small" id="jv-go">${t('jv.search_go')}</button>
    </div>
    <div id="jv-results"></div>
    <div class="jvwrap"><div class="jvtree card" id="jv-tree">${jvRender([], root, 0)}</div><div class="jvside card" id="jv-side"></div></div>`;
  main.scrollTop = top;
  jvBind();
  jvRenderResults();
  if (S.jv.sel) jvSelect(JSON.parse(S.jv.sel), false);
  else $('#jv-side').innerHTML = `<p class="muted">${t('jv.pick')}</p>`;
}
function jvLabel(c) {
  const nm = S.jv.raw || typeof c.k === 'number' ? (typeof c.k === 'number' ? `[${c.k}]` : c.k) : c.name;
  const alt = !S.jv.raw && typeof c.k === 'string' && c.name !== c.k ? ` <span class="jvobf">${esc(c.k)}</span>` : '';
  return `<span class="jvk">${esc(nm)}</span>${alt}`;
}
function jvValue(c) {
  if (c.type === 'object') return `<span class="jvt">{${c.len}}</span>`;
  if (c.type === 'array') return `<span class="jvt">[${c.len}]</span>`;
  const v = c.type === 'string' ? `"${esc(c.value)}"` : esc(String(c.value));
  return `<span class="jvv jv-${c.type}">${v}</span>${c.hint ? ` <span class="jvhint">${esc(c.hint)}</span>` : ''}`;
}
function jvRender(path, node, depth) {
  if (!node || !node.children) return '';
  let h = '<ul class="jvlist">';
  for (const c of node.children) {
    const p = path.concat([c.k]), k = pkey(p), box = c.type === 'object' || c.type === 'array', open = box && S.jv.open.has(k);
    h += `<li><div class="jvrow ${S.jv.sel === k ? 'sel' : ''}" data-p="${esc(k)}">
      <span class="jvcaret">${box ? (open ? '▾' : '▸') : ''}</span>${jvLabel(c)} ${jvValue(c)}</div>`;
    if (open && S.jv.nodes[k]) h += jvRender(p, S.jv.nodes[k], depth + 1);
    h += '</li>';
  }
  if (node.total > node.children.length)
    h += `<li><button class="btn small jvmore" data-more="${esc(pkey(path))}">${t('jv.more', { n: node.total - node.children.length })}</button></li>`;
  return h + '</ul>';
}
function jvRerenderTree() {
  const tree = $('#jv-tree'), top = tree.scrollTop;
  tree.innerHTML = jvRender([], S.jv.nodes[pkey([])], 0); tree.scrollTop = top; jvBindTree();
}
async function jvToggle(path) {
  const k = pkey(path);
  if (S.jv.open.has(k)) S.jv.open.delete(k);
  else { try { await jvEnsure(path); } catch (e) { return toast(errText(e), true); } S.jv.open.add(k); }
  jvRerenderTree();
}
async function jvSelect(path, rerender = true) {
  S.jv.sel = pkey(path);
  if (rerender) jvRerenderTree();
  let n;
  try { n = await jvFetch(path); } catch (e) { $('#jv-side').innerHTML = `<p class="msg block"><span>${esc(errText(e))}</span></p>`; return; }
  const rawPath = path.map(p => typeof p === 'number' ? `[${p}]` : p).join(' › ') || '—';
  const box = n.type === 'object' || n.type === 'array';
  const val = box ? `<p class="muted">${t('jv.children', { n: n.total })}</p>`
    : `<pre class="jvval">${esc(n.type === 'string' ? JSON.stringify(n.value) : String(n.value))}</pre>${n.hint ? `<p class="jvhint">${esc(n.hint)}</p>` : ''}`;
  const notes = (n.notes || []).map(x => `<div class="msg ${x.level}"><span>${t('jv.n.' + x.code, shapeParams(x.p || {}))}</span></div>`).join('');
  const editable = S.jv.expert && ['string', 'number', 'bool'].includes(n.type) && !n.binary;
  const editor = !S.jv.expert ? '' : !editable ? `<p class="muted small">${t('jv.ed_not_here')}</p>` : `<div class="jvedit card">
      <b>${t('jv.ed_title')}</b>
      ${n.type === 'bool' ? `<label class="inline"><input type="checkbox" id="jv-ed" ${n.value ? 'checked' : ''}> ${t('jv.ed_true')}</label>`
        : n.type === 'number' ? `<input id="jv-ed" type="text" inputmode="decimal" value="${esc(String(n.value))}" spellcheck="false">`
        : `<input id="jv-ed" type="text" maxlength="500" value="${esc(n.value)}" spellcheck="false">`}
      <p class="muted small">${t(n.type === 'number' ? (n.float ? 'jv.ed_float' : 'jv.ed_int') : n.type === 'bool' ? 'jv.ed_bool' : 'jv.ed_text')}</p>
      <div class="row end"><button class="btn small primary" id="jv-ed-go">${t('common.preview')}</button></div></div>`;
  $('#jv-side').innerHTML = `<h3 class="jvpath">${esc(n.rpath)}</h3><p class="muted small jvraw">${esc(rawPath)}</p>
    <p class="small">${t('jv.type.' + n.type)}</p>${val}${notes || `<p class="muted small">${t('jv.no_notes')}</p>`}${editor}
    <div class="row jvacts"><button class="btn small" id="jv-cp-path">${t('jv.copy_path')}</button>
      <button class="btn small" id="jv-cp-val">${t('jv.copy_value')}</button>
      <button class="btn small" id="jv-dl">${t('jv.download')}</button>
      <button class="btn small" id="jv-dl-names">${t('jv.download_names')}</button></div>`;
  const copy = async txt => { try { await navigator.clipboard.writeText(txt); toast(t('jv.copied')); } catch (_) { toast(t('jv.copy_fail'), true); } };
  const exp = async names => (await api('/api/session/json/export', { path, names, file: S.jv.file }, { quiet: true })).text;
  $('#jv-cp-path').onclick = () => copy(n.rpath);
  $('#jv-cp-val').onclick = async () => copy(box ? await exp(false) : (n.type === 'string' ? n.value : String(n.value)));
  const dl = async names => {
    const txt = await exp(names), a = document.createElement('a');
    const name = (n.rpath.split(/[.\[\]]/).filter(Boolean).pop() || 'save') + (names ? '_readable' : '') + '.json';
    a.href = URL.createObjectURL(new Blob([txt], { type: 'application/json' })); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
  $('#jv-dl').onclick = () => dl(false);
  $('#jv-dl-names').onclick = () => dl(true);
  if ($('#jv-ed-go')) $('#jv-ed-go').onclick = () => {
    const el = $('#jv-ed');
    let value;
    if (n.type === 'bool') value = el.checked;
    else if (n.type === 'number') {
      const txt = el.value.trim().replace(',', '.');
      if (!/^-?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(txt)) return toast(t('jv.ed_bad_number'), true);
      value = Number(txt);
    } else value = el.value;
    previewAndApply('json', { file: S.jv.file, path, value }, reloadSession);
  };
}
function jvRenderResults() {
  const box = $('#jv-results'), r = S.jv.results;
  if (!box) return;
  if (!r) { box.innerHTML = ''; return; }
  if (!r.results.length) { box.innerHTML = `<p class="muted small">${t('jv.found_none', { q: r.q })}</p>`; return; }
  box.innerHTML = `<div class="card jvres"><p class="small">${t(r.more ? 'jv.found_more' : 'jv.found', { n: r.results.length })}</p><ul>${r.results.map((x, i) =>
    `<li><a href="#" data-res="${i}">${esc(x.rpath)}</a> ${x.type === 'object' || x.type === 'array' ? '' : jvValue(x)}</li>`).join('')}</ul></div>`;
  box.querySelectorAll('[data-res]').forEach(a => a.onclick = async e => { e.preventDefault(); await jvReveal(r.results[+a.dataset.res].path); });
}
async function jvReveal(path) {       // abre todos los padres (cargando páginas si el índice queda lejos) y selecciona
  try {
    for (let i = 0; i < path.length; i++) {
      const parent = path.slice(0, i), n = await jvEnsure(parent);
      S.jv.open.add(pkey(parent));
      const child = path[i];
      if (typeof child === 'number') { while (n.children.length <= child - (n.offset || 0) && n.children.length < n.total) await jvMore(parent); }
      else { while (!n.children.some(c => c.k === child) && n.children.length < n.total) await jvMore(parent); }
    }
  } catch (e) { return toast(errText(e), true); }
  jvRerenderTree();
  await jvSelect(path, true);
  const row = document.querySelector(`.jvrow[data-p="${CSS.escape(pkey(path))}"]`);
  if (row) row.scrollIntoView({ block: 'center' });
}
function jvBindTree() {
  document.querySelectorAll('#jv-tree .jvrow').forEach(r => r.onclick = e => {
    const path = JSON.parse(r.dataset.p);
    if (e.target.classList.contains('jvcaret') && e.target.textContent) jvToggle(path);
    else { jvSelect(path); if (r.querySelector('.jvcaret').textContent && !S.jv.open.has(r.dataset.p)) jvToggle(path); }
  });
  document.querySelectorAll('#jv-tree .jvmore').forEach(b => b.onclick = async () => {
    try { await jvMore(JSON.parse(b.dataset.more)); } catch (e) { return toast(errText(e), true); } jvRerenderTree(); });
}
function jvBind() {
  jvBindTree();
  const f = $('#jv-file');
  if (f) f.onchange = () => { S.jv.file = +f.value; S.jv.key = null; S.jv.results = null; viewJson(); };
  $('#jv-raw').onchange = () => { S.jv.raw = $('#jv-raw').checked; jvRerenderTree(); };
  $('#jv-expert').onchange = () => {
    const box = $('#jv-expert');
    if (!box.checked) { S.jv.expert = false; if (S.jv.sel) jvSelect(JSON.parse(S.jv.sel), false); return; }
    box.checked = false;
    showModal(t('jv.expert_title'), `<div class="stack"><div class="msg warn"><span>${t('jv.expert_warn')}</span></div>
        <ul class="small">${['1', '2', '3', '4'].map(i => `<li>${t('jv.expert_rule' + i)}</li>`).join('')}</ul></div>`, [
      { label: t('common.cancel'), onClick: closeModal },
      { label: t('jv.expert_on'), primary: true, onClick: () => { closeModal(); S.jv.expert = true; box.checked = true;
        if (S.jv.sel) jvSelect(JSON.parse(S.jv.sel), false); } }]);
  };
  const go = async () => {
    S.jv.q = $('#jv-q').value;
    if (S.jv.q.trim().length < 2) { S.jv.results = null; return jvRenderResults(); }
    $('#jv-results').innerHTML = `<p class="muted small">${t('jv.searching')}</p>`;
    try { S.jv.results = await api('/api/session/json/search', { q: S.jv.q, file: S.jv.file }, { quiet: true }); }
    catch (e) { return toast(errText(e), true); }
    jvRenderResults();
  };
  $('#jv-go').onclick = go;
  $('#jv-q').onkeydown = e => { if (e.key === 'Enter') go(); };
}

/* -- Acerca de (estándar MightyZeus 1.3: créditos completos) -- */
function aboutHtml() {
  return `<div class="about"><h3>${t('about.title')}</h3>
    <p class="muted">Olympian NMS Save Editor — Copyright © 2026 MightyZeus · GPL-3.0</p>
    <p><b>${t('about.ack')}</b></p>${rawT('about.lines').split('\n').map(l => `<p>${esc(l)}</p>`).join('')}</div>`;
}
/* -- Ajustes -- */
function viewSettings() {
  const st = S.state.settings, L = { es: 'Español', en: 'English', ...{ 'es-ES': LANG_NAMES['es-ES'] } }, LT = LANG_NAMES;
  $('#main').innerHTML = `<h1>${t('settings.title')}</h1>
    <div class="card stack" style="max-width:640px">
      <div class="kv"><span>${t('settings.ui_lang')}</span><span>${L[st.ui_lang]}</span>
        <span>${t('settings.term_lang')}</span><span>${LT[st.term_lang] || st.term_lang}</span></div>
      ${gameDataHtml()}
      <div class="row"><button class="btn" id="s-lang">${t('settings.change_lang')}</button></div>
      ${folderForm()}
      ${securityHtml()}
      <p class="muted">${t('settings.version')}: ${esc(S.state.version)} · © 2026 MightyZeus · GPL-3.0 · <a href="https://paypal.me/ImMightyZeus" target="_blank" rel="noopener">${t('settings.donate')}</a> · <a href="https://github.com/ImMightyZeus/Olympian-NMS-Save-Editor" target="_blank" rel="noopener">GitHub</a></p>
      ${aboutHtml()}
      ${S.state.lz4 ? '' : `<p class="msg warn"><span>${t('settings.lz4_missing')}</span></p>`}
    </div>`;
  $('#s-lang').onclick = async () => { if (await langModal(false)) renderEditor(); };
  bindFolderForm(async () => { S.sess = null; showStart(); });
  bindSecurity(viewSettings);
}
$('#btn-settings').onclick = async function openStartSettings() {
  showModal(t('settings.title'), `<div class="stack"><div class="row"><button class="btn" id="s-lang2">${t('settings.change_lang')}</button></div>${folderForm()}${securityHtml()}
    <p class="muted">${t('settings.version')}: ${esc(S.state.version)} · © 2026 MightyZeus · GPL-3.0 · <a href="https://paypal.me/ImMightyZeus" target="_blank" rel="noopener">${t('settings.donate')}</a> · <a href="https://github.com/ImMightyZeus/Olympian-NMS-Save-Editor" target="_blank" rel="noopener">GitHub</a></p>${aboutHtml()}</div>`, [{ label: t('common.close'), onClick: closeModal }]);
  $('#s-lang2').onclick = async () => { if (await langModal(false)) showStart(); };
  bindFolderForm(() => { closeModal(); showStart(); });
  bindSecurity(() => { openStartSettings(); showStart(); });
};


/* ================================================================== PROTECCIÓN (PIN + código 2FA) */
const codeInput = (id, ph = '000000') => `<input id="${id}" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="${ph}">`;
function lockScreen() {
  return new Promise(resolve => {
    let rec = false;
    const draw = () => {
      showModal(t('lock.title'), `<div class="stack lockbox">
        <p class="muted">${t(rec ? 'lock.recover_help' : 'lock.help')}</p>
        ${rec ? `<label>${t('lock.recovery_code')}<input id="lk-rec" maxlength="11" placeholder="XXXXX-XXXXX" autocomplete="off"></label>`
              : `<label>${t('lock.pin')}<input id="lk-pin" type="password" inputmode="numeric" maxlength="12" autocomplete="off"></label>
                 <label>${t('lock.code')}${codeInput('lk-code')}</label>`}
        <p class="small"><a href="#" id="lk-alt">${t(rec ? 'lock.use_code' : 'lock.use_recovery')}</a></p></div>`, [
        { label: t('lock.unlock'), primary: true, onClick: go }]);
      $('#lk-alt').onclick = e => { e.preventDefault(); rec = !rec; draw(); };
      const first = $(rec ? '#lk-rec' : '#lk-pin'); first && first.focus();
      $('#modal-body').onkeydown = e => { if (e.key === 'Enter') go(); };
    };
    const go = async () => {
      try {
        if (rec) await api('/api/lock/recover', { code: $('#lk-rec').value });
        else await api('/api/lock/unlock', { pin: $('#lk-pin').value, code: $('#lk-code').value });
        $('#modal-body').onkeydown = null; closeModal(); await refreshState(); resolve();
      } catch (e) { toast(errText(e), true); }
    };
    draw();
  });
}
const protectedOk = () => !!((S.state.lock && S.state.lock.enabled) || S.state.settings.allow_unprotected);
/* primera vez: ofrecer la protección y decir qué queda deshabilitado sin ella */
function protectionOffer() {
  const done = async () => {
    try { await api('/api/settings', { lock_offered: true }); await refreshState(); } catch (_) {}
    showModal(t('lock.offer_after_title'), `<p>${t('lock.offer_after')}</p>`, [{ label: t('common.close'), primary: true, onClick: () => { closeModal(); showStart(); } }]);
  };
  showModal(t('lock.offer_title'), `<div class="stack"><p>${t('lock.offer_help')}</p>
    <p><b>${t('lock.offer_disabled')}</b></p><ul class="small"><li>${t('lock.offer_d1')}</li><li>${t('lock.offer_d2')}</li></ul>
    <p class="muted small">${t('lock.offer_alone')}</p><p class="muted small">${t('lock.limits')}</p></div>`, [
    { label: t('lock.offer_later'), onClick: () => { closeModal(); done(); } },
    { label: t('lock.offer_alone_btn'), onClick: async () => {
      closeModal();
      try { await api('/api/settings', { allow_unprotected: true }); } catch (_) {}
      done(); } },
    { label: '🔐 ' + t('lock.setup'), primary: true, onClick: () => lockSetup(() => done()) }]);
}
function securityHtml() {
  const L = S.state.lock || {}, ex = S.state.settings.backup_extra || '';
  const lock = L.enabled
    ? `<p><span class="pill ok">● ${t('lock.on')}</span> <span class="muted small">${t('lock.left', { n: L.recovery_left })}</span></p>
       ${L.recovered ? `<p class="msg warn"><span>${t('lock.recovered_note')}</span></p><div class="row"><button class="btn" id="lk-setup">${t('lock.setup_again')}</button><button class="btn danger" id="lk-off">${t('lock.disable')}</button></div>`
                     : `<div class="row"><button class="btn danger" id="lk-off">${t('lock.disable')}</button></div>`}`
    : `<p class="muted small">${t('lock.off_help')}</p><div class="row"><button class="btn" id="lk-setup">🔐 ${t('lock.setup')}</button></div>`;
  const alone = L.enabled ? '' : `<label class="chk"><input type="checkbox" id="lk-alone" ${S.state.settings.allow_unprotected ? 'checked' : ''}> ${t('lock.alone')}</label>
    <p class="muted small">${t('lock.alone_help')}</p>`;
  return `<h3>${t('lock.section')}</h3>${lock}${alone}<p class="muted small">${t('lock.limits')}</p>
    <h3>${t('bk.extra_title')}</h3><p class="muted small">${t('bk.extra_help')}</p>
    <label>${t('bk.extra')}<input id="bk-extra" value="${esc(ex)}" placeholder="D:\\OlympianNMS_Backups"></label>
    <div class="row end">${ex ? `<button class="btn" id="bk-extra-off">${t('bk.extra_off')}</button>` : ''}<button class="btn" id="bk-extra-save">${t('settings.folder_save')}</button></div>`;
}
function bindSecurity(redraw) {
  const save = async v => { try { await api('/api/settings', { backup_extra: v }); await refreshState(); toast(t(v ? 'bk.extra_ok' : 'bk.extra_removed')); redraw(); } catch (e) { toast(errText(e), true); } };
  $('#bk-extra-save') && ($('#bk-extra-save').onclick = () => save($('#bk-extra').value.trim()));
  $('#bk-extra-off') && ($('#bk-extra-off').onclick = () => save(''));
  $('#lk-setup') && ($('#lk-setup').onclick = () => lockSetup(redraw));
  $('#lk-alone') && ($('#lk-alone').onchange = e => {
    const on = e.target.checked, set = async v => {
      try { await api('/api/settings', { allow_unprotected: v }); await refreshState(); toast(t(v ? 'lock.alone_on' : 'lock.alone_off')); } catch (er) { toast(errText(er), true); }
      redraw(); };
    if (!on) return set(false);
    e.target.checked = false;
    showModal(t('lock.alone_title'), `<div class="stack"><p>${t('lock.alone_confirm')}</p>
      <div class="msg ok"><span class="lv">${t('level.ok')}</span><span>${t('lock.alone_safe')}</span></div></div>`, [
      { label: t('common.cancel'), onClick: closeModal },
      { label: t('lock.alone_ok'), primary: true, onClick: () => { closeModal(); set(true); } }]);
  });
  $('#lk-off') && ($('#lk-off').onclick = () => {
    const L = S.state.lock || {};
    showModal(t('lock.disable'), `<div class="stack"><p>${t('lock.disable_help')}</p>
      ${L.recovered ? '' : `<label>${t('lock.pin')}<input id="lo-pin" type="password" inputmode="numeric" maxlength="12"></label><label>${t('lock.code')}${codeInput('lo-code')}</label>`}</div>`, [
      { label: t('common.cancel'), onClick: closeModal },
      { label: t('lock.disable'), danger: true, primary: true, onClick: async () => {
        try { await api('/api/lock/disable', L.recovered ? {} : { pin: $('#lo-pin').value, code: $('#lo-code').value });
          closeModal(); await refreshState(); toast(t('lock.disabled')); redraw(); } catch (e) { toast(errText(e), true); } } }]);
  });
}
function lockSetup(redraw) {
  showModal(t('lock.setup'), `<div class="stack"><p>${t('lock.setup_help')}</p>
    <label>${t('lock.new_pin')}<input id="ls-pin" type="password" inputmode="numeric" maxlength="12" autocomplete="new-password"></label>
    <label>${t('lock.new_pin2')}<input id="ls-pin2" type="password" inputmode="numeric" maxlength="12" autocomplete="new-password"></label></div>`, [
    { label: t('common.cancel'), onClick: closeModal },
    { label: t('lock.next'), primary: true, onClick: async () => {
      const a = $('#ls-pin').value, b = $('#ls-pin2').value;
      if (!/^\d{4,12}$/.test(a)) return toast(t('err.lock_pin_format'), true);
      if (a !== b) return toast(t('lock.pin_mismatch'), true);
      let r; try { r = await api('/api/lock/begin', { pin: a }); } catch (e) { return toast(errText(e), true); }
      let qr = '';
      try { const q = qrcode(0, 'M'); q.addData(r.uri); q.make(); qr = q.createSvgTag(5, 4); } catch (_) {}
      showModal(t('lock.setup'), `<div class="stack"><p>${t('lock.scan_help')}</p>
        <div class="qrbox">${qr}</div>
        <p class="small">${t('lock.key_help')}<br><code class="lockkey">${esc(r.key)}</code></p>
        <label>${t('lock.code_first')}${codeInput('ls-code')}</label></div>`, [
        { label: t('common.cancel'), onClick: closeModal },
        { label: t('lock.activate'), primary: true, onClick: async () => {
          let c; try { c = await api('/api/lock/confirm', { code: $('#ls-code').value }); } catch (e) { return toast(errText(e), true); }
          await refreshState();
          showModal(t('lock.recovery_title'), `<div class="stack"><p>${t('lock.recovery_help')}</p>
            <pre class="reccodes">${c.recovery.map(esc).join('\n')}</pre></div>`, [
            { label: t('lock.recovery_copy'), onClick: () => { navigator.clipboard && navigator.clipboard.writeText(c.recovery.join('\n')).then(() => toast(t('lock.copied'))).catch(() => {}); } },
            { label: t('lock.recovery_done'), primary: true, onClick: () => { closeModal(); redraw(); } }]);
        } }]);
      $('#ls-code').focus();
    } }]);
}
/* ------------------------------------------------------------------ arranque */
(async function init() {
  try {
    await refreshState();
    const st = S.state.settings;
    await loadLang(st.ui_lang || 'es', st.term_lang || st.ui_lang || 'es');
    await refreshState();                       // the sidebar was drawn before the texts were loaded
    if (S.state.lock && S.state.lock.enabled && !S.state.lock.unlocked) await lockScreen();
    const g = await api('/api/galaxies'); S.galaxies = g.galaxies; S.glyphNames = g.glyphs;
    if (!st.lang_chosen) await langModal(true);
    try { S.slots = (await api('/api/slots')).slots; } catch (_) { S.slots = []; }
    if (S.state.session) { await openEditor(S.state.session); } else { await showStart(); }
    if (!S.state.settings.lock_offered && !(S.state.lock && S.state.lock.enabled)) protectionOffer();
  } catch (e) { document.body.insertAdjacentHTML('beforeend', `<p class="msg block"><span>${esc(e.message)}</span></p>`); }
})();
