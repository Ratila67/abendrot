# Déclencheur planifié Cloudflare

Le planificateur de GitHub Actions prend jusqu'à 6 h de retard sur ce dépôt : le 8 novembre 2026, les prévisions
de 10 h 30 UTC ont tourné à 16 h 44 et aucune capture du soir n'a eu lieu. Le Worker `worker/cron.js` lance donc
les workflows à l'heure, par l'API GitHub (`workflow_dispatch`). Les déclencheurs Cron de Cloudflare sont gratuits
et partent à la minute près.

| Cron (UTC) | Workflow | Rôle |
|---|---|---|
| `30 10 * * *` | truth-snapshot.yml | prévisions du jour (le cron GitHub reste en secours, un passage en double ne réécrit rien) |
| `0 11 * * *` | sunset-alert.yml | alerte Telegram (plus de cron GitHub : elle partirait deux fois) |
| `0 13-19 * * *` | truth-capture.yml | le passage qui tombe 90 min ou moins avant la fenêtre lance la boucle du soir, les autres repartent en 20 s |

## 1. Jeton GitHub (5 min)

1. GitHub → photo de profil → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. Nom : `abendrot-cron`. Expiration : la plus longue proposée (note la date pour le renouveler).
3. **Repository access** : *Only select repositories* → `Ratila67/abendrot`.
4. **Permissions** → **Repository permissions** → **Actions** : *Read and write*. Rien d'autre (*Metadata* passe en lecture tout seul).
5. **Generate token**, puis copie-le : GitHub ne le montre qu'une fois.

Ce jeton peut seulement lancer et lire les workflows de ce dépôt : ni le code, ni les secrets.

## 2. Worker Cloudflare (5 min)

1. Tableau de bord Cloudflare → **Workers & Pages** → **Create** → **Create Worker** (modèle « Hello World »).
   Nom : `abendrot-cron` → **Deploy**.
2. **Edit code** : remplace tout le contenu par celui de `worker/cron.js` → **Deploy**.
3. **Settings** → **Variables and Secrets** → **Add** : type *Secret*, nom `GITHUB_TOKEN`, valeur = le jeton → **Deploy**.
4. **Settings** → **Triggers** → **Cron Triggers** → **Add**, trois fois : `30 10 * * *`, `0 11 * * *`, `0 13-19 * * *`.
   Cloudflare compte ces heures en UTC, sans heure d'été : c'est voulu, les workflows calculent eux-mêmes les heures locales.

## 3. Vérifier

- Dans l'éditeur du Worker, onglet **Schedule** (ou *Trigger scheduled event*), choisis `0 13-19 * * *` puis lance-le :
  la page Actions du dépôt doit montrer un run « Vérité terrain, captures » lancé par *workflow_dispatch*.
  Hors de la fenêtre du soir, il repart en une vingtaine de secondes : c'est normal.
- Une erreur `GitHub 401` ou `403` dans les journaux du Worker (**Logs**) veut dire que le jeton est absent, expiré
  ou sans la permission *Actions : Read and write*.
