"""Run eval/build_dataset.py on Modal, in the same AWS region as the Sentinel-2 COGs.

From a home link a single 10 m block read takes about a minute; next to us-west-2 it takes well under a second.
The code is the repository's own (backend/ and eval/), the cache is a Modal Volume laid out like
Scripts/eval-cache, and the outputs come back as one tar archive.

  modal volume create karhutla-eval-cache
  modal volume put karhutla-eval-cache Scripts/eval-cache/firms /firms   (and /sentinel2 for the search cache)
  modal run eval/dataset_modal.py --target 30
"""

import io
import tarfile
from pathlib import Path

import modal

REPO = Path(__file__).resolve().parents[1]
app = modal.App("fight-karhutla-eval-dataset")
vol = modal.Volume.from_name("karhutla-eval-cache", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install("rasterio==1.5.2", "numpy", "httpx", "pydantic-settings", "pillow")
         .add_local_dir(REPO / "backend", "/w/a/repo/backend", ignore=["__pycache__", "tests"])
         .add_local_file(REPO / "eval" / "build_dataset.py", "/w/a/repo/eval/build_dataset.py"))


@app.function(image=image, volumes={"/w/Scripts/eval-cache": vol}, cpu=8, memory=8192, timeout=3 * 3600, region="us-west")
def build(target: int, workers: int, max_tries: int) -> bytes:
    import os
    import sys

    sys.path.insert(0, "/w/a/repo/eval")
    os.environ.setdefault("FIRMS_MAP_KEY", "cached")  # the FIRMS CSVs are in the volume; no fetch happens
    import build_dataset

    build_dataset.WORKERS = workers
    build_dataset.MAX_TRIES = max_tries
    # Next to the data the native 10 m true color is cheap, so the review sheets use it.
    build_dataset.sentinel.RGB_SCALE = 2
    try:
        build_dataset.main(target)
    finally:
        vol.commit()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        out = Path("/w/a/repo/eval")
        for p in [out / "labels.csv", out / "dataset" / "clusters.json", *sorted((out / "dataset" / "sheets").glob("*.png"))]:
            tar.add(p, arcname=str(p.relative_to(out)))
        labels = Path("/w/Scripts/eval-cache/labels-v3")
        for p in sorted(labels.glob("*.json")):
            tar.add(p, arcname=f"labels-v3/{p.name}")
    return buf.getvalue()


@app.local_entrypoint()
def main(target: int = 30, workers: int = 32, max_tries: int = 1800):
    data = build.remote(target, workers, max_tries)
    dest = REPO.parent.parent / "Scripts" / "eval-cache" / "modal-dataset.tar.gz"
    dest.write_bytes(data)
    print("wrote", dest, len(data), "bytes")
