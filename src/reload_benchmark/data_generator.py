from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import string
from typing import Iterable, Iterator, List, Sequence


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class RawDocument:
    source_path: str
    content: str
    created_at: str
    updated_at: str


_SHORT_WORDS = (
    "data", "load", "batch", "table", "record", "hash", "sqlite", "change",
    "source", "target", "file", "metric", "reload", "insert", "update", "skip",
)

# Przybliżone średnie rozmiary dokumentów w bajtach – używane w analizie progów
VARIANT_AVG_SIZES: dict = {
    "short":  135,
    "medium": 1_300,
    "long":   5_000,
    "xlarge": 10_000,
}


def _timestamp(index: int, version: int = 0) -> str:
    return (BASE_TIME + timedelta(seconds=index + version * 1_000_000)).isoformat()


def _make_content(rng: random.Random, index: int, variant: str, version: int = 0) -> str:
    if variant == "short":
        # ~135 znaków – krótki ciąg słów domenowych
        words = [_SHORT_WORDS[(index + i + version) % len(_SHORT_WORDS)] for i in range(18)]
        return f"doc={index} version={version} " + " ".join(words)
    if variant == "medium":
        # ~1 300 znaków – 2 akapity po 90 słów
        paragraphs = []
        for paragraph in range(2):
            words = [rng.choice(_SHORT_WORDS) for _ in range(90)]
            suffix = "".join(rng.choice(string.ascii_lowercase) for _ in range(16))
            paragraphs.append(
                f"doc={index} version={version} paragraph={paragraph} {suffix} " + " ".join(words)
            )
        return "\n".join(paragraphs)
    if variant == "long":
        # ~5 000 znaków – 8 akapitów po 90 słów
        paragraphs = []
        for paragraph in range(8):
            words = [rng.choice(_SHORT_WORDS) for _ in range(90)]
            suffix = "".join(rng.choice(string.ascii_lowercase) for _ in range(16))
            paragraphs.append(
                f"doc={index} version={version} paragraph={paragraph} {suffix} " + " ".join(words)
            )
        return "\n".join(paragraphs)
    if variant == "xlarge":
        # ~10 000 znaków – 16 akapitów po 90 słów
        paragraphs = []
        for paragraph in range(16):
            words = [rng.choice(_SHORT_WORDS) for _ in range(90)]
            suffix = "".join(rng.choice(string.ascii_lowercase) for _ in range(16))
            paragraphs.append(
                f"doc={index} version={version} paragraph={paragraph} {suffix} " + " ".join(words)
            )
        return "\n".join(paragraphs)
    raise ValueError(f"Nieobsługiwany wariant tekstu: {variant!r}")


def generate_document(index: int, variant: str, *, version: int = 0, total_size: int = 0) -> RawDocument:
    seed = 10_000 + index * 31 + version * 43 + len(variant)
    rng = random.Random(seed)
    return RawDocument(
        source_path=f"generated/{variant}/document_{index:08d}.txt",
        content=_make_content(rng, index, variant, version),
        created_at=_timestamp(index, 0),
        updated_at=_timestamp(index, version),
    )


def generate_documents(size: int, variant: str, *, start_index: int = 0, version: int = 0) -> List[RawDocument]:
    return [generate_document(index, variant, version=version, total_size=size) for index in range(start_index, start_index + size)]


def iter_documents(size: int, variant: str, *, start_index: int = 0, version: int = 0) -> Iterator[RawDocument]:
    for index in range(start_index, start_index + size):
        yield generate_document(index, variant, version=version, total_size=size)


def iter_document_batches(size: int, variant: str, batch_size: int, *, start_index: int = 0, version: int = 0) -> Iterator[List[RawDocument]]:
    if batch_size <= 0:
        raise ValueError("batch_size musi być większy od zera")
    batch: List[RawDocument] = []
    for doc in iter_documents(size, variant, start_index=start_index, version=version):
        batch.append(doc)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def iter_scenario_batches(
    size: int,
    variant: str,
    batch_size: int,
    *,
    change_ratio: float = 0.0,
    new_count: int = 0,
    change_distribution: str = "skupione",
    delete_ratio: float = 0.0,
) -> Iterator[List[RawDocument]]:
    """Generuje partie dokumentów dla scenariusza benchmarku.

    change_distribution:
      "skupione"  – zmiany skupione na początku zbioru (domyślne, zgodne z oryginałem)
      "czasowe"   – zmiany skupione na końcu (nowsze rekordy zmieniają się częściej)
      "rowne"     – zmiany losowo równomiernie rozłożone po całym zbiorze
    delete_ratio:
      Ułamek rekordów usuniętych ze źródła (symulacja usunięć).
    """
    if not 0 <= change_ratio <= 1:
        raise ValueError("change_ratio musi być między 0 a 1")
    if not 0 <= delete_ratio < 1:
        raise ValueError("delete_ratio musi być między 0 a 1 (wyłącznie)")

    # Efektywny rozmiar źródła po uwzględnieniu usuniętych rekordów
    effective_size = max(0, size - int(size * delete_ratio))
    change_count = int(effective_size * change_ratio)

    # Wybierz indeksy zmienionych dokumentów zgodnie z rozkładem
    if change_distribution == "czasowe":
        # Nowsze (wyższy indeks) zmieniają się częściej
        start_idx = max(0, effective_size - change_count)
        changed_indices = set(range(start_idx, effective_size))
    elif change_distribution == "rowne":
        # Losowy równomierny wybór – deterministyczny na podstawie parametrów
        rng = random.Random(12345 + size + int(change_ratio * 10000))
        indices = list(range(effective_size))
        rng.shuffle(indices)
        changed_indices = set(indices[:change_count])
    else:
        # Skupione – pierwsze change_count indeksów (domyślne)
        changed_indices = set(range(change_count))

    total = effective_size + new_count
    batch: List[RawDocument] = []

    for index in range(effective_size):
        version = 1 if index in changed_indices else 0
        doc = generate_document(index, variant, version=version, total_size=total)
        batch.append(doc)
        if len(batch) == batch_size:
            yield batch
            batch = []

    # Dołącz nowe rekordy (nie istniejące wcześniej w bazie)
    for index in range(size, size + new_count):
        doc = generate_document(index, variant, version=0, total_size=total)
        batch.append(doc)
        if len(batch) == batch_size:
            yield batch
            batch = []

    if batch:
        yield batch


def with_changed_documents(documents: Iterable[RawDocument], change_ratio: float, variant: str) -> List[RawDocument]:
    docs = list(documents)
    change_count = int(len(docs) * change_ratio)
    result: List[RawDocument] = []
    for index, doc in enumerate(docs):
        if index < change_count:
            logical_index = int(doc.source_path.rsplit("_", 1)[1].split(".")[0])
            result.append(generate_document(logical_index, variant, version=1, total_size=len(docs)))
        else:
            result.append(doc)
    return result


def with_new_documents(documents: Iterable[RawDocument], new_count: int, variant: str) -> List[RawDocument]:
    docs = list(documents)
    return docs + generate_documents(new_count, variant, start_index=len(docs), version=0)


def write_documents_to_disk(output_dir: Path, sizes: Sequence[int], variants: Sequence[str]) -> List[Path]:
    written_roots: List[Path] = []
    for variant in variants:
        for size in sizes:
            root = output_dir / variant / str(size)
            root.mkdir(parents=True, exist_ok=True)
            manifest_path = root / "manifest.csv"
            with manifest_path.open("w", newline="", encoding="utf-8") as manifest:
                writer = csv.DictWriter(manifest, fieldnames=["source_path", "created_at", "updated_at", "file_size"])
                writer.writeheader()
                for doc in iter_documents(size, variant):
                    file_path = root / Path(doc.source_path).name
                    file_path.write_text(doc.content, encoding="utf-8")
                    writer.writerow(
                        {
                            "source_path": str(file_path),
                            "created_at": doc.created_at,
                            "updated_at": doc.updated_at,
                            "file_size": len(doc.content.encode("utf-8")),
                        }
                    )
            written_roots.append(root)
    return written_roots
