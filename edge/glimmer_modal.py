"""Muse Glimmer as the drone ground station frame filter, on Modal.

Muse Glimmer is a 30B open-weight model (Apache 2.0). Its smallest published build (Q4_K_M GGUF, 17 GB,
plus a 1.4 GB vision projector) needs a 24 GB class GPU, so it cannot ride on a small drone. It runs where a
field team would put it: a ground station between the drone and a weak uplink, deciding which frames are
worth sending to the server. Modal stands in for that ground station here.

  modal run edge/glimmer_modal.py::download          # once: weights into a Modal Volume
  modal run edge/glimmer_modal.py --frames replay/drone/inc-0407/frames --out replay/drone/inc-0407/glimmer.json
"""

import base64
import json
import re
import subprocess
import time
from pathlib import Path

import modal

REPO = "meta-models/Muse-Glimmer-30B-GGUF"
MODEL = "Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf"
MMPROJ = "mmproj-Muse-Glimmer-30B-Q4_K_M.gguf"
WEIGHTS = "/weights"

app = modal.App("fight-karhutla-glimmer")
volume = modal.Volume.from_name("muse-glimmer-weights", create_if_missing=True)
dl_image = modal.Image.debian_slim(python_version="3.12").pip_install("huggingface_hub[hf_transfer]==0.36.0").env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
# Official llama.cpp CUDA server image; Muse Glimmer needs build b10353 or newer.
server_image = modal.Image.from_registry("ghcr.io/ggml-org/llama.cpp:server-cuda", add_python="3.12").entrypoint([]).pip_install("httpx==0.28.1")

PROMPT = """You are the frame filter at a drone ground station during a wildfire survey in Indonesia.
The uplink is weak, so only frames useful to the fire team are sent. This is a nadir photo from 120 m.
Answer with one JSON object and nothing else:
{"smoke": true|false, "flames": true|false, "burned_ground": true|false, "uplink": true|false, "reason": "at most 15 words"}
uplink is true when smoke, flames, or burned ground is visible. Healthy forest only means uplink false."""


@app.function(image=dl_image, volumes={WEIGHTS: volume}, timeout=3600)
def download() -> list[str]:
    from huggingface_hub import hf_hub_download

    for f in (MODEL, MMPROJ):
        hf_hub_download(REPO, f, local_dir=WEIGHTS)
    volume.commit()
    return sorted(p.name + f" {p.stat().st_size / 1e9:.2f} GB" for p in Path(WEIGHTS).iterdir() if p.is_file())


def _parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    keys = ("smoke", "flames", "burned_ground", "uplink")
    if not all(isinstance(d.get(k), bool) for k in keys):
        return None
    return {**{k: d[k] for k in keys}, "reason": str(d.get("reason", ""))[:200]}


@app.cls(image=server_image, gpu="A10G", volumes={WEIGHTS: volume}, timeout=3600, scaledown_window=120)
class GroundStation:
    @modal.enter()
    def start(self):
        self.proc = subprocess.Popen([
            "/app/llama-server", "-m", f"{WEIGHTS}/{MODEL}", "--mmproj", f"{WEIGHTS}/{MMPROJ}", "-a", "muse-glimmer",
            "-ngl", "99", "-c", "16384", "-np", "1", "--host", "127.0.0.1", "--port", "8080",
            "--chat-template-kwargs", '{"reasoning_strength":"low"}',
        ])
        import httpx

        t = time.time()
        while time.time() - t < 900:
            try:
                if httpx.get("http://127.0.0.1:8080/health", timeout=5).status_code == 200:
                    self.load_s = round(time.time() - t, 1)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(3)
        raise RuntimeError("llama-server did not become healthy")

    @modal.method()
    def classify(self, name: str, jpeg: bytes) -> dict:
        import httpx

        body = {
            "model": "muse-glimmer", "max_tokens": 1500, "temperature": 0.2,
            "chat_template_kwargs": {"reasoning_strength": "low"},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}},
            ]}],
        }
        t = time.time()
        r = httpx.post("http://127.0.0.1:8080/v1/chat/completions", json=body, timeout=600).json()
        msg = r["choices"][0]["message"]
        parsed = _parse(msg.get("content", ""))
        return {
            "file": name, "decision": parsed, "raw": (msg.get("content") or "")[:400], "finish_reason": r["choices"][0].get("finish_reason"),
            "seconds": round(time.time() - t, 2), "usage": r.get("usage"), "server_load_s": getattr(self, "load_s", None),
        }


@app.local_entrypoint()
def main(frames: str, out: str, limit: int = 0):
    files = sorted(Path(frames).glob("*.jpg"))
    if limit:
        files = files[:limit]
    station = GroundStation()
    t = time.time()
    results = [station.classify.remote(p.name, p.read_bytes()) for p in files]
    for r in results:
        print(r["file"], r["decision"], r["seconds"], "s")
    up = [r for r in results if r["decision"] and r["decision"]["uplink"]]
    summary = {
        "model": "Muse Glimmer 30B, Q4_K_M GGUF with vision projector", "runtime": "llama.cpp server-cuda on Modal A10G (ground station stand-in)",
        "reasoning_strength": "low", "frames": len(results), "uplinked": len(up), "dropped": len(results) - len(up),
        "unparsed": sum(1 for r in results if r["decision"] is None), "wall_seconds": round(time.time() - t, 1), "results": results,
    }
    Path(out).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print({k: v for k, v in summary.items() if k != "results"})
