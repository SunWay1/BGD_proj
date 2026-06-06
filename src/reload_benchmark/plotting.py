from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Etykiety i paleta kolorów
# ---------------------------------------------------------------------------

SCENARIO_LABELS: Dict[str, str] = {
    "full_reload":                  "Pełne przeładowanie",
    "incremental_no_changes":       "Inkrementalny: bez zmian",
    "incremental_new_only":         "Inkrementalny: tylko nowe",
    "incremental_new_and_changed":  "Inkrementalny: nowe i zmienione",
    "incremental_high_change":      "Inkrementalny: duże zmiany (50%)",
    "incremental_append_only":      "Inkrementalny: tylko dopisywanie",
    "incremental_with_deletes":     "Inkrementalny: z usuwaniem",
}

VARIANT_LABELS: Dict[str, str] = {
    "short":  "krótki (~135 B)",
    "medium": "średni (~1 300 B)",
    "long":   "długi (~5 000 B)",
    "xlarge": "bardzo długi (~10 000 B)",
}

DETECTION_LABELS: Dict[str, str] = {
    "hash_or_timestamp": "Hash lub timestamp",
    "hash_only":         "Tylko hash (SHA-256)",
    "timestamp_only":    "Tylko timestamp",
}

PALETTE: Dict[str, str] = {
    "full_reload":                 "#1F2937",
    "incremental_no_changes":      "#059669",
    "incremental_new_only":        "#2563EB",
    "incremental_new_and_changed": "#D97706",
    "incremental_high_change":     "#DC2626",
    "incremental_append_only":     "#7C3AED",
    "incremental_with_deletes":    "#DB2777",
}

# Kolejność scenariuszy w wykresach (pełny reload zawsze pierwszy)
SCENARIO_ORDER = list(SCENARIO_LABELS.keys())


def _require_matplotlib():
    try:
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
    except ImportError as exc:
        raise RuntimeError(
            "matplotlib jest wymagany do generowania wykresów. Zainstaluj: pip install -r requirements.txt"
        ) from exc
    return plt, FuncFormatter


def _group(rows: Iterable[Dict[str, object]], key: str) -> Dict[str, List[Dict[str, object]]]:
    grouped: Dict[str, List] = {}
    for row in rows:
        grouped.setdefault(row[key], []).append(row)
    return grouped


def _style(plt) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(
        {
            "figure.dpi": 140,
            "savefig.dpi": 180,
            "figure.facecolor": "#F8FAFC",
            "axes.facecolor": "#FFFFFF",
            "axes.edgecolor": "#CBD5E1",
            "axes.labelcolor": "#111827",
            "axes.titlecolor": "#111827",
            "axes.titlesize": 15,
            "axes.titleweight": "bold",
            "axes.labelsize": 11,
            "legend.fontsize": 9,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "grid.color": "#E5E7EB",
            "grid.linewidth": 0.8,
            "lines.linewidth": 2.4,
            "lines.markersize": 7,
            "font.family": "DejaVu Sans",
        }
    )


def _format_int(value: float, _pos=None) -> str:
    return f"{int(value):,}".replace(",", " ")  # wąska spacja jako separator tysięcy


def _format_ratio(value: float, _pos=None) -> str:
    return f"{value:.2f}x"


def _format_pct(value: float, _pos=None) -> str:
    return f"{value:.0f}%"


def _finish(plt, path: Path, *, legend: bool = True, loc: str = "best") -> None:
    ax = plt.gca()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", alpha=0.9)
    ax.grid(True, axis="x", alpha=0.25)
    if legend:
        ax.legend(frameon=True, facecolor="#FFFFFF", edgecolor="#CBD5E1", loc=loc)
    plt.tight_layout()
    plt.savefig(path, bbox_inches="tight")
    plt.close()


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, int(len(s) * 0.95) - 1)
    return s[idx]


def _variant_label(variant: str) -> str:
    return VARIANT_LABELS.get(variant, variant)


# ---------------------------------------------------------------------------
# Wykresy głównego benchmarku
# ---------------------------------------------------------------------------

def generate_plots(rows: List[Dict[str, object]], plots_dir: Path) -> None:
    if not rows:
        return
    plt, formatter = _require_matplotlib()
    _style(plt)
    plots_dir.mkdir(parents=True, exist_ok=True)

    for variant, variant_rows in _group(rows, "text_variant").items():
        _plot_total_time_by_size(plt, formatter, variant, variant_rows, plots_dir)
        _plot_total_time_by_size_loglog(plt, formatter, variant, variant_rows, plots_dir)
        _plot_total_time_by_batch_size(plt, formatter, variant, variant_rows, plots_dir)
        _plot_incremental_vs_full_by_batch_size(plt, formatter, variant, variant_rows, plots_dir)
        _plot_db_writes(plt, formatter, variant, variant_rows, plots_dir)
        _plot_speedup(plt, formatter, variant, variant_rows, plots_dir)
        _plot_boxplots_by_size(plt, formatter, variant, variant_rows, plots_dir)
        _plot_heatmap_speedup(plt, formatter, variant, variant_rows, plots_dir)
        _plot_hashing_fraction(plt, formatter, variant, variant_rows, plots_dir)
        _plot_memory_usage(plt, formatter, variant, variant_rows, plots_dir)


def _plot_total_time_by_size(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Czas ładowania wg rozmiaru zbioru – linie z obszarem ±std."""
    plt.figure(figsize=(13, 7))
    for scenario in SCENARIO_ORDER:
        scenario_rows = [r for r in rows if r["scenario"] == scenario]
        if not scenario_rows:
            continue
        per_size: Dict[int, List[float]] = {}
        for row in scenario_rows:
            per_size.setdefault(int(row["dataset_size"]), []).append(float(row["total_time_sec"]))
        sizes = sorted(per_size)
        means = [_mean(per_size[s]) for s in sizes]
        stds = [_std(per_size[s]) for s in sizes]
        color = PALETTE.get(scenario, "#6B7280")
        label = SCENARIO_LABELS.get(scenario, scenario)
        plt.plot(sizes, means, marker="o", color=color, label=label)
        if max(stds) > 0:
            plt.fill_between(
                sizes,
                [m - s for m, s in zip(means, stds)],
                [m + s for m, s in zip(means, stds)],
                alpha=0.15, color=color,
            )
        if sizes:
            plt.annotate(
                label, xy=(sizes[-1], means[-1]),
                xytext=(8, 0), textcoords="offset points",
                va="center", fontsize=7, color=color,
            )
    ax = plt.gca()
    ax.xaxis.set_major_formatter(formatter(_format_int))
    plt.title(f"Czas ładowania wg rozmiaru zbioru – {_variant_label(variant)}")
    plt.xlabel("Liczba rekordów")
    plt.ylabel("Średni czas całkowity [s]")
    _finish(plt, plots_dir / f"czas_wg_rozmiaru_{variant}.png")


def _plot_total_time_by_size_loglog(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Skalowanie czasu ładowania – skala logarytmiczna na obu osiach."""
    plt.figure(figsize=(13, 7))
    for scenario in SCENARIO_ORDER:
        scenario_rows = [r for r in rows if r["scenario"] == scenario]
        if not scenario_rows:
            continue
        per_size: Dict[int, List[float]] = {}
        for row in scenario_rows:
            per_size.setdefault(int(row["dataset_size"]), []).append(float(row["total_time_sec"]))
        sizes = sorted(per_size)
        if len(sizes) < 2:
            continue
        means = [_mean(per_size[s]) for s in sizes]
        color = PALETTE.get(scenario, "#6B7280")
        plt.plot(sizes, means, marker="o", color=color, label=SCENARIO_LABELS.get(scenario, scenario))
    plt.xscale("log")
    plt.yscale("log")
    ax = plt.gca()
    ax.xaxis.set_major_formatter(formatter(_format_int))
    plt.title(f"Skalowanie czasu ładowania (skala log-log) – {_variant_label(variant)}")
    plt.xlabel("Liczba rekordów (log)")
    plt.ylabel("Czas całkowity [s] (log)")
    _finish(plt, plots_dir / f"skalowanie_loglog_{variant}.png")


def _plot_total_time_by_batch_size(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Wpływ rozmiaru partii na czas ładowania."""
    plt.figure(figsize=(13, 7))
    for scenario in SCENARIO_ORDER:
        scenario_rows = [r for r in rows if r["scenario"] == scenario]
        if not scenario_rows:
            continue
        per_batch: Dict[int, List[float]] = {}
        for row in scenario_rows:
            per_batch.setdefault(int(row["batch_size"]), []).append(float(row["total_time_sec"]))
        batches = sorted(per_batch)
        means = [_mean(per_batch[b]) for b in batches]
        stds = [_std(per_batch[b]) for b in batches]
        color = PALETTE.get(scenario, "#6B7280")
        plt.plot(batches, means, marker="o", color=color, label=SCENARIO_LABELS.get(scenario, scenario))
        if max(stds) > 0:
            plt.fill_between(
                batches,
                [m - s for m, s in zip(means, stds)],
                [m + s for m, s in zip(means, stds)],
                alpha=0.15, color=color,
            )
    ax = plt.gca()
    ax.xaxis.set_major_formatter(formatter(_format_int))
    plt.title(f"Wpływ rozmiaru partii na czas ładowania – {_variant_label(variant)}")
    plt.xlabel("Rozmiar partii")
    plt.ylabel("Średni czas całkowity [s]")
    _finish(plt, plots_dir / f"czas_wg_partii_{variant}.png")


def _plot_incremental_vs_full_by_batch_size(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Stosunek czasu inkrementalnego do full reload wg rozmiaru partii."""
    plt.figure(figsize=(13, 7))
    incremental_rows = [r for r in rows if r["mode"] != "full_reload"]
    for scenario in SCENARIO_ORDER:
        scenario_rows = [r for r in incremental_rows if r["scenario"] == scenario]
        if not scenario_rows:
            continue
        per_batch: Dict[int, List[float]] = {}
        for row in scenario_rows:
            ratio = row.get("incremental_to_full_ratio")
            if ratio is not None:
                per_batch.setdefault(int(row["batch_size"]), []).append(float(ratio))
        batches = sorted(per_batch)
        if not batches:
            continue
        means = [_mean(per_batch[b]) for b in batches]
        color = PALETTE.get(scenario, "#6B7280")
        plt.plot(batches, means, marker="o", color=color, label=SCENARIO_LABELS.get(scenario, scenario))
    plt.axhline(1.0, color="#111827", linestyle="--", linewidth=1.8, label="Próg opłacalności (1.0x)")
    ax = plt.gca()
    ax.xaxis.set_major_formatter(formatter(_format_int))
    ax.yaxis.set_major_formatter(formatter(_format_ratio))
    plt.title(f"Stosunek koszt inkrementalny / pełny reload wg partii – {_variant_label(variant)}")
    plt.xlabel("Rozmiar partii")
    plt.ylabel("Inkrementalny / pełne przeładowanie")
    _finish(plt, plots_dir / f"stosunek_wg_partii_{variant}.png")


def _plot_db_writes(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Liczba operacji zapisu do bazy dla każdego scenariusza."""
    latest_size = max(int(r["dataset_size"]) for r in rows)
    filtered = [r for r in rows if int(r["dataset_size"]) == latest_size]
    labels, values, colors = [], [], []
    for scenario in SCENARIO_ORDER:
        scenario_rows = [r for r in filtered if r["scenario"] == scenario]
        if not scenario_rows:
            continue
        avg_writes = _mean([float(r["db_writes"]) for r in scenario_rows])
        labels.append(SCENARIO_LABELS.get(scenario, scenario))
        values.append(avg_writes)
        colors.append(PALETTE.get(scenario, "#6B7280"))
    plt.figure(figsize=(13, 7))
    bars = plt.bar(labels, values, color=colors, edgecolor="#FFFFFF", linewidth=1.2)
    ax = plt.gca()
    ax.yaxis.set_major_formatter(formatter(_format_int))
    n_fmt = _format_int(latest_size)
    plt.title(f"Operacje zapisu do bazy – {n_fmt} rekordów – {_variant_label(variant)}")
    plt.xlabel("Scenariusz")
    plt.ylabel("Średnia liczba operacji zapisu (INSERT/UPDATE/DELETE)")
    plt.xticks(rotation=22, ha="right")
    ax.bar_label(bars, labels=[_format_int(v) for v in values], padding=3, fontsize=8)
    _finish(plt, plots_dir / f"operacje_zapisu_{variant}.png", legend=False)


def _plot_speedup(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Przyspieszenie inkrementalnego vs pełny reload wg rozmiaru zbioru."""
    plt.figure(figsize=(13, 7))
    for scenario in SCENARIO_ORDER:
        scenario_rows = [
            r for r in rows
            if r["scenario"] == scenario and r.get("speedup_vs_full") is not None
        ]
        if not scenario_rows:
            continue
        per_size: Dict[int, List[float]] = {}
        for row in scenario_rows:
            val = row.get("speedup_vs_full")
            if val is not None:
                per_size.setdefault(int(row["dataset_size"]), []).append(float(val))
        sizes = sorted(per_size)
        if not sizes:
            continue
        means = [_mean(per_size[s]) for s in sizes]
        stds = [_std(per_size[s]) for s in sizes]
        color = PALETTE.get(scenario, "#6B7280")
        plt.plot(sizes, means, marker="o", color=color, label=SCENARIO_LABELS.get(scenario, scenario))
        if max(stds) > 0:
            plt.fill_between(
                sizes,
                [max(0, m - s) for m, s in zip(means, stds)],
                [m + s for m, s in zip(means, stds)],
                alpha=0.15, color=color,
            )
    plt.axhline(1.0, color="#111827", linestyle="--", linewidth=1.8, label="Brak przyspieszenia (1.0x)")
    ax = plt.gca()
    ax.xaxis.set_major_formatter(formatter(_format_int))
    ax.yaxis.set_major_formatter(formatter(_format_ratio))
    plt.title(f"Przyspieszenie inkrementalnego vs pełny reload – {_variant_label(variant)}")
    plt.xlabel("Liczba rekordów")
    plt.ylabel("Przyspieszenie")
    _finish(plt, plots_dir / f"przyspieszenie_{variant}.png")


def _plot_boxplots_by_size(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Rozkład czasów ładowania (boxplot) wg rozmiaru zbioru danych.
    Każde pudełko skupia wyniki ze wszystkich partii i uruchomień (n_runs).
    """
    sizes = sorted(set(int(r["dataset_size"]) for r in rows))
    present_scenarios = [s for s in SCENARIO_ORDER if any(r["scenario"] == s for r in rows)]
    n_scen = len(present_scenarios)
    if n_scen == 0:
        return

    fig, axes = plt.subplots(1, len(sizes), figsize=(5 * len(sizes), 7), sharey=False)
    if len(sizes) == 1:
        axes = [axes]

    for ax, size in zip(axes, sizes):
        size_rows = [r for r in rows if int(r["dataset_size"]) == size]
        data = []
        tick_labels = []
        box_colors = []
        for scenario in present_scenarios:
            times = [float(r["total_time_sec"]) for r in size_rows if r["scenario"] == scenario]
            if times:
                data.append(times)
                tick_labels.append(SCENARIO_LABELS.get(scenario, scenario))
                box_colors.append(PALETTE.get(scenario, "#6B7280"))

        if not data:
            continue

        bp = ax.boxplot(
            data, patch_artist=True, notch=False,
            medianprops={"color": "#111827", "linewidth": 2},
            whiskerprops={"linestyle": "--", "linewidth": 1.2},
            flierprops={"marker": "o", "markersize": 4, "alpha": 0.5},
        )
        for patch, color in zip(bp["boxes"], box_colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.75)

        ax.set_xticklabels(tick_labels, rotation=30, ha="right", fontsize=8)
        ax.set_title(f"{_format_int(size)} rek.", fontsize=11, fontweight="bold")
        ax.set_ylabel("Czas całkowity [s]")
        ax.grid(True, axis="y", alpha=0.7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle(
        f"Rozkład czasów ładowania wg scenariusza – {_variant_label(variant)}",
        fontsize=14, fontweight="bold", y=1.02,
    )
    plt.tight_layout()
    plt.savefig(plots_dir / f"boxplot_czasy_{variant}.png", bbox_inches="tight", dpi=180)
    plt.close()


def _plot_heatmap_speedup(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Heatmapa przyspieszenia: rozmiar zbioru × rozmiar partii.
    Pokazuje dwa wybrane scenariusze inkrementalne obok siebie.
    """
    scenarios_to_show = [
        ("incremental_no_changes", "Bez zmian"),
        ("incremental_high_change", "Duże zmiany (50%)"),
    ]
    available = [
        (s, lbl) for s, lbl in scenarios_to_show
        if any(r["scenario"] == s and r.get("speedup_vs_full") is not None for r in rows)
    ]
    if not available:
        return

    sizes = sorted(set(int(r["dataset_size"]) for r in rows))
    batches = sorted(set(int(r["batch_size"]) for r in rows))
    if len(sizes) < 2 or len(batches) < 1:
        return

    fig, axes = plt.subplots(1, len(available), figsize=(6 * len(available), 5))
    if len(available) == 1:
        axes = [axes]

    im_last = None
    for ax, (scenario, sub_title) in zip(axes, available):
        # Oblicz średnie przyspieszenie dla każdej kombinacji (rozmiar, partia)
        data = [[0.0] * len(batches) for _ in range(len(sizes))]
        counts = [[0] * len(batches) for _ in range(len(sizes))]
        for r in rows:
            if r["scenario"] != scenario:
                continue
            spd = r.get("speedup_vs_full")
            if spd is None:
                continue
            si = sizes.index(int(r["dataset_size"]))
            bi = batches.index(int(r["batch_size"]))
            data[si][bi] += float(spd)
            counts[si][bi] += 1
        for si in range(len(sizes)):
            for bi in range(len(batches)):
                if counts[si][bi] > 0:
                    data[si][bi] /= counts[si][bi]

        im = ax.imshow(data, cmap="RdYlGn", vmin=0.5, vmax=2.5, aspect="auto")
        im_last = im
        ax.set_xticks(range(len(batches)))
        ax.set_xticklabels([_format_int(b) for b in batches], fontsize=9)
        ax.set_yticks(range(len(sizes)))
        ax.set_yticklabels([_format_int(s) for s in sizes], fontsize=9)
        ax.set_xlabel("Rozmiar partii")
        ax.set_ylabel("Liczba rekordów")
        ax.set_title(sub_title, fontsize=11, fontweight="bold")

        for si in range(len(sizes)):
            for bi in range(len(batches)):
                val = data[si][bi]
                text_color = "black" if 0.9 < val < 2.1 else "white"
                ax.text(bi, si, f"{val:.2f}x", ha="center", va="center", fontsize=10,
                        color=text_color, fontweight="bold")

    if im_last is not None:
        fig.colorbar(im_last, ax=axes, label="Przyspieszenie vs pełny reload", shrink=0.8)

    fig.suptitle(
        f"Heatmapa przyspieszenia – {_variant_label(variant)}\n"
        "Zielony = inkrementalny szybszy, Czerwony = full reload szybszy",
        fontsize=13, fontweight="bold",
    )
    plt.tight_layout()
    plt.savefig(plots_dir / f"heatmapa_przyspieszenia_{variant}.png", bbox_inches="tight", dpi=180)
    plt.close()


def _plot_hashing_fraction(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Struktura czasu ładowania: udział haszowania vs zapisu do bazy (skumulowany słupek)."""
    latest_size = max(int(r["dataset_size"]) for r in rows)
    filtered = [r for r in rows if int(r["dataset_size"]) == latest_size]

    labels, hash_fracs, load_fracs = [], [], []
    for scenario in SCENARIO_ORDER:
        scenario_rows = [r for r in filtered if r["scenario"] == scenario]
        if not scenario_rows:
            continue
        avg_total = _mean([float(r["total_time_sec"]) for r in scenario_rows])
        avg_hash = _mean([float(r["hashing_time_sec"]) for r in scenario_rows])
        avg_load = _mean([float(r["load_time_sec"]) for r in scenario_rows])
        if avg_total <= 0:
            continue
        labels.append(SCENARIO_LABELS.get(scenario, scenario))
        hash_fracs.append(avg_hash / avg_total * 100)
        load_fracs.append(avg_load / avg_total * 100)

    if not labels:
        return

    x = list(range(len(labels)))
    plt.figure(figsize=(13, 7))
    bars_hash = plt.bar(x, hash_fracs, label="Haszowanie SHA-256", color="#6366F1", alpha=0.9)
    plt.bar(x, load_fracs, bottom=hash_fracs, label="Zapis do bazy (SQL)", color="#10B981", alpha=0.9)

    # Warstwa "inne" (overhead: fetch indeksu, logika, itp.)
    others = [max(0.0, 100 - h - l) for h, l in zip(hash_fracs, load_fracs)]
    plt.bar(x, others, bottom=[h + l for h, l in zip(hash_fracs, load_fracs)],
            label="Overhead (fetch, logika)", color="#F59E0B", alpha=0.9)

    plt.xticks(x, labels, rotation=22, ha="right", fontsize=9)
    plt.ylabel("Udział w czasie całkowitym [%]")
    n_fmt = _format_int(latest_size)
    plt.title(
        f"Struktura czasu ładowania – {_variant_label(variant)} – {n_fmt} rekordów"
    )
    plt.ylim(0, 115)
    _finish(plt, plots_dir / f"struktura_czasu_{variant}.png", loc="upper right")


def _plot_memory_usage(plt, formatter, variant: str, rows: List[Dict], plots_dir: Path) -> None:
    """Szczytowe zużycie pamięci RAM (Python tracemalloc) wg scenariusza."""
    latest_size = max(int(r["dataset_size"]) for r in rows)
    filtered = [r for r in rows if int(r["dataset_size"]) == latest_size]

    labels, memories, stds_mem, colors = [], [], [], []
    for scenario in SCENARIO_ORDER:
        scenario_rows = [r for r in filtered if r["scenario"] == scenario]
        if not scenario_rows:
            continue
        mems = [float(r.get("peak_memory_mb", 0)) for r in scenario_rows]
        labels.append(SCENARIO_LABELS.get(scenario, scenario))
        memories.append(_mean(mems))
        stds_mem.append(_std(mems))
        colors.append(PALETTE.get(scenario, "#6B7280"))

    if not labels:
        return

    plt.figure(figsize=(13, 7))
    bars = plt.bar(
        labels, memories, color=colors, edgecolor="#FFFFFF", linewidth=1.2,
        yerr=stds_mem, capsize=4,
    )
    ax = plt.gca()
    ax.bar_label(bars, labels=[f"{m:.1f} MB" for m in memories], padding=4, fontsize=8)
    plt.xticks(rotation=22, ha="right", fontsize=9)
    plt.ylabel("Szczytowe zużycie pamięci RAM [MB]")
    n_fmt = _format_int(latest_size)
    plt.title(
        f"Zużycie pamięci RAM – {_variant_label(variant)} – {n_fmt} rekordów"
    )
    _finish(plt, plots_dir / f"pamiec_ram_{variant}.png", legend=False)


# ---------------------------------------------------------------------------
# Wykresy analizy progu opłacalności
# ---------------------------------------------------------------------------

def generate_threshold_plots(rows: List[Dict[str, object]], plots_dir: Path) -> None:
    if not rows:
        return
    plt, formatter = _require_matplotlib()
    _style(plt)
    plots_dir.mkdir(parents=True, exist_ok=True)

    for variant, variant_rows in _group(rows, "text_variant").items():
        _plot_threshold_by_change_ratio(plt, formatter, variant, variant_rows, plots_dir)

    # Wykres przekrojowy: próg opłacalności vs rozmiar dokumentu
    _plot_breakeven_vs_docsize(plt, formatter, rows, plots_dir)


def _plot_threshold_by_change_ratio(
    plt, formatter, variant: str, rows: List[Dict], plots_dir: Path
) -> None:
    """Stosunek inkrementalny/full reload wg % zmienionych rekordów z przedziałem ufności."""
    # Agreguj po change_ratio (może być wiele uruchomień)
    per_ratio: Dict[float, List[float]] = {}
    for row in rows:
        cr = float(row["change_ratio"])
        ratio_val = row.get("incremental_to_full_ratio")
        if ratio_val is not None:
            per_ratio.setdefault(cr, []).append(float(ratio_val))

    if not per_ratio:
        return

    ordered = sorted(per_ratio)
    x_values = [cr * 100 for cr in ordered]
    y_means = [_mean(per_ratio[cr]) for cr in ordered]
    y_stds = [_std(per_ratio[cr]) for cr in ordered]
    y_lower = [m - s for m, s in zip(y_means, y_stds)]
    y_upper = [m + s for m, s in zip(y_means, y_stds)]
    colors = ["#059669" if v < 1.0 else "#DC2626" for v in y_means]

    plt.figure(figsize=(13, 7))
    plt.plot(x_values, y_means, linewidth=2.8, color="#2563EB", label="Śr. inkrementalny / pełny reload")
    if max(y_stds) > 0:
        plt.fill_between(x_values, y_lower, y_upper, alpha=0.20, color="#2563EB", label="Przedział ±σ")
    plt.scatter(x_values, y_means, s=70, c=colors, edgecolors="#FFFFFF", linewidths=1.3, zorder=3)
    plt.axhline(1.0, color="#111827", linestyle="--", linewidth=1.8, label="Próg opłacalności (1.0x)")
    plt.fill_between(
        x_values, y_means, 1.0,
        where=[v < 1.0 for v in y_means], alpha=0.15, color="#059669",
    )
    plt.fill_between(
        x_values, y_means, 1.0,
        where=[v >= 1.0 for v in y_means], alpha=0.15, color="#DC2626",
    )
    for x_val, y_val in zip(x_values, y_means):
        plt.annotate(f"{y_val:.2f}x", (x_val, y_val),
                     textcoords="offset points", xytext=(0, 10),
                     ha="center", fontsize=8)
    ax = plt.gca()
    ax.yaxis.set_major_formatter(formatter(_format_ratio))
    size = int(rows[0]["dataset_size"]) if rows else 0
    plt.title(
        f"Próg opłacalności wg % zmienionych rekordów – {_variant_label(variant)}\n"
        f"({_format_int(size)} rekordów, zielony = inkrementalny lepszy)"
    )
    plt.xlabel("Zmienione rekordy [%]")
    plt.ylabel("Inkrementalny / pełne przeładowanie")
    _finish(plt, plots_dir / f"prog_oplacalnosci_{variant}.png")


def _plot_breakeven_vs_docsize(plt, formatter, rows: List[Dict], plots_dir: Path) -> None:
    """Próg opłacalności jako funkcja rozmiaru dokumentu (dla wszystkich wariantów)."""
    from .data_generator import VARIANT_AVG_SIZES

    # Wyznacz próg dla każdego wariantu tekstu na podstawie średnich ratios
    breakeven: Dict[str, float] = {}
    for variant, variant_rows in _group(rows, "text_variant").items():
        per_ratio: Dict[float, List[float]] = {}
        for row in variant_rows:
            cr = float(row["change_ratio"])
            ratio_val = row.get("incremental_to_full_ratio")
            if ratio_val is not None:
                per_ratio.setdefault(cr, []).append(float(ratio_val))
        # Znajdź pierwszy punkt gdzie średnia ratio >= 1.0
        for cr in sorted(per_ratio):
            mean_ratio = _mean(per_ratio[cr])
            if mean_ratio >= 1.0:
                breakeven[variant] = cr * 100
                break
        else:
            breakeven[variant] = 100.0  # Inkrementalny nigdy nie staje się wolniejszy

    variants_with_size = [
        (v, VARIANT_AVG_SIZES.get(v, 0)) for v in breakeven
        if VARIANT_AVG_SIZES.get(v, 0) > 0
    ]
    if len(variants_with_size) < 2:
        return

    variants_sorted = sorted(variants_with_size, key=lambda t: t[1])
    x_vals = [t[1] for t in variants_sorted]
    y_vals = [breakeven[t[0]] for t in variants_sorted]
    labels_pts = [_variant_label(t[0]) for t in variants_sorted]

    plt.figure(figsize=(12, 7))
    plt.plot(x_vals, y_vals, marker="o", color="#2563EB", linewidth=2.5, markersize=11)
    for x, y, lbl in zip(x_vals, y_vals, labels_pts):
        plt.annotate(f"{lbl}\n{y:.0f}%", (x, y),
                     textcoords="offset points", xytext=(10, 5), fontsize=9)
    plt.fill_between(x_vals, y_vals, 0, alpha=0.12, color="#059669",
                     label="Zakres korzyści inkrementalnego")
    plt.fill_between(x_vals, y_vals, 100, alpha=0.12, color="#DC2626",
                     label="Zakres korzyści pełnego reloadu")
    plt.xscale("log")
    ax = plt.gca()
    ax.yaxis.set_major_formatter(formatter(_format_pct))
    plt.xlabel("Średni rozmiar dokumentu [bajty] – skala log")
    plt.ylabel("Próg opłacalności [% zmienionych rekordów]")
    plt.title(
        "Próg opłacalności inkrementalnego vs rozmiar dokumentu\n"
        "Im większy dokument, tym wyższy próg – inkrementalny opłaca się dłużej"
    )
    plt.ylim(0, 110)
    _finish(plt, plots_dir / "prog_vs_rozmiar_dokumentu.png")


# ---------------------------------------------------------------------------
# Wykresy porównania metod detekcji
# ---------------------------------------------------------------------------

def generate_detection_plots(rows: List[Dict[str, object]], plots_dir: Path) -> None:
    if not rows:
        return
    plt, formatter = _require_matplotlib()
    _style(plt)
    plots_dir.mkdir(parents=True, exist_ok=True)

    methods = sorted(set(str(r["detection_method"]) for r in rows))
    method_labels = [DETECTION_LABELS.get(m, m) for m in methods]
    colors = ["#2563EB", "#059669", "#D97706", "#DC2626"]

    # --- Wykres 1: Całkowity czas wg metody detekcji ---
    plt.figure(figsize=(12, 7))
    per_method: Dict[str, List[float]] = {m: [] for m in methods}
    hash_times: Dict[str, List[float]] = {m: [] for m in methods}
    for row in rows:
        m = str(row["detection_method"])
        per_method[m].append(float(row["total_time_sec"]))
        hash_times[m].append(float(row.get("hashing_time_sec", 0)))

    avg_total = [_mean(per_method[m]) for m in methods]
    std_total = [_std(per_method[m]) for m in methods]
    avg_hash = [_mean(hash_times[m]) for m in methods]
    x = list(range(len(methods)))

    bars = plt.bar(
        x, avg_total, color=colors[:len(methods)],
        yerr=std_total, capsize=5, edgecolor="#FFFFFF", linewidth=1.2, alpha=0.9,
    )
    # Nałóż udział haszowania
    plt.bar(
        x, avg_hash, color="#9CA3AF", alpha=0.6,
        label="z czego haszowanie", edgecolor="#FFFFFF", linewidth=1.2,
    )
    plt.bar_label(bars, labels=[f"{t:.4f}s" for t in avg_total], padding=3, fontsize=9)
    plt.xticks(x, method_labels, fontsize=10)
    plt.ylabel("Średni czas całkowity [s]")
    change_ratio = float(rows[0].get("change_ratio", 0)) * 100 if rows else 0
    size = int(rows[0].get("dataset_size", 0)) if rows else 0
    variant = str(rows[0].get("text_variant", "")) if rows else ""
    plt.title(
        f"Porównanie metod detekcji zmian\n"
        f"({_format_int(size)} rek., {_variant_label(variant)}, {change_ratio:.0f}% zmienionych)"
    )
    _finish(plt, plots_dir / "metody_detekcji_czas.png", loc="upper left")

    # --- Wykres 2: Udział haszowania wg metody ---
    plt.figure(figsize=(10, 6))
    fracs = []
    for m in methods:
        t = _mean(per_method[m])
        h = _mean(hash_times[m])
        fracs.append(h / t * 100 if t > 0 else 0)
    bars2 = plt.bar(method_labels, fracs, color=colors[:len(methods)], edgecolor="#FFFFFF", linewidth=1.2)
    plt.bar_label(bars2, labels=[f"{f:.1f}%" for f in fracs], padding=3, fontsize=9)
    plt.ylabel("Udział haszowania w czasie całkowitym [%]")
    plt.title("Udział czasu haszowania wg metody detekcji")
    plt.ylim(0, 110)
    _finish(plt, plots_dir / "metody_detekcji_haszowanie.png", legend=False)
