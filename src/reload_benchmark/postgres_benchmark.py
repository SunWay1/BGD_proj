from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import time
import tracemalloc
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .data_generator import RawDocument, iter_document_batches, iter_scenario_batches


SCHEMA_NAME = "reload_benchmark"
DOCUMENTS_TABLE = f"{SCHEMA_NAME}.documents"
DEFAULT_LOCAL_POSTGRES_DSN = "postgresql://postgres:postgres@localhost:5432/reload_benchmark"

INSERT_SQL = f"""
INSERT INTO {DOCUMENTS_TABLE}
(source_path, content, file_size, file_hash, created_at, updated_at, loaded_at)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

UPDATE_SQL = f"""
UPDATE {DOCUMENTS_TABLE}
SET content = %s, file_size = %s, file_hash = %s, updated_at = %s, loaded_at = %s
WHERE source_path = %s
"""

SCHEMA_SQL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA_NAME};

CREATE TABLE IF NOT EXISTS {DOCUMENTS_TABLE} (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    file_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    loaded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_hash ON {DOCUMENTS_TABLE}(file_hash);
CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON {DOCUMENTS_TABLE}(updated_at);
"""


@dataclass(frozen=True)
class PreparedDocument:
    source_path: str
    content: str
    file_size: int
    file_hash: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class LoadResult:
    mode: str
    records_total: int
    records_inserted: int
    records_updated: int
    records_skipped: int
    records_deleted: int
    db_writes: int
    hashing_time_sec: float
    load_time_sec: float
    total_time_sec: float
    peak_memory_mb: float


@dataclass(frozen=True)
class PostgresBenchmarkConfig:
    dsn: str
    results_csv: Path
    sizes: List[int]
    variants: List[str]
    batch_sizes: List[int]
    new_ratio: float = 0.10
    change_ratio: float = 0.10
    high_change_ratio: float = 0.50
    n_runs: int = 1


def _import_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Benchmark PostgreSQL wymaga pakietu 'psycopg'. "
            "Zainstaluj np. `pip install -e \".[postgres]\"`."
        ) from exc
    return psycopg


def _run_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now() -> str:
    return _run_at()


def _progress(msg: str) -> None:
    import sys

    print(msg, flush=True)
    sys.stdout.flush()


def _connect(dsn: str):
    psycopg = _import_psycopg()
    return psycopg.connect(dsn)


def init_db(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def reset_documents(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {DOCUMENTS_TABLE} RESTART IDENTITY")
    conn.commit()


def count_documents(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {DOCUMENTS_TABLE}")
        return int(cur.fetchone()[0])


def fetch_document_index(conn) -> Dict[str, Dict[str, str]]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT source_path, file_hash, updated_at FROM {DOCUMENTS_TABLE}")
        rows = cur.fetchall()
    return {
        str(source_path): {
            "file_hash": str(file_hash),
            "updated_at": str(updated_at),
        }
        for source_path, file_hash, updated_at in rows
    }


def table_size_bytes(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(pg_total_relation_size(%s::regclass), 0)", (DOCUMENTS_TABLE,))
        return int(cur.fetchone()[0])


def prepare_documents(
    documents: Iterable[RawDocument],
    *,
    compute_hash: bool = True,
) -> Tuple[List[PreparedDocument], float]:
    start = time.perf_counter()
    prepared: List[PreparedDocument] = []
    for doc in documents:
        encoded = doc.content.encode("utf-8")
        file_hash = hashlib.sha256(encoded).hexdigest() if compute_hash else ""
        prepared.append(
            PreparedDocument(
                source_path=doc.source_path,
                content=doc.content,
                file_size=len(encoded),
                file_hash=file_hash,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
            )
        )
    return prepared, time.perf_counter() - start


def _insert_rows(conn, rows: Sequence[PreparedDocument], loaded_at: str) -> None:
    if not rows:
        return
    payload = [
        (
            row.source_path,
            row.content,
            row.file_size,
            row.file_hash,
            row.created_at,
            row.updated_at,
            loaded_at,
        )
        for row in rows
    ]
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, payload)


def _update_rows(conn, rows: Sequence[PreparedDocument], loaded_at: str) -> None:
    if not rows:
        return
    payload = [
        (
            row.content,
            row.file_size,
            row.file_hash,
            row.updated_at,
            loaded_at,
            row.source_path,
        )
        for row in rows
    ]
    with conn.cursor() as cur:
        cur.executemany(UPDATE_SQL, payload)


def _is_changed(row: PreparedDocument, current: Dict[str, str], method: str) -> bool:
    if method == "hash_only":
        return row.file_hash != current["file_hash"]
    if method == "timestamp_only":
        return row.updated_at > current["updated_at"]
    return row.file_hash != current["file_hash"] or row.updated_at > current["updated_at"]


def _start_memory() -> None:
    if tracemalloc.is_tracing():
        tracemalloc.clear_traces()
    else:
        tracemalloc.start()


def _stop_memory() -> float:
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak_bytes / 1024 / 1024


def full_reload_batches(conn, document_batches: Iterable[Iterable[RawDocument]]) -> LoadResult:
    total_start = time.perf_counter()
    _start_memory()
    records_total = 0
    hashing_time = 0.0
    load_time = 0.0
    first_batch = True

    for documents in document_batches:
        prepared, batch_hashing_time = prepare_documents(documents)
        hashing_time += batch_hashing_time
        if first_batch:
            load_start = time.perf_counter()
            reset_documents(conn)
            load_time += time.perf_counter() - load_start
            first_batch = False
        if not prepared:
            continue
        load_start = time.perf_counter()
        _insert_rows(conn, prepared, utc_now())
        conn.commit()
        load_time += time.perf_counter() - load_start
        records_total += len(prepared)

    if first_batch:
        load_start = time.perf_counter()
        reset_documents(conn)
        load_time += time.perf_counter() - load_start

    peak_mb = _stop_memory()
    return LoadResult(
        mode="full_reload",
        records_total=records_total,
        records_inserted=records_total,
        records_updated=0,
        records_skipped=0,
        records_deleted=0,
        db_writes=records_total,
        hashing_time_sec=hashing_time,
        load_time_sec=load_time,
        total_time_sec=time.perf_counter() - total_start,
        peak_memory_mb=peak_mb,
    )


def incremental_load_batches(
    conn,
    document_batches: Iterable[Iterable[RawDocument]],
    *,
    detection_method: str = "hash_or_timestamp",
) -> LoadResult:
    total_start = time.perf_counter()
    _start_memory()
    compute_hash = detection_method != "timestamp_only"
    existing = fetch_document_index(conn)
    records_total = 0
    inserted = 0
    updated = 0
    skipped = 0
    hashing_time = 0.0
    load_time = 0.0

    for documents in document_batches:
        prepared, batch_hashing_time = prepare_documents(documents, compute_hash=compute_hash)
        hashing_time += batch_hashing_time
        records_total += len(prepared)
        to_insert: List[PreparedDocument] = []
        to_update: List[PreparedDocument] = []

        for row in prepared:
            current = existing.get(row.source_path)
            if current is None:
                to_insert.append(row)
            elif _is_changed(row, current, detection_method):
                to_update.append(row)
            else:
                skipped += 1

        loaded_at = utc_now()
        load_start = time.perf_counter()
        _insert_rows(conn, to_insert, loaded_at)
        _update_rows(conn, to_update, loaded_at)
        conn.commit()
        load_time += time.perf_counter() - load_start

        for row in to_insert:
            existing[row.source_path] = {"file_hash": row.file_hash, "updated_at": row.updated_at}
        for row in to_update:
            existing[row.source_path] = {"file_hash": row.file_hash, "updated_at": row.updated_at}
        inserted += len(to_insert)
        updated += len(to_update)

    peak_mb = _stop_memory()
    writes = inserted + updated
    return LoadResult(
        mode="incremental_load",
        records_total=records_total,
        records_inserted=inserted,
        records_updated=updated,
        records_skipped=skipped,
        records_deleted=0,
        db_writes=writes,
        hashing_time_sec=hashing_time,
        load_time_sec=load_time,
        total_time_sec=time.perf_counter() - total_start,
        peak_memory_mb=peak_mb,
    )


def _write_csv(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _record_run(
    *,
    conn,
    result: LoadResult,
    scenario: str,
    dataset_size: int,
    text_variant: str,
    batch_size: int,
    change_ratio: Optional[float],
    run_index: int,
    full_reload_time: Optional[float],
) -> Dict[str, object]:
    ratio = None
    speedup = None
    if full_reload_time and result.mode != "full_reload":
        ratio = result.total_time_sec / full_reload_time
        speedup = full_reload_time / result.total_time_sec if result.total_time_sec > 0 else None

    return {
        "run_at": _run_at(),
        "backend": "postgres",
        "mode": result.mode,
        "scenario": scenario,
        "dataset_size": dataset_size,
        "text_variant": text_variant,
        "batch_size": batch_size,
        "change_ratio": change_ratio,
        "change_distribution": "skupione",
        "delete_ratio": 0.0,
        "detection_method": "hash_or_timestamp",
        "run_index": run_index,
        "records_total": result.records_total,
        "records_inserted": result.records_inserted,
        "records_updated": result.records_updated,
        "records_skipped": result.records_skipped,
        "records_deleted": result.records_deleted,
        "db_writes": result.db_writes,
        "hashing_time_sec": result.hashing_time_sec,
        "load_time_sec": result.load_time_sec,
        "total_time_sec": result.total_time_sec,
        "peak_memory_mb": result.peak_memory_mb,
        "db_size_bytes": table_size_bytes(conn),
        "incremental_to_full_ratio": ratio,
        "speedup_vs_full": speedup,
    }


def resolve_dsn(dsn: Optional[str]) -> str:
    return dsn or os.environ.get("RELOAD_BENCHMARK_POSTGRES_DSN") or DEFAULT_LOCAL_POSTGRES_DSN


def run_postgres_benchmark(config: PostgresBenchmarkConfig) -> List[Dict[str, object]]:
    """Uruchamia minimalny podzbiór benchmarku na PostgreSQL.

    Obsługiwane scenariusze:
      - full_reload
      - incremental_no_changes
      - incremental_new_only
      - incremental_new_and_changed
      - incremental_high_change

    Celowo pomija scenariusze append-only, deletes i storage,
    żeby nie naruszać istniejącej ścieżki SQLite.
    """
    total_combos = len(config.variants) * len(config.sizes) * len(config.batch_sizes) * config.n_runs
    combo_idx = 0
    _progress(
        f"[postgres] Start: {total_combos} kombinacji "
        f"(warianty={config.variants}, rozmiary={config.sizes})"
    )
    rows: List[Dict[str, object]] = []
    conn = _connect(config.dsn)
    init_db(conn)

    try:
        for variant in config.variants:
            for size in config.sizes:
                for batch_size in config.batch_sizes:
                    base_batches = lambda: iter_document_batches(size, variant, batch_size)

                    for run_idx in range(config.n_runs):
                        combo_idx += 1
                        _progress(
                            f"  [{combo_idx}/{total_combos}] wariant={variant}, "
                            f"rozmiar={size:,}, partia={batch_size:,}, "
                            f"uruchomienie={run_idx + 1}/{config.n_runs}"
                        )
                        full_result = full_reload_batches(conn, base_batches())
                        if count_documents(conn) != size:
                            raise RuntimeError(
                                "PostgreSQL full_reload zwrócił nieoczekiwaną liczbę dokumentów"
                            )
                        full_time = full_result.total_time_sec
                        rows.append(
                            _record_run(
                                conn=conn,
                                result=full_result,
                                scenario="full_reload",
                                dataset_size=size,
                                text_variant=variant,
                                batch_size=batch_size,
                                change_ratio=None,
                                run_index=run_idx,
                                full_reload_time=None,
                            )
                        )

                        new_count = max(1, int(size * config.new_ratio))
                        incremental_scenarios = [
                            ("incremental_no_changes", 0.0, 0),
                            ("incremental_new_only", 0.0, new_count),
                            ("incremental_new_and_changed", config.change_ratio, new_count),
                            ("incremental_high_change", config.high_change_ratio, 0),
                        ]

                        for scenario, change_ratio, scenario_new_count in incremental_scenarios:
                            full_reload_batches(conn, base_batches())
                            result = incremental_load_batches(
                                conn,
                                iter_scenario_batches(
                                    size,
                                    variant,
                                    batch_size,
                                    change_ratio=change_ratio,
                                    new_count=scenario_new_count,
                                ),
                            )
                            rows.append(
                                _record_run(
                                    conn=conn,
                                    result=result,
                                    scenario=scenario,
                                    dataset_size=size,
                                    text_variant=variant,
                                    batch_size=batch_size,
                                    change_ratio=change_ratio,
                                    run_index=run_idx,
                                    full_reload_time=full_time,
                                )
                            )

        _write_csv(config.results_csv, rows)
        _progress(f"[postgres] Zapisano CSV: {config.results_csv}")
        return rows
    finally:
        conn.close()
