"""Single entry-point that re-runs all benchmarks with one recorded seed, then
generates figures and tables so every number in the writeup comes from the same
run (BUG 3 fix).

Usage:
    python analysis/run_all.py [seed]

The seed defaults to 42. Pass any integer to override.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(seed: int = 42) -> None:
    print(f"=== SCADS run_all  seed={seed} ===\n")

    # --- benchmarks ----------------------------------------------------------
    for mod_name, label in [
        ("benchmarks.bench_index", "index"),
        ("benchmarks.bench_cache", "cache"),
        ("benchmarks.bench_security", "security"),
    ]:
        print(f"[bench] {label} ...", flush=True)
        mod = importlib.import_module(mod_name)
        mod.run(base_seed=seed)

    print()

    # --- figures -------------------------------------------------------------
    print("[figures] generating ...", flush=True)
    import analysis.generate_figures as fig_mod
    fig_mod.generate(results_dir=ROOT / "results")

    # --- tables --------------------------------------------------------------
    print("[tables] generating ...", flush=True)
    import analysis.tables as tbl_mod
    tbl_mod.generate(results_dir=ROOT / "results")

    print(f"\n=== done  seed={seed} ===")


if __name__ == "__main__":
    _seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42
    main(seed=_seed)
