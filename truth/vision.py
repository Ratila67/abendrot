"""Étiquetage optionnel des images : type de ciel, mer de nuages, Alpes visibles.

Fournisseur choisi automatiquement selon le secret présent :
  GEMINI_API_KEY    → Gemini (palier gratuit de Google AI Studio)
  ANTHROPIC_API_KEY → Claude
"""
import base64
import json
import os
import re

import requests

MODEL = "claude-haiku-5-5"
GEMINI_MODELS = [m for m in (os.environ.get("GEMINI_MODEL", "").strip(), "gemini-2.5-flash", "gemini-flash-latest") if m]
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


def _parse(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    d = json.loads(m.group(0)) if m else {}
    sky = d.get("sky") if d.get("sky") in SKY else None
    alps = d.get("alps_visible")
    return {"vis_sky": sky, "vis_sea": int(bool(d.get("sea_of_clouds"))) if sky else None,
            "vis_alps": None if alps is None else int(bool(alps)),
            "vis_conf": round(float(d.get("confidence", 0)), 2) if sky else None} if sky else {}


def _claude(jpeg, key):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json={
        "model": MODEL, "max_tokens": 200,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
            {"type": "text", "text": PROMPT}]}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


def _gemini(jpeg, key):
    body = {"contents": [{"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
                                    {"text": PROMPT}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    last = None
    for model in GEMINI_MODELS:  # repli si un modèle est retiré ou renommé par Google
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          headers={"x-goog-api-key": key, "content-type": "application/json"}, json=body, timeout=60)
        if r.status_code == 404:
            last = f"modèle {model} introuvable"
            continue
        if not r.ok:
            raise RuntimeError(f"Gemini {r.status_code} : {r.text[:200]}")
        parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)
    raise RuntimeError(last or "aucun modèle Gemini disponible")


def provider():
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "claude"
    return None


def classify(jpeg):
    p = provider()
    if not p:
        return {}
    try:
        text = _gemini(jpeg, os.environ["GEMINI_API_KEY"].strip()) if p == "gemini" else _claude(jpeg, os.environ["ANTHROPIC_API_KEY"].strip())
        return _parse(text)
    except Exception as e:  # noqa: BLE001 - l'étiquetage ne doit jamais bloquer la capture
        print(f"  étiquetage {p} indisponible : {e}")
        return {}
