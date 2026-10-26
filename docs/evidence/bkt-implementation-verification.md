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

## BKT-05

Vier zusätzliche Evaluation-/Kennzahlprüfungen bestanden. Folds starten mit eigenem Cash und flacher Position; feste Strategieparameter und identische Kosten für flat/buy-hold. Handrechnungen für Nettorendite, Turnover und Drawdown einschließlich Startcash; Gebühren und Slippage getrennt. BLOCKED bewahrt abgeschlossene Foldresultate und erzeugt keine verkettete Gesamtrendite. Deterministischer Warm-up wird durch BKT-03 gebunden und durch die Domainmanifestregeln geprüft.

## BKT-06

Additive Strategierevisionen/Manifeste/Jobs/Folds/Resultate und append-only Audit in backtest, eigene Workerrolle ohne Finanzrechte, privates persistentes Cursorsignaturmaterial nur APIlesbar. Echter PG-Job-/Rollentest: Idempotenz409, Queue100, ein aktiver Job, Lease/Fenceübernahme, alte Manifest-/Checkpoint-/Publishversuche abgewiesen, Crashrollback, drei Attempts und unveränderliche Resultate. Vollständiger Workercrash vor Veröffentlichung wurde im Offline-Acceptancetest mit exakt gleichem Manifest und Resultat nach Übernahme reproduziert.

Nach Review: Providerselektion und Kalenderbindung bei nicht baraligned Start präzisiert; nun 14 Dataset-Unitfälle plus echter PG-Test bestanden (27.16s). Buildfingerprint bindet tatsächliche Quellen/Lock; Worker weist abweichende Jobversion vor Datensicht ab. Unit-/PG-Regressionen für Quellen-/Lockwechsel, Defaultdisabled und Versionsmismatch: 11 bestanden (57.91s). Separates Containerimage gebaut, ohne Netzwerk/private Mounts tatsächlichen Codefingerprint geprüft. Öffentliche Fillprojektion mit Context38 und 12MiB-Persistenzbudget: zwei zusätzliche Unitfälle bestanden. CPUarbeit hält keine SQLtransaktion; Checkpoints, feste Attempts und Rollen sind Teil der Laufzeit.
