# Backtesting-Implementierungsnachweis

Stand 26.10.2026, laufende Umsetzung. Noch keine vollständige Changeabnahme.

## BKT-01

Fünf additive Operationen, 48 Backtestschemas und 17 Fixtures implementiert. 173 Vertragsprüfungen (Backtest und bestehendes Research) bestanden. `validate-contracts`: 58 Operationen, 27 Schemas, 70 Antwortbeispiele; Redocly 2.62.1 bestanden. `oasdiff` 1.33.0 gegen tatsächliches main `5d65820` bestanden. OpenAPI Generator 7.26.0 erzeugte zweimal 208 bytegleiche Dateien; TypeScript 6.0.2/Angular 22.2.2 Compile bestanden. Quellreview: geschlossene DTOwurzeln, kanonische Decimal-/UTCwerte, zusätzliche Operationen ohne Änderung bestehender Routen. Konditionale ResearchMetric-Zweige enthalten explizite additionalProperties:true für den gepinnten Linter; die DTOwurzel bleibt geschlossen, Negativtest bestanden.

Dies belegt die Vertragsform. Persistenz, historische Semantik, Cursorsignatur und Browser sind gesonderte offene Tasks.

## BKT-02

Unveränderliche Experiment-/Fold-/Manifestwerte, geschlossenes Strategieregister, exakte Decimalvalidierung und kanonische Eingabehashs implementiert. 29 Modell-/Strategie-/Requestprüfungen bestanden: unbekannte Programme/Versionen, SMAgrenzen, überlappende/Zukunftsfenster, Nachnullnormalisierung und UTCpräzision. Modelle/Strategien haben keinen Broker-/Netzwerkport. Kanonische Requests werden vor Jobanlage geprüft; freie Manifestreferenzen bleiben abgewiesen. Alle Domain-/Simulator-/Evaluationprüfungen zusammen bestanden vor der Integration ebenfalls (42 plus neue Requestfälle).

## BKT-03

Acht Dataset-Unitfälle einschließlich 258-Schritt-Fensterparität bestanden. Echter PostgreSQL-Test erneut bestanden (26.98s): Snapshot bei offenem Writer, spätere Commits ausgeschlossen; 1er-Keysetseiten mit gleichem Barende/lokaler Revision und unterschiedlicher natürlicher Identität; 76 tatsächliche Einzelzeitpunktqueryvergleiche; Intradayplan, DST/Earlyclose und spätere Kalenderkorrektur; 60s-Signal vs spätere 300s-Ergebnisrevision; unveränderlicher Coveragebeleg ohne Runtime-Schreibrecht. Reader begrenzen Metadaten auf 100001 Zeilen und scheitern bei mehr als 100000, Strategieinputs bleiben auf höchstens slow Bars begrenzt. Bestehende Researchselektion unverändert. Additive Coverage-Migration0028 und exaktes Paper-ACLmanifest für0028/29 ergänzen keine Finanzrechte.

## BKT-04

Neun Simulator-/Propertyprüfungen bestanden (einschließlich 80 Hypothesisfällen). Unabhängige Kauf-/Verkaufsrechnungen bestätigen Gebühren, Slippage, Mengenraster und Cash; Nullmenge, unfinanzierbare Verkaufsgebühr, Overnight, fehlendes Fillziel und Fensterende geprüft. Jede Bar ruft einen Budgetcheckpoint auf. Festes Decimalmodell precision38/ROUND_HALF_EVEN und nächste kalenderzulässige Kerze; Ergebnisbars gelangen nicht in Strategieinputs. Keine Brokerimporte oder Brokeraktionen.
