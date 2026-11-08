"""Générateur de randos « spot d'abord » : points de vue OpenStreetMap ouverts sur le couchant, parking, aller-retour.

1. Overpass : points de vue nommés (tourism=viewpoint) et tours d'observation de la zone. Une étiquette
   `direction` qui ne regarde pas vers l'ouest les écarte.
2. Relief (tuiles Terrarium, zoom 11, environ 50 m) : angle d'horizon jusqu'à 50 km, tous les 5° sur l'arc des
   couchers de l'année (235° en décembre à 300° en juin). Le spot est retenu si au moins 35 % de l'arc est dégagé
   (horizon à 1° ou moins) et s'il domine d'au moins 50 m le point le plus bas à 5 km vers l'ouest. Le relief ne connaît pas la forêt : c'est l'étiquette OSM qui atteste la vue.
3. Parkings OSM entre 0,7 et 4,5 km à vol d'oiseau, du plus proche au plus loin : premier aller-retour
   BRouter qui atteint le spot, avec moins de 20 % de route et 1 à 8 km jusqu'au spot.
4. data/hikes-auto.json et gpx/auto/<id>.gpx. Les spots à moins de 800 m d'une rando de hikes.json, ou à
   moins de 400 m d'un spot mieux placé, sont écartés. hikes.json (randos vérifiées) n'est jamais modifié.

Usage : python scripts/spot_gen.py [--limit N] [--bbox s,w,n,e] [--report revue.md]
Les réponses d'Overpass, les tuiles et les itinéraires sont gardés dans .cache/spotgen : relancer ne refait
que ce qui manque. Données : © contributeurs OpenStreetMap (ODbL), itinéraires BRouter, relief Terrain Tiles (AWS).
"""
import argparse
import hashlib
import io
import json
import math
import time
from pathlib import Path

import gpx_route as G

ROOT = G.ROOT
CACHE = ROOT / ".cache" / "spotgen"
BBOX = (47.55, 6.6, 49.15, 8.35)          # sud, ouest, nord, est : Vosges, Alsace, Forêt-Noire, Palatinat du sud
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
DEPTS_FR = "25|54|55|57|67|68|70|88|90"   # départements de la zone : le polygone de la France entière fait tomber Overpass
DEM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
UA = {"User-Agent": "abendrot-spotgen/1.0 (+https://github.com/Ratila67/abendrot)"}
Z = 11
ARC = range(235, 301, 5)                 # azimuts du coucher sur l'année, en degrés
OPEN_DEG = 1.0                           # horizon dégagé : à 1° ou moins
MIN_OPEN = 0.35                          # part minimale de l'arc dégagée
MIN_DOMINANCE_M = 50                     # le spot domine d'au moins 50 m le point le plus bas à 5 km vers l'ouest
EYE_M = 2.0
REFRACTION = 0.13
PARK_KM = (0.7, 4.5)
MAX_TRIES = 4
TO_SPOT_KM = (1.0, 8.0)
NEAR_CURATED_KM = 0.8
NEAR_SPOT_KM = 0.4
SIMPLIFY_M = 4.0

COMPASS = {k: i * 22.5 for i, k in enumerate("N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}
COMPASS.update({k.replace("W", "O"): v for k, v in COMPASS.items() if "W" in k})   # étiquettes en français (O pour ouest)


# ---------- étiquette direction ----------
def _deg(tok):
    tok = tok.strip().upper()
    if tok in COMPASS:
        return COMPASS[tok]
    try:
        return float(tok) % 360
    except ValueError:
        return None


def direction_arcs(tag):
    """Arcs vus depuis le point, d'après l'étiquette `direction` : [(début, fin)] en degrés, sens horaire.
    Une direction seule couvre ±60°, « 0-360 » tout l'horizon. None si l'étiquette est absente ou illisible."""
    if not tag:
        return None
    arcs = []
    for part in tag.replace(",", ";").split(";"):
        part = part.strip()
        if "-" in part[1:]:
            a, b = part.split("-", 1)
            da, db = _deg(a), _deg(b)
            if da is not None and db is not None:
                arcs.append((0, 360) if da == db else (da, db))
        elif part:
            d = _deg(part)
            if d is not None:
                arcs.append(((d - 60) % 360, (d + 60) % 360))
    return arcs or None


def _in_arc(az, arc):
    a, b = arc
    if (a, b) == (0, 360):
        return True
    return (az - a) % 360 <= (b - a) % 360


def faces_sunset(tag):
    arcs = direction_arcs(tag)
    return arcs is None or any(_in_arc(az, arc) for az in ARC for arc in arcs)


# ---------- relief ----------
_tiles = {}


def _tile(x, y):
    import numpy as np
    from PIL import Image
    if (x, y) not in _tiles:
        p = CACHE / "dem" / f"{Z}_{x}_{y}.png"
        if not p.exists():
            import requests
            r = requests.get(DEM.format(z=Z, x=x, y=y), headers=UA, timeout=60)
            r.raise_for_status()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(r.content)
        a = np.asarray(Image.open(io.BytesIO(p.read_bytes())).convert("RGB"), dtype="float32")
        _tiles[(x, y)] = a[:, :, 0] * 256 + a[:, :, 1] + a[:, :, 2] / 256 - 32768
    return _tiles[(x, y)]


def elevation(lat, lon):
    """Altitude du MNT (pixel le plus proche, environ 50 m au zoom 11)."""
    n = 2 ** Z * 256
    px = (lon + 180) / 360 * n
    r = math.radians(lat)
    py = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n
    x, y = int(px), int(py)
    return float(_tile(x // 256, y // 256)[y % 256, x % 256])


def _ray_distances():
    d = [0.2 + 0.05 * i for i in range(17)]                   # 200 m à 1 km, tous les 50 m
    d += [1.0 + 0.1 * i for i in range(1, 41)]                 # jusqu'à 5 km, tous les 100 m
    d += [5.0 + 0.25 * i for i in range(1, 81)]                # jusqu'à 25 km, tous les 250 m
    d += [25.0 + 0.5 * i for i in range(1, 51)]                # jusqu'à 50 km : la crête des Vosges vue de la plaine
    return d


RAY = _ray_distances()


def _h0(lat, lon, extra_m, elev):
    """Hauteur de l'œil : point le plus haut à 40 m près (le pixel de 50 m arrondit les sommets), plus la tour."""
    k = 111.32
    return max(elev(lat + dy / k, lon + dx / (k * math.cos(math.radians(lat))))
               for dy, dx in ((0, 0), (0.04, 0), (-0.04, 0), (0, 0.04), (0, -0.04))) + EYE_M + extra_m


def dominance(lat, lon, extra_m=0.0, elev=None):
    """De combien le spot domine le point le plus bas à 5 km dans l'arc du coucher : une vue plonge, une plaine non."""
    elev = elev or elevation
    k, h0 = 111.32, _h0(lat, lon, extra_m, elev)
    low = min(elev(lat + d * math.cos(math.radians(az)) / k, lon + d * math.sin(math.radians(az)) / (k * math.cos(math.radians(lat))))
              for az in ARC for d in RAY if d <= 5)
    return h0 - low


def horizon(lat, lon, extra_m=0.0, elev=None):
    """Angle d'horizon (degrés, courbure et réfraction comprises) pour chaque azimut de l'arc du coucher."""
    elev = elev or elevation
    k = 111.32
    h0 = _h0(lat, lon, extra_m, elev)
    out = {}
    for az in ARC:
        s, c = math.sin(math.radians(az)), math.cos(math.radians(az))
        best = -90.0
        for d in RAY:
            h = elev(lat + d * c / k, lon + d * s / (k * math.cos(math.radians(lat))))
            drop = (d * 1000) ** 2 / (2 * 6371000 / (1 - REFRACTION))
            best = max(best, math.degrees(math.atan2(h - h0 - drop, d * 1000)))
        out[az] = best
    return out


def openness(hz):
    """Part de l'arc dégagée, et l'hiver (235 à 250°) ou l'été (285 à 300°) en particulier."""
    ok = {az: a <= OPEN_DEG for az, a in hz.items()}
    part = lambda lo, hi: sum(ok[a] for a in ok if lo <= a <= hi) / sum(1 for a in ok if lo <= a <= hi)
    return {"share": sum(ok.values()) / len(ok), "winter": part(235, 250), "summer": part(285, 300)}


def sunset_score(share):
    return 5 if share >= 0.85 else 4 if share >= 0.6 else 3 if share >= MIN_OPEN else 2


def view_text(o):
    when = ("toute l’année" if o["winter"] >= 0.5 and o["summer"] >= 0.5 else
            "surtout l’hiver" if o["winter"] >= 0.5 else "surtout l’été" if o["summer"] >= 0.5 else "à la mi-saison")
    return f"Ouest dégagé sur {o['share']:.0%} de l’arc du coucher, {when}".replace("%", " %")


# ---------- OpenStreetMap ----------
def overpass(query, name):
    p = CACHE / f"{name}.json"
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    import requests
    for i in range(6):   # serveurs publics souvent saturés (502, 504, 429) : on alterne et on patiente
        try:
            r = requests.post(OVERPASS[i % 2], data={"data": query}, headers=UA, timeout=300)
            if r.ok:
                break
            print(f"  Overpass {r.status_code}, nouvel essai")
        except requests.RequestException as e:
            print(f"  Overpass injoignable ({e.__class__.__name__}), nouvel essai")
        time.sleep(20)
    else:
        raise RuntimeError(f"Overpass indisponible pour {name}")
    els = r.json()["elements"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(els), "utf-8")
    time.sleep(2)
    return els


def _ll(e):
    return (e["lat"], e["lon"]) if "lat" in e else (e["center"]["lat"], e["center"]["lon"])


def candidates(bbox):
    b = ",".join(map(str, bbox))
    sel = (f'node["tourism"="viewpoint"]["name"]({b});node["man_made"="tower"]["tower:type"="observation"]["name"]({b});'
           f'way["man_made"="tower"]["tower:type"="observation"]["name"]({b});')
    els = overpass(f"[out:json][timeout:180];({sel});out center tags;", "viewpoints")
    fr = {(e["type"], e["id"]) for e in overpass(
        f'[out:json][timeout:300];area["admin_level"="6"]["ref:INSEE"~"^({DEPTS_FR})$"]->.fr;({sel.replace(f"({b})", f"(area.fr)({b})")});out ids;', "viewpoints_fr")}
    out = []
    for e in els:
        t = e.get("tags", {})
        lat, lon = _ll(e)
        tower = t.get("man_made") == "tower"
        try:
            height = float(str(t.get("height", "")).replace("m", "").strip() or (15 if tower else 0))
        except ValueError:
            height = 15.0 if tower else 0.0
        out.append({"osm": f"{e['type'][0]}{e['id']}", "name": t["name"], "lat": lat, "lon": lon, "tower": tower,
                    "height": height, "direction": t.get("direction"), "country": "FR" if (e["type"], e["id"]) in fr else "DE"})
    return out


def parkings(bbox):
    b = ",".join(map(str, bbox))
    els = overpass(f'[out:json][timeout:300];(node["amenity"="parking"]({b});way["amenity"="parking"]({b}););out center tags;', "parkings")
    bad_access, bad_kind = {"private", "no", "customers", "permit", "delivery"}, {"underground", "multi-storey", "rooftop"}
    return [{"lat": _ll(e)[0], "lon": _ll(e)[1], "name": e.get("tags", {}).get("name")} for e in els
            if e.get("tags", {}).get("access") not in bad_access and e.get("tags", {}).get("parking") not in bad_kind]


def places(bbox):
    b = ",".join(map(str, bbox))
    els = overpass(f'[out:json][timeout:180];node["place"~"^(town|village|city)$"]["name"]({b});out center tags;', "places")
    return [{"lat": e["lat"], "lon": e["lon"], "name": e["tags"]["name"]} for e in els]


def nearest(items, lat, lon, max_km=12):
    best = min(items, key=lambda p: G.hav(lat, lon, p["lat"], p["lon"]), default=None)
    return best if best and G.hav(lat, lon, best["lat"], best["lon"]) <= max_km else None


# ---------- itinéraire ----------
def route_cached(waypoints):
    key = hashlib.sha1(json.dumps([[round(w[0], 5), round(w[1], 5)] for w in waypoints]).encode()).hexdigest()[:16]
    p = CACHE / "brouter" / f"{key}.json"
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    for attempt in range(5):
        try:
            f = G.route(waypoints)
            break
        except Exception as e:  # noqa: BLE001
            if "BRouter 400" in str(e):   # pas d'itinéraire entre ces points : définitif, on le retient
                f = {"error": str(e)[:200]}
                break
            wait = 60 * (attempt + 1)     # 403 « retry later », 5xx, délai dépassé : le serveur public sature
            print(f"  BRouter indisponible ({str(e)[:40]}), pause de {wait} s")
            time.sleep(wait)
    else:
        raise RuntimeError("BRouter indisponible après 5 essais")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(f), "utf-8")
    if "localhost" not in G.BROUTER and "127.0.0.1" not in G.BROUTER:
        time.sleep(2.5)   # serveur public : une requête à la fois, sans le saturer
    return f


def pick_parking(spot, parks, route=route_cached):
    """Premier parking (du plus proche au plus loin) dont l'aller-retour atteint le spot sans trop de route.
    Rend (parking, résumé de trace) ou (None, raison du dernier refus)."""
    near = sorted(((G.hav(spot["lat"], spot["lon"], p["lat"], p["lon"]), p) for p in parks), key=lambda t: t[0])
    tried, why = [], "aucun parking entre 0,7 et 4,5 km"
    for d, p in near:
        if d < PARK_KM[0]:
            continue
        if d > PARK_KM[1] or len(tried) >= MAX_TRIES:
            break
        if any(G.hav(p["lat"], p["lon"], q["lat"], q["lon"]) < 0.25 for q in tried):
            continue
        tried.append(p)
        f = route([[p["lat"], p["lon"]], [spot["lat"], spot["lon"]], [p["lat"], p["lon"]]])
        if "error" in f:
            why = "pas d’itinéraire"
            continue
        s = G.summary(f)
        s.update(G.track_metrics(s["coords"], spot))
        if s["spot_m"] > G.MAX_SNAP_M:
            why = "spot hors des sentiers"
        elif s["road_share"] > G.MAX_ROAD:
            why = "plus de 20 % de route"
        elif not TO_SPOT_KM[0] <= s["to_spot_km"] <= TO_SPOT_KM[1]:
            why = "moins de 1 km ou plus de 8 km jusqu’au spot"
        else:
            return p, s
    return None, why


def simplify(coords, tol_m=SIMPLIFY_M):
    """Douglas-Peucker en mètres (projection locale) : la trace garde sa forme à quelques mètres près."""
    if len(coords) < 3:
        return list(coords)
    k = 111320.0
    c0 = math.cos(math.radians(coords[0][0]))
    xy = [(p[1] * k * c0, p[0] * k) for p in coords]
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = xy[a], xy[b]
        L = math.hypot(bx - ax, by - ay)
        best, idx = -1.0, None
        for i in range(a + 1, b):
            px, py = xy[i]
            d = math.hypot(px - ax, py - ay) if L == 0 else abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / L
            if d > best:
                best, idx = d, i
        if idx is not None and best > tol_m:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k_ in zip(coords, keep) if k_]


# ---------- fiche ----------
def difficulty(km, dplus):
    return 1 if dplus < 350 and km < 8 else 3 if dplus > 800 or km > 14 else 2


def build_hike(c, o, hz, park, s, place):
    """Une rando au format de data/hikes.json, marquée `auto`."""
    start_alt = round(s["coords"][0][2] or elevation(park["lat"], park["lon"]))
    at = s["coords"][G.nearest_index(s["coords"], c["lat"], c["lon"])[0]]
    spot_alt = round(at[2] if at[2] is not None else elevation(c["lat"], c["lon"]))
    zone = place["name"] if place else ""
    h = {
        "id": f"osm-{c['osm']}", "name": c["name"], "zone": zone, "country": c["country"], "auto": True, "added": False,
        "start": {"name": park.get("name") or (f"Parking, {zone}" if zone else "Parking"), "lat": round(park["lat"], 5),
                  "lon": round(park["lon"], 5), "alt": start_alt, "query": f"{park['lat']:.5f},{park['lon']:.5f}"},
        "km": 0, "dplus": 0, "dur": 0, "toSpot": 0, "down": 0,
        "diff": difficulty(s["km"], s["dplus"]), "sunsetScore": sunset_score(o["share"]),
        "view": view_text(o), "horizon": round(max(0.0, sorted(hz.values())[len(hz) // 2]), 1),
        "snow": spot_alt >= 800,
        "access": (f"Parking à {start_alt} m : vérifie que la route est ouverte et déneigée l’hiver." if start_alt >= 900
                   else "Accès routier non vérifié."),
        "risk": "Itinéraire calculé automatiquement sur OpenStreetMap, jamais parcouru : repère le sentier de jour avant d’y redescendre de nuit.",
        "gear": ["frontale"], "tip": "", "gpx": f"auto/osm-{c['osm']}.gpx",
        "spots": [{"name": c["name"], "lat": round(c["lat"], 5), "lon": round(c["lon"], 5), "alt": spot_alt,
                   "desc": "Tour d’observation" if c["tower"] else "Point de vue (OpenStreetMap)", "sunset": True}],
        "waypoints": [[round(park["lat"], 5), round(park["lon"], 5), start_alt], [round(c["lat"], 5), round(c["lon"], 5), spot_alt],
                      [round(park["lat"], 5), round(park["lon"], 5), start_alt]],
        "confidence": round(o["share"], 2),
    }
    G.refresh_fiche(h, s)
    return h


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="nombre maximal de spots à router (les mieux dégagés d'abord)")
    ap.add_argument("--bbox", help="sud,ouest,nord,est")
    ap.add_argument("--report", help="écrire le bilan (Markdown)")
    a = ap.parse_args()
    bbox = tuple(map(float, a.bbox.split(","))) if a.bbox else BBOX
    curated = json.loads((ROOT / "data" / "hikes.json").read_text("utf-8"))
    taken = [(s["lat"], s["lon"]) for h in curated for s in h["spots"]]

    cands = candidates(bbox)
    print(f"{len(cands)} points de vue et tours nommés")
    cands = [c for c in cands if faces_sunset(c["direction"])]
    cands = [c for c in cands if not any(G.hav(c["lat"], c["lon"], la, lo) < NEAR_CURATED_KM for la, lo in taken)]
    print(f"{len(cands)} tournés vers l’ouest (ou sans direction) et hors des randos vérifiées")

    scored = []
    for i, c in enumerate(cands):
        try:
            hz = horizon(c["lat"], c["lon"], c["height"])
        except Exception as e:  # noqa: BLE001 - une tuile manquante ne bloque pas les autres spots
            print(f"  {c['name']} : relief indisponible ({e})")
            continue
        o = openness(hz)
        if o["share"] >= MIN_OPEN and dominance(c["lat"], c["lon"], c["height"]) >= MIN_DOMINANCE_M:
            scored.append((c, o, hz))
        if i % 100 == 99:
            print(f"  relief : {i + 1}/{len(cands)}, {len(scored)} dégagés")
    scored.sort(key=lambda t: -t[1]["share"])
    kept = []
    for t in scored:
        if not any(G.hav(t[0]["lat"], t[0]["lon"], k[0]["lat"], k[0]["lon"]) < NEAR_SPOT_KM for k in kept):
            kept.append(t)
    print(f"{len(kept)} spots dégagés sur l’arc du coucher (au moins {MIN_OPEN:.0%}), doublons retirés".replace("%", " %"))
    if a.limit:
        kept = kept[:a.limit]

    parks, towns = parkings(bbox), places(bbox)
    hikes, refused = [], {}
    for i, (c, o, hz) in enumerate(kept):
        near = [p for p in parks if abs(p["lat"] - c["lat"]) < 0.05 and abs(p["lon"] - c["lon"]) < 0.07]
        try:
            park, s = pick_parking(c, near)
        except Exception as e:  # noqa: BLE001 - un spot en échec ne bloque pas les autres
            park, s = None, f"erreur ({e.__class__.__name__})"
        if park is None:
            refused[s] = refused.get(s, 0) + 1
            continue
        h = build_hike(c, o, hz, park, s, nearest(towns, park["lat"], park["lon"]))
        hikes.append((h, simplify(s["coords"])))
        print(f"  {len(hikes):3d}. {c['name'][:38]:38s} {h['km']:4.1f} km, D+ {h['dplus']:4d}, vue {o['share']:.0%}, {h['zone']}")
        if i % 25 == 24:
            print(f"  itinéraires : {i + 1}/{len(kept)}")

    out = ROOT / "gpx" / "auto"
    out.mkdir(parents=True, exist_ok=True)
    if not a.limit:
        for f in out.glob("*.gpx"):
            f.unlink()
    for h, coords in hikes:
        (out / f"{h['id']}.gpx").write_text(G.to_gpx(h, coords).replace(
            "à travers les points clés de la fiche", "du parking au point de vue, aller-retour"), "utf-8")
    data = sorted((h for h, _ in hikes), key=lambda h: h["id"])
    (ROOT / "data" / "hikes-auto.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")
    lines = [f"# Randos générées ({len(data)})", "", f"- spots dégagés routés : {len(kept)}",
             *[f"- refusés, {k} : {v}" for k, v in sorted(refused.items(), key=lambda kv: -kv[1])]]
    print("\n".join(lines))
    if a.report:
        Path(a.report).write_text("\n".join(lines) + "\n", "utf-8")


if __name__ == "__main__":
    main()
