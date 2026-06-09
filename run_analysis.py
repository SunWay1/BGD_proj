"""Skrypt uruchamiający pełną analizę benchmark z absolutnymi ścieżkami.

Użycie:
  python run_analysis.py          # pełny benchmark (10-15 min)
  python run_analysis.py --quick  # szybki test (1-2 min)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from reload_benchmark.benchmark import (
    BenchmarkConfig, ThresholdConfig, DetectionConfig, StorageConfig,
    run_benchmark, run_threshold, run_detection_comparison, run_storage_comparison
)

ROOT = Path(__file__).parent
RESULTS = ROOT / "results"
PLOTS = RESULTS / "plots"
RESULTS.mkdir(exist_ok=True)
PLOTS.mkdir(exist_ok=True)

QUICK = "--quick" in sys.argv

t_total = time.perf_counter()

# ---------------------------------------------------------------------------
# 1. Benchmark główny
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
if QUICK:
    print("BENCHMARK (szybki): 5 scenariuszy + append + deletes")
    print("  rozmiary: 5 000 i 20 000, wariant: short, 1 partia, 2 uruchomienia")
    cfg_bench = BenchmarkConfig(
        db_path=RESULTS / "benchmark.sqlite",
        results_csv=RESULTS / "benchmark_results.csv",
        plots_dir=PLOTS,
        sizes=[5_000, 20_000],
        variants=["short"],
        batch_sizes=[2_000],
        n_runs=2,
        create_plots=True,
    )
else:
    print("BENCHMARK: Full reload vs Inkrementalny - 7 scenariuszy")
    print("  rozmiary: 10 000 / 50 000 / 100 000, warianty: short + long, 2 partii, 3 uruchomienia")
    cfg_bench = BenchmarkConfig(
        db_path=RESULTS / "benchmark.sqlite",
        results_csv=RESULTS / "benchmark_results.csv",
        plots_dir=PLOTS,
        sizes=[10_000, 50_000, 100_000],
        variants=["short", "long"],
        batch_sizes=[5_000, 10_000],
        n_runs=3,
        create_plots=True,
    )
print("=" * 65)

t0 = time.perf_counter()
rows = run_benchmark(cfg_bench)
print(f"  >> Zakończono {len(rows)} uruchomień w {time.perf_counter()-t0:.1f}s\n")

# ---------------------------------------------------------------------------
# 2. Analiza progu opłacalności
# ---------------------------------------------------------------------------
print("=" * 65)
if QUICK:
    print("THRESHOLD (szybki): 2 warianty x 8 punktów x 2 uruchomienia")
    cfg_thr = ThresholdConfig(
        db_path=RESULTS / "threshold.sqlite",
        results_csv=RESULTS / "threshold_results.csv",
        plots_dir=PLOTS,
        size=10_000,
        variants=["short", "medium"],
        batch_size=2_000,
        change_ratios=[0.0, 0.10, 0.25, 0.40, 0.50, 0.60, 0.75, 1.0],
        n_runs=2,
        create_plots=True,
    )
else:
    print("THRESHOLD: Próg opłacalności - 4 warianty tekstu x 16 punktów x 3 uruchomienia")
    cfg_thr = ThresholdConfig(
        db_path=RESULTS / "threshold.sqlite",
        results_csv=RESULTS / "threshold_results.csv",
        plots_dir=PLOTS,
        size=50_000,
        variants=["short", "medium", "long", "xlarge"],
        batch_size=5_000,
        change_ratios=[
            0.00, 0.01, 0.05, 0.10, 0.20, 0.30,
            0.40, 0.45, 0.50, 0.55, 0.60,
            0.70, 0.75, 0.80, 0.90, 1.00,
        ],
        n_runs=3,
        create_plots=True,
    )
print("=" * 65)

t0 = time.perf_counter()
rows = run_threshold(cfg_thr)
print(f"  >> Zakończono {len(rows)} pomiarów w {time.perf_counter()-t0:.1f}s\n")

# ---------------------------------------------------------------------------
# 3. Porównanie metod detekcji
# ---------------------------------------------------------------------------
print("=" * 65)
if QUICK:
    print("DETECTION (szybki): 3 metody x 3 uruchomienia, 5 000 rekordów")
    cfg_det = DetectionConfig(
        db_path=RESULTS / "detection.sqlite",
        results_csv=RESULTS / "detection_results.csv",
        plots_dir=PLOTS,
        size=5_000,
        variant="medium",
        batch_size=2_000,
        change_ratio=0.10,
        detection_methods=["hash_or_timestamp", "hash_only", "timestamp_only"],
        n_runs=3,
        create_plots=True,
    )
else:
    print("DETECTION: Porównanie metod detekcji - 50 000 rekordów x 5 uruchomień")
    cfg_det = DetectionConfig(
        db_path=RESULTS / "detection.sqlite",
        results_csv=RESULTS / "detection_results.csv",
        plots_dir=PLOTS,
        size=50_000,
        variant="long",
        batch_size=5_000,
        change_ratio=0.10,
        detection_methods=["hash_or_timestamp", "hash_only", "timestamp_only"],
        n_runs=5,
        create_plots=True,
    )
print("=" * 65)

t0 = time.perf_counter()
rows = run_detection_comparison(cfg_det)
print(f"  >> Zakończono {len(rows)} pomiarów w {time.perf_counter()-t0:.1f}s\n")

# ---------------------------------------------------------------------------
# 4. Porównanie baz in-memory vs na systemie plikowym
# ---------------------------------------------------------------------------

print("=" * 65)
if QUICK:
    print("STORAGE (szybki): 2 metody x 3 uruchomienia, 5 000 rekordów")
    cfg_store = StorageConfig(
        results_db_path=RESULTS / "storage.sqlite",
        file_db_path=RESULTS / "tmp.sqlite",
        results_csv=RESULTS / "storage_results.csv",
        plots_dir=PLOTS,
        sizes=[5_000, 20_000],
        variant="short",
        batch_sizes=[2_000],
        delete_ratio=0.10,
        n_runs=3,
        create_plots=True,
    )
else:
    print("STORAGE: Porównanie metod zapisu danych - 50 000 rekordów x 5 uruchomień")
    cfg_store = StorageConfig(
        results_db_path=RESULTS / "storage.sqlite",
        file_db_path=RESULTS / "tmp.sqlite",
        results_csv=RESULTS / "storage_results.csv",
        plots_dir=PLOTS,
        sizes=[10_000, 50_000, 100_000],
        variant="long",
        batch_sizes=[5_000, 10_000],
        delete_ratio=0.10,
        n_runs=5,
        create_plots=True,
    )
print("=" * 65)

t0 = time.perf_counter()
rows = run_storage_comparison(cfg_store)
print(f"  >> Zakończono {len(rows)} pomiarów w {time.perf_counter()-t0:.1f}s\n")

# ---------------------------------------------------------------------------
# Podsumowanie
# ---------------------------------------------------------------------------
png_count = len(list(PLOTS.glob("*.png")))
total_elapsed = time.perf_counter() - t_total
print("=" * 65)
print(f"GOTOWE! Całkowity czas: {total_elapsed:.1f}s")
print(f"Wykresy: {png_count} plików PNG w {PLOTS}")
print(f"Wyniki:  {RESULTS}")
print("=" * 65)
