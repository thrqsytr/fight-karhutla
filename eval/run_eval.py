"""Run the evaluation: three ways of calling each labelled cluster, scored against the dNBR labels.

  (a) rule baseline: every cluster with a nominal or high confidence hotspot is a fire.
  (b) agent before the gate: the model's own status.
  (c) agent after the gate and critique: what the app would show.

Each cluster gets its own throwaway database under Scripts/eval-cache/run/, never the production one. Ingest,
clustering, and enrichment are the app's own code: weather comes from the Open-Meteo archive at the cluster's
last detection, OSM from the offline extract (Overpass is treated as down, so runs are repeatable). The agent may
call get_hotspots, get_weather, and get_nearby_assets only. Sentinel-2 is the label source, so get_satellite_pair
is withheld; with no independent evidence the prompt's rule 3 caps a call at possible_fire.

Positive = likely_fire or possible_fire. needs_more_info is counted as not positive and also reported as abstention.
Burned area is not scored: the label itself is the dNBR area the agent could measure, so it would grade the label against itself.

Usage (repo root): .venv/Scripts/python eval/run_eval.py --provider baseten [--only EVAL-01,EVAL-02]
Results append to eval/results/<provider>.jsonl (a rerun skips clusters already done); --summary rewrites the tables.
"""

import argparse
import json
import math
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import agent, db, evidence, gate, ingest, meta_client, tools  # noqa: E402
from backend.config import Settings  # noqa: E402
from backend.sources import firms, osm  # noqa: E402

CACHE = ROOT.parent.parent / "Scripts" / "eval-cache"
CREDS = ROOT.parent.parent / "credentials"
RESULTS = ROOT / "eval" / "results"
ALLOWED = {"get_hotspots", "get_weather", "get_nearby_assets"}
POSITIVE = {"likely_fire", "possible_fire"}
OSM_FALLBACK_URL = "https://github.com/thrqsytr/fight-karhutla/releases/download/osm-fallback-20261027/osm_fallback.sqlite"


class EvalSettings(Settings):
    """App settings with one shared source cache and offline extract, and a database per cluster."""

    @property
    def cache_dir(self) -> Path:
        return CACHE / "app-cache"

    @property
    def osm_fallback_path(self) -> Path:
        return CACHE / "osm_fallback.sqlite"


def settings_for(cid: str, provider: str) -> EvalSettings:
    data = CACHE / "run" / provider / cid
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True)
    key = lambda name: (CREDS / name).read_text(encoding="utf-8").strip()  # noqa: E731
    return EvalSettings(_env_file=None, data_dir=data, fixture_mode=False, baseten_api_key=key("baseten-key.txt"),
                        model_api_key=key("meta-api-key.txt") if provider == "meta" else "")


def load_incident(settings: Settings, cluster: dict) -> str:
    """Ingest the cluster's hotspots and let the app cluster and enrich them, as it would have at the time."""
    db.init(settings.db_path)
    hs = [firms.Hotspot(id=f"{cluster['id']}-{i:03d}", source="VIIRS_SNPP_SP", lat=h["lat"], lon=h["lon"], acquired_at=h["at"],
                        satellite="N", instrument="VIIRS", confidence=h["confidence"], frp=h["frp"], daynight="")
          for i, h in enumerate(cluster["hotspots"])]
    with db.session(settings.db_path) as conn:
        ingest.store_hotspots(conn, hs)
        made = ingest.cluster_into_incidents(conn, now=datetime.fromisoformat(cluster["last_seen"]) + timedelta(minutes=1))
    inc_id = made["created"][0]
    status = evidence.enrich_incident(settings, inc_id)
    if any(not str(v).startswith("ok") for v in status.values()):
        print("  enrichment:", status, flush=True)
    return inc_id


def rule_baseline(cluster: dict) -> str:
    return "possible_fire" if any(h["confidence"] in ("n", "h") for h in cluster["hotspots"]) else "unlikely"


def run_one(cluster: dict, provider: str) -> dict:
    settings = settings_for(cluster["id"], provider)
    inc_id = load_incident(settings, cluster)
    inc, ev = agent._incident_input(settings, inc_id)
    meter = meta_client.Meter()
    ctx = tools.ToolContext(settings=settings, incident_id=inc_id, meter=meter)
    started = time.time()
    out = {"id": cluster["id"], "label": cluster["label"], "kind": cluster["kind"], "provider": provider, "rule": rule_baseline(cluster)}
    try:
        raw = agent.run_model(settings, inc, ev, ctx, provider=provider, allowed=ALLOWED)
        _, ev = agent._incident_input(settings, inc_id)
        res = gate.run(raw, ev, inc_id)
        retried = False
        if not res.accepted:
            retried = True
            raw = agent.run_model(settings, inc, ev, ctx, provider=provider, allowed=ALLOWED)
            _, ev = agent._incident_input(settings, inc_id)
            res = gate.run(raw, ev, inc_id)
        out["before_gate"] = raw.get("status") if isinstance(raw, dict) else None
        if res.accepted:
            final = res.output
            critique = agent._critique(settings, final, ev, meter)
            for d in critique["disputed"]:
                final["claims"][d["index"]]["disputed"] = d["reason"]
            if critique["brief_disputed"]:
                final["dispatch_brief"]["disputed"] = critique["brief_disputed"]
            if critique["disputed"]:
                final["confidence"] = round(max(0.0, final["confidence"] - agent.CONFIDENCE_PENALTY_PER_DISPUTE * len(critique["disputed"])), 3)
            out.update(after_gate=final["status"], confidence=final["confidence"], critique_disputed=len(critique["disputed"]),
                       critique_errors=critique["errors"], uncertainties=final["uncertainties"], brief_en=final["dispatch_brief"]["en"],
                       output=final, critique=critique)
        else:
            out.update(after_gate="needs_more_info", schema_failed=True)
        out.update(gate=res.to_dict(), retried=retried)
    except Exception as exc:  # recorded, not hidden: a failed call counts against the agent in the summary
        out.update(error=f"{type(exc).__name__}: {str(exc)[:300]}")
    out["seconds"] = round(time.time() - started, 1)
    out["tools"] = [t["tool"] for t in ctx.log]
    usage = meter.summary()
    out["usage"] = {"input_tokens": sum(c.get("input_tokens", 0) for c in usage["calls"]),
                    "output_tokens": sum(c.get("output_tokens", 0) for c in usage["calls"]), "cost_usd": usage["cost_usd"]}
    return out


def scores(rows: list[dict], key: str) -> dict:
    tp = sum(1 for r in rows if r.get(key) in POSITIVE and r["label"] == "burned")
    fp = sum(1 for r in rows if r.get(key) in POSITIVE and r["label"] == "not_burned")
    fn = sum(1 for r in rows if r.get(key) not in POSITIVE and r["label"] == "burned")
    tn = sum(1 for r in rows if r.get(key) not in POSITIVE and r["label"] == "not_burned")
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec, "f1": f1,
            "accuracy": (tp + tn) / len(rows) if rows else None, "abstained": sum(1 for r in rows if r.get(key) == "needs_more_info")}


def summary(provider: str) -> dict:
    rows = [json.loads(line) for line in (RESULTS / f"{provider}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    claims = sum(r.get("gate", {}).get("claims_total", 0) for r in rows)
    passed = sum(r.get("gate", {}).get("claims_passed", 0) for r in rows)
    secs = sorted(r["seconds"] for r in rows if "error" not in r)
    costs = [r["usage"]["cost_usd"] for r in rows if r["usage"]["cost_usd"] is not None]
    s = {
        "provider": provider, "model": meta_client.SPARK if provider == "meta" else meta_client.SPARK_OPEN, "n": len(rows),
        "labels": {k: sum(1 for r in rows if r["label"] == k) for k in ("burned", "not_burned")},
        "rule": scores(rows, "rule"), "before_gate": scores(rows, "before_gate"), "after_gate": scores(rows, "after_gate"),
        "errors": sum(1 for r in rows if "error" in r), "schema_failed": sum(1 for r in rows if r.get("schema_failed")),
        "claims_total": claims, "claims_passed": passed, "grounding_rate": passed / claims if claims else None,
        "corrections": sum(len(r.get("gate", {}).get("corrections", [])) for r in rows),
        "downgrades": sum(len(r.get("gate", {}).get("downgrades", [])) for r in rows),
        "critique_disputed": sum(r.get("critique_disputed", 0) for r in rows),
        "seconds_median": secs[len(secs) // 2] if secs else None, "seconds_p90": secs[min(len(secs) - 1, math.ceil(len(secs) * 0.9) - 1)] if secs else None,
        "tokens_per_incident": round(sum(r["usage"]["input_tokens"] + r["usage"]["output_tokens"] for r in rows) / max(1, len(rows))),
        # Baseten runs on hackathon credit that is not priced per token, so its cost is unknown rather than zero.
        "cost_usd_total": round(sum(costs), 4) if provider == "meta" and costs else None,
    }
    import csv

    with (ROOT / "eval" / "labels.csv").open(encoding="utf-8") as f:
        agreed = {r["id"] for r in csv.DictReader(f) if r["reviewed_by_eye"] == "agree"}
    sub = [r for r in rows if r["id"] in agreed]
    s["eye_reviewed"] = {"n": len(sub), "rule": scores(sub, "rule"), "before_gate": scores(sub, "before_gate"), "after_gate": scores(sub, "after_gate")}
    (RESULTS / f"{provider}_summary.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    pct = lambda v: "n/a" if v is None else f"{v * 100:.0f}%"  # noqa: E731
    lines = [f"| {provider} ({s['model']}), n={s['n']} | Precision | Recall | F1 | Accuracy | Abstained |", "|---|---|---|---|---|---|"]
    for k, name in (("rule", "(a) Rule: any nominal or high hotspot"), ("before_gate", "(b) Agent before gate"), ("after_gate", "(c) Agent after gate and critique")):
        m = s[k]
        lines.append(f"| {name} | {pct(m['precision'])} | {pct(m['recall'])} | {pct(m['f1'])} | {pct(m['accuracy'])} | {m['abstained']} |")
    lines += ["", f"Only the {s['eye_reviewed']['n']} clusters whose label also passed a visual check:", "",
              "| Baseline | Precision | Recall | F1 | Accuracy | Abstained |", "|---|---|---|---|---|---|"]
    for k, name in (("rule", "(a) Rule"), ("before_gate", "(b) Agent before gate"), ("after_gate", "(c) Agent after gate and critique")):
        m = s["eye_reviewed"][k]
        lines.append(f"| {name} | {pct(m['precision'])} | {pct(m['recall'])} | {pct(m['f1'])} | {pct(m['accuracy'])} | {m['abstained']} |")
    lines += ["", f"Grounding rate {pct(s['grounding_rate'])} ({s['claims_passed']} of {s['claims_total']} claims kept), "
              f"{s['corrections']} corrections, {s['downgrades']} downgrades, {s['critique_disputed']} disputed by the critic, "
              f"{s['errors']} errors, {s['schema_failed']} schema failures. Median {s['seconds_median']} s per incident (p90 {s['seconds_p90']} s), "
              f"about {s['tokens_per_incident']} tokens per incident" + (f", ${s['cost_usd_total']} in total (${s['cost_usd_total'] / max(1, s['n']):.4f} per incident)."
                                                                if s["cost_usd_total"] is not None else ", cost not metered (Baseten credit).")]
    (RESULTS / f"{provider}_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=("baseten", "meta"), required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--summary", action="store_true", help="only rewrite the summary from existing results")
    a = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    if not a.summary:
        fallback = CACHE / "osm_fallback.sqlite"
        if not fallback.exists():
            osm.ensure_fallback(fallback, OSM_FALLBACK_URL)
        osm._overpass_down_until = float("inf")  # offline extract only: repeatable, and no load on public Overpass
        clusters = json.loads((ROOT / "eval" / "dataset" / "clusters.json").read_text(encoding="utf-8"))
        only = {x for x in a.only.split(",") if x}
        out = RESULTS / f"{a.provider}.jsonl"
        done = {json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines() if line.strip()} if out.exists() else set()
        todo = [c for c in clusters if not (only and c["id"] not in only) and c["id"] not in done]
        lock = threading.Lock()

        def one(c: dict) -> None:
            r = run_one(c, a.provider)
            with lock, out.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(c["id"], c["label"], "rule", r["rule"], "before", r.get("before_gate"), "after", r.get("after_gate"), f"{r['seconds']}s", r.get("error", ""), flush=True)

        # Each cluster has its own database, so a few run at once; seconds are still measured per incident.
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(ex.map(one, todo))
    summary(a.provider)


if __name__ == "__main__":
    main()
