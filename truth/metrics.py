"""Mesure de la qualité du ciel sur une image de webcam."""
import numpy as np
from PIL import Image


def sky_metrics(img: Image.Image, top=0.0, bottom=0.42):
    """Métriques sur la bande de ciel [top, bottom] (fractions de la hauteur).

    colorfulness : Hasler & Süsstrunk (2003), 0 = gris, > 0.3 = très coloré.
    warm_ratio   : part des pixels orange, rouge ou rose saturés (la « braise »).
    gray_ratio   : part de pixels désaturés (ciel couvert ou brouillard).
    dark_ratio   : part de pixels très sombres (image prise trop tard).
    """
    w, h = img.size
    crop = img.convert("RGB").crop((0, int(h * top), w, max(int(h * bottom), int(h * top) + 1)))
    a = np.asarray(crop, dtype=np.float32) / 255
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    colorfulness = float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))
    hsv = np.asarray(crop.convert("HSV"), dtype=np.float32) / 255
    hue, sat, val = hsv[..., 0] * 360, hsv[..., 1], hsv[..., 2]
    warm = ((hue <= 45) | (hue >= 320)) & (sat >= 0.35) & (val >= 0.2)
    gray = (sat < 0.12) & (val > 0.15)
    dark = val < 0.08
    warm_ratio, dark_ratio = float(warm.mean()), float(dark.mean())
    score = 100 * (0.55 * min(colorfulness / 0.30, 1) + 0.45 * min(warm_ratio / 0.40, 1)) * (1 - 0.6 * dark_ratio)
    return {
        "colorfulness": round(colorfulness, 4), "warm_ratio": round(warm_ratio, 4),
        "sat_mean": round(float(sat.mean()), 4), "bright_mean": round(float(val.mean()), 4),
        "gray_ratio": round(float(gray.mean()), 4), "dark_ratio": round(dark_ratio, 4),
        "sky_score_raw": round(score, 2),
    }
