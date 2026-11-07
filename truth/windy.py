"""Windy Webcams API v3."""
import datetime as dt

import requests

BASE = "https://api.windy.com/webcams/api/v3/webcams"


def _h(key):
    return {"x-windy-api-key": key, "User-Agent": "abendrot-truth/1.0"}


def _parse(w):
    img = (w.get("images") or {}).get("current") or {}
    upd = w.get("lastUpdatedOn")
    try:
        upd = dt.datetime.fromisoformat(upd.replace("Z", "+00:00")) if upd else None
    except ValueError:
        upd = None
    return {"id": str(w.get("webcamId") or w.get("id")), "title": w.get("title"), "status": w.get("status"),
            "city": (w.get("location") or {}).get("city"), "lat": (w.get("location") or {}).get("latitude"),
            "lon": (w.get("location") or {}).get("longitude"),
            "image": img.get("preview") or img.get("thumbnail") or img.get("icon"), "updated": upd,
            "url": (w.get("urls") or {}).get("detail") or f"https://www.windy.com/webcams/{w.get('webcamId')}"}


def _check(r):
    if not r.ok:  # affiche le message de Windy, plus parlant que le code HTTP seul
        raise RuntimeError(f"Windy {r.status_code} : {r.text[:300]}")


def nearby(key, lat, lon, radius_km=25, limit=20):
    r = requests.get(BASE, headers=_h(key), timeout=30, params={
        "nearby": f"{lat:.4f},{lon:.4f},{int(radius_km)}", "include": "images,location,urls", "limit": limit})
    _check(r)
    return [_parse(w) for w in r.json().get("webcams", [])]


def get(key, webcam_id):
    r = requests.get(f"{BASE}/{webcam_id}", headers=_h(key), timeout=30, params={"include": "images,location,urls"})
    r.raise_for_status()
    j = r.json()
    return _parse(j["webcams"][0] if "webcams" in j else j)


def download(url):
    r = requests.get(url, timeout=30, headers={"User-Agent": "abendrot-truth/1.0"})
    r.raise_for_status()
    return r.content
