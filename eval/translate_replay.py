"""Add the Indonesian twins to the three recorded replay cases without re-running them.

The recordings stay exactly as recorded (status, claims, numbers, gate result). Muse Spark 1.3 only translates
the English reader-facing text: claims, risk drivers, recommended actions, uncertainties. The evidence gate's
number check then runs on each translated claim against the recorded evidence, so a translated number that
does not match a tool value is corrected or marked the same way as in a live triage. The case records which
model translated and when.

Usage (repo root): MODEL_API_KEY=... .venv/Scripts/python eval/translate_replay.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import gate, meta_client  # noqa: E402
from backend.config import get_settings  # noqa: E402

CASES = ROOT / "replay" / "incidents"
SYSTEM = """Translate wildfire triage text from English into natural Indonesian for verifiers in Indonesia.
Keep every number, unit, date, ID, and name exactly as written. Use Indonesian terms (titik panas, tinggi, nominal,
rendah, tidak diketahui, arah angin dari). Field names such as burn_area_ha are not words for a reader: describe them.
Do not add, drop, or soften anything. Do not use dashes in sentences. Return the lists in the same order and length."""
LISTS = ("claims", "drivers", "actions", "uncertainties")
SCHEMA = {"type": "object", "additionalProperties": False, "required": list(LISTS),
          "properties": {k: {"type": "array", "items": {"type": "string"}} for k in LISTS}}


def translate(settings, src: dict, meter) -> dict:
    resp = meta_client.meta(settings).responses.create(
        model=meta_client.SPARK, reasoning={"effort": "low"},
        input=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps(src, ensure_ascii=False)}],
        text={"format": {"type": "json_schema", "name": "translation", "schema": SCHEMA, "strict": True}},
    )
    meter.spark(resp.usage)
    out = json.loads(resp.output_text)
    for k in LISTS:
        if len(out[k]) != len(src[k]):
            raise SystemExit(f"{k}: {len(src[k])} items in, {len(out[k])} out")
    return out


def main() -> None:
    settings = get_settings()
    if not settings.model_api_key:
        raise SystemExit("MODEL_API_KEY is not set")
    total = 0.0
    for path in sorted(CASES.glob("*/case.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        tri = json.loads(case["incident"]["triage_json"])
        if all(c.get("text_id") for c in tri["claims"]) and tri.get("recommended_actions_id"):
            print(f"{case['slug']}: already translated")
            continue
        src = {"claims": [c["text"] for c in tri["claims"]], "drivers": tri["risk"]["drivers"],
               "actions": tri["recommended_actions"], "uncertainties": tri["uncertainties"]}
        meter = meta_client.Meter()
        out = translate(settings, src, meter)
        by_id = {e["id"]: e for e in case["evidence"]}
        res = gate.GateResult(accepted=True, output=None)
        for i, c in enumerate(tri["claims"]):
            cited = [by_id[e] for e in c["evidence_ids"] if e in by_id]
            c["text_id"] = gate._content_filter(gate._check_numbers(out["claims"][i], cited, res, f"claims[{i}].id"), res, f"claims[{i}].id") or None
        tri["risk"]["drivers_id"] = out["drivers"]
        tri["recommended_actions_id"] = out["actions"]
        tri["uncertainties_id"] = out["uncertainties"]
        cost = sum(x["cost_usd"] or 0 for x in meter.calls)
        total += cost
        tri["translation"] = {"model": meta_client.SPARK, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "of": "the recorded English text; the recording itself is unchanged", "cost_usd": round(cost, 6),
                              "number_checks": res.corrections, "content_removals": res.violations}
        case["incident"]["triage_json"] = json.dumps(tri, ensure_ascii=False)
        case["translated_with"] = meta_client.SPARK
        path.write_text(json.dumps(case, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="")
        print(f"{case['slug']}: {len(tri['claims'])} claims, {len(res.corrections)} number check(s), ${cost:.4f}")
        for x in res.corrections:
            print("   ", x)
    print(f"total ${total:.4f}")


if __name__ == "__main__":
    main()
