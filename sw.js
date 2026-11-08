// Service worker : app installable + carte et données disponibles sans réseau
const VERSION = 'v3.4.2';
const SHELL = `shell-${VERSION}`, DATA = 'data-v1', TILES = 'tiles-v1', API = 'api-v1';
const MAX_TILES = 6000;
const CDN = ['https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js', 'https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css'];
const LOCAL = ['./', './index.html', './css/app.css', './js/app.js', './js/sun.js', './js/geo.js', './js/weather.js', './js/map.js', './js/store.js', './js/sources.js', './js/model.js', './data/hikes.json', './manifest.webmanifest', './icons/icon.svg', './icons/icon-192.png', './icons/icon-512.png'];

self.addEventListener('install', e => e.waitUntil((async () => {
  const c = await caches.open(SHELL);
  await Promise.all([...LOCAL, ...CDN].map(u => c.add(new Request(u, { mode: u.startsWith('http') ? 'cors' : 'same-origin' })).catch(() => {})));
  await self.skipWaiting();
})()));

self.addEventListener('activate', e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k.startsWith('shell-') && k !== SHELL) await caches.delete(k);
  await self.clients.claim();
})()));

const isTile = u => u.hostname === 'data.geopf.fr' || u.hostname.endsWith('tile.opentopomap.org') || u.pathname.startsWith('/elevation-tiles-prod/');

let puts = 0;
async function trim() {
  const c = await caches.open(TILES), keys = await c.keys();
  for (let i = 0; i < keys.length - MAX_TILES; i++) await c.delete(keys[i]);
}
async function cacheFirst(req, name) {
  const c = await caches.open(name), hit = await c.match(req);
  if (hit) return hit;
  try {
    const res = await fetch(req);
    if (res.ok || res.type === 'opaque') { c.put(req, res.clone()); if (name === TILES && ++puts % 50 === 0) trim(); }
    return res;
  } catch { return hit || Response.error(); }
}
async function networkFirst(req, name, fallbackUrl) {
  const c = await caches.open(name);
  try { const res = await fetch(req); if (res.ok) c.put(req, res.clone()); return res; }
  catch { return (await c.match(req)) || (fallbackUrl && (await caches.match(fallbackUrl))) || Response.error(); }
}
async function staleWhileRevalidate(req, name) {
  const c = await caches.open(name), hit = await c.match(req);
  const net = fetch(req).then(res => { if (res.ok) c.put(req, res.clone()); return res; }).catch(() => hit);
  return hit || net;
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const u = new URL(req.url);
  if (isTile(u)) return e.respondWith(cacheFirst(req, TILES));
  // OpenFreeMap : le style et l'index changent chaque semaine (réseau d'abord), tuiles, polices et icônes sont versionnées
  if (u.hostname === 'tiles.openfreemap.org') return e.respondWith(/^\/(styles\/|planet\/?$)/.test(u.pathname) ? networkFirst(req, API) : cacheFirst(req, TILES));
  if (['api.open-meteo.com', 'air-quality-api.open-meteo.com', 'overpass-api.de', 'api.sunsethue.com'].includes(u.hostname)) return e.respondWith(networkFirst(req, API));
  if (u.hostname === 'api.windy.com' || u.hostname.endsWith('windy.com')) return; // jetons d'image à 10 min : jamais en cache
  if (req.mode === 'navigate') return e.respondWith(networkFirst(req, SHELL, new URL('./index.html', self.registration.scope).href));
  if (u.origin === location.origin && /\/(data|gpx)\//.test(u.pathname)) return e.respondWith(staleWhileRevalidate(req, DATA));
  if (u.origin === location.origin) return e.respondWith(staleWhileRevalidate(req, SHELL));
  if (['cdn.jsdelivr.net'].includes(u.hostname)) return e.respondWith(cacheFirst(req, SHELL));
});
