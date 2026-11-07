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

1. `python -m http.server 8080` à la racine, puis ouvrir http://localhost:8080/scripts/revue-gpx.html
2. Pour chaque rando : comparer la trace à la fiche, corriger les points clés (clic pour ajouter,
   glisser pour déplacer, clic droit pour supprimer), puis « Valider cette trace ».
   Plus rapide : glisser sur la carte une trace de référence (GPX ou KML d'un randonneur, Wikiloc par
   exemple), puis « Placer les points depuis la référence ». La référence guide seulement : elle reste
   dans ton navigateur, n'est jamais exportée, et la trace publiée reste calculée sur OpenStreetMap.
   « Voir le plus gros écart » centre la carte là où la trace s'en éloigne.
3. « Exporter les corrections » télécharge `corrections-gpx.json`.
4. `python scripts/gpx_route.py --apply corrections-gpx.json` reporte les points corrigés dans
   `hikes.json`, régénère les traces concernées et branche celles qui sont validées.

Pour une trace perso, utilise plutôt le bouton « Importer un GPX » de la fiche : elle reste sur ton téléphone.
