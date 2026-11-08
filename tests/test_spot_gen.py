"""Générateur de randos « spot d'abord » : direction OSM, horizon sur un relief simulé, choix du parking, fiche (hors-ligne)."""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gpx_route as G  # noqa: E402
import spot_gen as SG  # noqa: E402


def test_direction_osm():
    assert SG.faces_sunset(None) and SG.faces_sunset("W") and SG.faces_sunset("O") and SG.faces_sunset("0-360")
    assert SG.faces_sunset("180-270") and SG.faces_sunset("NW") and SG.faces_sunset("E;W")
    assert not SG.faces_sunset("E") and not SG.faces_sunset("90") and not SG.faces_sunset("30-120")
    assert SG.direction_arcs("n'importe quoi") is None and SG.faces_sunset("n'importe quoi")


def test_horizon_degage_ou_masque():
    plat = lambda lat, lon: 1000.0 if abs(lat - 48.0) < 0.001 and abs(lon - 7.0) < 0.001 else 200.0   # sommet isolé sur la plaine
    o = SG.openness(SG.horizon(48.0, 7.0, elev=plat))
    assert o["share"] == 1 and SG.sunset_score(o["share"]) == 5
    mur = lambda lat, lon: 1500.0 if lon < 6.97 else 1000.0 if abs(lat - 48.0) < 0.001 and abs(lon - 7.0) < 0.001 else 900.0
    hz = SG.horizon(48.0, 7.0, elev=mur)                   # crête 500 m plus haut, 2 km à l'ouest
    assert all(a > 5 for a in hz.values()) and SG.openness(hz)["share"] == 0
    tour = lambda lat, lon: 1020.0 if lon < 6.995 else 990.0   # plateau 30 m plus haut à 400 m : la tour de 40 m dégage la vue
    assert SG.openness(SG.horizon(48.0, 7.0, elev=tour))["share"] == 0
    assert SG.openness(SG.horizon(48.0, 7.0, extra_m=40, elev=tour))["share"] == 1
    plaine = lambda lat, lon: 1000.0 if lon < 6.55 else 150.0   # plaine, Vosges à 34 km : vue sur la crête, rien en dessous
    assert SG.openness(SG.horizon(48.0, 7.0, elev=plaine))["share"] == 0          # au-delà de 25 km, la crête compte
    assert SG.dominance(48.0, 7.0, elev=plaine) < SG.MIN_DOMINANCE_M <= SG.dominance(48.0, 7.0, elev=plat)


def feature(p, spot, ele_top=900, road=False):
    """Aller-retour en ligne droite du parking au spot, en 20 pas."""
    n = 20
    go = [[p[1] + (spot[1] - p[1]) * i / n, p[0] + (spot[0] - p[0]) * i / n, 500 + (ele_top - 500) * i / n] for i in range(n + 1)]
    coords = go + go[-2::-1]
    km = 2 * G.hav(*p, *spot)
    head = ["Longitude", "Latitude", "Elevation", "Distance", "CostPerKm", "ElevCost", "TurnCost", "NodeCost",
            "InitialCost", "WayTags", "NodeTags", "Time", "Energy"]
    msg = ["0"] * 3 + [str(km * 1000)] + ["0"] * 5 + ["highway=residential" if road else "highway=path"] + ["", "0", "0"]
    return {"geometry": {"coordinates": coords}, "properties": {"track-length": str(km * 1000), "filtered ascend": "400", "messages": [head, msg]}}


def test_parking_le_plus_proche_qui_convient():
    spot = {"lat": 48.0, "lon": 7.0}
    parks = [{"lat": 48.003, "lon": 7.0, "name": "trop près"},        # 330 m
             {"lat": 48.012, "lon": 7.0, "name": "par la route"},     # 1,3 km
             {"lat": 48.0122, "lon": 7.0005, "name": "même endroit"},  # à 30 m du précédent : pas retenté
             {"lat": 48.02, "lon": 7.0, "name": "bon"},               # 2,2 km
             {"lat": 48.03, "lon": 7.0, "name": "plus loin"}]
    calls = []
    def route(w):
        calls.append(w)
        return feature(w[0], w[1], road=abs(w[0][0] - 48.012) < 0.001)
    p, s = SG.pick_parking(spot, parks, route=route)
    assert p["name"] == "bon" and len(calls) == 2
    assert abs(s["to_spot_km"] - 2.22) < 0.05 and s["road_share"] == 0
    jumeaux = [{"lat": 48.02, "lon": 7.0, "name": "a"}, {"lat": 48.02, "lon": 7.0, "name": "b"}]   # même distance : pas de comparaison de dict
    assert SG.pick_parking(spot, jumeaux, route=route)[0]["name"] == "a"
    p, why = SG.pick_parking(spot, parks[:1], route=route)
    assert p is None and "aucun parking" in why


def test_fiche_au_format_de_l_app(monkeypatch):
    monkeypatch.setattr(SG, "elevation", lambda lat, lon: 500.0)
    park, spot = {"lat": 48.02, "lon": 7.0, "name": None}, {"lat": 48.0, "lon": 7.0}
    s = G.summary(feature((48.02, 7.0), (48.0, 7.0)))
    s.update(G.track_metrics(s["coords"], spot))
    c = {"osm": "n42", "name": "Rocher du Couchant", "lat": 48.0, "lon": 7.0, "tower": False, "height": 0, "direction": None, "country": "FR"}
    o = {"share": 0.7, "winter": 1.0, "summer": 0.25}
    h = SG.build_hike(c, o, {az: 0.4 for az in SG.ARC}, park, s, {"name": "Orbey"})
    curated = json.loads((G.ROOT / "data" / "hikes.json").read_text("utf-8"))[0]
    assert set(curated) - {"merged"} <= set(h)              # tous les champs que l'app lit
    assert h["id"] == "osm-n42" and h["auto"] and h["gpx"] == "auto/osm-n42.gpx" and h["sunsetScore"] == 4
    assert h["spots"][0]["sunset"] and h["spots"][0]["alt"] == 900 and h["start"]["alt"] == 500
    assert h["start"]["name"] == "Parking, Orbey" and h["start"]["query"] == "48.02000,7.00000"
    assert h["km"] == round(s["km"], 1) and h["toSpot"] % 5 == 0 and h["toSpot"] > h["down"] > 0
    assert "surtout l’hiver" in h["view"] and "70 %" in h["view"] and h["horizon"] == 0.4
    assert h["access"] == "Accès routier non vérifié." and h["snow"]


def test_simplification_garde_la_forme():
    ligne = [(48.0, 7.0 + i * 0.0001, 500) for i in range(50)]              # 50 points alignés
    assert SG.simplify(ligne) == [ligne[0], ligne[-1]]
    coude = ligne + [(48.0 + i * 0.0001, 7.0049, 500) for i in range(1, 30)]
    s = SG.simplify(coude)
    assert len(s) == 3 and s[1] == ligne[-1]
    assert math.isclose(G.hav(*s[0][:2], *s[1][:2]), G.hav(*ligne[0][:2], *ligne[-1][:2]))
