"""Build the place search file (gazetteer) from a Geofabrik extract.

The map search box answers from this file: provinces, regencies and cities (kabupaten, kota), and districts
(kecamatan) from OSM administrative boundaries (admin_level 4, 5, 6) with a simplified outline and a bounding
box, plus named settlements (city, town, village, hamlet nodes). Every place carries the names of the regions
that contain it, so two villages with the same name can be told apart. Names are searched with SQLite FTS5.
Only places inside Indonesia are kept (the country relation, ISO3166-1 ID; or, if it does not assemble, the
provinces tagged ISO3166-2 ID-..): Geofabrik extracts reach into Timor-Leste, Malaysia, and Papua New Guinea.
Boundaries count only as relations: islands drawn as closed ways and tagged admin_level 5 are mapping errors
(269 "regencies" in Sulawesi Tengah), and a level 4 boundary must carry an ID- code ("Dusun Duwet" does not).
Relations that do not assemble into an area are named in the build log.

Usage (repo root): .venv/Scripts/python tools/build_places.py INPUT.osm.pbf OUTPUT.sqlite
Needs: pip install osmium shapely (build time only; the server reads the SQLite with the standard library).
Data (c) OpenStreetMap contributors, ODbL. The output is a derived database under the same license.
"""

import json
import sqlite3
import sys
import time
import zlib
from pathlib import Path

import osmium
import shapely
from shapely import wkb
from shapely.strtree import STRtree

LEVELS = {"4": "province", "5": "regency", "6": "district"}
PLACES = {"city", "town", "village", "hamlet"}
# Outline tolerance per level, in degrees: about 1 km, 300 m, 100 m. Enough to draw, never used to measure.
SIMPLIFY = {"4": 0.01, "5": 0.003, "6": 0.001}
# Order of results with the same match quality: wide regions first, hamlets last.
RANK = {("region", "province"): 0, ("region", "regency"): 1, ("region", "city"): 1, ("settlement", "city"): 2,
        ("region", "district"): 3, ("settlement", "town"): 4, ("settlement", "village"): 5, ("settlement", "hamlet"): 6}

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE places (
    rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, cat TEXT NOT NULL, kind TEXT NOT NULL, rank INTEGER NOT NULL,
    name TEXT NOT NULL, label TEXT NOT NULL, alt TEXT, lat REAL NOT NULL, lon REAL NOT NULL, bbox TEXT,
    district TEXT, regency TEXT, province TEXT, ctx TEXT
);
CREATE TABLE outlines (id TEXT PRIMARY KEY, geojson BLOB NOT NULL);
CREATE VIRTUAL TABLE places_fts USING fts5(name, alt, ctx, content='places', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2', prefix='2 3');
"""


def alt_names(tags) -> str:
    seen, out = {tags.get("name")}, []
    for k in ("name:id", "official_name", "alt_name", "short_name", "name:en"):
        for v in (tags.get(k) or "").split(";"):
            v = v.strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
    return " ; ".join(out)


def round_coords(geom, nd=5):
    return shapely.set_precision(geom, 10 ** -nd) if geom.is_valid else geom


def main(src: Path, out: Path) -> None:
    t0 = time.time()
    fab = osmium.geom.WKBFactory()
    regions, points, countries = [], [], []
    seen_rel = {}  # relation id -> (level, name) for every boundary relation in the file
    not_id = set()  # level 4 areas without an ID- code: assembled, left out on purpose
    n_bad = 0
    proc = (osmium.FileProcessor(str(src))
            .with_areas(osmium.filter.TagFilter(("boundary", "administrative")))
            .with_filter(osmium.filter.KeyFilter("place", "boundary")))
    for o in proc:
        tags = o.tags
        name = tags.get("name")
        if not name:
            continue
        if isinstance(o, osmium.osm.Relation):
            if tags.get("boundary") == "administrative" and tags.get("admin_level") in LEVELS:
                seen_rel[o.id] = (tags.get("admin_level"), name)
            continue
        if o.is_area():
            lvl = tags.get("admin_level")
            if tags.get("boundary") != "administrative" or o.from_way():
                continue
            if lvl == "2" and tags.get("ISO3166-1") == "ID":
                try:
                    countries.append(wkb.loads(fab.create_multipolygon(o), hex=True))
                except Exception:
                    pass
                continue
            if lvl not in LEVELS:
                continue
            if lvl == "4" and not (tags.get("ISO3166-2") or "").startswith("ID-"):
                not_id.add(o.orig_id())
                continue
            try:
                geom = wkb.loads(fab.create_multipolygon(o), hex=True)
            except Exception:  # incomplete relation in the extract
                n_bad += 1
                continue
            if not geom.is_valid:
                geom = geom.buffer(0)
            if geom.is_empty:
                n_bad += 1
                continue
            kind = LEVELS[lvl]
            official = tags.get("official_name") or ""
            # OSM names a regency "Kupang" and the city "Kupang" too; official_name says Kabupaten or Kota.
            if lvl == "5" and (name.lower().startswith("kota ") or official.lower().startswith("kota ")):
                kind = "city"
            label = official if official.lower().startswith(("provinsi ", "kabupaten ", "kota ", "kecamatan ")) else name
            if lvl == "4":  # provinces by their plain name: "Bali", not "Provinsi Bali" next to "Jawa Barat"
                label = label[9:] if label.lower().startswith("provinsi ") else label
            # Without an official_name (Batang, Ngawi, Biak Numfor) the label still says what the region is.
            prefix = {"regency": "Kabupaten ", "city": "Kota ", "district": "Kecamatan "}.get(kind)
            if prefix and not label.lower().startswith(prefix.lower()):
                label = prefix + label
            src_id = f"r{o.orig_id()}"
            regions.append({"id": src_id, "lvl": lvl, "kind": kind, "name": name, "label": label, "alt": alt_names(tags), "geom": geom,
                            "iso": tags.get("ISO3166-2") or ""})
        elif isinstance(o, osmium.osm.Node) and tags.get("place") in PLACES and o.location.valid():
            points.append({"id": f"n{o.id}", "kind": tags.get("place"), "name": name, "label": name, "alt": alt_names(tags),
                           "lat": o.location.lat, "lon": o.location.lon})
    t1 = time.time()
    print(f"read: {len(regions)} regions, {len(points)} settlements, {n_bad} broken areas, {len(countries)} country areas, {t1 - t0:.0f} s", flush=True)
    built = {int(r["id"][1:]) for r in regions} | not_id
    missing = sorted((lvl, name) for rid, (lvl, name) in seen_rel.items() if rid not in built)
    print(f"boundary relations that did not assemble: {len(missing)}; level 4 and 5: {[m for m in missing if m[0] in ('4', '5')]}", flush=True)

    # Indonesia only: a region or settlement counts when its inner point lies in the country (or an ID- province).
    provinces = [r for r in regions if r["lvl"] == "4"]
    if not countries and not provinces:
        raise SystemExit("neither the Indonesia relation nor an Indonesian province (ISO3166-2 ID-..) assembled")
    indo = STRtree(countries or [r["geom"] for r in provinces])
    print(f"Indonesia filter: {'country relation' if countries else 'provinces'}", flush=True)

    def inside(geoms):
        inp, _ = indo.query(geoms, predicate="within")
        return set(inp.tolist())

    for r in regions:
        r["pt"] = r["geom"].representative_point()
    keep = inside([r["pt"] for r in regions])
    dropped_r = len(regions) - len(keep)
    regions = [r for i, r in enumerate(regions) if i in keep]
    pts = list(shapely.points([(p["lon"], p["lat"]) for p in points])) if points else []
    keep = inside(pts)
    dropped_p = len(points) - len(keep)
    points = [p for i, p in enumerate(points) if i in keep]
    print(f"outside Indonesia: {dropped_r} regions, {dropped_p} settlements dropped", flush=True)

    # Containment, level by level: a point (or a region's inner point) gets the name of each region it falls in.
    by_level = {lvl: [r for r in regions if r["lvl"] == lvl] for lvl in LEVELS}
    trees = {lvl: STRtree([r["geom"] for r in rs]) for lvl, rs in by_level.items() if rs}
    field = {"4": "province", "5": "regency", "6": "district"}

    def contain(geoms, own_lvl=None):
        res = [dict() for _ in geoms]
        for lvl, tree in trees.items():
            if own_lvl is not None and int(lvl) >= int(own_lvl):
                continue
            inp, hit = tree.query(geoms, predicate="within")
            for i, j in zip(inp.tolist(), hit.tolist()):
                res[i].setdefault(field[lvl], by_level[lvl][j]["label"])
        return res

    for lvl, rs in by_level.items():
        if not rs:
            continue
        inner = [r["pt"] for r in rs]
        for r, p, par in zip(rs, inner, contain(inner, lvl)):
            r["lat"], r["lon"] = p.y, p.x
            r.update(par)
    pts = shapely.points([(p["lon"], p["lat"]) for p in points]) if points else []
    for p, par in zip(points, contain(list(pts))):
        p.update(par)
    t2 = time.time()
    print(f"containment: {t2 - t1:.0f} s", flush=True)

    if out.exists():
        out.unlink()
    conn = sqlite3.connect(out)
    conn.executescript(SCHEMA)
    rows, outlines = [], []

    def ctx(x):  # the regions around a place, searchable: "sukamaju lampung"
        return " ".join(v for v in (x.get("district"), x.get("regency"), x.get("province")) if v)

    for r in regions:
        w, s, e, n = r["geom"].bounds
        simple = round_coords(r["geom"].simplify(SIMPLIFY[r["lvl"]], preserve_topology=True))
        outlines.append((r["id"], zlib.compress(json.dumps(shapely.geometry.mapping(simple), separators=(",", ":")).encode(), 6)))
        rows.append((r["id"], "region", r["kind"], RANK[("region", r["kind"])], r["name"], r["label"], r["alt"], round(r["lat"], 5), round(r["lon"], 5),
                     f"{w:.5f},{s:.5f},{e:.5f},{n:.5f}", r.get("district"), r.get("regency"), r.get("province"), ctx(r)))
    for p in points:
        rows.append((p["id"], "settlement", p["kind"], RANK[("settlement", p["kind"])], p["name"], p["label"], p["alt"], round(p["lat"], 5), round(p["lon"], 5),
                     None, p.get("district"), p.get("regency"), p.get("province"), ctx(p)))
    conn.executemany("INSERT OR IGNORE INTO places (id, cat, kind, rank, name, label, alt, lat, lon, bbox, district, regency, province, ctx) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT OR IGNORE INTO outlines VALUES (?,?)", outlines)
    conn.execute("INSERT INTO places_fts(places_fts) VALUES ('rebuild')")
    counts = dict(conn.execute("SELECT cat || ':' || kind, COUNT(*) FROM places GROUP BY 1").fetchall())
    no_regency = conn.execute("SELECT COUNT(*) FROM places WHERE cat='settlement' AND regency IS NULL").fetchone()[0]
    meta = {"source": src.name, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "license": "ODbL 1.0, (c) OpenStreetMap contributors",
            "counts": json.dumps(counts), "settlements_without_regency": str(no_regency), "kind": "places"}
    conn.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    print(f"{counts}; settlements without a regency {no_regency}; {out.stat().st_size / 1e6:.1f} MB; {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
