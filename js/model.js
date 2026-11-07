// Modèle entraîné sur ta vérité terrain (truth/train.py → data/model.json).
// Régression logistique exportée en JSON : imputation, standardisation, puis sigmoïde.
let M = null;

export async function loadModel() {
  try { const r = await fetch('./data/model.json', { cache: 'no-cache' }); if (r.ok) M = await r.json(); } catch { /* pas encore de modèle */ }
  return M;
}
export const modelInfo = target => M?.targets?.[target] || null;

/** Mêmes variables, mêmes unités que truth/snapshot.py. */
export function modelFeatures({ series, plain, k, alt, heuristic, leadDay, date, sunsethue = null, aod = null }) {
  const g = (o, key) => o?.hourly?.[key]?.[k] ?? null;
  const t850 = g(series, 'temperature_850hPa'), pt = g(plain, 'temperature_2m'), vis = g(series, 'visibility');
  const month = Number(date.slice(5, 7));
  const mid = g(series, 'cloud_cover_mid'), high = g(series, 'cloud_cover_high');
  const color = mid == null && high == null ? null : Math.max(mid ?? 0, high ?? 0);
  return {
    cloud_low: g(series, 'cloud_cover_low'), cloud_mid: g(series, 'cloud_cover_mid'), cloud_high: g(series, 'cloud_cover_high'),
    precip: g(series, 'precipitation'), visibility_km: vis == null ? null : vis / 1000, gust: g(series, 'wind_gusts_10m'),
    t850_minus_plain: t850 != null && pt != null ? t850 - pt : null, plain_low: g(plain, 'cloud_cover_low'),
    color_band: color == null ? null : (color >= 20 && color <= 70 ? 1 : 0),
    alt, heuristic, sunsethue, aod, lead_day: leadDay,
    month_sin: Math.sin(2 * Math.PI * month / 12), month_cos: Math.cos(2 * Math.PI * month / 12)
  };
}

export function predict(target, f) {
  const t = modelInfo(target);
  if (!t) return null;
  let z = t.intercept;
  t.features.forEach((name, i) => {
    let v = f[name];
    if (v == null || Number.isNaN(v)) v = t.impute[i] ?? t.mean[i];
    z += t.coef[i] * ((v - t.mean[i]) / (t.scale[i] || 1));
  });
  return 1 / (1 + Math.exp(-z));
}
