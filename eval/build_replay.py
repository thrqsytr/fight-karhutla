"""Record the three replay cases from the evaluation set into replay/incidents/<slug>/.

Each case runs the app's real pipeline once, in a throwaway database: ingest, clustering, enrichment
(Open-Meteo archive, offline OSM), then a full triage with gate and critique in which the agent may also call
get_satellite_pair. The satellite pair is taken as it was 40 days after the first detection, as in the labels,
so the replay shows what the system would have said then. The result is written as files with the incident
ID replaced by {INC}; backend/replay.py loads them without network or model calls.

Usage (repo root): .venv/Scripts/python eval/build_replay.py --burned EVAL-07 --false-positive EVAL-12 --needs-more-info EVAL-20 [--provider baseten]
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
import run_eval  # noqa: E402
from backend import agent, db, meta_client, replay, satellite, tools  # noqa: E402
from backend.sources import osm, sentinel  # noqa: E402

OUT = ROOT / "replay" / "incidents"
POST_DAYS = 40
ALLOWED = run_eval.ALLOWED | {"get_satellite_pair"}
TEXT = {
    "burned": ({"id": "Jelas terbakar", "en": "Clearly burned"},
               {"id": "Titik panas yang bekas terbakarnya terlihat di Sentinel-2. Agen membaca pasangan citra dan luas dNBR dihitung kode.",
                "en": "Hotspots whose burn scar shows on Sentinel-2. The agent reads the image pair and code computes the dNBR area."}),
    "false_positive": ({"id": "Positif palsu", "en": "False positive"},
                       {"id": "Titik panas dengan keyakinan nominal, tetapi Sentinel-2 hampir tidak menemukan bekas terbakar. Agen mengklaim likely_fire; gerbang bukti menurunkannya ke possible_fire karena luas terukur di bawah 1 ha.",
                        "en": "A nominal-confidence hotspot, but Sentinel-2 finds almost no burn scar. The agent claimed likely_fire; the evidence gate lowered it to possible_fire because the measured burn is under 1 ha."}),
    "needs_more_info": ({"id": "Butuh info tambahan", "en": "Needs more info"},
                        {"id": "Citra setelah kejadian tertutup awan atau asap, jadi luas tidak diklaim dan agen meminta bukti lain.",
                         "en": "The after image is covered by cloud or smoke, so no area is claimed and the agent asks for other evidence."}),
}


def find_cluster(ref: str) -> dict:
    clusters = {c["id"]: c for c in json.loads((ROOT / "eval" / "dataset" / "clusters.json").read_text(encoding="utf-8"))}
    if ref in clusters:
        return clusters[ref]
    # A candidate that got no label (hazy or cloudy pair) is referred to by its label-cache key.
    import os

    import build_dataset

    cached = build_dataset.CACHE / "labels-v3" / f"{ref}.json"
    if not cached.exists():
        raise SystemExit(f"{ref} is neither in eval/dataset/clusters.json nor a label-cache key")
    pair = json.loads(cached.read_text(encoding="utf-8"))
    for c in build_dataset.candidates(os.environ["FIRMS_MAP_KEY"]):
        if f"{c['region']}_{c['day']}_{c['lat']}_{c['lon']}" == ref:
            return {**c, "id": ref, "kind": build_dataset.kind(c), "label": pair["status"], "dnbr": pair.get("pair") or {}}
    raise SystemExit(f"{ref}: candidate not found")


def record(role: str, cluster: dict, provider: str) -> Path:
    slug = role.replace("_", "-")
    c = {**cluster, "id": "REPLAY"}
    settings = run_eval.settings_for(f"replay-{slug}", provider)
    inc_id = run_eval.load_incident(settings, c)
    then = datetime.fromisoformat(cluster["first_seen"]) + timedelta(days=POST_DAYS)
    # The tool asks for the pair "now"; for a historical case "now" is 40 days after the first detection.
    collect = satellite.collect
    # Looked up first, as a verifier's "check Sentinel-2" would: the agent then sees it among the stored evidence.
    print(role, "satellite:", collect(settings, inc_id, now=then), flush=True)
    tools.satellite.collect = lambda s, i, **k: collect(s, i, now=then, **k)
    try:
        r = agent.triage(settings, inc_id, provider=provider, allowed=ALLOWED)
    finally:
        tools.satellite.collect = collect
    print(role, inc_id, r, flush=True)
    if r["outcome"] != "ok":
        raise SystemExit(f"{role}: triage {r['outcome']}: {r['error']}")

    with db.session(settings.db_path) as conn:
        inc = dict(conn.execute("SELECT * FROM incidents WHERE id=?", (inc_id,)).fetchone())
        hs = [dict(h) for h in conn.execute("SELECT * FROM hotspots WHERE incident_id=? ORDER BY acquired_at, id", (inc_id,))]
        ev = db.list_evidence(conn, inc_id)
        runs = [dict(x) for x in conn.execute("SELECT * FROM agent_runs WHERE incident_id=? ORDER BY id", (inc_id,))]
    for k in ("id", "created_at", "updated_at", "simulation"):
        inc.pop(k, None)
    for h in hs:
        h.pop("incident_id", None)
        h.pop("ingested_at", None)
    for x in runs:
        x.pop("id", None)
        x.pop("incident_id", None)
        x["detail"] = json.loads(x.pop("detail_json") or "null")
    for e in ev:
        e.pop("incident_id", None)
    title, note = TEXT[role]
    model = meta_client.SPARK_OPEN if provider == "baseten" else meta_client.SPARK
    case = {"slug": slug, "role": role, "title": title, "note": note, "recorded_with": model,
            "recorded_at": db.utcnow(), "source": {"eval_id": cluster["id"], "label": cluster["label"], "dnbr_near": cluster["dnbr"].get("near")},
            "incident": inc, "hotspots": hs, "evidence": ev, "runs": runs}
    text = json.dumps(case, ensure_ascii=False, indent=1).replace(inc_id, replay.TOKEN).replace("REPLAY-", replay.TOKEN + "-H")
    folder = OUT / slug
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / "case.json").write_text(text, encoding="utf-8")
    images = satellite.image_dir(settings.data_dir, inc_id)
    if images.exists():
        shutil.copytree(images, folder / "satellite")
    return folder


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--burned", required=True)
    ap.add_argument("--false-positive", required=True)
    ap.add_argument("--needs-more-info", required=True)
    ap.add_argument("--provider", choices=("baseten", "meta"), default="baseten")
    a = ap.parse_args()
    fallback = run_eval.CACHE / "osm_fallback.sqlite"
    if not fallback.exists():
        osm.ensure_fallback(fallback, run_eval.OSM_FALLBACK_URL)
    osm._overpass_down_until = float("inf")
    # Same slow-link settings as the label builder; true color is read from the 20 m overview.
    sentinel.HTTP_TIMEOUT_S = 180
    sentinel.RGB_SCALE = 1
    for role, ref in (("burned", a.burned), ("false_positive", a.false_positive), ("needs_more_info", a.needs_more_info)):
        # A recording whose triage failed the schema twice is taken again (up to three times); the replay is meant to show
        # a completed triage. Failures stay counted in the evaluation, which never retries beyond the app's own one retry.
        for attempt in range(3):
            try:
                print("recorded", record(role, find_cluster(ref), a.provider))
                break
            except SystemExit as exc:
                print("attempt", attempt + 1, "failed:", exc, flush=True)
        else:
            raise SystemExit(f"{role}: no completed triage in three recordings")


if __name__ == "__main__":
    main()
