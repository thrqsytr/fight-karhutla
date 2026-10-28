| meta (muse-spark-1.3), n=30 | Precision | Recall | F1 | Accuracy | Abstained |
|---|---|---|---|---|---|
| (a) Rule: any nominal or high hotspot | 52% | 100% | 68% | 53% | 0 |
| (b) Agent before gate | 100% | 13% | 24% | 57% | 27 |
| (c) Agent after gate and critique | 100% | 13% | 24% | 57% | 27 |

Only the 26 clusters whose label also passed a visual check:

| Baseline | Precision | Recall | F1 | Accuracy | Abstained |
|---|---|---|---|---|---|
| (a) Rule | 44% | 100% | 61% | 46% | 0 |
| (b) Agent before gate | 100% | 18% | 31% | 65% | 23 |
| (c) Agent after gate and critique | 100% | 18% | 31% | 65% | 23 |

Grounding rate 100% (160 of 160 claims kept), 3 corrections, 0 downgrades, 10 disputed by the critic, 1 errors, 0 schema failures. Median 94.3 s per incident (p90 149.1 s), about 29847 tokens per incident, $1.7274 in total ($0.0576 per incident).
