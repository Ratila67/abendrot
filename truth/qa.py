"""Contrôle qualité des caméras actives, d'après data/truth/fetches.csv.

Pour chaque caméra : part des relevés où l'image était fraîche, âge des images périmées,
image figée (même contenu sous plusieurs horodatages) et soupçon de pivot (le décor change).

Usage : python -m truth.qa [--days 14] [--out data/truth/qa.md]
"""
import argparse
import datetime as dt
import statistics
from collections import defaultdict
from pathlib import Path

from .common import DATA, TZ, read_csv, read_webcams_raw
from .metrics import hamming

FRESH_MIN = 0.5      # en dessous : « souvent périmée »
FROZEN_MIN = 3       # même image sous au moins 3 horodatages différents : « image figée »
PIVOT_BITS = 12      # écart (sur 64 bits) au-delà duquel le décor ne ressemble plus à la référence
PIVOT_SHARE = 0.2    # part d'images « autre décor » au-delà de laquelle on soupçonne un pivot
PIVOT_MIN_IMAGES = 5
PIVOT_MAX_MFS = 10   # au-delà de +10 min après le coucher, l'image est trop sombre pour comparer le décor


def medoid(hashes):
    """L'empreinte la plus proche de toutes les autres : le cadrage « habituel » de la caméra."""
    return min(set(hashes), key=lambda h: sum(hamming(h, x) for x in hashes))


def audit(fetches, cams, since=None):
    by_cam = defaultdict(list)
    for f in fetches:
        if not since or f["date"] >= since:
            by_cam[f["webcam_id"]].append(f)
    out = []
    for cid, cam in sorted(cams.items()):
        if not cam.get("enabled"):
            continue
        rows = by_cam.get(cid, [])
        fresh = [r for r in rows if r["status"] == "fresh"]
        stale_ages = [float(r["age_min"]) for r in rows if r["status"] == "stale" and r.get("age_min")]
        errors = sum(r["status"] == "error" for r in rows)
        stamps = defaultdict(set)
        for r in fresh:
            if r.get("sha1"):
                stamps[r["sha1"]].add(r["image_updated_utc"])
        frozen = max((len(v) for v in stamps.values()), default=0)
        images = {r["image_updated_utc"]: r["dhash"] for r in fresh   # une empreinte par image, pas par relevé
                  if r.get("dhash") and r.get("minutes_from_sunset") and float(r["minutes_from_sunset"]) <= PIVOT_MAX_MFS}
        hashes = list(images.values())
        pivot = None
        if len(hashes) >= PIVOT_MIN_IMAGES:
            ref = medoid(hashes)
            pivot = sum(hamming(ref, h) > PIVOT_BITS for h in hashes) / len(hashes)
        n = len(rows)
        flags = []
        if not n:
            flags.append("jamais relevée")
        elif not fresh:
            flags.append("jamais fraîche")
        elif len(fresh) / n < FRESH_MIN:
            flags.append("souvent périmée")
        if n and errors / n >= 0.5:
            flags.append("erreurs")
        if frozen >= FROZEN_MIN:
            flags.append("image figée")
        if pivot is not None and pivot >= PIVOT_SHARE:
            flags.append("pivote ?")
        out.append({"id": cid, "title": cam.get("title") or "", "evenings": len({r["date"] for r in rows}),
                    "checks": n, "fresh_share": len(fresh) / n if n else None,
                    "stale_age_median": statistics.median(stale_ages) if stale_ages else None,
                    "errors": errors, "frozen": frozen, "pivot_share": pivot, "images": len(hashes), "flags": flags})
    return sorted(out, key=lambda c: (not c["flags"], c["id"]))


def _pct(x):
    return "" if x is None else f"{100 * x:.0f} %"


def report(rows, since=None):
    flagged = [r for r in rows if r["flags"]]
    lines = [f"# Qualité des caméras ({dt.datetime.now(TZ):%Y-%m-%d})", "",
             f"{len(rows)} caméras actives{f' depuis le {since}' if since else ''}, {len(flagged)} à vérifier.", "",
             "| Caméra | Titre | Soirs | Relevés | Fraîches | Âge médian périmées | Erreurs | Figée | Pivot | Alertes |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        age = "" if r["stale_age_median"] is None else f"{r['stale_age_median']:.0f} min"
        piv = "" if r["pivot_share"] is None else f"{_pct(r['pivot_share'])} de {r['images']}"
        lines.append(f"| {r['id']} | {r['title'][:40]} | {r['evenings']} | {r['checks']} | {_pct(r['fresh_share'])} | "
                     f"{age} | {r['errors'] or ''} | {r['frozen'] if r['frozen'] >= FROZEN_MIN else ''} | {piv} | "
                     f"{', '.join(r['flags'])} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=0, help="ne garder que les N derniers jours (0 = tout)")
    ap.add_argument("--out", help="écrire aussi le rapport dans ce fichier")
    a = ap.parse_args()
    since = (dt.datetime.now(TZ).date() - dt.timedelta(days=a.days)).isoformat() if a.days else None
    fetches = read_csv(DATA / "fetches.csv")
    if not fetches:
        raise SystemExit("data/truth/fetches.csv est vide : attends au moins un soir de capture.")
    cams, _ = read_webcams_raw()
    md = report(audit(fetches, cams, since), since)
    print(md)
    if a.out:
        Path(a.out).write_text(md, "utf-8")


if __name__ == "__main__":
    main()
