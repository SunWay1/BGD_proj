# Opis wykresów

Wykresy zapisywane są do folderu `results/plots/`. Większość z nich generuje się osobno dla każdego wariantu tekstu (`short`, `long`, itp.), co widać w nazwie pliku — np. `czas_wg_rozmiaru_short.png`.

---

## Wykresy głównego benchmarku

Generowane przez `run_benchmark`. Porównują siedem scenariuszy:

- **Pełne przeładowanie** — całkowite wyczyszczenie bazy i wgranie wszystkich danych od zera
- **Inkrementalny: bez zmian** — dane źródłowe nie zmieniły się, silnik sprawdza każdy rekord i nic nie zapisuje
- **Inkrementalny: tylko nowe** — 10% rekordów jest nowych, reszta bez zmian
- **Inkrementalny: nowe i zmienione** — 10% rekordów zmienionych + 10% nowych
- **Inkrementalny: duże zmiany (50%)** — połowa rekordów została zmodyfikowana
- **Inkrementalny: tylko dopisywanie** — pomija sprawdzanie istniejących, dodaje wyłącznie nowe (szybsze, ale nie wykrywa zmian)
- **Inkrementalny: z usuwaniem** — wykrywa i usuwa rekordy, które zniknęły ze źródła

---

### `czas_wg_rozmiaru_{wariant}.png`

Pokazuje jak rośnie czas ładowania w zależności od liczby rekordów w zbiorze. Każda linia to jeden scenariusz. Zacieniowany obszar wokół linii to odchylenie standardowe z kilku uruchomień — im węższy, tym bardziej stabilne wyniki.

**Jak czytać:** jeśli linia inkrementalnego rośnie wolniej niż linia pełnego przeładowania, ładowanie przyrostowe skaluje się lepiej.

---

### `skalowanie_loglog_{wariant}.png`

Ten sam wykres co powyżej, ale obie osie są logarytmiczne. Pozwala zobaczyć, czy wzrost czasu jest liniowy (prosta linia na wykresie log-log), kwadratowy, czy inny. Przydatny do oceny złożoności obliczeniowej każdego scenariusza.

---

### `czas_wg_partii_{wariant}.png`

Pokazuje jak rozmiar partii (batch size) wpływa na całkowity czas ładowania. Dane są uśredniane po wszystkich rozmiarach zbiorów.

**Jak czytać:** zbyt małe partie powodują narzut transakcyjny (wiele małych transakcji SQL), zbyt duże mogą spowalniać przez zużycie pamięci. Optymalny batch to ten, przy którym czas jest najniższy.

---

### `stosunek_wg_partii_{wariant}.png`

Pokazuje stosunek `czas inkrementalny / czas pełnego przeładowania` dla różnych rozmiarów partii. Przerywana linia pozioma na wartości `1.0x` to punkt równowagi.

**Jak czytać:** wartość poniżej 1.0x oznacza, że inkrementalny jest szybszy. Powyżej 1.0x — pełne przeładowanie jest bardziej opłacalne.

---

### `operacje_zapisu_{wariant}.png`

Wykres słupkowy — liczba operacji INSERT, UPDATE i DELETE wykonanych w bazie dla każdego scenariusza (dla największego testowanego zbioru).

**Jak czytać:** pełne przeładowanie zawsze zapisuje wszystkie rekordy. Inkrementalny bez zmian powinien mieć zero zapisów. Widać tutaj dlaczego inkrementalny bywa szybszy — po prostu mniej pisze do bazy.

---

### `przyspieszenie_{wariant}.png`

Pokazuje ile razy inkrementalny jest szybszy od pełnego przeładowania (`czas_full / czas_inkrementalny`). Wartość `2.0x` oznacza dwukrotne przyspieszenie. Zacieniowany obszar to odchylenie standardowe.

**Jak czytać:** linia powyżej `1.0x` — inkrementalny wygrywa. Poniżej — pełne przeładowanie jest szybsze lub porównywalne.

---

### `boxplot_czasy_{wariant}.png`

Wykres pudełkowy (boxplot) dla każdego scenariusza, osobno dla każdego rozmiaru zbioru. Pudełko obejmuje środkowe 50% wyników, linia w środku to mediana, wąsy to zakres, a kropki to wartości odstające.

**Jak czytać:** wąskie pudełko = stabilne, przewidywalne wyniki. Szerokie pudełko lub długie wąsy = wyniki mocno się wahają między uruchomieniami.

---

### `heatmapa_przyspieszenia_{wariant}.png`

Siatka kolorów: wiersze to rozmiary zbioru, kolumny to rozmiary partii. Kolor komórki pokazuje przyspieszenie inkrementalnego. Zielony — inkrementalny szybszy, czerwony — pełne przeładowanie szybsze. Wyświetlane są dwa scenariusze: „bez zmian" i „duże zmiany (50%)".

**Jak czytać:** pozwala jednym spojrzeniem ocenić, przy jakich kombinacjach parametrów inkrementalny daje największą korzyść.

---

### `struktura_czasu_{wariant}.png`

Skumulowany wykres słupkowy pokazujący z czego składa się czas każdego scenariusza (dla największego zbioru):

- **fioletowy** — haszowanie SHA-256 (obliczanie odcisków palca rekordów)
- **zielony** — zapis do bazy (SQL INSERT/UPDATE/DELETE)
- **żółty** — overhead (pobieranie indeksu, logika porównania, itp.)

**Jak czytać:** jeśli haszowanie zajmuje większość czasu, warto rozważyć metodę detekcji opartą tylko na timestampie.

---

### `pamiec_ram_{wariant}.png`

Szczytowe zużycie pamięci RAM (mierzone przez `tracemalloc`) dla każdego scenariusza, dla największego testowanego zbioru. Słupki błędów pokazują odchylenie standardowe między uruchomieniami.

**Jak czytać:** inkrementalny z pełnym indeksem w pamięci (słownik hash → rekord) zużywa więcej RAM niż wariant „tylko dopisywanie", który trzyma tylko zbiór ścieżek.

---

## Wykresy analizy progu opłacalności

Generowane przez `run_threshold`. Odpowiadają na pytanie: **przy jakim procencie zmienionych rekordów inkrementalny przestaje być opłacalny?**

---

### `prog_oplacalnosci_{wariant}.png`

Oś X: procent zmienionych rekordów (0–100%). Oś Y: stosunek czasu inkrementalnego do pełnego przeładowania. Przerywana linia pozioma na `1.0x` to próg opłacalności.

Zielony obszar pod krzywą — inkrementalny wygrywa. Czerwony obszar — pełne przeładowanie jest lepsze. Zacieniowany niebieski obszar wokół krzywej to przedział ±σ z powtórzeń.

**Jak czytać:** punkt przecięcia krzywej z linią `1.0x` to praktyczny próg. Przy mniejszym procencie zmian warto używać inkrementalnego; powyżej progu — lepiej przeładować wszystko.

---

### `prog_vs_rozmiar_dokumentu.png`

Przekrojowy wykres dla wszystkich wariantów tekstu. Oś X (skala logarytmiczna): średni rozmiar dokumentu w bajtach. Oś Y: przy jakim procencie zmian inkrementalny przestaje być opłacalny.

**Jak czytać:** im większy dokument, tym wyższy próg — haszowanie dużych plików kosztuje proporcjonalnie więcej, więc inkrementalny „utrzymuje przewagę" nawet przy większej liczbie zmian.

---

## Wykresy porównania metod detekcji

Generowane przez `run_detection_comparison`. Porównują trzy strategie wykrywania zmian:

- **Hash lub timestamp** — zmiana wykryta jeśli hash SHA-256 LUB timestamp się różni
- **Tylko hash (SHA-256)** — porównuje wyłącznie sumy kontrolne treści
- **Tylko timestamp** — porównuje wyłącznie datę modyfikacji (nie liczy hashy)

---

### `metody_detekcji_czas.png`

Słupki pokazują średni całkowity czas dla każdej metody detekcji (z paskami błędu). Nałożony szary słupek wewnątrz każdego baru pokazuje ile z tego czasu pochłonęło samo haszowanie.

**Jak czytać:** metoda „tylko timestamp" jest najszybsza, bo pomija obliczanie SHA-256. Kosztem jest brak detekcji zmian treści przy niezmienionej dacie modyfikacji.

---

### `metody_detekcji_haszowanie.png`

Pokazuje jaki procent całkowitego czasu stanowi haszowanie dla każdej metody. Dla metody „tylko timestamp" wynosi 0%.

**Jak czytać:** jeśli haszowanie stanowi duży procent (np. 60–80%), dokumenty są duże i przejście na metodę timestampową znacząco przyspieszy działanie.

## Porównanie in-memory vs system plików

Generowane przez dodatkowe wykresy porównawcze. Pokazują różnice między bazą działającą wyłącznie w pamięci RAM a bazą zapisywaną na dysku.

Porównywane są dwa odpowiadające sobie tryby:

- **Pełne przeładowanie (in-memory)** <-> **Pełne przeładowanie (system plików)**
- **Inkrementalny z usuwaniem (in-memory)** <-> **Inkrementalny z usuwaniem (system plików)**

Celem jest określenie kosztu operacji I/O oraz wpływu zapisu na dysk na całkowity czas przetwarzania.

---

### `in_memory_vs_filesystem_time.png`

Wykres słupkowy porównujący średni całkowity czas wykonania dla wersji działającej wyłącznie w pamięci RAM oraz wersji korzystającej z pliku bazy danych.

**Jak czytać:** różnica wysokości słupków pokazuje rzeczywisty koszt operacji dyskowych. Jeżeli słupki są zbliżone, oznacza to, że większość czasu pochłania logika aplikacyjna lub haszowanie, a nie zapis do pliku.

---

### `filesystem_overhead.png`

Pokazuje procentowy narzut wynikający z użycia systemu plików względem wariantu in-memory.

Wartość liczona jest według wzoru:

```text
(file_time / memory_time - 1) × 100%
```

**Jak czytać:** wartość 0% oznacza brak różnicy. Wartość 20% oznacza, że wersja wykorzystująca plik działa o 20% wolniej od wersji działającej wyłącznie w pamięci.

---

### `filesystem_breakdown.png`

Skumulowany wykres słupkowy przedstawiający strukturę czasu wykonania dla poszczególnych wariantów:

* **Haszowanie** — obliczanie sum kontrolnych SHA-256
* **Zapis/Odczyt** — operacje bazodanowe wykonywane podczas ładowania danych

Pokazywane są jednocześnie:

* pełne przeładowanie (RAM)
* pełne przeładowanie (plik)
* inkrementalny z usuwaniem (RAM)
* inkrementalny z usuwaniem (plik)

**Jak czytać:** jeżeli wysokość części odpowiadającej zapisowi/odczytowi znacząco rośnie dla wariantu plikowego, oznacza to, że głównym źródłem spowolnienia są operacje I/O. Jeżeli różnica występuje głównie w części haszującej, ograniczeniem staje się przetwarzanie danych, a nie zapis na dysk.

---

### (Opcjonalnie) `db_size_vs_runtime.png`

Pokazuje zależność między rozmiarem pliku bazy danych a czasem wykonania operacji.

Oś X przedstawia rozmiar bazy danych na dysku, a oś Y całkowity czas wykonania benchmarku.

**Jak czytać:** rosnący trend oznacza, że wraz ze wzrostem rozmiaru bazy zwiększa się koszt operacji dyskowych. Wykres pozwala ocenić, jak silnie wydajność zależy od wielkości przechowywanych danych i czy dalsze skalowanie będzie ograniczane przez I/O.

