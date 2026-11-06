# Quant Strategy Lab betreiben

Das Strategy Lab nutzt den isolierten lokalen Backtest-Dispatcher. Der normale
Labstart lässt ihn deaktiviert. Lokale Research-Aktivierung und Abschaltung
erfolgen wie bei den [bestehenden Backtests](backtesting.md). API und Dispatcher
müssen denselben Build-Fingerprint melden. Die Migrationen bis zum aktuellen
Head werden vor der Dispatcher-Aktivierung eingespielt.

## Experiment und Ergebnis

1. Katalog und Strategierevision über `/api/v1/research/strategies` lesen. Modus,
   Suchraster, Zeitphasen, Kosten und Auswahlgrenzen im Draft festlegen.
2. Den Draft validieren. Ein `INVALID`-Beleg bleibt über seine Validierungs-ID
   abrufbar. Dieselbe Anfrage mit demselben Idempotenzschlüssel liefert den
   ursprünglichen Beleg. Nach einer Änderung der Datengrundlage kann eine neue
   Validierung mit neuem Schlüssel erfolgen.
3. Den Batch mit Definitionsversion und -hash, der startfähigen Validierungs-ID
   und ihrem Hash starten. Der Start bindet Snapshot und Expositionsregister.
   PIT-Eignung und Queuekapazität werden separat geprüft.
4. Batchstatus, Selektionen und Ergebnisse über die Batch-ID lesen. Die
   endgültige Wahl und alle Foldentscheidungen stehen vor dem ersten
   Out-of-Sample-Lauf fest. Bei `NO_ELIGIBLE_CANDIDATE` bleiben die
   Validationdaten und festen Referenzen abrufbar. Der Holdout bleibt separat.
5. Equity und Drawdown für denselben Kandidaten, dieselbe Rolle, dieselbe Phase
   und denselben Fold lesen. Bericht, Sensitivität und Aktivitätsverteilungen stammen aus der
   API. Paginierte Abfragen verwenden den Cursor der jeweiligen Abfrage und
   Session. Aktuelle Reliability-Warnungen können sich nach Publikation ändern.

## Diagnose und Wiederanlauf

- Bei `ADMISSION_LIMIT` den Status laufender Batches prüfen. Die gemeinsame
  Queue fasst 100 gewichtete Slots. Ein Quant-Batch belegt einen Slot pro
  Suchkandidat und zwei weitere Slots für die Referenzrollen.
- Die gemeinsame Lease erlaubt einen aktiven Auftrag. Pro Quant-Batch gelten
  höchstens drei Versuche, je 295 Sekunden. Die Veröffentlichung und der
  Fortschritt sind an den jeweils gültigen Fence gebunden. Bei einem Neustart
  werden gebundene Phasen und bereits publizierte Resultatlinks wiederverwendet.
- Die Grenzen liegen bei 100.000 Snapshotzeilen, 100.000 Seed- und
  Simulationsbarschritten und 12 MiB kumulierter Veröffentlichung. Bei
  ausgeschöpftem Budget einen kleineren, neuen Draft mit eigener Version und
  Validierung anlegen. Batches mit `BLOCKED` anhand ihrer Daten- und
  Reliability-Gründe untersuchen. Strict PIT und Retrospective Exploratory
  sind ausdrücklich gewählte Modi.
- Für Diagnose den Batchstatus, seine Phase und die Trace-ID aus der
  standardisierten Fehlerantwort verwenden. API-Zähler erfassen feste
  Operations- und Fehlercodes, Laufzeit und begrenzte Zeilenzahlen. Historische
  Berichte und die SQL-Ereignisse bleiben beim Stoppen des Dispatchers erhalten.
