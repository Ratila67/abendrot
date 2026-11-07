# Traces GPX

Une trace par rando, branchée dans `data/hikes.json` par son champ `gpx` :

```json
{ "id": "donon", "gpx": "donon.gpx", ... }
```

Priorité dans l'app : trace importée sur le téléphone > fichier de ce dossier > tracé schématique.

## Produire et valider les traces

Les traces de ce dossier sont calculées sur les sentiers OpenStreetMap (BRouter, profil randonnée)
à travers les points clés (`waypoints`) de chaque rando. Elles sont publiées sous licence ODbL
(© contributeurs OpenStreetMap). N'ajoute pas ici de GPX de Visorando, d'IGNrando ou d'un topo :
leur licence interdit de les republier dans un dépôt public.

**Spot d'abord.** Une rando mène du parking (`start`) au spot du coucher (sinon au dernier spot) et en
revient, par défaut en aller-retour : on redescend de nuit, à la frontale, par un chemin déjà parcouru.
On n'ajoute un point de passage que pour éviter la route, et une boucle seulement là où elle en vaut la peine.

1. `python -m http.server 8080` à la racine, puis ouvrir http://localhost:8080/scripts/revue-gpx.html
2. « Aller-retour pour toutes » propose l'aller-retour parking → spot pour chaque rando pas encore
   retouchée et les calcule une à une. La liste marque « à revoir » :
   - plus de 20 % de route : ajouter un point de passage sur le sentier (clic sur la carte) ;
   - moins de 1 km jusqu'au spot : ce n'est pas une rando, choisir un parking plus bas ;
   - plus de 1,5 fois la distance de la fiche : vérifier le parking ;
   - spot ou point clé à plus de 150 m de la trace.
   Gestes : clic pour ajouter un point, glisser pour le déplacer, clic droit pour le supprimer, Ctrl+Z.
   « Aller-retour vers le spot » repart de l'aller-retour pour la rando affichée.
3. Une trace de référence (GPX ou KML d'un randonneur, Wikiloc par exemple) glissée sur la carte aide
   à placer les points : « Placer les points depuis la référence », « Voir le plus gros écart ».
   Elle reste dans ton navigateur et n'est jamais exportée ; la trace publiée reste calculée sur OpenStreetMap.
4. « Valider cette trace », puis « Exporter les corrections » télécharge `corrections-gpx.json`.
5. `python scripts/gpx_route.py --apply corrections-gpx.json` reporte les points corrigés dans
   `hikes.json`, régénère les traces concernées, branche celles qui sont validées, et leur fiche
   reprend les chiffres de la trace : `km`, `dplus`, `dur`, `toSpot`, `down` (temps à l'allure hiver
   des fiches, × 1,25, arrondis à 5 min). Ce sont les chiffres que l'app affiche avec la trace.

Pour une trace perso, utilise plutôt le bouton « Importer un GPX » de la fiche : elle reste sur ton téléphone.
