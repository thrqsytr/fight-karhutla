"""Build the place search file (tools/build_places.py) for all of Indonesia on Modal, where the 1.7 GB
Geofabrik extract downloads in a minute.

The Indonesia extract cuts boundary relations that cross into a neighbour, so Kalimantan Utara and Kepulauan
Riau (borders with Malaysia and Singapore) did not assemble. The Malaysia-Singapore-Brunei extract is merged in
first; places outside Indonesian provinces are still dropped by build_places.py.

  modal run tools/places_modal.py              # download, verify MD5, build, keep in the Volume
  modal volume get karhutla-places places.sqlite ./places.sqlite
"""

import hashlib
import subprocess
from pathlib import Path

import modal

URLS = ["https://download.geofabrik.de/asia/indonesia-latest.osm.pbf",
        "https://download.geofabrik.de/asia/malaysia-singapore-brunei-latest.osm.pbf"]
app = modal.App("fight-karhutla-places")
vol = modal.Volume.from_name("karhutla-places", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12").apt_install("curl", "osmium-tool").pip_install("osmium==4.3.2", "shapely==2.2.0")
         .add_local_file(Path(__file__).with_name("build_places.py"), "/root/build_places.py"))


@app.function(image=image, volumes={"/out": vol}, cpu=4, memory=16384, timeout=3600, region="us-west")
def build() -> dict:
    parts, dates = [], []
    for i, url in enumerate(URLS):
        pbf = Path(f"/tmp/part{i}.osm.pbf")
        subprocess.run(["curl", "-sSL", "-o", str(pbf), url], check=True)
        md5_expected = subprocess.run(["curl", "-sSL", url + ".md5"], check=True, capture_output=True, text=True).stdout.split()[0]
        h = hashlib.md5()
        with pbf.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                h.update(chunk)
        if h.hexdigest() != md5_expected:
            raise RuntimeError(f"MD5 mismatch for {url}: {h.hexdigest()} != {md5_expected}")
        stamp = subprocess.run(["curl", "-sSIL", url], check=True, capture_output=True, text=True).stdout
        dates.append(next((l.split("-")[-1].split(".")[0] for l in stamp.splitlines() if l.lower().startswith("location:")), ""))
        parts.append(str(pbf))
    pbf = Path("/tmp/merged.osm.pbf")
    subprocess.run(["osmium", "merge", *parts, "-o", str(pbf), "--overwrite"], check=True)
    date = dates[0]
    out = Path("/out/places.sqlite")
    r = subprocess.run(["python", "/root/build_places.py", str(pbf), str(out)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stdout[-2000:] + r.stderr[-2000:])
    if len(date) == 6:
        import sqlite3

        with sqlite3.connect(out) as c:
            c.execute("UPDATE meta SET value=? WHERE key='source'", (f"Geofabrik Indonesia 20{date[:2]}-{date[2:4]}-{date[4:]} (borders completed from Malaysia-Singapore-Brunei)",))
        c.close()
    vol.commit()
    return {"pbf_bytes": pbf.stat().st_size, "dates": dates, "build_log": r.stdout[-1500:], "out_bytes": out.stat().st_size}


@app.local_entrypoint()
def main():
    print(build.remote())
