"""Agent tests with a scripted fake model: no network, no credit spent."""

import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from backend import agent, db, evidence, ingest, meta_client, reports

AUTH = {"Authorization": "Bearer test-token"}


def usage(i=1000, o=500, cached=0):
    return NS(model_dump=lambda: {"input_tokens": i, "output_tokens": o, "input_tokens_details": {"cached_tokens": cached}, "output_tokens_details": {"reasoning_tokens": o // 2}})


class FakeResponses:
    """Plays back a script: tool calls first, then the final structured answer."""

    def __init__(self, script, final):
        self.script, self.final, self.calls = list(script), final, []

    def create(self, **kw):
        self.calls.append(kw)
        if "text" in kw and kw["text"]["format"]["name"] == "triage":
            out = self.final(kw) if callable(self.final) else self.final
            return NS(id=f"r{len(self.calls)}", output=[], output_text=json.dumps(out), usage=usage())
        if "text" in kw and kw["text"]["format"]["name"] == "critique":
            return NS(id="c", output=[], output_text=json.dumps({"disputed": []}), usage=usage(200, 50))
        step = self.script.pop(0) if self.script else []
        items = [NS(type="function_call", name=n, arguments=a, call_id=f"call{len(self.calls)}{n}") for n, a in step]
        return NS(id=f"r{len(self.calls)}", output=items, output_text="", usage=usage(cached=300))


class FakeChat:
    def __init__(self, disputed):
        self.disputed, self.calls = disputed, []

    def create(self, **kw):
        self.calls.append(kw)
        return NS(choices=[NS(message=NS(content=json.dumps({"disputed": self.disputed})))], usage=usage(300, 100))


def good_output(inc_id, lat, lon):
    return {
        "incident_id": inc_id, "location": {"lat": lat, "lon": lon, "admin": "Tuana Tuha", "accuracy_m": 375},
        "status": "possible_fire", "confidence": 0.6, "evidence": [],
        "claims": [
            {"text": "13 FIRMS hotspots with max FRP 33.86 MW.", "evidence_ids": ["EV-001"]},
            {"text": "Wind 9 m/s from the east.", "evidence_ids": ["EV-002"]},  # wrong number: gate must correct it
            {"text": "Fire is moving toward the village.", "evidence_ids": ["EV-099"]},  # unknown evidence: gate must drop it
        ],
        "risk": {"score": 55, "drivers": ["dry season"], "spread_direction_deg": None},
        "burn_area_ha": {"value": 120.0, "method": "sam_mask", "evidence_id": "EV-001"},  # invented area: gate must clear it
        "recommended_actions": ["Coordinate with the nearest village officer to check road access."],
        "dispatch_brief": {"id": "Ada 13 titik panas. Warga sebaiknya memakai masker N95.", "en": "There are 13 hotspots."},
        "uncertainties": ["No photo yet."], "needs_human": True,
    }


@pytest.fixture
def ready(settings, monkeypatch):
    db.init(settings.db_path)
    ingest.run_once(settings)
    evidence.run_enrichment(settings)
    with db.session(settings.db_path) as conn:
        row = conn.execute("SELECT id, lat, lon FROM incidents WHERE id='INC-20261025-0003'").fetchone()
    live = settings.model_copy(update={"model_api_key": "test", "baseten_api_key": "test"})
    return live, row


def install(monkeypatch, responses, chat):
    monkeypatch.setattr(meta_client, "meta", lambda s: NS(responses=responses))
    monkeypatch.setattr(meta_client, "baseten", lambda s: NS(chat=NS(completions=chat)))


def test_triage_runs_tools_gates_critiques_and_stores(ready, monkeypatch):
    live, inc = ready
    responses = FakeResponses([[("get_weather", "{}"), ("get_nearby_assets", "{}")]], good_output(inc["id"], inc["lat"], inc["lon"]))
    chat = FakeChat([{"index": 0, "reason": "test dispute"}])
    install(monkeypatch, responses, chat)

    r = agent.triage(live, inc["id"])
    assert r["outcome"] == "ok" and r["error"] is None

    with db.session(live.db_path) as conn:
        row = conn.execute("SELECT status, confidence, triage_json, gate_json FROM incidents WHERE id=?", (inc["id"],)).fetchone()
        run = conn.execute("SELECT * FROM agent_runs WHERE incident_id=?", (inc["id"],)).fetchone()
    out, g = json.loads(row["triage_json"]), json.loads(row["gate_json"])
    # Gate: unknown-evidence claim dropped and status lowered one level, wrong wind corrected, invented area cleared, medical sentence removed.
    assert len(out["claims"]) == 2 and g["claims_dropped"][0]["text"].startswith("Fire is moving")
    assert row["status"] == "needs_more_info"
    assert "1.82 m/s" in out["claims"][1]["text"]
    assert out["burn_area_ha"] == {"value": None, "method": None, "evidence_id": None}
    assert "masker" not in out["dispatch_brief"]["id"].lower()
    assert out["needs_human"] is True
    # Evidence list comes from storage, not from the model.
    assert [e["id"] for e in out["evidence"]] == ["EV-001", "EV-002", "EV-003"]
    # Critique: public-only claims went to Baseten; the disputed claim is marked and confidence lowered.
    assert len(chat.calls) == 1 and out["claims"][0]["disputed"] == "test dispute"
    assert row["confidence"] == pytest.approx(0.5)
    assert g["critique"]["providers"] == ["baseten:" + meta_client.SPARK_OPEN]
    # Run is recorded with tools and a metered cost.
    detail = json.loads(run["detail_json"])
    assert run["outcome"] == "ok" and run["cost_usd"] > 0
    assert [t["tool"] for t in detail["tools"]] == ["get_weather", "get_nearby_assets"]
    assert detail["usage"]["unpriced_calls"] == 1  # the Baseten call is credit, not priced per token
    # The system prompt goes first and is identical on every call, for prompt caching.
    assert len({c.get("instructions") for c in responses.calls if c.get("instructions") == agent.SYSTEM}) == 1


def test_schema_failure_retries_once_then_falls_back(ready, monkeypatch):
    live, inc = ready
    bad = {"incident_id": inc["id"], "status": "likely_fire"}  # missing required fields
    responses = FakeResponses([], bad)
    install(monkeypatch, responses, FakeChat([]))
    r = agent.triage(live, inc["id"])
    assert r["outcome"] == "schema_failed" and r["status"] == "needs_more_info"
    finals = [c for c in responses.calls if "text" in c]
    assert len(finals) == 2  # one retry, not more
    with db.session(live.db_path) as conn:
        out = json.loads(conn.execute("SELECT triage_json FROM incidents WHERE id=?", (inc["id"],)).fetchone()[0])
    assert out["claims"] == [] and "failed the schema twice" in out["uncertainties"][0]


def test_model_error_is_recorded_and_previous_state_kept(ready, monkeypatch):
    live, inc = ready

    def boom(s):
        raise meta_client.ModelUnavailable("HTTP 503 from upstream")

    monkeypatch.setattr(meta_client, "meta", boom)
    r = agent.triage(live, inc["id"])
    assert r["outcome"] == "error" and "503" in r["error"]
    with db.session(live.db_path) as conn:
        row = conn.execute("SELECT status, triage_json FROM incidents WHERE id=?", (inc["id"],)).fetchone()
        run = conn.execute("SELECT outcome, error FROM agent_runs WHERE incident_id=?", (inc["id"],)).fetchone()
    assert row["status"] == "pending_triage" and row["triage_json"] is None
    assert run["outcome"] == "error" and "503" in run["error"]


def test_tool_limit_is_enforced(ready, monkeypatch):
    live, inc = ready
    flood = [[("get_weather", "{}")] for _ in range(12)]
    responses = FakeResponses(flood, good_output(inc["id"], inc["lat"], inc["lon"]))
    install(monkeypatch, responses, FakeChat([]))
    agent.triage(live, inc["id"])
    with db.session(live.db_path) as conn:
        detail = json.loads(conn.execute("SELECT detail_json FROM agent_runs WHERE incident_id=?", (inc["id"],)).fetchone()[0])
    assert len(detail["tools"]) == agent.MAX_TOOL_CALLS


def test_claims_on_citizen_evidence_are_critiqued_on_meta_not_baseten(ready, monkeypatch):
    live, inc = ready
    with db.session(live.db_path) as conn:
        from datetime import datetime, timedelta

        last = conn.execute("SELECT last_seen FROM incidents WHERE id=?", (inc["id"],)).fetchone()[0]
        r = reports.submit(conn, live.data_dir, lat=inc["lat"], lon=inc["lon"], accuracy_m=10, location_source="gps",
                           text="Asap tebal dekat kebun", photos=[], audio=None, now=datetime.fromisoformat(last) + timedelta(hours=1))
    report_ev = r["evidence_ids"][0]
    out = good_output(inc["id"], inc["lat"], inc["lon"])
    out["claims"] = [{"text": "A resident reports thick smoke near a garden.", "evidence_ids": [report_ev]},
                     {"text": "13 FIRMS hotspots with max FRP 33.86 MW.", "evidence_ids": ["EV-001"]}]
    responses = FakeResponses([[("get_citizen_reports", "{}")]], out)
    chat = FakeChat([])
    install(monkeypatch, responses, chat)
    agent.triage(live, inc["id"])
    sent_to_baseten = json.dumps(chat.calls[0]["messages"])
    assert "Asap tebal" not in sent_to_baseten and report_ev not in sent_to_baseten
    meta_critiques = [c for c in responses.calls if c.get("text", {}).get("format", {}).get("name") == "critique"]
    assert len(meta_critiques) == 1 and "Asap tebal" in meta_critiques[0]["input"]


def test_public_view_hides_triage_built_on_citizen_evidence(ready, monkeypatch):
    live, inc = ready
    from backend.config import get_settings
    from backend.main import app

    monkeypatch.setattr("backend.main.get_settings", lambda: live)
    app.dependency_overrides[get_settings] = lambda: live
    try:
        with db.session(live.db_path) as conn:
            from datetime import datetime, timedelta

            last = conn.execute("SELECT last_seen FROM incidents WHERE id=?", (inc["id"],)).fetchone()[0]
            r = reports.submit(conn, live.data_dir, lat=inc["lat"], lon=inc["lon"], accuracy_m=10, location_source="gps",
                               text="Asap tebal dekat kebun", photos=[], audio=None, now=datetime.fromisoformat(last) + timedelta(hours=1))
        out = good_output(inc["id"], inc["lat"], inc["lon"])
        out["claims"] = [{"text": "A resident reports thick smoke.", "evidence_ids": [r["evidence_ids"][0]]}]
        install(monkeypatch, FakeResponses([], out), FakeChat([]))
        agent.triage(live, inc["id"])
        with TestClient(app) as c:
            pub = c.get(f"/api/incidents/{inc['id']}").json()
            assert pub["triage"] == {"status": pub["status"], "hidden": "awaiting human verification"}
            priv = c.get(f"/api/incidents/{inc['id']}", headers=AUTH).json()
            assert priv["triage"]["claims"]
            c.post(f"/api/incidents/{inc['id']}/verdict", json={"decision": "approve"}, headers=AUTH)
            assert c.get(f"/api/incidents/{inc['id']}").json()["triage"].get("claims")
    finally:
        app.dependency_overrides.clear()


def test_triage_endpoint_requires_key_and_token(settings):
    from backend.main import app

    with TestClient(app) as c:
        inc_id = c.get("/api/incidents").json()["items"][0]["id"]
        assert c.post(f"/api/incidents/{inc_id}/triage", json={"code": "x"}).status_code == 401
        assert c.post(f"/api/incidents/{inc_id}/triage", json={"code": "x"}, headers=AUTH).status_code == 503  # no MODEL_API_KEY in tests
