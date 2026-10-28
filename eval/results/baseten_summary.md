| baseten (meta-models/muse-spark-open-1026), n=30 | Precision | Recall | F1 | Accuracy | Abstained |
|---|---|---|---|---|---|
| (a) Rule: any nominal or high hotspot | 52% | 100% | 68% | 53% | 0 |
| (b) Agent before gate | 0% | 0% | n/a | 47% | 27 |
| (c) Agent after gate and critique | n/a | 0% | n/a | 50% | 28 |

Only the 26 clusters whose label also passed a visual check:

| Baseline | Precision | Recall | F1 | Accuracy | Abstained |
|---|---|---|---|---|---|
| (a) Rule | 44% | 100% | 61% | 46% | 0 |
| (b) Agent before gate | 0% | 0% | n/a | 54% | 23 |
| (c) Agent after gate and critique | n/a | 0% | n/a | 58% | 24 |

Grounding rate 100% (172 of 172 claims kept), 26 corrections, 0 downgrades, 7 disputed by the critic, 2 errors, 5 schema failures. Median 76.2 s per incident (p90 131.8 s), about 34194 tokens per incident, cost not metered (Baseten credit).
