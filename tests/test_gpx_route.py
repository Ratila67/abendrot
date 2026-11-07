"""Traces GPX calculées : résumé d'une réponse BRouter, écarts avec la fiche, GPX valide (hors-ligne)."""
import json
import random
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gpx_route as G  # noqa: E402

HEAD = ["Longitude", "Latitude", "Elevation", "Distance", "CostPerKm", "ElevCost", "TurnCost", "NodeCost",
        "InitialCost", "WayTags", "NodeTags", "Time", "Energy"]


def feature():
    coords = [[7.0 + i * 0.001, 48.0, 1000 + i] for i in range(11)]   # environ 750 m vers l'est
    msg = lambda d, tags: ["0", "0", "0", str(d), "0", "0", "0", "0", "0", tags, "", "0", "0"]
    return {"geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"track-length": "750", "filtered ascend": "12",
                           "messages": [HEAD, msg(600, "highway=path sac_scale=hiking"), msg(150, "highway=residential surface=asphalt")]}}


def test_summary_lit_distance_denivele_et_route():
    s = G.summary(feature())
    assert s["km"] == 0.75 and s["dplus"] == 12
    assert abs(s["road_share"] - 0.2) < 1e-9
    assert s["coords"][0] == (48.0, 7.0, 1000)


def test_alertes_spot_d_abord():
    s = {**G.summary(feature()), "snap_m": 40, "spot_m": 20, "to_spot_km": 2.5}
    assert G.flags(s, {"km": 0.6}) == []
    s.update(road_share=0.5, snap_m=400, spot_m=300, to_spot_km=0.4)
    f = G.flags(s, {"km": 0.4})
    assert len(f) == 5 and "50% sur route" in f and "spot à 300 m de la trace" in f
    assert "0.4 km jusqu’au spot : trop court pour une rando" in f
    assert "0.8 km contre 0.4 km sur la fiche : vérifier le parking" in f
    del s["spot_m"]
    assert "aucun spot sur la fiche" in G.flags(s, {"km": 1})


def aller_retour():
    """Parking à 1000 m, spot 2 km plus à l'est à 1200 m, retour par le même chemin."""
    aller = [(48.0, 7.0 + i * 0.0027, 1000 + 10 * i) for i in range(21)]
    return aller + aller[-2::-1]


def test_aller_retour_spot_au_demi_tour_et_temps_de_fiche():
    coords = aller_retour()
    spot = {"lat": 48.0, "lon": 7.054}
    i, d = G.nearest_index(coords, spot["lat"], spot["lon"])
    assert i == 20 and d < 0.001                       # le demi-tour, pas la sortie du rayon au retour
    graze = coords + [(48.00126, 7.054, 1000)]         # un dernier point qui frôle le rayon, à 140 m du spot
    assert G.nearest_index(graze, spot["lat"], spot["lon"])[0] == 20
    twice = coords + aller_retour()[1:21]              # une boucle qui repasse vraiment par le spot
    assert G.nearest_index(twice, spot["lat"], spot["lon"])[0] == len(twice) - 1
    m = G.track_metrics(coords, spot)
    assert abs(m["to_spot_km"] - m["km"] / 2) < 1e-9
    assert 150 < m["dplus"] <= 200                     # lissage sur 5 points : le sommet du demi-tour est arrondi, comme dans l'app
    assert abs(m["to_spot"] - G.din(m["km"] / 2, m["dplus"], 0, G.FACTOR)) < 1e-9   # toute la montée est à l'aller
    assert m["down"] < m["to_spot"]                    # on redescend plus vite
    h = {"km": 0, "dplus": 0, "dur": 0, "toSpot": 0, "down": 0}
    G.refresh_fiche(h, m)
    assert h["km"] == round(m["km"], 1) and h["dplus"] % 10 == 0 and h["toSpot"] % 5 == 0 and h["dur"] >= h["toSpot"] + h["down"] - 5


def test_spot_first_et_spot_cible():
    h = {"start": {"lat": 48.0, "lon": 7.0, "alt": 900}, "spots": [{"lat": 48.1, "lon": 7.1, "alt": 1100}, {"lat": 48.2, "lon": 7.2, "alt": 1200, "sunset": True}]}
    assert G.spot_first(h) == [[48.0, 7.0, 900], [48.2, 7.2, 1200], [48.0, 7.0, 900]]
    h["spots"][1]["sunset"] = False
    assert G.target_spot(h)["lat"] == 48.2              # sans spot du coucher : le dernier spot


@pytest.mark.skipif(not shutil.which("node"), reason="node absent")
def test_parite_js_python_des_chiffres_de_trace():
    rnd = random.Random(3)
    pts = aller_retour() + [(48.00126, 7.054, 1000)]   # avec un frôlement final, ignoré des deux côtés
    coords = [(la, lo, None if k % 17 == 5 else e + rnd.uniform(-6, 6)) for k, (la, lo, e) in enumerate(pts)]
    spot = {"lat": 48.0, "lon": 7.054}
    py = G.track_metrics(coords, spot)
    geo = (G.ROOT / "js" / "geo.js").as_uri()
    js = f"""
      import {{ buildTrack, nearestIndex, segment, din }} from '{geo}';
      const T = buildTrack({json.dumps(coords)}, {{ threshold: 4 }}), n = nearestIndex(T, {spot['lat']}, {spot['lon']}, true);
      const a = segment(T, 0, n.i), b = segment(T, n.i, T.pts.length - 1);
      console.log(JSON.stringify({{ km: T.km, dplus: T.dplus, total: din(T.km, T.dplus, T.dminus, 1.25), to_spot: din(a.km, a.up, a.down, 1.25), down: din(b.km, b.up, b.down, 1.25), spot_m: n.d * 1000 }}));
    """
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
    got = json.loads(out.stdout)
    for k, v in got.items():
        assert abs(py[k] - v) < 1e-9, (k, py[k], v)


def test_point_cle_loin_de_la_trace():
    coords = G.summary(feature())["coords"]
    assert G.snap_m(coords, [(48.0, 7.0)]) < 1
    assert 300 < G.snap_m(coords, [(48.0, 7.0), (48.004, 7.005)]) < 500   # environ 445 m au nord


def test_gpx_valide_et_echappe():
    hike = {"name": "Château & crête l’hiver"}
    xml = G.to_gpx(hike, [(48.1, 7.1, 1000.04), (48.2, 7.2, None)])
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    root = ET.fromstring(xml.encode("utf-8"))
    pts = root.findall(".//g:trkpt", ns)
    assert len(pts) == 2 and pts[0].get("lat") == "48.100000" and pts[0].find("g:ele", ns).text == "1000.0"
    assert pts[1].find("g:ele", ns) is None
    assert root.find("g:metadata/g:name", ns).text == hike["name"]


def test_apply_reporte_les_corrections_et_branche_les_traces_validees(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir(); (tmp_path / "gpx").mkdir()
    hike = lambda i: {"id": i, "name": i, "km": 0.75, "dplus": 12, "gpx": None, "spots": [],
                      "waypoints": [[48.0, 7.0, 1000], [48.0, 7.01, 1010], [48.0, 7.0, 1000]]}
    (tmp_path / "data" / "hikes.json").write_text(json.dumps([hike("a"), hike("b"), hike("c")], ensure_ascii=False, indent=1), "utf-8")
    corr = tmp_path / "corrections-gpx.json"
    corr.write_text(json.dumps({"waypoints": {"a": [[48.0, 7.0, 1000.4], [48.000004, 7.005, None], [48.0, 7.0, 1000.4]]},
                                "validate": ["a", "b"]}), "utf-8")
    routed = []
    monkeypatch.setattr(G, "ROOT", tmp_path)
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    monkeypatch.setattr(G, "route", lambda w: routed.append(w) or feature())
    monkeypatch.setattr("sys.argv", ["gpx_route", "--apply", str(corr)])
    G.main()
    out = {h["id"]: h for h in json.loads((tmp_path / "data" / "hikes.json").read_text("utf-8"))}
    assert out["a"]["waypoints"] == [[48.0, 7.0, 1000], [48.0, 7.005, None], [48.0, 7.0, 1000]]
    assert out["a"]["gpx"] == "a.gpx" and out["b"]["gpx"] == "b.gpx" and out["c"]["gpx"] is None
    assert out["b"]["waypoints"] == hike("b")["waypoints"]          # validée sans correction : points inchangés
    assert len(routed) == 2 and sorted(p.name for p in (tmp_path / "gpx").iterdir()) == ["a.gpx", "b.gpx"]
    assert out["a"]["km"] == 0.7 and out["a"]["dplus"] == 10 and out["c"]["km"] == 0.75   # fiche reprise de la trace validée
    assert (tmp_path / "data" / "hikes.json").read_text("utf-8").startswith('[\n {\n  "id": "a"')   # même mise en forme


def test_apply_refuse_une_rando_inconnue():
    import pytest
    with pytest.raises(SystemExit, match="zzz"):
        G.apply_corrections([{"id": "a", "waypoints": []}], {"validate": ["zzz"]})
