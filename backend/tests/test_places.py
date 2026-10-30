import json
import sqlite3
import zlib

from fastapi.testclient import TestClient

from backend import places

SQUARE = {"type": "Polygon", "coordinates": [[[113.5, -3.5], [114.5, -3.5], [114.5, -2.0], [113.5, -2.0], [113.5, -3.5]]]}
ROWS = [
    # id, cat, kind, rank, name, label, alt, lat, lon, bbox, district, regency, province
    ("r1", "region", "province", 0, "Kalimantan Tengah", "Kalimantan Tengah", "Central Kalimantan", -1.5, 113.5, "110.7,-3.6,115.9,0.8", None, None, None),
    ("r2", "region", "regency", 1, "Pulang Pisau", "Kabupaten Pulang Pisau", "Kabupaten Pulang Pisau", -2.8, 114.0, "113.5,-3.5,114.5,-2.0", None, None, "Kalimantan Tengah"),
    ("r3", "region", "city", 1, "Palangka Raya", "Kota Palangka Raya", "Kota Palangka Raya", -2.0, 113.9, "113.5,-2.4,114.1,-1.6", None, None, "Kalimantan Tengah"),
    ("n1", "settlement", "city", 2, "Palangka Raya", "Palangka Raya", "", -2.21, 113.92, None, "Jekan Raya", "Kota Palangka Raya", "Kalimantan Tengah"),
    ("n2", "settlement", "village", 5, "Sukamaju", "Sukamaju", "", -2.6, 114.1, None, "Kahayan Hilir", "Kabupaten Pulang Pisau", "Kalimantan Tengah"),
    ("n3", "settlement", "village", 5, "Sukamaju", "Sukamaju", "", -6.9, 107.6, None, None, "Kabupaten Bandung", "Jawa Barat"),
    ("n4", "settlement", "hamlet", 6, "Sukamaju Dua", "Sukamaju Dua", "", -2.61, 114.11, None, "Kahayan Hilir", "Kabupaten Pulang Pisau", "Kalimantan Tengah"),
    ("n5", "settlement", "village", 5, "Jabiren", "Jabiren", "", -2.52, 114.17, None, "Jabiren Raya", "Kabupaten Pulang Pisau", "Kalimantan Tengah"),
]


def make_gazetteer(path):
    conn = sqlite3.connect(path)
    conn.executescript(places.SCHEMA)
    rows = [r + (" ".join(v for v in r[10:13] if v),) for r in ROWS]
    conn.executemany("INSERT INTO places (id, cat, kind, rank, name, label, alt, lat, lon, bbox, district, regency, province, ctx) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO outlines VALUES (?, ?)", ("r2", zlib.compress(json.dumps(SQUARE).encode())))
    conn.execute("INSERT INTO places_fts(places_fts) VALUES ('rebuild')")
    conn.execute("INSERT INTO meta VALUES ('kind', 'places')")
    conn.commit()
    conn.close()


def ids(items):
    return [i["id"] for i in items]


def test_prefix_of_every_word_finds_the_regency_with_its_box(tmp_path):
    db = tmp_path / "places.sqlite"
    make_gazetteer(db)
    found = places.search(db, "pulang pis")
    assert ids(found)[0] == "r2"
    assert found[0]["bbox"] == [113.5, -3.5, 114.5, -2.0] and found[0]["label"] == "Kabupaten Pulang Pisau"


def test_exact_name_first_and_regions_before_settlements(tmp_path):
    db = tmp_path / "places.sqlite"
    make_gazetteer(db)
    assert ids(places.search(db, "Palangka Raya"))[:2] == ["r3", "n1"]
    # Two villages with the same name come first (exact), told apart by their regency; the hamlet after.
    found = places.search(db, "desa sukamaju")
    assert ids(found)[:2] in (["n2", "n3"], ["n3", "n2"]) and ids(found)[2] == "n4"
    assert {f["regency"] for f in found[:2]} == {"Kabupaten Pulang Pisau", "Kabupaten Bandung"}


def test_a_region_word_narrows_a_common_village_name(tmp_path):
    db = tmp_path / "places.sqlite"
    make_gazetteer(db)
    assert ids(places.search(db, "sukamaju bandung")) == ["n3"]
    assert ids(places.search(db, "sukamaju pulang"))[:2] == ["n2", "n4"]


def test_user_text_never_reaches_fts_syntax(tmp_path):
    db = tmp_path / "places.sqlite"
    make_gazetteer(db)
    for q in ['"', "* OR NEAR(", "jab) AND (", "a", "  ", "NOT"]:
        places.search(db, q)  # no sqlite3.OperationalError
    assert places.search(db, "a") == []
    assert ids(places.search(db, "(jab*")) == ["n5"]


def test_api_search_outline_and_not_ready(settings):
    from backend.main import app

    with TestClient(app) as c:
        r = c.get("/api/places", params={"q": "pulang"})
        assert r.status_code == 503 and "not downloaded" in r.json()["detail"]
        make_gazetteer(settings.places_path)
        body = c.get("/api/places", params={"q": "pulang"}).json()
        assert body["items"][0]["id"] == "r2" and "OpenStreetMap" in body["attribution"]
        f = c.get("/api/places/r2/outline").json()
        assert f["geometry"]["type"] == "Polygon" and f["properties"]["label"] == "Kabupaten Pulang Pisau"
        assert c.get("/api/places/n2/outline").status_code == 404


def test_a_database_that_is_not_a_gazetteer_is_refused(tmp_path):
    other = tmp_path / "osm_fallback.sqlite"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta VALUES ('source', 'Geofabrik')")
    conn.commit()
    conn.close()
    assert places.info(other) is None and places.info(tmp_path / "missing.sqlite") is None
