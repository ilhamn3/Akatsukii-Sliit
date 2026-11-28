# Retrieval Scalability (`n100`, `n400`, `n2000`)

Measured on `dev` (20 dev queries × 3 repeats, `HybridRetriever` only, no LLM calls, `machine_type=None`).

| Store | Episodes | Build (s) | Latency p50 (ms) | Latency p95 (ms) | Latency max (ms) | Top-3 Gold Ep Hit | Top-3 Root-Cause Hit | Top-3 Cause-Family Hit | Disk (MB) | Vector RAM est. (MB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `n100` | 100 | 14.66 | 30.97 | 47.83 | 109.75 | 100.0% | 100.0% | 100.0% | 1.31 | 0.15 |
| `n400` | 400 | 12.39 | 34.37 | 56.90 | 118.49 | 100.0% | 100.0% | 100.0% | 5.19 | 0.59 |
| `n2000` | 2000 | 63.07 | 67.55 | 85.53 | 742.70 | 85.0% | 100.0% | 100.0% | 25.57 | 2.93 |

## Honest Assessment

1. **What scales:** Steady-state `HybridRetriever` query latency, SQLite storage, and in-memory float32 FAISS `IndexFlatIP` vector memory stay small and sub-second up to 2,000 episodes (and cause-family / root-cause retrieval hit rates hold steady or improve as more fault variants are indexed).
2. **What does not scale:** Store ingestion (`GraphAgent.put_verified` + per-episode MiniLM encoding and `FAISS` / `NetworkX` exact rebuilds on projection refresh) grows linearly with episode count, and exact linear-scan `IndexFlatIP` + full-graph traversal will eventually need batched writes and an approximate index (e.g., IVF/HNSW) well beyond 2,000–10,000 episodes.
3. **Synthetic data caveat:** All `n100`, `n400`, and `n2000` episodes and the 20 dev queries are synthetic (`project/data/expanded/`), where `n2000` adds duplicate-fault variants that outrank some original `n400` gold `EP-xxxx` IDs (dropping gold-ID hit rate from 100.0% to 85.0% while root-cause/family stays 100.0%), so real plant logs with noisier terminology and drift may see lower retrieval accuracy.
