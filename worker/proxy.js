// Proxy Cloudflare Worker : garde les clés API côté serveur et met en cache.
// Variables à créer dans le Worker : SUNSETHUE_KEY, WINDY_KEY (secrets), ALLOWED_ORIGIN (ex. https://abendrot.pages.dev)
export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const origin = req.headers.get('Origin') || '';
    const allowed = env.ALLOWED_ORIGIN || '*';
    const cors = {
      'Access-Control-Allow-Origin': allowed === '*' ? '*' : allowed,
      'Access-Control-Allow-Methods': 'GET, OPTIONS',
      'Access-Control-Allow-Headers': '*',
      'Vary': 'Origin'
    };
    if (req.method === 'OPTIONS') return new Response(null, { headers: cors });
    if (allowed !== '*' && origin && origin !== allowed) return new Response('Origine refusée', { status: 403, headers: cors });

    const routes = {
      '/sunsethue': { up: 'https://api.sunsethue.com/event', headers: { 'x-api-key': env.SUNSETHUE_KEY }, ttl: 3 * 3600 },
      '/webcams': { up: 'https://api.windy.com/webcams/api/v3/webcams', headers: { 'x-windy-api-key': env.WINDY_KEY }, ttl: 300 }
    };
    const r = routes[url.pathname];
    if (!r) return new Response('Introuvable', { status: 404, headers: cors });

    const upstream = r.up + url.search, cacheKey = new Request(upstream);
    let res = await caches.default.match(cacheKey);
    if (!res) {
      res = await fetch(upstream, { headers: r.headers });
      if (res.ok) {
        res = new Response(res.body, res);
        res.headers.set('Cache-Control', `public, max-age=${r.ttl}`);
        ctx.waitUntil(caches.default.put(cacheKey, res.clone()));
      }
    }
    const out = new Response(res.body, res);
    for (const [k, v] of Object.entries(cors)) out.headers.set(k, v);
    return out;
  }
};
