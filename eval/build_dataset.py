"""Build the evaluation set: about 30 hotspot clusters from the 2025 dry season, labelled by Sentinel-2 dNBR.

Steps:
1. FIRMS VIIRS S-NPP standard processing (archive) for four fire-prone regions and three dates.
2. Cluster as the app does (2 km, 24 h). Pick candidates on both sides: large or hot clusters (likely burned)
   and single low-energy or static-source detections (likely not), so the set is not all positives.
3. Label each with dNBR: a clear Sentinel-2 pair before, and within 40 days after, the first detection.
   burned: at least 2 ha at dNBR >= 0.27; not_burned: under 0.5 ha with at least half the area clear;
   anything else (hazy, cloudy, in between) is dropped rather than guessed.
4. Write eval/dataset/clusters.json, eval/labels.csv, and a review sheet per cluster (before, after, burn mask)
   so a person can check each label by eye.

Usage (repo root, FIRMS_MAP_KEY set): .venv/Scripts/python eval/build_dataset.py [TARGET=30]
"""

import csv
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.geo import Point, centroid, cluster_points  # noqa: E402
from backend.sources import firms, sentinel  # noqa: E402
from backend.sources.http import SourceError  # noqa: E402

REGIONS = {
    "kalbar": (108.8, -3.0, 112.0, 1.5),
    "kalteng": (111.5, -3.5, 115.5, 0.0),
    "riau": (100.0, -1.0, 103.8, 2.5),
    "sumsel_jambi": (102.5, -4.5, 106.2, -1.0),
}
DATES = ("2025-08-28", "2025-09-12", "2025-09-26")
SOURCE = "VIIRS_SNPP_SP"
POST_DAYS = 40
BURNED_HA, CLEAN_HA = 2.0, 0.5
OUT = ROOT / "eval"
CACHE = ROOT.parent.parent / "Scripts" / "eval-cache"


def fetch(region: str, day: str, key: str) -> list[dict]:
    w, s, e, n = REGIONS[region]
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{SOURCE}/{w},{s},{e},{n}/2/{day}"
    cache = CACHE / "firms" / f"{region}_{day}.csv"
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(httpx.get(url, timeout=60).text, encoding="utf-8")
    text = cache.read_text(encoding="utf-8")
    rows = list(csv.DictReader(text.splitlines()))
    hs = firms.parse_csv(text, SOURCE)
    # FIRMS archive "type": 0 vegetation fire, 2 other static land source. Hotspot is frozen, so it rides alongside.
    return [(h, r.get("type")) for h, r in zip(hs, rows)]


def candidates(key: str) -> list[dict]:
    out = []
    for region in REGIONS:
        for day in DATES:
            pairs = fetch(region, day, key)
            hs = [h for h, _ in pairs]
            types = {h.id: t for h, t in pairs}
            by_id = {h.id: h for h in hs}
            pts = [Point(h.id, h.lat, h.lon, datetime.fromisoformat(h.acquired_at)) for h in hs]
            for cl in cluster_points(pts):
                members = [by_id[p.id] for p in cl]
                lat, lon = centroid(cl)
                out.append({
                    "region": region, "day": day, "lat": round(lat, 5), "lon": round(lon, 5),
                    "first_seen": min(h.acquired_at for h in members), "last_seen": max(h.acquired_at for h in members),
                    "hotspots": [{"lat": h.lat, "lon": h.lon, "at": h.acquired_at, "confidence": h.confidence, "frp": h.frp, "type": types[h.id]}
                                 for h in members],
                })
    return out


def kind(c: dict) -> str:
    hs = c["hotspots"]
    max_frp = max((h["frp"] or 0) for h in hs)
    if any(h["type"] == "2" for h in hs):
        return "static_source"
    if len(hs) >= 5 or max_frp >= 20:
        return "large"
    if len(hs) == 1 and max_frp < 5:
        return "small"
    return "medium"


def label(c: dict) -> dict:
    first = datetime.fromisoformat(c["first_seen"])
    pts = [(h["lat"], h["lon"]) for h in c["hotspots"]]
    pair = sentinel.satellite_pair(pts, first, now=first + timedelta(days=POST_DAYS), cache_dir=CACHE)
    render = pair.pop("_render", None)
    ha = pair.get("burned_area_ha")
    clear = pair.get("valid_pixel_count", 0) / max(1, pair.get("aoi_pixel_count", 1))
    if pair["status"] != "ok":
        lab = None
    elif ha >= BURNED_HA:
        lab = "burned"
    elif ha < CLEAN_HA and clear >= 0.5:
        lab = "not_burned"
    else:
        lab = None
    return {"label": lab, "pair": pair, "render": render}


def main(target: int) -> None:
    key = os.environ["FIRMS_MAP_KEY"]
    pool = candidates(key)
    groups = {}
    for c in pool:
        groups.setdefault(kind(c), []).append(c)
    print({k: len(v) for k, v in groups.items()})
    rng = random.Random(20251028)
    for v in groups.values():
        rng.shuffle(v)
    # Interleave so likely positives and likely negatives are tried in turn.
    order = []
    while any(groups.values()):
        for k in ("large", "small", "static_source", "medium"):
            if groups.get(k):
                order.append(groups[k].pop())
    (OUT / "dataset" / "sheets").mkdir(parents=True, exist_ok=True)
    kept, counts = [], {"burned": 0, "not_burned": 0}
    for c in order:
        if len(kept) >= target:
            break
        try:
            r = label(c)
            pngs = sentinel.render_pngs(r["render"]) if r["label"] else None
        except SourceError as exc:
            print("skip", c["region"], c["day"], "read error", str(exc)[:120])
            continue
        lab = r["label"]
        tried = f"{c['region']} {c['day']} {kind(c)} n={len(c['hotspots'])}"
        if lab is None or counts[lab] >= target // 2 + 2:
            print("skip", tried, r["pair"]["status"], r["pair"].get("burned_area_ha"))
            continue
        cid = f"EVAL-{len(kept) + 1:02d}"
        counts[lab] += 1
        for k in ("pre", "post", "overlay"):
            (OUT / "dataset" / "sheets" / f"{cid}_{k}.png").write_bytes(pngs[k])
        kept.append({**c, "id": cid, "kind": kind(c), "label": lab, "dnbr": r["pair"]})
        print("keep", cid, tried, lab, r["pair"]["burned_area_ha"])
    (OUT / "dataset" / "clusters.json").write_text(json.dumps(kept, indent=1), encoding="utf-8")
    with (OUT / "labels.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "label", "dnbr_burned_ha", "kind", "region", "first_seen", "hotspots", "max_frp", "pre_scene", "post_scene", "reviewed_by_eye"])
        for k in kept:
            w.writerow([k["id"], k["label"], k["dnbr"]["burned_area_ha"], k["kind"], k["region"], k["first_seen"], len(k["hotspots"]),
                        max((h["frp"] or 0) for h in k["hotspots"]), k["dnbr"]["pre"]["id"], k["dnbr"]["post"]["id"], ""])
    print("kept", len(kept), counts)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
