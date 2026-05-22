"""KDD Cup 1999 (10% subset) loader.

Real data: data/raw/kddcup.data_10_percent — 494,021 records, no header, 42 comma-separated
fields (41 features + label).  Download from the UCI ML Repository "KDD Cup 1999 Data" page.

Synthetic fallback: seeded generation with the same 42-column schema and realistic value
distributions.  is_synthetic=True is set on the returned dict and a disclosure line is printed.
"""
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any

_REAL_PATH = Path(__file__).parent / "raw" / "kddcup.data_10_percent"

# KDD Cup 1999 column names (41 features + label), positional — no header in the file.
_COLUMNS = [
    "duration", "protocol_type", "service", "flag", "src_bytes", "dst_bytes",
    "land", "wrong_fragment", "urgent", "hot", "num_failed_logins", "logged_in",
    "num_compromised", "root_shell", "su_attempted", "num_root", "num_file_creations",
    "num_shells", "num_access_files", "num_outbound_cmds", "is_host_login",
    "is_guest_login", "count", "srv_count", "serror_rate", "srv_serror_rate",
    "rerror_rate", "srv_rerror_rate", "same_srv_rate", "diff_srv_rate",
    "srv_diff_host_rate", "dst_host_count", "dst_host_srv_count",
    "dst_host_same_srv_rate", "dst_host_diff_srv_rate",
    "dst_host_same_src_port_rate", "dst_host_srv_diff_host_rate",
    "dst_host_serror_rate", "dst_host_srv_serror_rate",
    "dst_host_rerror_rate", "dst_host_srv_rerror_rate", "label",
]

_PROTOCOLS = ["tcp", "udp", "icmp"]
_SERVICES = ["http", "ftp", "smtp", "ssh", "dns", "finger", "telnet", "pop3", "imap4", "other"]
_FLAGS = ["SF", "S0", "REJ", "RSTO", "RSTR", "SH", "S1", "S2", "S3", "OTH"]
_LABELS = ["normal.", "smurf.", "neptune.", "back.", "teardrop.", "pod.", "land.", "ipsweep.",
           "portsweep.", "satan.", "nmap.", "warezclient.", "warezmaster.", "guess_passwd."]

_VALID_SIZES = {50_000, 100_000, 200_000, 300_000, 494_021}


def _parse_real(n: int) -> list[dict]:
    records: list[dict] = []
    with _REAL_PATH.open() as fh:
        for i, line in enumerate(fh):
            if i >= n:
                break
            parts = line.rstrip(".\n").split(",")
            # The label field in the raw file ends with '.' — already stripped above.
            if len(parts) != 42:
                continue
            rec: dict[str, Any] = {}
            for col, val in zip(_COLUMNS, parts):
                try:
                    rec[col] = int(val)
                except ValueError:
                    try:
                        rec[col] = float(val)
                    except ValueError:
                        rec[col] = val
            records.append(rec)
    return records


def _synthetic_record(rng: random.Random) -> dict[str, Any]:
    proto = rng.choice(_PROTOCOLS)
    src_bytes = rng.randint(0, 1_000_000)
    dst_bytes = rng.randint(0, 1_000_000)
    return {
        "duration": rng.randint(0, 58_329),
        "protocol_type": proto,
        "service": rng.choice(_SERVICES),
        "flag": rng.choice(_FLAGS),
        "src_bytes": src_bytes,
        "dst_bytes": dst_bytes,
        "land": rng.randint(0, 1),
        "wrong_fragment": rng.randint(0, 3),
        "urgent": rng.randint(0, 3),
        "hot": rng.randint(0, 30),
        "num_failed_logins": rng.randint(0, 5),
        "logged_in": rng.randint(0, 1),
        "num_compromised": rng.randint(0, 884),
        "root_shell": rng.randint(0, 1),
        "su_attempted": rng.randint(0, 1),
        "num_root": rng.randint(0, 993),
        "num_file_creations": rng.randint(0, 28),
        "num_shells": rng.randint(0, 5),
        "num_access_files": rng.randint(0, 9),
        "num_outbound_cmds": 0,
        "is_host_login": rng.randint(0, 1),
        "is_guest_login": rng.randint(0, 1),
        "count": rng.randint(0, 511),
        "srv_count": rng.randint(0, 511),
        "serror_rate": round(rng.random(), 2),
        "srv_serror_rate": round(rng.random(), 2),
        "rerror_rate": round(rng.random(), 2),
        "srv_rerror_rate": round(rng.random(), 2),
        "same_srv_rate": round(rng.random(), 2),
        "diff_srv_rate": round(rng.random(), 2),
        "srv_diff_host_rate": round(rng.random(), 2),
        "dst_host_count": rng.randint(0, 255),
        "dst_host_srv_count": rng.randint(0, 255),
        "dst_host_same_srv_rate": round(rng.random(), 2),
        "dst_host_diff_srv_rate": round(rng.random(), 2),
        "dst_host_same_src_port_rate": round(rng.random(), 2),
        "dst_host_srv_diff_host_rate": round(rng.random(), 2),
        "dst_host_serror_rate": round(rng.random(), 2),
        "dst_host_srv_serror_rate": round(rng.random(), 2),
        "dst_host_rerror_rate": round(rng.random(), 2),
        "dst_host_srv_rerror_rate": round(rng.random(), 2),
        "label": rng.choice(_LABELS),
    }


def load(n: int = 494_021, seed: int = 42) -> dict:
    """Return {'records': list[dict], 'is_synthetic': bool, 'n': int}.

    n must be one of {50000, 100000, 200000, 300000, 494021}.
    Prefers data/raw/kddcup.data_10_percent; falls back to seeded synthetic data.
    """
    if n not in _VALID_SIZES:
        raise ValueError(f"n must be one of {sorted(_VALID_SIZES)}, got {n}")

    if _REAL_PATH.exists():
        records = _parse_real(n)
        return {"records": records, "is_synthetic": False, "n": len(records)}

    print(
        f"[DISCLOSURE] data/kdd.py: real file not found at {_REAL_PATH}. "
        f"Generating {n:,} SYNTHETIC KDD records (seed={seed}). "
        "This data is NOT from the KDD Cup 1999 dataset."
    )
    rng = random.Random(seed)
    records = [_synthetic_record(rng) for _ in range(n)]
    return {"records": records, "is_synthetic": True, "n": len(records)}


if __name__ == "__main__":
    result = load(n=50_000)
    assert len(result["records"]) == 50_000, f"Expected 50000, got {len(result['records'])}"
    print(
        f"KDD self-check OK: {len(result['records']):,} records, "
        f"is_synthetic={result['is_synthetic']}"
    )
