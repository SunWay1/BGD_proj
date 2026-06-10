from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .benchmark import BenchmarkConfig, run_benchmark
from .plotting import generate_backend_comparison_plots
from .postgres_benchmark import PostgresBenchmarkConfig, run_postgres_benchmark


COMMON_SCENARIOS = (
    "full_reload",
    "incremental_no_changes",
    "incremental_new_only",
    "incremental_new_and_changed",
    "incremental_high_change",
)


@dataclass(frozen=True)
class BackendComparisonConfig:
    sqlite_db_path: Path
    sqlite_results_csv: Path
    postgres_results_csv: Path
    comparison_csv: Path
    plots_dir: Path
    postgres_dsn: str
    sizes: List[int]
    variants: List[str]
    batch_sizes: List[int]
    new_ratio: float = 0.10
    change_ratio: float = 0.10
    high_change_ratio: float = 0.50
    n_runs: int = 1
    create_plots: bool = True


def _run_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_csv(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _progress(msg: str) -> None:
    import sys

    print(msg, flush=True)
    sys.stdout.flush()


def _comparison_key(row: Dict[str, object]) -> Tuple[object, ...]:
    return (
        row["scenario"],
        row["dataset_size"],
        row["text_variant"],
        row["batch_size"],
        row["change_ratio"],
        row["run_index"],
    )


def build_comparison_rows(
    sqlite_rows: Iterable[Dict[str, object]],
    postgres_rows: Iterable[Dict[str, object]],
) -> List[Dict[str, object]]:
    sqlite_index = {
        _comparison_key(row): row
        for row in sqlite_rows
        if row["scenario"] in COMMON_SCENARIOS
    }
    postgres_index = {
        _comparison_key(row): row
        for row in postgres_rows
        if row["scenario"] in COMMON_SCENARIOS
    }

    sqlite_keys = set(sqlite_index)
    postgres_keys = set(postgres_index)
    if sqlite_keys != postgres_keys:
        missing_in_postgres = sorted(sqlite_keys - postgres_keys, key=str)
        missing_in_sqlite = sorted(postgres_keys - sqlite_keys, key=str)
        raise RuntimeError(
            "Nie da się zbudować porównania backendów: "
            f"brakujące klucze postgres={missing_in_postgres[:3]}, "
            f"brakujące klucze sqlite={missing_in_sqlite[:3]}"
        )

    rows: List[Dict[str, object]] = []
    for key in sorted(
        sqlite_keys,
        key=lambda item: (
            str(item[2]),
            int(item[1]),
            int(item[3]),
            str(item[0]),
            int(item[5]),
        ),
    ):
        sqlite_row = sqlite_index[key]
        postgres_row = postgres_index[key]
        sqlite_total = float(sqlite_row["total_time_sec"])
        postgres_total = float(postgres_row["total_time_sec"])
        ratio = postgres_total / sqlite_total if sqlite_total > 0 else None
        if ratio is None:
            faster_backend = "n/a"
        elif ratio < 1.0:
            faster_backend = "postgres"
        elif ratio > 1.0:
            faster_backend = "sqlite"
        else:
            faster_backend = "tie"

        rows.append(
            {
                "run_at": _run_at(),
                "scenario": sqlite_row["scenario"],
                "dataset_size": sqlite_row["dataset_size"],
                "text_variant": sqlite_row["text_variant"],
                "batch_size": sqlite_row["batch_size"],
                "change_ratio": sqlite_row["change_ratio"],
                "run_index": sqlite_row["run_index"],
                "sqlite_total_time_sec": sqlite_total,
                "postgres_total_time_sec": postgres_total,
                "postgres_to_sqlite_time_ratio": ratio,
                "faster_backend": faster_backend,
                "sqlite_load_time_sec": sqlite_row["load_time_sec"],
                "postgres_load_time_sec": postgres_row["load_time_sec"],
                "sqlite_hashing_time_sec": sqlite_row["hashing_time_sec"],
                "postgres_hashing_time_sec": postgres_row["hashing_time_sec"],
                "sqlite_peak_memory_mb": sqlite_row["peak_memory_mb"],
                "postgres_peak_memory_mb": postgres_row["peak_memory_mb"],
                "sqlite_db_size_bytes": sqlite_row["db_size_bytes"],
                "postgres_db_size_bytes": postgres_row["db_size_bytes"],
                "sqlite_records_inserted": sqlite_row["records_inserted"],
                "postgres_records_inserted": postgres_row["records_inserted"],
                "sqlite_records_updated": sqlite_row["records_updated"],
                "postgres_records_updated": postgres_row["records_updated"],
                "sqlite_records_skipped": sqlite_row["records_skipped"],
                "postgres_records_skipped": postgres_row["records_skipped"],
                "sqlite_db_writes": sqlite_row["db_writes"],
                "postgres_db_writes": postgres_row["db_writes"],
            }
        )

    return rows


def run_backend_comparison(config: BackendComparisonConfig) -> List[Dict[str, object]]:
    _progress("[compare] Uruchamianie benchmarku SQLite...")
    sqlite_rows = run_benchmark(
        BenchmarkConfig(
            db_path=config.sqlite_db_path,
            results_csv=config.sqlite_results_csv,
            plots_dir=config.plots_dir,
            sizes=config.sizes,
            variants=config.variants,
            batch_sizes=config.batch_sizes,
            new_ratio=config.new_ratio,
            change_ratio=config.change_ratio,
            high_change_ratio=config.high_change_ratio,
            n_runs=config.n_runs,
            create_plots=False,
        )
    )

    _progress("[compare] Uruchamianie benchmarku PostgreSQL...")
    postgres_rows = run_postgres_benchmark(
        PostgresBenchmarkConfig(
            dsn=config.postgres_dsn,
            results_csv=config.postgres_results_csv,
            sizes=config.sizes,
            variants=config.variants,
            batch_sizes=config.batch_sizes,
            new_ratio=config.new_ratio,
            change_ratio=config.change_ratio,
            high_change_ratio=config.high_change_ratio,
            n_runs=config.n_runs,
        )
    )

    _progress("[compare] Budowanie bezpośredniego porównania SQLite vs PostgreSQL...")
    rows = build_comparison_rows(sqlite_rows, postgres_rows)
    _write_csv(config.comparison_csv, rows)
    _progress(f"[compare] Zapisano CSV porównawcze: {config.comparison_csv}")
    if config.create_plots:
        _progress("[compare] Generowanie wykresów porównawczych...")
        generate_backend_comparison_plots(rows, config.plots_dir)
        _progress(f"[compare] Wykresy zapisane w: {config.plots_dir}")
    return rows
