# Sortierverfahren

Sortierverfahren ordnen eine Menge von Elementen nach einer Ordnungsrelation. Welche Auswahl sinnvoll ist, hängt von Datenmenge, Vorordnung und Speicherbedarf ab (vgl. Cormen 2022, S. 5). Die Sprache *Python* nutzt standardmäßig **Timsort** (vgl. Python Software Foundation 2024).

## Grundlagen

Die wichtigsten Begriffe sind *stabil* und *in-place*. Ein stabiles Verfahren erhält die Reihenfolge gleicher Elemente (Knuth 1998, S. 12). Die Abkürzung Künstliche Intelligenz (KI) spielt hier eine Rolle, und KI auch ein zweites Mal.

> **Info: Stabilität**
>
> Ein Verfahren heißt **stabil**, wenn Elemente mit gleichem Schlüssel ihre relative Reihenfolge behalten. Das ist wichtig bei mehrstufigen Sortierungen.

> **Merke: Satz von Pythagoras**
>
> In einem rechtwinkligen Dreieck gilt: $a^{2} + b^{2} = c^{2}$.

## Verfahren im Vergleich

| Algorithmus | Komplexität | Bemerkung |
| --- | --- | --- |
| Bubble Sort | $O\left( n^{2} \right)$ | Einfach, aber langsam |
| Merge Sort | $O\left( n\text{ log }n \right)$ | Stabil, zusätzlicher Speicher |
| Quicksort | $O\left( n\text{ log }n \right)$ | In-place, nicht stabil |

**Wichtige Fachbegriffe**

| Begriff | Bedeutung |
| --- | --- |
| stabil | gleiche Schlüssel behalten Reihenfolge |
| in-place | kein nennenswerter Zusatzspeicher |

**Vorteile**

- Schnelle Average-Case-Laufzeit
- Kein Zusatzspeicher

**Nachteile**

- Worst Case $O\left( n^{2} \right)$
- Rekursion tief bei ungünstigen Pivots

## Ablauf am Beispiel Quicksort

1. Pivot-Element wählen
2. Menge in kleiner und größer partitionieren
3. Teilmengen rekursiv sortieren

> **Beispiel: Quicksort auf \[3, 1, 4, 1, 5\]**
>
> Pivot = 3, links: \[1, 1\], rechts: \[4, 5\] — danach werden beide Hälften rekursiv sortiert.

> **Aufgabe: Laufzeitanalyse**
>
> Bestimmen Sie die Zeitkomplexität von Bubblesort im schlechtesten Fall.

> **Achtung: Typischer Fehler**
>
> Der Worst Case von Quicksort tritt bei bereits sortierten Daten auf, wenn stets das erste Element Pivot ist.

## Zusammenfassung

### Was merken?

Stabil: Merge Sort, Timsort

In-place: Quicksort, Heapsort
