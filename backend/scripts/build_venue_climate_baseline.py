from __future__ import annotations

import json
import statistics
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VENUES_PATH = ROOT / "data" / "seed" / "world-cup-2026-venues.json"
OUTPUT_PATH = ROOT / "data" / "seed" / "world-cup-2026-venue-climate.json"
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
YEARS = range(2015, 2025)
MONTHS = (6, 7)
DAILY_FIELDS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "relative_humidity_2m_mean",
    "precipitation_sum",
    "wind_speed_10m_max",
)


def _fetch_archive(latitude: float, longitude: float) -> dict:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": f"{min(YEARS)}-06-01",
        "end_date": f"{max(YEARS)}-07-31",
        "daily": ",".join(DAILY_FIELDS),
        "timezone": "UTC",
    }
    request = urllib.request.Request(
        OPEN_METEO_ARCHIVE_URL + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": "world-cup-team-profile/1.0"},
    )
    for attempt in range(4):
        try:
            return json.loads(urllib.request.urlopen(request, timeout=30).read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                raise
            time.sleep(20 * (attempt + 1))
    raise RuntimeError("unreachable")


def _mean(values: list[float]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return round(statistics.fmean(clean), 1) if clean else None


def _month_baseline(daily: dict, month: int) -> dict:
    indexes = [idx for idx, value in enumerate(daily["time"]) if int(value[5:7]) == month]
    def values(field: str) -> list[float]:
        return [daily[field][idx] for idx in indexes if daily[field][idx] is not None]
    precipitation = values("precipitation_sum")
    return {
        "sample_days": len(indexes),
        "temperature_2m_mean_c": _mean(values("temperature_2m_mean")),
        "temperature_2m_max_mean_c": _mean(values("temperature_2m_max")),
        "temperature_2m_min_mean_c": _mean(values("temperature_2m_min")),
        "relative_humidity_2m_mean_pct": _mean(values("relative_humidity_2m_mean")),
        "precipitation_sum_mean_mm": _mean(precipitation),
        "rain_day_rate": round(sum(1 for value in precipitation if value >= 1.0) / len(precipitation), 3) if precipitation else None,
        "wind_speed_10m_max_mean_kmh": _mean(values("wind_speed_10m_max")),
    }


def build_baseline() -> dict:
    venue_payload = json.loads(VENUES_PATH.read_text(encoding="utf-8"))
    venues = {}
    source_hashes = {}
    for venue, metadata in venue_payload["venues"].items():
        merged_daily = {field: [] for field in ("time", *DAILY_FIELDS)}
        archive = _fetch_archive(metadata["latitude"], metadata["longitude"])
        source_hashes[venue] = sha256(json.dumps(archive, sort_keys=True).encode()).hexdigest()
        daily = archive["daily"]
        for idx, day in enumerate(daily["time"]):
            year = int(day[:4])
            month = int(day[5:7])
            if year not in YEARS or month not in MONTHS:
                continue
            for field in merged_daily:
                merged_daily[field].append(daily[field][idx])
        time.sleep(2)
        venues[venue] = {
            "city": metadata["city"],
            "timezone": metadata["timezone"],
            "latitude": metadata["latitude"],
            "longitude": metadata["longitude"],
            "baseline_by_month": {str(month): _month_baseline(merged_daily, month) for month in MONTHS},
        }
    return {
        "source": {
            "provider": "open_meteo_historical_archive",
            "source_url": OPEN_METEO_ARCHIVE_URL,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "years": [min(YEARS), max(YEARS)],
            "months": list(MONTHS),
            "daily_fields": list(DAILY_FIELDS),
            "note": "Historical climate baseline only. This is not a match-day forecast.",
            "source_hashes_sha256": source_hashes,
        },
        "venues": venues,
    }


def main() -> None:
    payload = build_baseline()
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({"venues": len(payload["venues"]), "output": str(OUTPUT_PATH)})


if __name__ == "__main__":
    main()
