"""Evidence gate. Deterministic code, not a model, decides what reaches a human verifier.

The gate never fails silently: every removal, correction, and downgrade is recorded in the result.
"""

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from . import heat_sources
from .geo import haversine_m, in_indonesia, pixel_area_ha
from .schema import STATUS_LADDER, TriageOutput

NUMBER_TOLERANCE = 0.01
LOCATION_RADIUS_M = 5000
INDEPENDENT_TYPES = {"image", "voice", "satellite"}
HEAT_SOURCE_REASON = "inside a persistent heat source zone approved by a verifier, likely_fire needs a citizen photo or voice note"
# Satellite evidence supports likely_fire only when it measured a burn: 25 pixels at 20 m. Below that, a dNBR count
# is within edge and misregistration noise. Found on 2026-10-28: a pair with 0.28 ha let the agent claim likely_fire.
MIN_SATELLITE_BURN_HA = 1.0
LOCATED_TYPES = {"hotspot", "image", "voice"}

# Content rules: no medical advice, no cause attribution, no naming parties.
BLOCKED_PATTERNS = {
    # "masker" alone is allowed: in Indonesian it also names a SAM segmentation mask.
    "medical": r"\b(pakai masker|gunakan masker|masker wajah|N95|obat|dokter|rumah sakit|puskesmas|ispa|asma|paru|kesehatan|"
    r"gejala|pernapasan|face mask|wear a mask|medicine|doctor|hospital|clinic|asthma|lung|respiratory|symptom|health)\b",
    "cause": r"\b(sengaja|dibakar oleh|pembakaran lahan oleh|pelaku|tersangka|penyebab|disebabkan|ulah|"
    r"arson|deliberately|set by|caused by|culprit|suspect|blame)\b",
    "party": r"\b(PT\.?\s+[A-Z]\w+|CV\.?\s+[A-Z]\w+|Tbk|perusahaan|korporasi|konsesi|company|corporation|concession|plantation owner)\b",
}
DASHES = re.compile("\\s*[\u2013\u2014\u2015]\\s*")

UNIT_FIELDS = {
    # unit regex -> (evidence type, payload path)
    r"m/s": ("weather", ("wind_speed_ms",)),
    r"mm": ("weather", ("rain_24h_mm",)),
}
NUM_UNIT = re.compile(r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>m/s|mm|km|%)")


@dataclass
class GateResult:
    accepted: bool
    output: dict | None
    claims_total: int = 0
    claims_passed: int = 0
    claims_dropped: list[dict] = field(default_factory=list)
    corrections: list[dict] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    downgrades: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)

    @property
    def grounding_rate(self) -> float | None:
        return None if self.claims_total == 0 else self.claims_passed / self.claims_total

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "claims_total": self.claims_total,
            "claims_passed": self.claims_passed,
            "claims_dropped": self.claims_dropped,
            "corrections": self.corrections,
            "violations": self.violations,
            "downgrades": self.downgrades,
            "schema_errors": self.schema_errors,
            "grounding_rate": self.grounding_rate,
        }


def _downgrade(out: dict, res: GateResult, reason: str) -> None:
    cur = out["status"]
    if cur in STATUS_LADDER and cur != STATUS_LADDER[-1]:
        out["status"] = STATUS_LADDER[STATUS_LADDER.index(cur) + 1]
        res.downgrades.append(f"{cur} -> {out['status']}: {reason}")


def _set_status(out: dict, res: GateResult, status: str, reason: str) -> None:
    if out["status"] != status:
        res.downgrades.append(f"{out['status']} -> {status}: {reason}")
        out["status"] = status


def _payload_value(payload: dict, path: tuple[str, ...]):
    v = payload
    for k in path:
        if not isinstance(v, dict) or k not in v:
            return None
        v = v[k]
    return v


def _distance_values_km(payload: dict) -> list[float]:
    near = payload.get("nearest") or {}
    return [n["distance_m"] / 1000 for n in near.values() if n and n.get("distance_m") is not None]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(abs(b) * NUMBER_TOLERANCE, 0.05)


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _check_numbers(text: str, cited: list[dict], res: GateResult, where: str) -> str:
    """Numbers with a unit must equal a value from a cited tool result; otherwise replace or flag."""

    def fix(m: re.Match) -> str:
        num = float(m.group("num").replace(",", "."))
        unit = m.group("unit")
        candidates: list[float] = []
        if unit in UNIT_FIELDS:
            ev_type, path = UNIT_FIELDS[unit]
            for ev in cited:
                if ev["type"] == ev_type:
                    v = _payload_value(ev["payload"], path)
                    if isinstance(v, (int, float)):
                        candidates.append(float(v))
        elif unit == "km":
            for ev in cited:
                if ev["type"] == "osm":
                    candidates.extend(_distance_values_km(ev["payload"]))
        elif unit == "%":
            for ev in cited:
                if ev["type"] == "weather":
                    v = _payload_value(ev["payload"], ("relative_humidity_pct",))
                    if isinstance(v, (int, float)):
                        candidates.append(float(v))
        if any(_close(num, c) for c in candidates):
            return m.group(0)
        if len(candidates) == 1:
            res.corrections.append({"where": where, "claimed": m.group(0), "corrected": f"{_fmt(candidates[0])} {unit}"})
            return f"{_fmt(candidates[0])} {unit}"
        res.corrections.append({"where": where, "claimed": m.group(0), "corrected": None, "note": "no matching tool value"})
        return "[angka tidak terverifikasi]"

    return NUM_UNIT.sub(fix, text)


def _content_filter(text: str, res: GateResult, where: str) -> str:
    """Drop whole sentences that break content rules, and replace dashes with commas."""
    text = DASHES.sub(", ", text)
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        hit = next((k for k, p in BLOCKED_PATTERNS.items() if re.search(p, sentence, re.IGNORECASE)), None)
        if hit:
            res.violations.append({"where": where, "rule": hit, "removed": sentence})
        else:
            kept.append(sentence)
    return " ".join(kept).strip()


def run(raw: dict, evidence: list[dict], incident_id: str) -> GateResult:
    """Check an agent output against the evidence stored for this incident.

    `evidence` is db.list_evidence(conn, incident_id): dicts with id, type, payload.
    A schema failure is not repaired here; the caller retries the agent once, then marks needs_more_info.
    """
    try:
        parsed = TriageOutput.model_validate(raw)
    except ValidationError as exc:
        return GateResult(accepted=False, output=None, schema_errors=[e["msg"] + " at " + ".".join(map(str, e["loc"])) for e in exc.errors()])

    out = parsed.model_dump()
    res = GateResult(accepted=True, output=out)
    by_id = {e["id"]: e for e in evidence}

    if out["incident_id"] != incident_id:
        res.corrections.append({"where": "incident_id", "claimed": out["incident_id"], "corrected": incident_id})
        out["incident_id"] = incident_id

    # Evidence list shown to humans comes from the database, not from the model.
    out["evidence"] = [
        {"id": e["id"], "type": e["type"], "source": e["source"], "retrieved_at": e["retrieved_at"], "summary": e["summary"]}
        for e in evidence
    ]

    # Claims: each cited ID must exist; numbers must match the cited tool results.
    res.claims_total = len(out["claims"])
    kept_claims, dropped_any = [], False
    for i, claim in enumerate(out["claims"]):
        missing = [eid for eid in claim["evidence_ids"] if eid not in by_id]
        if not claim["evidence_ids"] or missing:
            res.claims_dropped.append({"text": claim["text"], "reason": "no evidence" if not claim["evidence_ids"] else f"unknown evidence {missing}"})
            dropped_any = True
            continue
        cited = [by_id[eid] for eid in claim["evidence_ids"]]
        text = _check_numbers(claim["text"], cited, res, f"claims[{i}]")
        text = _content_filter(text, res, f"claims[{i}]")
        if not text:
            res.claims_dropped.append({"text": claim["text"], "reason": "content rule"})
            dropped_any = True
            continue
        # The Indonesian twin gets the same number and content checks; if nothing survives, the reader sees the English.
        text_id = claim.get("text_id")
        if text_id:
            text_id = _content_filter(_check_numbers(text_id, cited, res, f"claims[{i}].id"), res, f"claims[{i}].id") or None
        kept_claims.append({"text": text, "text_id": text_id, "evidence_ids": claim["evidence_ids"]})
    out["claims"] = kept_claims
    res.claims_passed = len(kept_claims)
    if dropped_any:
        _downgrade(out, res, "claims without valid evidence were removed")

    # A hotspot alone is not enough for likely_fire.
    def supports(e: dict) -> bool:
        if e["type"] != "satellite":
            return e["type"] in INDEPENDENT_TYPES
        p = e["payload"]
        return p.get("status") == "ok" and (p.get("burned_area_ha") or 0) >= MIN_SATELLITE_BURN_HA

    cited = [by_id[eid] for c in kept_claims for eid in c["evidence_ids"]]
    if out["status"] == "likely_fire" and not any(supports(e) for e in cited):
        reason = "likely_fire needs independent evidence (image, voice, or a satellite burn of at least " f"{MIN_SATELLITE_BURN_HA:g} ha)"
        _set_status(out, res, "possible_fire", reason)
    # Inside a persistent heat source zone a mine or a flare looks like a burn from orbit: only a citizen photo
    # or voice note from the ground can carry likely_fire there.
    if out["status"] == "likely_fire" and heat_sources.active_zone_evidence(evidence) and not any(e["type"] in {"image", "voice"} for e in cited):
        _set_status(out, res, "possible_fire", HEAT_SOURCE_REASON)

    # Burn area is computed by code from a real mask, never taken from the model.
    area = out["burn_area_ha"]
    ev = by_id.get(area["evidence_id"]) if area["evidence_id"] else None
    pc = _payload_value(ev["payload"], ("pixel_count",)) if ev else None
    ps = _payload_value(ev["payload"], ("pixel_size_m",)) if ev else None
    if area["method"] and ev and ev["type"] in {"image", "satellite"} and isinstance(pc, int) and isinstance(ps, (int, float)):
        computed = round(pixel_area_ha(pc, float(ps)), 2)
        if area["value"] is None or not _close(area["value"], computed):
            res.corrections.append({"where": "burn_area_ha.value", "claimed": area["value"], "corrected": computed})
        area["value"] = computed
        # The method follows from the evidence: satellite pairs are dNBR, photos and frames are SAM masks.
        method = "dnbr" if ev["type"] == "satellite" else "sam_mask"
        if area["method"] != method:
            res.corrections.append({"where": "burn_area_ha.method", "claimed": area["method"], "corrected": method})
            area["method"] = method
    else:
        if area["value"] is not None:
            res.corrections.append({"where": "burn_area_ha.value", "claimed": area["value"], "corrected": None, "note": "no real mask cited"})
        out["burn_area_ha"] = {"value": None, "method": None, "evidence_id": None}

    # Location: inside Indonesia and within 5 km of a located piece of evidence.
    lat, lon = out["location"]["lat"], out["location"]["lon"]
    anchors = [
        (e["payload"]["lat"], e["payload"]["lon"])
        for e in evidence
        if e["type"] in LOCATED_TYPES and isinstance(e["payload"].get("lat"), (int, float)) and isinstance(e["payload"].get("lon"), (int, float))
    ]
    if not in_indonesia(lat, lon) or not any(haversine_m(lat, lon, a, b) <= LOCATION_RADIUS_M for a, b in anchors):
        _set_status(out, res, "needs_more_info", "location outside Indonesia or more than 5 km from cited hotspot or report")

    # Text rules on everything a human reads.
    for key in ("recommended_actions", "recommended_actions_id"):
        actions = [_content_filter(a, res, f"{key}[{i}]") for i, a in enumerate(out[key])]
        out[key] = [a for a in actions if a]
    for lang in ("id", "en"):
        out["dispatch_brief"][lang] = _content_filter(out["dispatch_brief"][lang], res, f"dispatch_brief.{lang}")
    for key in ("uncertainties", "uncertainties_id"):
        out[key] = [DASHES.sub(", ", u) for u in out[key]]
    for key in ("drivers", "drivers_id"):
        out["risk"][key] = [DASHES.sub(", ", d) for d in out["risk"][key]]

    out["needs_human"] = True
    return res
