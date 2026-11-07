"""Trouve les webcams Windy proches de chaque spot coucher et écrit truth/webcams.yml.

Usage : WINDY_KEY=... python -m truth.discover [--radius 25] [--per-spot 4]
Relis ensuite le fichier : mets enabled: false sur une caméra qui ne montre pas le ciel,
et ajuste sky_top / sky_bottom (fractions de la hauteur de l'image) pour cadrer la bande de ciel.
"""
import argparse
from math import asin, cos, radians, sin, sqrt

import yaml

from . import windy
from .common import WEBCAMS_FILE, env, load_webcams, sunset_spots


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--radius", type=int, default=25)
    ap.add_argument("--per-spot", type=int, default=4)
    args = ap.parse_args()
    key = env("WINDY_KEY")
    if not key:
        raise SystemExit("WINDY_KEY manquante.")
    previous = {}
    if WEBCAMS_FILE.exists():
        raw = yaml.safe_load(WEBCAMS_FILE.read_text("utf-8")) or {}
        previous = {(s, str(c["id"])): c for s, cams in raw.items() for c in cams or []}
    out = {}
    for s in sunset_spots():
        cams = [c for c in windy.nearby(key, s["lat"], s["lon"], args.radius) if c["status"] in (None, "active")]
        for c in cams:
            if c["lat"] is None:
                c["dist"] = 99
                continue
            dlat, dlon = radians(c["lat"] - s["lat"]), radians(c["lon"] - s["lon"])
            h = sin(dlat / 2) ** 2 + cos(radians(s["lat"])) * cos(radians(c["lat"])) * sin(dlon / 2) ** 2
            c["dist"] = round(2 * 6371 * asin(sqrt(h)), 1)
        cams.sort(key=lambda c: c["dist"])
        out[s["spot_id"]] = []
        for c in cams[: args.per_spot]:
            old = previous.get((s["spot_id"], c["id"]), {})  # on garde tes réglages manuels
            out[s["spot_id"]].append({
                "id": c["id"], "title": c["title"], "city": c["city"], "distance_km": c["dist"], "url": c["url"],
                "enabled": old.get("enabled", True), "sky_top": old.get("sky_top", 0.0),
                "sky_bottom": old.get("sky_bottom", 0.42)})
        print(f"{s['spot_id']:<20} {len(out[s['spot_id']])} webcam(s)")
    header = ("# Généré par python -m truth.discover. Tes modifications (enabled, sky_top, sky_bottom) sont conservées\n"
              "# à la prochaine découverte. sky_top/sky_bottom : bande de ciel analysée, en fraction de la hauteur.\n")
    WEBCAMS_FILE.write_text(header + yaml.safe_dump(out, allow_unicode=True, sort_keys=False), "utf-8")
    n = sum(len(v) for v in load_webcams().values())
    print(f"→ {WEBCAMS_FILE.relative_to(WEBCAMS_FILE.parents[1])} : {n} caméra(s) actives")


if __name__ == "__main__":
    main()
