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


def dhash(img: Image.Image, sky_bottom=0.42, size=8):
    """Empreinte de structure (dHash, 64 bits, en hexadécimal) du décor sous la bande de ciel.

    Le relief bouge peu d'un soir à l'autre, contrairement aux nuages : deux images d'une caméra fixe
    restent à quelques bits l'une de l'autre, une caméra qui a pivoté s'en écarte nettement.
    Si la bande de ciel couvre presque toute l'image, on prend l'image entière.
    """
    w, h = img.size
    top = sky_bottom if sky_bottom <= 0.85 else 0.0
    crop = img.convert("L").crop((0, int(h * top), w, h))
    a = np.asarray(crop.resize((size + 1, size), Image.Resampling.BOX), dtype=np.float32)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):0{size * size // 4}x}"


def hamming(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")
