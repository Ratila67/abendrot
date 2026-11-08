import * as store from './store.js';
import { sunTime, sunPosition, effectiveSunset } from './sun.js';
import { hav, parseGPX, densify, buildTrack, din, nearestIndex, segment, pointAt } from './geo.js';
import { fetchForecast, fetchElevations, hourIndex, scoreSunset, scoreLabel } from './weather.js';
import * as X from './sources.js';
import * as MAP from './map.js';
import { loadModel, modelInfo, modelFeatures, predict } from './model.js';

/* ---------- utilitaires ---------- */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const app = $('#app');
const pad = n => String(n).padStart(2, '0');
const nf = (x, d = 1) => Number(x).toLocaleString('fr-FR', { maximumFractionDigits: d });
const hm = d => d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '–';
const dur = m => { m = Math.round(m); const h = Math.floor(m / 60), r = m % 60; return h ? `${h} h${r ? ' ' + pad(r) : ''}` : `${r} min`; };
const addMin = (d, m) => new Date(d.getTime() + m * 6e4);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const todayStr = () => ymd(new Date());
const noon = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d, 12); };
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const ic = (n, cls = '') => `<svg class="ic ${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const buzz = () => { try { navigator.vibrate?.(8); } catch { /* */ } };
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast.k); toast.k = setTimeout(() => t.classList.remove('show'), 3800); }

const HOME = { lat: 48.5734, lon: 7.7521, name: 'Strasbourg' };
const DIFF = ['', 'Facile', 'Moyen', 'Engagé'];
const SRC = { gpx: 'Trace GPX', import: 'Ta trace', schematic: 'Tracé approximatif' };

const S = { hikes: [], date: todayStr(), pos: null, filter: 'all', sort: 'smart', q: '', saved: { fav: [], done: [] }, pace: 1.25, home: 'gps', stay: false,
  wx: null, wxErr: null, base: 'ign', token: 0, cur: null, settings: { sunsethue: '', windy: '', proxy: '' } };
const trackCache = new Map();
let map = null, mapMarkers = [];
const isToday = (d = S.date) => d === todayStr();

/* ---------- traces ---------- */
const sunsetSpot = h => h.spots.find(s => s.sunset) || null;
const wxPoint = h => sunsetSpot(h) || h.spots[0] || h.start;

async function getTrack(h) {
  if (trackCache.has(h.id)) return trackCache.get(h.id);
  let pts = null, src = 'schematic', text = null;
  try { text = await store.get('gpx:' + h.id); } catch { /* */ }
  if (text) src = 'import';
  else if (h.gpx) { try { const r = await fetch('./gpx/' + h.gpx); if (r.ok) { text = await r.text(); src = 'gpx'; } } catch { /* */ } }
  if (text) { try { pts = parseGPX(text); } catch (e) { toast(`${e.message}, tracé approximatif utilisé.`); src = 'schematic'; pts = null; } }
  if (!pts) pts = densify(h.waypoints);
  if (src === 'schematic' || pts.some(p => p[2] == null)) {
    const key = `elev:${h.id}:${src}:${pts.length}`;
    let el = null;
    try { el = await store.get(key); } catch { /* */ }
    if (!el) { try { el = await fetchElevations(pts); store.set(key, el).catch(() => {}); } catch { el = null; } }
    if (el && el.length === pts.length) pts = pts.map((p, i) => [p[0], p[1], (src === 'schematic' || p[2] == null) ? el[i] : p[2]]);
  }
  const T = buildTrack(pts, { threshold: src === 'schematic' ? 8 : 4 });
  T.src = src; trackCache.set(h.id, T);
  return T;
}

/* ---------- calculs ---------- */
function metrics(h, T) {
  const f = S.pace;
  if (T && T.src !== 'schematic') {
    const spot = sunsetSpot(h);
    let toSpot = null, down = null, spotGap = 0;
    if (spot) {
      const n = nearestIndex(T, spot.lat, spot.lon, true);
      const a = segment(T, 0, n.i), b = segment(T, n.i, T.pts.length - 1);
      toSpot = din(a.km, a.up, a.down, f); down = din(b.km, b.up, b.down, f); spotGap = n.d;
    }
    return { km: T.km, dplus: T.dplus, total: din(T.km, T.dplus, T.dminus, f), toSpot, down, real: true, spotGap };
  }
  const k = f / 1.25;
  return { km: h.km, dplus: h.dplus, total: h.dur * k, toSpot: h.toSpot * k, down: h.down * k, real: false, spotGap: 0 };
}
// Pic de couleur : PEAK_AFTER min après le coucher géométrique (horizon 0), comme les fenêtres de truth/capture.py.
// Valeur moyenne en attendant peak_minutes_from_sunset mesuré par spot dans observations.csv.
const PEAK_AFTER = 15, LEAVE_AFTER = 10;
function plan(h, m, date = S.date, stay = S.stay) {
  const spot = sunsetSpot(h), p = spot || h.start, day = noon(date);
  const set = effectiveSunset(day, p.lat, p.lon, p.alt, h.horizon || 0);
  const peak = addMin(effectiveSunset(day, p.lat, p.lon, p.alt, 0), PEAK_AFTER);
  const gold = sunTime(day, p.lat, p.lon, 6), dusk = sunTime(day, p.lat, p.lon, -6);
  const sunsetHike = !!spot && h.sunsetScore >= 2;
  let dep, arrive = null, back;
  // Au spot jusqu'à coucher + 10 min, ou jusqu'au pic + 5 min si on reste pour les couleurs.
  const leaveSpot = stay ? new Date(Math.max(addMin(set, LEAVE_AFTER), addMin(peak, 5))) : addMin(set, LEAVE_AFTER);
  if (sunsetHike) { arrive = addMin(set, -20); dep = addMin(arrive, -m.toSpot); back = addMin(leaveSpot, m.down); }
  else { back = addMin(dusk, -30); dep = addMin(back, -m.total); }
  return { spot, set, peak, leaveSpot, gold, dusk, dep, arrive, back, sunsetHike, night: Math.max(0, (back - dusk) / 6e4), az: sunPosition(set, p.lat, p.lon).azimuth };
}
const origin = () => (S.home === 'gps' && S.pos) ? S.pos : HOME;
const originName = () => (S.home === 'gps' && S.pos) ? 'ta position' : HOME.name;
const road = h => { const o = origin(), km = hav(o.lat, o.lon, h.start.lat, h.start.lon) * 1.3; return { km, min: km / 65 * 60 + 8 }; };

const daysAhead = (d = S.date) => Math.round((noon(d) - noon(todayStr())) / 864e5);
function wxFor(h, p, date = S.date) {
  if (!S.wx) return null;
  const s = S.wx.spots[S.hikes.indexOf(h)];
  const k = hourIndex(s, p.sunsetHike ? p.set : addMin(p.dep, 120));
  if (k < 0) return null;
  const alt = wxPoint(h).alt, r = scoreSunset(s, S.wx.plain, k, alt);
  const feats = modelFeatures({ series: s, plain: S.wx.plain, k, alt, heuristic: r.score, leadDay: daysAhead(date), date });
  const prob = predict('good', feats), sea = alt >= 850 ? predict('sea', feats) : null;
  return { ...r, series: s, k, model: s.hourly.model?.[k], feats, prob, sea, display: prob != null ? Math.round(prob * 100) : r.score };
}
const scoreCls = sc => sc >= 75 ? 'hot' : sc >= 50 ? 'mid' : 'cold';
const ring = (v, size = 44, cls = '') => {
  const r = (size - 6) / 2, c = 2 * Math.PI * r, o = size / 2;
  return `<svg class="ring ${scoreCls(v)} ${cls}" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="Score coucher ${v} sur 100"><circle cx="${o}" cy="${o}" r="${r}" class="track"/><circle cx="${o}" cy="${o}" r="${r}" class="val" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - v / 100)).toFixed(1)}" transform="rotate(-90 ${o} ${o})"/><text x="50%" y="50%" dy=".35em" text-anchor="middle">${v}</text></svg>`;
};

/** Tout ce qu'il faut pour classer et afficher une rando à une date (par défaut, celle choisie). */
function evaluate(h, date = S.date) {
  const m = metrics(h, trackCache.get(h.id)), p = plan(h, m, date), w = wxFor(h, p, date), rd = road(h);
  const leave = addMin(p.dep, -rd.min);
  const late = isToday(date) && leave.getTime() < Date.now() - 10 * 6e4;
  const base = p.sunsetHike ? (w ? w.display : h.sunsetScore * 16) : 10;
  const fav = S.saved.fav.includes(h.id);
  return { h, m, p, w, rd, leave, late, fav, value: base - rd.min * 0.12 + (fav ? 4 : 0) - (late ? 1000 : 0) };
}
const leaveTxt = t => {
  if (!isToday()) return `Pars à ${hm(new Date(t))}`;
  const m = Math.round((t - Date.now()) / 6e4);
  return m <= 0 ? 'Pars maintenant' : m < 60 ? `Pars dans ${m} min` : `Pars dans ${dur(m)}`;
};
setInterval(() => $$('[data-leave]').forEach(el => { el.textContent = leaveTxt(+el.dataset.leave); }), 30000);

/* ---------- en-tête et jours ---------- */
function days() {
  const out = [], t = new Date();
  for (let i = 0; i < 7; i++) {
    const d = new Date(t); d.setDate(t.getDate() + i);
    out.push({ s: ymd(d), l: i === 0 ? 'Ce soir' : i === 1 ? 'Demain' : d.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric' }) });
  }
  if (!out.some(d => d.s === S.date)) out.push({ s: S.date, l: noon(S.date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' }) });
  return out;
}
function headerHTML(title, withDays = true) {
  return `<header class="hd">
    <div class="hd-row"><h1>${title}</h1>
      <div class="hd-actions">
        <button class="icbtn${S.pos ? ' on' : ''}" id="locBtn" type="button" aria-label="Me localiser">${ic('locate')}</button>
        <button class="icbtn" id="setBtn" type="button" aria-label="Réglages">${ic('gear')}</button>
      </div></div>
    ${withDays ? `<div class="days" role="tablist" aria-label="Date du coucher">
      ${days().map(d => `<button class="day" type="button" role="tab" data-d="${d.s}" aria-selected="${d.s === S.date}">${d.l}</button>`).join('')}
      <button class="day cal" type="button" id="calBtn" aria-label="Choisir une autre date">${ic('cal')}</button>
      <input type="date" id="dateIn" class="vh" tabindex="-1" aria-hidden="true">
    </div>` : ''}</header>`;
}
function bindHeader() {
  $('#locBtn').onclick = locate;
  $('#setBtn').onclick = openSettings;
  $$('.day[data-d]').forEach(b => b.onclick = () => { S.date = b.dataset.d; buzz(); route(); });
  const di = $('#dateIn'); if (!di) return;
  di.value = S.date;
  $('#calBtn').onclick = () => { try { di.showPicker(); } catch { di.style.pointerEvents = 'auto'; di.focus(); di.click(); } };
  di.onchange = e => { if (e.target.value) { S.date = e.target.value; route(); } };
  $('.day[aria-selected="true"]')?.scrollIntoView?.({ inline: 'center', block: 'nearest' });
}

/* ---------- liste ---------- */
const FILTERS = [['all', 'Tout'], ['sun', 'Meilleurs spots'], ['short', 'Moins de 2 h 30'], ['nosnow', 'Sans neige']];
const GOOD = 40;   // score en dessous duquel l’app ne pousse plus à partir
const weekDays = () => days().slice(0, 7);
const dayName = s => s === weekDays()[1]?.s ? 'demain' : noon(s).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric' });
const upFirst = t => t.charAt(0).toUpperCase() + t.slice(1);
/** Coucher atteignable le mieux noté à une date, ou null si pas de prévision. */
const bestOf = (E) => E.filter(e => e.p.sunsetHike && e.w && !e.late).sort((a, b) => b.w.display - a.w.display || b.value - a.value)[0] || null;
/** Meilleure soirée des 7 prochains jours, hors date courante : { s, e } ou null. */
function bestAhead(except) {
  return weekDays().filter(d => d.s !== except).map(d => ({ s: d.s, e: bestOf(S.hikes.map(h => evaluate(h, d.s))) }))
    .filter(x => x.e).sort((a, b) => b.e.w.display - a.e.w.display)[0] || null;
}
function nextHTML(a) {
  if (!a) return '';
  const good = a.e.w.display >= GOOD;
  return `<a class="next" href="#${a.e.h.id}" data-date="${a.s}">
      ${ring(a.e.w.display, 46)}
      <span><span class="next-k">${good ? 'Meilleure soirée en vue' : `Rien au-dessus de ${GOOD} sur 7 jours, le moins mauvais`}</span>
      <strong>${esc(a.e.h.name)}</strong>
      <span class="next-d">${upFirst(dayName(a.s))}, coucher à ${hm(a.e.p.set)}</span></span></a>`;
}
/** Accueil sobre quand aucun coucher ne vaut la route : on ne pousse pas à partir, on montre quand ça vaudra le coup. */
function calmHeroHTML(title, line) {
  return `<div class="hero calm">
    <span class="hero-k">${isToday() ? 'Ce soir' : upFirst(noon(S.date).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' }))}</span>
    <span class="hero-name">${title}</span>
    <p class="hero-line">${line}</p>
    ${nextHTML(bestAhead(S.date))}
    <a class="hero-link" href="#semaine">${ic('cal')}Voir toute la semaine</a>
  </div>`;
}
function heroHTML(E) {
  const best = E.filter(e => e.p.sunsetHike && !e.late).sort((a, b) => b.value - a.value)[0];
  const when = isToday() ? 'Ce soir' : noon(S.date).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });
  if (!best) return calmHeroHTML(`Plus aucun coucher atteignable${isToday() ? ' ce soir' : ''}`, 'Trop tard pour rejoindre un spot avant le coucher.');
  const top = S.wx && !S.wxErr ? bestOf(E) : null;
  if (top && top.w.display < GOOD) return calmHeroHTML('Pas de beau coucher en vue', `${top.w.display} sur 100 au mieux, à ${esc(top.h.name)}. Pas la peine de faire la route.`);
  const { h, p, w, leave } = best;
  return `<a class="hero" href="#${h.id}">
    <span class="hero-k">${when}, le meilleur plan</span>
    <span class="hero-name">${esc(h.name)}</span>
    ${w ? ring(w.display, 52) : ''}
    <span class="hero-line">Coucher à ${hm(p.set)} depuis ${esc(p.spot.name)}</span>
    <span class="hero-go">${ic('car')}<strong data-leave="${leave.getTime()}">${leaveTxt(leave.getTime())}</strong><span>de ${originName()}, parking à ${hm(p.dep)}</span></span>
  </a>`;
}
function rowHTML(e) {
  const { h, m, p, w, late, fav } = e;
  const lead = p.sunsetHike && w ? ring(w.display, 44) : `<span class="lead">${p.sunsetHike ? `<span><b>${hm(p.set)}</b>coucher</span>` : `<span>${ic('boot')}<br>journée</span>`}</span>`;
  const time = p.sunsetHike ? hm(p.dep) : `${hm(p.dep)}`;
  const cap = late ? 'trop tard' : p.sunsetHike ? 'au parking' : 'départ max';
  return `<li><a class="row${late ? ' late' : ''}" href="#${h.id}">
    ${lead}
    <span><span class="row-title">${esc(h.name)}${fav ? ic('star', 'fill') : ''}</span>
      <span class="row-meta">${nf(m.km)} km, ${Math.round(m.dplus)} m, ${dur(m.total)}</span>
      <span class="row-meta">${esc(h.zone)}</span></span>
    <span class="row-end"><span class="row-time">${time}</span><span class="row-cap">${cap}</span></span>
  </a></li>`;
}
function viewList(onlyFav) {
  document.body.classList.remove('is-detail');
  const q = S.q.trim().toLowerCase();
  let E = S.hikes.map(h => evaluate(h));
  const heroE = E;
  if (onlyFav) E = E.filter(e => e.fav);
  if (q) E = E.filter(e => `${e.h.name} ${e.h.zone} ${e.h.merged}`.toLowerCase().includes(q));
  if (S.filter === 'sun') E = E.filter(e => e.h.sunsetScore >= 4);
  if (S.filter === 'short') E = E.filter(e => e.m.total <= 150);
  if (S.filter === 'nosnow') E = E.filter(e => !e.h.snow);
  const by = { smart: (a, b) => b.value - a.value, dist: (a, b) => a.rd.km - b.rd.km, dur: (a, b) => a.m.total - b.m.total };
  E.sort(by[S.sort] || by.smart);
  const banner = S.wxErr ? `<p class="banner">${ic('warn')} Météo indisponible, classement selon la qualité des spots.</p>` : '';
  app.innerHTML = `${headerHTML(onlyFav ? 'Favoris' : 'Abendrot')}
    ${onlyFav ? '' : heroHTML(heroE)}${banner}
    <div class="tools">
      <label class="search">${ic('search')}<input id="q" type="search" placeholder="Lac, château, sommet" value="${esc(S.q)}" aria-label="Chercher une rando"></label>
      <div class="chips" role="group" aria-label="Filtres">${FILTERS.map(([k, l]) => `<button type="button" class="chip" data-f="${k}" aria-pressed="${S.filter === k}">${l}</button>`).join('')}</div>
      <div class="sortrow"><span>${E.length} rando${E.length > 1 ? 's' : ''}</span>
        <select id="sort" aria-label="Trier"><option value="smart">Pertinence</option><option value="dist">Distance</option><option value="dur">Durée</option></select></div>
    </div>
    ${E.length ? `<ul class="list">${E.map(rowHTML).join('')}</ul>` : `<p class="empty">${onlyFav ? 'Touche l’étoile d’une fiche pour la retrouver ici.' : 'Aucun résultat avec ces filtres.'}</p>`}`;
  bindHeader();
  $('#sort').value = S.sort;
  $('#sort').onchange = e => { S.sort = e.target.value; route(); };
  $('#q').oninput = e => { S.q = e.target.value; const c = e.target.selectionStart; route(); const n = $('#q'); n.focus(); n.setSelectionRange(c, c); };
  $$('.chip').forEach(b => b.onclick = () => { S.filter = S.filter === b.dataset.f ? 'all' : b.dataset.f; route(); });
}

/* ---------- fiche ---------- */
/** Écarte des centres triés (en %) d'au moins `gap`, dans [lo, hi] : les libellés ne se chevauchent plus. */
function spread(xs, gap, lo, hi) {
  const x = [...xs];
  for (let i = 0; i < x.length; i++) x[i] = Math.max(x[i], i ? x[i - 1] + gap : lo);
  for (let i = x.length - 1; i >= 0; i--) x[i] = Math.min(x[i], i < x.length - 1 ? x[i + 1] - gap : hi);
  return x;
}
function lightBar(p) {
  const marks = [{ t: p.dep, l: 'Départ' }];
  if (p.sunsetHike) marks.push({ t: p.set, l: 'Coucher' }, { t: p.peak, l: 'Couleurs' });
  marks.push({ t: p.dusk, l: 'Nuit' }, { t: p.back, l: 'Parking' });
  marks.sort((a, b) => a.t - b.t).forEach((m, i) => { m.c = i % 2 ? 'up' : 'dn'; });
  const t0 = addMin(p.dep, -15), t1 = addMin(new Date(Math.max(p.back, p.dusk)), 15), span = t1 - t0;
  const pos = d => Math.max(2, Math.min(98, (d - t0) / span * 100));
  const pc = d => pos(d).toFixed(1);
  for (const row of ['up', 'dn']) {   // une rangée au-dessus, une en dessous, chacune écartée à part
    const ms = marks.filter(m => m.c === row), xs = spread(ms.map(m => pos(m.t)), 15, 7, 93);
    ms.forEach((m, i) => { m.x = xs[i]; });
  }
  const g = `linear-gradient(90deg,var(--sky-day) 0%,var(--sky-day) ${pc(p.gold)}%,var(--sky-gold) ${pc(addMin(p.set, -15))}%,var(--sky-set) ${pc(p.set)}%,var(--sky-dusk) ${pc(p.dusk)}%,var(--sky-night) 100%)`;
  return `<div class="light" id="light" style="background:${g}" role="img" aria-label="${marks.map(m => `${m.l} ${hm(m.t)}`).join(', ')}">
    ${marks.map(m => `<i class="lm" style="left:${pc(m.t)}%"></i><span class="ll ${m.c}" style="left:${m.x.toFixed(1)}%"><b>${hm(m.t)}</b>${m.l}</span>`).join('')}</div>`;
}
function profileSVG(h, T, m) {
  const W = 360, H = 140, L = 38, R = 10, Tp = 12, B = 22, N = 220, samp = [];
  for (let i = 0; i <= N; i++) samp.push({ t: i / N, a: pointAt(T, i / N * T.km).ele });
  const mn = Math.floor((T.minEle - 20) / 50) * 50, mx = Math.ceil((T.maxEle + 20) / 50) * 50;
  const Xf = t => L + t * (W - L - R), Y = a => Tp + (1 - (a - mn) / (mx - mn)) * (H - Tp - B);
  const pts = samp.map(s => `${Xf(s.t).toFixed(1)},${Y(s.a).toFixed(1)}`);
  const step = (mx - mn) > 600 ? 200 : (mx - mn) > 250 ? 100 : 50, yl = [], xl = [];
  for (let a = mn; a <= mx; a += step) yl.push(`<line x1="${L}" x2="${W - R}" y1="${Y(a)}" y2="${Y(a)}" stroke="var(--line)" stroke-width=".7"/><text x="${L - 6}" y="${Y(a) + 3.5}" text-anchor="end" font-size="10" fill="var(--muted)">${a}</text>`);
  const ks = m.km > 8 ? 2 : 1;
  for (let k = 0; k <= m.km + 0.01; k += ks) xl.push(`<text x="${Xf(k / m.km)}" y="${H - 6}" text-anchor="middle" font-size="10" fill="var(--muted)">${k} km</text>`);
  const sp = h.spots.map(s => { const t = T.cum[nearestIndex(T, s.lat, s.lon, !!s.sunset).i] / T.km; return `<circle cx="${Xf(t).toFixed(1)}" cy="${Y(pointAt(T, t * T.km).ele).toFixed(1)}" r="4.5" fill="${s.sunset ? 'var(--sun)' : 'var(--accent)'}" stroke="var(--surface)" stroke-width="2"/>`; }).join('');
  S.cur.prof = { X: Xf, Y, W, L, R };
  return `<svg id="prof" viewBox="0 0 ${W} ${H}" role="img" aria-label="Profil de ${Math.round(T.minEle)} à ${Math.round(T.maxEle)} m" style="touch-action:pan-y">
    <defs><linearGradient id="pf" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".35"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
    ${yl.join('')}${xl.join('')}
    <path d="M${Xf(0)},${Y(mn)} L${pts.join(' L')} L${Xf(1)},${Y(mn)}Z" fill="url(#pf)"/>
    <polyline points="${pts.join(' ')}" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linejoin="round"/>
    ${sp}
    <line id="cur" x1="-10" x2="-10" y1="${Tp}" y2="${H - B}" stroke="var(--gold)" stroke-width="1.5"/>
    <circle id="curDot" r="5" cx="-10" cy="-10" fill="var(--gold)" stroke="var(--surface)" stroke-width="2"/>
    <rect id="hit" x="${L}" y="0" width="${W - L - R}" height="${H}" fill="transparent"/>
  </svg>`;
}
function scrubTo(t) {
  const c = S.cur; if (!c?.prof) return;
  const p = pointAt(c.T, t * c.T.km), Xv = c.prof.X(t), Yv = c.prof.Y(p.ele);
  $('#cur')?.setAttribute('x1', Xv); $('#cur')?.setAttribute('x2', Xv);
  $('#curDot')?.setAttribute('cx', Xv); $('#curDot')?.setAttribute('cy', Yv);
  c.scrub?.setLngLat([p.lon, p.lat]);
  const ro = $('#readout'); if (ro) ro.textContent = `Km ${nf(t * c.m.km)}, ${Math.round(p.ele)} m d’altitude`;
}

function wxHTML(h, p) {
  if (S.wxErr) return `<p>${ic('warn')} Météo indisponible pour le moment.</p>`;
  if (!S.wx) return `<div class="sk" style="height:64px"></div>`;
  const w = wxFor(h, p);
  if (!w) return `<p>Pas encore de prévision pour cette date. Elle arrive 16 jours avant.</p>`;
  const s = w.series, k0 = Math.max(0, hourIndex(s, p.dep)), k1 = Math.min(s.hourly.time.length - 1, hourIndex(s, p.dusk) + 1);
  const cols = []; for (let k = k0; k <= k1; k++) cols.push(k);
  const Hh = s.hourly, cell = v => `<td><span class="cbar" style="--v:${Math.round(v ?? 0)}%"></span></td>`;
  const row = (lbl, f) => `<tr><th scope="row">${lbl}</th>${cols.map(f).join('')}</tr>`;
  const head = p.sunsetHike
    ? `<div class="wx-sum">${ring(w.display, 56, 'lg')}<div><strong>${scoreLabel(w.display)}</strong><p>${w.prob != null ? `${w.display} % de chances d’un beau coucher selon ton modèle, entraîné sur ${modelInfo('good').n_days} soirs observés. ` : ''}${esc(w.why.join('. '))}.</p>${w.sea != null ? `<p>${ic('mtn')} Mer de nuages : ${Math.round(w.sea * 100)} % selon tes observations.</p>` : ''}</div></div>`
    : `<div class="wx-sum"><div><strong>Conditions en cours de rando</strong><p>Vers ${hm(addMin(p.dep, 120))}</p></div></div>`;
  return `${head}
    <ul class="kv3">
      <li>Ressenti<b>${Math.round(w.feels)}°</b></li>
      <li>Rafales<b>${Math.round(w.gust)} km/h</b></li>
      <li>${w.snow ? 'Neige au sol' : 'Isotherme 0°'}<b>${w.snow ? Math.round(w.snow * 100) + ' cm' : Math.round(w.freezing) + ' m'}</b></li>
    </ul>
    <div class="second" id="second"><span id="shue"></span><span id="alps"></span><span id="mdl"></span></div>
    <details class="sub"><summary>Heure par heure</summary>
      <div class="wxtable"><table>
        <thead><tr><th></th>${cols.map(k => `<th scope="col" class="${k === w.k ? 'now' : ''}">${Hh.time[k].slice(11, 13)}h</th>`).join('')}</tr></thead>
        <tbody>${row('Nuages bas', k => cell(Hh.cloud_cover_low[k]))}${row('Moyens', k => cell(Hh.cloud_cover_mid[k]))}${row('Hauts', k => cell(Hh.cloud_cover_high[k]))}
        ${row('Rafales', k => `<td>${Math.round(Hh.wind_gusts_10m[k] ?? 0)}</td>`)}${row('Ressenti', k => `<td>${Math.round(Hh.apparent_temperature[k] ?? 0)}°</td>`)}</tbody>
      </table></div></details>
    <p class="cap">Prévision ${esc(w.model || 'Open-Meteo')}, à l’altitude du spot.</p>`;
}

/** Option « rester pour les couleurs » : ce que ça coûte en retour et en descente de nuit. */
function stayHTML(h, m) {
  const a = plan(h, m, S.date, false), b = plan(h, m, S.date, true);
  const more = Math.round((b.back - a.back) / 6e4), night = Math.round(b.night - a.night);
  const cost = more <= 0 ? 'Sans incidence sur le retour' : `Retour ${more} min plus tard${night > 0 ? `, dont ${night} min de plus de nuit` : ''}`;
  return `<label class="stay"><input type="checkbox" id="stayIn"${S.stay ? ' checked' : ''}>
    <span>Rester jusqu’au pic de couleur, vers ${hm(a.peak)}<small>${cost}</small></span></label>`;
}
function alertsHTML(h, p, w, late) {
  const a = [];
  if (late) a.push(['warn', 'Trop tard pour ce coucher. Regarde demain ou une rando plus proche.']);
  if (p.sunsetHike && p.night > 5) a.push(['moon', `${dur(p.night)} de descente de nuit. Frontale chargée et batterie de secours.`]);
  if (w && w.gust >= 60) a.push(['wind', `Rafales à ${Math.round(w.gust)} km/h au spot. Crête exposée à éviter si ça dépasse 80.`]);
  if (w && w.feels <= -15) a.push(['temp', `Ressenti ${Math.round(w.feels)} °C. Doudoune et gants de rechange.`]);
  if (h.snow && w && w.snow >= 0.2) a.push(['warn', 'Neige au sol significative. Raquettes, et vigilance avalanche Météo-France avant les pentes raides.']);
  if (!a.length && p.sunsetHike) return `<p class="alert ok">${ic('check')}Retour au parking avant la nuit si tu tiens l’horaire.</p>`;
  return a.map(([i, t]) => `<p class="alert">${ic(i)}${t}</p>`).join('');
}

function skeleton() {
  return `<div class="mhero"></div><div class="sheet"><div class="sk" style="height:34px;width:70%"></div><div class="sk" style="height:18px;width:40%;margin-top:10px"></div><div class="sk" style="height:70px;margin-top:20px"></div><div class="sk" style="height:120px;margin-top:20px"></div></div>`;
}

async function viewDetail(h) {
  document.body.classList.add('is-detail');
  const token = S.token;
  app.innerHTML = skeleton();
  const T = await getTrack(h);
  if (token !== S.token) return;
  const m = metrics(h, T), p = plan(h, m), rd = road(h), w = wxFor(h, p);
  const leave = addMin(p.dep, -rd.min), late = isToday() && leave.getTime() < Date.now() - 10 * 6e4;
  const fav = S.saved.fav.includes(h.id), done = S.saved.done.includes(h.id);
  S.cur = { h, T, m, p, w, late };
  if (h.country !== 'FR') S.base = 'topo';
  const gm = `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(h.start.query)}`;
  const spotsHTML = h.spots.map((s, i) => `<li><button type="button" data-t="${T.cum[nearestIndex(T, s.lat, s.lon, !!s.sunset).i] / T.km}"><span class="dot${s.sunset ? ' sun' : ''}">${s.sunset ? ic('sunset') : i + 1}</span><span>${esc(s.name)}<small>${esc(s.desc)}</small></span><small>${s.alt} m</small></button></li>`).join('');

  app.innerHTML = `
  <div class="mhero">
    <div id="map" class="mapfull"></div>
    <a class="fab back" href="#liste" aria-label="Retour">${ic('back')}</a>
    <div class="fabcol">
      <button class="fab" id="baseBtn" type="button" aria-label="Fond de carte : ${S.base === 'ign' ? 'IGN' : 'Topo'}">${ic('layers')}</button>
      ${p.sunsetHike ? `<button class="fab" id="d3Btn" type="button" aria-pressed="false" aria-label="Vue 3D vers le couchant">${ic('cube')}</button>` : ''}
    </div>
  </div>
  <div class="sheet">
    <h1 class="d-title">${esc(h.name)}</h1>
    <p class="d-zone">${esc(h.zone)}</p>
    <div class="verdict">
      ${p.sunsetHike ? `<div class="first"><span class="v-big">${hm(p.set)}</span><span class="v-cap">${ic('sunset')}coucher au spot</span></div>`
        : `<div><span class="v-big">${hm(p.dusk)}</span><span class="v-cap">${ic('moon')}nuit</span></div>`}
      <div><span class="v-big">${hm(p.dep)}</span><span class="v-cap">${ic('boot')}${p.sunsetHike ? 'départ parking' : 'départ au plus tard'}</span></div>
      <div>${w && p.sunsetHike ? ring(w.display, 56, 'lg') : ''}</div>
    </div>
    <p class="leave${late ? ' late' : ''}">${ic('car')}${late ? 'Trop tard pour ce soir' : `<span><strong data-leave="${leave.getTime()}">${leaveTxt(leave.getTime())}</strong> de ${originName()}, ${dur(rd.min)} de route</span>`}</p>
    ${lightBar(p)}
    ${p.sunsetHike ? stayHTML(h, m) : ''}
    <ul class="facts">
      <li>${ic('route')}<b>${nf(m.km)} km</b></li><li>${ic('up')}<b>${Math.round(m.dplus)} m</b></li>
      <li>${ic('clock')}<b>${dur(m.total)}</b></li><li>${ic('mtn')}<b>${DIFF[h.diff]}</b></li>
    </ul>
    <div id="alerts">${alertsHTML(h, p, w, late)}</div>

    <section class="grp"><h2>Météo au spot</h2><div class="card" id="wx">${wxHTML(h, p)}</div></section>
    <section class="grp" id="camGrp" hidden><h2>Webcams</h2><div class="cams" id="cams"></div><p class="cap">Images Windy.com, rafraîchies à chaque ouverture.</p></section>
    <section class="grp"><h2>Profil</h2><div class="card flush prof">${profileSVG(h, T, m)}</div><p class="cap" id="readout">Glisse sur le profil pour te repérer sur la carte.</p></section>
    <section class="grp"><h2>Spots</h2><ul class="card flush ilist">${spotsHTML}</ul><p class="cap">${esc(h.tip)}</p></section>
    <section class="grp" id="poiGrp" hidden><h2>Autour du tracé</h2><ul class="card flush ilist" id="pois"></ul><p class="cap">OpenStreetMap. En hiver, sources souvent gelées et fermes-auberges souvent fermées.</p></section>
    <section class="grp">
      <details class="card acc"><summary>Conditions d’hiver</summary>
        <dl class="kv"><dt>Accès</dt><dd>${esc(h.access)}</dd><dt>Vigilance</dt><dd>${esc(h.risk)}</dd></dl>
        <div class="gear">${h.gear.map(g => `<span>${esc(g)}</span>`).join('')}</div>
        ${h.snow ? '<p class="cap">Pas de bulletin d’avalanche quotidien pour les Vosges : Météo-France émet une vigilance avalanche quand le risque le justifie.</p>' : ''}
      </details>
      <details class="card acc"><summary>Trace <span class="src${m.real ? ' real' : ''}">${SRC[T.src]}</span></summary>
        <p class="cap" style="margin:0">${m.real ? 'Distance, dénivelé et horaires calculés sur la trace.' : 'Importe le GPX de Visorando ou IGNrando pour des horaires exacts.'}</p>
        <div class="links"><button class="btn" id="impBtn" type="button">${T.src === 'import' ? 'Remplacer la trace' : 'Importer un GPX'}</button>
        ${T.src === 'import' ? '<button class="btn ghost" id="delBtn" type="button">Supprimer ma trace</button>' : ''}</div>
        <input type="file" id="gpxIn" accept=".gpx,application/gpx+xml,application/xml,text/xml" hidden>
      </details>
      <details class="card acc"><summary>Liens</summary>
        <div class="links">
          <a class="btn" href="https://www.google.com/search?q=${encodeURIComponent(h.name + ' randonnée GPX visorando')}" target="_blank" rel="noopener">Chercher la trace GPX</a>
          <a class="btn" href="https://vigilance.meteofrance.fr/fr" target="_blank" rel="noopener">Vigilance Météo-France</a>
          <a class="btn" href="https://sunsethue.com/app" target="_blank" rel="noopener">Carte Sunsethue</a>
        </div>
      </details>
      <button class="btn ghost" id="doneBtn" type="button" style="width:100%;margin-top:10px">${done ? `${ic('check')} Faite` : 'Marquer comme faite'}</button>
    </section>
  </div>
  <div class="actionbar">
    <a class="btn primary" href="${gm}" target="_blank" rel="noopener">${ic('car')}Itinéraire</a>
    <button class="icbtn" id="shareBtn" type="button" aria-label="Partager le plan">${ic('share')}</button>
    <button class="icbtn" id="offBtn" type="button" aria-label="Enregistrer la carte pour le hors-ligne">${ic('dl')}</button>
    <button class="icbtn" id="favBtn" type="button" aria-pressed="${fav}" aria-label="${fav ? 'Retirer des favoris' : 'Ajouter aux favoris'}">${ic('star', fav ? 'fill' : '')}</button>
  </div>`;
  window.scrollTo(0, 0);
  bindDetail(h);
  initDetailMap(h);
  loadExtras(h, T, p, token);
}

function bindDetail(h) {
  const c = S.cur;
  $('#favBtn').onclick = e => { toggle('fav', h.id); buzz(); const on = S.saved.fav.includes(h.id); const b = e.currentTarget; b.setAttribute('aria-pressed', on); b.innerHTML = ic('star', on ? 'fill' : ''); toast(on ? 'Ajoutée aux favoris' : 'Retirée des favoris'); };
  $('#stayIn')?.addEventListener('change', e => {
    S.stay = e.target.checked; store.set('stay', S.stay).catch(() => {}); buzz();
    c.p = plan(h, c.m);
    $('#light').outerHTML = lightBar(c.p);
    $('#alerts').innerHTML = alertsHTML(h, c.p, c.w, c.late);
  });
  $('#doneBtn').onclick = e => { toggle('done', h.id); buzz(); e.currentTarget.innerHTML = S.saved.done.includes(h.id) ? `${ic('check')} Faite` : 'Marquer comme faite'; };
  $('#shareBtn').onclick = async () => {
    const p = c.p, when = isToday() ? 'ce soir' : noon(S.date).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric' });
    const text = p.sunsetHike ? `${h.name} ${when} : départ du parking ${hm(p.dep)}, coucher ${hm(p.set)} depuis ${p.spot.name}.${c.w ? ` Score météo ${c.w.display}/100.` : ''}` : `${h.name} ${when} : départ ${hm(p.dep)} au plus tard.`;
    try { if (navigator.share) await navigator.share({ title: h.name, text, url: location.href }); else { await navigator.clipboard.writeText(`${text} ${location.href}`); toast('Plan copié'); } } catch { /* partage annulé */ }
  };
  const svg = $('#prof'), hit = $('#hit');
  const tOf = e => { const r = svg.getBoundingClientRect(), pr = c.prof, x = (e.clientX - r.left) / r.width * pr.W; return Math.min(1, Math.max(0, (x - pr.L) / (pr.W - pr.L - pr.R))); };
  hit.addEventListener('pointerdown', e => scrubTo(tOf(e)));
  hit.addEventListener('pointermove', e => { if (e.pointerType === 'mouse' || e.buttons) scrubTo(tOf(e)); });
  $$('.ilist button[data-t]').forEach(b => b.onclick = () => flyToT(+b.dataset.t));
  $('#impBtn').onclick = () => $('#gpxIn').click();
  $('#gpxIn').onchange = async e => {
    const f = e.target.files?.[0]; if (!f) return;
    try { const text = await f.text(); parseGPX(text); await store.set('gpx:' + h.id, text); trackCache.delete(h.id); toast('Trace importée, horaires recalculés'); route(); }
    catch (err) { toast(err.message || 'Import impossible'); }
  };
  $('#delBtn')?.addEventListener('click', async () => { await store.del('gpx:' + h.id).catch(() => {}); trackCache.delete(h.id); toast('Trace supprimée'); route(); });
}
function flyToT(t) {
  const c = S.cur; scrubTo(t);
  const p = pointAt(c.T, t * c.T.km);
  map?.easeTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 14), duration: reduced() ? 0 : 600 });
  window.scrollTo({ top: 0, behavior: reduced() ? 'auto' : 'smooth' });
}

function mk(cls, inner) { const el = document.createElement('div'); el.className = `mk ${cls}`; el.innerHTML = inner; return el; }

function initDetailMap(h) {
  const c = S.cur, T = c.T;
  if (!window.maplibregl) { $('#map').innerHTML = '<p class="empty">Carte indisponible : ouvre l’app une fois avec du réseau.</p>'; return; }
  map = MAP.createMap('map', { center: [h.start.lon, h.start.lat], zoom: 13, base: S.base });
  map.on('load', () => {
    MAP.setRoute(map, T, '#CF4560');
    MAP.fitTrack(map, T, { top: 60, bottom: 40, left: 64, right: 50 });
    mapMarkers.push(MAP.marker(mk('start', 'P'), [h.start.lon, h.start.lat]).setPopup(MAP.popup(`<strong>${esc(h.start.name)}</strong><br>${h.start.alt} m`)).addTo(map));
    h.spots.forEach((s, i) => mapMarkers.push(MAP.marker(mk(s.sunset ? 'sun' : '', s.sunset ? ic('sunset') : i + 1), [s.lon, s.lat]).setPopup(MAP.popup(`<strong>${esc(s.name)}</strong><br>${s.alt} m`)).addTo(map)));
    const sc = document.createElement('div'); sc.className = 'scrub-mk';
    c.scrub = MAP.marker(sc, [h.start.lon, h.start.lat]).addTo(map);
    MAP.geolocate(map, onPos);
    const sp = sunsetSpot(h);
    scrubTo(sp ? T.cum[nearestIndex(T, sp.lat, sp.lon, true).i] / T.km : 0);
    if (c.pois) addPoiMarkers(c.pois);
  });
  $('#baseBtn').onclick = e => { S.base = S.base === 'ign' ? 'topo' : 'ign'; MAP.setBase(map, S.base); e.currentTarget.setAttribute('aria-label', `Fond de carte : ${S.base === 'ign' ? 'IGN' : 'Topo'}`); toast(S.base === 'ign' ? 'Plan IGN' : 'OpenTopoMap'); };
  let d3 = false;
  $('#d3Btn')?.addEventListener('click', e => {
    d3 = !d3; const sp = c.p.spot;
    MAP.set3D(map, d3, { lat: sp.lat, lon: sp.lon, bearing: c.p.az });
    e.currentTarget.setAttribute('aria-pressed', d3);
    if (d3) toast(`Vue depuis ${sp.name}, soleil couchant au ${compass(c.p.az)}`);
  });
  $('#offBtn').onclick = async e => {
    const b = e.currentTarget; if (b.disabled) return;
    if (!navigator.serviceWorker?.controller) { toast('Ferme et rouvre l’app une fois, puis réessaie'); return; }
    let a = 90, z = -90, o = 180, x = -180;
    T.pts.forEach(p => { a = Math.min(a, p[0]); z = Math.max(z, p[0]); o = Math.min(o, p[1]); x = Math.max(x, p[1]); });
    const urls = MAP.tileUrls([o - 0.012, a - 0.008, x + 0.012, z + 0.008], S.base);
    b.disabled = true;
    const r = await MAP.prefetch(urls, (d, n) => { if (d % 20 === 0 || d === n) toast(`Carte hors-ligne ${Math.round(d / n * 100)} %`); });
    b.disabled = false; b.innerHTML = ic('check');
    toast(r.fail ? `${r.done - r.fail} tuiles enregistrées, ${r.fail} en échec` : 'Carte disponible sans réseau');
  };
}
const compass = az => ['nord', 'nord-est', 'est', 'sud-est', 'sud', 'sud-ouest', 'ouest', 'nord-ouest'][Math.round(az / 45) % 8];

const POI_ICON = { hut: 'hut', food: 'food', eye: 'eye', drop: 'drop', peak: 'peak' };
function addPoiMarkers(pois) {
  if (!map) return;
  pois.slice(0, 40).forEach(pt => mapMarkers.push(MAP.marker(mk('poi', ic(POI_ICON[pt.kind])), [pt.lon, pt.lat]).setPopup(MAP.popup(`<strong>${esc(pt.name)}</strong><br>${X.POI_LABEL[pt.kind]}${pt.ele ? `, ${pt.ele} m` : ''}`)).addTo(map)));
}

/** Données secondaires : chargées en parallèle, chacune affichée dès qu'elle arrive. */
function fillSecond(h, p, token) {
  const c = S.cur, sp = wxPoint(h), alive = () => token === S.token, ahead = daysAhead();
  const shueP = p.sunsetHike && ahead <= 2 ? X.sunsethue(S.settings, { lat: sp.lat, lon: sp.lon, date: S.date }) : Promise.resolve(null);
  const aodP = ahead <= 4 ? X.aerosols(sp).then(a => a.hourly?.aerosol_optical_depth?.[hourIndex(a, p.set)] ?? null) : Promise.resolve(null);
  shueP.then(r => {
    if (!alive() || !r) return; const el = $('#shue'); if (!el) return;
    el.innerHTML = r.available ? `<span class="tag ${scoreCls(r.quality)}">${r.quality}</span><span>Sunsethue, modèle à rayons : ${esc(r.text || '')}${r.cloud != null ? `, ${r.cloud} % de nuages à l’horizon` : ''}</span>` : '<span>Sunsethue : pas encore de prévision pour cette date.</span>';
  }).catch(e => { if (alive() && $('#shue')) $('#shue').innerHTML = `<span>Sunsethue injoignable (${esc(e.message)}). Vérifie la clé ou utilise le proxy.</span>`; });
  aodP.then(aod => {
    if (!alive() || !/Alpes/.test(h.view)) return;
    const w = c.w, v = X.alpsVisibility(aod, w?.series?.hourly?.visibility?.[w.k], w?.low ?? 0);
    const el = $('#alps'); if (v && el) el.innerHTML = `${ic('eye')} ${esc(v.text)}`;
  }).catch(() => {});
  // Le modèle a été entraîné avec Sunsethue et les aérosols : on affine dès qu'ils arrivent.
  Promise.allSettled([shueP, aodP]).then(([a, b]) => {
    const w = c?.w; if (!alive() || !w || w.prob == null || !p.sunsetHike) return;
    const shue = a.value?.available ? a.value.quality : null, aod = b.value ?? null;
    if (shue == null && aod == null) return;
    const pr = Math.round(predict('good', { ...w.feats, sunsethue: shue, aod }) * 100);
    const el = $('#mdl'); if (el && Math.abs(pr - w.display) >= 2) el.innerHTML = `${ic('check')} Avec ces données en plus, ton modèle passe à <strong>${pr} %</strong>.`;
  });
}
async function loadExtras(h, T, p, token) {
  const c = S.cur, sp = wxPoint(h), alive = () => token === S.token;
  fillSecond(h, p, token);
  // Webcams Windy
  X.webcams(S.settings, { lat: sp.lat, lon: sp.lon }).then(list => {
    if (!alive() || !list?.length) return;
    $('#cams').innerHTML = list.map(w => `<a class="cam" href="${esc(w.url)}" target="_blank" rel="noopener"><img src="${esc(w.img)}" alt="Webcam ${esc(w.title)}" loading="lazy"><span>${esc(w.city || w.title)}</span></a>`).join('');
    $('#camGrp').hidden = false;
  }).catch(e => console.warn('Webcams', e.message));
  // OSM autour du tracé
  let a = 90, z = -90, o = 180, x = -180;
  T.pts.forEach(q => { a = Math.min(a, q[0]); z = Math.max(z, q[0]); o = Math.min(o, q[1]); x = Math.max(x, q[1]); });
  X.osmPOIs([a - 0.006, o - 0.009, z + 0.006, x + 0.009]).then(pois => {
    if (!alive() || !pois.length) return;
    const near = pois.map(pt => ({ ...pt, n: nearestIndex(T, pt.lat, pt.lon) })).filter(pt => pt.n.d < 0.6)
      .sort((u, v) => T.cum[u.n.i] - T.cum[v.n.i]);
    if (!near.length) return;
    c.pois = near; addPoiMarkers(near);
    $('#pois').innerHTML = near.slice(0, 12).map(pt => `<li><button type="button" data-t="${T.cum[pt.n.i] / T.km}"><span class="dot poi">${ic(POI_ICON[pt.kind])}</span><span>${esc(pt.name)}<small>${X.POI_LABEL[pt.kind]}, km ${nf(T.cum[pt.n.i] / T.km * c.m.km)}${pt.n.d > 0.15 ? `, à ${Math.round(pt.n.d * 1000)} m du chemin` : ''}</small></span><small>${pt.ele ? pt.ele + ' m' : ''}</small></button></li>`).join('');
    $$('#pois button').forEach(b => b.onclick = () => flyToT(+b.dataset.t));
    $('#poiGrp').hidden = false;
  }).catch(e => console.warn('OSM', e.message));
}

function onPos(coords, err) {
  const c = S.cur;
  if (!coords) { toast(err?.code === 1 ? 'Localisation refusée dans les réglages du navigateur' : 'Position indisponible'); return; }
  S.pos = { lat: coords.latitude, lon: coords.longitude };
  if (!c) return;
  const n = nearestIndex(c.T, coords.latitude, coords.longitude), last = c.T.pts.length - 1;
  const ro = $('#readout'); if (!ro) return;
  if (n.d < 0.15) {
    const seg = segment(c.T, n.i, last), k = c.m.km / c.T.km;
    const rest = din(seg.km * k, seg.up, seg.down, S.pace), arr = addMin(new Date(), rest);
    ro.innerHTML = `<strong>Km ${nf(c.T.cum[n.i] * k)} sur ${nf(c.m.km)}</strong>, parking dans ${dur(rest)} (${hm(arr)})${arr > c.p.dusk ? ', de nuit' : ''}`;
    scrubTo(c.T.cum[n.i] / c.T.km);
  } else ro.textContent = `Tu es à ${n.d < 5 ? Math.round(n.d * 1000) + ' m' : Math.round(n.d) + ' km'} du tracé.`;
}

/* ---------- semaine ---------- */
function viewWeek() {
  document.body.classList.remove('is-detail');
  const ds = weekDays();
  const head = headerHTML('Semaine', false);
  if (!S.wx) {
    app.innerHTML = `${head}<p class="${S.wxErr ? 'banner' : 'empty'}">${S.wxErr ? `${ic('warn')} Météo indisponible, la semaine ne peut pas être calculée.` : 'Chargement des prévisions…'}</p>`;
    bindHeader(); return;
  }
  const rows = S.hikes.filter(h => sunsetSpot(h) && h.sunsetScore >= 2).map(h => {
    const cells = ds.map(d => ({ s: d.s, e: evaluate(h, d.s) }));
    return { h, cells, best: Math.max(-1, ...cells.map(c => c.e.w && !c.e.late ? c.e.w.display : -1)) };
  }).sort((a, b) => b.best - a.best || a.h.name.localeCompare(b.h.name, 'fr'));
  const label = s => { const d = noon(s); return `<span>${d.toLocaleDateString('fr-FR', { weekday: 'short' }).replace('.', '')}</span><b>${d.getDate()}</b>`; };
  const cell = (h, c) => {
    const { e } = c, v = e.w?.display;
    if (v == null) return `<td><span class="wk na" aria-label="Pas de prévision">–</span></td>`;
    const say = `${h.name}, ${dayName(c.s)} : ${v} sur 100${e.late ? ', trop tard' : ''}`;
    return `<td><a class="wk ${scoreCls(v)}${e.late ? ' late' : ''}" href="#${h.id}" data-date="${c.s}" aria-label="${esc(say)}">${v}</a></td>`;
  };
  const top = bestAhead(null);
  app.innerHTML = `${head}
    ${top ? `<div class="hero calm wk-top">${nextHTML(top)}</div>` : ''}
    <div class="weekwrap"><table class="week">
      <thead><tr><th scope="col"><span class="vh">Rando</span></th>${ds.map(d => `<th scope="col"${d.s === todayStr() ? ' class="today"' : ''}>${label(d.s)}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r => `<tr><th scope="row"><a href="#${r.h.id}">${esc(r.h.name)}</a></th>${r.cells.map(c => cell(r.h, c)).join('')}</tr>`).join('')}</tbody>
    </table></div>
    <p class="cap wk-cap">Chance de beau coucher sur 100, au spot. Touche une case pour ouvrir le plan de ce soir-là.
      Prévision Météo-France jusqu’à 4 jours, puis multi-modèle : plus on s’éloigne, plus c’est indicatif.</p>`;
  bindHeader();
}

/* ---------- carte d'ensemble ---------- */
const darkUI = () => matchMedia('(prefers-color-scheme: dark)').matches;
function viewMap() {
  document.body.classList.remove('is-detail');
  document.body.classList.add('is-map');
  const when = isToday() ? 'Ce soir' : upFirst(noon(S.date).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric' }));
  app.innerHTML = `<div class="fullmap"><div id="bigmap"></div>
    <div class="maphead"><strong>${when}</strong>
      <span class="lg" aria-label="Chance de beau coucher"><i class="hot"></i>75+<i class="mid"></i>50+<i class="cold"></i>moins</span></div>
    <div class="mapcard" id="mapcard" aria-live="polite"></div></div>`;
  if (!window.maplibregl) { $('#bigmap').innerHTML = '<p class="empty">Carte indisponible : ouvre l’app une fois avec du réseau.</p>'; return; }
  const E = S.hikes.map(h => evaluate(h));
  const az = E.reduce((a, e) => a + e.p.az, 0) / E.length;
  const lons = S.hikes.map(h => h.start.lon), lats = S.hikes.map(h => h.start.lat);
  map = MAP.createMap('bigmap', { overview: true, dark: darkUI(), sunAz: az, center: [7.2, 48.2], zoom: 7.6,
    bounds: [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]] });
  const z = () => $('.fullmap')?.classList.toggle('names', map.getZoom() >= 9);
  map.on('zoom', z);
  map.on('load', () => {
    z(); mapPins();
    $('.fullmap .maplibregl-compact-show')?.classList.remove('maplibregl-compact-show');   // crédits repliés derrière le (i)
    map.on('click', () => { $('#mapcard')?.classList.remove('show'); $$('.pin.sel').forEach(x => x.classList.remove('sel')); });
    MAP.geolocate(map, cc => { if (cc) S.pos = { lat: cc.latitude, lon: cc.longitude }; });
  });
}
/** Un repère par rando, posé au parking : le score du coucher, ou une simple pastille pour une rando de journée. */
function mapPins() {
  mapMarkers.forEach(m => m.remove()); mapMarkers = [];
  const E = S.hikes.map(h => evaluate(h));
  const val = e => e.p.sunsetHike ? (e.w ? e.w.display : e.h.sunsetScore * 16) : -1;
  E.sort((a, b) => val(a) - val(b)).forEach(e => {   // les meilleurs posés en dernier, donc au-dessus
    const { h, p, w, m } = e, v = val(e);
    const el = document.createElement('button'); el.type = 'button';
    el.className = `pin ${v < 0 ? 'day' : scoreCls(v)}${w ? '' : ' guess'}${e.late ? ' late' : ''}`;
    el.innerHTML = `<span class="pin-b">${v >= 0 && w ? v : v >= 0 ? ic('sunset') : ic('boot')}</span><span class="pin-n">${esc(h.name)}</span>`;
    el.onclick = ev => {
      ev.stopPropagation();
      $$('.pin.sel').forEach(x => x.classList.remove('sel')); el.classList.add('sel');
      const card = $('#mapcard');
      const lead = p.sunsetHike && w ? ring(w.display, 46) : `<span class="lead">${p.sunsetHike ? `<span><b>${hm(p.set)}</b>coucher</span>` : `<span>${ic('boot')}<br>journée</span>`}</span>`;
      card.innerHTML = `<a class="mc" href="#${h.id}">${lead}
        <span><strong>${esc(h.name)}</strong>
          <small>${p.sunsetHike ? `Coucher à ${hm(p.set)}, parking à ${hm(p.dep)}` : 'Rando de journée'}${e.late ? ', trop tard' : ''}</small>
          <small>${nf(m.km)} km, ${Math.round(m.dplus)} m, ${dur(m.total)}</small></span>
        <span class="mc-go">${ic('back')}</span></a>`;
      card.classList.add('show');
    };
    mapMarkers.push(MAP.marker(el, [h.start.lon, h.start.lat], { anchor: 'center' }).addTo(map));
    el.setAttribute('aria-label', `${h.name}${v >= 0 && w ? `, score ${v}` : ''}`);   // après coup : MapLibre pose « Map marker »
  });
}

/* ---------- réglages ---------- */
function openSettings() {
  const d = $('#settings'), f = d.querySelector('form').elements;
  f.pace.value = String(S.pace); f.home.value = S.home;
  f.sunsethue.value = S.settings.sunsethue || ''; f.windy.value = S.settings.windy || ''; f.proxy.value = S.settings.proxy || '';
  if (d.showModal) d.showModal(); else d.setAttribute('open', '');
}
function bindSettings() {
  const d = $('#settings'), f = d.querySelector('form').elements;
  d.addEventListener('close', async () => {
    if (d.returnValue !== 'save') return;
    S.pace = +f.pace.value; S.home = f.home.value;
    S.settings = { sunsethue: f.sunsethue.value.trim(), windy: f.windy.value.trim(), proxy: f.proxy.value.trim() };
    await Promise.all([store.set('pace', S.pace), store.set('home', S.home), store.set('settings', S.settings)]).catch(() => {});
    toast('Réglages enregistrés'); route();
  });
  $('#clearCache').onclick = async () => { await caches?.delete?.('tiles-v1'); toast('Carte hors-ligne effacée'); };
}

/* ---------- navigation ---------- */
function teardown() { mapMarkers.forEach(m => m.remove()); mapMarkers = []; if (map) { map.remove(); map = null; } S.cur = null; }
function toggle(k, id) { const a = S.saved[k], i = a.indexOf(id); i < 0 ? a.push(id) : a.splice(i, 1); store.set('saved', S.saved).catch(() => {}); }
function route() {
  S.token++; teardown(); document.body.classList.remove('is-map');
  const h = location.hash.slice(1), hike = S.hikes.find(x => x.id === h);
  const tab = hike ? null : ['carte', 'favoris', 'semaine'].includes(h) ? h : 'liste';
  $$('.tabs a').forEach(a => a.toggleAttribute('aria-current', false));
  if (tab) $(`.tabs a[data-tab="${tab}"]`).setAttribute('aria-current', 'page');
  if (hike) viewDetail(hike); else if (tab === 'carte') viewMap(); else if (tab === 'semaine') viewWeek(); else viewList(tab === 'favoris');
}
function locate() {
  if (!navigator.geolocation) { toast('Position non disponible sur ce navigateur'); return; }
  toast('Recherche de ta position…');
  navigator.geolocation.getCurrentPosition(p => {
    S.pos = { lat: p.coords.latitude, lon: p.coords.longitude }; S.home = 'gps'; store.set('home', 'gps').catch(() => {});
    toast('Temps de route calculés depuis ta position'); route();
  }, e => toast(e.code === 1 ? 'Localisation refusée dans les réglages du navigateur' : 'Position indisponible'), { timeout: 12000, maximumAge: 300000 });
}
async function loadWeather() {
  try { S.wx = await fetchForecast(S.hikes.map(wxPoint)); S.wxErr = null; } catch (e) { S.wxErr = e.message; }
  const c = S.cur;
  if (c && $('#wx')) { c.w = wxFor(c.h, c.p); $('#wx').innerHTML = wxHTML(c.h, c.p); fillSecond(c.h, c.p, S.token); }
  else if (location.hash === '#carte') { if (map?.loaded()) mapPins(); }
  else route();
}
async function init() {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js').catch(() => {});
  window.addEventListener('hashchange', route);
  document.addEventListener('click', e => { const a = e.target.closest?.('a[data-date]'); if (a) S.date = a.dataset.date; });
  bindSettings();
  try { S.hikes = await (await fetch('./data/hikes.json')).json(); }
  catch { app.innerHTML = '<p class="empty">Impossible de charger les randos. Ouvre l’app depuis son adresse web.</p>'; return; }
  try {
    S.saved = (await store.get('saved')) || S.saved; S.pace = (await store.get('pace')) || S.pace; S.stay = !!(await store.get('stay'));
    S.home = (await store.get('home')) || S.home; S.settings = (await store.get('settings')) || S.settings;
  } catch { /* navigation privée */ }
  await loadModel();
  route();
  loadWeather();
}
init();
