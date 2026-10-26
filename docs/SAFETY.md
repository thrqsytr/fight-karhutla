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
- An incident built only from citizen reports appears on the public map only after a verifier approves it. When a report joins a satellite hotspot incident, that incident stays public, but the citizen text, photos, and voice inside it stay private.
- Phone numbers and verifier identities are stored as hashes.
- Photo EXIF is stripped on intake: every photo is decoded and re-encoded as JPEG without metadata, so GPS and device tags never reach storage. A test proves the output carries no EXIF.
- Voice notes are stored as recorded. Browser recordings carry no location, but the server does not rewrite audio files.
- The report form needs no account and no phone number. A simple per-client limit (6 reports per hour) is kept in memory with a hashed client address; the raw IP is not stored.
- Citizen text, photos, and voice can be deleted per incident with `DELETE /api/incidents/{id}/citizen-data` (verifier only).
- Retention: report data is deleted after the demo period ends (planned date to be set before submission).
