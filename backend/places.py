"""Place search for the map: provinces, regencies and cities, districts, and settlements by name.

The data is a gazetteer built from a Geofabrik extract by tools/build_places.py and published as a release
asset; the server downloads it once (PLACES_URL), like the offline OSM extract. No live geocoder is called:
public Nominatim forbids search-as-you-type, and public OSM services were down for hours on this project.
"""

import json
import logging
import re
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path

log = logging.getLogger("karhutla.places")

ATTRIBUTION = "Places: (c) OpenStreetMap contributors, ODbL, via Geofabrik"
MIN_QUERY = 2
LIMIT = 8
# Words people type that are not part of a settlement's name in OSM.
NOISE = {"desa", "ds", "kelurahan", "kel", "dusun"}

# Must match tools/build_places.py.
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


class NotReady(Exception):
    """The gazetteer has not been downloaded (or did not open)."""


def info(path: Path | None) -> dict | None:
    if not path or not path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error:
        return None
    return meta if meta.get("kind") == "places" else None


def ensure(path: Path, url: str) -> bool:
    """Download once, next to the target, and rename only after it opens as a gazetteer."""
    import httpx

    if path.exists() or not url:
        return path.exists()
    part = path.with_suffix(".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60, headers={"User-Agent": "fight-karhutla"}) as r:
            r.raise_for_status()
            with part.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        if info(part) is None:
            raise ValueError("downloaded file is not a place search database")
        part.replace(path)
        log.info("place search file ready: %s (%.0f MB)", path, path.stat().st_size / 1e6)
        for old in path.parent.glob("places*.sqlite"):  # earlier releases, now replaced
            if old != path:
                old.unlink(missing_ok=True)
                log.info("removed the earlier place search file %s", old.name)
        return True
    except (httpx.HTTPError, ValueError, OSError) as exc:
        log.warning("place search file download failed: %s", exc)
        part.unlink(missing_ok=True)
        return False


def _connect(path: Path | None) -> sqlite3.Connection:
    if info(path) is None:
        raise NotReady("place search file is not downloaded yet")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def tokens(q: str) -> list[str]:
    return [t for t in re.findall(r"\w+", q.lower()) if t not in NOISE]


def _item(r: sqlite3.Row) -> dict:
    bbox = [float(v) for v in r["bbox"].split(",")] if r["bbox"] else None
    return {"id": r["id"], "cat": r["cat"], "kind": r["kind"], "name": r["name"], "label": r["label"], "lat": r["lat"], "lon": r["lon"],
            "bbox": bbox, "district": r["district"], "regency": r["regency"], "province": r["province"]}


def search(path: Path | None, q: str, limit: int = LIMIT) -> list[dict]:
    """Every word must start a word of the name, an alternative name, or a containing region ("sukamaju lampung").
    Exact names first, then names that
    start with the query, then names holding it as a whole word, then wide regions before villages and hamlets,
    then FTS relevance."""
    words = tokens(q)
    if len("".join(words)) < MIN_QUERY:
        return []
    match = " ".join(f'"{w}"*' for w in words)  # \w tokens only, quoted: no FTS syntax from the user
    norm = " ".join(words)
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            """SELECT p.*, bm25(places_fts, 10.0, 2.0, 1.0) AS score FROM places_fts JOIN places p ON p.rowid = places_fts.rowid
               WHERE places_fts MATCH :m
               ORDER BY (lower(p.name) = :n OR lower(p.label) = :n) DESC, (lower(p.name) LIKE :n || '%') DESC,
                        (' ' || lower(p.name) || ' ') LIKE '% ' || :n || ' %' DESC, p.rank, score
               LIMIT :lim""",
            {"m": match, "n": norm, "lim": limit},
        ).fetchall()
    return [_item(r) for r in rows]


def outline(path: Path | None, place_id: str) -> dict | None:
    with closing(_connect(path)) as conn:
        row = conn.execute("SELECT o.geojson, p.label, p.kind FROM outlines o JOIN places p ON p.id = o.id WHERE o.id=?", (place_id,)).fetchone()
    if not row:
        return None
    return {"type": "Feature", "geometry": json.loads(zlib.decompress(row["geojson"])), "properties": {"id": place_id, "label": row["label"], "kind": row["kind"]}}
