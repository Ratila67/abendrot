"""Entraînement sur données synthétiques + parité exacte entre sklearn et js/model.js."""
import datetime as dt
import json
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from truth import common, train


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    rng = np.random.default_rng(7)
    monkeypatch.setattr(train, "DATA", tmp_path)
    monkeypatch.setattr(train, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    spots = ["donon", "hohneck", "grand-ballon", "petit-ballon"]
    fc, ob = [], []
    for d in range(90):
        date = (dt.date(2026, 11, 1) + dt.timedelta(days=d)).isoformat()
        for s in spots:
            low, mid, high = rng.uniform(0, 100, 3)
            inv = rng.random() < 0.2
            plain_low = rng.uniform(60, 100) if inv else rng.uniform(0, 60)
            t850mp = rng.normal(3, 2) if inv else rng.normal(-7, 2)
            heur = np.clip(80 - 0.75 * low + (15 if 20 <= mid <= 70 else 0) + rng.normal(0, 25), 0, 100)
            # « vérité » : beau si peu de nuages bas ET nuages moyens épars, ou inversion
            truth = (1.4 - low / 50 + (1.2 if 20 <= mid <= 70 else -0.5) + (1.5 if inv else 0) + rng.normal(0, 0.6))
            for lead in (0, 1):
                fc.append({"snapshot_date": date, "date": date, "spot_id": s, "lead_day": lead, "cloud_low": low,
                           "cloud_mid": mid, "cloud_high": high, "precip": 0, "visibility_km": 40, "gust": 30,
                           "t850_minus_plain": t850mp, "plain_low": plain_low, "inversion": int(inv), "heuristic": heur,
                           "aod": rng.uniform(0.03, 0.3), "sunsethue": None if rng.random() < 0.4 else heur, "alt": 1100})
            for cam in ("a", "b"):
                ob.append({"date": date, "spot_id": s, "webcam_id": f"{s}-{cam}", "dark_ratio": 0.05,
                           "sky_score_raw": 50 + 15 * truth + rng.normal(0, 4), "vis_sea": int(inv and rng.random() < 0.9),
                           "vis_sky": "colore"})
    pd.DataFrame(fc).to_csv(tmp_path / "forecasts.csv", index=False)
    pd.DataFrame(ob).to_csv(tmp_path / "observations.csv", index=False)
    return tmp_path


def test_entrainement_bat_la_reference(dataset, monkeypatch):
    monkeypatch.setattr("sys.argv", ["train", "--min-days", "30"])
    train.main()
    model = json.loads((dataset / "data" / "model.json").read_text())
    good = model["targets"]["good"]
    assert good["auc"] > good["auc_baseline"]
    assert "sea" in model["targets"] and model["targets"]["sea"]["auc"] > 0.8
    assert (dataset / "report.md").exists()


@pytest.mark.skipif(not shutil.which("node"), reason="node absent")
def test_parite_js_python(dataset, monkeypatch):
    monkeypatch.setattr("sys.argv", ["train", "--min-days", "30"])
    train.main()
    df = train.build_dataset()
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    pipe = make_pipeline(SimpleImputer(strategy="mean"), StandardScaler(), LogisticRegression(C=0.5, class_weight="balanced", max_iter=2000))
    pipe.fit(df[train.FEATURES], df["good"])
    sample = df[train.FEATURES].head(25)
    expected = pipe.predict_proba(sample)[:, 1]
    rows = json.loads(sample.to_json(orient="records"))
    js = f"""
      import {{ predict }} from '{common.ROOT}/js/model.js';
      const M = {json.dumps(json.loads((dataset / 'data' / 'model.json').read_text()))};
      globalThis.fetch = async () => ({{ ok: true, json: async () => M }});
      const mod = await import('{common.ROOT}/js/model.js'); await mod.loadModel();
      console.log(JSON.stringify({json.dumps(rows)}.map(r => mod.predict('good', r))));
    """
    out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
    got = np.array(json.loads(out.stdout))
    assert np.allclose(got, expected, atol=1e-9), (got[:3], expected[:3])
