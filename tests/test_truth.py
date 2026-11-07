"""Tests hors-ligne du pipeline (API simulées). Lancer : python -m pytest tests -q"""
import datetime as dt
import io
import json

import numpy as np
import pytest
from PIL import Image

from truth import capture, common, metrics, snapshot, sun, weather, windy


def sky(color_top, color_bottom, size=(400, 225)):
    """Image synthétique : dégradé vertical."""
    w, h = size
    t = np.linspace(0, 1, h)[:, None, None]
    a = (np.array(color_top) * (1 - t) + np.array(color_bottom) * t) * np.ones((1, w, 1))
    return Image.fromarray(a.astype(np.uint8))


def test_metrics_separent_feu_et_gris():
    fire = metrics.sky_metrics(sky((60, 40, 120), (255, 110, 40)), 0, 1)
    gray = metrics.sky_metrics(sky((150, 150, 155), (170, 170, 172)), 0, 1)
    night = metrics.sky_metrics(sky((5, 5, 12), (10, 10, 20)), 0, 1)
    assert fire["sky_score_raw"] > 60 > gray["sky_score_raw"]
    assert gray["gray_ratio"] > 0.9 and night["dark_ratio"] > 0.9


def test_coucher_identique_a_l_app():
    s = sun.effective_sunset(dt.date(2026, 12, 21), 48.0397, 7.0172, 1363, 0, common.TZ)
    assert s.astimezone(common.TZ).strftime("%H:%M") == "16:51"  # même valeur que js/sun.js


@pytest.fixture
def isole(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA", tmp_path)
    for mod in (capture, snapshot):
        monkeypatch.setattr(mod, "DATA", tmp_path)
    cams = tmp_path / "webcams.yml"
    cams.write_text("donon:\n  - {id: '111', enabled: true, sky_top: 0.0, sky_bottom: 0.5}\n"
                    "  - {id: '222', enabled: false}\n", "utf-8")
    monkeypatch.setattr(common, "WEBCAMS_FILE", cams)
    monkeypatch.setenv("WINDY_KEY", "test")
    return tmp_path


def test_capture_puis_consolidation(isole, monkeypatch):
    day = dt.date(2026, 11, 20)
    s = next(x for x in common.sunset_spots() if x["spot_id"] == "donon")
    sset = sun.effective_sunset(day, s["lat"], s["lon"], s["alt"], s["horizon"], common.TZ)
    imgs = {-30: sky((120, 150, 200), (200, 190, 160)), -10: sky((70, 50, 130), (250, 120, 50)),
            0: sky((60, 40, 110), (255, 100, 40)), 10: sky((30, 25, 60), (120, 60, 50))}
    state = {}

    def fake_get(key, wid):
        m = state["m"]
        return {"id": wid, "image": f"https://img/{wid}/{m}", "updated": sset + dt.timedelta(minutes=m), "status": "active"}

    def fake_dl(url):
        b = io.BytesIO(); imgs[int(url.rsplit("/", 1)[1])].save(b, "JPEG"); return b.getvalue()

    monkeypatch.setattr(windy, "get", fake_get)
    monkeypatch.setattr(windy, "download", fake_dl)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    for m in (-30, -10, 0, 10):
        state["m"] = m
        capture.main(now=sset + dt.timedelta(minutes=m + 2))
        capture.main(now=sset + dt.timedelta(minutes=m + 4))  # même image : pas de doublon
    caps = common.read_csv(isole / "captures.csv")
    assert len(caps) == 4 and {c["webcam_id"] for c in caps} == {"111"}  # caméra désactivée ignorée
    capture.main(now=sset + dt.timedelta(minutes=60))
    obs = common.read_csv(isole / "observations.csv")
    assert len(obs) == 1 and float(obs[0]["peak_minutes_from_sunset"]) in (-10.0, 0.0)
    capture.main(now=sset + dt.timedelta(minutes=90))
    assert len(common.read_csv(isole / "observations.csv")) == 1  # consolidation idempotente


def test_snapshot(isole, monkeypatch):
    now = dt.datetime(2026, 11, 20, 10, 30, tzinfo=dt.timezone.utc)
    spots = common.sunset_spots()
    times = [(dt.datetime(2026, 11, 20) + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(72)]
    ser = lambda low: {"time": times, "model": ["meteofrance"] * 72, **{v: [low if v == "cloud_cover_low" else 5.0] * 72 for v in weather.VARS}}
    monkeypatch.setattr(weather, "forecast", lambda pts: ([ser(10) for _ in pts], ser(90)))
    monkeypatch.setattr(weather, "aerosols", lambda lat, lon: {"time": times, "aerosol_optical_depth": [0.08] * 72})
    snapshot.main(now=now)
    snapshot.main(now=now)  # relancer le même jour n'ajoute rien
    rows = common.read_csv(isole / "forecasts.csv")
    assert len(rows) == 2 * len(spots)
    assert {r["lead_day"] for r in rows} == {"0", "1"} and rows[0]["aod"] == "0.08"


def test_camera_partagee_entre_spots(tmp_path, monkeypatch):
    """Une caméra rattachée à 2 spots : téléchargée et étiquetée une seule fois, comptée pour les deux."""
    monkeypatch.setattr(common, "DATA", tmp_path)
    monkeypatch.setattr(capture, "DATA", tmp_path)
    cams = tmp_path / "webcams.yml"
    cams.write_text("cameras:\n  '999': {title: Tour, enabled: true, sky_top: 0.0, sky_bottom: 0.6}\n"
                    "spots:\n  champ-du-feu: [{id: '999', distance_km: 0.4, use: true}]\n"
                    "  sainte-odile: [{id: '999', distance_km: 10.2, use: true}]\n", "utf-8")
    monkeypatch.setattr(common, "WEBCAMS_FILE", cams)
    monkeypatch.setenv("WINDY_KEY", "test")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    day = dt.date(2026, 11, 20)
    s = next(x for x in common.sunset_spots() if x["spot_id"] == "champ-du-feu")
    sset = sun.effective_sunset(day, s["lat"], s["lon"], s["alt"], s["horizon"], common.TZ)
    calls = {"dl": 0, "vision": 0}

    def fake_dl(url):
        calls["dl"] += 1
        b = io.BytesIO(); sky((60, 40, 110), (255, 100, 40)).save(b, "JPEG"); return b.getvalue()

    def fake_vision(jpeg):
        calls["vision"] += 1
        return {"vis_sky": "feu", "vis_sea": 0, "vis_alps": None, "vis_conf": 0.9}

    monkeypatch.setattr(windy, "get", lambda key, wid: {"id": wid, "image": "https://img/x", "updated": sset, "status": "active"})
    monkeypatch.setattr(windy, "download", fake_dl)
    monkeypatch.setattr(capture.vision, "classify", fake_vision)
    capture.main(now=sset + dt.timedelta(minutes=3))
    caps = common.read_csv(tmp_path / "captures.csv")
    assert {c["spot_id"] for c in caps} == {"champ-du-feu", "sainte-odile"}
    assert calls == {"dl": 1, "vision": 1}
    assert all(c["vis_sky"] == "feu" for c in caps)
    capture.main(now=sset + dt.timedelta(minutes=70))
    obs = common.read_csv(tmp_path / "observations.csv")
    assert {o["spot_id"] for o in obs} == {"champ-du-feu", "sainte-odile"}


def test_ancien_format_converti(tmp_path, monkeypatch):
    f = tmp_path / "w.yml"
    f.write_text("a:\n- {id: '1', enabled: true, sky_bottom: 0.3}\nb:\n- {id: '1', enabled: true, sky_bottom: 0.2}\n"
                 "- {id: '2', enabled: false}\n", "utf-8")
    monkeypatch.setattr(common, "WEBCAMS_FILE", f)
    cams, links = common.read_webcams_raw()
    assert cams["1"]["sky_bottom"] == 0.2 and cams["2"]["enabled"] is False
    assert [c["id"] for c in common.load_webcams()["b"]] == ["1"]


def test_gemini_reponse_et_repli(monkeypatch):
    from truth import vision
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(vision, "GEMINI_MODELS", ["retire", "gemini-2.5-flash"])
    calls = []

    class R:
        def __init__(self, code, data): self.status_code, self._d, self.ok, self.text = code, data, code == 200, ""
        def json(self): return self._d

    def fake_post(url, **kw):
        calls.append(url.split("/models/")[1].split(":")[0])
        if "retire" in url:
            return R(404, {})
        txt = '{"sky": "feu", "sea_of_clouds": true, "alps_visible": null, "confidence": 0.8}'
        return R(200, {"candidates": [{"content": {"parts": [{"text": txt}]}}]})

    monkeypatch.setattr(vision.requests, "post", fake_post)
    assert vision.classify(b"jpeg") == {"vis_sky": "feu", "vis_sea": 1, "vis_alps": None, "vis_conf": 0.8}
    assert calls == ["retire", "gemini-2.5-flash"]
