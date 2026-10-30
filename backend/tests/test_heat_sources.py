"""Persistent heat sources (review round 3): a public flag counts only after a verifier accepts it; the zone marks
incidents, the gate asks for a citizen photo or voice inside it, and a withdrawn zone stops counting."""

from fastapi.testclient import TestClient

from backend import db, gate, heat_sources

AUTH = {"Authorization": "Bearer test-token"}


def _client(settings):
    from backend.main import app

    heat_sources._sent.clear()
    return TestClient(app)


def _public_incident(c):
    return c.get("/api/incidents").json()["items"][0]["id"]


def test_flag_needs_consent_a_known_kind_and_is_limited(settings):
    with _client(settings) as c:
        inc = _public_incident(c)
        assert c.post(f"/api/incidents/{inc}/heat-source-flags", json={"kind": "mine_smelter"}).status_code == 422  # no consent
        assert c.post(f"/api/incidents/{inc}/heat-source-flags", json={"kind": "arson", "consent": True}).status_code == 422
        r = c.post(f"/api/incidents/{inc}/heat-source-flags", json={"kind": "mine_smelter", "note": "tambang batu bara", "consent": True})
        assert r.status_code == 201 and r.json()["status"] == "pending"
        assert c.post(f"/api/incidents/{inc}/heat-source-flags", json={"kind": "mine_smelter", "consent": True}).status_code == 409  # same day
        public = c.get(f"/api/incidents/{inc}").json()["heat_source_flags"]
        assert public["pending"] == 1 and "items" not in public and public["zone"] is None
        verifier = c.get(f"/api/incidents/{inc}", headers=AUTH).json()["heat_source_flags"]
        assert verifier["items"][0]["note"] == "tambang batu bara" and "sender_hash" not in verifier["items"][0]
        assert c.get("/api/admin/queue", headers=AUTH).json()["heat_flags"][0]["incident_id"] == inc


def test_accepting_makes_a_zone_that_marks_incidents_and_withdrawing_undoes_it(settings):
    with _client(settings) as c:
        inc = _public_incident(c)
        fid = c.post(f"/api/incidents/{inc}/heat-source-flags", json={"kind": "gas_flare_refinery", "consent": True}).json()["id"]
        assert c.post(f"/api/heat-source-flags/{fid}/decision", json={"decision": "accept"}).status_code == 401
        assert c.post(f"/api/heat-source-flags/{fid}/decision", json={"decision": "accept", "radius_m": 50}, headers=AUTH).status_code == 422
        r = c.post(f"/api/heat-source-flags/{fid}/decision", json={"decision": "accept", "radius_m": 1500}, headers=AUTH).json()
        assert r["status"] == "accepted" and r["incidents_marked"] >= 1
        assert c.post(f"/api/heat-source-flags/{fid}/decision", json={"decision": "reject"}, headers=AUTH).status_code == 409
        zones = c.get("/api/heat-source-zones").json()["items"]
        assert zones[0]["radius_m"] == 1500 and zones[0]["kind"] == "gas_flare_refinery"
        listed = {i["id"]: i for i in c.get("/api/incidents").json()["items"]}
        assert listed[inc]["heat_source"] is True
        detail = c.get(f"/api/incidents/{inc}").json()
        assert detail["heat_source_flags"]["zone"]["id"] == r["zone_id"]
        assert any(e["type"] == "heat_source" for e in detail["evidence"])  # public: it is a verifier's finding
        assert c.post(f"/api/heat-source-zones/{r['zone_id']}/withdraw", headers=AUTH).status_code == 200
        assert c.get("/api/heat-source-zones").json()["items"] == []
        assert {i["id"]: i for i in c.get("/api/incidents").json()["items"]}[inc]["heat_source"] is False
        with db.session(settings.db_path) as conn:
            ev = db.list_evidence(conn, inc)
        assert heat_sources.active_zone_evidence(ev) == []  # the withdrawal row is the newest for the zone


def test_rejecting_leaves_the_incident_as_it_was(settings):
    with _client(settings) as c:
        inc = _public_incident(c)
        fid = c.post(f"/api/incidents/{inc}/heat-source-flags", json={"kind": "other", "consent": True}).json()["id"]
        assert c.post(f"/api/heat-source-flags/{fid}/decision", json={"decision": "reject", "note": "titik di hutan"}, headers=AUTH).json()["zone_id"] is None
        assert c.get("/api/heat-source-zones").json()["count"] == 0
        assert not any(e["type"] == "heat_source" for e in c.get(f"/api/incidents/{inc}").json()["evidence"])


def _raw(inc_id, ev_ids):
    return {"incident_id": inc_id, "location": {"lat": -0.02, "lon": 116.45, "admin": "unknown", "accuracy_m": 375}, "status": "likely_fire",
            "confidence": 0.9, "evidence": [], "claims": [{"text": "Titik panas dan bekas terbakar terlihat.", "evidence_ids": ev_ids}],
            "risk": {"score": 60, "drivers": []}, "burn_area_ha": {"value": None, "method": None, "evidence_id": None}, "recommended_actions": [],
            "dispatch_brief": {"id": "Api.", "en": "Fire."}, "uncertainties": [], "needs_human": True}


def test_gate_needs_a_citizen_photo_for_likely_fire_inside_a_zone():
    sat = {"id": "EV-001", "type": "satellite", "payload": {"status": "ok", "burned_area_ha": 12.0}, "summary": "", "source": "", "retrieved_at": "2026-10-30T00:00:00+00:00"}
    zone = {"id": "EV-002", "type": "heat_source", "payload": {"zone_id": 1, "kind": "mine_smelter"}, "summary": "", "source": "", "retrieved_at": "2026-10-30T00:00:00+00:00"}
    hs = {"id": "EV-009", "type": "hotspot", "payload": {"lat": -0.02, "lon": 116.45}, "summary": "", "source": "", "retrieved_at": "2026-10-30T00:00:00+00:00"}
    photo = {"id": "EV-003", "type": "image", "payload": {}, "summary": "", "source": "", "retrieved_at": "2026-10-30T00:00:00+00:00"}
    assert gate.run(_raw("INC-1", ["EV-001"]), [hs, sat], "INC-1").output["status"] == "likely_fire"  # outside a zone a 12 ha burn carries it
    out = gate.run(_raw("INC-1", ["EV-001"]), [hs, sat, zone], "INC-1")
    assert out.output["status"] == "possible_fire" and "heat source" in out.downgrades[-1]
    assert gate.run(_raw("INC-1", ["EV-001", "EV-003"]), [hs, sat, zone, photo], "INC-1").output["status"] == "likely_fire"
    withdrawn = {"id": "EV-004", "type": "heat_source", "payload": {"zone_id": 1, "withdrawn": True}, "summary": "", "source": "", "retrieved_at": "2026-10-30T00:00:00+00:00"}
    assert gate.run(_raw("INC-1", ["EV-001"]), [hs, sat, zone, withdrawn], "INC-1").output["status"] == "likely_fire"
