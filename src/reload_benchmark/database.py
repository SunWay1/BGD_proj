from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Dict, Iterable, Mapping, Set


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    file_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    loaded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON documents(updated_at);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    backend TEXT NOT NULL DEFAULT 'sqlite',
    mode TEXT NOT NULL,
    scenario TEXT NOT NULL,
    dataset_size INTEGER NOT NULL,
    text_variant TEXT NOT NULL,
    batch_size INTEGER NOT NULL,
    change_ratio REAL,
    change_distribution TEXT NOT NULL DEFAULT 'skupione',
    delete_ratio REAL NOT NULL DEFAULT 0,
    detection_method TEXT NOT NULL DEFAULT 'hash_or_timestamp',
    run_index INTEGER NOT NULL DEFAULT 0,
    records_total INTEGER NOT NULL,
    records_inserted INTEGER NOT NULL,
    records_updated INTEGER NOT NULL,
    records_skipped INTEGER NOT NULL,
    records_deleted INTEGER NOT NULL DEFAULT 0,
    db_writes INTEGER NOT NULL,
    hashing_time_sec REAL NOT NULL,
    load_time_sec REAL NOT NULL,
    total_time_sec REAL NOT NULL,
    peak_memory_mb REAL NOT NULL DEFAULT 0,
    db_size_bytes INTEGER NOT NULL,
    incremental_to_full_ratio REAL,
    speedup_vs_full REAL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(benchmark_runs)").fetchall()
    }
    if "backend" not in columns:
        conn.execute(
            "ALTER TABLE benchmark_runs ADD COLUMN backend TEXT NOT NULL DEFAULT 'sqlite'"
        )
    conn.commit()


def reset_documents(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM documents")


def count_documents(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
    return int(row["count"])


def fetch_document_index(conn: sqlite3.Connection) -> Dict[str, sqlite3.Row]:
    """Zwraca słownik source_path → {file_hash, updated_at} dla wszystkich rekordów w bazie."""
    rows = conn.execute("SELECT source_path, file_hash, updated_at FROM documents").fetchall()
    return {str(row["source_path"]): row for row in rows}


def fetch_document_paths(conn: sqlite3.Connection) -> Set[str]:
    """Zwraca zbiór wszystkich source_path z bazy – szybsze niż fetch_document_index gdy hash jest niepotrzebny."""
    rows = conn.execute("SELECT source_path FROM documents").fetchall()
    return {str(row["source_path"]) for row in rows}


def delete_documents_by_paths(conn: sqlite3.Connection, paths: Iterable[str]) -> int:
    """Usuwa dokumenty po liście source_path. Zwraca liczbę usuniętych rekordów.
    Operuje w partiach po 999 ze względu na limit zmiennych SQLite.
    """
    paths_list = list(paths)
    if not paths_list:
        return 0
    deleted = 0
    # SQLite domyślnie pozwala na max 999 zmiennych w zapytaniu
    chunk_size = 999
    for i in range(0, len(paths_list), chunk_size):
        chunk = paths_list[i : i + chunk_size]
        placeholders = ", ".join("?" for _ in chunk)
        cursor = conn.execute(
            f"DELETE FROM documents WHERE source_path IN ({placeholders})", chunk
        )
        deleted += cursor.rowcount
    return deleted


def insert_benchmark_run(conn: sqlite3.Connection, run: Mapping[str, object]) -> None:
    columns = list(run.keys())
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO benchmark_runs ({', '.join(columns)}) VALUES ({placeholders})"
    conn.execute(sql, [run[column] for column in columns])
    conn.commit()


def db_size_bytes(db_path: Path) -> int:
    total = 0
    for path in [db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")]:
        if path.exists():
            total += path.stat().st_size
    return total


def load_runs(conn: sqlite3.Connection) -> Iterable[sqlite3.Row]:
    return conn.execute("SELECT * FROM benchmark_runs ORDER BY id").fetchall()
