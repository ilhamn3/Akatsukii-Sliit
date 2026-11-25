# Synthetic repair episodes

**All data here is synthetic.** It is generated from `project/data/fault_catalogue/` by
`project/data/generator.py`. No real technicians, machines or incidents are described.

Regenerate (same seed gives byte-identical files):

```
python project/data/generator.py            # seed 42, 400 episodes
python project/data/generator.py --check    # confirm the files here match a fresh run
```

| File | What it holds | Who uses it |
|---|---|---|
| `narratives.jsonl` | One line per episode: id, date, technician, machine, source, and the narrative text. **No labels.** | Input to Agents 1-2 |
| `ground_truth.jsonl` | Same episode ids: `fault_id`, hazard ids, the full `RepairRecord`, and `narrative_coverage` (which symptoms, steps and safety step the narrative actually mentioned) | Scoring only. Never give it to an agent |
| `samples.json` | First episode from each of the 12 technicians, narrative and ground truth together | Quick hand-off for development |
| `technicians.json` | The 12 simulated technician profiles (experience, filler, jargon, omission and safety-mention rates) | Reference |
| `manifest.json` | Seed, generator version, catalogue hash, counts and SHA-256 of every file | Reproducibility check |

Two sources: `audio_debrief` (spoken style with fillers, jargon, self-corrections and
step timestamps) and `shift_log` (terse written notes, no timestamps). Some narratives
leave out steps, and some leave out the safety step on purpose, so the Safety agent
has something to catch. `narrative_coverage.steps_omitted` lists them.

The narratives come from seeded templates, not an LLM, so they can be reproduced
exactly and need no API key.
