"""NYC TLC Yellow Taxi trip loader.

Real data: any single CSV or Parquet file in data/raw/ whose name contains 'yellow_tripdata'
(e.g. yellow_tripdata_2023-01.csv).  Download from the NYC TLC "Trip Record Data" page.
Required columns: tpep_pickup_datetime, tpep_dropoff_datetime, trip_distance, PULocationID.

Synthetic fallback: seeded generation of up to 100,000 geo-correlated trips mapped to 5 NYC
boroughs.  is_synthetic=True is set and a disclosure line is printed.

If a real file is present, a scipy KS goodness-of-fit test compares the synthetic generator
against the real distribution for trip_distance and pickup_hour; divergence is printed.
"""
from __future__ import annotations

import math
import os
import random
from pathlib import Path
from typing import Any

_RAW_DIR = Path(__file__).parent / "raw"

_BOROUGHS = {
    1: "Manhattan",
    2: "Bronx",
    3: "Brooklyn",
    4: "Queens",
    5: "Staten Island",
}
_BOROUGH_IDS = list(_BOROUGHS.keys())

# Rough centre coordinates per borough — used for synthetic geo-correlation.
_BOROUGH_CENTRES = {
    1: (40.7831, -73.9712),
    2: (40.8448, -73.8648),
    3: (40.6501, -73.9496),
    4: (40.7282, -73.7949),
    5: (40.5795, -74.1502),
}

# Borough weights: Manhattan is heavily over-represented in real TLC data.
_BOROUGH_WEIGHTS = [0.55, 0.08, 0.18, 0.16, 0.03]

_TRIP_TYPES = ["street-hail", "dispatch"]


def _find_real_file() -> Path | None:
    for p in _RAW_DIR.glob("*yellow_tripdata*"):
        if p.suffix in {".csv", ".parquet"}:
            return p
    return None


def _parse_real(path: Path, n: int) -> list[dict]:
    import pandas as pd  # type: ignore

    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, nrows=n)

    df = df.head(n)
    df["tpep_pickup_datetime"] = pd.to_datetime(df["tpep_pickup_datetime"], errors="coerce")
    df["pickup_hour"] = df["tpep_pickup_datetime"].dt.hour
    df["borough_id"] = (df["PULocationID"] % 5 + 1).astype(int)
    df["trip_type"] = "street-hail"
    df["trip_distance"] = pd.to_numeric(df.get("trip_distance", 1.5), errors="coerce").fillna(1.5)

    records = df[["pickup_hour", "borough_id", "trip_type", "trip_distance", "PULocationID"]].to_dict(orient="records")
    return records


def _geo_correlated_distance(rng: random.Random, borough_id: int) -> float:
    """Longer trips for outer boroughs (further from Manhattan)."""
    base = {1: 1.8, 2: 4.2, 3: 3.5, 4: 5.1, 5: 6.8}[borough_id]
    return max(0.1, rng.lognormvariate(math.log(base), 0.6))


def _synthetic_record(rng: random.Random) -> dict[str, Any]:
    borough_id = rng.choices(_BOROUGH_IDS, weights=_BOROUGH_WEIGHTS, k=1)[0]
    pickup_hour = rng.randint(0, 23)
    trip_distance = _geo_correlated_distance(rng, borough_id)
    lat, lon = _BOROUGH_CENTRES[borough_id]
    lat += rng.gauss(0, 0.02)
    lon += rng.gauss(0, 0.02)
    return {
        "pickup_hour": pickup_hour,
        "borough_id": borough_id,
        "borough_name": _BOROUGHS[borough_id],
        "trip_type": rng.choice(_TRIP_TYPES),
        "trip_distance": round(trip_distance, 3),
        "pickup_lat": round(lat, 6),
        "pickup_lon": round(lon, 6),
        "PULocationID": borough_id * 10 + rng.randint(1, 9),
    }


def _ks_divergence(real_records: list[dict], seed: int, n_synth: int = 5000) -> None:
    """Print KS goodness-of-fit between synthetic generator and real data."""
    try:
        from scipy import stats  # type: ignore
    except ImportError:
        print("[taxi.py] scipy not available — skipping KS divergence test.")
        return

    rng = random.Random(seed)
    synth = [_synthetic_record(rng) for _ in range(n_synth)]

    real_dist = [r["trip_distance"] for r in real_records if isinstance(r.get("trip_distance"), (int, float))]
    synth_dist = [r["trip_distance"] for r in synth]
    real_hour = [r["pickup_hour"] for r in real_records if isinstance(r.get("pickup_hour"), (int, float))]
    synth_hour = [r["pickup_hour"] for r in synth]

    if real_dist and synth_dist:
        ks_dist = stats.ks_2samp(real_dist[:n_synth], synth_dist)
        print(f"[taxi.py KS fit] trip_distance: statistic={ks_dist.statistic:.4f}, p={ks_dist.pvalue:.4f}")
        if ks_dist.pvalue < 0.05:
            print("  -> Significant divergence in trip_distance distribution.")
        else:
            print("  -> trip_distance distributions are not significantly different.")

    if real_hour and synth_hour:
        ks_hour = stats.ks_2samp(real_hour[:n_synth], synth_hour)
        print(f"[taxi.py KS fit] pickup_hour:    statistic={ks_hour.statistic:.4f}, p={ks_hour.pvalue:.4f}")
        if ks_hour.pvalue < 0.05:
            print("  -> Significant divergence in pickup_hour distribution.")
        else:
            print("  -> pickup_hour distributions are not significantly different.")


def load(n: int = 100_000, seed: int = 42) -> dict:
    """Return {'records': list[dict], 'is_synthetic': bool, 'n': int}.

    n is capped at 100,000.
    Prefers a real NYC TLC yellow_tripdata file in data/raw/; falls back to seeded synthetic.
    If a real file is found, runs a KS goodness-of-fit check and prints divergence.
    """
    n = min(n, 100_000)
    real_path = _find_real_file()

    if real_path is not None:
        records = _parse_real(real_path, n)
        print(f"[taxi.py] Loaded {len(records):,} real records from {real_path.name}.")
        _ks_divergence(records, seed=seed)
        return {"records": records, "is_synthetic": False, "n": len(records)}

    print(
        f"[DISCLOSURE] data/taxi.py: no real NYC TLC file found in {_RAW_DIR}. "
        f"Generating {n:,} SYNTHETIC taxi trip records (seed={seed}). "
        "This data is NOT from the NYC TLC dataset."
    )
    rng = random.Random(seed)
    records = [_synthetic_record(rng) for _ in range(n)]
    return {"records": records, "is_synthetic": True, "n": len(records)}


if __name__ == "__main__":
    result = load(n=1_000)
    assert len(result["records"]) == 1_000, f"Expected 1000, got {len(result['records'])}"
    print(
        f"Taxi self-check OK: {len(result['records']):,} records, "
        f"is_synthetic={result['is_synthetic']}"
    )
