# Safety, limits, and privacy

## What Fight Karhutla is not

- Not an official emergency channel. It does not replace BPBD, Manggala Agni, or any fire service.
- It sends no command to anyone. Every incident ends as `needs_human`; a verifier decides.
- It gives no health or medical advice about smoke.
- It does not attribute causes and does not name any party or company.

## What is proven, what is flagged, what is out of reach

| Kind | Examples |
| --- | --- |
| Proven by code | Distances, hotspot clusters, burn area from mask pixel counts, weather values copied from the tool result |
| Flagged, not proven | Model interpretation of photos, status suggestions, claims the critique pass disputes |
| Out of reach | Fires under thick cloud with no photo, ground truth without a human visit |

A satellite hotspot is a thermal anomaly, not a confirmed fire. A hotspot alone never yields `likely_fire`.

## Privacy

- Citizen photos and voice are visible to verifiers only.
- The public map rounds coordinates to three decimals (about 100 m).
- Citizen reports become public only after a verifier approves them.
- Phone numbers and verifier identities are stored as hashes.
- Photo EXIF is stripped on intake.
- Retention: report data is deleted after the demo period ends (planned date to be set before submission).
