from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import sqlite3
import time
import tracemalloc
from typing import Iterable, List, Set, Sequence, Tuple
from pathlib import Path

from .data_generator import RawDocument
from .database import connect, delete_documents_by_paths, fetch_document_index, fetch_document_paths,init_db, reset_documents


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


INSERT_SQL = """
INSERT INTO documents
(source_path, content, file_size, file_hash, created_at, updated_at, loaded_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

UPDATE_SQL = """
UPDATE documents
SET content = ?, file_size = ?, file_hash = ?, updated_at = ?, loaded_at = ?
WHERE source_path = ?
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prepare_documents(
    documents: Iterable[RawDocument],
    *,
    compute_hash: bool = True,
) -> Tuple[List[PreparedDocument], float]:
    """Przygotowuje dokumenty do załadowania: koduje UTF-8, opcjonalnie liczy SHA-256."""
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


def _insert_rows(conn: sqlite3.Connection, rows: Sequence[PreparedDocument], loaded_at: str) -> None:
    conn.executemany(
        INSERT_SQL,
        [
            (row.source_path, row.content, row.file_size, row.file_hash, row.created_at, row.updated_at, loaded_at)
            for row in rows
        ],
    )


def _is_changed(row: PreparedDocument, current: dict, method: str) -> bool:
    """Sprawdza czy dokument zmienił się względem stanu w bazie."""
    if method == "hash_only":
        return row.file_hash != current["file_hash"]
    if method == "timestamp_only":
        return row.updated_at > current["updated_at"]
    # Domyślnie: hash_or_timestamp – wystarczy jeden warunek
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


# ---------------------------------------------------------------------------
# Pełne przeładowanie
# ---------------------------------------------------------------------------

def full_reload(conn: sqlite3.Connection, documents: Iterable[RawDocument]) -> LoadResult:
    return full_reload_batches(conn, [list(documents)])


def full_reload_batches(conn: sqlite3.Connection, document_batches: Iterable[Iterable[RawDocument]]) -> LoadResult:
    """Czyści tabelę i ładuje wszystkie dokumenty od zera."""
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
            with conn:
                reset_documents(conn)
            load_time += time.perf_counter() - load_start
            first_batch = False
        if not prepared:
            continue
        load_start = time.perf_counter()
        with conn:
            _insert_rows(conn, prepared, utc_now())
        load_time += time.perf_counter() - load_start
        records_total += len(prepared)

    if first_batch:
        load_start = time.perf_counter()
        with conn:
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


# ---------------------------------------------------------------------------
# Ładowanie inkrementalne (standardowe)
# ---------------------------------------------------------------------------

def incremental_load(conn: sqlite3.Connection, documents: Iterable[RawDocument]) -> LoadResult:
    return incremental_load_batches(conn, [list(documents)])


def incremental_load_batches(
    conn: sqlite3.Connection,
    document_batches: Iterable[Iterable[RawDocument]],
    *,
    detection_method: str = "hash_or_timestamp",
) -> LoadResult:
    """Wstawia nowe, aktualizuje zmienione, pomija niezmienione rekordy.

    detection_method:
      "hash_or_timestamp" – zmiana hasha lub nowszy timestamp (domyślne)
      "hash_only"         – tylko porównanie SHA-256
      "timestamp_only"    – tylko porównanie updated_at (nie liczy SHA-256)
    """
    total_start = time.perf_counter()
    _start_memory()
    # Przy detekcji tylko po timestamp nie liczymy SHA-256 – oszczędzamy CPU
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
        with conn:
            _insert_rows(conn, to_insert, loaded_at)
            conn.executemany(
                UPDATE_SQL,
                [
                    (row.content, row.file_size, row.file_hash, row.updated_at, loaded_at, row.source_path)
                    for row in to_update
                ],
            )
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


# ---------------------------------------------------------------------------
# Ładowanie inkrementalne – tylko dopisywanie (append-only)
# ---------------------------------------------------------------------------

def incremental_append_only_batches(
    conn: sqlite3.Connection,
    document_batches: Iterable[Iterable[RawDocument]],
) -> LoadResult:
    """Wstawia wyłącznie nowe rekordy – zakłada brak modyfikacji istniejących.

    Nie liczy SHA-256 dla już istniejących dokumentów – najszybsza strategia
    inkrementalna dla potoków tylko-dopisujących (logi, zdarzenia, transakcje).
    """
    total_start = time.perf_counter()
    _start_memory()
    # Pobieramy tylko ścieżki – nie potrzebujemy hashy
    existing_paths: Set[str] = fetch_document_paths(conn)
    records_total = 0
    inserted = 0
    skipped = 0
    hashing_time = 0.0
    load_time = 0.0

    for documents in document_batches:
        doc_list = list(documents)
        records_total += len(doc_list)

        # Odfiltruj istniejące – nie hashujemy ich w ogóle
        new_docs = [doc for doc in doc_list if doc.source_path not in existing_paths]
        skipped += len(doc_list) - len(new_docs)

        if new_docs:
            prepared, batch_hashing_time = prepare_documents(new_docs)
            hashing_time += batch_hashing_time

            loaded_at = utc_now()
            load_start = time.perf_counter()
            with conn:
                _insert_rows(conn, prepared, loaded_at)
            load_time += time.perf_counter() - load_start

            for row in prepared:
                existing_paths.add(row.source_path)
            inserted += len(prepared)

    peak_mb = _stop_memory()
    return LoadResult(
        mode="incremental_append_only",
        records_total=records_total,
        records_inserted=inserted,
        records_updated=0,
        records_skipped=skipped,
        records_deleted=0,
        db_writes=inserted,
        hashing_time_sec=hashing_time,
        load_time_sec=load_time,
        total_time_sec=time.perf_counter() - total_start,
        peak_memory_mb=peak_mb,
    )


# ---------------------------------------------------------------------------
# Ładowanie inkrementalne z detekcją usunięć
# ---------------------------------------------------------------------------

def incremental_load_with_deletes_batches(
    conn: sqlite3.Connection,
    document_batches: Iterable[Iterable[RawDocument]],
    *,
    detection_method: str = "hash_or_timestamp",
) -> LoadResult:
    """Wstawia nowe, aktualizuje zmienione, usuwa rekordy brakujące w źródle.

    Scenariusz: część rekordów zniknęła ze źródła danych.
    Full reload obsługuje to automatycznie (czyści tabelę).
    Ta strategia inkrementalna replikuje efekt full reload bez czyszczenia tabeli.
    """
    total_start = time.perf_counter()
    _start_memory()
    compute_hash = detection_method != "timestamp_only"
    existing = fetch_document_index(conn)
    seen_paths: Set[str] = set()
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
            seen_paths.add(row.source_path)
            current = existing.get(row.source_path)
            if current is None:
                to_insert.append(row)
            elif _is_changed(row, current, detection_method):
                to_update.append(row)
            else:
                skipped += 1

        loaded_at = utc_now()
        load_start = time.perf_counter()
        with conn:
            _insert_rows(conn, to_insert, loaded_at)
            conn.executemany(
                UPDATE_SQL,
                [
                    (row.content, row.file_size, row.file_hash, row.updated_at, loaded_at, row.source_path)
                    for row in to_update
                ],
            )
        load_time += time.perf_counter() - load_start

        for row in to_insert:
            existing[row.source_path] = {"file_hash": row.file_hash, "updated_at": row.updated_at}
        for row in to_update:
            existing[row.source_path] = {"file_hash": row.file_hash, "updated_at": row.updated_at}
        inserted += len(to_insert)
        updated += len(to_update)

    # Usuń rekordy których nie ma już w źródle
    paths_to_delete = set(existing.keys()) - seen_paths
    deleted = len(paths_to_delete)
    if paths_to_delete:
        load_start = time.perf_counter()
        with conn:
            delete_documents_by_paths(conn, paths_to_delete)
        load_time += time.perf_counter() - load_start

    peak_mb = _stop_memory()
    writes = inserted + updated + deleted
    return LoadResult(
        mode="incremental_with_deletes",
        records_total=records_total,
        records_inserted=inserted,
        records_updated=updated,
        records_skipped=skipped,
        records_deleted=deleted,
        db_writes=writes,
        hashing_time_sec=hashing_time,
        load_time_sec=load_time,
        total_time_sec=time.perf_counter() - total_start,
        peak_memory_mb=peak_mb,
    )


# ---------------------------------------------------------------------------
# Warianty plikowe – algorytmy full-reload i Ładowanie inkrementalne z detekcją usunięć,
# ---------------------------------------------------------------------------

def _open_fresh_file_conn(db_path: Path) -> sqlite3.Connection:
    """Usuwa istniejący plik bazy (+ WAL/SHM) i zwraca świeże połączenie."""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    conn = connect(db_path)
    init_db(conn)
    return conn


def full_reload_file(
    db_path: Path,
    document_batches: Iterable[Iterable[RawDocument]],
) -> LoadResult:
    """Pełne przeładowanie z zapisem do pliku SQLite."""
    conn = _open_fresh_file_conn(db_path)
    try:
        result = full_reload_batches(conn, document_batches)
    finally:
        conn.close()

    return LoadResult(
        mode="full_reload_file",
        records_total=result.records_total,
        records_inserted=result.records_inserted,
        records_updated=result.records_updated,
        records_skipped=result.records_skipped,
        records_deleted=result.records_deleted,
        db_writes=result.db_writes,
        hashing_time_sec=result.hashing_time_sec,
        load_time_sec=result.load_time_sec,
        total_time_sec=result.total_time_sec,
        peak_memory_mb=result.peak_memory_mb,
    )


def incremental_load_with_deletes_file(
    db_path: Path,
    base_document_batches: Iterable[Iterable[RawDocument]],
    scenario_document_batches: Iterable[Iterable[RawDocument]],
    *,
    detection_method: str = "hash_or_timestamp",
) -> LoadResult:
    """Inkrementalne ładowanie z detekcją usunięć, zapis do pliku SQLite."""
    conn = _open_fresh_file_conn(db_path)
    try:
        # Etap 1 - seed: załaduj dane bazowe (czas nie jest mierzony jako część
        # scenariusza - benchmark.py robi to samo dla wariantu in-memory).
        full_reload_batches(conn, base_document_batches)

        # Etap 2 - mierzony inkrement z usuwaniem.
        result = incremental_load_with_deletes_batches(
            conn,
            scenario_document_batches,
            detection_method=detection_method,
        )
    finally:
        conn.close()

    return LoadResult(
        mode="incremental_with_deletes_file",
        records_total=result.records_total,
        records_inserted=result.records_inserted,
        records_updated=result.records_updated,
        records_skipped=result.records_skipped,
        records_deleted=result.records_deleted,
        db_writes=result.db_writes,
        hashing_time_sec=result.hashing_time_sec,
        load_time_sec=result.load_time_sec,
        total_time_sec=result.total_time_sec,
        peak_memory_mb=result.peak_memory_mb,
    )