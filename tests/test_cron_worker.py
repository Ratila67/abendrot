"""Worker Cloudflare planifié (worker/cron.js) : bon workflow par cron, appel GitHub, couverture des soirées de l'année."""
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from truth import capture
from truth.common import TZ, sunset_spots

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "worker" / "cron.js").read_text("utf-8")
CRONS = dict(re.findall(r"'([^']+)': '([^']+\.yml)'", SRC))


def test_chaque_cron_vise_un_workflow_lancable_a_la_main():
    assert set(CRONS.values()) == {"truth-snapshot.yml", "sunset-alert.yml", "truth-capture.yml"}
    for wf in CRONS.values():
        y = (ROOT / ".github" / "workflows" / wf).read_text("utf-8")
        assert "workflow_dispatch:" in y, wf
    alert = (ROOT / ".github" / "workflows" / "sunset-alert.yml").read_text("utf-8")
    assert "schedule:" not in alert   # sinon l'alerte Telegram partirait deux fois


def test_une_capture_part_avant_chaque_soiree_de_l_annee():
    m = re.fullmatch(r"0 (\d+)-(\d+) \* \* \*", next(k for k, v in CRONS.items() if v == "truth-capture.yml"))
    hours = range(int(m[1]), int(m[2]) + 1)
    spots = sunset_spots()
    for k in range(366):
        day = dt.date(2027, 1, 1) + dt.timedelta(days=k)
        noon = dt.datetime.combine(day, dt.time(12), TZ)
        start, end = capture.evening_window(noon, spots)
        runs = [dt.datetime.combine(day, dt.time(h), dt.timezone.utc) for h in hours]
        ok = [r for r in runs if capture.wait_seconds(r, spots) is not None]
        assert ok, f"aucun déclenchement utile le {day} (fenêtre {start:%H:%M}-{end:%H:%M})"
        assert (end - ok[0]).total_seconds() / 60 <= 230, day   # sous le timeout du job (240 min)


@pytest.mark.skipif(not shutil.which("node"), reason="node absent")
def test_worker_lance_le_bon_workflow():
    js = f"""
      const calls = [];
      globalThis.fetch = async (url, init) => {{ calls.push({{ url, init }}); return new Response(null, {{ status: url.includes('sunset-alert') ? 403 : 204 }}); }};
      const w = (await import('{(ROOT / "worker" / "cron.js").as_uri()}')).default;
      const env = {{ GITHUB_TOKEN: 'jeton-de-test' }}, out = [];
      for (const cron of ['30 10 * * *', '0 13-19 * * *', '0 11 * * *', '5 5 * * *']) {{
        const waits = [];
        await w.scheduled({{ cron }}, env, {{ waitUntil: p => waits.push(p) }});
        out.push(await Promise.all(waits).then(() => 'ok', e => e.message));
      }}
      console.log(JSON.stringify({{ out, calls: calls.map(c => ({{ url: c.url, body: c.init.body, auth: c.init.headers.Authorization }})) }}));
    """
    r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
    got = json.loads(r.stdout.strip().splitlines()[-1])   # avant : les journaux du Worker (« lancé : … »)
    assert got["out"][:2] == ["ok", "ok"] and got["out"][2].startswith("GitHub 403") and got["out"][3] == "ok"
    assert [c["url"].rsplit("/", 2)[-2] for c in got["calls"]] == ["truth-snapshot.yml", "truth-capture.yml", "sunset-alert.yml"]
    assert all(c["body"] == '{"ref":"main"}' and c["auth"] == "Bearer jeton-de-test" for c in got["calls"])
    assert got["calls"][0]["url"] == "https://api.github.com/repos/Ratila67/abendrot/actions/workflows/truth-snapshot.yml/dispatches"
