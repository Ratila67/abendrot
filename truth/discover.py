"""Trouve les webcams Windy proches de chaque spot coucher et écrit truth/webcams.yml.

Usage : WINDY_KEY=... python -m truth.discover [--radius 25] [--per-spot 4]
Relis ensuite le fichier : mets enabled: false sur une caméra qui ne montre pas le ciel,
et ajuste sky_top / sky_bottom (fractions de la hauteur de l'image) pour cadrer la bande de ciel.
"""
import argparse
from math import asin, cos, radians, sin, sqrt

from . import windy
from .common import env, load_webcams, read_webcams_raw, sunset_spots, write_webcams


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=int, default=25)
    ap.add_argument("--per-spot", type=int, default=4)
    args = ap.parse_args()
    key = env("WINDY_KEY")
    if not key:
        raise SystemExit("WINDY_KEY manquante.")
    cams, old_links = read_webcams_raw()          # tes réglages actuels, conservés
    old_use = {(sp, l["id"]): l.get("use", True) for sp, lst in old_links.items() for l in lst}
    links = {}
    for s in sunset_spots():
        found = [c for c in windy.nearby(key, s["lat"], s["lon"], args.radius) if c["status"] in (None, "active")]
        for c in found:
            if c["lat"] is None:
                c["dist"] = 99
                continue
            dlat, dlon = radians(c["lat"] - s["lat"]), radians(c["lon"] - s["lon"])
            h = sin(dlat / 2) ** 2 + cos(radians(s["lat"])) * cos(radians(c["lat"])) * sin(dlon / 2) ** 2
            c["dist"] = round(2 * 6371 * asin(sqrt(h)), 1)
        found.sort(key=lambda c: c["dist"])
        links[s["spot_id"]] = []
        for c in found[: args.per_spot]:
            cams.setdefault(c["id"], {"title": c["title"], "city": c["city"], "url": c["url"],
                                      "enabled": True, "sky_top": 0.0, "sky_bottom": 0.42})
            links[s["spot_id"]].append({"id": c["id"], "distance_km": c["dist"], "use": old_use.get((s["spot_id"], c["id"]), True)})
        print(f"{s['spot_id']:<20} {len(links[s['spot_id']])} webcam(s)")
    write_webcams(cams, links)
    n = len({c["id"] for v in load_webcams().values() for c in v})
    print(f"→ truth/webcams.yml : {n} caméra(s) uniques actives")


if __name__ == "__main__":
    main()
