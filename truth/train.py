"""Entraîne les modèles « beau coucher » et « mer de nuages » et les exporte pour l'app.

Cible « beau coucher » : pour chaque caméra, le score du ciel est converti en rang percentile
sur tout son historique (chaque caméra a sa propre exposition), puis moyenné par spot et par soir.
Un soir est « beau » s'il est dans les 30 % meilleurs de ce spot.

Le modèle n'est exporté que s'il bat le score heuristique actuel en validation temporelle.
Usage : python -m truth.train [--min-days 30]
"""
import argparse
import datetime as dt
import json

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .common import DATA, ROOT, read_webcams_raw

FEATURES = ["cloud_low", "cloud_mid", "cloud_high", "precip", "visibility_km", "gust", "t850_minus_plain",
            "plain_low", "color_band", "alt", "heuristic", "sunsethue", "aod", "lead_day", "month_sin", "month_cos"]
GOOD_QUANTILE = 0.70


def build_dataset():
    fc = pd.read_csv(DATA / "forecasts.csv")
    ob = pd.read_csv(DATA / "observations.csv")
    ob = ob[ob["dark_ratio"] < 0.6].copy()  # image prise de nuit : inexploitable
    ob["webcam_id"] = ob["webcam_id"].astype(str)
    # Percentile calculé sur les images uniques : une caméra partagée par 3 spots ne compte pas 3 fois.
    uniq = ob.drop_duplicates(["date", "webcam_id"])[["date", "webcam_id", "sky_score_raw"]].copy()
    uniq["pct"] = uniq.groupby("webcam_id")["sky_score_raw"].rank(pct=True)
    ob = ob.merge(uniq[["date", "webcam_id", "pct"]], on=["date", "webcam_id"], how="left")
    # Une caméra à 15 km pèse moins qu'une caméra au sommet : poids = 1 / (1 + distance / 5 km).
    _, links = read_webcams_raw()
    dist = {(sp, l["id"]): l.get("distance_km") for sp, lst in links.items() for l in lst}
    ob["dist"] = [dist.get((sp, w)) if dist.get((sp, w)) is not None else 10.0 for sp, w in zip(ob["spot_id"], ob["webcam_id"])]
    ob["w"] = 1 / (1 + ob["dist"] / 5)
    ob["wp"] = ob["w"] * ob["pct"]
    ob["sea_near"] = np.where(ob["dist"] <= 12, pd.to_numeric(ob.get("vis_sea"), errors="coerce"), np.nan)
    spot = ob.groupby(["date", "spot_id"]).agg(wp=("wp", "sum"), w=("w", "sum"), n_cams=("webcam_id", "nunique"),
                                               sea=("sea_near", "max"), sky=("vis_sky", "first")).reset_index()
    spot["pct"] = spot["wp"] / spot["w"]
    spot["good"] = (spot["pct"] >= GOOD_QUANTILE).astype(int)
    # dernière prévision émise pour chaque (soir, spot, échéance)
    fc = fc.sort_values("snapshot_date").drop_duplicates(["date", "spot_id", "lead_day"], keep="last")
    df = fc.merge(spot, on=["date", "spot_id"], how="inner")
    color = df[["cloud_mid", "cloud_high"]].apply(pd.to_numeric, errors="coerce").max(axis=1)
    df["color_band"] = np.where(color.isna(), np.nan, ((color >= 20) & (color <= 70)).astype(float))  # nuages qui s'enflamment
    month = pd.to_datetime(df["date"]).dt.month
    df["month_sin"], df["month_cos"] = np.sin(2 * np.pi * month / 12), np.cos(2 * np.pi * month / 12)
    for c in FEATURES:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def temporal_cv(df, target, folds=4):
    """Validation « walk-forward » par date : on n'apprend jamais sur le futur."""
    dates = np.array(sorted(df["date"].unique()))
    cuts = np.array_split(dates, folds + 1)
    pred = {"logreg": [], "gbm": [], "heuristic": [], "sunsethue": [], "y": []}
    for k in range(1, folds + 1):
        tr = df[df["date"].isin(np.concatenate(cuts[:k]))]
        te = df[df["date"].isin(cuts[k])]
        if tr[target].nunique() < 2 or te[target].nunique() < 2:
            continue
        lr = make_pipeline(SimpleImputer(strategy="mean"), StandardScaler(), LogisticRegression(C=0.5, class_weight="balanced", max_iter=2000))
        lr.fit(tr[FEATURES], tr[target])
        gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=150, l2_regularization=1.0)
        gb.fit(tr[FEATURES], tr[target])
        pred["logreg"] += list(lr.predict_proba(te[FEATURES])[:, 1])
        pred["gbm"] += list(gb.predict_proba(te[FEATURES])[:, 1])
        pred["heuristic"] += list(te["heuristic"].fillna(50) / 100)
        pred["sunsethue"] += list(te["sunsethue"] / 100)
        pred["y"] += list(te[target])
    y = np.array(pred["y"])
    res = {}
    for name in ("logreg", "gbm", "heuristic", "sunsethue"):
        p = np.array(pred[name], dtype=float)
        ok = ~np.isnan(p)
        if ok.sum() >= 10 and len(set(y[ok])) == 2:
            res[name] = {"auc": round(roc_auc_score(y[ok], p[ok]), 3), "brier": round(brier_score_loss(y[ok], p[ok]), 3), "n": int(ok.sum())}
    return res


def export(df, target):
    pipe = make_pipeline(SimpleImputer(strategy="mean"), StandardScaler(), LogisticRegression(C=0.5, class_weight="balanced", max_iter=2000))
    pipe.fit(df[FEATURES], df[target])
    imp, sc, lr = pipe.named_steps.values()
    impute = [None if np.isnan(v) else float(v) for v in imp.statistics_]
    return {"features": FEATURES, "impute": impute, "mean": sc.mean_.tolist(), "scale": sc.scale_.tolist(),
            "coef": lr.coef_[0].tolist(), "intercept": float(lr.intercept_[0])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=30)
    args = ap.parse_args()
    if not (DATA / "observations.csv").exists() or not (DATA / "forecasts.csv").exists():
        raise SystemExit("Pas encore de données : laisse tourner les captures et les prévisions quelques semaines.")
    df = build_dataset()
    lines = [f"# Rapport d'entraînement Abendrot ({dt.date.today()})", "",
             f"{len(df)} lignes, {df['date'].nunique()} soirs, {df['spot_id'].nunique()} spots.", ""]
    out = {"version": 1, "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "targets": {}}
    for target, label in (("good", "Beau coucher"), ("sea", "Mer de nuages")):
        d = df.dropna(subset=[target]).copy()
        d[target] = d[target].astype(int)
        n_days, n_pos = d["date"].nunique(), int(d[target].sum())
        lines.append(f"## {label}")
        if n_days < args.min_days or n_pos < 8 or d[target].nunique() < 2:
            lines += [f"Pas assez de données : {n_days} soirs, {n_pos} cas positifs (minimum {args.min_days} soirs, 8 positifs).", ""]
            continue
        cv = temporal_cv(d, target)
        lines += ["| Modèle | AUC | Brier | n |", "|---|---|---|---|"]
        lines += [f"| {k} | {v['auc']} | {v['brier']} | {v['n']} |" for k, v in cv.items()]
        base = cv.get("heuristic", {}).get("auc", 0.5) if target == "good" else 0.5
        lr_auc = cv.get("logreg", {}).get("auc", 0)
        if lr_auc >= base + 0.01:
            out["targets"][target] = {**export(d, target), "n": len(d), "n_days": n_days, "auc": lr_auc, "auc_baseline": base}
            lines += ["", f"Exporté : AUC {lr_auc} contre {base} pour la référence."]
        else:
            lines += ["", f"Non exporté : AUC {lr_auc} ne bat pas la référence ({base}). L'app garde le score actuel."]
        lines.append("")
    (DATA / "report.md").write_text("\n".join(lines), "utf-8")
    if out["targets"]:
        (ROOT / "data" / "model.json").write_text(json.dumps(out, indent=1), "utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
