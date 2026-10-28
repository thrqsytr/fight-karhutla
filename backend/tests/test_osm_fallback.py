"""Offline OSM fallback: used when Overpass fails, skips Overpass while it is down, never replaces a good file."""

import sqlite3
import struct
import zlib

import pytest

from backend.sources import osm
from backend.sources.http import SourceError


def pack(coords):
    return zlib.compress(b"".join(struct.pack("<ii", round(a * 1e6), round(b * 1e6)) for a, b in coords))


def make_fallback(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE features (id TEXT PRIMARY KEY, cat TEXT NOT NULL, kind TEXT, name TEXT, geom BLOB NOT NULL);
        CREATE TABLE tiles (tlat INTEGER NOT NULL, tlon INTEGER NOT NULL, fid TEXT NOT NULL);
    """)
    feats = [
        ("node/1", "settlement", "village", "Kahala", [(0.01, 116.45)]),
        ("way/2", "road", "primary", "Jalan Poros", [(0.0, 116.40), (0.0, 116.50)]),
        ("node/3", "fire_station", "fire_station", "Pos Damkar", [(0.2, 116.6)]),
    ]
    for fid, cat, kind, name, coords in feats:
        conn.execute("INSERT INTO features VALUES (?,?,?,?,?)", (fid, cat, kind, name, pack(coords)))
        for la, lo in coords:
            conn.execute("INSERT INTO tiles VALUES (?,?,?)", (*osm.tile_of(la, lo), fid))
    conn.execute("INSERT INTO meta VALUES ('source', 'test.osm.pbf')")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def overpass_up():
    osm._overpass_down_until = 0.0
    yield
    osm._overpass_down_until = 0.0


def test_fallback_answers_when_overpass_fails_and_skips_it_while_down(tmp_path, monkeypatch):
    fb = tmp_path / "osm_fallback.sqlite"
    make_fallback(fb)
    calls = []

    def down(tile, cache_dir):
        calls.append(tile)
        raise SourceError("all Overpass endpoints failed")

    monkeypatch.setattr(osm, "fetch_tile", down)
    a = osm.get_nearby_assets(0.0, 116.45, cache_dir=tmp_path, fixtures_dir=tmp_path, fixture=False, fallback=fb)
    assert a["endpoint"] == "offline extract" and a["offline_source"] == "test.osm.pbf"
    assert a["nearest"]["settlement"]["name"] == "Kahala" and a["nearest"]["road"]["distance_m"] == 0
    assert a["nearest"]["fire_station"]["name"] == "Pos Damkar"  # about 25 km, inside the 30 km station radius
    osm.get_nearby_assets(0.01, 116.46, cache_dir=tmp_path, fixtures_dir=tmp_path, fixture=False, fallback=fb)
    assert len(calls) == 1  # Overpass is not retried while it is marked down


def test_without_a_fallback_the_failure_still_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr(osm, "fetch_tile", lambda tile, cache_dir: (_ for _ in ()).throw(SourceError("down")))
    with pytest.raises(SourceError):
        osm.get_nearby_assets(0.0, 116.45, cache_dir=tmp_path, fixtures_dir=tmp_path, fixture=False, fallback=tmp_path / "missing.sqlite")


def test_a_broken_download_never_becomes_the_fallback(tmp_path, monkeypatch):
    import httpx

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self, n):
            yield b"<html>not a database</html>"

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Resp())
    target = tmp_path / "osm_fallback.sqlite"
    assert osm.ensure_fallback(target, "https://example.invalid/x") is False
    assert not target.exists() and not target.with_suffix(".part").exists()
