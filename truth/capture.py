"""Toutes les 10 min en fin de journée : capture les webcams autour du coucher, puis consolide la soirée.

1. Pour chaque spot dont le coucher est dans [-40 min, +20 min] : image fraîche de chaque webcam,
   métriques du ciel, étiquette Claude (une seule par caméra, l'image la plus proche du coucher).
2. Quand la fenêtre d'un spot est passée : garde le pic de la soirée par caméra → observations.csv.

Usage : WINDY_KEY=... python -m truth.capture   (ANTHROPIC_API_KEY, HF_TOKEN + HF_DATASET optionnels)
"""
import datetime as dt
import io
from collections import defaultdict

from PIL import Image

from . import vision, windy
from .common import (CAPTURE_FIELDS, DATA, OBS_FIELDS, TZ, append_csv, env, load_webcams, read_csv, sunset_spots)
from .metrics import sky_metrics
from .storage import ImageStore
from .sun import effective_sunset

WINDOW = (-40, 20)        # minutes autour du coucher pendant lesquelles on capture
PEAK = (-30, 15)          # minutes retenues pour le pic de la soirée
VISION = (-12, 12)        # image étiquetée par Claude
MAX_AGE = dt.timedelta(minutes=20)


def capture(now, spots, cams_by_spot, key, store, captures):
    seen = {(r["webcam_id"], r["image_updated_utc"]) for r in captures}
    labelled = {(r["date"], r["webcam_id"]) for r in captures if r.get("vis_sky")}
    ant = env("ANTHROPIC_API_KEY")
    rows = []
    for s in spots:
        day = now.astimezone(TZ).date()
        sset = effective_sunset(day, s["lat"], s["lon"], s["alt"], s["horizon"], TZ)
        delta = (now - sset).total_seconds() / 60
        if not WINDOW[0] <= delta <= WINDOW[1]:
            continue
        for cam in cams_by_spot.get(s["spot_id"], []):
            try:
                w = windy.get(key, cam["id"])
                if not w["image"] or not w["updated"] or now - w["updated"] > MAX_AGE:
                    print(f"  {s['spot_id']}/{cam['id']} : image trop ancienne, ignorée")
                    continue
                stamp = w["updated"].isoformat(timespec="seconds")
                if (str(cam["id"]), stamp) in seen:
                    continue
                jpeg = windy.download(w["image"])
                m = sky_metrics(Image.open(io.BytesIO(jpeg)), cam.get("sky_top", 0.0), cam.get("sky_bottom", 0.42))
                mfs = round((w["updated"] - sset).total_seconds() / 60, 1)
                lab = {}
                if VISION[0] <= mfs <= VISION[1] and (day.isoformat(), str(cam["id"])) not in labelled:
                    lab = vision.classify(jpeg, ant)
                    if lab:
                        labelled.add((day.isoformat(), str(cam["id"])))
                path = store.add(f"images/{day}/{s['spot_id']}/{cam['id']}_{w['updated'].strftime('%H%M')}.jpg", jpeg)
                rows.append({"date": day.isoformat(), "spot_id": s["spot_id"], "webcam_id": cam["id"],
                             "captured_utc": now.isoformat(timespec="seconds"), "image_updated_utc": stamp,
                             "minutes_from_sunset": mfs, **m, **lab, "image_path": path})
                print(f"  {s['spot_id']}/{cam['id']} : {mfs:+.0f} min, ciel {m['sky_score_raw']:.0f}"
                      + (f", Claude : {lab.get('vis_sky')}" if lab else ""))
            except Exception as e:  # noqa: BLE001 - une caméra en panne ne doit pas bloquer les autres
                print(f"  {s['spot_id']}/{cam['id']} : erreur {e}")
    return rows


def finalize(now, spots, captures, observations):
    """Une ligne par (soir, caméra) : la meilleure image de la fenêtre, avec l'étiquette Claude de la soirée."""
    done = {(r["date"], r["webcam_id"]) for r in observations}
    by_key = defaultdict(list)
    for r in captures:
        by_key[(r["date"], r["spot_id"], r["webcam_id"])].append(r)
    spot = {s["spot_id"]: s for s in spots}
    rows = []
    for (date, sid, wid), caps in by_key.items():
        if (date, wid) in done or sid not in spot:
            continue
        s, day = spot[sid], dt.date.fromisoformat(date)
        if now < effective_sunset(day, s["lat"], s["lon"], s["alt"], s["horizon"], TZ) + dt.timedelta(minutes=WINDOW[1] + 5):
            continue
        win = [c for c in caps if PEAK[0] <= float(c["minutes_from_sunset"]) <= PEAK[1]]
        if not win:
            continue
        peak = max(win, key=lambda c: float(c["sky_score_raw"]))
        lab = next((c for c in caps if c.get("vis_sky")), {})
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
    new = capture(now, spots, cams, key, store, captures)
    append_csv(cap_path, new, CAPTURE_FIELDS)
    store.flush(f"captures {now:%Y-%m-%d %H:%M}Z")
    obs = finalize(now, spots, captures + [{k: str(v) for k, v in r.items()} for r in new], read_csv(obs_path))
    append_csv(obs_path, obs, OBS_FIELDS)
    print(f"{len(new)} capture(s), {len(obs)} soirée(s) consolidée(s)")


if __name__ == "__main__":
    main()
