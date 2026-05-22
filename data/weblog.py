"""Web log loader — fully synthetic.

All records are SYNTHETIC.  No real log file is used or required.  is_synthetic is always True
and a disclosure line is printed on every call.

Generates up to 100,000 HTTP access log entries with realistic timestamps, ~10 GeoIP regions,
HTTP methods, status codes, and log levels.  All generators take a seed for reproducibility.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

_GEO_REGIONS = [
    "us-east-1", "us-west-2", "eu-west-1", "eu-central-1",
    "ap-southeast-1", "ap-northeast-1", "sa-east-1", "af-south-1",
    "me-south-1", "ca-central-1",
]
# Traffic weights — us-east-1 and eu-west-1 are the busiest.
_REGION_WEIGHTS = [0.28, 0.18, 0.16, 0.10, 0.08, 0.07, 0.04, 0.03, 0.03, 0.03]

_METHODS = ["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"]
_METHOD_WEIGHTS = [0.60, 0.25, 0.08, 0.04, 0.02, 0.01]

_PATHS = [
    "/api/v1/records", "/api/v1/search", "/api/v1/users", "/api/v2/chunks",
    "/health", "/metrics", "/static/app.js", "/static/style.css",
    "/api/v1/auth/login", "/api/v1/auth/logout",
]

_STATUS_CODES = [200, 201, 204, 301, 302, 400, 401, 403, 404, 429, 500, 502, 503]
_STATUS_WEIGHTS = [0.50, 0.10, 0.05, 0.03, 0.03, 0.06, 0.05, 0.04, 0.06, 0.03, 0.02, 0.02, 0.01]

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_LEVEL_WEIGHTS = [0.05, 0.60, 0.20, 0.12, 0.03]

_EPOCH = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _random_ip(rng: random.Random, region: str) -> str:
    # Deterministic first octet per region for geo-correlation.
    first = {
        "us-east-1": 54, "us-west-2": 35, "eu-west-1": 18, "eu-central-1": 52,
        "ap-southeast-1": 13, "ap-northeast-1": 57, "sa-east-1": 177, "af-south-1": 196,
        "me-south-1": 157, "ca-central-1": 99,
    }.get(region, rng.randint(1, 254))
    return f"{first}.{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(1,254)}"


def _status_to_level(status: int) -> str:
    if status < 400:
        return "INFO"
    if status < 500:
        return "WARNING"
    return "ERROR"


def _synthetic_record(rng: random.Random, base_ts: datetime) -> dict[str, Any]:
    region = rng.choices(_GEO_REGIONS, weights=_REGION_WEIGHTS, k=1)[0]
    offset_s = rng.randint(0, 365 * 24 * 3600)
    ts = base_ts + timedelta(seconds=offset_s)
    status = rng.choices(_STATUS_CODES, weights=_STATUS_WEIGHTS, k=1)[0]
    method = rng.choices(_METHODS, weights=_METHOD_WEIGHTS, k=1)[0]
    # Log level: derive from status for consistency, occasionally override.
    level = _status_to_level(status) if rng.random() < 0.80 else rng.choices(_LOG_LEVELS, weights=_LEVEL_WEIGHTS, k=1)[0]
    return {
        "timestamp": ts.isoformat(),
        "timestamp_hour": ts.hour,
        "geo_region": region,
        "ip_address": _random_ip(rng, region),
        "method": method,
        "path": rng.choice(_PATHS),
        "status_code": status,
        "response_bytes": rng.randint(64, 65536),
        "response_ms": round(rng.lognormvariate(3.5, 1.0), 1),
        "log_level": level,
    }


def load(n: int = 100_000, seed: int = 42) -> dict:
    """Return {'records': list[dict], 'is_synthetic': bool, 'n': int}.

    n is capped at 100,000.  Always synthetic — no real file is used.
    """
    n = min(n, 100_000)
    print(
        f"[DISCLOSURE] data/weblog.py: generating {n:,} SYNTHETIC web log records (seed={seed}). "
        "No real log file is used."
    )
    rng = random.Random(seed)
    records = [_synthetic_record(rng, _EPOCH) for _ in range(n)]
    return {"records": records, "is_synthetic": True, "n": len(records)}


if __name__ == "__main__":
    result = load(n=1_000)
    assert len(result["records"]) == 1_000, f"Expected 1000, got {len(result['records'])}"
    print(
        f"Weblog self-check OK: {len(result['records']):,} records, "
        f"is_synthetic={result['is_synthetic']}"
    )
