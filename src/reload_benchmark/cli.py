from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from .benchmark import BenchmarkConfig, DetectionConfig, ThresholdConfig
from .benchmark import run_benchmark, run_detection_comparison, run_threshold
from .data_generator import write_documents_to_disk


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Porównanie pełnego przeładowania i ładowania inkrementalnego do SQLite."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------------
    # benchmark – pełny zestaw scenariuszy
    # ------------------------------------------------------------------
    benchmark = subparsers.add_parser("benchmark", help="Uruchom scenariusze benchmarku")
    benchmark.add_argument("--db", type=Path, default=Path("results/benchmark.sqlite"))
    benchmark.add_argument("--csv", type=Path, default=Path("results/benchmark_results.csv"))
    benchmark.add_argument("--plots-dir", type=Path, default=Path("results/plots"))
    benchmark.add_argument(
        "--sizes", type=int, nargs="+", default=[10_000, 50_000, 100_000],
        help="Rozmiary zbiorów danych",
    )
    benchmark.add_argument(
        "--variants", choices=["short", "medium", "long", "xlarge"], nargs="+",
        default=["short", "long"], help="Warianty długości tekstu",
    )
    benchmark.add_argument("--batch-size", type=int, default=None)
    benchmark.add_argument(
        "--batch-sizes", type=int, nargs="+", default=None,
        help="Lista rozmiarów partii do porównania",
    )
    benchmark.add_argument("--new-ratio", type=float, default=0.10)
    benchmark.add_argument("--change-ratio", type=float, default=0.10)
    benchmark.add_argument("--high-change-ratio", type=float, default=0.50)
    benchmark.add_argument("--delete-ratio", type=float, default=0.05,
                           help="Ułamek rekordów usuniętych ze źródła (scenariusz with_deletes)")
    benchmark.add_argument(
        "--n-runs", type=int, default=3,
        help="Liczba powtórzeń każdego scenariusza (do obliczeń statystycznych)",
    )
    benchmark.add_argument("--no-plots", action="store_true")

    # ------------------------------------------------------------------
    # threshold – analiza progu opłacalności
    # ------------------------------------------------------------------
    threshold = subparsers.add_parser("threshold", help="Wyznacz próg opłacalności inkrementalnego")
    threshold.add_argument("--db", type=Path, default=Path("results/threshold.sqlite"))
    threshold.add_argument("--csv", type=Path, default=Path("results/threshold_results.csv"))
    threshold.add_argument("--plots-dir", type=Path, default=Path("results/plots"))
    threshold.add_argument("--size", type=int, default=50_000)
    threshold.add_argument(
        "--variants", choices=["short", "medium", "long", "xlarge"], nargs="+",
        default=["short", "medium", "long", "xlarge"],
        help="Warianty tekstu – pozwala zbadać wpływ rozmiaru dokumentu na próg",
    )
    threshold.add_argument("--batch-size", type=int, default=5_000)
    threshold.add_argument(
        "--change-ratios", type=float, nargs="+",
        # Zagęszczone próbkowanie w przedziale 40-60% (okolice progu opłacalności)
        default=[
            0.00, 0.01, 0.05, 0.10, 0.20, 0.30,
            0.40, 0.45, 0.50, 0.55, 0.60,
            0.70, 0.75, 0.80, 0.90, 1.00,
        ],
        help="Lista wartości change_ratio do przetestowania",
    )
    threshold.add_argument(
        "--n-runs", type=int, default=3,
        help="Liczba powtórzeń każdego punktu pomiarowego",
    )
    threshold.add_argument("--no-plots", action="store_true")

    # ------------------------------------------------------------------
    # detection – porównanie metod detekcji zmian
    # ------------------------------------------------------------------
    detection = subparsers.add_parser(
        "detection",
        help="Porównaj metody detekcji zmian: hash, timestamp, hash+timestamp",
    )
    detection.add_argument("--db", type=Path, default=Path("results/detection.sqlite"))
    detection.add_argument("--csv", type=Path, default=Path("results/detection_results.csv"))
    detection.add_argument("--plots-dir", type=Path, default=Path("results/plots"))
    detection.add_argument("--size", type=int, default=50_000)
    detection.add_argument(
        "--variant", choices=["short", "medium", "long", "xlarge"], default="long",
        help="Wariant tekstu – długi tekst najlepiej uwypukla różnicę kosztu haszowania",
    )
    detection.add_argument("--batch-size", type=int, default=5_000)
    detection.add_argument("--change-ratio", type=float, default=0.10)
    detection.add_argument(
        "--detection-methods", nargs="+",
        default=["hash_or_timestamp", "hash_only", "timestamp_only"],
    )
    detection.add_argument("--n-runs", type=int, default=5)
    detection.add_argument("--no-plots", action="store_true")

    # ------------------------------------------------------------------
    # generate-data – zapis wygenerowanych dokumentów na dysk
    # ------------------------------------------------------------------
    generate = subparsers.add_parser("generate-data", help="Zapisz dokumenty na dysk do inspekcji")
    generate.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    generate.add_argument("--sizes", type=int, nargs="+", default=[1_000])
    generate.add_argument(
        "--variants", choices=["short", "medium", "long", "xlarge"], nargs="+",
        default=["short", "long"],
    )

    return parser


def _batch_sizes(args) -> List[int]:
    if getattr(args, "batch_sizes", None):
        return args.batch_sizes
    if getattr(args, "batch_size", None):
        return [args.batch_size]
    return [5_000, 10_000]


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "benchmark":
        config = BenchmarkConfig(
            db_path=args.db,
            results_csv=args.csv,
            plots_dir=args.plots_dir,
            sizes=args.sizes,
            variants=args.variants,
            batch_sizes=_batch_sizes(args),
            new_ratio=args.new_ratio,
            change_ratio=args.change_ratio,
            high_change_ratio=args.high_change_ratio,
            delete_ratio=args.delete_ratio,
            n_runs=args.n_runs,
            create_plots=not args.no_plots,
        )
        rows = run_benchmark(config)
        print(f"Zakończono {len(rows)} uruchomień benchmarku")
        print(f"SQLite: {config.db_path}")
        print(f"CSV: {config.results_csv}")
        if config.create_plots:
            print(f"Wykresy: {config.plots_dir}")
        return 0

    if args.command == "threshold":
        config = ThresholdConfig(
            db_path=args.db,
            results_csv=args.csv,
            plots_dir=args.plots_dir,
            size=args.size,
            variants=args.variants,
            batch_size=args.batch_size,
            change_ratios=args.change_ratios,
            n_runs=args.n_runs,
            create_plots=not args.no_plots,
        )
        rows = run_threshold(config)
        print(f"Zakończono {len(rows)} pomiarów progu opłacalności")
        print(f"CSV: {config.results_csv}")
        if config.create_plots:
            print(f"Wykresy: {config.plots_dir}")
        return 0

    if args.command == "detection":
        config = DetectionConfig(
            db_path=args.db,
            results_csv=args.csv,
            plots_dir=args.plots_dir,
            size=args.size,
            variant=args.variant,
            batch_size=args.batch_size,
            change_ratio=args.change_ratio,
            detection_methods=args.detection_methods,
            n_runs=args.n_runs,
            create_plots=not args.no_plots,
        )
        rows = run_detection_comparison(config)
        print(f"Zakończono {len(rows)} pomiarów metod detekcji")
        print(f"CSV: {config.results_csv}")
        if config.create_plots:
            print(f"Wykresy: {config.plots_dir}")
        return 0

    if args.command == "generate-data":
        roots = write_documents_to_disk(args.output_dir, args.sizes, args.variants)
        print(f"Wygenerowano {len(roots)} zbiorów danych")
        for root in roots:
            print(root)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
