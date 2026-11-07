"""Toutes les 10 min en fin de journée : capture les webcams autour du coucher, puis consolide la soirée.

1. Pour chaque spot dont le coucher est dans [-40 min, +30 min] : image fraîche de chaque webcam,
   métriques du ciel, étiquette IA (Gemini ou Claude) (une seule par caméra, l'image la plus proche du coucher).
2. Quand la fenêtre d'un spot est passée : garde le pic de la soirée par caméra → observations.csv.

Usage : WINDY_KEY=... python -m truth.capture   (GEMINI_API_KEY ou ANTHROPIC_API_KEY, HF_TOKEN + HF_DATASET optionnels)
"""
import datetime as dt
import hashlib
import io
from collections import defaultdict

from PIL import Image

from . import vision, windy
from .common import (CAPTURE_FIELDS, DATA, FETCH_FIELDS, OBS_FIELDS, TZ, append_csv, env, load_webcams, read_csv,
                     sunset_spots)
from .metrics import dhash, sky_metrics
from .storage import ImageStore
from .sun import effective_sunset

# Les fenêtres se calent sur le coucher géométrique (sans masque de relief) : la couleur du ciel
# dépend de la hauteur du soleil, pas de la crête qui le cache, et le pic arrive souvent 10 à 20 min après.
WINDOW = (-40, 30)        # minutes autour du coucher pendant lesquelles on capture
PEAK = (-30, 25)          # minutes retenues pour le pic de la soirée
VISION = (-12, 12)        # image étiquetée par Claude
MAX_AGE = dt.timedelta(minutes=20)


VIS = ("vis_sky", "vis_sea", "vis_alps", "vis_conf")


def _check(now, day, cid, w, sset):
    """Relevé de fraîcheur d'une caméra (une ligne de fetches.csv). Statuts : fresh, stale, no_image, no_date."""
    upd = w["updated"]
    status = "no_image" if not w["image"] else "no_date" if not upd else "stale" if now - upd > MAX_AGE else "fresh"
    return {"date": day.isoformat(), "webcam_id": cid, "checked_utc": now.isoformat(timespec="seconds"),
            "image_updated_utc": upd.isoformat(timespec="seconds") if upd else "",
            "age_min": round((now - upd).total_seconds() / 60, 1) if upd else "",
            # coucher du premier spot qui interroge la caméra : les spots qui la partagent sont à 1 ou 2 min près
            "minutes_from_sunset": round((upd - sset).total_seconds() / 60, 1) if upd else "",
            "status": status, "windy_status": w.get("status") or "", "sha1": "", "dhash": "", "error": ""}


def capture(now, spots, cams_by_spot, key, store, captures, checks=None):
    """checks : dict {webcam_id: relevé} rempli au passage, à écrire dans fetches.csv."""
    checks = {} if checks is None else checks
    seen = {(r["spot_id"], r["webcam_id"], r["image_updated_utc"]) for r in captures}
    labels = {(r["date"], r["webcam_id"]): {k: r.get(k) for k in VIS} for r in captures if r.get("vis_sky")}
    fetched = {}   # webcam_id → (infos, jpeg, métriques, chemin image) : une seule requête par caméra et par exécution
    rows = []
    for s in spots:
        day = now.astimezone(TZ).date()
        sset = effective_sunset(day, s["lat"], s["lon"], s["alt"], 0, TZ)
        delta = (now - sset).total_seconds() / 60
        if not WINDOW[0] <= delta <= WINDOW[1]:
            continue
        for cam in cams_by_spot.get(s["spot_id"], []):
            cid = str(cam["id"])
            try:
                if cid not in fetched:
                    checks.setdefault(cid, {"date": day.isoformat(), "webcam_id": cid,
                                            "checked_utc": now.isoformat(timespec="seconds"), "status": "error"})
                    w = windy.get(key, cid)
                    checks[cid] = _check(now, day, cid, w, sset)
                    fresh = checks[cid]["status"] == "fresh"
                    fetched[cid] = [w, None, None, ""] if fresh else None
                    if not fresh:
                        print(f"  caméra {cid} : {checks[cid]['status']} (âge {checks[cid]['age_min'] or '?'} min), ignorée")
                if not fetched[cid]:
                    continue
                w = fetched[cid][0]
                stamp = w["updated"].isoformat(timespec="seconds")
                if (s["spot_id"], cid, stamp) in seen:
                    continue
                if fetched[cid][1] is None:
                    jpeg = windy.download(w["image"])
                    img = Image.open(io.BytesIO(jpeg))
                    fetched[cid][1] = jpeg
                    fetched[cid][2] = sky_metrics(img, cam.get("sky_top", 0.0), cam.get("sky_bottom", 0.42))
                    fetched[cid][3] = store.add(f"images/{day}/{cid}_{w['updated'].strftime('%H%M')}.jpg", jpeg)
                    checks[cid].update(status="fresh", error="", sha1=hashlib.sha1(jpeg).hexdigest()[:12],
                                       dhash=dhash(img, cam.get("sky_bottom", 0.42)))
                jpeg, m, path = fetched[cid][1], fetched[cid][2], fetched[cid][3]
                mfs = round((w["updated"] - sset).total_seconds() / 60, 1)
                lab = labels.get((day.isoformat(), cid), {})
                if not lab and VISION[0] <= mfs <= VISION[1]:
                    lab = vision.classify(jpeg)
                    if lab:
                        labels[(day.isoformat(), cid)] = lab
                seen.add((s["spot_id"], cid, stamp))
                rows.append({"date": day.isoformat(), "spot_id": s["spot_id"], "webcam_id": cid,
                             "captured_utc": now.isoformat(timespec="seconds"), "image_updated_utc": stamp,
                             "minutes_from_sunset": mfs, **m, **lab, "image_path": path})
                print(f"  {s['spot_id']}/{cid} : {mfs:+.0f} min, ciel {m['sky_score_raw']:.0f}"
                      + (f", étiquette : {lab.get('vis_sky')}" if lab else ""))
            except Exception as e:  # noqa: BLE001 - une caméra en panne ne doit pas bloquer les autres
                print(f"  {s['spot_id']}/{cid} : erreur {e}")
                if cid in checks:
                    checks[cid].update(status="error", error=str(e)[:200])
    return rows


def finalize(now, spots, captures, observations):
    """Une ligne par (soir, caméra) : la meilleure image de la fenêtre, avec l'étiquette Claude de la soirée."""
    done = {(r["date"], r["spot_id"], r["webcam_id"]) for r in observations}
    by_key = defaultdict(list)
    for r in captures:
        by_key[(r["date"], r["spot_id"], r["webcam_id"])].append(r)
    spot = {s["spot_id"]: s for s in spots}
    rows = []
    for (date, sid, wid), caps in by_key.items():
        if (date, sid, wid) in done or sid not in spot:
            continue
        s, day = spot[sid], dt.date.fromisoformat(date)
        if now < effective_sunset(day, s["lat"], s["lon"], s["alt"], 0, TZ) + dt.timedelta(minutes=WINDOW[1] + 5):
            continue
        win = [c for c in caps if PEAK[0] <= float(c["minutes_from_sunset"]) <= PEAK[1]]
        if not win:
            continue
        peak = max(win, key=lambda c: float(c["sky_score_raw"]))
        lab = next((c for c in caps if c.get("vis_sky")), None) or next(
            (c for (d, _, w), cs in by_key.items() if d == date and w == wid for c in cs if c.get("vis_sky")), {})
        rows.append({"date": date, "spot_id": sid, "webcam_id": wid, "n_captures": len(win),
                     "peak_minutes_from_sunset": peak["minutes_from_sunset"],
                     **{k: peak[k] for k in ("colorfulness", "warm_ratio", "gray_ratio", "dark_ratio", "sky_score_raw", "image_path")},
                     **{k: lab.get(k, "") for k in ("vis_sky", "vis_sea", "vis_alps", "vis_conf")}})
    return rows


def main(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    key = env("WINDY_KEY")
    if not key:
        raise SystemExit("WINDY_KEY manquante.")
    cams = load_webcams()
    if not cams:
        raise SystemExit("Aucune webcam : lance d'abord python -m truth.discover.")
    spots = [s for s in sunset_spots() if cams.get(s["spot_id"])]
    cap_path, obs_path = DATA / "captures.csv", DATA / "observations.csv"
    captures = read_csv(cap_path)
    store = ImageStore()
    checks = {}
    new = capture(now, spots, cams, key, store, captures, checks)
    append_csv(cap_path, new, CAPTURE_FIELDS)
    append_csv(DATA / "fetches.csv", list(checks.values()), FETCH_FIELDS)
    store.flush(f"captures {now:%Y-%m-%d %H:%M}Z")
    obs = finalize(now, spots, captures + [{k: str(v) for k, v in r.items()} for r in new], read_csv(obs_path))
    append_csv(obs_path, obs, OBS_FIELDS)
    print(f"{len(new)} capture(s), {len(obs)} soirée(s) consolidée(s)")


if __name__ == "__main__":
    main()
