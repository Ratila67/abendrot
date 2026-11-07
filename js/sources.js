// Sources complémentaires. Sans clé : OSM (Overpass), aérosols (Open-Meteo/CAMS).
// Avec clé gratuite (Réglages) : Sunsethue (qualité du coucher), Windy (webcams).
const mem = new Map();
async function cached(key, ttlMs, fn) {
  const hit = mem.get(key);
  if (hit && Date.now() - hit.t < ttlMs) return hit.v;
  const v = await fn(); mem.set(key, { t: Date.now(), v }); return v;
}
async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
/** Passe par ton proxy Cloudflare s'il est configuré (clés côté serveur), sinon appel direct avec la clé locale. */
function call(settings, proxyPath, directUrl, headers) {
  if (settings.proxy) return getJSON(settings.proxy.replace(/\/+$/, '') + proxyPath);
  return getJSON(directUrl, { headers });
}

/** Sunsethue : modèle « ray-based », 0-100 %, 3 jours d'horizon, 1000 crédits/jour gratuits. */
export async function sunsethue(settings, { lat, lon, date }) {
  if (!settings.sunsethue && !settings.proxy) return null;
  const q = `latitude=${lat.toFixed(3)}&longitude=${lon.toFixed(3)}&date=${date}&type=sunset`;
  return cached(`shue:${q}`, 3 * 3600e3, async () => {
    const j = await call(settings, `/sunsethue?${q}`, `https://api.sunsethue.com/event?${q}`, { 'x-api-key': settings.sunsethue });
    const d = j?.data;
    if (!d || d.model_data === false || d.quality == null) return { available: false };
    return { available: true, quality: Math.round(d.quality * 100), text: d.quality_text, cloud: d.cloud_cover != null ? Math.round(d.cloud_cover * 100) : null, direction: d.direction };
  });
}

/** Windy Webcams v3 : images à jeton valable ~10 min en gratuit, donc jamais mises en cache. */
export async function webcams(settings, { lat, lon, radiusKm = 20, limit = 4 }) {
  if (!settings.windy && !settings.proxy) return null;
  const q = `nearby=${lat.toFixed(4)},${lon.toFixed(4)},${radiusKm}&include=images,location,urls&limit=${limit}`;
  const j = await call(settings, `/webcams?${q}`, `https://api.windy.com/webcams/api/v3/webcams?${q}`, { 'x-windy-api-key': settings.windy });
  return (j.webcams || []).map(w => ({
    id: w.webcamId ?? w.id, title: w.title, city: w.location?.city,
    img: w.images?.current?.preview || w.images?.current?.thumbnail || w.images?.current?.icon,
    updated: w.lastUpdatedOn, url: w.urls?.detail || `https://www.windy.com/webcams/${w.webcamId ?? w.id}`
  })).filter(w => w.img);
}

/** OpenStreetMap via Overpass : abris, points de vue, eau, sommets, fermes-auberges autour du tracé. */
const POI_KIND = t =>
  t.amenity === 'shelter' || t.tourism === 'wilderness_hut' || t.tourism === 'alpine_hut' ? 'hut'
  : /ferme[- ]auberge/i.test(t.name || '') ? 'food'
  : t.tourism === 'viewpoint' ? 'eye'
  : t.natural === 'spring' || t.amenity === 'drinking_water' ? 'drop'
  : t.natural === 'peak' ? 'peak' : null;
export const POI_LABEL = { hut: 'Abri', food: 'Ferme-auberge', eye: 'Point de vue', drop: 'Eau', peak: 'Sommet' };

export async function osmPOIs([s, w, n, e]) {
  const b = [s, w, n, e].map(x => x.toFixed(4)).join(',');
  const q = `[out:json][timeout:20];(nwr["amenity"="shelter"](${b});nwr["tourism"~"^(wilderness_hut|alpine_hut|viewpoint)$"](${b});node["natural"="spring"](${b});node["amenity"="drinking_water"](${b});node["natural"="peak"]["name"](${b});nwr["name"~"[Ff]erme[- ][Aa]uberge"](${b}););out center tags 120;`;
  return cached(`osm:${b}`, 24 * 3600e3, async () => {
    const j = await getJSON(`https://overpass-api.de/api/interpreter?data=${encodeURIComponent(q)}`);
    return (j.elements || []).map(el => {
      const t = el.tags || {}, kind = POI_KIND(t);
      const lat = el.lat ?? el.center?.lat, lon = el.lon ?? el.center?.lon;
      return kind && lat != null ? { kind, lat, lon, name: t.name || POI_LABEL[kind], ele: t.ele ? Math.round(+t.ele) : null } : null;
    }).filter(Boolean);
  });
}

/** Épaisseur optique des aérosols (CAMS) : < 0,1 air très pur, Alpes nettes ; > 0,3 brume. */
export async function aerosols({ lat, lon }) {
  const u = `https://air-quality-api.open-meteo.com/v1/air-quality?latitude=${lat.toFixed(3)}&longitude=${lon.toFixed(3)}&hourly=aerosol_optical_depth,dust&timezone=Europe%2FParis&forecast_days=5`;
  return cached(`aod:${u}`, 3 * 3600e3, () => getJSON(u));
}
export function alpsVisibility(aod, visibilityM, lowCloud) {
  if (aod == null) return null;
  if (lowCloud > 70) return { level: 'no', text: 'Alpes probablement masquées par les nuages' };
  if (aod < 0.1 && (visibilityM ?? 50000) >= 40000) return { level: 'yes', text: 'Air très pur : Alpes nettes si le ciel est dégagé au sud' };
  if (aod < 0.25) return { level: 'maybe', text: 'Alpes visibles mais voilées' };
  return { level: 'no', text: 'Air brumeux : Alpes invisibles, mais couleurs plus chaudes' };
}
