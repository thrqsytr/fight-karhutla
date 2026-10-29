"""Admin and verifier panel (owner review round 2): the queue, the extra overview numbers, the settings log, jobs, CSV."""

import time

from fastapi.testclient import TestClient

from backend import admin_panel, db

AUTH = {"Authorization": "Bearer test-token"}


def client(settings, code="exec-code"):
    settings.execution_code = code
    from backend.main import app

    return TestClient(app)


def _triaged(settings, status="possible_fire"):
    with db.session(settings.db_path) as conn:
        inc = conn.execute("SELECT id FROM incidents ORDER BY id LIMIT 1").fetchone()[0]
        conn.execute("UPDATE incidents SET status=?, triage_json='{}', simulation=0 WHERE id=?", (status, inc))  # a real incident
    return inc


def test_queue_lists_ai_recommendations_until_a_verifier_decides(settings):
    with client(settings) as c:
        assert c.get("/api/admin/queue").status_code == 401
        inc = _triaged(settings)
        q = c.get("/api/admin/queue", headers=AUTH).json()
        assert [a["id"] for a in q["awaiting_decision"]] == [inc] and q["count"] >= 1
        assert c.post(f"/api/incidents/{inc}/verdict", json={"decision": "approve"}, headers=AUTH).status_code == 200
        q = c.get("/api/admin/queue", headers=AUTH).json()
        assert q["awaiting_decision"] == [] and q["recent_decisions"][0]["incident_id"] == inc
        # A revoked decision puts it back in the queue.
        c.post(f"/api/incidents/{inc}/verdict", json={"decision": "revoke", "note": "wrong place"}, headers=AUTH)
        q = c.get("/api/admin/queue", headers=AUTH).json()
        assert [a["id"] for a in q["awaiting_decision"]] == [inc] and q["recent_decisions"][0]["note"] == "wrong place"


def test_overview_has_status_coverage_offline_file_and_drone_counts(settings):
    with client(settings) as c:
        o = c.get("/api/admin/overview", headers=AUTH).json()
    total = sum(o["status_counts"].values()) + o["simulated_incidents"]
    assert total == o["counts"]["incidents"]  # real by status, plus the simulated ones apart
    assert set(o["coverage"]) == {"eligible", "enriched", "pending", "failed_by_source"}
    assert o["coverage"]["pending"] == o["coverage"]["eligible"] - o["coverage"]["enriched"]
    assert o["offline"]["present"] is False and "overpass_down" in o["offline"]
    assert o["drone"]["proposals"] == {} and o["settings_log"] == [] and o["jobs"] == {}


def test_settings_changes_are_logged_with_old_and_new(settings):
    with client(settings) as c:
        c.put("/api/admin/settings", json={"values": {"weather_per_run": 55}, "code": "exec-code"}, headers=AUTH)
        log = c.get("/api/admin/overview", headers=AUTH).json()["settings_log"]
    assert log[0]["key"] == "weather_per_run" and log[0]["old"] == "40" and log[0]["new"] == "55"


def test_jobs_need_the_code_and_run_in_the_background(settings):
    with client(settings) as c:
        assert c.post("/api/admin/jobs/enrich", json={"code": "wrong"}, headers=AUTH).status_code == 403
        assert c.post("/api/admin/jobs/fetch", json={"code": "exec-code"}, headers=AUTH).status_code == 409  # fixture mode
        assert c.post("/api/admin/jobs/other", json={"code": "exec-code"}, headers=AUTH).status_code == 422
        r = c.post("/api/admin/jobs/enrich", json={"code": "exec-code"}, headers=AUTH)
        assert r.status_code == 202 and r.json()["started"] is True
        for _ in range(50):
            job = c.get("/api/admin/overview", headers=AUTH).json()["jobs"].get("enrich", {})
            if job.get("state") != "running":
                break
            time.sleep(0.1)
    assert job["state"] == "done" and isinstance(job["result"]["complete"], int)
    admin_panel._jobs.clear()


def test_csv_export_for_verifiers(settings):
    with client(settings) as c:
        assert c.get("/api/admin/export/incidents.csv").status_code == 401
        r = c.get("/api/admin/export/incidents.csv", headers=AUTH)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert r.text.splitlines()[0].startswith("id,trigger,status") and len(r.text.splitlines()) > 1
        assert c.get("/api/admin/export/verdicts.csv", headers=AUTH).text.splitlines()[0].startswith("id,incident_id,decision")
        assert c.get("/api/admin/export/secrets.csv", headers=AUTH).status_code == 422
