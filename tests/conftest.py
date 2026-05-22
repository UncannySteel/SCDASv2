"""Shared pytest fixtures: small sample record lists for each dataset shape.

These are deliberately tiny and deterministic so unit tests can assert exact
chunk counts and round-trip behavior.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def kdd_records() -> list[dict]:
    """KDD-shaped records (network intrusion subset columns)."""
    return [
        {"src_bytes": 0, "dst_bytes": 0, "protocol_type": "tcp", "label": "normal"},
        {"src_bytes": 0, "dst_bytes": 0, "protocol_type": "tcp", "label": "smurf"},
        {"src_bytes": 1, "dst_bytes": 0, "protocol_type": "tcp", "label": "normal"},
        {"src_bytes": 0, "dst_bytes": 1, "protocol_type": "udp", "label": "normal"},
        {"src_bytes": 24, "dst_bytes": 8, "protocol_type": "tcp", "label": "normal"},
        {"src_bytes": 5, "dst_bytes": 3, "protocol_type": "icmp", "label": "normal"},
    ]


@pytest.fixture
def taxi_records() -> list[dict]:
    """NYC Taxi-shaped records (geo-correlated trips)."""
    return [
        {"pickup_hour": 8, "borough_id": 1, "trip_type": "street_hail"},
        {"pickup_hour": 8, "borough_id": 1, "trip_type": "street_hail"},
        {"pickup_hour": 8, "borough_id": 2, "trip_type": "street_hail"},
        {"pickup_hour": 9, "borough_id": 1, "trip_type": "dispatch"},
        {"pickup_hour": 32, "borough_id": 1, "trip_type": "street_hail"},  # 32 % 24 == 8
    ]


@pytest.fixture
def weblog_records() -> list[dict]:
    """Web-log-shaped records (timestamps, geo regions, log levels)."""
    return [
        {"timestamp_hour": 0, "geo_region": "us-east", "log_level": "INFO"},
        {"timestamp_hour": 0, "geo_region": "us-east", "log_level": "INFO"},
        {"timestamp_hour": 0, "geo_region": "us-east", "log_level": "ERROR"},
        {"timestamp_hour": 1, "geo_region": "eu-west", "log_level": "INFO"},
        {"timestamp": 3600, "geo_region": "eu-west", "log_level": "INFO"},  # hour 1 from epoch
    ]
