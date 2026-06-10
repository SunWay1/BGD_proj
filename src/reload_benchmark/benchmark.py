from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
import sqlite3

from .data_generator import iter_document_batches, iter_scenario_batches
from .database import connect, count_documents, db_size_bytes, init_db, insert_benchmark_run
from .loaders import (
    LoadResult,
    full_reload_batches,
    incremental_append_only_batches,
    incremental_load_batches,
    incremental_load_with_deletes_batches,
    full_reload_file,
    incremental_load_with_deletes_file
)
from .plotting import generate_detection_plots, generate_plots, generate_threshold_plots, generate_storage_plots


@dataclass(frozen=True)
class BenchmarkConfig:
    db_path: Path
    results_csv: Path
    plots_dir: Path
    sizes: List[int]
    variants: List[str]
    batch_sizes: List[int]
    new_ratio: float = 0.10
    change_ratio: float = 0.10
    high_change_ratio: float = 0.50
    delete_ratio: float = 0.05
    n_runs: int = 1
    create_plots: bool = True


@dataclass(frozen=True)
class ThresholdConfig:
    db_path: Path
    results_csv: Path
    plots_dir: Path
    size: int
    variants: List[str]
    batch_size: int
    change_ratios: List[float]
    n_runs: int = 1
    create_plots: bool = True


@dataclass(frozen=True)
class DetectionConfig:
    db_path: Path
    results_csv: Path
    plots_dir: Path
    size: int
    variant: str
    batch_size: int
    change_ratio: float
    detection_methods: List[str]
    n_runs: int = 1
    create_plots: bool = True



def _run_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh_connection(db_path: Path):
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        if path.exists():
            path.unlink()
    conn = connect(db_path)
    init_db(conn)
    return conn


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
    db_path: Path,
    result: LoadResult,
    scenario: str,
    dataset_size: int,
    text_variant: str,
    batch_size: int,
    change_ratio: Optional[float],
    delete_ratio: float,
    change_distribution: str,
    detection_method: str,
    run_index: int,
    full_reload_time: Optional[float],
) -> Dict[str, object]:
    ratio = None
    speedup = None
    if full_reload_time and result.mode != "full_reload":
        ratio = result.total_time_sec / full_reload_time
        speedup = full_reload_time / result.total_time_sec if result.total_time_sec > 0 else None

    row: Dict[str, object] = {
        "run_at": _run_at(),
        "backend": "sqlite",
        "mode": result.mode,
        "scenario": scenario,
        "dataset_size": dataset_size,
        "text_variant": text_variant,
        "batch_size": batch_size,
        "change_ratio": change_ratio,
        "change_distribution": change_distribution,
        "delete_ratio": delete_ratio,
        "detection_method": detection_method,
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
        "db_size_bytes": db_size_bytes(db_path) if db_path is not None else 0,
        "incremental_to_full_ratio": ratio,
        "speedup_vs_full": speedup,
    }
    insert_benchmark_run(conn, row)
    return row


def _progress(msg: str) -> None:
    import sys
    print(msg, flush=True)
    sys.stdout.flush()


def run_benchmark(config: BenchmarkConfig) -> List[Dict[str, object]]:
    """Uruchamia pełny zestaw scenariuszy benchmarku."""
    total_combos = len(config.variants) * len(config.sizes) * len(config.batch_sizes) * config.n_runs
    combo_idx = 0
    _progress(f"[benchmark] Start: {total_combos} kombinacji (warianty={config.variants}, rozmiary={config.sizes})")
    rows: List[Dict[str, object]] = []
    conn = _fresh_connection(config.db_path)
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
                            f"uruchomienie={run_idx+1}/{config.n_runs}"
                        )
                        # --- Pełne przeładowanie (baseline) ---
                        full_result = full_reload_batches(conn, base_batches())
                        if count_documents(conn) != size:
                            raise RuntimeError("full_reload zwrócił nieoczekiwaną liczbę dokumentów")
                        full_time = full_result.total_time_sec
                        rows.append(
                            _record_run(
                                conn=conn,
                                db_path=config.db_path,
                                result=full_result,
                                scenario="full_reload",
                                dataset_size=size,
                                text_variant=variant,
                                batch_size=batch_size,
                                change_ratio=None,
                                delete_ratio=0.0,
                                change_distribution="skupione",
                                detection_method="hash_or_timestamp",
                                run_index=run_idx,
                                full_reload_time=None,
                            )
                        )

                        new_count = max(1, int(size * config.new_ratio))

                        # --- Scenariusze inkrementalne ---
                        incremental_scenarios = [
                            ("incremental_no_changes",       0.0,                    0,         0.0,                "skupione"),
                            ("incremental_new_only",         0.0,                    new_count,  0.0,                "skupione"),
                            ("incremental_new_and_changed",  config.change_ratio,    new_count,  0.0,                "skupione"),
                            ("incremental_high_change",      config.high_change_ratio, 0,        0.0,                "skupione"),
                        ]

                        for scenario, change_ratio, scenario_new_count, del_ratio, dist in incremental_scenarios:
                            full_reload_batches(conn, base_batches())
                            result = incremental_load_batches(
                                conn,
                                iter_scenario_batches(
                                    size, variant, batch_size,
                                    change_ratio=change_ratio,
                                    new_count=scenario_new_count,
                                    change_distribution=dist,
                                    delete_ratio=del_ratio,
                                ),
                            )
                            rows.append(
                                _record_run(
                                    conn=conn,
                                    db_path=config.db_path,
                                    result=result,
                                    scenario=scenario,
                                    dataset_size=size,
                                    text_variant=variant,
                                    batch_size=batch_size,
                                    change_ratio=change_ratio,
                                    delete_ratio=del_ratio,
                                    change_distribution=dist,
                                    detection_method="hash_or_timestamp",
                                    run_index=run_idx,
                                    full_reload_time=full_time,
                                )
                            )

                        # --- Tylko dopisywanie (append-only) ---
                        full_reload_batches(conn, base_batches())
                        result_append = incremental_append_only_batches(
                            conn,
                            iter_scenario_batches(size, variant, batch_size, new_count=new_count),
                        )
                        rows.append(
                            _record_run(
                                conn=conn,
                                db_path=config.db_path,
                                result=result_append,
                                scenario="incremental_append_only",
                                dataset_size=size,
                                text_variant=variant,
                                batch_size=batch_size,
                                change_ratio=0.0,
                                delete_ratio=0.0,
                                change_distribution="skupione",
                                detection_method="brak",
                                run_index=run_idx,
                                full_reload_time=full_time,
                            )
                        )

                        # --- Inkrementalny z usuwaniem ---
                        full_reload_batches(conn, base_batches())
                        result_del = incremental_load_with_deletes_batches(
                            conn,
                            iter_scenario_batches(
                                size, variant, batch_size,
                                delete_ratio=config.delete_ratio,
                            ),
                        )
                        # Czas referencyjny: full reload pomniejszonego zbioru
                        result_full_reduced = full_reload_batches(
                            conn,
                            iter_scenario_batches(size, variant, batch_size, delete_ratio=config.delete_ratio),
                        )
                        full_time_reduced = result_full_reduced.total_time_sec
                        rows.append(
                            _record_run(
                                conn=conn,
                                db_path=config.db_path,
                                result=result_del,
                                scenario="incremental_with_deletes",
                                dataset_size=size,
                                text_variant=variant,
                                batch_size=batch_size,
                                change_ratio=0.0,
                                delete_ratio=config.delete_ratio,
                                change_distribution="skupione",
                                detection_method="hash_or_timestamp",
                                run_index=run_idx,
                                full_reload_time=full_time_reduced,
                            )
                        )

        _write_csv(config.results_csv, rows)
        _progress(f"[benchmark] Zapisano CSV: {config.results_csv}")
        if config.create_plots:
            _progress("[benchmark] Generowanie wykresów...")
            generate_plots(rows, config.plots_dir)
            _progress(f"[benchmark] Wykresy zapisane w: {config.plots_dir}")
        return rows
    finally:
        conn.close()


def run_threshold(config: ThresholdConfig) -> List[Dict[str, object]]:
    """Wyznacza próg opłacalności inkrementalnego ładowania vs pełny reload."""
    total = len(config.variants) * config.n_runs * len(config.change_ratios)
    done = 0
    _progress(
        f"[threshold] Start: {total} pomiarów "
        f"(warianty={config.variants}, {len(config.change_ratios)} punktów, {config.n_runs} powtórzeń)"
    )
    rows: List[Dict[str, object]] = []
    conn = _fresh_connection(config.db_path)
    try:
        for variant in config.variants:
            base_batches = lambda: iter_document_batches(config.size, variant, config.batch_size)

            for run_idx in range(config.n_runs):
                _progress(f"  [threshold] wariant={variant}, uruchomienie={run_idx+1}/{config.n_runs}")
                full_result = full_reload_batches(conn, base_batches())
                full_time = full_result.total_time_sec
                threshold_ratio: Optional[float] = None

                for change_ratio in config.change_ratios:
                    done += 1
                    full_reload_batches(conn, base_batches())
                    incremental = incremental_load_batches(
                        conn,
                        iter_scenario_batches(
                            config.size, variant, config.batch_size,
                            change_ratio=change_ratio,
                            new_count=0,
                        ),
                    )
                    ratio = incremental.total_time_sec / full_time if full_time > 0 else None
                    speedup = full_time / incremental.total_time_sec if incremental.total_time_sec > 0 else None
                    recommended = "full_reload" if ratio is not None and ratio >= 1.0 else "incremental_load"
                    if threshold_ratio is None and ratio is not None and ratio >= 1.0:
                        threshold_ratio = change_ratio
                    rows.append(
                        {
                            "run_at": _run_at(),
                            "text_variant": variant,
                            "dataset_size": config.size,
                            "batch_size": config.batch_size,
                            "change_ratio": change_ratio,
                            "run_index": run_idx,
                            "full_reload_time_sec": full_time,
                            "incremental_time_sec": incremental.total_time_sec,
                            "incremental_hashing_sec": incremental.hashing_time_sec,
                            "incremental_load_sec": incremental.load_time_sec,
                            "peak_memory_mb": incremental.peak_memory_mb,
                            "incremental_to_full_ratio": ratio,
                            "speedup_vs_full": speedup,
                            "recommended_strategy": recommended,
                            "break_even_change_ratio": threshold_ratio,
                        }
                    )

        _write_csv(config.results_csv, rows)
        _progress(f"[threshold] Zapisano CSV: {config.results_csv}")
        if config.create_plots:
            _progress("[threshold] Generowanie wykresów...")
            generate_threshold_plots(rows, config.plots_dir)
            _progress(f"[threshold] Wykresy zapisane w: {config.plots_dir}")
        return rows
    finally:
        conn.close()


def run_detection_comparison(config: DetectionConfig) -> List[Dict[str, object]]:
    """Porównuje metody detekcji zmian: hash, timestamp, hash+timestamp."""
    total = config.n_runs * len(config.detection_methods)
    done = 0
    _progress(f"[detection] Start: {total} pomiarów (metody={config.detection_methods})")
    rows: List[Dict[str, object]] = []
    conn = _fresh_connection(config.db_path)
    try:
        base_batches = lambda: iter_document_batches(config.size, config.variant, config.batch_size)
        scenario_batches = lambda: iter_scenario_batches(
            config.size, config.variant, config.batch_size,
            change_ratio=config.change_ratio,
        )

        for run_idx in range(config.n_runs):
            for method in config.detection_methods:
                done += 1
                _progress(f"  [{done}/{total}] metoda={method}, uruchomienie={run_idx+1}/{config.n_runs}")
                full_reload_batches(conn, base_batches())
                result = incremental_load_batches(conn, scenario_batches(), detection_method=method)
                rows.append(
                    {
                        "run_at": _run_at(),
                        "detection_method": method,
                        "dataset_size": config.size,
                        "text_variant": config.variant,
                        "batch_size": config.batch_size,
                        "change_ratio": config.change_ratio,
                        "run_index": run_idx,
                        "hashing_time_sec": result.hashing_time_sec,
                        "load_time_sec": result.load_time_sec,
                        "total_time_sec": result.total_time_sec,
                        "peak_memory_mb": result.peak_memory_mb,
                        "records_updated": result.records_updated,
                        "db_writes": result.db_writes,
                    }
                )

        _write_csv(config.results_csv, rows)
        _progress(f"[detection] Zapisano CSV: {config.results_csv}")
        if config.create_plots:
            _progress("[detection] Generowanie wykresów...")
            generate_detection_plots(rows, config.plots_dir)
            _progress(f"[detection] Wykresy zapisane w: {config.plots_dir}")
        return rows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Porównanie in-memory vs plik
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StorageConfig:
    """Konfiguracja benchmarku porównującego bazę in-memory z bazą plikową.

    Atrybuty:
        results_db_path:  Plik SQLite przechowujący rekordy benchmark_runs.
        file_db_path:     Plik SQLite używany przez testowane scenariusze plikowe.
                          Przy każdym uruchomieniu scenariusza plik jest usuwany
                          i tworzony od zera (przez `_open_fresh_file_conn`).
    """
    results_db_path: Path
    file_db_path: Path
    results_csv: Path
    plots_dir: Path
    sizes: List[int]
    variant: str
    batch_sizes: List[int]
    delete_ratio: float = 0.05
    n_runs: int = 1
    create_plots: bool = True


def run_storage_comparison(config: StorageConfig) -> List[Dict[str, object]]:
    """Porównuje te same algorytmy na bazie in-memory i na pliku SQLite.

    Dla każdej kombinacji (wariant x rozmiar x partia x powtórzenie) uruchamia
    cztery scenariusze

    Kolumna ``mode`` w CSV jednoznacznie identyfikuje backend, a ``scenario``
    identyfikuje algorytm - co ułatwia porównanie parami na wykresach.

    Pomiar inkrementu plikowego jest sprawiedliwy wobec odpowiednika in-memory:
    seed (full reload danych bazowych) nie jest wliczany do czasu inkrementu
    w żadnym z wariantów.

    Args:
        config: Konfiguracja benchmarku.

    Returns:
        Lista słowników z wynikami - jeden wiersz na scenariusz x uruchomienie.
    """
    total_combos = (
        len(config.sizes) * len(config.batch_sizes) * config.n_runs
    )
    combo_idx = 0
    _progress(
        f"[storage] Start: {total_combos} kombinacji "
        f"(rozmiary={config.sizes}, partie={config.batch_sizes}, powtórzenia={config.n_runs})"
    )

    rows: List[Dict[str, object]] = []
    # Rejestr benchmark_runs ląduje w oddzielnym pliku, żeby nie zakłócać
    # pomiaru testowanej bazy plikowej.
    results_conn = _fresh_connection(config.results_db_path)

    try:
        for size in config.sizes:
            for batch_size in config.batch_sizes:
                # Lambdy przechwytują bieżące wartości zmiennych pętli
                # przez parametry domyślne – unika problemów z domknięciami.
                def base_batches(v=config.variant, s=size, b=batch_size):
                    return iter_document_batches(s, v, b)

                def scenario_batches(v=config.variant, s=size, b=batch_size, dr=config.delete_ratio):
                    return iter_scenario_batches(s, v, b, delete_ratio=dr)

                for run_idx in range(config.n_runs):
                    combo_idx += 1
                    _progress(
                        f"  [{combo_idx}/{total_combos}] wariant={config.variant}, "
                        f"rozmiar={size:,}, partia={batch_size:,}, "
                        f"uruchomienie={run_idx + 1}/{config.n_runs}"
                    )

                    # ── 1. Full reload – in-memory ────────────────────────
                    # Świeże połączenie :memory: dla każdego uruchomienia,
                    # żeby poprzedni stan nie wpływał na pomiar.
                    mem_conn = sqlite3.connect(":memory:")
                    mem_conn.row_factory = sqlite3.Row
                    mem_conn.execute("PRAGMA journal_mode = WAL")
                    mem_conn.execute("PRAGMA synchronous = NORMAL")
                    init_db(mem_conn)
                    try:
                        result_fr_mem = full_reload_batches(mem_conn, base_batches())
                    finally:
                        mem_conn.close()

                    full_reload_time_mem = result_fr_mem.total_time_sec
                    rows.append(
                        _record_run(
                            conn=results_conn,
                            db_path=None,           # in-memory → db_size_bytes = 0
                            result=result_fr_mem,
                            scenario="full_reload_in_memory",
                            dataset_size=size,
                            text_variant=config.variant,
                            batch_size=batch_size,
                            change_ratio=None,
                            delete_ratio=0.0,
                            change_distribution="n/a",
                            detection_method="n/a",
                            run_index=run_idx,
                            full_reload_time=None,
                        )
                    )

                    # ── 2. Full reload – plik ─────────────────────────────
                    # full_reload_file otwiera i zamyka połączenie wewnętrznie,
                    # więc czas close() (fsync WAL) jest wliczony w pomiar.
                    result_fr_file = full_reload_file(config.file_db_path, base_batches())

                    full_reload_time_file = result_fr_file.total_time_sec
                    rows.append(
                        _record_run(
                            conn=results_conn,
                            db_path=config.file_db_path,
                            result=result_fr_file,
                            scenario="full_reload_files",
                            dataset_size=size,
                            text_variant=config.variant,
                            batch_size=batch_size,
                            change_ratio=None,
                            delete_ratio=0.0,
                            change_distribution="n/a",
                            detection_method="n/a",
                            run_index=run_idx,
                            full_reload_time=None,
                        )
                    )

                    # ── 3. Incremental with deletes – in-memory ───────────
                    # Seed nie jest mierzony: pełny reload na :memory: przed
                    # inkrementem (identycznie jak w run_benchmark).
                    mem_conn = sqlite3.connect(":memory:")
                    mem_conn.row_factory = sqlite3.Row
                    mem_conn.execute("PRAGMA journal_mode = WAL")
                    mem_conn.execute("PRAGMA synchronous = NORMAL")
                    init_db(mem_conn)
                    try:
                        full_reload_batches(mem_conn, base_batches())
                        result_del_mem = incremental_load_with_deletes_batches(
                            mem_conn,
                            scenario_batches(),
                        )
                    finally:
                        mem_conn.close()

                    rows.append(
                        _record_run(
                            conn=results_conn,
                            db_path=None,
                            result=result_del_mem,
                            scenario="incremental_with_deletes_in_memory",
                            dataset_size=size,
                            text_variant=config.variant,
                            batch_size=batch_size,
                            change_ratio=None,
                            delete_ratio=config.delete_ratio,
                            change_distribution="n/a",
                            detection_method="hash_or_timestamp",
                            run_index=run_idx,
                            full_reload_time=full_reload_time_mem,
                        )
                    )

                    # ── 4. Incremental with deletes – plik ────────────────
                    # Seed jest realizowany wewnątrz incremental_load_with_deletes_file
                    # na tym samym połączeniu plikowym (WAL nie jest opróżniany między
                    # etapami – symuluje warunki rzeczywistego procesu ETL).
                    result_del_file = incremental_load_with_deletes_file(
                        config.file_db_path,
                        base_batches(),
                        scenario_batches(),
                    )

                    rows.append(
                        _record_run(
                            conn=results_conn,
                            db_path=config.file_db_path,
                            result=result_del_file,
                            scenario="incremental_with_deletes_files",
                            dataset_size=size,
                            text_variant=config.variant,
                            batch_size=batch_size,
                            change_ratio=None,
                            delete_ratio=config.delete_ratio,
                            change_distribution="n/a",
                            detection_method="hash_or_timestamp",
                            run_index=run_idx,
                            full_reload_time=full_reload_time_file,
                        )
                    )

        _write_csv(config.results_csv, rows)
        _progress(f"[storage] Zapisano CSV: {config.results_csv}")
        if config.create_plots:
            _progress("[storage] Generowanie wykresów...")
            generate_storage_plots(rows, config.plots_dir)
            _progress(f"[storage] Wykresy zapisane w: {config.plots_dir}")
        return rows
    finally:
        results_conn.close()
