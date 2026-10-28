"""Share an incident: a short public summary and a static OG image (map crop with its hotspots plus a card).

Only what the public map already shows is used: rounded coordinates, satellite hotspots, the status, and
the nearest settlement from OSM. Citizen media and text never appear. The basemap is OpenStreetMap's
standard raster tiles, fetched rarely (a few tiles per image), cached for 30 days, and credited on the image.
"""

import hashlib
import io
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

W, H = 1200, 630
TILE = 256
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_TTL_S = 30 * 24 * 3600
USER_AGENT = "fight-karhutla/0.1 (+https://github.com/thrqsytr/fight-karhutla) share images"
WITA = timezone(timedelta(hours=8))
FONTS = {
    False: ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"],
    True: ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"],
}
HEAT = [(0, (255, 214, 107)), (5, (255, 171, 64)), (15, (255, 122, 26)), (40, (239, 68, 35)), (100, (179, 18, 46))]
STATUS_COLOR = {"pending_triage": (122, 139, 153), "triaging": (0, 100, 224), "likely_fire": (217, 45, 32),
                "possible_fire": (247, 144, 9), "unlikely": (18, 183, 106), "needs_more_info": (122, 90, 248)}

TEXT = {
    "id": {
        "status": {"pending_triage": "Menunggu triase", "triaging": "Triase berjalan", "likely_fire": "Kemungkinan besar api",
                   "possible_fire": "Mungkin api", "unlikely": "Kecil kemungkinan", "needs_more_info": "Perlu info tambahan"},
        "ai": "rekomendasi AI", "verified": "TERVERIFIKASI", "rejected": "DITOLAK VERIFIKATOR", "sim": "SIMULASI",
        "hotspots": "{n} titik panas", "frp": "FRP maks {v} MW", "near": "Dekat {name}, {km} km", "seen": "Terakhir terdeteksi {when} WITA",
        "tagline": "Triase karhutla terverifikasi", "emergency": "Bukan kanal darurat resmi. Darurat: hubungi 112.",
        "months": ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"],
    },
    "en": {
        "status": {"pending_triage": "Pending triage", "triaging": "Triage running", "likely_fire": "Likely fire",
                   "possible_fire": "Possible fire", "unlikely": "Unlikely", "needs_more_info": "Needs more info"},
        "ai": "AI recommendation", "verified": "VERIFIED", "rejected": "REJECTED BY VERIFIER", "sim": "SIMULATION",
        "hotspots": "{n} hotspots", "frp": "max FRP {v} MW", "near": "Near {name}, {km} km", "seen": "Last detected {when} WITA",
        "tagline": "Verified wildfire triage", "emergency": "Not an official emergency channel. Emergency: call 112.",
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    },
}


def _num(v: float, lang: str, digits: int = 1) -> str:
    s = f"{v:,.{digits}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if lang == "id" else s


def _when(iso: str, lang: str) -> str:
    d = datetime.fromisoformat(iso).astimezone(WITA)
    sep = "." if lang == "id" else ":"
    return f"{d.day} {TEXT[lang]['months'][d.month - 1]} {d.year}, {d.hour:02d}{sep}{d.minute:02d}"


def facts(inc: dict, evidence: list[dict], verdict: str | None, lang: str = "id") -> dict:
    """Everything the summary and the image say, from public data only."""
    T = TEXT[lang]
    status = T["status"].get(inc["status"], inc["status"])
    triaged = inc["status"] not in ("pending_triage", "triaging")
    osm = next((e for e in reversed(evidence) if e["type"] == "osm"), None)
    near = (osm or {}).get("payload", {}).get("nearest", {}).get("settlement") if osm else None
    lines = [T["hotspots"].format(n=inc["hotspot_count"])]
    if inc.get("max_frp") is not None:
        lines[0] += " · " + T["frp"].format(v=_num(inc["max_frp"], lang))
    place = T["near"].format(name=near["name"], km=_num(near["distance_m"] / 1000, lang)) if near and near.get("name") else None
    return {
        "id": inc["id"], "status": status, "status_key": inc["status"], "ai": T["ai"] if triaged and verdict is None else None,
        "verdict": verdict, "simulation": bool(inc.get("simulation")), "stats": lines[0], "place": place,
        "seen": T["seen"].format(when=_when(inc["last_seen"], lang)), "lat": inc["lat"], "lon": inc["lon"],
    }


def summary(f: dict, url: str, lang: str = "id") -> tuple[str, str]:
    """(title, description) for OG tags and the share text."""
    T = TEXT[lang]
    head = f"{f['id']} · {f['status']}" + (f" ({f['ai']})" if f["ai"] else "")
    if f["verdict"] == "approve":
        head += f" · {T['verified'].capitalize()}"
    parts = [f["stats"], f["place"], f["seen"]]
    desc = ". ".join(p for p in parts if p) + ". " + T["emergency"]
    return head, desc


def cache_key(inc: dict, verdict: str | None, lang: str) -> str:
    raw = "|".join(str(x) for x in (inc["status"], inc["hotspot_count"], inc["last_seen"], inc.get("max_frp"), verdict, lang, 2))
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


# Web Mercator
def _px(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = TILE * 2 ** z
    s = math.sin(math.radians(max(min(lat, 85), -85)))
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def fetch_tile(z: int, x: int, y: int, cache_dir: Path) -> Image.Image:
    p = cache_dir / "tiles" / str(z) / str(x) / f"{y}.png"
    if not (p.exists() and time.time() - p.stat().st_mtime < TILE_TTL_S):
        r = httpx.get(TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT}, timeout=10)
        r.raise_for_status()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
    return Image.open(p).convert("RGB")


def _font(size: int, bold: bool = False):
    for path in FONTS[bold]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _heat(frp: float | None) -> tuple[int, int, int]:
    c = HEAT[0][1]
    for stop, col in HEAT:
        if (frp or 0) >= stop:
            c = col
    return c


def render(f: dict, hotspots: list[dict], *, cache_dir: Path, lang: str = "id", tile_fetch=None) -> bytes:
    T = TEXT[lang]
    tile_fetch = tile_fetch or fetch_tile  # looked up at call time, so tests can replace it
    pts = [(h["lat"], h["lon"], h.get("frp")) for h in hotspots] or [(f["lat"], f["lon"], None)]
    # Zoom so the cluster fills about 420 px, between regional context (9) and street level (14).
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    span_deg = max(max(lats) - min(lats), (max(lons) - min(lons)) * math.cos(math.radians(f["lat"])), 0.002)
    z = int(max(9, min(14, math.floor(math.log2(420 / TILE * 360 / span_deg)))))
    cx, cy = _px((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2, z)
    # The card sits on the left, so the hotspots are centred in the right part of the image.
    ox, oy = cx - W * 0.66, cy - H * 0.47
    canvas = Image.new("RGB", (W, H), (233, 236, 239))
    tiles_ok = True
    for tx in range(math.floor(ox / TILE), math.floor((ox + W) / TILE) + 1):
        for ty in range(math.floor(oy / TILE), math.floor((oy + H) / TILE) + 1):
            try:
                tile = tile_fetch(z, tx % (2 ** z), ty, cache_dir)
                canvas.paste(tile, (int(tx * TILE - ox), int(ty * TILE - oy)))
            except Exception as exc:  # a missing tile leaves grey; the image still says what it must
                tiles_ok = False
                log.warning("share tile %s/%s/%s failed: %s", z, tx, ty, exc)
    # Soften the basemap so hotspots and text stand out.
    canvas = Image.blend(canvas, Image.new("RGB", (W, H), (245, 247, 249)), 0.25).convert("RGBA")
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    xs = [_px(la, lo, z)[0] - ox for la, lo, _ in pts]
    ys = [_px(la, lo, z)[1] - oy for la, lo, _ in pts]
    ring = max(36.0, max(math.dist((x, y), (sum(xs) / len(xs), sum(ys) / len(ys))) for x, y in zip(xs, ys)) + 26)
    rc = (sum(xs) / len(xs), sum(ys) / len(ys))
    sc = STATUS_COLOR.get(f["status_key"], (122, 139, 153))
    d.ellipse([rc[0] - ring, rc[1] - ring, rc[0] + ring, rc[1] + ring], outline=(*sc, 230), width=4, fill=(*sc, 28))
    for (la, lo, frp), x, y in sorted(zip(pts, xs, ys), key=lambda t: t[0][2] or 0):
        r = 7 + min(9, (frp or 0) / 6)
        d.ellipse([x - r - 2, y - r - 2, x + r + 2, y + r + 2], fill=(255, 255, 255, 230))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*_heat(frp), 255))
    canvas = Image.alpha_composite(canvas, over)

    # Card
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    c = ImageDraw.Draw(card)
    x0, y0, x1, y1 = 36, 150, 640, H - 36
    c.rounded_rectangle([x0, y0, x1, y1], radius=22, fill=(28, 43, 51, 236))
    y = y0 + 30
    c.ellipse([x0 + 30, y + 3, x0 + 46, y + 19], fill=(0, 100, 224, 255))
    c.text((x0 + 56, y), "FIGHT KARHUTLA", font=_font(19, True), fill=(255, 255, 255))
    c.text((x0 + 56 + c.textlength("FIGHT KARHUTLA  ", font=_font(19, True)), y), T["tagline"], font=_font(19), fill=(170, 187, 199))
    y += 46
    c.text((x0 + 30, y), f["id"], font=_font(46, True), fill=(255, 255, 255))
    y += 70
    label = f["status"] + (f"  ·  {f['ai']}" if f["ai"] else "")
    lw = c.textlength(label, font=_font(24, True))
    c.rounded_rectangle([x0 + 30, y, x0 + 30 + lw + 32, y + 42], radius=21, fill=(*sc, 255))
    c.text((x0 + 46, y + 7), label, font=_font(24, True), fill=(255, 255, 255))
    y += 64
    for line in (f["stats"], f["place"], f["seen"]):
        if line:
            c.text((x0 + 30, y), line, font=_font(25), fill=(232, 238, 242))
            y += 38
    c.text((x0 + 30, y1 - 44), T["emergency"], font=_font(18), fill=(170, 187, 199))
    # Badges
    bx = W - 36
    for text, col in ((T["verified"], (6, 118, 71)) if f["verdict"] == "approve" else (T["rejected"], (180, 35, 24)) if f["verdict"] == "reject" else (None, None),
                      (T["sim"], (181, 71, 8)) if f["simulation"] else (None, None)):
        if not text:
            continue
        tw = c.textlength(text, font=_font(20, True))
        c.rounded_rectangle([bx - tw - 32, 36, bx, 76], radius=20, fill=(*col, 245))
        c.text((bx - tw - 16, 44), text, font=_font(20, True), fill=(255, 255, 255))
        bx -= tw + 44
    attrib = ("© OpenStreetMap contributors" if tiles_ok else "Map tiles unavailable") + " · NASA FIRMS"
    aw = c.textlength(attrib, font=_font(15))
    c.rounded_rectangle([W - aw - 30, H - 34, W - 8, H - 8], radius=8, fill=(255, 255, 255, 210))
    c.text((W - aw - 19, H - 30), attrib, font=_font(15), fill=(60, 72, 82))
    img = Image.alpha_composite(canvas, card).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
