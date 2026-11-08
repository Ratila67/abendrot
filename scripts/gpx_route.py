"""Traces GPX calculées sur les sentiers OpenStreetMap, à travers les points clés de chaque rando.

Spot d'abord : une rando mène du parking au spot du coucher et en revient, par défaut en aller-retour
(on redescend de nuit, par un chemin déjà parcouru). Un ou deux points de passage seulement là où il faut
éviter la route ; une boucle seulement là où elle en vaut la peine.

Pour chaque rando de data/hikes.json : itinéraire BRouter (profil hiking-mountain) qui passe par ses
`waypoints`, écrit dans gpx/<id>.gpx, puis contrôlé (spot atteint, assez long pour une rando, part de
route, écart des points clés). Sans --apply, hikes.json n'est pas modifié : l'app n'utilise une trace
qu'une fois validée et renseignée dans le champ `gpx` de la rando.

Revue : scripts/revue-gpx.html (proposer l'aller-retour, corriger les points, valider), qui exporte
corrections-gpx.json. --apply reporte cet export dans hikes.json (points corrigés, `gpx` des traces
validées), régénère ces traces, et la fiche reprend leurs chiffres (distance, D+, temps).

Usage : python scripts/gpx_route.py [--only id1,id2] [--report revue.md] [--apply corrections-gpx.json]
Données : © contributeurs OpenStreetMap (ODbL), itinéraire calculé par BRouter (brouter.de).
"""
import argparse
import json
import math
import os
import time
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parents[1]
BROUTER = os.environ.get("BROUTER_URL", "https://brouter.de/brouter")   # serveur local : voir gpx/README.md
PROFILE = "hiking-mountain"
ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service"}
MAX_SNAP_M = 150       # distance maximale entre un point clé (ou le spot) et la trace
MAX_ROAD = 0.20        # part maximale de goudron
MIN_TO_SPOT_KM = 1.0   # en deçà, ce n'est pas une rando
MAX_LONGER = 1.5       # au-delà de 1,5 fois la fiche, le parking est sans doute mal choisi
FACTOR = 1.25          # allure « hiver » des temps de la fiche (app.js : metrics divise par 1,25)


def hav(a, b, c, d):
    p = math.radians
    x = math.sin(p(c - a) / 2) ** 2 + math.cos(p(a)) * math.cos(p(c)) * math.sin(p(d - b) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def route(waypoints, profile=PROFILE):
    """Itinéraire BRouter passant par les points clés (lat, lon, …) : une feature GeoJSON."""
    import requests
    r = requests.get(BROUTER, timeout=90, headers={"User-Agent": "abendrot-gpx/1.0"}, params={
        "lonlats": "|".join(f"{w[1]:.6f},{w[0]:.6f}" for w in waypoints), "profile": profile,
        "alternativeidx": 0, "format": "geojson"})
    if not r.ok:
        raise RuntimeError(f"BRouter {r.status_code} : {r.text[:200]}")
    return r.json()["features"][0]


def summary(feature):
    """Distance, D+ et part de route d'une feature BRouter. Coordonnées rendues en (lat, lon, ele)."""
    p = feature["properties"]
    coords = [(c[1], c[0], c[2] if len(c) > 2 else None) for c in feature["geometry"]["coordinates"]]
    msgs = p.get("messages") or []
    road = total = 0.0
    if msgs:
        head = msgs[0]
        i_dist, i_tags = head.index("Distance"), head.index("WayTags")
        for m in msgs[1:]:
            d = float(m[i_dist])
            tags = dict(t.split("=", 1) for t in m[i_tags].split() if "=" in t)
            total += d
            road += d if tags.get("highway") in ROADS else 0
    return {"km": float(p["track-length"]) / 1000, "dplus": float(p["filtered ascend"]),
            "road_share": road / total if total else 0.0, "coords": coords}


def target_spot(hike):
    """Le spot du coucher, sinon le dernier spot de la fiche (lac, cascade…)."""
    spots = hike.get("spots") or []
    return next((s for s in spots if s.get("sunset")), spots[-1] if spots else None)


def spot_first(hike):
    """Points clés de l'aller-retour parking → spot → parking."""
    st, sp = hike["start"], target_spot(hike)
    a = [st["lat"], st["lon"], st.get("alt")]
    return [a, [sp["lat"], sp["lon"], sp.get("alt")], a]


def din(km, up, down, factor=1.0):
    """Temps de marche DIN 33466 (min), comme din() de js/geo.js."""
    h, v = km / 4, up / 300 + down / 500
    return (max(h, v) + min(h, v) / 2) * 60 * factor


def build_track(coords, threshold=4):
    """Cumuls distance / D+ / D- comme buildTrack() de js/geo.js (lissage sur 5 points, hystérésis)."""
    n = len(coords)
    cum = [0.0] * n
    for i in range(1, n):
        cum[i] = cum[i - 1] + hav(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1])
    raw = [c[2] for c in coords]
    last = next((v for v in raw if v is not None), 0)
    for i in range(n):
        if raw[i] is None:
            raw[i] = last
        else:
            last = raw[i]
    ele = [sum(raw[max(0, i - 2):i + 3]) / len(raw[max(0, i - 2):i + 3]) for i in range(n)]
    up = down = 0.0
    ref, cu, cd = ele[0], [0.0] * n, [0.0] * n
    for i in range(1, n):
        d = ele[i] - ref
        if d >= threshold:
            up += d
            ref = ele[i]
        elif d <= -threshold:
            down -= d
            ref = ele[i]
        cu[i], cd[i] = up, down
    return {"cum": cum, "up": cu, "down": cd, "km": cum[-1], "dplus": up, "dminus": down}


def nearest_index(coords, lat, lon, radius_km=0.15):
    """Comme nearestIndex(…, preferLast) de js/geo.js : le plus proche du dernier vrai passage à moins de
    radius_km ; un passage qui frôle seulement le rayon (plus de 50 m de plus que le meilleur) est ignoré."""
    best, runs, inside = (0, math.inf), [], False
    for i, c in enumerate(coords):
        d = hav(lat, lon, c[0], c[1])
        if d < best[1]:
            best = (i, d)
        if d < radius_km:
            if not inside:
                runs.append((i, d))
            elif d < runs[-1][1]:
                runs[-1] = (i, d)
            inside = True
        else:
            inside = False
    return next((r for r in reversed(runs) if r[1] <= best[1] + 0.05), best)


def track_metrics(coords, spot):
    """Ce que l'app affichera avec cette trace : distance, D+, temps total, aller jusqu'au spot et retour."""
    T = build_track(coords)
    out = {"km": T["km"], "dplus": T["dplus"], "total": din(T["km"], T["dplus"], T["dminus"], FACTOR)}
    if spot:
        i, d = nearest_index(coords, spot["lat"], spot["lon"])
        j = len(coords) - 1

        def seg(a, b):
            return T["cum"][b] - T["cum"][a], T["up"][b] - T["up"][a], T["down"][b] - T["down"][a]
        out.update(spot_m=d * 1000, to_spot_km=seg(0, i)[0], to_spot=din(*seg(0, i), FACTOR), down=din(*seg(i, j), FACTOR))
    return out


def snap_m(coords, waypoints):
    """Écart maximal (m) entre un point clé et le point de trace le plus proche."""
    return max(min(hav(w[0], w[1], c[0], c[1]) for c in coords) for w in waypoints) * 1000


def flags(s, hike):
    out = []
    if s["km"] > MAX_LONGER * hike["km"]:
        out.append(f"{s['km']:.1f} km contre {hike['km']} km sur la fiche : vérifier le parking")
    if "spot_m" not in s:
        out.append("aucun spot sur la fiche")
    else:
        if s["spot_m"] > MAX_SNAP_M:
            out.append(f"spot à {s['spot_m']:.0f} m de la trace")
        if s["to_spot_km"] < MIN_TO_SPOT_KM:
            out.append(f"{s['to_spot_km']:.1f} km jusqu’au spot : trop court pour une rando")
    if s["snap_m"] > MAX_SNAP_M:
        out.append(f"point clé à {s['snap_m']:.0f} m de la trace")
    if s["road_share"] > MAX_ROAD:
        out.append(f"{s['road_share']:.0%} sur route")
    return out


def to_gpx(hike, coords):
    pts = "\n".join(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}">' + (f"<ele>{ele:.1f}</ele>" if ele is not None else "") + "</trkpt>"
                    for lat, lon, ele in coords)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="abendrot" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata>
    <name>{escape(hike["name"])}</name>
    <desc>Itinéraire calculé sur les sentiers OpenStreetMap à travers les points clés de la fiche.</desc>
    <copyright author="Contributeurs OpenStreetMap"><license>https://opendatacommons.org/licenses/odbl/</license></copyright>
    <link href={quoteattr("https://www.openstreetmap.org/copyright")}><text>© OpenStreetMap</text></link>
  </metadata>
  <trk>
    <name>{escape(hike["name"])}</name>
    <trkseg>
{pts}
    </trkseg>
  </trk>
</gpx>
"""


def report(rows):
    lines = ["| Rando | Trace | Fiche | D+ trace | D+ fiche | Jusqu’au spot | Route | Point clé max | À vérifier |",
             "|---|---|---|---|---|---|---|---|---|"]
    for h, s in rows:
        to = f"{s['to_spot_km']:.1f} km, {s['to_spot']:.0f} min" if "to_spot" in s else "?"
        lines.append(f"| {h['id']} | {s['km']:.1f} km | {h['km']} km | {s['dplus']:.0f} m | {h['dplus']} m | {to} | {s['road_share']:.0%} | "
                     f"{s['snap_m']:.0f} m | {', '.join(s['flags']) or 'non'} |")
    ok = sum(not s["flags"] for _, s in rows)
    return f"{ok} trace(s) sans écart sur {len(rows)}.\n\n" + "\n".join(lines) + "\n"


def apply_corrections(hikes, corr):
    """Reporte les points clés corrigés. Renvoie les ids à recalculer (corrigés ou validés)."""
    by_id = {h["id"]: h for h in hikes}
    unknown = (set(corr.get("waypoints", {})) | set(corr.get("validate", []))) - set(by_id)
    if unknown:
        raise SystemExit(f"Randos inconnues dans les corrections : {', '.join(sorted(unknown))}")
    for hid, pts in corr.get("waypoints", {}).items():
        if len(pts) < 2:
            raise SystemExit(f"{hid} : il faut au moins deux points clés")
        by_id[hid]["waypoints"] = [[round(p[0], 5), round(p[1], 5), None if len(p) < 3 or p[2] is None else round(p[2])] for p in pts]
    return set(corr.get("waypoints", {})) | set(corr.get("validate", []))


def refresh_fiche(hike, s):
    """La fiche reprend les chiffres de la trace validée (temps à l'allure de la fiche, arrondis à 5 min)."""
    def r5(m):
        return int(5 * round(m / 5))
    hike.update(km=round(s["km"], 1), dplus=int(10 * round(s["dplus"] / 10)), dur=r5(s["total"]))
    if "to_spot" in s:
        hike.update(toSpot=r5(s["to_spot"]), down=r5(s["down"]))


def write_hikes(hikes):
    """Même mise en forme que le fichier d'origine : le diff ne montre que ce qui change."""
    (ROOT / "data" / "hikes.json").write_text(json.dumps(hikes, ensure_ascii=False, indent=1), "utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="ids séparés par des virgules")
    ap.add_argument("--report", help="écrire le tableau de revue (Markdown)")
    ap.add_argument("--apply", help="export de scripts/revue-gpx.html à reporter dans hikes.json")
    a = ap.parse_args()
    all_hikes = json.loads((ROOT / "data" / "hikes.json").read_text("utf-8"))
    corr = json.loads(Path(a.apply).read_text("utf-8")) if a.apply else None
    keep = apply_corrections(all_hikes, corr) if corr else set(a.only.split(",")) if a.only else None
    hikes = [h for h in all_hikes if keep is None or h["id"] in keep]
    rows = []
    for i, h in enumerate(hikes):
        if i:
            time.sleep(1.5)   # serveur public : une requête à la fois, sans insister
        try:
            s = summary(route(h["waypoints"]))
            s.update(track_metrics(s["coords"], target_spot(h)))
        except Exception as e:  # noqa: BLE001 - une rando en échec ne bloque pas les autres
            print(f"{h['id']} : échec ({e})")
            continue
        s["snap_m"] = snap_m(s["coords"], h["waypoints"])
        s["flags"] = flags(s, h)
        (ROOT / "gpx" / f"{h['id']}.gpx").write_text(to_gpx(h, s["coords"]), "utf-8")
        rows.append((h, s))
        print(f"{h['id']:22s} {s['km']:5.1f} km ({h['km']}), D+ {s['dplus']:4.0f} ({h['dplus']}), spot {s.get('to_spot_km', 0):.1f} km, route {s['road_share']:.0%}, "
              f"point clé {s['snap_m']:.0f} m  {'; '.join(s['flags']) or 'ok'}")
    md = report(rows)
    print("\n" + md)
    if a.report:
        Path(a.report).write_text(md, "utf-8")
    if corr:
        done = {h["id"]: s for h, s in rows}
        for h in all_hikes:   # seule une trace validée ET régénérée est branchée dans l'app
            if h["id"] in set(corr.get("validate", [])) & set(done):
                h["gpx"] = f"{h['id']}.gpx"
                refresh_fiche(h, done[h["id"]])
        write_hikes(all_hikes)
        missed = set(corr.get("validate", [])) - set(done)
        print(f"hikes.json mis à jour : {len(corr.get('waypoints', {}))} rando(s) corrigée(s), "
              f"{len(set(corr.get('validate', [])) & set(done))} trace(s) branchée(s), fiche(s) mise(s) à jour."
              + (f" Échec de calcul, non branchées : {', '.join(sorted(missed))}." if missed else ""))


if __name__ == "__main__":
    main()
