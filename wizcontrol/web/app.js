'use strict';

/* ======================================================================== helpers */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

const store = {
  get(k, d) { try { const v = localStorage.getItem('wiz.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('wiz.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

function throttle(fn, ms) {
  let last = 0, timer = null, args;
  return (...a) => {
    args = a;
    const run = () => { last = Date.now(); timer = null; fn(...args); };
    const wait = ms - (Date.now() - last);
    if (wait <= 0) run();
    else if (!timer) timer = setTimeout(run, wait);
  };
}

function hsvToHex(h, s, v) {
  const f = (n) => { const k = (n + h / 60) % 6; return v - v * s * Math.max(0, Math.min(k, 4 - k, 1)); };
  return '#' + [f(5), f(3), f(1)].map((x) => Math.round(x * 255).toString(16).padStart(2, '0')).join('');
}
function hexToRgb(hex) { const v = parseInt(hex.slice(1), 16); return [(v >> 16) & 255, (v >> 8) & 255, v & 255]; }
function rgbToHex(r, g, b) { return '#' + [r, g, b].map((x) => clamp(Math.round(x), 0, 255).toString(16).padStart(2, '0')).join(''); }
function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h = 0;
  if (d) {
    if (mx === r) h = ((g - b) / d) % 6; else if (mx === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
    h *= 60; if (h < 0) h += 360;
  }
  return [h, mx ? d / mx : 0, mx];
}
function kelvinToHex(k) {
  const t = k / 100;
  let r, g, b;
  if (t <= 66) { r = 255; g = 99.47 * Math.log(t) - 161.12; b = t <= 19 ? 0 : 138.52 * Math.log(t - 10) - 305.04; }
  else { r = 329.7 * Math.pow(t - 60, -0.1332); g = 288.12 * Math.pow(t - 60, -0.0755); b = 255; }
  return rgbToHex(r, g, b);
}
const grad = (cols, dir = '90deg') => cols.length > 1 ? `linear-gradient(${dir}, ${cols.join(', ')})` : (cols[0] || '#333');

/* ======================================================================== state */
const S = {
  meta: null,
  state: null,
  target: store.get('target', 'all'),
  tab: store.get('tab', 'controls'),
  fxCat: 'All',
  fxSearch: '',
  fx: { speed: 1, brightness: 100, color: store.get('fxColor', '#ffffff'), palette: store.get('fxPalette', 'rainbow') },
  connected: false,
};

/* ======================================================================== api */
async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}
async function act(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg, 'ok');
    return r;
  } catch (e) {
    toast(e.message || String(e), 'error');
    return undefined;
  }
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), kind === 'error' ? 5000 : 2800);
}

/* ======================================================================== websocket */
function connect() {
  let ws;
  try { ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws'); }
  catch { setTimeout(connect, 2000); return; }
  ws.onopen = () => setConn(true);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === 'state') { S.state = m.data; render(); }
  };
  ws.onclose = () => { setConn(false); setTimeout(connect, 1500); };
  ws.onerror = () => ws.close();
}
function setConn(ok) {
  S.connected = ok;
  $('#conn').classList.toggle('ok', ok);
  $('#conn').textContent = ok ? '' : 'Reconnecting…';
}

/* ======================================================================== target helpers */
const lightsById = () => Object.fromEntries((S.state?.lights || []).map((l) => [l.id, l]));
function targetLights(target = S.target) {
  const st = S.state; if (!st) return [];
  if (target === 'all') return st.lights;
  const [kind, id] = target.split(':');
  const byId = lightsById();
  if (kind === 'light') return byId[id] ? [byId[id]] : [];
  if (kind === 'group') { const g = st.groups.find((x) => x.id === id); return g ? g.lights.map((i) => byId[i]).filter(Boolean) : []; }
  return [];
}
function targetName(target = S.target) {
  if (target === 'all') return 'All Lights';
  const [kind, id] = target.split(':');
  if (kind === 'light') return lightsById()[id]?.name || 'Light';
  if (kind === 'group') return S.state?.groups.find((x) => x.id === id)?.name || 'Group';
  return 'Custom selection';
}
function targetExists(target) {
  if (target === 'all') return true;
  const [kind, id] = target.split(':');
  if (kind === 'light') return !!lightsById()[id];
  if (kind === 'group') return !!S.state?.groups.find((x) => x.id === id);
  return false;
}
function runsForTarget() {
  const ids = new Set(targetLights().map((l) => l.id));
  const runs = (S.state?.running || []).filter((r) => r.lights.some((i) => ids.has(i)));
  runs.sort((a, b) => (b.target === S.target) - (a.target === S.target));
  return runs;
}
const currentRun = () => runsForTarget()[0];
const effectMeta = (id) => S.meta?.effects.find((e) => e.id === id);

function selectTarget(t) {
  S.target = t;
  store.set('target', t);
  document.body.classList.remove('drawer');
  syncFxFromRun(true);
  render(true);
}

/* ======================================================================== light visuals */
const SCENE_COLORS = {
  1: ['#003f88', '#00a6fb', '#48cae4'], 2: ['#ff477e', '#c9184a', '#ff85a1'], 3: ['#ff7b00', '#ff4e00', '#ffb700'],
  4: ['#ff00ff', '#00ffff', '#ffff00'], 5: ['#ff5500', '#ff2200', '#ffaa00'], 6: ['#ff9a3c', '#ffcb8e'],
  7: ['#228b22', '#9acd32', '#2e8b57'], 8: ['#ffb3ba', '#bae1ff', '#baffc9'], 9: ['#ff8c42', '#fff4e5'],
  10: ['#ff9329', '#7a3b00'], 11: ['#ffc58f'], 12: ['#fff4e5'], 13: ['#e6f0ff'], 14: ['#ff6a00', '#3d1a00'],
  15: ['#f5f8ff'], 16: ['#ffd8a8'], 17: ['#ffffff', '#ffe4c4'], 18: ['#8a7dff', '#4a3dbf'], 19: ['#ff00ff', '#ff3399'],
  20: ['#ff9ff3', '#48dbfb', '#1dd1a1'], 21: ['#ffd32a', '#ff9f1a', '#ffe66d'], 22: ['#d2691e', '#b8860b', '#a52a00'],
  23: ['#001a72', '#0047ab', '#00296b'], 24: ['#0b6623', '#4cbb17', '#ffd700'], 25: ['#00ff7f', '#7fff00', '#00e5b0'],
  26: ['#ff00a0', '#7a00ff', '#00f0ff'], 27: ['#ff0000', '#00b300', '#ffffff'], 28: ['#ff6a00', '#7f00ff'],
  29: ['#ff8c1a', '#ffa040'], 30: ['#ffcc33', '#ffe9a8'], 31: ['#ff2a6d', '#ffffff'], 32: ['#b5651d', '#ffbf00', '#704214'],
  33: ['#ff9933', '#ffd700', '#ff3300'], 34: ['#ffffff'], 35: ['#ff0000', '#330000'], 36: ['#d6eaff', '#8fb8ff', '#ffffff'],
};
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const segs = (cols) => cols.map((c) => `<span style="background:${c}"></span>`).join('');
const strip = (cols, cls = 'strip') => `<span class="${cls}" aria-hidden="true">${segs(cols)}</span>`;
const split = (cols) => cols.length > 1 ? `conic-gradient(${cols[0]} 0 50%, ${cols[1]} 0 100%)` : cols[0];
function lightColor(l) {
  const s = l.state || {};
  if (l.effect) {
    const run = (S.state?.running || []).find((r) => r.id === l.effect);
    const pv = effectMeta(run?.effect)?.preview || [];
    return split(pv.length ? pv : ['#ffffff']);
  }
  if (!s.state) return null;
  if (s.sceneId) return split(SCENE_COLORS[s.sceneId] || ['#ffffff']);
  if (s.r != null && (s.r || s.g || s.b)) return rgbToHex(s.r, s.g, s.b);
  if (s.temp) return kelvinToHex(s.temp);
  return '#fff4e0';
}
/* the swatch is the colour the light is giving, dimmed with it */
function bulbHtml(l) {
  if (l.online === false) return '<span class="bulb offline" aria-hidden="true"></span>';
  const c = lightColor(l);
  if (!c) return '<span class="bulb" aria-hidden="true"></span>';
  const op = l.state?.dimming ? 0.45 + 0.55 * (l.state.dimming / 100) : 1;
  return `<span class="bulb lit" aria-hidden="true" style="background:${c};opacity:${op.toFixed(2)}"></span>`;
}

/* range tracks fill up to the thumb */
function paintSlider(el) { const min = +el.min || 0, max = +el.max || 100; el.style.setProperty('--pct', `${((+el.value - min) / (max - min)) * 100}%`); }
const paintSliders = () => $$('input.slider').forEach(paintSlider);
document.addEventListener('input', (e) => { if (e.target.matches?.('input.slider')) paintSlider(e.target); });

/* ======================================================================== render */
function render(force = false) {
  if (!S.state || !S.meta) return;
  if (!targetExists(S.target)) { S.target = 'all'; store.set('target', 'all'); }
  const hasLights = S.state.lights.length > 0;
  $('#welcome').classList.toggle('hidden', hasLights);
  $('#controlArea').classList.toggle('hidden', !hasLights);
  renderSidebar();
  renderHeader();
  syncControls(force);
  renderFxActive();
  renderScenesActive();
  renderPalettes();
  renderFxPaletteSelect();
  $('#discoverBtn').disabled = S.state.discovering;
  paintSliders();
}

function renderSidebar() {
  const st = S.state;
  const lights = st.lights;
  const onCount = (ls) => ls.filter((l) => l.state?.state).length;
  const runningTargets = new Set(st.running.map((r) => r.target));
  const runName = (t) => st.running.find((r) => r.target === t)?.name;
  const item = (t, name, sub, extra = '') => `
    <li class="side-item ${S.target === t ? 'active' : ''}" data-target="${esc(t)}" tabindex="0" aria-current="${S.target === t}">
      <span class="name">${esc(name)}<span class="sub">${sub}</span></span>
      ${runningTargets.has(t) ? `<span class="fx-tag" title="Effect running">${esc(runName(t))}</span>` : ''}
      ${extra}
    </li>`;
  let html = item('all', 'All Lights', `${lights.length} lights, ${onCount(lights)} on`);
  for (const g of st.groups) {
    const ls = targetLights('group:' + g.id);
    html += item('group:' + g.id, g.name, `${ls.length} lights, ${onCount(ls)} on`,
      `<button type="button" class="mini icon-only edit" data-edit-group="${g.id}" title="Edit group" aria-label="Edit ${esc(g.name)}">${icon('edit')}</button>`);
  }
  $('#groupList').innerHTML = html;

  $('#lightList').innerHTML = lights.map((l) => {
    const t = 'light:' + l.id;
    const kind = { rgb: 'color', tw: 'tunable white', dw: 'dimmable', socket: 'plug', unknown: '' }[l.kind] || '';
    const status = l.online === false ? 'offline' : kind;
    return `<li class="side-item ${S.target === t ? 'active' : ''}" data-target="${t}" tabindex="0" aria-current="${S.target === t}">
      ${bulbHtml(l)}
      <span class="name">${esc(l.name)}<span class="sub">${esc(l.ip)}${status ? ', ' + esc(status) : ''}</span></span>
      <button type="button" class="mini icon-only edit" data-edit-light="${l.id}" title="Edit light" aria-label="Edit ${esc(l.name)}">${icon('edit')}</button>
      <label class="switch" title="On/off"><input type="checkbox" aria-label="${esc(l.name)} on or off" data-toggle="${l.id}" ${l.state?.state ? 'checked' : ''}><span></span></label>
    </li>`;
  }).join('') || '<li class="empty-note">No lights yet</li>';
  $('#lightCount').textContent = lights.length ? `${lights.filter((l) => l.online).length} of ${lights.length} online` : '';
}

function renderHeader() {
  const ls = targetLights();
  $('#targetName').textContent = targetName();
  const on = ls.filter((l) => l.state?.state).length;
  const off = ls.filter((l) => l.online === false).length;
  $('#targetSub').textContent = ls.length
    ? `${ls.length} light${ls.length === 1 ? '' : 's'}, ${on} on${off ? `, ${off} offline` : ''}`
    : 'No lights in this group yet. Edit the group to add some.';
  const runs = runsForTarget();
  $('#runningBanner').classList.toggle('hidden', !runs.length);
  if (runs.length) {
    const r = runs[0];
    const m = effectMeta(r.effect);
    $('#runningText').innerHTML = `<b>${esc(r.name)}</b> is running on ${esc(targetName(r.target))}, ${r.lights.length} light${r.lights.length === 1 ? '' : 's'}${runs.length > 1 ? ` <span class="muted">(and ${runs.length - 1} more)</span>` : ''}`;
  }
}

/* sync sliders with the actual light state (unless the user is dragging) */
const busy = new Set();
function syncControls(force) {
  const ls = targetLights().filter((l) => l.state?.state);
  const rep = ls[0];
  if (!rep) return;
  const s = rep.state;
  const setVal = (id, v) => { if (force || !busy.has(id)) $('#' + id).value = v; };
  if (s.dimming) { setVal('bri', s.dimming); $('#briVal').textContent = $('#bri').value + '%'; }
  if (s.temp) { setVal('temp', s.temp); $('#tempVal').textContent = $('#temp').value + '\u00a0K'; }
  if (s.r != null && (s.r || s.g || s.b) && !busy.has('hue') && !busy.has('sat')) {
    const [h, sat] = rgbToHsv(s.r, s.g, s.b);
    setVal('hue', Math.round(h)); setVal('sat', Math.round(sat * 100));
    updateColorUi();
  }
}

/* ======================================================================== controls tab */
const sendControl = (body) => act(() => api('POST', '/api/control', { target: S.target, ...body }));
const sendControlThrottled = throttle(sendControl, 110);

function currentPickerHex() { return hsvToHex(+$('#hue').value, +$('#sat').value / 100, 1); }
function updateColorUi() {
  const hex = currentPickerHex();
  $('#colorSwatch').style.background = hex;
  $('#colorPick').value = hex;
  $('#sat').style.setProperty('--hue-color', hsvToHex(+$('#hue').value, 1, 1));
}
function setPickerFromHex(hex) {
  const [h, s] = rgbToHsv(...hexToRgb(hex));
  $('#hue').value = Math.round(h); $('#sat').value = Math.round(s * 100);
  updateColorUi();
}

const SWATCHES = ['#ff0000', '#ff4000', '#ff8000', '#ffc000', '#ffff00', '#80ff00', '#00ff00', '#00ff80', '#00ffff', '#0080ff',
  '#0000ff', '#4000ff', '#8000ff', '#c000ff', '#ff00ff', '#ff0080', '#ff6080', '#ffb3c6', '#b3e5ff', '#ffffff'];

function initControls() {
  $('#hueTrack').innerHTML = segs(Array.from({ length: 90 }, (_, i) => hsvToHex(i * 4, 1, 1)));
  $('#swatches').innerHTML = SWATCHES.map((c) => `<button class="swatch" style="background:${c}" data-color="${c}" title="${c}"></button>`).join('');
  $('#swatches').addEventListener('click', (e) => {
    const c = e.target.dataset.color; if (!c) return;
    setPickerFromHex(c); sendControl({ color: c });
  });
  $('#bri').addEventListener('input', (e) => { $('#briVal').textContent = e.target.value + '%'; sendControlThrottled({ brightness: +e.target.value }); });
  $('#bri').addEventListener('change', (e) => sendControl({ brightness: +e.target.value }));
  $('#temp').addEventListener('input', (e) => { $('#tempVal').textContent = e.target.value + '\u00a0K'; sendControlThrottled({ temp: +e.target.value }); });
  $('#temp').addEventListener('change', (e) => sendControl({ temp: +e.target.value }));
  for (const id of ['hue', 'sat']) {
    $('#' + id).addEventListener('input', () => { updateColorUi(); sendControlThrottled({ color: currentPickerHex() }); });
    $('#' + id).addEventListener('change', () => sendControl({ color: currentPickerHex() }));
  }
  $('#colorPick').addEventListener('input', (e) => { setPickerFromHex(e.target.value); sendControlThrottled({ color: e.target.value }); });
  $$('[data-temp]').forEach((b) => b.addEventListener('click', () => {
    $('#temp').value = b.dataset.temp; $('#tempVal').textContent = b.dataset.temp + '\u00a0K';
    sendControl({ temp: +b.dataset.temp });
  }));
  $('#onBtn').addEventListener('click', () => sendControl({ on: true }));
  $('#offBtn').addEventListener('click', () => sendControl({ on: false }));
  $('#allOffBtn').addEventListener('click', () => act(() => api('POST', '/api/control', { target: 'all', on: false }), 'All lights off'));
  $('#stopBtn').addEventListener('click', () => {
    const runs = runsForTarget();
    act(async () => { for (const r of runs) await api('POST', '/api/effects/stop', { id: r.id }); }, 'Effect stopped');
  });
  // track slider dragging so live state updates don't yank the thumb
  $$('input[type=range]').forEach((el) => {
    el.addEventListener('pointerdown', () => busy.add(el.id));
    const release = () => setTimeout(() => busy.delete(el.id), 400);
    el.addEventListener('pointerup', release);
    el.addEventListener('pointercancel', release);
    el.addEventListener('change', release);
  });
  updateColorUi();
}

/* ======================================================================== effects tab */
const speedToSlider = (s) => Math.round(1000 * Math.log(clamp(s, 0.1, 10) / 0.1) / Math.log(100));
const sliderToSpeed = (v) => +(0.1 * Math.pow(100, v / 1000)).toFixed(2);
function speedLabel(s) { return s >= 9.95 ? 'MAX' : '×' + (s < 1 ? s.toFixed(2) : s.toFixed(1)); }

function allPalettes() { return S.state?.palettes || []; }
function paletteById(id) { return allPalettes().find((p) => p.id === id); }

const updateRun = throttle((patch) => {
  const r = currentRun(); if (!r) return;
  act(() => api('POST', '/api/effects/update', { id: r.id, ...patch }));
}, 120);

function initEffects() {
  const cats = ['All', ...S.meta.categories];
  $('#fxCats').innerHTML = cats.map((c) => `<button class="chip ${c === S.fxCat ? 'active' : ''}" data-cat="${esc(c)}">${esc(c)}</button>`).join('');
  $('#fxCats').addEventListener('click', (e) => {
    const c = e.target.closest('[data-cat]')?.dataset.cat; if (!c) return;
    S.fxCat = c; $$('#fxCats .chip').forEach((b) => b.classList.toggle('active', b.dataset.cat === c));
    buildFxGrid();
  });
  $('#fxSearch').addEventListener('input', (e) => { S.fxSearch = e.target.value.toLowerCase(); buildFxGrid(); });
  $('#fxGrid').addEventListener('click', (e) => {
    const id = e.target.closest('[data-fx]')?.dataset.fx; if (id) startEffect(id);
  });

  $('#fxSpeed').addEventListener('input', (e) => {
    S.fx.speed = sliderToSpeed(+e.target.value);
    $('#fxSpeedVal').textContent = speedLabel(S.fx.speed);
    updateRun({ speed: S.fx.speed });
  });
  $('#fxBri').addEventListener('input', (e) => {
    S.fx.brightness = +e.target.value;
    $('#fxBriVal').textContent = S.fx.brightness + '%';
    updateRun({ brightness: S.fx.brightness });
  });
  $('#fxColor').addEventListener('input', (e) => {
    S.fx.color = e.target.value; store.set('fxColor', S.fx.color);
    $('#fxColorSwatch').style.background = S.fx.color;
    updateRun({ color: S.fx.color });
  });
  $('#fxPalette').addEventListener('change', (e) => choosePalette(e.target.value));
  $('#fxColorSwatch').style.background = S.fx.color;
  $('#fxColor').value = S.fx.color;
  setSpeedUi(S.fx.speed);
  buildFxGrid();
}

function setSpeedUi(s) {
  $('#fxSpeed').value = speedToSlider(s);
  paintSlider($('#fxSpeed'));
  $('#fxSpeedVal').textContent = speedLabel(s);
}

function choosePalette(id) {
  S.fx.palette = id; store.set('fxPalette', id);
  renderFxPaletteSelect(); renderPalettes();
  const r = currentRun();
  if (r && effectMeta(r.effect)?.uses.includes('palette')) updateRun({ palette: id });
}

function buildFxGrid() {
  const list = S.meta.effects.filter((e) =>
    (S.fxCat === 'All' || e.category === S.fxCat) &&
    (!S.fxSearch || (e.name + ' ' + e.description + ' ' + e.category).toLowerCase().includes(S.fxSearch)));
  $('#fxGrid').innerHTML = list.map((e) => {
    const cols = e.preview.length ? e.preview : ['#7c5cff', '#ff4fa3'];
    return `<button type="button" class="fx-card ${e.flashing ? 'flashing' : ''}" data-fx="${e.id}">
      ${strip(cols)}
      <span class="fx-body">
        <span class="fx-name">${esc(e.name)}<span class="fx-tag fx-running">Running</span></span>
        <span class="fx-desc">${esc(e.description)}</span>
        ${e.flashing ? `<span class="fx-flag">${icon('warning')}Flashing lights</span>` : ''}
      </span></button>`;
  }).join('') || '<p class="muted">No effects match.</p>';
  renderFxActive();
}

function renderFxActive() {
  const r = currentRun();
  $$('#fxGrid .fx-card').forEach((c) => c.classList.toggle('active', !!r && r.effect === c.dataset.fx));
  const m = r ? effectMeta(r.effect) : null;
  $('#fxColor').closest('.fx-ctrl').classList.toggle('disabled', !!m && !m.uses.includes('color'));
  $('#fxPalette').closest('.fx-ctrl').classList.toggle('disabled', !!m && !m.uses.includes('palette'));
}

/* when switching target, mirror the running effect's parameters in the sliders */
function syncFxFromRun(force) {
  const r = currentRun(); if (!r) return;
  if (!force && (busy.has('fxSpeed') || busy.has('fxBri'))) return;
  S.fx.speed = r.speed; setSpeedUi(r.speed);
  S.fx.brightness = r.brightness; $('#fxBri').value = r.brightness; $('#fxBriVal').textContent = r.brightness + '%';
  paintSliders();
}

function startEffect(id) {
  const m = effectMeta(id);
  if (!m) return;
  if (!targetLights().length) { toast('No lights in this target', 'error'); return; }
  if (m.flashing && S.state.settings.flash_warning) { flashWarning(() => doStartEffect(m)); return; }
  doStartEffect(m);
}
async function doStartEffect(m) {
  S.fx.speed = m.speed; setSpeedUi(m.speed);
  await act(() => api('POST', '/api/effects/start', {
    target: S.target, effect: m.id, speed: S.fx.speed, brightness: S.fx.brightness,
    color: S.fx.color, palette: S.fx.palette,
  }));
}

function renderFxPaletteSelect() {
  const sel = $('#fxPalette');
  const pals = allPalettes();
  const sig = pals.map((p) => p.id + p.name).join('|');
  if (sel.dataset.sig !== sig) {
    sel.innerHTML = `<optgroup label="Built-in">${pals.filter((p) => p.builtin).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</optgroup>` +
      (pals.some((p) => !p.builtin) ? `<optgroup label="My palettes">${pals.filter((p) => !p.builtin).map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</optgroup>` : '');
    sel.dataset.sig = sig;
  }
  if (!paletteById(S.fx.palette)) S.fx.palette = 'rainbow';
  sel.value = S.fx.palette;
  const p = paletteById(S.fx.palette);
  $('#fxPalettePreview').innerHTML = p ? segs(p.colors) : '';
}

/* ======================================================================== scenes tab */
function initScenes() {
  $('#sceneGrid').innerHTML = S.meta.scenes.map((s) =>
    `<button type="button" class="scene-btn" data-scene="${s.id}">${strip(SCENE_COLORS[s.id] || ['#888888'])}
      <span>${esc(s.name)}</span><small>${s.dynamic ? 'Dynamic' : 'Static'}</small></button>`).join('');
  $('#sceneGrid').addEventListener('click', (e) => {
    const id = e.target.closest('[data-scene]')?.dataset.scene; if (!id) return;
    sendControl({ scene: +id, speed: +$('#sceneSpeed').value });
  });
  const speedSend = throttle(() => {
    const ls = targetLights().filter((l) => l.state?.sceneId);
    if (ls.length) sendControl({ scene: ls[0].state.sceneId, speed: +$('#sceneSpeed').value });
  }, 300);
  $('#sceneSpeed').addEventListener('input', (e) => { $('#sceneSpeedVal').textContent = e.target.value; speedSend(); });
}
function renderScenesActive() {
  const ls = targetLights();
  const ids = new Set(ls.filter((l) => l.state?.state && !l.effect).map((l) => l.state.sceneId));
  const one = ids.size === 1 ? [...ids][0] : null;
  $$('#sceneGrid .scene-btn').forEach((b) => b.classList.toggle('active', one != null && +b.dataset.scene === one));
}

/* ======================================================================== palettes tab */
function renderPalettes() {
  const pals = allPalettes();
  const sig = pals.map((p) => p.id + p.name + p.colors.join()).join('|') + S.fx.palette;
  const grid = $('#paletteGrid');
  if (grid.dataset.sig === sig) return;
  grid.dataset.sig = sig;
  grid.innerHTML = pals.map((p) => `
    <div class="pal-card ${p.id === S.fx.palette ? 'active' : ''}" data-pal="${p.id}">
      ${strip(p.colors)}
      <div class="pal-body"><b>${esc(p.name)}</b>
        <span class="row gap">${p.builtin ? '<span class="muted small">built-in</span>' :
          `<button type="button" class="mini icon-only" data-pal-edit="${p.id}" title="Edit" aria-label="Edit ${esc(p.name)}">${icon('edit')}</button><button type="button" class="mini icon-only" data-pal-del="${p.id}" title="Delete" aria-label="Delete ${esc(p.name)}">${icon('trash')}</button>`}</span></div>
      <div class="pal-actions">
        <button class="mini" data-pal-fx="palette_fade">Fade</button>
        <button class="mini" data-pal-fx="palette_wave">Wave</button>
        <button class="mini" data-pal-fx="palette_twinkle">Twinkle</button>
        <button class="mini" data-pal-fx="color_chase">Chase</button>
        <button class="mini" data-pal-fx="bass_pulse">Pulse</button>
        <button class="mini" data-pal-fx="palette_strobe">Strobe</button>
      </div>
    </div>`).join('');
}
function initPalettes() {
  $('#paletteGrid').addEventListener('click', (e) => {
    const card = e.target.closest('[data-pal]'); if (!card) return;
    const id = card.dataset.pal;
    if (e.target.dataset.palEdit) { paletteModal(paletteById(id)); return; }
    if (e.target.dataset.palDel) {
      if (confirm('Delete this palette?')) act(() => api('DELETE', '/api/palettes/' + id), 'Palette deleted');
      return;
    }
    choosePalette(id);
    if (e.target.dataset.palFx) startEffect(e.target.dataset.palFx);
    else toast(`Palette "${paletteById(id)?.name}" selected for palette effects`);
  });
  $('#newPaletteBtn').addEventListener('click', () => paletteModal(null));
}

/* ======================================================================== modals */
function openModal(title, body, buttons = []) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = body;
  const foot = $('#modalFoot');
  foot.innerHTML = '';
  for (const b of buttons) {
    const el = document.createElement('button');
    el.className = 'btn ' + (b.cls || '') + (b.left ? ' left' : '');
    el.textContent = b.label;
    el.addEventListener('click', async () => {
      if (!b.onClick) { closeModal(); return; }
      el.disabled = true;
      try { const keep = await b.onClick(); if (keep !== false) closeModal(); }
      catch (e) { toast(e.message || String(e), 'error'); }
      finally { el.disabled = false; }
    });
    foot.appendChild(el);
  }
  $('#modal').classList.remove('hidden');
  paintSliders();
  setTimeout(() => $('#modalBody input:not([type=checkbox]):not([type=color]), #modalBody textarea')?.focus(), 30);
}
function closeModal() { $('#modal').classList.add('hidden'); }

function parseIpLines(text) {
  return text.split(/[\n,;]+/).map((l) => l.trim()).filter(Boolean).map((l) => {
    const m = l.match(/^(\d{1,3}(?:\.\d{1,3}){3})\s*(.*)$/);
    return m ? { ip: m[1], name: m[2].trim() || undefined } : { ip: l };
  });
}
async function addIps(text) {
  const items = parseIpLines(text);
  if (!items.length) throw new Error('Enter at least one IP address');
  let ok = 0; const errs = [];
  for (const it of items) {
    try { await api('POST', '/api/lights', it); ok++; } catch (e) { errs.push(e.message); }
  }
  if (ok) toast(`Added ${ok} light${ok === 1 ? '' : 's'}`, 'ok');
  errs.forEach((m) => toast(m, 'error'));
  return ok > 0 || !errs.length;
}

function addLightsModal() {
  openModal('Add lights', `
    <div class="field"><label for="mIps">IP addresses, one per line, optionally followed by a name</label>
      <textarea id="mIps" name="ips" rows="6" placeholder="192.168.1.20 Desk lamp&#10;192.168.1.21 Ceiling front&#10;192.168.1.22"></textarea></div>
    <p class="muted small">Tip: give your lights fixed IPs (DHCP reservation) in your router so they never move. If an IP changes anyway, Discover will find the bulb again by its MAC address.</p>`,
  [{ label: 'Cancel' }, { label: 'Add', cls: 'primary', onClick: () => addIps($('#mIps').value) }]);
}

function discoverModal() {
  openModal('Discover lights', `
    <p class="muted">Scans your local network: a broadcast plus a direct probe of every address on your subnet. Takes about 4 seconds.</p>
    <div class="field"><label for="mNets">Extra networks to scan (optional, e.g. 192.168.50.0/24)</label>
      <input type="text" id="mNets" placeholder="192.168.50.0/24" value="${esc(store.get('nets', ''))}"></div>
    <div id="mDiscRes"></div>`,
  [{ label: 'Close' }, {
    label: 'Scan now', cls: 'primary', onClick: async () => {
      const nets = $('#mNets').value.split(/[\s,]+/).filter(Boolean);
      store.set('nets', $('#mNets').value);
      $('#mDiscRes').innerHTML = '<p class="muted">Scanning…</p>';
      const r = await api('POST', '/api/discover', { networks: nets });
      $('#mDiscRes').innerHTML = `<dl class="facts"><dt>Found</dt><dd>${r.found} light(s)</dd><dt>New</dt><dd>${r.added.length}</dd><dt>IP updated</dt><dd>${r.updated.length}</dd></dl>
        ${r.found === 0 ? '<p class="muted small">Nothing answered. Check that this PC is on the same network/VLAN as the lights, that Windows Firewall allows the app (Private network), or add them by IP.</p>' : ''}`;
      if (r.added.length) toast(`Added ${r.added.length} new light(s)`, 'ok');
      return false;
    },
  }]);
}

function lightModal(id) {
  const l = lightsById()[id]; if (!l) return;
  openModal('Edit light', `
    <div class="field"><label for="mName">Name</label><input type="text" id="mName" name="mName" value="${esc(l.name)}"></div>
    <div class="field"><label for="mIp">IP address</label><input type="text" id="mIp" name="mIp" value="${esc(l.ip)}"></div>
    <dl class="facts">
      <dt>Status</dt><dd>${l.online === true ? 'Online' : l.online === false ? 'Offline' : 'Unknown'}</dd>
      <dt>MAC</dt><dd>${esc(l.mac || 'Unknown')}</dd>
      <dt>Module</dt><dd>${esc(l.module || 'Unknown')}</dd>
      <dt>Type</dt><dd>${esc({ rgb: 'Full color + white', tw: 'Tunable white', dw: 'Dimmable white', socket: 'Smart plug', unknown: 'Unknown' }[l.kind])}</dd>
    </dl>`,
  [
    { label: 'Delete', cls: 'danger', left: true, onClick: async () => {
      if (!confirm(`Remove "${l.name}"?`)) return false;
      await api('DELETE', '/api/lights/' + id); toast('Light removed');
    } },
    { label: 'Identify', onClick: async () => { await api('POST', `/api/lights/${id}/identify`); return false; } },
    { label: 'Save', cls: 'primary', onClick: async () => {
      await api('PATCH', '/api/lights/' + id, { name: $('#mName').value, ip: $('#mIp').value }); toast('Saved', 'ok');
    } },
  ]);
}

function groupModal(gid) {
  const g = gid ? S.state.groups.find((x) => x.id === gid) : null;
  const members = new Set(g ? g.lights : []);
  // members first, in their saved order (that order drives chase/wave effects)
  const lights = S.state.lights.slice().sort((a, b) => {
    const ia = g ? g.lights.indexOf(a.id) : -1, ib = g ? g.lights.indexOf(b.id) : -1;
    return (ia < 0 ? 1e9 : ia) - (ib < 0 ? 1e9 : ib);
  });
  openModal(g ? 'Edit group' : 'New group', `
    <div class="field"><label for="mName">Name</label><input type="text" id="mName" name="mName" value="${esc(g?.name || '')}" placeholder="e.g. Front lights, Living room…"></div>
    <div class="field"><label class="row between"><span>Lights</span>
      <span><button class="mini" id="mAll" type="button">All</button> <button class="mini" id="mNone" type="button">None</button></span></label>
      <div class="check-list">${lights.map((l) => `
        <label class="check-row"><input type="checkbox" value="${l.id}" ${members.has(l.id) ? 'checked' : ''}>
          ${bulbHtml(l)}<span class="name">${esc(l.name)} <span class="muted small">${esc(l.ip)}</span></span></label>`).join('') || '<p class="empty-note">Add some lights first.</p>'}
      </div>
      <p class="muted small">Chase / wave effects run through the lights in the order they were added to the group.</p>
    </div>`,
  [
    ...(g ? [{ label: 'Delete group', cls: 'danger', left: true, onClick: async () => {
      if (!confirm(`Delete group "${g.name}"? (The lights stay.)`)) return false;
      await api('DELETE', '/api/groups/' + g.id); toast('Group deleted');
    } }] : []),
    { label: 'Cancel' },
    { label: g ? 'Save' : 'Create group', cls: 'primary', onClick: async () => {
      const prev = g ? g.lights : [];
      const checked = $$('.check-list input:checked').map((i) => i.value);
      const ordered = [...prev.filter((i) => checked.includes(i)), ...checked.filter((i) => !prev.includes(i))];
      const body = { name: $('#mName').value, lights: ordered };
      if (g) await api('PATCH', '/api/groups/' + g.id, body);
      else {
        const ng = await api('POST', '/api/groups', body);
        S.state = await api('GET', '/api/state'); // so the new group exists before selecting it
        selectTarget('group:' + ng.id);
      }
      toast(g ? 'Group saved' : 'Group created', 'ok');
    } },
  ]);
  $('#mAll')?.addEventListener('click', () => $$('.check-list input').forEach((i) => { i.checked = true; }));
  $('#mNone')?.addEventListener('click', () => $$('.check-list input').forEach((i) => { i.checked = false; }));
}

function paletteModal(p) {
  let colors = p ? [...p.colors] : ['#ff0080', '#7c5cff', '#00e5ff'];
  openModal(p ? 'Edit palette' : 'New palette', `
    <div class="field"><label for="mName">Name</label><input type="text" id="mName" name="mName" value="${esc(p?.name || '')}" placeholder="My palette"></div>
    <div class="field"><label>Colors</label><div id="mColors" class="color-list"></div></div>
    <div class="field"><label>Preview</label><div id="mPrev" class="pal-strip pal-strip-l"></div></div>`,
  [{ label: 'Cancel' }, { label: 'Save palette', cls: 'primary', onClick: async () => {
    const saved = await api('POST', '/api/palettes', { id: p?.id, name: $('#mName').value, colors });
    choosePalette(saved.id); toast('Palette saved', 'ok');
  } }]);
  const draw = () => {
    $('#mColors').innerHTML = colors.map((c, i) => `
      <div class="color-item"><label class="color-native"><input type="color" value="${c}" data-i="${i}"><span style="background:${c}"></span></label>
      ${colors.length > 1 ? `<button type="button" class="rm" data-rm="${i}" title="Remove" aria-label="Remove color ${i + 1}">${icon('close')}</button>` : ''}</div>`).join('') +
      (colors.length < 16 ? `<button type="button" class="add-color" id="mAdd" title="Add color" aria-label="Add color">${icon('plus')}</button>` : '');
    $('#mPrev').innerHTML = segs(colors);
  };
  draw();
  $('#mColors').addEventListener('input', (e) => {
    const i = e.target.dataset.i; if (i == null) return;
    colors[+i] = e.target.value; e.target.nextElementSibling.style.background = e.target.value;
    $('#mPrev').innerHTML = segs(colors);
  });
  $('#mColors').addEventListener('click', (e) => {
    if (e.target.dataset.rm != null) { colors.splice(+e.target.dataset.rm, 1); draw(); }
    else if (e.target.id === 'mAdd') { colors.push(hsvToHex(Math.random() * 360, 1, 1)); draw(); }
  });
}

function settingsModal() {
  const s = S.state.settings;
  openModal('Settings', `
    <div class="field"><label class="row between"><span>Max effect update rate (per light)</span><span id="mRateVal" class="val">${s.max_rate}/s</span></label>
      <input type="range" id="mRate" min="2" max="50" value="${s.max_rate}" class="slider">
      <p class="muted small">How many commands per second each bulb gets during effects. This caps how fast strobes can go. WiZ bulbs handle ~20/s reliably; above ~30/s many start lagging or dropping frames. A strobe needs two updates per flash, so 20/s = 10 flashes per second.</p></div>
    <div class="field"><label class="row between"><span>Status refresh interval</span><span id="mPollVal" class="val">${s.poll_interval}s</span></label>
      <input type="range" id="mPoll" min="2" max="60" value="${s.poll_interval}" class="slider"></div>
    <div class="toggle-row"><span>Restore lights when an effect is stopped<br><span class="muted small">Puts each light back to how it was before the effect started</span></span>
      <label class="switch"><input type="checkbox" id="mRestore" ${s.restore_on_stop ? 'checked' : ''}><span></span></label></div>
    <div class="toggle-row"><span>Photosensitivity warning<br><span class="muted small">Ask before starting effects with rapid flashing</span></span>
      <label class="switch"><input type="checkbox" id="mFlash" ${s.flash_warning ? 'checked' : ''}><span></span></label></div>
    <dl class="facts"><dt>Version</dt><dd>${esc(S.meta.version)}</dd><dt>Config file</dt><dd>${esc(S.meta.config_path)}</dd></dl>
    <p class="muted small">Want to control the lights from your phone? Start the app with <code>--host 0.0.0.0</code> and open http://&lt;this-pc-ip&gt;:8765 on the phone.</p>`,
  [{ label: 'Cancel' }, { label: 'Save', cls: 'primary', onClick: async () => {
    await api('PATCH', '/api/settings', {
      max_rate: +$('#mRate').value, poll_interval: +$('#mPoll').value,
      restore_on_stop: $('#mRestore').checked, flash_warning: $('#mFlash').checked,
    });
    toast('Settings saved', 'ok');
  } }]);
  $('#mRate').addEventListener('input', (e) => { $('#mRateVal').textContent = e.target.value + '/s'; });
  $('#mPoll').addEventListener('input', (e) => { $('#mPollVal').textContent = e.target.value + 's'; });
}

function flashWarning(onOk) {
  openModal('Photosensitivity warning', `
    <div class="warn-box"><p><b>This effect produces rapid flashing lights.</b></p>
    <p>Flashing lights can trigger seizures in people with photosensitive epilepsy and cause discomfort, headaches or disorientation for others. Make sure everyone in the room is OK with it, and don’t stare directly at the bulbs.</p></div>
    <label class="check-row"><input type="checkbox" id="mNoWarn" name="noWarn"> Don’t warn me again</label>`,
  [{ label: 'Cancel' }, { label: 'I understand, start', cls: 'primary', onClick: async () => {
    if ($('#mNoWarn').checked) await api('PATCH', '/api/settings', { flash_warning: false });
    onOk();
  } }]);
}

/* ======================================================================== wiring */
function initShell() {
  $('#tabs').addEventListener('click', (e) => {
    const t = e.target.closest('[data-tab]')?.dataset.tab; if (t) showTab(t);
  });
  $('#sidebar').addEventListener('click', (e) => {
    if (e.target.dataset.toggle) return; // switch
    if (e.target.closest('.switch')) return;
    const eg = e.target.dataset.editGroup; if (eg) { groupModal(eg); return; }
    const el = e.target.dataset.editLight; if (el) { lightModal(el); return; }
    const t = e.target.closest('[data-target]')?.dataset.target; if (t) selectTarget(t);
  });
  $('#sidebar').addEventListener('keydown', (e) => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.side-item')) { e.preventDefault(); selectTarget(e.target.dataset.target); }
  });
  $('#sidebar').addEventListener('change', (e) => {
    const id = e.target.dataset.toggle; if (!id) return;
    act(() => api('POST', '/api/control', { target: 'light:' + id, on: e.target.checked }));
  });
  $('#newGroupBtn').addEventListener('click', () => groupModal(null));
  $('#addLightBtn').addEventListener('click', addLightsModal);
  $('#discoverBtn').addEventListener('click', discoverModal);
  $('#settingsBtn').addEventListener('click', settingsModal);
  $('#welcomeAdd').addEventListener('click', () => act(() => addIps($('#welcomeIps').value)));
  $('#welcomeDiscover').addEventListener('click', discoverModal);
  $('#modalClose').addEventListener('click', closeModal);
  $('#modal').addEventListener('mousedown', (e) => { if (e.target.id === 'modal') closeModal(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
    if (e.key === ' ' && e.target === document.body && currentRun()) { e.preventDefault(); $('#stopBtn').click(); }
  });
  $('#menuBtn').addEventListener('click', () => document.body.classList.toggle('drawer'));
  $('#scrim').addEventListener('click', () => document.body.classList.remove('drawer'));
}
function showTab(t) {
  S.tab = t; store.set('tab', t);
  $$('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === t));
  $$('.tab').forEach((s) => s.classList.toggle('hidden', s.id !== 'tab-' + t));
}

async function boot() {
  initShell();
  initControls();
  for (;;) {
    try { S.meta = await api('GET', '/api/meta'); break; }
    catch { setConn(false); await new Promise((r) => setTimeout(r, 1500)); }
  }
  initEffects();
  initScenes();
  initPalettes();
  showTab(S.tab);
  try { S.state = await api('GET', '/api/state'); } catch { /* ws will deliver */ }
  render(true);
  syncFxFromRun(true);
  connect();
}
boot();
