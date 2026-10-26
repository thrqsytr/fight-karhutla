"""Shared HTTP access for data sources: file cache, 10 s timeout, bounded retry on transient failures only."""

import hashlib
import json
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

TIMEOUT_S = 10.0
MAX_RETRIES = 2
BACKOFF_S = (1.0, 3.0)
USER_AGENT = "fight-karhutla/0.1 (+https://github.com/thrqsytr/fight-karhutla)"


class SourceError(RuntimeError):
    """A source failed. The caller records the part as unknown instead of stopping triage."""


def _cache_path(cache_dir: Path, source: str, key: dict) -> Path:
    digest = hashlib.sha256(json.dumps({"source": source, **key}, sort_keys=True).encode()).hexdigest()
    return cache_dir / source / f"{digest}.txt"


def fetch_text(
    source: str,
    method: str,
    url: str,
    *,
    cache_dir: Path,
    cache_key: dict,
    ttl_s: int,
    params: dict | None = None,
    data: dict | None = None,
) -> str:
    """Return the response body, from cache when fresh. Empty bodies are never cached."""
    path = _cache_path(cache_dir, source, cache_key)
    if path.exists() and time.time() - path.stat().st_mtime < ttl_s:
        return path.read_text(encoding="utf-8")

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = httpx.request(
                method, url, params=params, data=data, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            log.warning("%s transient error on attempt %d: %r", source, attempt + 1, exc)
        else:
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = SourceError(f"{source} HTTP {resp.status_code}")
                log.warning("%s transient HTTP %d on attempt %d", source, resp.status_code, attempt + 1)
            elif resp.status_code >= 400:
                # A rejection is an answer. Retrying only repeats it.
                raise SourceError(f"{source} HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                body = resp.text
                if body.strip():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(body, encoding="utf-8")
                return body
        if attempt < MAX_RETRIES:
            delay = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
            log.info("%s retrying in %.1f s", source, delay)
            time.sleep(delay)
    raise SourceError(f"{source} failed after {MAX_RETRIES + 1} attempts: {last_exc!r}")
