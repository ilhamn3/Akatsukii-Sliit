# Expanded stores (scalability tests)

**All data is synthetic.** Built by `python project/data/expand_stores.py`; check with `--check`.

| Store | Episodes | Faults | Contents | `episodes.jsonl` size |
|---|---:|---:|---|---:|
| `n100/` | 100 | 40 | Seeded subset of the base 400 that keeps every fault | 0.26 MB |
| `n400/` | 400 | 40 | The base episodes (`project/data/episodes/`), which the test set cites | 1.06 MB |
| `n2000/` | 2,000 | 40 | Base 400 plus 1,600 extra episodes (seed 4242), EP-0401 to EP-2000 | 5.29 MB |

The stores are nested (n100 ⊂ n400 ⊂ n2000), so a change in results across sizes comes
from store size, not from different data.

Each folder holds:
- `episodes.jsonl`: one `VerifiedEpisode` envelope per line, ready for
  `KnowledgeStore.put_verified(...)`. All 2,500 envelopes validate against the store's model.
- `labels.jsonl`: `episode_id` to `fault_id`. Scoring only; don't load it into the store.
- `manifest.json`: counts per machine and file hashes.

`verified_by` is `synthetic-generator (not human-reviewed)`. The records come from generator
ground truth, so they are correct by construction, but no person reviewed them.
`provenance.source_path` is a `synthetic://` placeholder, and `source_sha256` is the hash
of the narrative text.

Loading example (once the knowledge store from PR #12 is merged):

```python
import json
from project.store.knowledge_store import KnowledgeStore
from project.store.models import VerifiedEpisode

store = KnowledgeStore()
with open("project/data/expanded/n400/episodes.jsonl", encoding="utf-8") as f:
    for line in f:
        store.put_verified(VerifiedEpisode.model_validate_json(line), trace_id="TR-LOAD-N400")
```
