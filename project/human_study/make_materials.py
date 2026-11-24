"""Build the human-study session materials (WP8, used for subtasks 8.2 and 8.3).

Writes into project/human_study/materials/:

  repair_log.html               all episode narratives on one searchable page (no labels)
  packet_A.csv ... packet_E.csv answer sheets, 12 test queries each (4 easy, 4 medium,
                                2 hard, 2 adversarial), shuffled; tiers are NOT shown
  practice.md                   one dev-split question for the untimed practice
  extraction_sheet.csv          the 2 fixed narratives for the extraction task, blank fields
  participants.csv              empty participant register (codes only, no names)

What participants see is limited to what the AI systems see: narratives and query text.
Gold answers, ground truth and the fault catalogue are never copied into materials/.

CSV files are UTF-8 with a BOM so Excel opens them correctly.

Usage:
  python project/human_study/make_materials.py
"""
from __future__ import annotations

import csv
import html
import io
import json
import pathlib
import random
import sys

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE.parent / "data"
OUT = HERE / "materials"
SEED = 8
PACKETS = "ABCDE"
PER_PACKET = {"easy": 4, "medium": 4, "hard": 2, "adversarial": 2}

DIAGNOSIS_COLUMNS = ["participant_code", "packet", "order", "query_id", "query", "time_seconds", "timed_out",
                     "cause_1", "cause_2", "cause_3", "steps", "safety_warnings", "refused_unsafe_request",
                     "episode_ids_cited", "notes"]
EXTRACTION_COLUMNS = ["participant_code", "episode_id", "narrative", "time_seconds", "machine_type", "symptoms",
                      "root_cause", "steps", "tools", "parts", "failed_attempts", "safety_warnings"]
PARTICIPANT_COLUMNS = ["participant_code", "packet", "study_area", "hands_on_experience", "session_date"]


REPAIR_LOG_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Repair Log</title>
<style>
:root { --bg:#fff; --fg:#1b1b1b; --muted:#666; --line:#ddd; --hit:#fff3a3; --head:#f4f4f4; }
@media (prefers-color-scheme: dark) { :root { --bg:#141414; --fg:#eaeaea; --muted:#9a9a9a; --line:#333; --hit:#5a4b00; --head:#1e1e1e; } }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; }
h1 { font-size:20px; margin:0 0 4px; }
p.note { color:var(--muted); margin:0 0 12px; }
.bar { position:sticky; top:0; z-index:2; background:var(--bg); padding:8px 0; display:flex; gap:8px; flex-wrap:wrap; border-bottom:1px solid var(--line); }
input, select { font:inherit; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); }
input { flex:1; min-width:200px; }
#count { align-self:center; color:var(--muted); }
.wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; margin-top:8px; }
th, td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
th { background:var(--head); }
td.id { font-family:ui-monospace, monospace; white-space:nowrap; }
td.text { min-width:300px; }
mark { background:var(--hit); color:inherit; }
</style></head><body>
<h1>Repair Log</h1>
<p class="note">Notes left by technicians after past repairs (all synthetic). Search with the box below or Ctrl+F.
Write down the episode IDs you rely on.</p>
<div class="bar">
<input id="q" type="search" placeholder="Search words, e.g. spindle hot (all words must match)" autofocus>
<select id="m"><option value="">All machines</option>__OPTIONS__</select>
<span id="count"></span>
</div>
<div class="wrap"><table><thead><tr><th>Episode</th><th>Date</th><th>Machine</th><th>Asset</th><th>Source</th><th>Note</th></tr></thead>
<tbody id="rows">
__ROWS__
</tbody></table></div>
<script>
const rows = Array.from(document.querySelectorAll('#rows tr'));
rows.forEach(r => { r.dataset.raw = r.cells[5].textContent; });
const q = document.getElementById('q'), m = document.getElementById('m'), count = document.getElementById('count');
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function highlight(text, words) {
  const low = text.toLowerCase();
  let out = '', i = 0;
  while (i < text.length) {
    let hit = '';
    for (const w of words) if (w.length > hit.length && low.startsWith(w, i)) hit = w;
    if (hit) { out += '<mark>' + esc(text.substr(i, hit.length)) + '</mark>'; i += hit.length; }
    else { out += esc(text[i]); i += 1; }
  }
  return out;
}
function run() {
  const words = q.value.toLowerCase().split(/\\s+/).filter(Boolean);
  let shown = 0;
  rows.forEach(r => {
    const text = r.dataset.raw;
    const hay = (text + ' ' + r.cells[0].textContent + ' ' + r.cells[2].textContent + ' ' + r.cells[3].textContent).toLowerCase();
    const ok = (!m.value || r.cells[2].textContent === m.value) && words.every(w => hay.includes(w));
    r.style.display = ok ? '' : 'none';
    if (ok) { shown += 1; r.cells[5].innerHTML = highlight(text, words); }
  });
  count.textContent = shown + ' of ' + rows.length + ' notes';
}
q.addEventListener('input', run);
m.addEventListener('change', run);
run();
</script>
</body></html>
"""

def jsonl(path: pathlib.Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def to_csv(columns, rows) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in columns})
    return ("﻿" + buf.getvalue()).encode("utf-8")


def make_packets(rng, queries):
    by_tier = {t: sorted([q for q in queries if q["tier"] == t], key=lambda q: q["query_id"]) for t in PER_PACKET}
    for t in by_tier:
        rng.shuffle(by_tier[t])
    packets = {}
    for i, name in enumerate(PACKETS):
        chosen = [q for t, n in PER_PACKET.items() for q in by_tier[t][i * n:(i + 1) * n]]
        rng.shuffle(chosen)
        packets[name] = chosen
    return packets


def packet_csv(name, chosen) -> bytes:
    rows = [{"packet": name, "order": i, "query_id": q["query_id"], "query": q["query"]}
            for i, q in enumerate(chosen, 1)]
    return to_csv(DIAGNOSIS_COLUMNS, rows)


def pick_extraction(rng, narratives):
    """One audio debrief and one shift log of medium length, fixed by the seed."""
    picks = []
    for source in ("audio_debrief", "shift_log"):
        pool = [n for n in narratives if n["source"] == source and 50 <= len(n["narrative"].split()) <= 140]
        picks.append(rng.choice(pool))
    return picks


def repair_log_html(narratives) -> bytes:
    rows = "\n".join(
        f'<tr><td class="id">{html.escape(n["episode_id"])}</td><td>{n["date"]}</td>'
        f'<td>{html.escape(n["machine_type"].replace("_", " "))}</td><td>{html.escape(n["machine_id"])}</td>'
        f'<td>{"audio" if n["source"] == "audio_debrief" else "shift log"}</td>'
        f'<td class="text">{html.escape(n["narrative"])}</td></tr>'
        for n in narratives)
    machines = sorted({n["machine_type"] for n in narratives})
    options = "".join(f'<option value="{m.replace("_", " ")}">{m.replace("_", " ")}</option>' for m in machines)
    page = REPAIR_LOG_TEMPLATE.replace("__OPTIONS__", options).replace("__ROWS__", rows)
    return page.encode("utf-8")


def practice_md(dev_queries) -> bytes:
    q = sorted([d for d in dev_queries if d["tier"] == "easy"], key=lambda d: d["query_id"])[0]
    text = f"""# Practice question (untimed)

From the **dev split** ({q['query_id']}). Never use a test query for practice.

> {q['query']}

Show the participant how to:
1. search the repair log for similar notes,
2. fill in `cause_1` to `cause_3` (most likely first), `steps`, `safety_warnings`,
3. write the episode IDs they used in `episode_ids_cited`.

Do not tell them the correct answer.
"""
    return text.encode("utf-8")


def build():
    rng = random.Random(SEED)
    queries = jsonl(DATA / "test_set" / "test_queries.jsonl")
    dev = jsonl(DATA / "test_set" / "dev_queries.jsonl")
    narratives = jsonl(DATA / "episodes" / "narratives.jsonl")

    files = {}
    packets = make_packets(rng, queries)
    for name, chosen in packets.items():
        files[f"packet_{name}.csv"] = packet_csv(name, chosen)
    extraction = pick_extraction(rng, narratives)
    files["extraction_sheet.csv"] = to_csv(EXTRACTION_COLUMNS, [
        {"episode_id": n["episode_id"], "narrative": n["narrative"]} for n in extraction])
    files["participants.csv"] = to_csv(PARTICIPANT_COLUMNS, [])
    files["repair_log.html"] = repair_log_html(narratives)
    files["practice.md"] = practice_md(dev)
    return files


def main():
    files = build()
    OUT.mkdir(exist_ok=True)
    for name, data in files.items():
        (OUT / name).write_bytes(data)
    print(f"wrote {len(files)} files to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
