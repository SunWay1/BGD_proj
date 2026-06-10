from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reload_benchmark.backend_comparison import build_comparison_rows
from reload_benchmark.benchmark import BenchmarkConfig, ThresholdConfig, run_benchmark, run_threshold
from reload_benchmark.cli import build_parser
from reload_benchmark.data_generator import (
    generate_documents,
    iter_document_batches,
    with_changed_documents,
    with_new_documents,
    write_documents_to_disk,
)
from reload_benchmark.database import connect, count_documents, init_db
from reload_benchmark.loaders import full_reload, full_reload_batches, incremental_load, incremental_load_batches
from reload_benchmark.postgres_benchmark import DEFAULT_LOCAL_POSTGRES_DSN, resolve_dsn


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "test.sqlite"
        self.conn = connect(self.db_path)
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tempdir.cleanup()

    def test_iter_document_batches_returns_expected_count(self) -> None:
        batches = list(iter_document_batches(25, "short", 10))
        self.assertEqual([len(batch) for batch in batches], [10, 10, 5])
        self.assertEqual(sum(len(batch) for batch in batches), 25)

    def test_full_reload_matches_source_count(self) -> None:
        docs = generate_documents(25, "short")
        result = full_reload(self.conn, docs)
        self.assertEqual(result.records_inserted, 25)
        self.assertEqual(count_documents(self.conn), 25)

        smaller = generate_documents(10, "short")
        full_reload(self.conn, smaller)
        self.assertEqual(count_documents(self.conn), 10)

    def test_full_reload_batches_matches_source_count(self) -> None:
        result = full_reload_batches(self.conn, iter_document_batches(25, "short", 8))
        self.assertEqual(result.records_inserted, 25)
        self.assertEqual(count_documents(self.conn), 25)

    def test_incremental_adds_new_records(self) -> None:
        base = generate_documents(20, "short")
        full_reload(self.conn, base)
        incoming = with_new_documents(base, 5, "short")
        result = incremental_load(self.conn, incoming)
        self.assertEqual(result.records_inserted, 5)
        self.assertEqual(result.records_updated, 0)
        self.assertEqual(result.records_skipped, 20)
        self.assertEqual(count_documents(self.conn), 25)

    def test_incremental_updates_changed_records(self) -> None:
        base = generate_documents(20, "short")
        full_reload(self.conn, base)
        incoming = with_changed_documents(base, 0.25, "short")
        result = incremental_load(self.conn, incoming)
        self.assertEqual(result.records_inserted, 0)
        self.assertEqual(result.records_updated, 5)
        self.assertEqual(result.records_skipped, 15)
        self.assertEqual(count_documents(self.conn), 20)

    def test_incremental_skips_unchanged_records(self) -> None:
        base = generate_documents(20, "short")
        full_reload(self.conn, base)
        result = incremental_load(self.conn, base)
        self.assertEqual(result.records_inserted, 0)
        self.assertEqual(result.records_updated, 0)
        self.assertEqual(result.records_skipped, 20)
        self.assertEqual(count_documents(self.conn), 20)

    def test_incremental_batches_keep_same_final_count(self) -> None:
        full_reload_batches(self.conn, iter_document_batches(20, "long", 7))
        result = incremental_load_batches(self.conn, iter_document_batches(25, "long", 6))
        self.assertEqual(result.records_inserted, 5)
        self.assertEqual(result.records_updated, 0)
        self.assertEqual(result.records_skipped, 20)
        self.assertEqual(count_documents(self.conn), 25)

    def test_incremental_does_not_create_duplicates(self) -> None:
        base = generate_documents(20, "short")
        full_reload(self.conn, base)
        incremental_load(self.conn, with_new_documents(base, 5, "short"))
        incremental_load(self.conn, with_new_documents(base, 5, "short"))
        self.assertEqual(count_documents(self.conn), 25)
        duplicate_count = self.conn.execute(
            "SELECT COUNT(*) FROM (SELECT source_path FROM documents GROUP BY source_path HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        self.assertEqual(duplicate_count, 0)

    def test_write_documents_to_disk_creates_files_and_manifest(self) -> None:
        roots = write_documents_to_disk(self.root / "data" / "generated", [7], ["short"])
        self.assertEqual(len(roots), 1)
        files = list(roots[0].glob("document_*.txt"))
        self.assertEqual(len(files), 7)
        manifest = roots[0] / "manifest.csv"
        self.assertTrue(manifest.exists())
        with manifest.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 7)

    def test_benchmark_csv_contains_batch_size(self) -> None:
        csv_path = self.root / "results" / "benchmark.csv"
        config = BenchmarkConfig(
            db_path=self.root / "results" / "benchmark.sqlite",
            results_csv=csv_path,
            plots_dir=self.root / "results" / "plots",
            sizes=[10],
            variants=["short"],
            batch_sizes=[4],
            create_plots=False,
        )
        rows = run_benchmark(config)
        self.assertTrue(csv_path.exists())
        self.assertIn("batch_size", rows[0])
        self.assertIn("backend", rows[0])
        self.assertEqual(rows[0]["batch_size"], 4)
        self.assertEqual(rows[0]["backend"], "sqlite")

    def test_threshold_csv_contains_strategy(self) -> None:
        csv_path = self.root / "results" / "threshold.csv"
        config = ThresholdConfig(
            db_path=self.root / "results" / "threshold.sqlite",
            results_csv=csv_path,
            plots_dir=self.root / "results" / "plots",
            size=10,
            variants=["short"],
            batch_size=4,
            change_ratios=[0.0, 0.5],
            create_plots=False,
        )
        rows = run_threshold(config)
        self.assertTrue(csv_path.exists())
        self.assertEqual(len(rows), 2)
        self.assertIn("change_ratio", rows[0])
        self.assertIn("incremental_to_full_ratio", rows[0])
        self.assertIn(rows[0]["recommended_strategy"], {"incremental_load", "full_reload"})

    def test_cli_parses_postgres_benchmark_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "benchmark-postgres",
                "--dsn",
                "postgresql://postgres:postgres@localhost:5432/reload_benchmark",
                "--sizes",
                "10",
                "--variants",
                "short",
                "--batch-size",
                "4",
                "--n-runs",
                "1",
            ]
        )
        self.assertEqual(args.command, "benchmark-postgres")
        self.assertEqual(args.batch_size, 4)
        self.assertEqual(args.variants, ["short"])

    def test_cli_parses_backend_compare_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "benchmark-compare",
                "--dsn",
                "postgresql://postgres:postgres@localhost:5432/reload_benchmark",
                "--sizes",
                "10",
                "--variants",
                "short",
                "--batch-size",
                "4",
                "--n-runs",
                "1",
            ]
        )
        self.assertEqual(args.command, "benchmark-compare")
        self.assertEqual(args.batch_size, 4)
        self.assertEqual(args.variants, ["short"])

    def test_build_comparison_rows_creates_explicit_sqlite_vs_postgres_view(self) -> None:
        sqlite_rows = [
            {
                "scenario": "full_reload",
                "dataset_size": 10,
                "text_variant": "short",
                "batch_size": 4,
                "change_ratio": None,
                "run_index": 0,
                "total_time_sec": 2.0,
                "load_time_sec": 1.5,
                "hashing_time_sec": 0.5,
                "peak_memory_mb": 10.0,
                "db_size_bytes": 1000,
                "records_inserted": 10,
                "records_updated": 0,
                "records_skipped": 0,
                "db_writes": 10,
            }
        ]
        postgres_rows = [
            {
                "scenario": "full_reload",
                "dataset_size": 10,
                "text_variant": "short",
                "batch_size": 4,
                "change_ratio": None,
                "run_index": 0,
                "total_time_sec": 1.0,
                "load_time_sec": 0.8,
                "hashing_time_sec": 0.2,
                "peak_memory_mb": 12.0,
                "db_size_bytes": 2000,
                "records_inserted": 10,
                "records_updated": 0,
                "records_skipped": 0,
                "db_writes": 10,
            }
        ]
        rows = build_comparison_rows(sqlite_rows, postgres_rows)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["faster_backend"], "postgres")
        self.assertEqual(rows[0]["sqlite_total_time_sec"], 2.0)
        self.assertEqual(rows[0]["postgres_total_time_sec"], 1.0)
        self.assertEqual(rows[0]["postgres_to_sqlite_time_ratio"], 0.5)

    def test_resolve_dsn_returns_local_fallback_when_env_missing(self) -> None:
        previous = os.environ.pop("RELOAD_BENCHMARK_POSTGRES_DSN", None)
        try:
            self.assertEqual(resolve_dsn(None), DEFAULT_LOCAL_POSTGRES_DSN)
        finally:
            if previous is not None:
                os.environ["RELOAD_BENCHMARK_POSTGRES_DSN"] = previous

    def test_resolve_dsn_prefers_explicit_argument_over_env(self) -> None:
        previous = os.environ.get("RELOAD_BENCHMARK_POSTGRES_DSN")
        os.environ["RELOAD_BENCHMARK_POSTGRES_DSN"] = "postgresql://env-user:env-pass@env-host:5432/env-db"
        try:
            resolved = resolve_dsn("postgresql://arg-user:arg-pass@arg-host:5432/arg-db")
            self.assertEqual(resolved, "postgresql://arg-user:arg-pass@arg-host:5432/arg-db")
        finally:
            if previous is None:
                os.environ.pop("RELOAD_BENCHMARK_POSTGRES_DSN", None)
            else:
                os.environ["RELOAD_BENCHMARK_POSTGRES_DSN"] = previous

if __name__ == "__main__":
    unittest.main()
