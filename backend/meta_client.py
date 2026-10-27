"""Model clients: Meta Model API (triage, photos, SAM, voice) and Baseten (Muse Spark Open, critique and evaluation).

Keys are passed explicitly: the OpenAI SDK does not read MODEL_API_KEY on its own.
Cost is computed from each response's usage at the list prices checked on 2026-10-27
(docs/DATA_SOURCES.md), so the numbers shown come from metering, not estimates.
"""

import base64
import io
import json
import logging
import subprocess
from dataclasses import dataclass, field

import httpx
from openai import AsyncOpenAI, OpenAI

from .config import Settings

log = logging.getLogger(__name__)

SPARK = "muse-spark-1.3"
SAM = "sam-3.1"
ASR = "muse-voice-transcribe-1.0"
SPARK_OPEN = "meta-models/muse-spark-open-1026"
BASETEN_URL = "https://inference.baseten.co/v1"

# USD per 1M tokens (Muse Spark 1.3, Standard mode), per image (SAM 3.1), per hour of audio (Voice Transcribe).
SPARK_INPUT, SPARK_CACHED, SPARK_OUTPUT = 1.25, 0.15, 4.25
SAM_PER_IMAGE = 2.50 / 1000
ASR_PER_HOUR = 0.18

TIMEOUT_S = 180
MAX_RETRIES = 2  # SDK retries 429 and 5xx with backoff; anything else is an answer


class ModelUnavailable(RuntimeError):
    """No key configured or the provider failed. The caller records it; triage does not pretend."""


@dataclass
class Meter:
    """Accumulates usage and cost for one agent run, per model."""

    calls: list[dict] = field(default_factory=list)

    def spark(self, usage, model: str = SPARK) -> None:
        if usage is None:
            return
        u = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
        inp = u.get("input_tokens", u.get("prompt_tokens", 0)) or 0
        out = u.get("output_tokens", u.get("completion_tokens", 0)) or 0
        details = u.get("input_tokens_details") or u.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens") or 0
        rdetails = u.get("output_tokens_details") or u.get("completion_tokens_details") or {}
        cost = None
        if model == SPARK:
            cost = ((inp - cached) * SPARK_INPUT + cached * SPARK_CACHED + out * SPARK_OUTPUT) / 1e6
        # Baseten hackathon credit is not priced per token here; its cost is reported as null, not zero.
        self.calls.append({"model": model, "input_tokens": inp, "cached_tokens": cached, "output_tokens": out,
                           "reasoning_tokens": rdetails.get("reasoning_tokens") or 0, "cost_usd": cost})

    def sam(self, images: int = 1) -> None:
        self.calls.append({"model": SAM, "images": images, "cost_usd": images * SAM_PER_IMAGE})

    def asr(self, audio_ms: int) -> None:
        self.calls.append({"model": ASR, "audio_ms": audio_ms, "cost_usd": (audio_ms // 1000) / 3600 * ASR_PER_HOUR})

    def summary(self) -> dict:
        priced = [c["cost_usd"] for c in self.calls if c.get("cost_usd") is not None]
        return {
            "calls": self.calls,
            "cost_usd": round(sum(priced), 6),
            "unpriced_calls": sum(1 for c in self.calls if c.get("cost_usd") is None),
        }


def meta(settings: Settings) -> OpenAI:
    if not settings.model_api_key:
        raise ModelUnavailable("MODEL_API_KEY is not set")
    return OpenAI(base_url=settings.meta_base_url, api_key=settings.model_api_key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES)


def meta_async(settings: Settings) -> AsyncOpenAI:
    if not settings.model_api_key:
        raise ModelUnavailable("MODEL_API_KEY is not set")
    return AsyncOpenAI(base_url=settings.meta_base_url, api_key=settings.model_api_key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES)


def baseten(settings: Settings) -> OpenAI:
    if not settings.baseten_api_key:
        raise ModelUnavailable("BASETEN_API_KEY is not set")
    return OpenAI(base_url=BASETEN_URL, api_key=settings.baseten_api_key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES)


def image_data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def to_wav_16k(audio: bytes) -> bytes:
    """Voice Transcribe accepts only mono 16-bit PCM WAV at 16 or 24 kHz; browsers record WebM, Ogg, or MP4."""
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-map_metadata", "-1", "-f", "wav", "pipe:1"],
        input=audio, capture_output=True, timeout=60,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise ModelUnavailable(f"ffmpeg could not convert the audio: {proc.stderr.decode(errors='replace')[:300]}")
    return proc.stdout


def transcribe(settings: Settings, wav: bytes, meter: Meter | None = None) -> dict:
    """Muse Voice Transcribe, biased toward Indonesian. Returns {transcript, audio_ms, session_id}."""
    if not settings.model_api_key:
        raise ModelUnavailable("MODEL_API_KEY is not set")
    req = {"model": ASR, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK", "languageBias": ["Indonesian"]}
    last = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = httpx.post(
                f"{settings.meta_base_url}/asr/transcribe",
                headers={"Authorization": f"Bearer {settings.model_api_key}", "Accept": "application/json"},
                files={"request": (None, json.dumps(req), "application/json"), "audio": ("voice.wav", wav, "audio/wav")},
                timeout=TIMEOUT_S,
            )
        except httpx.TransportError as exc:
            last = repr(exc)
            log.warning("transcribe transport error, attempt %d: %s", attempt + 1, last)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = f"HTTP {r.status_code}"
            log.warning("transcribe transient %s, attempt %d", last, attempt + 1)
            continue
        if r.status_code != 200:
            raise ModelUnavailable(f"transcribe HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        if meter is not None:
            meter.asr(int(body.get("audioDurationMs") or 0))
        return {"transcript": body.get("transcript", ""), "audio_ms": body.get("audioDurationMs"), "session_id": body.get("sessionId")}
    raise ModelUnavailable(f"transcribe failed after retries: {last}")


async def segment(settings: Settings, jpeg: bytes, concept: str, meter: Meter | None = None) -> dict:
    """SAM 3.1 concept segmentation of one image. Pixel counts are computed here, in code, from the decoded masks."""
    import meta_sam_parser as sam

    client = meta_async(settings)
    stream = await client.responses.create(
        model=SAM, stream=True, metadata={"include_confidence": "true"},
        input=[{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": concept}, {"type": "input_image", "image_url": image_data_url(jpeg)}]}],
    )
    result = await sam.parse_responses_stream(stream, sam.image_segmentation_format()).final_result()
    if meter is not None:
        meter.sam(1)
    from PIL import Image

    width, height = Image.open(io.BytesIO(jpeg)).size
    objects = []
    for rec in result.records:
        if rec.kind != "mask":
            continue
        raster = sam.decode_mask_to_raster(rec.mask)
        b = rec.bounds
        # Masks are box-local rasters; one raster cell covers this many source pixels.
        cell = ((b.right - b.left) / rec.mask.width) * ((b.bottom - b.top) / rec.mask.height)
        objects.append({"object_id": rec.object_id, "confidence": rec.confidence, "box": [b.left, b.top, b.right, b.bottom],
                        "source_pixels": round(sum(raster) * cell)})
    total = sum(o["source_pixels"] for o in objects)
    return {"concept": concept, "image_size": [width, height], "objects": objects, "pixel_count": total,
            "fraction_of_frame": round(total / (width * height), 4), "outcome": getattr(result.outcome, "status", str(result.outcome))}
