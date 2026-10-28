"""Build the offline OSM fallback on Modal, where downloading the 1.7 GB Geofabrik extract takes a minute.

  modal run tools/osm_fallback_modal.py            # download, verify MD5, build, keep in the Volume
  modal volume get osm-fallback osm_fallback.sqlite ./osm_fallback.sqlite
"""

import hashlib
import subprocess
from pathlib import Path

import modal

URL = "https://download.geofabrik.de/asia/indonesia-latest.osm.pbf"
app = modal.App("fight-karhutla-osm-fallback")
vol = modal.Volume.from_name("osm-fallback", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12").apt_install("curl").pip_install("osmium==4.3.2", "shapely==2.2.0")
         .add_local_file(Path(__file__).with_name("build_osm_fallback.py"), "/root/build_osm_fallback.py"))


@app.function(image=image, volumes={"/out": vol}, cpu=4, memory=16384, timeout=3600)
def build() -> dict:
    pbf = Path("/tmp/indonesia.osm.pbf")
    subprocess.run(["curl", "-sSL", "-o", str(pbf), URL], check=True)
    md5_expected = subprocess.run(["curl", "-sSL", URL + ".md5"], check=True, capture_output=True, text=True).stdout.split()[0]
    h = hashlib.md5()
    with pbf.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    if h.hexdigest() != md5_expected:
        raise RuntimeError(f"MD5 mismatch: {h.hexdigest()} != {md5_expected}")
    out = Path("/out/osm_fallback.sqlite")
    # The extract date travels with the data: evidence names the source by it.
    stamp = subprocess.run(["curl", "-sSIL", URL], check=True, capture_output=True, text=True).stdout
    date = next((l.split("-")[-1].split(".")[0] for l in stamp.splitlines() if l.lower().startswith("location:")), "")
    r = subprocess.run(["python", "/root/build_osm_fallback.py", str(pbf), str(out)], check=True, capture_output=True, text=True)
    if len(date) == 6:
        import sqlite3

        with sqlite3.connect(out) as c:
            c.execute("UPDATE meta SET value=? WHERE key='source'", (f"Geofabrik Indonesia 20{date[:2]}-{date[2:4]}-{date[4:]}",))
        c.close()
    vol.commit()
    return {"pbf_bytes": pbf.stat().st_size, "md5": md5_expected, "build_log": r.stdout[-800:], "out_bytes": out.stat().st_size}


@app.local_entrypoint()
def main():
    print(build.remote())
