"""NASA FIRMS area API. A hotspot is a thermal anomaly, not a confirmed fire."""

import csv
import io
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .http import fetch_text

BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
NRT_SOURCES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT")
FIXTURE_FILE = "firms_viirs_snpp_kaltim.csv"
ATTRIBUTION = "NASA FIRMS (VIIRS and MODIS NRT)"


@dataclass(frozen=True)
class Hotspot:
    id: str
    source: str
    lat: float
    lon: float
    acquired_at: str  # ISO-8601 UTC
    satellite: str
    instrument: str
    confidence: str  # VIIRS: l|n|h, MODIS: 0-100
    frp: float | None
    daynight: str

    def to_dict(self) -> dict:
        return asdict(self)


def parse_csv(text: str, source: str) -> list[Hotspot]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        hhmm = row["acq_time"].zfill(4)
        at = datetime.strptime(f"{row['acq_date']} {hhmm}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
        lat, lon = float(row["latitude"]), float(row["longitude"])
        frp = row.get("frp")
        out.append(
            Hotspot(
                id=f"HS-{source}-{at:%Y%m%d%H%M}-{lat:.5f}-{lon:.5f}",
                source=source,
                lat=lat,
                lon=lon,
                acquired_at=at.isoformat(),
                satellite=row.get("satellite", ""),
                instrument=row.get("instrument", ""),
                confidence=row.get("confidence", ""),
                frp=float(frp) if frp not in (None, "") else None,
                daynight=row.get("daynight", ""),
            )
        )
    return out


def get_area(
    bbox: tuple[float, float, float, float],
    days: int,
    *,
    map_key: str,
    cache_dir: Path,
    fixtures_dir: Path,
    fixture: bool,
    source: str = "VIIRS_SNPP_NRT",
) -> list[Hotspot]:
    """Hotspots in bbox (west, south, east, north) over the last `days` (1 to 5)."""
    if not 1 <= days <= 5:
        raise ValueError("FIRMS day range must be 1 to 5")
    if fixture:
        text = (fixtures_dir / FIXTURE_FILE).read_text(encoding="utf-8")
        source = "VIIRS_SNPP_NRT"
    else:
        area = ",".join(f"{v:g}" for v in bbox)
        text = fetch_text(
            "firms",
            "GET",
            f"{BASE_URL}/{map_key}/{source}/{area}/{days}",
            cache_dir=cache_dir,
            cache_key={"source": source, "bbox": area, "days": days},
            ttl_s=15 * 60,
        )
    w, s, e, n = bbox
    return [h for h in parse_csv(text, source) if w <= h.lon <= e and s <= h.lat <= n]
