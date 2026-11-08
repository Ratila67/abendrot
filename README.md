# Abendrot, v4

Où voir le coucher de soleil ce soir en Alsace et dans les Vosges.

PWA statique, sans build et sans backend : elle s’héberge gratuitement et s’installe sur l’écran d’accueil du téléphone.

## Fonctionnalités

| Bloc | Ce qu’il fait | Source |
|---|---|---|
| Horaires | Coucher au spot (altitude + masque de relief), heure de départ, heure de retour, durée de descente de nuit | Calcul local (SunCalc) |
| Temps de marche | DIN 33466 calculé sur la vraie trace, avec un facteur hiver (×1 / ×1,25 / ×1,5) | GPX |
| Météo sunset | Score 0-100 (nuages bas, moyens, hauts, pluie, visibilité), détection de mer de nuages, rafales, ressenti, isotherme 0 °C, neige au sol | Open-Meteo, un seul appel pour 20 spots |
| Carte | Plan IGN ou OpenTopoMap, ombrage du relief, tracé, spots, suivi GPS avec heure d’arrivée estimée au parking | Géoplateforme IGN, MapLibre |
| Vue 3D | Caméra placée sur le spot, orientée dans l’axe du coucher du jour | DEM Terrarium |
| Hors-ligne | Pré-chargement des tuiles de la zone, cache des données et de la météo | Service worker |
| Traces | Import GPX depuis le téléphone (stocké en IndexedDB) ou fichiers dans `gpx/` | Visorando, IGNrando… |
| Alerte | Message Telegram quotidien quand un spot dépasse un score donné | GitHub Actions |


## Sources de données (v3)

| Source | Ce qu’elle apporte | Clé | Limite gratuite |
|---|---|---|---|
| Open-Meteo, modèle Météo-France AROME/ARPEGE | Nuages bas, moyens, hauts, vent, ressenti, neige, à 1,3 km de résolution sur environ 4 jours, puis relais multi-modèle jusqu’à 16 jours | Non | Usage non commercial |
| Open-Meteo Air Quality (CAMS) | Aérosols : « les Alpes seront-elles visibles ? » | Non | Usage non commercial |
| Overpass (OpenStreetMap) | Abris, points de vue, eau, sommets et fermes-auberges le long du tracé | Non | Usage raisonnable |
| Sunsethue | 2e avis sur la qualité du coucher (modèle à rayons), 3 jours d’horizon | Oui | 1 000 crédits par jour, usage perso |
| Windy Webcams v3 | Photos en direct près du spot | Oui | Images valables 10 min, attribution requise |

Les clés se collent dans l’app (roue crantée). Elles restent dans le stockage du téléphone et ne sont jamais publiées avec le site.

### Proxy optionnel (recommandé si un appel est bloqué)

Certaines API refusent les appels directs depuis un navigateur (CORS). Le fichier `worker/proxy.js` règle ça et garde les clés côté serveur :

1. Cloudflare, Workers & Pages, Créer, Worker. Nomme-le `abendrot-proxy`, déploie, puis « Modifier le code ».
2. Remplace le code par le contenu de `worker/proxy.js` et déploie.
3. Dans Paramètres, Variables et secrets, ajoute `SUNSETHUE_KEY`, `WINDY_KEY` (secrets) et `ALLOWED_ORIGIN` = l’URL de ton app.
4. Dans l’app, Réglages, colle l’URL du Worker.

### Avalanche dans les Vosges

Météo-France ne publie de bulletin d’estimation du risque d’avalanche (BRA) que pour les Alpes, les Pyrénées et la Corse. Pour les Vosges, il existe une vigilance avalanche ponctuelle. L’app renvoie donc vers la vigilance Météo-France.

## Vérité terrain et modèle entraîné (v4)

Chaque soir, le pipeline photographie le ciel via les webcams proches des spots, le mesure, puis le compare à la prévision archivée le midi. Toutes les deux semaines, il entraîne un modèle et le publie dans `data/model.json`. L'app l'utilise automatiquement dès qu'il bat le score actuel.

```
10:30 UTC  truth.snapshot   prévision de ce soir et de demain, par spot      → data/truth/forecasts.csv
soirée     truth.capture    un job par soir, un passage toutes les 10 min    → data/truth/captures.csv
           (même job)       pic de la soirée par caméra, étiquette IA        → data/truth/observations.csv
           (même job)       un relevé par caméra : fraîche, périmée, erreur  → data/truth/fetches.csv
1er et 15  truth.train      walk-forward CV, export si meilleur que la réf.  → data/model.json + report.md
à la main  truth.qa         caméras jamais fraîches, figées ou qui pivotent  → terminal (ou --out)
```

**Contrôle des caméras.** `python -m truth.qa --days 14` lit `fetches.csv` et liste les caméras actives à vérifier : jamais fraîches ou souvent périmées (image de plus de 20 min au moment du relevé), en erreur, figées (même image sous plusieurs horodatages) ou qui pivotent (l’empreinte du décor, sous la bande de ciel, s’écarte de son cadrage habituel). Les caméras qui ne remontent jamais d’image fraîche n’apparaissent nulle part ailleurs : `captures.csv` ne garde que les images mesurées.

**Mesure du ciel.** La colorfulness de Hasler et Süsstrunk, la part de pixels chauds saturés, la part de gris et la part de noir sont calculées sur la bande de ciel de l’image. Chaque caméra est ensuite normalisée sur son propre historique (rang percentile), car chacune a son exposition.

**Étiquettes Claude (optionnelles).** Pour chaque caméra, l’image la plus proche du coucher est classée : type de ciel, mer de nuages, Alpes visibles. C’est la cible du modèle « mer de nuages ».

**Modèles.** Deux régressions logistiques, exportées en JSON et recalculées à l’identique en JavaScript (test de parité inclus). Un gradient boosting tourne aussi en comparaison dans le rapport : s’il écrase la logistique, c’est le signal qu’il faudra passer à un export ONNX.

### Mise en place (sur ordinateur, 15 min)

1. Pousse ce dossier dans un dépôt GitHub.
2. Dans Settings, Secrets and variables, Actions :
   - secret `WINDY_KEY` (obligatoire) ;
   - secret `GEMINI_API_KEY` (étiquettes par Gemini, palier gratuit) ou `ANTHROPIC_API_KEY` (Claude), optionnel ;
   - secret `SUNSETHUE_KEY` (variable du modèle, optionnel) ;
   - secret `HF_TOKEN` et variable `HF_DATASET`, par exemple `ton-pseudo/abendrot-sky` (archive privée des images sur Hugging Face, recommandé).
3. Onglet Actions, lance « Vérité terrain, découverte des webcams ».
4. Ouvre `truth/webcams.yml`. Dans `cameras`, chaque caméra se règle une seule fois : `enabled: false` si elle ne montre pas le ciel, et `sky_bottom` pour cadrer la bande de ciel (42 % par défaut). Dans `spots`, `use: false` exclut une caméra d’un spot précis seulement. Une caméra partagée par plusieurs spots est téléchargée et étiquetée une seule fois, et pèse moins quand elle est loin du spot.
5. Laisse tourner. Après environ 30 soirs observés, l’entraînement exporte un modèle s’il bat le score heuristique. Sinon, `data/truth/report.md` explique pourquoi.

Pour que chaque nouveau modèle arrive dans l’app sans manipulation, crée le projet Cloudflare Pages depuis ce dépôt GitHub (Git integration, sans commande de build, dossier de sortie `/`) plutôt qu’en import de zip.

### Coûts et limites

- GitHub Actions : environ 42 exécutions courtes par jour. C’est gratuit en dépôt public. En dépôt privé, ça tient dans le quota gratuit mensuel, mais surveille la consommation.
- Claude : au plus un appel par caméra et par soir.
- Les CSV restent petits (quelques Mo par an). Les images partent sur Hugging Face, jamais dans Git.
- Garde la trace écrite de l’autorisation de Windy pour l’entraînement dans `docs/`.

### Tests

```bash
pip install -r requirements-train.txt pytest
python -m pytest tests -q
```

## Lancer en local

```bash
python3 -m http.server 8080   # puis http://localhost:8080
```

Un serveur est obligatoire : les modules ES, le service worker et la géolocalisation ne fonctionnent pas en `file://`.

## Déployer en 2 minutes (GitHub Pages)

1. Crée un dépôt et pousse ce dossier.
2. Va dans Settings, Pages, choisis « Deploy from a branch », puis `main` et `/root`.
3. Ouvre l’URL `https://<toi>.github.io/<repo>/` sur ton téléphone, puis « Ajouter à l’écran d’accueil ».

Cloudflare Pages ou Netlify fonctionnent de la même façon, sans commande de build. Le HTTPS est obligatoire pour le GPS et le mode hors-ligne.

## Architecture

```
index.html ── js/app.js (vues, routing par #hash, état)
               ├─ js/sun.js      soleil : coucher effectif, azimut
               ├─ js/geo.js      GPX → trace, D+/D- lissés, DIN 33466
               ├─ js/weather.js  Open-Meteo : prévisions, altitudes, score  ◄── réutilisé par scripts/
               ├─ js/map.js      MapLibre : fonds, 3D, tuiles hors-ligne
               └─ js/store.js    IndexedDB : favoris, GPX importés, cache altitudes
               ├─ js/sources.js  Sunsethue, Windy, Overpass, aérosols
worker/proxy.js  proxy Cloudflare optionnel (clés côté serveur, cache)
worker/cron.js   déclencheur planifié des workflows (docs/declencheur-cloudflare.md)
sw.js          shell stale-while-revalidate, tuiles cache-first (6 000 max), API network-first
data/hikes.json  le « CMS » : ajouter une rando = ajouter un objet
```

Les modules `sun.js` et `weather.js` sont purs (sans DOM). Le même code tourne donc dans le navigateur et dans le script d’alerte Node.

## Ajouter une rando

Ajoute un objet dans `data/hikes.json`. Les champs clés sont :

- `spots[].sunset: true` désigne le point où tu veux être au coucher.
- `horizon` est le masque de relief à l’ouest, en degrés (0 sur un sommet, environ 3 sur le piémont).
- `waypoints` contient les points clés `[lat, lon, alt]`. Ils servent de repli tant qu’il n’y a pas de GPX.
- `gpx` est un nom de fichier dans `gpx/` (optionnel).
- `country: "DE"` bascule automatiquement le fond de carte sur Topo, car le Plan IGN ne couvre que la France.

## Alerte Telegram

1. Crée un bot avec @BotFather, puis récupère ton `chat_id`.
2. Dans le dépôt, va dans Settings, Secrets, et ajoute `TELEGRAM_TOKEN` et `TELEGRAM_CHAT_ID`.
3. Optionnel : ajoute le secret `SUNSETHUE_KEY` pour un 2e avis dans le message. Dans Variables, tu peux définir `APP_URL`, `MIN_SCORE` (70 par défaut) et `HIKES` (ids séparés par des virgules).
4. Le workflow `.github/workflows/sunset-alert.yml` est lancé tous les jours à 11 h UTC (12 h en hiver, 13 h en été) par le Worker
   `worker/cron.js` : voir `docs/declencheur-cloudflare.md`. Tu peux aussi le lancer à la main.

Test local sans token : `TZ=Europe/Paris node scripts/sunset-alert.mjs` affiche le message dans le terminal.

## Limites connues

- Le score sunset est une heuristique, pas un modèle validé. Calibre-le avec tes sorties (voir la roadmap).
- Tant qu’aucun GPX n’est importé, les tracés sont schématiques : ce sont des segments droits entre points clés. Le profil vient du MNT (90 m), mais il ne faut pas naviguer avec.
- Les coordonnées des parkings et des spots sont approximatives (±200 m). L’itinéraire voiture passe par une recherche Google Maps par nom.
- OpenTopoMap et les tuiles Terrarium ont des politiques d’usage « raisonnable » : ça convient pour un usage perso, pas pour un service public à fort trafic.
- Les accès hivernaux (Route des Crêtes, cols) sont à vérifier le jour même.

## Roadmap

1. **Masque d’horizon calculé** : lancer des rayons dans le MNT dans l’axe de l’azimut du coucher, ce qui remplace le champ `horizon` saisi à la main.
2. **Boucle de calibration** : bouton « note ce sunset » après la sortie, puis régression simple entre les variables météo et ta note (CSV vers scikit-learn).
3. **Partage** : Supabase (Postgres + auth) pour des listes partagées entre amis, quand le besoin existera vraiment.
4. **Webcams** : lien vers la webcam la plus proche du spot pour vérifier en direct.

## Licences des données

Plan IGN : licence ouverte Etalab. OpenTopoMap : CC-BY-SA. OpenStreetMap : ODbL. Open-Meteo : CC-BY 4.0. MNT : Copernicus GLO-90.
