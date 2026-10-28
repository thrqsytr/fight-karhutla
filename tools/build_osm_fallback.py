"""Build the offline OpenStreetMap fallback from a Geofabrik extract.

When every public Overpass endpoint fails, backend/sources/osm.py answers from this file instead, with the
same features the Overpass query asks for: places (village, hamlet, town, city), main roads (trunk, primary,
secondary, tertiary), rivers, water bodies, and fire stations. Lines are simplified to about 20 m, which
moves a nearest distance by at most that much against kilometre scale radii.

Usage (repo root): .venv/Scripts/python tools/build_osm_fallback.py INPUT.osm.pbf OUTPUT.sqlite
Needs: pip install osmium shapely (build time only; the server reads the SQLite with the standard library).
Data (c) OpenStreetMap contributors, ODbL. The output is a derived database under the same license.
"""

import math
import sqlite3
import struct
import sys
import time
import zlib
from pathlib import Path

import osmium
from shapely.geometry import LineString

TILE_DEG = 0.2  # must match backend/sources/osm.py
SIMPLIFY_DEG = 0.0002  # about 22 m
PLACES = {"village", "hamlet", "town", "city"}
ROADS = {"trunk", "primary", "secondary", "tertiary"}
SCALE = 1e6

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE features (id TEXT PRIMARY KEY, cat TEXT NOT NULL, kind TEXT, name TEXT, geom BLOB NOT NULL);
CREATE TABLE tiles (tlat INTEGER NOT NULL, tlon INTEGER NOT NULL, fid TEXT NOT NULL);
"""


def category(tags) -> tuple[str, str] | None:
    if tags.get("amenity") == "fire_station":
        return "fire_station", "fire_station"
    if tags.get("place") in PLACES:
        return "settlement", tags.get("place")
    if tags.get("highway") in ROADS:
        return "road", tags.get("highway")
    if tags.get("waterway") == "river":
        return "river", "river"
    if tags.get("natural") == "water":
        return "lake", tags.get("water") or "water"
    return None


def pack(coords: list[tuple[float, float]]) -> bytes:
    """(lat, lon) pairs as int32 microdegrees, zlib compressed."""
    return zlib.compress(b"".join(struct.pack("<ii", round(la * SCALE), round(lo * SCALE)) for la, lo in coords), 6)


def tiles_of(coords) -> set[tuple[int, int]]:
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    return {(a, b) for a in range(math.floor(min(lats) / TILE_DEG), math.floor(max(lats) / TILE_DEG) + 1)
            for b in range(math.floor(min(lons) / TILE_DEG), math.floor(max(lons) / TILE_DEG) + 1)}


class Handler(osmium.SimpleHandler):
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.n = {"node": 0, "way": 0, "skipped": 0}
        self.batch_f, self.batch_t = [], []

    def _add(self, fid, cat, kind, name, coords):
        self.batch_f.append((fid, cat, kind, name, pack(coords)))
        self.batch_t.extend((a, b, fid) for a, b in tiles_of(coords))
        if len(self.batch_f) >= 20000:
            self.flush()

    def flush(self):
        self.conn.executemany("INSERT OR IGNORE INTO features VALUES (?,?,?,?,?)", self.batch_f)
        self.conn.executemany("INSERT INTO tiles VALUES (?,?,?)", self.batch_t)
        self.batch_f, self.batch_t = [], []

    def node(self, n):
        c = category(n.tags)
        if c and c[0] in ("settlement", "fire_station") and n.location.valid():
            self._add(f"node/{n.id}", c[0], c[1], n.tags.get("name"), [(n.location.lat, n.location.lon)])
            self.n["node"] += 1

    def way(self, w):
        c = category(w.tags)
        if not c:
            return
        try:
            coords = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
        except osmium.InvalidLocationError:
            self.n["skipped"] += 1
            return
        if not coords:
            self.n["skipped"] += 1
            return
        if c[0] == "fire_station":
            coords = [(sum(p[0] for p in coords) / len(coords), sum(p[1] for p in coords) / len(coords))]
        elif len(coords) > 2:
            line = LineString([(lo, la) for la, lo in coords]).simplify(SIMPLIFY_DEG, preserve_topology=False)
            coords = [(y, x) for x, y in line.coords]
        self._add(f"way/{w.id}", c[0], c[1], w.tags.get("name"), coords)
        self.n["way"] += 1


def main(src: Path, out: Path) -> None:
    if out.exists():
        out.unlink()
    conn = sqlite3.connect(out)
    conn.executescript(SCHEMA)
    t = time.time()
    h = Handler(conn)
    # Node locations for all of Indonesia fit in memory with the sparse index (about 4 GB).
    h.apply_file(str(src), locations=True, idx="sparse_mem_array")
    h.flush()
    conn.execute("CREATE INDEX tiles_key ON tiles(tlat, tlon)")
    counts = dict(conn.execute("SELECT cat, COUNT(*) FROM features GROUP BY cat").fetchall())
    meta = {"source": src.name, "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tile_deg": str(TILE_DEG),
            "simplify_deg": str(SIMPLIFY_DEG), "license": "ODbL 1.0, (c) OpenStreetMap contributors", "counts": str(counts)}
    conn.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    print(f"{h.n} in {time.time() - t:.0f} s; {counts}; {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
