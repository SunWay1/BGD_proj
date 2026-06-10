"""Generuje wykresy z istniejących plików CSV bez ponownego uruchamiania benchmarku.

Użycie:
  python generate_plots.py
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from reload_benchmark.plotting import (
    generate_backend_comparison_plots,
    generate_plots,
    generate_threshold_plots,
    generate_detection_plots,
)

ROOT = Path(__file__).parent
RESULTS = ROOT / "results"
PLOTS = RESULTS / "plots"
PLOTS.mkdir(parents=True, exist_ok=True)


def _load_csv(path: Path) -> list[dict]:
    if not path.exists():
        print(f"  [pominięto] brak pliku: {path}")
        return []
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key, val in row.items():
            if val == "" or val is None:
                row[key] = None
                continue
            try:
                row[key] = int(val)
            except ValueError:
                try:
                    row[key] = float(val)
                except ValueError:
                    pass
    print(f"  Wczytano {len(rows)} wierszy z {path.name}")
    return rows


print("=" * 60)
print("Generowanie wykresów z istniejących plików CSV")
print(f"Katalog wyników: {RESULTS}")
print("=" * 60)

bench_rows = _load_csv(RESULTS / "benchmark_results.csv")
if bench_rows:
    print("\n[1/3] Wykresy benchmarku...")
    generate_plots(bench_rows, PLOTS)
    print("  OK")

thr_rows = _load_csv(RESULTS / "threshold_results.csv")
if thr_rows:
    print("\n[2/3] Wykresy progu opłacalności...")
    generate_threshold_plots(thr_rows, PLOTS)
    print("  OK")

det_rows = _load_csv(RESULTS / "detection_results.csv")
if det_rows:
    print("\n[3/3] Wykresy metod detekcji...")
    generate_detection_plots(det_rows, PLOTS)
    print("  OK")

cmp_rows = _load_csv(RESULTS / "benchmark_backend_comparison.csv")
if cmp_rows:
    print("\n[4/4] Wykresy porównawcze SQLite vs PostgreSQL...")
    generate_backend_comparison_plots(cmp_rows, PLOTS)
    print("  OK")

png_count = len(list(PLOTS.glob("*.png")))
print(f"\nGOTOWE! Wygenerowano {png_count} wykresów w {PLOTS}")
