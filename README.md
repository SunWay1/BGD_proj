# Full Reload vs Incremental Load — Benchmark

Projekt porównuje dwie strategie ładowania danych do bazy SQLite:

- **Pełne przeładowanie (Full Reload)** — usuwa wszystkie rekordy i wgrywa zbiór od zera
- **Ładowanie inkrementalne (Incremental Load)** — sprawdza każdy rekord i zapisuje tylko to, co się zmieniło

Analiza mierzy czas, liczbę operacji na bazie i zużycie RAM dla różnych rozmiarów zbiorów, długości dokumentów i strategii wykrywania zmian.

---

## Instalacja

Wymagany Python 3.11+. Projekt nie ma obowiązkowych zewnętrznych zależności — SQLite jest wbudowany w Pythona. Matplotlib jest potrzebny tylko do generowania wykresów.

```bash
pip install matplotlib
```

Aby zainstalować projekt jako paczkę (opcjonalnie, umożliwia polecenie `reload-benchmark` w terminalu):

```bash
pip install -e ".[plots]"
```

---

## Jak przebiega analiza

Analiza składa się z trzech niezależnych etapów:

### 1. Benchmark główny

Uruchamia siedem scenariuszy dla każdej kombinacji rozmiaru zbioru, wariantu tekstu i rozmiaru partii:

| Scenariusz | Co testuje |
| --- | --- |
| Pełne przeładowanie | Baseline — usuń wszystko i wgraj od nowa |
| Inkrementalny: bez zmian | Dane się nie zmieniły — czy inkrementalny pomija zapis? |
| Inkrementalny: tylko nowe | Pojawiły się nowe rekordy (domyślnie 10%) |
| Inkrementalny: nowe i zmienione | Nowe + zmienione rekordy (po 10%) |
| Inkrementalny: duże zmiany | 50% rekordów zmodyfikowanych |
| Inkrementalny: tylko dopisywanie | Nie sprawdza istniejących, tylko dodaje nowe — szybsze |
| Inkrementalny: z usuwaniem | Wykrywa i usuwa rekordy usunięte ze źródła |

Każdy scenariusz jest powtarzany `n_runs` razy. Wyniki trafiają do `results/benchmark_results.csv`.

### 2. Analiza progu opłacalności

Mierzy stosunek `czas_inkrementalny / czas_full_reload` dla wielu wartości odsetka zmienionych rekordów (od 0% do 100%). Szuka punktu, w którym inkrementalny przestaje być opłacalny. Analizę wykonuje dla każdego wariantu tekstu osobno, żeby zobaczyć jak rozmiar dokumentu wpływa na ten próg.

Wyniki: `results/threshold_results.csv`.

### 3. Porównanie metod detekcji

Porównuje trzy sposoby wykrywania zmian:

- `hash_or_timestamp` — zmiana wykryta jeśli hash SHA-256 **lub** timestamp się różni
- `hash_only` — porównuje wyłącznie sumy kontrolne treści dokumentów
- `timestamp_only` — porównuje wyłącznie datę modyfikacji, pomija haszowanie

Wyniki: `results/detection_results.csv`.

### 4. Porównanie bazy in-memory vs na systemie plikowym

Porównuje algorytmy:
- full-reload na bazie in-memory
- full-reload na bazie opartej o system plikowy
- inkrementalny z usuwaniem na bazie in-memory
- inkrementalny z usuwaniem na systemie plikowym

Wyniki: `results/storage_results.csv`.

---

## Uruchamianie

### Szybki sposób — skrypt `run_analysis.py`

```bash
# Szybki test (~2 minuty) — weryfikuje poprawność działania
python run_analysis.py --quick

# Pełny benchmark (~10-20 minut)
python run_analysis.py
```

Skrypt uruchamia wszystkie trzy etapy po kolei i wyświetla postęp w konsoli. Wykresy trafiają do `results/plots/`.

### Zaawansowany sposób — CLI

Każdy etap można uruchomić osobno z pełną kontrolą nad parametrami:

```bash
reload-benchmark benchmark [opcje]
reload-benchmark threshold [opcje]
reload-benchmark detection [opcje]
reload-benchmark generate-data [opcje]
```

Lub bez instalacji paczki:

```bash
python -m reload_benchmark.cli benchmark [opcje]
```

---

## Wszystkie flagi CLI

### `benchmark` — główny benchmark

| Flaga | Domyślnie | Opis |
| --- | --- | --- |
| `--sizes` | `10000 50000 100000` | Rozmiary zbiorów danych (liczba rekordów) |
| `--variants` | `short long` | Warianty tekstu: `short` (~135 B), `medium` (~1 300 B), `long` (~5 000 B), `xlarge` (~10 000 B) |
| `--batch-sizes` | `5000 10000` | Rozmiary partii do porównania (można podać kilka) |
| `--batch-size` | — | Pojedynczy rozmiar partii (alternatywa dla `--batch-sizes`) |
| `--n-runs` | `3` | Liczba powtórzeń każdego scenariusza (więcej = dokładniejsze statystyki) |
| `--new-ratio` | `0.10` | Odsetek nowych rekordów w scenariuszu „tylko nowe" |
| `--change-ratio` | `0.10` | Odsetek zmienionych rekordów w scenariuszu „nowe i zmienione" |
| `--high-change-ratio` | `0.50` | Odsetek zmian w scenariuszu „duże zmiany" |
| `--delete-ratio` | `0.05` | Odsetek rekordów usuniętych ze źródła (scenariusz z usuwaniem) |
| `--db` | `results/benchmark.sqlite` | Ścieżka do pliku bazy SQLite |
| `--csv` | `results/benchmark_results.csv` | Ścieżka do pliku CSV z wynikami |
| `--plots-dir` | `results/plots` | Katalog do zapisu wykresów |
| `--no-plots` | — | Pomiń generowanie wykresów |

Przykład:

```bash
reload-benchmark benchmark --sizes 50000 100000 --variants short long --n-runs 5
```

---

### `threshold` — próg opłacalności

| Flaga | Domyślnie | Opis |
| --- | --- | --- |
| `--size` | `50000` | Rozmiar zbioru danych |
| `--variants` | `short medium long xlarge` | Warianty tekstu do zbadania |
| `--batch-size` | `5000` | Rozmiar partii |
| `--change-ratios` | 16 punktów od 0% do 100% | Lista odsetków zmienionych rekordów do przetestowania |
| `--n-runs` | `3` | Liczba powtórzeń każdego punktu pomiarowego |
| `--db` | `results/threshold.sqlite` | Ścieżka do bazy SQLite |
| `--csv` | `results/threshold_results.csv` | Ścieżka do pliku CSV |
| `--plots-dir` | `results/plots` | Katalog wykresów |
| `--no-plots` | — | Pomiń generowanie wykresów |

Przykład:

```bash
reload-benchmark threshold --size 100000 --variants short long --change-ratios 0 0.1 0.2 0.5 1.0
```

---

### `detection` — porównanie metod detekcji

| Flaga | Domyślnie | Opis |
| --- | --- | --- |
| `--size` | `50000` | Rozmiar zbioru danych |
| `--variant` | `long` | Wariant tekstu (długi tekst najlepiej uwypukla koszt haszowania) |
| `--batch-size` | `5000` | Rozmiar partii |
| `--change-ratio` | `0.10` | Odsetek zmienionych rekordów |
| `--detection-methods` | `hash_or_timestamp hash_only timestamp_only` | Metody detekcji do porównania |
| `--n-runs` | `5` | Liczba powtórzeń |
| `--db` | `results/detection.sqlite` | Ścieżka do bazy SQLite |
| `--csv` | `results/detection_results.csv` | Ścieżka do pliku CSV |
| `--plots-dir` | `results/plots` | Katalog wykresów |
| `--no-plots` | — | Pomiń generowanie wykresów |

Przykład:

```bash
reload-benchmark detection --size 100000 --variant xlarge --n-runs 10
```

---

### `generate-data` — zapis dokumentów na dysk

Generuje przykładowe dokumenty tekstowe do folderu na dysku (do inspekcji lub debugowania). Benchmark nie wymaga tego kroku — dane generuje w pamięci.

| Flaga | Domyślnie | Opis |
| --- | --- | --- |
| `--output-dir` | `data/generated` | Katalog docelowy |
| `--sizes` | `1000` | Rozmiary zbiorów do wygenerowania |
| `--variants` | `short long` | Warianty tekstu |

---

### `generate_plots.py` — regeneracja wykresów z CSV

Jeśli masz już pliki CSV z wynikami, możesz wygenerować wykresy bez ponownego uruchamiania benchmarku:

```bash
python generate_plots.py
```

Skrypt czyta `results/benchmark_results.csv`, `results/threshold_results.csv` i `results/detection_results.csv`. Pomija brakujące pliki.

---

## Struktura projektu

```text
├── run_analysis.py          # główny skrypt uruchamiający całą analizę
├── generate_plots.py        # regeneracja wykresów z istniejących CSV
├── src/
│   └── reload_benchmark/
│       ├── benchmark.py     # logika trzech etapów analizy
│       ├── loaders.py       # implementacje strategii ładowania
│       ├── data_generator.py# generowanie danych testowych w pamięci
│       ├── database.py      # połączenie SQLite, schemat, operacje
│       ├── plotting.py      # wszystkie wykresy (matplotlib)
│       └── cli.py           # interfejs wiersza poleceń
├── tests/
│   └── test_loaders.py      # testy jednostkowe
├── results/
│   ├── benchmark_results.csv
│   ├── threshold_results.csv
│   ├── detection_results.csv
│   └── plots/               # wygenerowane wykresy PNG
└── PLOTS.md                 # opis wszystkich wykresów
```

## Wyniki

Wykresy PNG generowane są do `results/plots/`. Ich opis — co każdy pokazuje i jak go interpretować — znajdziesz w [PLOTS.md](PLOTS.md).
