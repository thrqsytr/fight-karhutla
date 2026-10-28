"""Triage agent: Muse Spark 1.3 gathers evidence with tools, answers in a fixed schema, then code decides.

Flow: tool loop (at most 8 calls) -> schema-constrained answer -> evidence gate (deterministic) ->
critique pass (a second model lists unsupported claims) -> stored with usage and cost.
The status always ends as needs_human; nothing is sent to anyone.
"""

import json
import logging
import time
from datetime import datetime, timezone

from . import db, gate, meta_client, tools
from .config import Settings
from typing import get_args

from .schema import STATUS_LADDER, EvidenceType

EVIDENCE_TYPES = get_args(EvidenceType)

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 8
REASONING = {"effort": "medium"}
PRIVATE_TYPES = {"report", "voice", "image"}
CONFIDENCE_PENALTY_PER_DISPUTE = 0.1

# Fixed text first so prompt caching applies (spec section 3, decision 6).
SYSTEM = """You triage forest and land fire (karhutla) incidents in Indonesia for human verifiers.
Gather evidence with the tools, then answer. Rules, all mandatory:
1. Every claim cites at least one evidence_id that a tool returned or that is listed in the input.
2. Numbers (area, distance, wind, rain, humidity, FRP) come only from tool results, copied exactly with their units. If a value is missing, use null and add it to uncertainties.
3. Satellite hotspots alone are not enough for likely_fire. likely_fire needs at least one independent source: a citizen photo, voice note, report, or satellite image.
4. Actions are operational only: access routes, water points, coordination with the nearest officers or village. Never give health advice, never suggest a cause, never name any person, company, or party.
5. needs_human is always true.
6. If the data is not enough, choose needs_more_info and say which data is needed in uncertainties.
7. dispatch_brief.id is concise natural Indonesian, at most 120 words, with Indonesian terms (tinggi, nominal, rendah, tidak diketahui);
   dispatch_brief.en has the same content in English. The brief only restates the claims; anything in the brief must also be a claim.
   Do not put evidence IDs in the brief.
8. Wind direction from the weather tool is where the wind blows FROM. Smoke and fire tend to move the opposite way;
   if you state a likely spread direction, make it a claim citing the weather evidence and set risk.spread_direction_deg.
9. Do not use dashes (the long punctuation marks) in sentences. Hyphens inside IDs such as EV-001 are fine.
   Do not invent place names; location.admin is the nearest settlement from get_nearby_assets, or "unknown".
10. Leave the evidence list empty; the system fills it from storage. burn_area_ha is null unless get_satellite_pair returned
    burned_area_ha; then use that value, method "dnbr", and its evidence_id. A dNBR burn scar shows burned ground, not active fire.
11. risk.score is a number from 0 to 100 (0 no concern, 100 most urgent), with short drivers.
12. When independent evidence is missing, you may call plan_drone_survey once. It only proposes a flight for a human
    to approve; mention it in recommended_actions, never as evidence or a claim."""

NUM_NULL = {"type": ["number", "null"]}
STR_NULL = {"type": ["string", "null"]}
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["incident_id", "location", "status", "confidence", "evidence", "claims", "risk", "burn_area_ha",
                 "recommended_actions", "dispatch_brief", "uncertainties", "needs_human"],
    "properties": {
        "incident_id": {"type": "string"},
        "location": {"type": "object", "additionalProperties": False, "required": ["lat", "lon", "admin", "accuracy_m"],
                     "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}, "admin": {"type": "string"}, "accuracy_m": {"type": "number"}}},
        "status": {"type": "string", "enum": ["likely_fire", "possible_fire", "unlikely", "needs_more_info"]},
        "confidence": {"type": "number"},
        "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["id", "type", "source", "retrieved_at", "summary"],
                                                "properties": {"id": {"type": "string"}, "type": {"type": "string", "enum": list(EVIDENCE_TYPES)}, "source": {"type": "string"},
                                                               "retrieved_at": {"type": "string"}, "summary": {"type": "string"}}}},
        "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["text", "evidence_ids"],
                                              "properties": {"text": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}}}},
        "risk": {"type": "object", "additionalProperties": False, "required": ["score", "drivers", "spread_direction_deg"],
                 "properties": {"score": {"type": "number"}, "drivers": {"type": "array", "items": {"type": "string"}}, "spread_direction_deg": NUM_NULL}},
        "burn_area_ha": {"type": "object", "additionalProperties": False, "required": ["value", "method", "evidence_id"],
                         "properties": {"value": NUM_NULL, "method": {"type": ["string", "null"], "enum": ["sam_mask", "dnbr", None]}, "evidence_id": STR_NULL}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "dispatch_brief": {"type": "object", "additionalProperties": False, "required": ["id", "en"], "properties": {"id": {"type": "string"}, "en": {"type": "string"}}},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "needs_human": {"type": "boolean"},
    },
}

CRITIC_SYSTEM = """You are a skeptical reviewer of wildfire triage claims. For each claim you get the full text of the evidence it cites.
List only the claims whose cited evidence does not support them: a number that differs, a direction or trend not in the data,
a conclusion that goes beyond the evidence. Wording that paraphrases the evidence faithfully is supported. Do not judge style."""
CRITIC_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["disputed"], "properties": {
    "disputed": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["index", "reason"],
                                            "properties": {"index": {"type": "integer"}, "reason": {"type": "string"}}}}}}


def _incident_input(settings: Settings, incident_id: str) -> tuple[dict, list[dict]]:
    with db.session(settings.db_path) as conn:
        inc = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if inc is None:
            raise KeyError(incident_id)
        inc = dict(inc)
        ev = db.list_evidence(conn, incident_id)
    return inc, ev


def _user_message(inc: dict, ev: list[dict]) -> str:
    listed = "\n".join(f"- {e['id']} ({e['type']}, {e['source']}): {e['summary']}" for e in ev) or "- none yet"
    return (
        f"Incident {inc['id']}. Trigger: {inc['trigger']}. Centre lat {inc['lat']:.5f}, lon {inc['lon']:.5f}. "
        f"First seen {inc['first_seen']}, last seen {inc['last_seen']}. Hotspots: {inc['hotspot_count']}. Citizen reports: {inc.get('reporter_count') or 0}.\n"
        f"Evidence already stored:\n{listed}\n"
        "Use the tools to read the evidence you need, then return the triage."
    )


def _tool_defs(allowed: set[str] | None) -> list[dict]:
    return [d for d in tools.TOOL_DEFS if allowed is None or d["name"] in allowed]


def _call(ctx: tools.ToolContext, name: str, arguments: str, calls: int, allowed: set[str] | None) -> dict:
    if calls > MAX_TOOL_CALLS:
        return {"status": "refused", "reason": "tool call limit reached; answer now"}
    if allowed is not None and name not in allowed:
        return {"status": "refused", "reason": f"tool {name} is not available in this run"}
    return tools.call(ctx, name, arguments)


def run_model(settings: Settings, inc: dict, ev: list[dict], ctx: tools.ToolContext, *, provider: str = "meta",
              allowed: set[str] | None = None) -> dict:
    """One agent pass. provider "meta" is production (Muse Spark 1.3, Responses API); "baseten" runs Muse Spark Open
    through Chat Completions with the same prompt, tools, and schema, for evaluation at volume."""
    if provider == "baseten":
        return _run_chat(settings, inc, ev, ctx, allowed)
    return _run_model(settings, inc, ev, ctx, allowed)


def _run_chat(settings: Settings, inc: dict, ev: list[dict], ctx: tools.ToolContext, allowed: set[str] | None) -> dict:
    client = meta_client.baseten(settings)
    defs = [{"type": "function", "function": {k: d[k] for k in ("name", "description", "parameters")}} for d in _tool_defs(allowed)]
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _user_message(inc, ev)}]
    calls = 0
    while calls <= MAX_TOOL_CALLS:
        r = client.chat.completions.create(model=meta_client.SPARK_OPEN, messages=msgs, tools=defs)
        ctx.meter.spark(r.usage, model=meta_client.SPARK_OPEN)
        m = r.choices[0].message
        if not m.tool_calls:
            break
        msgs.append({"role": "assistant", "content": m.content or "",
                     "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in m.tool_calls]})
        for tc in m.tool_calls:
            calls += 1
            result = _call(ctx, tc.function.name, tc.function.arguments, calls, allowed)
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
    msgs.append({"role": "user", "content": "Return the triage now in the required format."})
    final = client.chat.completions.create(model=meta_client.SPARK_OPEN, messages=msgs,
                                           response_format={"type": "json_schema", "json_schema": {"name": "triage", "schema": OUTPUT_SCHEMA, "strict": True}})
    ctx.meter.spark(final.usage, model=meta_client.SPARK_OPEN)
    return json.loads(final.choices[0].message.content)


def _run_model(settings: Settings, inc: dict, ev: list[dict], ctx: tools.ToolContext, allowed: set[str] | None = None) -> dict:
    client = meta_client.meta(settings)
    resp = client.responses.create(model=meta_client.SPARK, instructions=SYSTEM, input=_user_message(inc, ev), tools=_tool_defs(allowed), reasoning=REASONING)
    ctx.meter.spark(resp.usage)
    calls = 0
    while True:
        fcs = [o for o in resp.output if o.type == "function_call"]
        if not fcs:
            break
        outputs = []
        for fc in fcs:
            calls += 1
            result = _call(ctx, fc.name, fc.arguments, calls, allowed)
            outputs.append({"type": "function_call_output", "call_id": fc.call_id, "output": json.dumps(result, ensure_ascii=False)})
        resp = client.responses.create(model=meta_client.SPARK, instructions=SYSTEM, input=outputs, previous_response_id=resp.id,
                                       tools=_tool_defs(allowed), reasoning=REASONING)
        ctx.meter.spark(resp.usage)
        if calls > MAX_TOOL_CALLS:
            break
    final = client.responses.create(
        model=meta_client.SPARK, instructions=SYSTEM, previous_response_id=resp.id, reasoning=REASONING,
        input="Return the triage now in the required format.",
        text={"format": {"type": "json_schema", "name": "triage", "schema": OUTPUT_SCHEMA, "strict": True}},
    )
    ctx.meter.spark(final.usage)
    return json.loads(final.output_text)


def _critique(settings: Settings, output: dict, ev: list[dict], meter: meta_client.Meter) -> dict:
    """Second opinion on claims that passed the gate. Public-only claims go to Muse Spark Open on Baseten;
    claims citing citizen evidence stay on Meta Model API (Standard mode), since Baseten data handling is unchecked."""
    by_id = {e["id"]: e for e in ev}
    public, private = [], []
    # The brief is checked too: it must not say anything the claims do not. It is sent as one extra item
    # citing everything the claims cite, so a sentence with no support shows up as disputed.
    for i, c in enumerate(output["claims"]):
        cited = [by_id[x] for x in c["evidence_ids"] if x in by_id]
        item = {"index": i, "claim": c["text"], "evidence": [{"id": e["id"], "summary": e["summary"], "data": _critic_payload(e)} for e in cited]}
        (private if any(e["type"] in PRIVATE_TYPES for e in cited) else public).append(item)
    all_cited = [by_id[x] for c in output["claims"] for x in c["evidence_ids"] if x in by_id]
    seen, uniq = set(), []
    for e in all_cited:
        if e["id"] not in seen:
            seen.add(e["id"])
            uniq.append(e)
    brief_item = {"index": len(output["claims"]), "claim": "Dispatch brief (English version): " + output["dispatch_brief"]["en"],
                  "evidence": [{"id": e["id"], "summary": e["summary"], "data": _critic_payload(e)} for e in uniq]}
    (private if any(e["type"] in PRIVATE_TYPES for e in uniq) else public).append(brief_item)
    disputed, providers, errors = [], [], []
    if public:
        try:
            r = meta_client.baseten(settings).chat.completions.create(
                model=meta_client.SPARK_OPEN, messages=[{"role": "system", "content": CRITIC_SYSTEM}, {"role": "user", "content": json.dumps(public, ensure_ascii=False)}],
                response_format={"type": "json_schema", "json_schema": {"name": "critique", "schema": CRITIC_SCHEMA, "strict": True}},
            )
            meter.spark(r.usage, model=meta_client.SPARK_OPEN)
            disputed += json.loads(r.choices[0].message.content)["disputed"]
            providers.append("baseten:" + meta_client.SPARK_OPEN)
        except Exception as exc:
            log.warning("baseten critique failed: %r", exc)
            errors.append(f"public claims not reviewed: {str(exc)[:200]}")
    if private:
        try:
            r = meta_client.meta(settings).responses.create(
                model=meta_client.SPARK, instructions=CRITIC_SYSTEM, input=json.dumps(private, ensure_ascii=False), reasoning={"effort": "low"},
                text={"format": {"type": "json_schema", "name": "critique", "schema": CRITIC_SCHEMA, "strict": True}},
            )
            meter.spark(r.usage)
            disputed += json.loads(r.output_text)["disputed"]
            providers.append("meta:" + meta_client.SPARK)
        except Exception as exc:
            log.warning("meta critique failed: %r", exc)
            errors.append(f"citizen-evidence claims not reviewed: {str(exc)[:200]}")
    valid = {d["index"]: d["reason"] for d in disputed if 0 <= d["index"] < len(output["claims"])}
    brief = [d["reason"] for d in disputed if d["index"] == len(output["claims"])]
    return {"reviewed": len(public) + len(private), "disputed": [{"index": i, "reason": r} for i, r in sorted(valid.items())],
            "brief_disputed": brief, "providers": providers, "errors": errors}


def _critic_payload(e: dict) -> dict:
    """What the critic sees per evidence item: the measured fields, without media or long ID lists."""
    p = dict(e["payload"])
    for k in ("hotspot_ids", "media_id", "sha256", "objects", "forecast_6h"):
        p.pop(k, None)
    return p


def _fallback(inc: dict, reason: str) -> dict:
    return {
        "incident_id": inc["id"], "location": {"lat": inc["lat"], "lon": inc["lon"], "admin": "unknown", "accuracy_m": 0},
        "status": "needs_more_info", "confidence": 0.0, "evidence": [], "claims": [],
        "risk": {"score": 0, "drivers": [], "spread_direction_deg": None}, "burn_area_ha": {"value": None, "method": None, "evidence_id": None},
        "recommended_actions": [], "dispatch_brief": {"id": "Triase otomatis gagal. Perlu pemeriksaan manual.", "en": "Automatic triage failed. Manual review needed."},
        "uncertainties": [reason], "needs_human": True,
    }


def triage(settings: Settings, incident_id: str) -> dict:
    """Run one triage. Writes happen only in short sessions, never during a model or network call."""
    started = time.time()
    meter = meta_client.Meter()
    with db.session(settings.db_path) as conn:
        run_id = conn.execute("INSERT INTO agent_runs (incident_id, started_at, outcome) VALUES (?,?,?)", (incident_id, db.utcnow(), "running")).lastrowid
        conn.execute("UPDATE incidents SET status='triaging', updated_at=? WHERE id=?", (db.utcnow(), incident_id))
    inc, ev = _incident_input(settings, incident_id)
    ctx = tools.ToolContext(settings=settings, incident_id=incident_id, meter=meter)
    outcome, error, gate_result, critique = "ok", None, None, None
    try:
        raw = _run_model(settings, inc, ev, ctx)
        _, ev = _incident_input(settings, incident_id)  # tools may have stored new evidence
        gate_result = gate.run(raw, ev, incident_id)
        if not gate_result.accepted:
            log.warning("gate rejected schema for %s, retrying once: %s", incident_id, gate_result.schema_errors)
            raw = _run_model(settings, inc, ev, ctx)
            _, ev = _incident_input(settings, incident_id)
            gate_result = gate.run(raw, ev, incident_id)
        if gate_result.accepted:
            output = gate_result.output
            critique = _critique(settings, output, ev, meter)
            for d in critique["disputed"]:
                output["claims"][d["index"]]["disputed"] = d["reason"]
            if critique["brief_disputed"]:
                output["dispatch_brief"]["disputed"] = critique["brief_disputed"]
            if critique["disputed"]:
                output["confidence"] = round(max(0.0, output["confidence"] - CONFIDENCE_PENALTY_PER_DISPUTE * len(critique["disputed"])), 3)
        else:
            outcome = "schema_failed"
            output = _fallback(inc, "agent output failed the schema twice: " + "; ".join(gate_result.schema_errors[:3]))
    except Exception as exc:
        log.exception("triage failed for %s", incident_id)
        outcome, error = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
        output = None

    seconds = round(time.time() - started, 1)
    cost = meter.summary()
    detail = {"tools": ctx.log, "usage": cost, "critique": critique, "models": {"triage": meta_client.SPARK, "critique": critique["providers"] if critique else []}}
    with db.session(settings.db_path) as conn:
        if output is not None:
            gate_doc = {**(gate_result.to_dict() if gate_result else {}), "critique": critique, "seconds": seconds,
                        "cost_usd": cost["cost_usd"], "triaged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "outcome": outcome}
            conn.execute(
                "UPDATE incidents SET status=?, confidence=?, risk_score=?, triage_json=?, gate_json=?, updated_at=? WHERE id=?",
                (output["status"], output["confidence"], output["risk"]["score"], json.dumps(output, ensure_ascii=False), json.dumps(gate_doc, ensure_ascii=False), db.utcnow(), incident_id),
            )
        else:
            # Keep the previous triage, if any; only the run records the failure.
            prev = conn.execute("SELECT triage_json FROM incidents WHERE id=?", (incident_id,)).fetchone()[0]
            prev_status = json.loads(prev)["status"] if prev else "pending_triage"
            conn.execute("UPDATE incidents SET status=?, updated_at=? WHERE id=?", (prev_status if prev_status in STATUS_LADDER + ("unlikely",) else "pending_triage", db.utcnow(), incident_id))
        conn.execute("UPDATE agent_runs SET finished_at=?, outcome=?, error=?, seconds=?, cost_usd=?, detail_json=? WHERE id=?",
                     (db.utcnow(), outcome, error, seconds, cost["cost_usd"], json.dumps(detail, ensure_ascii=False), run_id))
    log.info("triage %s: outcome=%s seconds=%s cost=$%.4f tools=%s", incident_id, outcome, seconds, cost["cost_usd"], [t["tool"] for t in ctx.log])
    return {"incident_id": incident_id, "outcome": outcome, "error": error, "seconds": seconds, "cost_usd": cost["cost_usd"],
            "status": output["status"] if output else None}
