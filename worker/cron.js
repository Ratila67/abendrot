// Worker Cloudflare planifié : lance les workflows GitHub à l'heure.
// Le planificateur de GitHub prend jusqu'à 6 h de retard sur ce dépôt (8/11 : prévisions de 10 h 30 lancées à 16 h 44,
// aucune capture du soir) ; les déclencheurs Cron de Cloudflare partent à la minute près.
//
// Mise en place (tableau de bord Cloudflare, sans outil à installer) : voir docs/declencheur-cloudflare.md.
//   Secret à créer dans le Worker : GITHUB_TOKEN (jeton GitHub à portée fine : ce dépôt seul, Actions en écriture).
//   Déclencheurs Cron à ajouter, en UTC : exactement les clés de CRONS ci-dessous.

export const REPO = 'Ratila67/abendrot';

/** Expression Cron (UTC) → workflow à lancer. */
export const CRONS = {
  '30 10 * * *': 'truth-snapshot.yml',   // prévisions du jour, archivées avant le coucher
  '0 11 * * *': 'sunset-alert.yml',      // alerte Telegram : 12 h en hiver, 13 h en été
  '0 13-19 * * *': 'truth-capture.yml'   // captures : le passage qui tombe 90 min ou moins avant la fenêtre lance la boucle du soir
};

export async function dispatch(env, workflow, fetchImpl = fetch) {
  const r = await fetchImpl(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'abendrot-cron'
    },
    body: JSON.stringify({ ref: 'main' })
  });
  if (r.status !== 204) throw new Error(`GitHub ${r.status} pour ${workflow} : ${(await r.text()).slice(0, 200)}`);
  return workflow;
}

export default {
  async scheduled(event, env, ctx) {
    const wf = CRONS[event.cron];
    if (!wf) { console.warn(`Cron inconnu : ${event.cron}`); return; }
    ctx.waitUntil(dispatch(env, wf).then(w => console.log(`lancé : ${w}`)));
  },
  // Pas de déclenchement par URL : seule la planification lance les workflows.
  async fetch() { return new Response('Abendrot : déclencheur planifié des workflows GitHub.', { status: 200 }); }
};
