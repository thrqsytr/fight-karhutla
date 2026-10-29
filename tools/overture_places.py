"""Add Overture Maps offices to the offline OSM fallback built by tools/build_osm_fallback.py.

OpenStreetMap maps few offices in Indonesia (413 fire stations in October 2026); Overture places, mostly from
Meta, add fire stations, police stations, military sites, and government offices. Rows go into the same
features and tiles tables with ids starting with overture/, so backend/sources/osm.py reads them the same way.

Usage: python tools/overture_places.py FALLBACK.sqlite RELEASE   (for example 2026-10-21.0)
Needs: pip install duckdb (build time only). Reads s3://overturemaps-us-west-2 anonymously; run it near
us-west-2 (tools/osm_fallback_modal.py does). Overture places carry no OSM data; licenses per source are
CDLA Permissive 2.0, Apache 2.0, or CC0 (https://docs.overturemaps.org/attribution/).
"""

import sqlite3
import sys
from pathlib import Path

import duckdb

from build_osm_fallback import pack, tiles_of

BBOX = (94.5, -11.5, 141.5, 6.5)  # backend/geo.py INDONESIA_BBOX
# Overture taxonomy.primary (or basic_category) per office category, from a probe of the 2026-10-21.0 release.
CATEGORY = {
    "fire_station": "fire_station",
    "police_station": "police",
    "military_site": "military",
    "military_base": "military",
    "government_office": "government",
    "town_hall": "government",
    "civic_center": "government",
}
# The docs warn of junk and duplicates; below this confidence a place is left out.
MIN_CONFIDENCE = 0.6


def main(db: Path, release: str) -> dict:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    w, s, e, n = BBOX
    keys = ", ".join(f"'{k}'" for k in CATEGORY)
    rows = con.execute(f"""
        SELECT id, names.primary, coalesce(taxonomy.primary, basic_category), confidence, bbox.ymin, bbox.xmin
        FROM read_parquet('s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*', hive_partitioning=1)
        WHERE bbox.xmin BETWEEN {w} AND {e} AND bbox.ymin BETWEEN {s} AND {n}
          AND coalesce(taxonomy.primary, basic_category) IN ({keys})
          AND confidence >= {MIN_CONFIDENCE}
          AND coalesce(operating_status, 'open') != 'permanently_closed'""").fetchall()
    counts: dict[str, int] = {}
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM tiles WHERE fid LIKE 'overture/%'")
        conn.execute("DELETE FROM features WHERE id LIKE 'overture/%'")
        feats, tiles = [], []
        for oid, name, kind, _conf, lat, lon in rows:
            cat = CATEGORY[kind]
            fid = f"overture/{oid}"
            coords = [(lat, lon)]
            feats.append((fid, cat, kind, name, pack(coords)))
            tiles.extend((a, b, fid) for a, b in tiles_of(coords))
            counts[cat] = counts.get(cat, 0) + 1
        conn.executemany("INSERT OR IGNORE INTO features VALUES (?,?,?,?,?)", feats)
        conn.executemany("INSERT INTO tiles VALUES (?,?,?)", tiles)
        meta = {"overture_release": release, "overture_counts": str(counts), "overture_min_confidence": str(MIN_CONFIDENCE),
                "overture_license": "Overture Maps places: CDLA Permissive 2.0, Apache 2.0, or CC0 per source"}
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", meta.items())
    with sqlite3.connect(db) as conn:
        conn.execute("VACUUM")
    return counts


if __name__ == "__main__":
    print(main(Path(sys.argv[1]), sys.argv[2]))
