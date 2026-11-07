"""Prévisions : mêmes sources et même score heuristique que l'app (js/weather.js)."""
import datetime as dt

import requests

from .common import TZ

VARS = ["temperature_2m", "apparent_temperature", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
        "precipitation", "visibility", "wind_speed_10m", "wind_gusts_10m", "snow_depth", "freezing_level_height",
        "temperature_850hPa"]
PLAIN = (48.08, 7.36, 190)
MF, GLOBAL = "meteofrance_seamless", "best_match"
UA = {"User-Agent": "abendrot-truth/1.0"}


def _merge(o):
    h = o["hourly"]
    out = {"time": h["time"]}
    for v in VARS:
        a, b, c = h.get(f"{v}_{MF}"), h.get(f"{v}_{GLOBAL}"), h.get(v)
        out[v] = [next((s[i] for s in (a, b, c) if s is not None and s[i] is not None), None) for i in range(len(h["time"]))]
    out["model"] = ["meteofrance" if (h.get(f"cloud_cover_low_{MF}") or [None] * (i + 1))[i] is not None else "global"
                    for i in range(len(h["time"]))]
    return out


def forecast(points):
    """points = [(lat, lon, alt), …] → (liste des séries horaires, série plaine)."""
    allp = list(points) + [PLAIN]
    params = {
        "latitude": ",".join(f"{p[0]:.4f}" for p in allp), "longitude": ",".join(f"{p[1]:.4f}" for p in allp),
        "elevation": ",".join(str(round(p[2])) for p in allp), "hourly": ",".join(VARS),
        "timezone": "Europe/Paris", "forecast_days": 3, "models": f"{MF},{GLOBAL}",
    }
    r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, headers=UA, timeout=30)
    if not r.ok:
        params.pop("models")
        r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, headers=UA, timeout=30)
    r.raise_for_status()
    j = r.json()
    j = j if isinstance(j, list) else [j]
    series = [_merge(o) for o in j]
    return series[:-1], series[-1]


def hour_index(hourly, when_utc):
    local = (when_utc + dt.timedelta(minutes=30)).astimezone(TZ)
    key = local.strftime("%Y-%m-%dT%H:00")
    try:
        return hourly["time"].index(key)
    except ValueError:
        return -1


def heuristic(s, plain, i, alt):
    """Copie conforme de scoreSunset() côté app."""
    g = lambda o, k: (o.get(k) or [None] * (i + 1))[i]
    low, mid, high = g(s, "cloud_cover_low") or 0, g(s, "cloud_cover_mid") or 0, g(s, "cloud_cover_high") or 0
    pr, vis = g(s, "precipitation") or 0, g(s, "visibility")
    p_low, p_t, t850 = g(plain, "cloud_cover_low") or 0, g(plain, "temperature_2m"), g(s, "temperature_850hPa")
    inversion = alt >= 850 and p_low >= 60 and t850 is not None and p_t is not None and t850 > p_t - 2
    sc = 80
    if inversion:
        sc += 10
    else:
        sc -= low * 0.75
    color = max(mid, high)
    if 20 <= color <= 70:
        sc += 15
    elif color > 90:
        sc -= 30
    if pr > 0.2:
        sc -= 40
    if vis is not None and vis < 10000:
        sc -= 15
    return round(max(0, min(100, sc))), inversion


def aerosols(lat, lon):
    try:
        r = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", headers=UA, timeout=20, params={
            "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}", "hourly": "aerosol_optical_depth",
            "timezone": "Europe/Paris", "forecast_days": 3})
        r.raise_for_status()
        return r.json()["hourly"]
    except Exception as e:  # noqa: BLE001 - source optionnelle
        print(f"  aérosols indisponibles : {e}")
        return None


def sunsethue(lat, lon, date, key):
    if not key:
        return None
    try:
        r = requests.get("https://api.sunsethue.com/event", headers={"x-api-key": key, **UA}, timeout=20, params={
            "latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}", "date": date, "type": "sunset"})
        r.raise_for_status()
        d = r.json().get("data") or {}
        return None if d.get("model_data") is False or d.get("quality") is None else round(d["quality"] * 100)
    except Exception as e:  # noqa: BLE001
        print(f"  Sunsethue indisponible : {e}")
        return None
