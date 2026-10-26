import copy

from backend import gate

INC = "INC-20261025-0001"

EVIDENCE = [
    {"id": "EV-001", "type": "hotspot", "source": "NASA FIRMS", "retrieved_at": "2026-10-26T00:00:00+00:00",
     "summary": "3 VIIRS hotspots", "payload": {"lat": -0.02, "lon": 116.45, "count": 3}},
    {"id": "EV-002", "type": "weather", "source": "Open-Meteo", "retrieved_at": "2026-10-26T00:00:00+00:00",
     "summary": "wind and rain", "payload": {"wind_speed_ms": 3.4, "rain_24h_mm": 0.0, "relative_humidity_pct": 62}},
    {"id": "EV-003", "type": "image", "source": "citizen photo", "retrieved_at": "2026-10-26T00:00:00+00:00",
     "summary": "smoke mask", "payload": {"lat": -0.021, "lon": 116.451, "pixel_count": 2500, "pixel_size_m": 10}},
    {"id": "EV-004", "type": "osm", "source": "OpenStreetMap", "retrieved_at": "2026-10-26T00:00:00+00:00",
     "summary": "nearest assets", "payload": {"nearest": {"road": {"distance_m": 1200}, "settlement": {"distance_m": 3400}}}},
]

BASE = {
    "incident_id": INC,
    "location": {"lat": -0.02, "lon": 116.45, "admin": "Kahala, Kenohan, Kutai Kartanegara", "accuracy_m": 375},
    "status": "likely_fire",
    "confidence": 0.8,
    "evidence": [],
    "claims": [
        {"text": "Tiga titik panas VIIRS terdeteksi.", "evidence_ids": ["EV-001"]},
        {"text": "Foto warga menunjukkan asap.", "evidence_ids": ["EV-003"]},
        {"text": "Angin 3.4 m/s tanpa hujan 0 mm dalam 24 jam.", "evidence_ids": ["EV-002"]},
    ],
    "risk": {"score": 70, "drivers": ["angin kering"], "spread_direction_deg": 45},
    "burn_area_ha": {"value": 30.0, "method": "sam_mask", "evidence_id": "EV-003"},
    "recommended_actions": ["Koordinasi dengan petugas desa terdekat."],
    "dispatch_brief": {"id": "Asap terlihat di dekat Kahala.", "en": "Smoke seen near Kahala."},
    "uncertainties": [],
    "needs_human": True,
}


def make(**changes):
    d = copy.deepcopy(BASE)
    d.update(changes)
    return d


def test_clean_output_passes_unchanged():
    r = gate.run(make(), EVIDENCE, INC)
    assert r.accepted
    assert r.output["status"] == "likely_fire"
    assert r.grounding_rate == 1.0
    assert r.claims_dropped == [] and r.violations == [] and r.downgrades == []


def test_schema_failure_is_rejected_not_repaired():
    bad = make()
    del bad["claims"]
    r = gate.run(bad, EVIDENCE, INC)
    assert not r.accepted and r.output is None and r.schema_errors


def test_needs_human_false_fails_schema():
    r = gate.run(make(needs_human=False), EVIDENCE, INC)
    assert not r.accepted


def test_claim_with_unknown_evidence_is_dropped_and_status_downgraded():
    d = make()
    d["claims"].append({"text": "Api menyebar ke utara.", "evidence_ids": ["EV-999"]})
    r = gate.run(d, EVIDENCE, INC)
    assert len(r.claims_dropped) == 1
    assert r.claims_total == 4 and r.claims_passed == 3
    assert r.output["status"] == "possible_fire"


def test_claim_without_evidence_is_dropped():
    d = make()
    d["claims"].append({"text": "Lahan gambut terbakar.", "evidence_ids": []})
    r = gate.run(d, EVIDENCE, INC)
    assert r.claims_dropped[0]["reason"] == "no evidence"


def test_hotspot_only_cannot_be_likely_fire():
    d = make(claims=[{"text": "Tiga titik panas VIIRS terdeteksi.", "evidence_ids": ["EV-001"]}])
    r = gate.run(d, EVIDENCE, INC)
    assert r.output["status"] == "possible_fire"


def test_wrong_wind_number_is_replaced_with_tool_value():
    d = make()
    d["claims"][2]["text"] = "Angin 9 m/s dari timur."
    r = gate.run(d, EVIDENCE, INC)
    assert "3.4 m/s" in r.output["claims"][2]["text"]
    assert r.corrections[0]["claimed"] == "9 m/s"


def test_distance_within_tolerance_is_kept():
    d = make()
    d["claims"].append({"text": "Jalan terdekat 1.2 km.", "evidence_ids": ["EV-004"]})
    r = gate.run(d, EVIDENCE, INC)
    assert r.output["claims"][-1]["text"] == "Jalan terdekat 1.2 km."


def test_area_is_recomputed_from_pixels():
    r = gate.run(make(), EVIDENCE, INC)
    assert r.output["burn_area_ha"]["value"] == 25.0  # 2500 px * 10 m * 10 m = 250,000 m2 = 25 ha
    assert any(c["where"] == "burn_area_ha.value" for c in r.corrections)


def test_area_without_real_mask_is_cleared():
    d = make(burn_area_ha={"value": 40.0, "method": "sam_mask", "evidence_id": "EV-002"})
    r = gate.run(d, EVIDENCE, INC)
    assert r.output["burn_area_ha"] == {"value": None, "method": None, "evidence_id": None}


def test_location_far_from_evidence_needs_more_info():
    d = make(location={"lat": -0.2, "lon": 116.45, "admin": "x", "accuracy_m": 0})
    r = gate.run(d, EVIDENCE, INC)
    assert r.output["status"] == "needs_more_info"


def test_location_outside_indonesia_needs_more_info():
    d = make(location={"lat": 14.6, "lon": 121.0, "admin": "x", "accuracy_m": 0})
    r = gate.run(d, EVIDENCE, INC)
    assert r.output["status"] == "needs_more_info"


def test_content_rules_remove_medical_cause_and_party_sentences():
    d = make(dispatch_brief={
        "id": "Asap terlihat. Warga sebaiknya pakai masker N95. Lahan sengaja dibakar oleh PT Contoh.",
        "en": "Smoke seen. The fire was caused by a plantation company.",
    })
    r = gate.run(d, EVIDENCE, INC)
    assert r.output["dispatch_brief"]["id"] == "Asap terlihat."
    assert r.output["dispatch_brief"]["en"] == "Smoke seen."
    assert {v["rule"] for v in r.violations} >= {"medical", "cause"}


def test_segmentation_masker_word_is_allowed():
    d = make(dispatch_brief={"id": "Masker SAM menandai area asap.", "en": "The SAM mask marks smoke."})
    r = gate.run(d, EVIDENCE, INC)
    assert r.output["dispatch_brief"]["id"] == "Masker SAM menandai area asap."


def test_dashes_are_replaced():
    em, en = chr(0x2014), chr(0x2013)
    d = make(dispatch_brief={"id": f"Asap tebal {em} dekat desa.", "en": f"Thick smoke {en} near village."})
    r = gate.run(d, EVIDENCE, INC)
    assert r.output["dispatch_brief"]["id"] == "Asap tebal, dekat desa."
    assert r.output["dispatch_brief"]["en"] == "Thick smoke, near village."


def test_evidence_list_comes_from_database_not_model():
    d = make(evidence=[{"id": "EV-777", "type": "image", "source": "made up", "retrieved_at": "x", "summary": "fake"}])
    r = gate.run(d, EVIDENCE, INC)
    assert [e["id"] for e in r.output["evidence"]] == ["EV-001", "EV-002", "EV-003", "EV-004"]
