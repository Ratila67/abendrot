"""Étiquetage optionnel par Claude : type de ciel, mer de nuages, Alpes visibles."""
import base64
import json
import re

import requests

MODEL = "claude-haiku-5-5"
PROMPT = (
    "Image de webcam de montagne (Vosges), prise vers le coucher du soleil. "
    "Réponds UNIQUEMENT par un objet JSON, sans texte autour : "
    '{"sky": "feu" | "colore" | "pale" | "couvert" | "brouillard" | "nuit" | "inexploitable", '
    '"sea_of_clouds": true | false, "alps_visible": true | false | null, "confidence": nombre entre 0 et 1}. '
    "sky=feu : nuages ou ciel rouge-orange intense ; colore : teintes chaudes nettes ; pale : dégagé, peu de couleurs ; "
    "couvert : gris uniforme ; brouillard : visibilité nulle ou caméra dans le nuage. "
    "sea_of_clouds=true seulement si une couche nuageuse continue est visible EN DESSOUS du point de vue. "
    "alps_visible : chaîne enneigée lointaine visible à l'horizon, null si l'orientation ne le permet pas."
)
SKY = {"feu", "colore", "pale", "couvert", "brouillard", "nuit", "inexploitable"}


def classify(jpeg, key):
    if not key:
        return {}
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
            "model": MODEL, "max_tokens": 200,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(jpeg).decode()}},
                {"type": "text", "text": PROMPT}]}]})
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        d = json.loads(m.group(0)) if m else {}
        sky = d.get("sky") if d.get("sky") in SKY else None
        alps = d.get("alps_visible")
        return {"vis_sky": sky, "vis_sea": int(bool(d.get("sea_of_clouds"))) if sky else None,
                "vis_alps": None if alps is None else int(bool(alps)),
                "vis_conf": round(float(d.get("confidence", 0)), 2) if sky else None}
    except Exception as e:  # noqa: BLE001 - l'étiquetage ne doit jamais bloquer la capture
        print(f"  vision indisponible : {e}")
        return {}
