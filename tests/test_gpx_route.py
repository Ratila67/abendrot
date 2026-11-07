"""Traces GPX calculées : résumé d'une réponse BRouter, écarts avec la fiche, GPX valide (hors-ligne)."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

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


def test_ecarts_avec_la_fiche():
    s = {**G.summary(feature()), "snap_m": 40}
    assert G.flags(s, {"km": 0.8, "dplus": 13}) == []
    s.update(road_share=0.5, snap_m=400)
    f = G.flags(s, {"km": 2.0, "dplus": 100})
    assert len(f) == 4 and "distance 0.8 km contre 2.0 km" in f and "50% sur route" in f


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
    import json
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
    assert (tmp_path / "data" / "hikes.json").read_text("utf-8").startswith('[\n {\n  "id": "a"')   # même mise en forme


def test_apply_refuse_une_rando_inconnue():
    import pytest
    with pytest.raises(SystemExit, match="zzz"):
        G.apply_corrections([{"id": "a", "waypoints": []}], {"validate": ["zzz"]})
