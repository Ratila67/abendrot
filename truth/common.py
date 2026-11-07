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


def load_webcams():
    """truth/webcams.yml → {spot_id: [cam, …]} (caméras activées uniquement)."""
    import yaml
    if not WEBCAMS_FILE.exists():
        return {}
    raw = yaml.safe_load(WEBCAMS_FILE.read_text("utf-8")) or {}
    return {spot: [c for c in cams or [] if c.get("enabled", True)] for spot, cams in raw.items()}
