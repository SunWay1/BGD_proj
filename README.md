# Full Reload vs Incremental Load -- Benchmark

Benchmarks two data loading strategies for SQLite and PostgreSQL:

- **Full Reload** -- truncate the table, re-insert everything from scratch
- **Incremental Load** -- compare incoming records against what is already in the database, insert/update/skip as needed

Measures total time, DB write count, and peak RAM across dataset sizes, text variants, batch sizes, and change distributions. Also compares SQLite vs PostgreSQL side by side.

---

## Requirements

- Python 3.11+
- Docker (optional, for PostgreSQL benchmarks)

---

## Installation

```bash
pip install -e .
```

This installs all required dependencies including `matplotlib` and `psycopg[binary]`.

For local PostgreSQL via Docker:

```bash
docker compose up -d postgres
```

Default connection: `postgresql://postgres:postgres@localhost:5432/reload_benchmark`

A custom DSN can be passed via `--dsn` flag or `RELOAD_BENCHMARK_POSTGRES_DSN` env variable.

---

## Running

### Full analysis (recommended)

```bash
# Quick sanity check (~2 min)
python run_analysis.py --quick

# Full benchmark (~10-20 min)
python run_analysis.py
```

Runs all four stages in sequence and writes results to `results/`.

### Individual stages via CLI

```bash
# SQLite benchmark: 7 scenarios x sizes x variants x batch sizes
python -m reload_benchmark.cli benchmark --sizes 10000 50000 100000 --variants short long --n-runs 3

# Threshold analysis: find break-even change ratio
python -m reload_benchmark.cli threshold --size 50000 --variants short medium long xlarge --n-runs 3

# Change detection method comparison
python -m reload_benchmark.cli detection --size 50000 --variant long --n-runs 5

# PostgreSQL benchmark (requires running Postgres)
python -m reload_benchmark.cli benchmark-postgres --sizes 10000 50000 100000 --variants short long

# Direct SQLite vs PostgreSQL comparison
python -m reload_benchmark.cli benchmark-compare --sizes 10000 50000 100000 --variants short long --n-runs 3

# Reuse existing CSV results instead of re-running
python -m reload_benchmark.cli benchmark-compare --skip-sqlite --skip-postgres

# In-memory vs file-backed SQLite comparison
python run_storage.py
```

### Regenerate plots from existing CSV files

```bash
python generate_plots.py
```

Reads all CSV files in `results/` and regenerates all PNG plots without re-running benchmarks.

---

## Analysis stages

### 1. Main benchmark

Seven load scenarios run for each combination of dataset size, text variant, and batch size:

| Scenario | Description |
| --- | --- |
| `full_reload` | DELETE all, INSERT all -- baseline |
| `incremental_no_changes` | 0% changes -- does incremental skip all writes? |
| `incremental_new_only` | 10% new records appended |
| `incremental_new_and_changed` | 10% new + 10% modified |
| `incremental_high_change` | 50% records modified |
| `incremental_append_only` | Inserts new records only, skips hash comparison |
| `incremental_with_deletes` | Detects and removes records absent from source |

Results: `results/benchmark_sqlite_results.csv`

### 2. Threshold analysis

Sweeps `change_ratio` from 0% to 100% in 16 steps to find the crossover point where incremental load stops being faster than full reload. Runs separately for each text variant to show how document size shifts the threshold.

Results: `results/threshold_results.csv`

### 3. Change detection comparison

Compares three strategies for detecting whether a record has changed:

- `hash_or_timestamp` -- changed if SHA-256 hash OR timestamp differs
- `hash_only` -- compare content hashes only
- `timestamp_only` -- compare timestamps only, skip hashing entirely

Results: `results/detection_results.csv`

### 4. In-memory vs file-backed SQLite

Runs full reload and incremental-with-deletes on both an in-memory SQLite database and a file-backed one (WAL mode, `synchronous=NORMAL`). Measures the filesystem overhead.

Results: `results/storage_results.csv`

### 5. SQLite vs PostgreSQL comparison

Runs the five common scenarios on both backends with identical parameters and produces a joined comparison CSV with columns like `sqlite_total_time_sec`, `postgres_total_time_sec`, `postgres_to_sqlite_time_ratio`.

Results: `results/benchmark_backend_comparison.csv`, plots: `results/plots/backend_total_time_*.png`

---

## CLI flags

### `benchmark`

| Flag | Default | Description |
| --- | --- | --- |
| `--sizes` | `10000 50000 100000` | Dataset sizes (number of records) |
| `--variants` | `short long` | Text variants: `short` (~135 B), `medium` (~1300 B), `long` (~5000 B), `xlarge` (~10000 B) |
| `--batch-sizes` | `5000 10000` | Batch sizes to compare (multiple allowed) |
| `--batch-size` | -- | Single batch size (alternative to `--batch-sizes`) |
| `--n-runs` | `3` | Repetitions per scenario |
| `--new-ratio` | `0.10` | Fraction of new records |
| `--change-ratio` | `0.10` | Fraction of modified records |
| `--high-change-ratio` | `0.50` | Fraction modified in high-change scenario |
| `--delete-ratio` | `0.05` | Fraction deleted from source (with-deletes scenario) |
| `--db` | `results/benchmark.sqlite` | SQLite results database path |
| `--csv` | `results/benchmark_results.csv` | CSV output path |
| `--plots-dir` | `results/plots` | Plot output directory |
| `--no-plots` | -- | Skip plot generation |

### `threshold`

| Flag | Default | Description |
| --- | --- | --- |
| `--size` | `50000` | Dataset size |
| `--variants` | `short medium long xlarge` | Text variants to test |
| `--batch-size` | `5000` | Batch size |
| `--change-ratios` | 16 points 0%..100% | Change ratio values to sweep |
| `--n-runs` | `3` | Repetitions per measurement point |

### `detection`

| Flag | Default | Description |
| --- | --- | --- |
| `--size` | `50000` | Dataset size |
| `--variant` | `long` | Text variant |
| `--batch-size` | `5000` | Batch size |
| `--change-ratio` | `0.10` | Fraction of modified records |
| `--n-runs` | `5` | Repetitions per method |

### `benchmark-compare`

| Flag | Default | Description |
| --- | --- | --- |
| `--dsn` | env or local default | PostgreSQL connection string |
| `--sqlite-csv` | `results/benchmark_sqlite_results.csv` | SQLite results CSV |
| `--postgres-csv` | `results/benchmark_postgres_results.csv` | PostgreSQL results CSV |
| `--compare-csv` | `results/benchmark_backend_comparison.csv` | Joined comparison CSV |
| `--skip-sqlite` | -- | Skip SQLite benchmark, load from `--sqlite-csv` |
| `--skip-postgres` | -- | Skip PostgreSQL benchmark, load from `--postgres-csv` |
| `--no-plots` | -- | Skip plot generation |

All size/variant/batch/n-runs flags are the same as `benchmark`.

---

## Tests

```bash
python -m pytest tests/

# Single test
python -m pytest tests/test_loaders.py::LoaderTests::test_full_reload_matches_source_count

# Without pytest
python -m unittest tests/test_loaders.py
```

Tests use in-memory SQLite via `tempfile.TemporaryDirectory` -- no external dependencies required.

---

## Project structure

```text
.
+-- docker-compose.yml          # local PostgreSQL for benchmarks
+-- run_analysis.py             # runs all four analysis stages
+-- run_storage.py              # standalone in-memory vs file benchmark
+-- generate_plots.py           # regenerate plots from existing CSV files
+-- src/
|   +-- reload_benchmark/
|       +-- data_generator.py   # synthetic document generation (in-memory)
|       +-- loaders.py          # all loading strategies, returns LoadResult
|       +-- database.py         # SQLite connection factory, schema, fetch helpers
|       +-- benchmark.py        # orchestration, config dataclasses, run_* functions
|       +-- backend_comparison.py # SQLite vs PostgreSQL side-by-side runner
|       +-- postgres_benchmark.py # PostgreSQL benchmark using psycopg v3
|       +-- plotting.py         # all matplotlib charts
|       +-- cli.py              # argparse CLI entry point
+-- tests/
|   +-- test_loaders.py
+-- results/
    +-- benchmark_sqlite_results.csv
    +-- benchmark_postgres_results.csv
    +-- benchmark_backend_comparison.csv
    +-- threshold_results.csv
    +-- detection_results.csv
    +-- storage_results.csv
    +-- plots/                  # 38 PNG charts
```

## Output files

| File | Generated by | Contents |
| --- | --- | --- |
| `benchmark_sqlite_results.csv` | `benchmark-compare` or `benchmark` | Per-run metrics for all SQLite scenarios |
| `benchmark_postgres_results.csv` | `benchmark-compare` or `benchmark-postgres` | Per-run metrics for PostgreSQL scenarios |
| `benchmark_backend_comparison.csv` | `benchmark-compare` | Joined SQLite vs PostgreSQL with ratios |
| `threshold_results.csv` | `threshold` | Full/incremental time at each change ratio |
| `detection_results.csv` | `detection` | Per-method timing for change detection |
| `storage_results.csv` | `run_storage.py` | In-memory vs file-backed SQLite timing |
| `results/plots/*.png` | Any run with plots enabled | 38 charts total |
