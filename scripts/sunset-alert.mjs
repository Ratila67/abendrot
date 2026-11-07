// Alerte Telegram « sunset du jour » : réutilise exactement le scoring de l'app.
// Usage local : TZ=Europe/Paris node scripts/sunset-alert.mjs   (sans token : affiche le message)
import { readFile } from 'node:fs/promises';
import { effectiveSunset } from '../js/sun.js';
import { fetchForecast, hourIndex, scoreSunset } from '../js/weather.js';
import { sunsethue } from '../js/sources.js';

const hikes = JSON.parse(await readFile(new URL('../data/hikes.json', import.meta.url), 'utf8'));
const only = (process.env.HIKES || '').split(',').map(s => s.trim()).filter(Boolean);
const MIN = Number(process.env.MIN_SCORE || 70);
const list = hikes.filter(h => h.sunsetScore >= 4 && (!only.length || only.includes(h.id)));
const spotOf = h => h.spots.find(s => s.sunset) || h.start;

const wx = await fetchForecast(list.map(spotOf));
const day = new Date(); day.setDate(day.getDate() + Number(process.env.DAY_OFFSET || 0)); day.setHours(12, 0, 0, 0);

const res = list.map((h, i) => {
  const s = spotOf(h), set = effectiveSunset(day, s.lat, s.lon, s.alt, h.horizon || 0);
  const k = hourIndex(wx.spots[i], set);
  return k < 0 ? null : { h, set, ...scoreSunset(wx.spots[i], wx.plain, k, s.alt) };
}).filter(Boolean).sort((a, b) => b.score - a.score);

const top = res.filter(r => r.score >= MIN).slice(0, 3);
if (!top.length) { console.log(`Aucun spot ≥ ${MIN} aujourd’hui (meilleur : ${res[0]?.h.name} ${res[0]?.score}).`); process.exit(0); }

if (process.env.SUNSETHUE_KEY) {
  const ds = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
  for (const r of top) {
    const s = spotOf(r.h);
    try { const q = await sunsethue({ sunsethue: process.env.SUNSETHUE_KEY }, { lat: s.lat, lon: s.lon, date: ds }); if (q?.available) r.shue = q.quality; } catch { /* quota ou réseau */ }
  }
}
const fmt = d => d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Paris' });
const lines = top.map(r => `• ${r.h.name} : ${r.score}/100, coucher ${fmt(r.set)}, rafales ${Math.round(r.gust)} km/h${r.shue != null ? `, Sunsethue ${r.shue} %` : ''}${r.inversion ? ', mer de nuages probable' : ''}`);
const url = process.env.APP_URL ? `\n${process.env.APP_URL}#${top[0].h.id}` : '';
const text = `Abendrot : bon plan sunset ${process.env.DAY_OFFSET === '1' ? 'demain' : 'ce soir'}\n${lines.join('\n')}${url}`;

if (!process.env.TELEGRAM_TOKEN) { console.log(text); process.exit(0); }
const r = await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_TOKEN}/sendMessage`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ chat_id: process.env.TELEGRAM_CHAT_ID, text, disable_web_page_preview: true })
});
if (!r.ok) { console.error(await r.text()); process.exit(1); }
console.log('Alerte envoyée.');
