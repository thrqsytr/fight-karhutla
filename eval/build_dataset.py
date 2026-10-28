"""Build the evaluation set: about 30 hotspot clusters from the 2025 dry season, labelled by Sentinel-2 dNBR.

Steps:
1. FIRMS VIIRS S-NPP standard processing (archive) for four fire-prone regions and three dates.
2. Cluster as the app does (2 km, 24 h). Pick candidates on both sides: large or hot clusters (likely burned)
   and single low-energy or static-source detections (likely not), so the set is not all positives.
3. Label each with dNBR: a clear Sentinel-2 pair before, and within 40 days after, the first detection.
   Only ground within 500 m of a hotspot counts (a VIIRS pixel is 375 m): the area of interest is at least
   2 by 2 km, and over a dry season some of it changes for other reasons, so a whole-area count marks
   almost every cluster burned. burned: at least 2 ha at dNBR >= 0.27 near the hotspots; not_burned: under
   0.5 ha there with at least 60% of it clear; anything else (hazy, cloudy, in between) is dropped rather than guessed.
4. Each candidate's label is cached, so a rerun resumes. Write eval/dataset/clusters.json, eval/labels.csv, and a review sheet per cluster (before, after, burn mask)
   so a person can check each label by eye.

Usage (repo root, FIRMS_MAP_KEY set): .venv/Scripts/python eval/build_dataset.py [TARGET=30]
"""

import csv
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
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
NEAR_M = 500
NEAR_CLEAR = 0.6
OUT = ROOT / "eval"
CACHE = ROOT.parent.parent / "Scripts" / "eval-cache"
WORKERS = 8
MAX_TRIES = 320
# From a slow link a 10 m true-color block (about 3 MB) takes a minute; review sheets read the 20 m overview instead.
sentinel.HTTP_TIMEOUT_S = 180
sentinel.RGB_SCALE = 1


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


def near(c: dict, render: dict) -> dict:
    """Burned and clear ground within NEAR_M of any hotspot of the cluster."""
    import numpy as np
    from rasterio.warp import transform

    b, (rows, cols) = render["bounds"], render["shape"]
    xs, ys = transform("EPSG:4326", f"EPSG:{render['epsg']}", [h["lon"] for h in c["hotspots"]], [h["lat"] for h in c["hotspots"]])
    cx = b[0] + (np.arange(cols) + 0.5) * sentinel.PIXEL_M
    cy = b[3] - (np.arange(rows) + 0.5) * sentinel.PIXEL_M
    gx, gy = np.meshgrid(cx, cy)
    mask = np.zeros((rows, cols), dtype=bool)
    for x, y in zip(xs, ys):
        mask |= (gx - x) ** 2 + (gy - y) ** 2 <= NEAR_M ** 2
    px_ha = sentinel.PIXEL_M ** 2 / 10_000
    burned, valid = render["burned"] & mask, render["valid"] & mask
    return {"radius_m": NEAR_M, "area_ha": round(int(mask.sum()) * px_ha, 2), "clear_frac": round(float(valid.sum() / max(1, mask.sum())), 3),
            "burned_ha": round(int(burned.sum()) * px_ha, 2)}


def cached_read(href: str, bounds: tuple, shape: tuple, resampling: str, bands: int = 1):
    """sentinel.read_window with every window kept on disk: a change of label rule then needs no new downloads."""
    import hashlib

    import numpy as np

    key = hashlib.sha1(repr((href, tuple(round(b, 3) for b in bounds), tuple(shape), resampling, bands)).encode()).hexdigest()
    path = CACHE / "windows" / f"{key}.npy"
    if path.exists():
        return np.load(path)
    a = sentinel.read_window(href, bounds, shape, resampling, bands)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npy")
    np.save(tmp, a)
    tmp.replace(path)
    return a


def label(c: dict) -> dict:
    first = datetime.fromisoformat(c["first_seen"])
    pts = [(h["lat"], h["lon"]) for h in c["hotspots"]]
    pair = sentinel.satellite_pair(pts, first, now=first + timedelta(days=POST_DAYS), cache_dir=CACHE, reader=cached_read)
    render = pair.pop("_render", None)
    if pair["status"] != "ok":
        return {"label": None, "pair": pair, "render": render}
    pair["near"] = near(c, render)
    ha, clear = pair["near"]["burned_ha"], pair["near"]["clear_frac"]
    if ha >= BURNED_HA:
        lab = "burned"
    elif ha < CLEAN_HA and clear >= NEAR_CLEAR:
        lab = "not_burned"
    else:
        lab = None
    return {"label": lab, "pair": pair, "render": render}


def labelled(c: dict) -> dict:
    """Label one candidate, once: the result and its review images are kept in the cache, so reruns skip the reads."""
    key = f"{c['region']}_{c['day']}_{c['lat']}_{c['lon']}"
    folder = CACHE / "labels-v3"
    path = folder / f"{key}.json"
    if path.exists():
        out = json.loads(path.read_text(encoding="utf-8"))
        if not out["label"] or (folder / f"{key}_overlay.png").exists():
            return out
        # Labelled, but its review images failed last time: read the pair again for them.
    try:
        r = label(c)
    except SourceError as exc:
        return {"key": key, "label": None, "status": "read error", "error": str(exc)[:200]}  # not cached: worth a retry
    out = {"key": key, "label": r["label"], "status": r["pair"]["status"], "pair": r["pair"]}
    if r["render"]:  # masks kept, so a change of label rule needs no new reads
        import numpy as np

        np.savez_compressed(folder / f"{key}.npz", burned=r["render"]["burned"], valid=r["render"]["valid"],
                            bounds=np.array(r["render"]["bounds"]), epsg=r["render"]["epsg"])
    path.write_text(json.dumps(out), encoding="utf-8")
    if r["label"]:
        sheets(key, r["render"])
    return out


def sheets(key: str, render: dict) -> bool:
    """Review images for one labelled candidate; a failed read leaves the label and is retried on the next run."""
    try:
        for k, png in sentinel.render_pngs(render).items():
            if k in ("pre", "post", "overlay"):
                (CACHE / "labels-v3" / f"{key}_{k}.png").write_bytes(png)
        return True
    except SourceError as exc:
        print("sheet read failed", key, str(exc)[:120], flush=True)
        return False


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
    (CACHE / "labels-v3").mkdir(parents=True, exist_ok=True)
    sheet_dir = OUT / "dataset" / "sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    for old in sheet_dir.glob("*.png"):
        old.unlink()
    per_label = (target + 1) // 2
    kept, counts = [], {"burned": 0, "not_burned": 0}
    # Reads are network bound (COG range requests to us-west-2), so a few candidates run at once.
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i in range(0, len(order), WORKERS * 2):
            if min(counts.values()) >= per_label or i >= MAX_TRIES:
                break  # past MAX_TRIES the set stays unbalanced and the summary says so
            batch = order[i:i + WORKERS * 2]
            for c, r in zip(batch, ex.map(labelled, batch)):
                lab = r["label"]
                tried = f"{c['region']} {c['day']} {kind(c)} n={len(c['hotspots'])}"
                if lab is None or counts[lab] >= per_label:
                    print("skip", tried, r["status"], ((r.get("pair") or {}).get("near") or {}).get("burned_ha"), flush=True)
                    continue
                cid = f"EVAL-{len(kept) + 1:02d}"
                counts[lab] += 1
                for k in ("pre", "post", "overlay"):
                    src = CACHE / "labels-v3" / f"{r['key']}_{k}.png"
                    if src.exists():  # missing when the true-color read failed; a rerun tries it again
                        (sheet_dir / f"{cid}_{k}.png").write_bytes(src.read_bytes())
                kept.append({**c, "id": cid, "kind": kind(c), "label": lab, "dnbr": r["pair"]})
                print("keep", cid, tried, lab, r["pair"]["near"]["burned_ha"], "near;", r["pair"]["burned_area_ha"], "in area", flush=True)
    (OUT / "dataset" / "clusters.json").write_text(json.dumps(kept, indent=1), encoding="utf-8")
    with (OUT / "labels.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "label", "burned_ha_near", "clear_frac_near", "burned_ha_area", "kind", "region", "first_seen", "hotspots", "max_frp", "pre_scene", "post_scene", "reviewed_by_eye"])
        for k in kept:
            w.writerow([k["id"], k["label"], k["dnbr"]["near"]["burned_ha"], k["dnbr"]["near"]["clear_frac"], k["dnbr"]["burned_area_ha"], k["kind"], k["region"], k["first_seen"], len(k["hotspots"]),
                        max((h["frp"] or 0) for h in k["hotspots"]), k["dnbr"]["pre"]["id"], k["dnbr"]["post"]["id"], ""])
    print("kept", len(kept), counts)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
