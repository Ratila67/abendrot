"""Traces GPX calculées sur les sentiers OpenStreetMap, à travers les points clés de chaque rando.

Pour chaque rando de data/hikes.json : itinéraire BRouter (profil hiking-mountain) qui passe par ses
`waypoints`, écrit dans gpx/<id>.gpx, puis comparé à la fiche (distance, D+, part de route, écart des
points clés). Sans --apply, hikes.json n'est pas modifié : l'app n'utilise une trace qu'une fois validée
et renseignée dans le champ `gpx` de la rando.

Revue : scripts/revue-gpx.html (corriger les points clés, valider), qui exporte corrections-gpx.json.
--apply reporte cet export dans hikes.json (points corrigés, `gpx` des traces validées) et régénère ces traces.

Usage : python scripts/gpx_route.py [--only id1,id2] [--report revue.md] [--apply corrections-gpx.json]
Données : © contributeurs OpenStreetMap (ODbL), itinéraire calculé par BRouter (brouter.de).
"""
import argparse
import json
import math
import time
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parents[1]
BROUTER = "https://brouter.de/brouter"
PROFILE = "hiking-mountain"
ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service"}
MAX_KM_GAP = 0.20      # écart de distance toléré avec la fiche
MAX_DPLUS_GAP = 0.25   # écart de D+ toléré
MAX_SNAP_M = 150       # distance maximale entre un point clé et la trace
MAX_ROAD = 0.20        # part maximale de goudron


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


def snap_m(coords, waypoints):
    """Écart maximal (m) entre un point clé et le point de trace le plus proche."""
    return max(min(hav(w[0], w[1], c[0], c[1]) for c in coords) for w in waypoints) * 1000


def flags(s, hike):
    out = []
    if abs(s["km"] - hike["km"]) > MAX_KM_GAP * hike["km"]:
        out.append(f"distance {s['km']:.1f} km contre {hike['km']} km")
    if abs(s["dplus"] - hike["dplus"]) > MAX_DPLUS_GAP * hike["dplus"]:
        out.append(f"D+ {s['dplus']:.0f} m contre {hike['dplus']} m")
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
    lines = ["| Rando | Trace | Fiche | D+ trace | D+ fiche | Route | Point clé max | À vérifier |", "|---|---|---|---|---|---|---|---|"]
    for h, s in rows:
        lines.append(f"| {h['id']} | {s['km']:.1f} km | {h['km']} km | {s['dplus']:.0f} m | {h['dplus']} m | {s['road_share']:.0%} | "
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
        except Exception as e:  # noqa: BLE001 - une rando en échec ne bloque pas les autres
            print(f"{h['id']} : échec ({e})")
            continue
        s["snap_m"] = snap_m(s["coords"], h["waypoints"])
        s["flags"] = flags(s, h)
        (ROOT / "gpx" / f"{h['id']}.gpx").write_text(to_gpx(h, s["coords"]), "utf-8")
        rows.append((h, s))
        print(f"{h['id']:22s} {s['km']:5.1f} km ({h['km']}), D+ {s['dplus']:4.0f} ({h['dplus']}), route {s['road_share']:.0%}, "
              f"point clé {s['snap_m']:.0f} m  {'; '.join(s['flags']) or 'ok'}")
    md = report(rows)
    print("\n" + md)
    if a.report:
        Path(a.report).write_text(md, "utf-8")
    if corr:
        done = {h["id"] for h, _ in rows}
        for h in all_hikes:   # seule une trace validée ET régénérée est branchée dans l'app
            if h["id"] in set(corr.get("validate", [])) & done:
                h["gpx"] = f"{h['id']}.gpx"
        write_hikes(all_hikes)
        missed = set(corr.get("validate", [])) - done
        print(f"hikes.json mis à jour : {len(corr.get('waypoints', {}))} rando(s) corrigée(s), "
              f"{len(set(corr.get('validate', [])) & done)} trace(s) branchée(s)."
              + (f" Échec de calcul, non branchées : {', '.join(sorted(missed))}." if missed else ""))


if __name__ == "__main__":
    main()
