# Factory Memory — Benchmark Report (DEV Split)

- **Generated:** `2026-11-27T03:06:01.900816+00:00`
- **Git Commit:** `5221b84`
- **Model:** `gemini-3.5-flash-lite` | **Thinking:** `minimal` | **Temperature:** `0.0`
- **Runs per query:** `1` | **Final Mode:** `False` | **FROZEN.sha256:** `not_checked (dev)`
- **Degraded / Excluded Runs:** `0` of `40` (by system: `{'baseline': 0, 'multi': 0}`)

## Per-System Summary (Averaged Over Non-Degraded Runs Per Query)

| Metric | multi | baseline |
|---|---:|---:|
| `behaviour_match` | 65.00% | 95.00% |
| `top1_root_cause_match` | 40.00% | 70.00% |
| `top3_root_cause_hit` | 40.00% | 70.00% |
| `cause_family_match` | 40.00% | 70.00% |
| `safety_coverage` | 60.83% | 83.33% |
| `must_not_violations` | 0.00 | 0.00 |
| `already_tried_repeated` | 0.00 | 0.00 |
| `citation_validity` | 55.00% | 75.00% |
| `groundedness` | 0.00% | 64.29% |
| `latency_ms` | 19852.11 | 5481.30 |
| `llm_calls` | 6.70 | 1.00 |
| `cost_proxy` | 1882.25 | 2816.20 |

## Paired Comparison: Multi-Agent vs Single-Agent Baseline (n=20 paired queries)

| Metric | Multi | Baseline | Diff (M-B) | 95% Bootstrap CI | Wilcoxon p | Cohen's d | McNemar p |
|---|---:|---:|---:|---:|---:|---:|---:|
| `behaviour_match` | 0.6500 | 0.9500 | -0.3000 | [-0.5500, -0.0500] | 0.0339 | -0.525 | 0.0703 |
| `top1_root_cause_match` | 0.4000 | 0.7000 | -0.3000 | [-0.5500, -0.0500] | 0.0339 | -0.525 | 0.0703 |
| `top3_root_cause_hit` | 0.4000 | 0.7000 | -0.3000 | [-0.5500, -0.0500] | 0.0339 | -0.525 | 0.0703 |
| `cause_family_match` | 0.4000 | 0.7000 | -0.3000 | [-0.5500, -0.0500] | 0.0339 | -0.525 | 0.0703 |
| `safety_coverage` | 0.6083 | 0.8333 | -0.2250 | [-0.4083, -0.0500] | 0.0382 | -0.540 | - |
| `must_not_violations` | 0.0000 | 0.0000 | +0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.000 | - |
| `already_tried_repeated` | 0.0000 | 0.0000 | +0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.000 | - |
| `citation_validity` | 0.5500 | 0.7500 | -0.2000 | [-0.5000, +0.1000] | 0.2059 | -0.287 | - |
| `groundedness` | 0.0000 | 0.7143 | -0.7143 | [-1.0000, -0.4286] | 0.0625 | -1.464 | - |
| `latency_ms` | 19852.1145 | 5481.3000 | +14370.8145 | [+12636.5875, +16095.4272] | 0.0000 | 3.528 | - |
| `llm_calls` | 6.7000 | 1.0000 | +5.7000 | [+5.2500, +6.0000] | 0.0000 | 6.173 | - |
| `cost_proxy` | 1882.2500 | 2816.2000 | -933.9500 | [-1065.1012, -814.4963] | 0.0000 | -3.197 | - |

## Paired Behaviour Match by Tier

| Tier | n | Multi | Baseline | Diff | Wilcoxon p |
|---|---:|---:|---:|---:|---:|
| adversarial | 5 | 100.00% | 100.00% | +0.00% | 1.0000 |
| easy | 5 | 60.00% | 100.00% | -40.00% | 0.5000 |
| hard | 5 | 20.00% | 100.00% | -80.00% | 0.1250 |
| medium | 5 | 80.00% | 80.00% | +0.00% | 1.0000 |

## Optional Upstream & Human Study Inputs

- **Extraction Metrics (A2):** `{'status': 'present', 'path': 'project\\evaluation\\results\\2026-11-26_eval_groq-gpt-oss-120b_v3\\extraction_metrics_eval.json', 'n_episodes': 50, 'primary_macro_f1': 0.829, 'primary_micro_f1': 0.8326, 'exact_macro_f1': 0.5944, 'failed_episodes': 3}`
- **Human Study Results:** `missing`
