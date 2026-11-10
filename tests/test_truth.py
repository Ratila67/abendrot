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
    monkeypatch.setattr(weather, "forecast", lambda pts: (_ for _ in ()).throw(RuntimeError("Open-Meteo 429")))
    snapshot.main(now=now)  # relancer le même jour n'ajoute rien, et ne rappelle pas la météo
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
    monkeypatch.setattr(vision, "time", FakeTime())
    assert vision.classify(b"jpeg") == {"vis_sky": "feu", "vis_sea": 1, "vis_alps": None, "vis_conf": 0.8}
    assert calls == ["retire", "gemini-2.5-flash"]


class FakeTime:
    """Horloge simulée : sleep avance le temps sans attendre."""
    def __init__(self): self.now, self.sleeps = 1000.0, []
    def monotonic(self): return self.now
    def sleep(self, s):
        if s > 0:
            self.sleeps.append(round(s, 1)); self.now += s


def test_gemini_cadence_reprise_et_pause(monkeypatch):
    from truth import vision
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(vision, "GEMINI_MODELS", ["gemini-flash-latest", "gemini-flash-lite-latest"])
    monkeypatch.setattr(vision, "_last", [0.0])
    monkeypatch.setattr(vision, "_paused", [False])
    monkeypatch.setattr(vision, "_warned", set())
    clock = FakeTime()
    monkeypatch.setattr(vision, "time", clock)
    ok = '{"sky": "pale", "sea_of_clouds": false, "alps_visible": false, "confidence": 0.6}'

    class R:
        def __init__(self, code, text=""):
            self.status_code, self.ok, self.text = code, code == 200, text
        def json(self): return {"candidates": [{"content": {"parts": [{"text": ok}]}}]}

    replies, calls = [], []
    def fake_post(url, **kw):
        calls.append(url.split("/models/")[1].split(":")[0])
        return replies.pop(0)
    monkeypatch.setattr(vision.requests, "post", fake_post)

    # 429 avec délai court : on attend ce que Google demande, puis on reprend le même modèle
    replies[:] = [R(429, '{"error": {"details": [{"retryDelay": "5s"}]}}'), R(200)]
    assert vision.classify(b"a")["vis_sky"] == "pale"
    assert calls == ["gemini-flash-latest"] * 2 and clock.sleeps == [5.0, 2.0]   # 5 s demandées, puis 7 s entre deux appels

    # appel suivant espacé de 7 s ; quota du jour épuisé (délai d'une heure) : modèle lite tout de suite ;
    # lite surchargé trois fois : on abandonne pour ce passage, sans plus rien appeler
    calls.clear(); clock.sleeps.clear()
    replies[:] = [R(429, '{"retryDelay": "3600s"}'), R(503), R(503), R(503)]
    assert vision.classify(b"b") == {}
    assert calls == ["gemini-flash-latest"] + ["gemini-flash-lite-latest"] * 3
    assert clock.sleeps[0] == 7.0 and 3600.0 not in clock.sleeps
    assert vision.classify(b"c") == {} and len(calls) == 4   # en pause jusqu'au passage suivant


def decor(seed, size=(400, 225)):
    """Image synthétique texturée : des blocs de gris, comme un relief vu de loin."""
    rng = np.random.default_rng(seed)
    a = rng.integers(20, 235, (9, 16, 1)).repeat(3, axis=2).astype(np.uint8)
    return Image.fromarray(a).resize(size, Image.Resampling.NEAREST)


def test_dhash_stable_a_la_lumiere_sensible_au_pivot():
    from truth.qa import PIVOT_BITS
    img = decor(1)
    ref = metrics.dhash(img, 0.0)
    plus_sombre = Image.fromarray((np.asarray(img) * 0.45).astype(np.uint8))
    pivot = Image.fromarray(np.roll(np.asarray(img), 130, axis=1))
    assert len(ref) == 16
    assert metrics.hamming(ref, metrics.dhash(plus_sombre, 0.0)) <= 4
    assert metrics.hamming(ref, metrics.dhash(pivot, 0.0)) > PIVOT_BITS
    assert metrics.hamming(ref, metrics.dhash(decor(2), 0.0)) > PIVOT_BITS


def test_capture_journalise_les_releves(isole, monkeypatch):
    day = dt.date(2026, 11, 20)
    s = next(x for x in common.sunset_spots() if x["spot_id"] == "donon")
    sset = sun.effective_sunset(day, s["lat"], s["lon"], s["alt"], s["horizon"], common.TZ)
    state = {"upd": sset - dt.timedelta(minutes=60), "fail": False}

    def fake_get(key, wid):
        if state["fail"]:
            raise RuntimeError("Windy 503 : indisponible")
        return {"id": wid, "image": "https://img/x", "updated": state["upd"], "status": "active"}

    def fake_dl(url):
        b = io.BytesIO(); decor(3).save(b, "JPEG"); return b.getvalue()

    monkeypatch.setattr(windy, "get", fake_get)
    monkeypatch.setattr(windy, "download", fake_dl)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    capture.main(now=sset + dt.timedelta(minutes=2))          # image vieille d'une heure
    state["upd"] = sset
    capture.main(now=sset + dt.timedelta(minutes=3))          # image fraîche
    state["fail"] = True
    capture.main(now=sset + dt.timedelta(minutes=13))         # API en panne
    capture.main(now=sset + dt.timedelta(minutes=120))        # hors fenêtre : aucun relevé
    f = common.read_csv(isole / "fetches.csv")
    assert [r["status"] for r in f] == ["stale", "fresh", "error"] and {r["webcam_id"] for r in f} == {"111"}
    assert float(f[0]["age_min"]) == 62.0 and not f[0]["sha1"]
    assert len(f[1]["sha1"]) == 12 and len(f[1]["dhash"]) == 16 and float(f[1]["minutes_from_sunset"]) == 0.0
    assert "503" in f[2]["error"]
    assert len(common.read_csv(isole / "captures.csv")) == 1   # seule l'image fraîche est mesurée


def test_qa_signale_les_cameras_a_problemes():
    from truth import qa
    h_fixe, h_pivot = metrics.dhash(decor(1), 0.0), metrics.dhash(Image.fromarray(np.roll(np.asarray(decor(1)), 130, axis=1)), 0.0)

    def rel(cid, d, m, status="fresh", sha="", h=""):
        return {"date": f"2026-11-{d:02d}", "webcam_id": cid, "image_updated_utc": f"2026-11-{d:02d}T16:{m:02d}:00+00:00",
                "age_min": "45" if status == "stale" else "2", "minutes_from_sunset": str(m - 15), "status": status,
                "sha1": sha or f"{cid}{d}{m}", "dhash": h if status == "fresh" else ""}

    f = []
    for d in range(1, 5):
        for m in (5, 15, 25):
            f += [rel("perimee", d, m, "stale"),
                  rel("figee", d, m, sha="toujours-la-meme", h=h_fixe),
                  rel("pivote", d, m, h=h_pivot if d % 2 else h_fixe),
                  rel("saine", d, m, h=h_fixe)]
    cams = {c: {"enabled": True, "title": c} for c in ("perimee", "figee", "pivote", "saine", "muette")}
    cams["off"] = {"enabled": False}
    res = {r["id"]: r["flags"] for r in qa.audit(f, cams)}
    assert res == {"perimee": ["jamais fraîche"], "figee": ["image figée"], "pivote": ["pivote ?"], "saine": [],
                   "muette": ["jamais relevée"]}
    assert qa.audit(f, cams, since="2026-11-05")[0]["checks"] == 0   # filtre de date
    assert "| saine |" in qa.report(qa.audit(f, cams))


def test_boucle_du_soir_couvre_chaque_fenetre(capsys, isole):
    """Le workflow boucle sur --wait : en partant de n'importe quel déclenchement assez tôt,
    chaque spot est capturé au moins toutes les 10 min dans sa fenêtre, puis la soirée est consolidée."""
    spots = common.sunset_spots()
    day = dt.date(2026, 11, 8)
    start, end = capture.evening_window(dt.datetime(2026, 11, 8, 12, tzinfo=dt.timezone.utc), spots)
    assert capture.wait_seconds(start - dt.timedelta(minutes=capture.LEAD + 1), spots) is None      # trop tôt : un créneau plus tard s'en charge
    assert capture.wait_seconds(end, spots) is None                                  # soirée finie
    assert capture.wait_seconds(start - dt.timedelta(minutes=30), spots) == 1800    # attend le début de la fenêtre
    assert capture.wait_seconds(start + dt.timedelta(minutes=3), spots) == 420      # créneau suivant
    for t0 in (start - dt.timedelta(minutes=capture.LEAD - 5), start + dt.timedelta(minutes=27, seconds=13)):
        now, passes = t0, []
        while (w := capture.wait_seconds(now, spots)) is not None:
            now += dt.timedelta(seconds=w)
            passes.append(now)
            now += dt.timedelta(seconds=75)                                          # durée d'un passage
        assert passes[-1] >= end - dt.timedelta(minutes=1) and len(passes) < 40
        for s in spots:
            sset = sun.effective_sunset(day, s["lat"], s["lon"], s["alt"], 0, common.TZ)
            lo, hi = max(t0, sset + dt.timedelta(minutes=capture.WINDOW[0])), sset + dt.timedelta(minutes=capture.WINDOW[1])
            inside = [p for p in passes if lo <= p <= hi]
            assert inside and max(b - a for a, b in zip([lo] + inside, inside + [hi])) <= dt.timedelta(minutes=11), s["spot_id"]
    donon = [s for s in spots if s["spot_id"] == "donon"]          # main ne garde que les spots ayant une caméra
    d_start, d_end = capture.evening_window(start, donon)
    capture.main(now=d_start - dt.timedelta(minutes=10), argv=["--wait"])
    assert capsys.readouterr().out.strip() == "600"
    with pytest.raises(SystemExit, match="Rien à capturer"):
        capture.main(now=d_end + dt.timedelta(minutes=1), argv=["--wait"])


def test_echec_d_etiquetage_visible_en_annotation(monkeypatch, capsys):
    from truth import vision
    monkeypatch.setattr(vision, "_warned", set())
    monkeypatch.setattr(vision, "_paused", [False])
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert vision.classify(b"jpeg") == {} and vision.classify(b"jpeg") == {}
    out = capsys.readouterr().out
    assert out.count("::warning::étiquetage IA désactivé") == 1   # une seule annotation par passage

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(vision, "_gemini", lambda jpeg, key: (_ for _ in ()).throw(RuntimeError("Gemini 429 : quota")))
    assert vision.classify(b"jpeg") == {}
    monkeypatch.setattr(vision, "_gemini", lambda jpeg, key: "pas du JSON {")
    assert vision.classify(b"jpeg") == {}
    out = capsys.readouterr().out
    assert "::warning::étiquetage gemini indisponible : Gemini 429 : quota" in out
    assert "::warning::étiquetage gemini : réponse illisible" in out
