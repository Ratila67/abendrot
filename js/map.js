// MapLibre : fonds IGN / OpenTopoMap, relief, 3D, tracé, pré-chargement hors-ligne
const ML = () => window.maplibregl;
export const IGN = 'https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/png&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}';
export const TOPO = ['a', 'b', 'c'].map(s => `https://${s}.tile.opentopomap.org/{z}/{x}/{y}.png`);
const DEM = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
const OFM = dark => `https://tiles.openfreemap.org/styles/${dark ? 'fiord' : 'positron'}`;

/** Carte d'ensemble : fond vectoriel sobre (OpenFreeMap), clair ou sombre au choix, relief éclairé depuis le couchant,
 *  glissé sous les noms de lieux. Au changement de fond, la trace de l'aperçu en cours est reprise telle quelle. */
function overviewStyle(sunAz = 240, dark = false) {
  return (prev, next) => {
    const layers = next.layers.filter(l => l.source !== 'ne2_shaded');   // relief basse résolution du style : remplacé par le nôtre
    const at = layers.findIndex(l => l.type === 'symbol');
    const hs = { id: 'hs', type: 'hillshade', source: 'demhs', paint: dark
      ? { 'hillshade-illumination-direction': Math.round(sunAz) % 360, 'hillshade-exaggeration': 0.6,
          'hillshade-shadow-color': '#04070A', 'hillshade-highlight-color': '#C99A62', 'hillshade-accent-color': '#0A1016' }
      : { 'hillshade-illumination-direction': Math.round(sunAz) % 360, 'hillshade-exaggeration': 0.45,
          'hillshade-shadow-color': '#3B4652', 'hillshade-highlight-color': '#FFE6B8', 'hillshade-accent-color': '#56616B' } };
    layers.splice(at < 0 ? layers.length : at, 0, hs);
    const route = prev?.sources?.route;
    if (route) layers.push(...prev.layers.filter(l => l.source === 'route'));
    return { ...next, layers, sources: { ...next.sources, ...(route ? { route } : {}),
      demhs: { type: 'raster-dem', tiles: [DEM], encoding: 'terrarium', tileSize: 256, maxzoom: 12, attribution: 'Terrain Tiles' } } };
  };
}

export function setOverviewTheme(map, dark, sunAz) {
  map.setStyle(OFM(dark), { transformStyle: overviewStyle(sunAz, dark) });
}

function style(base) {
  const dem = { type: 'raster-dem', tiles: [DEM], encoding: 'terrarium', tileSize: 256, maxzoom: 13, attribution: 'Terrain Tiles' };
  return {
    version: 8,
    sources: {
      ign: { type: 'raster', tiles: [IGN], tileSize: 256, maxzoom: 19, attribution: '© IGN Plan IGN' },
      topo: { type: 'raster', tiles: TOPO, tileSize: 256, maxzoom: 17, attribution: '© OpenTopoMap (CC-BY-SA), © OpenStreetMap' },
      dem, demhs: { ...dem }
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#dfe6ea' } },
      { id: 'ign', type: 'raster', source: 'ign', layout: { visibility: base === 'ign' ? 'visible' : 'none' } },
      { id: 'topo', type: 'raster', source: 'topo', layout: { visibility: base === 'topo' ? 'visible' : 'none' } },
      { id: 'hs', type: 'hillshade', source: 'demhs', paint: { 'hillshade-exaggeration': 0.25, 'hillshade-shadow-color': '#24323a' } }
    ]
  };
}

export function createMap(container, { center, zoom = 12, base = 'ign', overview = false, dark = false, sunAz, bounds } = {}) {
  const map = new (ML().Map)({
    container, style: overview ? undefined : style(base), center, zoom, attributionControl: { compact: true },
    ...(bounds ? { bounds, fitBoundsOptions: { padding: { top: 100, bottom: 100, left: 30, right: 64 }, maxZoom: 10 } } : {}),
    ...(overview ? { maxPitch: 0, dragRotate: false, pitchWithRotate: false, touchPitch: false } : { maxPitch: 80 })
  });
  if (overview) { map.touchZoomRotate.disableRotation(); setOverviewTheme(map, dark, sunAz); }
  map.addControl(new (ML().NavigationControl)(overview ? { showCompass: false } : { visualizePitch: true }), 'top-right');
  map.addControl(new (ML().ScaleControl)({ unit: 'metric' }), 'bottom-left');
  map.on('error', e => console.warn('Carte :', e?.error?.message));
  return map;
}

export function setBase(map, base) {
  for (const id of ['ign', 'topo']) map.setLayoutProperty(id, 'visibility', id === base ? 'visible' : 'none');
}

export function setRoute(map, T, color) {
  const data = { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: T.pts.map(p => [p[1], p[0]]) } };
  if (map.getSource('route')) return map.getSource('route').setData(data);
  map.addSource('route', { type: 'geojson', data });
  const layout = { 'line-join': 'round', 'line-cap': 'round' };
  map.addLayer({ id: 'route-casing', type: 'line', source: 'route', layout, paint: { 'line-color': '#ffffff', 'line-width': 8 } });
  map.addLayer({ id: 'route', type: 'line', source: 'route', layout, paint: { 'line-color': color, 'line-width': 4.5 } });
}

export function clearRoute(map) {
  map.getSource('route')?.setData({ type: 'FeatureCollection', features: [] });
}

export function fitTrack(map, T, padding = 40, duration = 0, maxZoom = 15) {
  const b = new (ML().LngLatBounds)();
  T.pts.forEach(p => b.extend([p[1], p[0]]));
  map.fitBounds(b, { padding, duration, maxZoom });
}

export const marker = (el, lngLat, opts = {}) => new (ML().Marker)({ element: el, ...opts }).setLngLat(lngLat);
export const popup = html => new (ML().Popup)({ offset: 14, closeButton: false }).setHTML(html);

export function geolocate(map, onPos) {
  const g = new (ML().GeolocateControl)({ positionOptions: { enableHighAccuracy: true }, trackUserLocation: true, showAccuracyCircle: true });
  map.addControl(g, 'top-right');
  g.on('geolocate', e => onPos(e.coords));
  g.on('error', e => onPos(null, e));
  return g;
}

/** Vue 3D : caméra posée sur le spot, regard dans l'axe du coucher. */
export function set3D(map, on, { lat, lon, bearing } = {}) {
  if (!on) { map.setTerrain(null); map.easeTo({ pitch: 0, bearing: 0, duration: 800 }); return; }
  map.setTerrain({ source: 'dem', exaggeration: 1.3 });
  try { map.setSky?.({ 'sky-color': '#57568F', 'horizon-color': '#E5727E', 'fog-color': '#F1C266', 'sky-horizon-blend': 0.6, 'horizon-fog-blend': 0.6, 'fog-ground-blend': 0.4 }); } catch (e) { /* ciel optionnel */ }
  const d = 2.5, b = bearing * Math.PI / 180;
  const center = [lon + d / (111.32 * Math.cos(lat * Math.PI / 180)) * Math.sin(b), lat + d / 111.32 * Math.cos(b)];
  map.flyTo({ center, zoom: 13, pitch: 74, bearing, duration: 1800, essential: true });
}

const lon2x = (lon, z) => Math.floor((lon + 180) / 360 * 2 ** z);
const lat2y = (lat, z) => { const r = lat * Math.PI / 180; return Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * 2 ** z); };

/** Liste des tuiles couvrant la bbox [minLon,minLat,maxLon,maxLat], en baissant le zoom max si on dépasse le plafond. */
export function tileUrls(bbox, base, zMin = 11, zMax = 16, cap = 900) {
  const tpl = base === 'ign' ? IGN : TOPO[0];
  let urls = [];
  for (let zm = zMax; zm >= zMin; zm--) {
    urls = [];
    for (let z = zMin; z <= zm; z++) {
      const [x0, x1, y0, y1] = [lon2x(bbox[0], z), lon2x(bbox[2], z), lat2y(bbox[3], z), lat2y(bbox[1], z)];
      for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) urls.push(tpl.replace('{z}', z).replace('{x}', x).replace('{y}', y));
    }
    if (urls.length <= cap) break;
  }
  return urls;
}

/** Télécharge les tuiles (le service worker les met en cache). */
export async function prefetch(urls, onProgress, concurrency = 6) {
  let i = 0, done = 0, fail = 0;
  const worker = async () => {
    while (i < urls.length) {
      const u = urls[i++];
      try { const r = await fetch(u, { mode: 'cors' }); if (!r.ok) fail++; } catch { fail++; }
      onProgress?.(++done, urls.length, fail);
    }
  };
  await Promise.all(Array.from({ length: concurrency }, worker));
  return { done, fail };
}
