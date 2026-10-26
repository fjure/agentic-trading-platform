# Lokale Researchsimulation

Umsetzung von `point-in-time-research-backtesting`, BKT-01..09. Der Implementierungsnachweis unterscheidet abgeschlossene Pakete und ausstehende Abnahme. Keine Handelsfreigabe.

## Vertrag

Fünf additive authentifizierte Operationen liegen unter `/api/v1/research/backtests`: GET `strategies`, POST/GET `runs`, GET `runs/{runId}` und GET `runs/{runId}/results`. Der bestehende APIpräfix bleibt verbindlich. POST benötigt Idempotency-Key; identische Wiederholung liefert dieselbe runId mit 202, geänderter Auftrag 409. Unbekannte Felder, unzulässige Strategien/Parameter, nichtkanonische Dezimalstrings und UTCwerte werden abgewiesen. Der Server bindet Manifest und Snapshot. Resultatarten sind summary, equity und fills mit begrenzter Pagination.

Die Schemaabnahme prüft Form und Wertebereiche. Chronologische Beziehungen, historische Zulässigkeit, Auth, Idempotenz und Cursorsignaturen benötigen zusätzliche Laufzeitprüfungen. Der generierte Angularclient wird ausschließlich mit dem gepinnten Generator erzeugt.

## Modellannahmen

Nur ein SPY-/QQQ-Instrument, raw IEX, USD, reguläre 15Min-Kerzen, Long/Flat. Feste Strategien flat-v1, buy-hold-v1 und sma-cross-v1; keine Parametersuche. SMA: 2 <= fast < slow <= 100. Je Fold eigenes Startcash und flache Position, keine verkettete Gesamtrendite.

Signalzeit ist Barende +60s. Fills erfolgen am ersten damals belegten regulären Open nach dem Signal; die schon laufende Kerze ist ausgeschlossen. Treffen mehrere Signale auf dasselbe nächste Open, gilt das zuletzt entstandene Ziel. Ergebnispreise sind getrennte ex-post Proxys mit Beobachtungscutoff Barende +300s. Spätere Korrekturen erreichen weder frühere Signale noch diese Proxys. Übernachtung ist zulässig; am Fensterende wird Bestand markiert und kein Zwangsverkauf ausgeführt.

Decimal precision 38, ROUND_HALF_EVEN, USD 8 und Renditen 12 Stellen, Mengenraster 0.000001. Kaufmenge wird abgerundet und vollständig aus Cash einschließlich konfigurierter Gebühren finanziert. Gebühren/Slippage bilden kein behauptetes Brokermodell. Raw IEX ist keine Gesamtmarkt- oder Total-Return-Reihe. Dividenden und Kapitalmaßnahmen werden nicht simuliert; fehlende explizite Abwesenheitsbelege blockieren.

## Historische Grenzen

Ein heutiger FIRST_OBSERVED-Backfill belegt keine damalige Verfügbarkeit. Fehlende damalige Kalender-, Warm-up-, Qualitäts- oder Kapitalmaßnahmenbelege ergeben BLOCKED. Synthetische offline zertifizierte Fixtures dienen der Softwareabnahme; Produktionsdaten dürfen nicht zurückdatiert werden. Parameter, Revisionen, Cutoffs, Codeversion, Kalender und Datenhashs sind im Manifest nachvollziehbar.

Dispatcherstandard ist deaktiviert. Maximal ein aktiver Job, 12 Folds, 366 Tage, 100000 Revisionszeilen, 300s pro Attempt und drei Attempts. Backtesting verwendet eine eigene Rolle, Queue und SQLpool ohne Broker-, Provider-, Jev- oder Credentialmounts. Fehlende Pflichtdaten blockieren, erschöpfte Budgets ergeben FAILED/BUDGET_EXHAUSTED.
