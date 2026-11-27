# Factory Memory — Benchmark Report (DEV Split)

- **Generated:** `2026-11-27T09:16:14.274945+00:00`
- **Git Commit:** `6722858`
- **Model:** `gemini-3.5-flash-lite` | **Thinking:** `minimal` | **Temperature:** `0.0`
- **Runs per query:** `1` | **Final Mode:** `False` | **FROZEN.sha256:** `not_checked (dev)`
- **Degraded / Excluded Runs:** `0` of `40` (by system: `{'baseline': 0, 'multi': 0}`)

## Per-System Summary (Averaged Over Non-Degraded Runs Per Query)

| Metric | multi | baseline |
|---|---:|---:|
| `behaviour_match` | 100.00% | 95.00% |
| `top1_root_cause_match` | 75.00% | 70.00% |
| `top3_root_cause_hit` | 75.00% | 70.00% |
| `cause_family_match` | 75.00% | 70.00% |
| `safety_coverage` | 100.00% | 86.67% |
| `must_not_violations` | 0.00 | 0.00 |
| `already_tried_repeated` | 0.00 | 0.00 |
| `citation_validity` | 100.00% | 70.00% |
| `groundedness` | 100.00% | 57.14% |
| `latency_ms` | 17383.37 | 3720.02 |
| `llm_calls` | 4.85 | 1.00 |
| `cost_proxy` | 1604.80 | 2794.40 |

## Paired Comparison: Multi-Agent vs Single-Agent Baseline (n=20 paired queries)

| Metric | Multi | Baseline | Diff (M-B) | 95% Bootstrap CI | Wilcoxon p | Cohen's d | McNemar p |
|---|---:|---:|---:|---:|---:|---:|---:|
| `behaviour_match` | 1.0000 | 0.9500 | +0.0500 | [+0.0000, +0.1500] | 0.3173 | 0.224 | 1.0000 |
| `top1_root_cause_match` | 0.7500 | 0.7000 | +0.0500 | [+0.0000, +0.1500] | 0.3173 | 0.224 | 1.0000 |
| `top3_root_cause_hit` | 0.7500 | 0.7000 | +0.0500 | [+0.0000, +0.1500] | 0.3173 | 0.224 | 1.0000 |
| `cause_family_match` | 0.7500 | 0.7000 | +0.0500 | [+0.0000, +0.1500] | 0.3173 | 0.224 | 1.0000 |
| `safety_coverage` | 1.0000 | 0.8667 | +0.1333 | [+0.0333, +0.2583] | 0.0412 | 0.497 | - |
| `must_not_violations` | 0.0000 | 0.0000 | +0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.000 | - |
| `already_tried_repeated` | 0.0000 | 0.0000 | +0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.000 | - |
| `citation_validity` | 1.0000 | 0.7000 | +0.3000 | [+0.1000, +0.5000] | 0.0143 | 0.638 | - |
| `groundedness` | 1.0000 | 0.5714 | +0.4286 | [+0.2143, +0.7143] | 0.0143 | 0.835 | - |
| `latency_ms` | 17383.3695 | 3720.0245 | +13663.3450 | [+9681.3991, +18431.8884] | 0.0000 | 1.305 | - |
| `llm_calls` | 4.8500 | 1.0000 | +3.8500 | [+3.3000, +4.4500] | 0.0000 | 2.855 | - |
| `cost_proxy` | 1604.8000 | 2794.4000 | -1189.6000 | [-1355.1025, -1011.5000] | 0.0001 | -2.949 | - |

## Paired Behaviour Match by Tier

| Tier | n | Multi | Baseline | Diff | Wilcoxon p |
|---|---:|---:|---:|---:|---:|
| adversarial | 5 | 100.00% | 100.00% | +0.00% | 1.0000 |
| easy | 5 | 100.00% | 100.00% | +0.00% | 1.0000 |
| hard | 5 | 100.00% | 100.00% | +0.00% | 1.0000 |
| medium | 5 | 100.00% | 80.00% | +20.00% | 1.0000 |

## Optional Upstream & Human Study Inputs

- **Extraction Metrics (A2):** `{'status': 'present', 'path': 'project\\evaluation\\results\\2026-11-26_eval_groq-gpt-oss-120b_v3\\extraction_metrics_eval.json', 'n_episodes': 50, 'primary_macro_f1': 0.829, 'primary_micro_f1': 0.8326, 'exact_macro_f1': 0.5944, 'failed_episodes': 3}`
- **Human Study Results:** `missing`
