// Traces : parsing GPX, densification, distances, dénivelés, temps de marche
const R = 6371, rad = Math.PI / 180;

export function hav(a, b, c, d) {
  const x = (c - a) * rad, y = (d - b) * rad;
  const h = Math.sin(x / 2) ** 2 + Math.cos(a * rad) * Math.cos(c * rad) * Math.sin(y / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** GPX → [[lat, lon, ele|null], …] (trk prioritaire, sinon rte). */
export function parseGPX(text) {
  const doc = new DOMParser().parseFromString(text, 'application/xml');
  if (doc.getElementsByTagName('parsererror').length) throw new Error('Fichier GPX illisible');
  let nodes = [...doc.getElementsByTagName('trkpt')];
  if (nodes.length < 2) nodes = [...doc.getElementsByTagName('rtept')];
  if (nodes.length < 2) throw new Error('Aucune trace dans ce fichier GPX');
  return nodes.map(p => {
    const e = p.getElementsByTagName('ele')[0];
    const ele = e ? parseFloat(e.textContent) : NaN;
    return [parseFloat(p.getAttribute('lat')), parseFloat(p.getAttribute('lon')), Number.isFinite(ele) ? ele : null];
  }).filter(p => Number.isFinite(p[0]) && Number.isFinite(p[1]));
}

/** Points clés → points tous les `stepKm` (pour obtenir un profil depuis un MNT). */
export function densify(wps, stepKm = 0.06) {
  const out = [];
  for (let i = 0; i < wps.length - 1; i++) {
    const a = wps[i], b = wps[i + 1], n = Math.max(1, Math.ceil(hav(a[0], a[1], b[0], b[1]) / stepKm));
    for (let k = 0; k < n; k++) {
      const u = k / n, s = u * u * (3 - 2 * u);
      out.push([a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] != null && b[2] != null ? a[2] + (b[2] - a[2]) * s : null]);
    }
  }
  const l = wps[wps.length - 1];
  out.push([l[0], l[1], l[2] ?? null]);
  return out;
}

/** Construit les cumuls distance / D+ / D- (lissage + hystérésis anti-bruit GPS). */
export function buildTrack(pts, { threshold = 4 } = {}) {
  const n = pts.length, cum = new Float64Array(n);
  for (let i = 1; i < n; i++) cum[i] = cum[i - 1] + hav(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]);
  const raw = pts.map(p => p[2]);
  let last = raw.find(v => v != null) ?? 0;
  for (let i = 0; i < n; i++) { if (raw[i] == null) raw[i] = last; else last = raw[i]; }
  const ele = raw.map((_, i) => {
    let s = 0, c = 0;
    for (let k = Math.max(0, i - 2); k <= Math.min(n - 1, i + 2); k++) { s += raw[k]; c++; }
    return s / c;
  });
  const cumUp = new Float64Array(n), cumDown = new Float64Array(n);
  let ref = ele[0], up = 0, down = 0;
  for (let i = 1; i < n; i++) {
    const d = ele[i] - ref;
    if (d >= threshold) { up += d; ref = ele[i]; } else if (d <= -threshold) { down -= d; ref = ele[i]; }
    cumUp[i] = up; cumDown[i] = down;
  }
  let minEle = Infinity, maxEle = -Infinity;
  for (const v of ele) { if (v < minEle) minEle = v; if (v > maxEle) maxEle = v; }
  return { pts, cum, ele, cumUp, cumDown, km: cum[n - 1], dplus: up, dminus: down, minEle, maxEle };
}

export const segment = (T, i0, i1) => ({ km: T.cum[i1] - T.cum[i0], up: T.cumUp[i1] - T.cumUp[i0], down: T.cumDown[i1] - T.cumDown[i0] });

/** Point le plus proche. preferLast : dernier passage à moins de radiusKm (boucles en aller-retour). */
export function nearestIndex(T, lat, lon, preferLast = false, radiusKm = 0.15) {
  let best = { i: 0, d: Infinity }, lastNear = -1;
  for (let i = 0; i < T.pts.length; i++) {
    const d = hav(lat, lon, T.pts[i][0], T.pts[i][1]);
    if (d < best.d) best = { i, d };
    if (d < radiusKm) lastNear = i;
  }
  if (preferLast && lastNear >= 0) return { i: lastNear, d: hav(lat, lon, T.pts[lastNear][0], T.pts[lastNear][1]) };
  return best;
}

/** Point interpolé à `km` depuis le départ. */
export function pointAt(T, km) {
  const c = T.cum, n = c.length;
  km = Math.min(Math.max(km, 0), c[n - 1]);
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (c[m] < km) lo = m; else hi = m; }
  const seg = c[hi] - c[lo] || 1, u = (km - c[lo]) / seg, a = T.pts[lo], b = T.pts[hi];
  return { lat: a[0] + (b[0] - a[0]) * u, lon: a[1] + (b[1] - a[1]) * u, ele: T.ele[lo] + (T.ele[hi] - T.ele[lo]) * u, i: lo };
}

/** Temps de marche DIN 33466 (min) : 4 km/h, 300 m/h en montée, 500 m/h en descente, × facteur hiver. */
export function din(km, up, down, factor = 1) {
  const h = km / 4, v = up / 300 + down / 500;
  return (Math.max(h, v) + Math.min(h, v) / 2) * 60 * factor;
}
