"""Structured output the triage agent must return (spec section 6)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["likely_fire", "possible_fire", "unlikely", "needs_more_info"]
EvidenceType = Literal["hotspot", "weather", "image", "voice", "satellite", "osm", "peat"]

# Ordered from strongest to weakest; "downgrade one level" moves one step right.
STATUS_LADDER: tuple[Status, ...] = ("likely_fire", "possible_fire", "needs_more_info")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(Strict):
    lat: float
    lon: float
    admin: str
    accuracy_m: float = Field(ge=0)


class EvidenceRef(Strict):
    id: str
    type: EvidenceType
    source: str
    retrieved_at: str
    summary: str


class Claim(Strict):
    text: str
    evidence_ids: list[str]


class Risk(Strict):
    score: float = Field(ge=0, le=100)
    drivers: list[str]
    spread_direction_deg: float | None = Field(default=None, ge=0, lt=360)


class BurnArea(Strict):
    value: float | None = Field(default=None, ge=0)
    method: Literal["sam_mask", "dnbr"] | None = None
    evidence_id: str | None = None


class DispatchBrief(Strict):
    id: str
    en: str


class TriageOutput(Strict):
    incident_id: str
    location: Location
    status: Status
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceRef]
    claims: list[Claim]
    risk: Risk
    burn_area_ha: BurnArea
    recommended_actions: list[str]
    dispatch_brief: DispatchBrief
    uncertainties: list[str]
    needs_human: Literal[True]
