"""Outils partagés du pipeline « vérité terrain »."""
import csv
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "truth"
TZ = ZoneInfo("Europe/Paris")
WEBCAMS_FILE = ROOT / "truth" / "webcams.yml"

CAPTURE_FIELDS = ["date", "spot_id", "webcam_id", "captured_utc", "image_updated_utc", "minutes_from_sunset",
                  "colorfulness", "warm_ratio", "sat_mean", "bright_mean", "gray_ratio", "dark_ratio", "sky_score_raw",
                  "vis_sky", "vis_sea", "vis_alps", "vis_conf", "image_path"]
OBS_FIELDS = ["date", "spot_id", "webcam_id", "n_captures", "peak_minutes_from_sunset", "colorfulness", "warm_ratio",
              "gray_ratio", "dark_ratio", "sky_score_raw", "vis_sky", "vis_sea", "vis_alps", "vis_conf", "image_path"]
# Un relevé par caméra et par exécution, fraîche ou non : c'est le seul endroit où une image périmée laisse une trace.
FETCH_FIELDS = ["date", "webcam_id", "checked_utc", "image_updated_utc", "age_min", "minutes_from_sunset", "status",
                "windy_status", "sha1", "dhash", "error"]
FORECAST_FIELDS = ["snapshot_date", "date", "spot_id", "lead_day", "sunset_utc", "model", "cloud_low", "cloud_mid",
                   "cloud_high", "precip", "visibility_km", "gust", "t850", "plain_t2m", "plain_low", "t850_minus_plain",
                   "inversion", "heuristic", "aod", "sunsethue", "alt"]


def env(name, default=""):
    return os.environ.get(name, default).strip()


def load_hikes():
    return json.loads((ROOT / "data" / "hikes.json").read_text("utf-8"))


def sunset_spots():
    """Un point par rando « coucher de soleil » : le spot marqué sunset dans hikes.json."""
    out = []
    for h in load_hikes():
        s = next((x for x in h["spots"] if x.get("sunset")), None)
        if s and h.get("sunsetScore", 0) >= 2:
            out.append({"spot_id": h["id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"],
                        "alt": s["alt"], "horizon": h.get("horizon", 0) or 0})
    return out


def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_csv(path, rows, fields):
    if not rows:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def read_webcams_raw():
    """Lit truth/webcams.yml et renvoie (caméras, rattachements) quel que soit le format.

    Format actuel :
      cameras: {id: {title, city, url, enabled, sky_top, sky_bottom}}
      spots:   {spot_id: [{id, distance_km, use}]}
    L'ancien format (une liste de caméras par spot) est converti à la volée.
    """
    import yaml
    if not WEBCAMS_FILE.exists():
        return {}, {}
    raw = yaml.safe_load(WEBCAMS_FILE.read_text("utf-8")) or {}
    if "cameras" in raw or "spots" in raw:
        cams = {str(k): v for k, v in (raw.get("cameras") or {}).items()}
        links = {sp: [{**l, "id": str(l["id"])} for l in (lst or [])] for sp, lst in (raw.get("spots") or {}).items()}
        return cams, links
    cams, links = {}, {}
    for sp, lst in raw.items():
        links[sp] = []
        for c in lst or []:
            cid = str(c["id"])
            links[sp].append({"id": cid, "distance_km": c.get("distance_km"), "use": bool(c.get("enabled", True))})
            cur = cams.setdefault(cid, {k: c.get(k) for k in ("title", "city", "url")} | {"enabled": False, "sky_top": 0.0, "sky_bottom": None})
            if c.get("enabled", True):
                cur["enabled"] = True
                b = c.get("sky_bottom", 0.42)
                cur["sky_bottom"] = b if cur["sky_bottom"] is None else min(cur["sky_bottom"], b)
                cur["sky_top"] = max(cur["sky_top"], c.get("sky_top", 0.0) or 0.0)
    for c in cams.values():
        c["sky_bottom"] = 0.42 if c["sky_bottom"] is None else c["sky_bottom"]
    return cams, links


def load_webcams():
    """{spot_id: [caméra effective, …]} : caméra activée ET rattachée au spot (use: true)."""
    cams, links = read_webcams_raw()
    out = {}
    for sp, lst in links.items():
        out[sp] = [{**cams[l["id"]], "id": l["id"], "distance_km": l.get("distance_km")}
                   for l in lst if l.get("use", True) and cams.get(l["id"], {}).get("enabled", True) and l["id"] in cams]
    return out


def write_webcams(cams, links):
    import yaml
    header = ("# Caméras : réglées une seule fois, même si plusieurs spots les utilisent.\n"
              "#   enabled    : false si l'image ne montre pas le ciel (désactive la caméra partout)\n"
              "#   sky_top / sky_bottom : bande de ciel analysée, en fraction de la hauteur de l'image\n"
              "# Spots : quelles caméras représentent ce spot. use: false pour l'exclure de CE spot seulement.\n")
    body = {"cameras": dict(sorted(cams.items())), "spots": links}
    WEBCAMS_FILE.write_text(header + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=200), "utf-8")
