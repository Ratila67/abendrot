// Open-Meteo (sans clé) : prévisions horaires multi-points + altitudes MNT + score sunset
const VARS = 'temperature_2m,apparent_temperature,cloud_cover_low,cloud_cover_mid,cloud_cover_high,precipitation,visibility,wind_speed_10m,wind_gusts_10m,snow_depth,freezing_level_height,temperature_850hPa';
export const PLAIN = { lat: 48.08, lon: 7.36, alt: 190 }; // Colmar : référence plaine pour détecter l'inversion

const pad = n => String(n).padStart(2, '0');

const MF = 'meteofrance_seamless', GLOBAL = 'best_match';

/** Fusionne heure par heure : Météo-France (AROME 1,3 km puis ARPEGE, ~4 jours) sinon multi-modèle global (16 jours). */
function mergeModels(o) {
  const h = o.hourly, out = { time: h.time, model: [] };
  const vars = VARS.split(',');
  for (const v of vars) out[v] = h.time.map((_, i) => h[`${v}_${MF}`]?.[i] ?? h[`${v}_${GLOBAL}`]?.[i] ?? h[v]?.[i] ?? null);
  out.model = h.time.map((_, i) => h[`cloud_cover_low_${MF}`]?.[i] != null ? 'Météo-France AROME/ARPEGE' : 'multi-modèle global');
  return { ...o, hourly: out };
}

/** Prévisions de tous les points (Open-Meteo accepte des listes de coordonnées), par lots de 80 pour garder des URL courtes. */
export async function fetchForecast(points) {
  const all = [...points, PLAIN], lots = [];
  for (let i = 0; i < all.length; i += 80) lots.push(all.slice(i, i + 80));
  const j = (await Promise.all(lots.map(fetchLot))).flat().map(mergeModels);
  const plain = j.pop();
  return { spots: j, plain, fetchedAt: Date.now() };
}
async function fetchLot(pts) {
  const build = models => {
    const u = new URL('https://api.open-meteo.com/v1/forecast');
    u.searchParams.set('latitude', pts.map(p => p.lat.toFixed(4)).join(','));
    u.searchParams.set('longitude', pts.map(p => p.lon.toFixed(4)).join(','));
    u.searchParams.set('elevation', pts.map(p => Math.round(p.alt)).join(','));
    u.searchParams.set('hourly', VARS);
    u.searchParams.set('timezone', 'Europe/Paris');
    u.searchParams.set('forecast_days', '16');
    if (models) u.searchParams.set('models', models);
    return u;
  };
  let r = await fetch(build(`${MF},${GLOBAL}`));
  if (!r.ok) r = await fetch(build(null)); // repli si la combinaison de modèles est refusée
  if (!r.ok) throw new Error(`Météo indisponible (${r.status})`);
  const j = await r.json();
  return Array.isArray(j) ? j : [j];
}

/** Altitudes MNT (Copernicus 90 m) par lots de 100 points. */
export async function fetchElevations(pts) {
  const out = [];
  for (let i = 0; i < pts.length; i += 100) {
    const ch = pts.slice(i, i + 100);
    const u = `https://api.open-meteo.com/v1/elevation?latitude=${ch.map(p => p[0].toFixed(5)).join(',')}&longitude=${ch.map(p => p[1].toFixed(5)).join(',')}`;
    const r = await fetch(u);
    if (!r.ok) throw new Error('Altitudes indisponibles');
    out.push(...(await r.json()).elevation);
  }
  return out;
}

/** Index horaire le plus proche de `date` (heure locale Europe/Paris). */
export function hourIndex(series, date) {
  const d = new Date(date.getTime() + 30 * 6e4);
  const key = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:00`;
  return series?.hourly?.time?.indexOf(key) ?? -1;
}

/**
 * Score heuristique 0-100 de qualité de coucher de soleil.
 * Horizon dégagé (peu de nuages bas) + nuages moyens/hauts épars (ils s'enflamment) = jackpot.
 * Inversion (plaine sous stratus, air doux à 850 hPa) + spot en altitude = mer de nuages.
 */
export function scoreSunset(s, plain, i, spotAlt) {
  const g = (o, k) => o?.hourly?.[k]?.[i] ?? null;
  const low = g(s, 'cloud_cover_low') ?? 0, mid = g(s, 'cloud_cover_mid') ?? 0, high = g(s, 'cloud_cover_high') ?? 0;
  const pr = g(s, 'precipitation') ?? 0, vis = g(s, 'visibility');
  const pLow = g(plain, 'cloud_cover_low') ?? 0, pT = g(plain, 'temperature_2m'), t850 = g(s, 'temperature_850hPa');
  const inversion = spotAlt >= 850 && pLow >= 60 && t850 != null && pT != null && t850 > pT - 2;
  let sc = 80; const why = [];
  if (inversion) { sc += 10; why.push('Mer de nuages probable sous le spot'); }
  else { sc -= low * 0.75; if (low > 50) why.push('Nuages bas : horizon bouché'); }
  const color = Math.max(mid, high);
  if (color >= 20 && color <= 70) { sc += 15; why.push('Nuages moyens et hauts épars : bonnes chances de couleurs'); }
  else if (color > 90) { sc -= 30; why.push('Couvert en altitude'); }
  else if (color < 20 && !inversion && low < 30) why.push('Ciel limpide : beau, mais peu de couleurs');
  if (pr > 0.2) { sc -= 40; why.push('Précipitations'); }
  if (vis != null && vis < 10000) { sc -= 15; why.push('Visibilité réduite'); }
  return {
    score: Math.round(Math.max(0, Math.min(100, sc))), inversion, why, low, mid, high,
    temp: g(s, 'temperature_2m'), feels: g(s, 'apparent_temperature'), wind: g(s, 'wind_speed_10m'),
    gust: g(s, 'wind_gusts_10m'), snow: g(s, 'snow_depth'), freezing: g(s, 'freezing_level_height')
  };
}

export const scoreLabel = sc => sc >= 75 ? 'Feu d’artifice possible' : sc >= 50 ? 'Correct' : sc >= 25 ? 'Bof' : 'Gris';
