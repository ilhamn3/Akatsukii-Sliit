# Factory Memory — Benchmark Report (DEV Split)

- **Generated:** `2026-11-27T08:16:28.294921+00:00`
- **Git Commit:** `2a465d4`
- **Model:** `gemini-3.5-flash-lite` | **Thinking:** `minimal` | **Temperature:** `0.0`
- **Runs per query:** `1` | **Final Mode:** `False` | **FROZEN.sha256:** `not_checked (dev)`
- **Degraded / Excluded Runs:** `0` of `40` (by system: `{'baseline': 0, 'multi': 0}`)

## Per-System Summary (Averaged Over Non-Degraded Runs Per Query)

| Metric | multi | baseline |
|---|---:|---:|
| `behaviour_match` | 90.00% | 100.00% |
| `top1_root_cause_match` | 65.00% | 75.00% |
| `top3_root_cause_hit` | 65.00% | 75.00% |
| `cause_family_match` | 65.00% | 75.00% |
| `safety_coverage` | 51.67% | 88.33% |
| `must_not_violations` | 0.00 | 0.00 |
| `already_tried_repeated` | 0.00 | 0.00 |
| `citation_validity` | 100.00% | 55.00% |
| `groundedness` | 100.00% | 40.00% |
| `latency_ms` | 24338.54 | 5382.33 |
| `llm_calls` | 5.35 | 1.00 |
| `cost_proxy` | 1699.35 | 2834.30 |

## Paired Comparison: Multi-Agent vs Single-Agent Baseline (n=20 paired queries)

| Metric | Multi | Baseline | Diff (M-B) | 95% Bootstrap CI | Wilcoxon p | Cohen's d | McNemar p |
|---|---:|---:|---:|---:|---:|---:|---:|
| `behaviour_match` | 0.9000 | 1.0000 | -0.1000 | [-0.2500, +0.0000] | 0.1573 | -0.325 | 0.5000 |
| `top1_root_cause_match` | 0.6500 | 0.7500 | -0.1000 | [-0.2500, +0.0000] | 0.1573 | -0.325 | 0.5000 |
| `top3_root_cause_hit` | 0.6500 | 0.7500 | -0.1000 | [-0.2500, +0.0000] | 0.1573 | -0.325 | 0.5000 |
| `cause_family_match` | 0.6500 | 0.7500 | -0.1000 | [-0.2500, +0.0000] | 0.1573 | -0.325 | 0.5000 |
| `safety_coverage` | 0.5167 | 0.8833 | -0.3667 | [-0.5917, -0.1417] | 0.0133 | -0.708 | - |
| `must_not_violations` | 0.0000 | 0.0000 | +0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.000 | - |
| `already_tried_repeated` | 0.0000 | 0.0000 | +0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.000 | - |
| `citation_validity` | 1.0000 | 0.5500 | +0.4500 | [+0.2500, +0.6500] | 0.0027 | 0.882 | - |
| `groundedness` | 1.0000 | 0.3846 | +0.6154 | [+0.3077, +0.8462] | 0.0078 | 1.215 | - |
| `latency_ms` | 24338.5445 | 5382.3275 | +18956.2170 | [+13004.8098, +26083.1181] | 0.0000 | 1.260 | - |
| `llm_calls` | 5.3500 | 1.0000 | +4.3500 | [+3.7500, +4.9500] | 0.0001 | 2.841 | - |
| `cost_proxy` | 1699.3500 | 2834.3000 | -1134.9500 | [-1314.3012, -949.2475] | 0.0000 | -2.638 | - |

## Paired Behaviour Match by Tier

| Tier | n | Multi | Baseline | Diff | Wilcoxon p |
|---|---:|---:|---:|---:|---:|
| adversarial | 5 | 100.00% | 100.00% | +0.00% | 1.0000 |
| easy | 5 | 100.00% | 100.00% | +0.00% | 1.0000 |
| hard | 5 | 80.00% | 100.00% | -20.00% | 1.0000 |
| medium | 5 | 80.00% | 100.00% | -20.00% | 1.0000 |

## Optional Upstream & Human Study Inputs

- **Extraction Metrics (A2):** `{'status': 'present', 'path': 'project\\evaluation\\results\\2026-11-26_eval_groq-gpt-oss-120b_v3\\extraction_metrics_eval.json', 'n_episodes': 50, 'primary_macro_f1': 0.829, 'primary_micro_f1': 0.8326, 'exact_macro_f1': 0.5944, 'failed_episodes': 3}`
- **Human Study Results:** `missing`
