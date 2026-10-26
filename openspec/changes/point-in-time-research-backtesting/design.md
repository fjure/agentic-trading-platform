# Design

## Context

Siehe proposal.md. Vorhanden: `packages/research/query.py` mit committed Snapshotversion und ingestgebundener Selektion, `models.py` mit FIRST_OBSERVED und Decimal-Fingerprints, Kalender/Qualität, dauerhafte Researchjobs und authentifizierte Researchrouten. `packages/decisions/replay.py` wiederholt gespeicherte Entscheidungen ohne Brokerport; es simuliert keine historische Equitykurve. Neue Fähigkeit: `research-backtesting`, unveränderte REQ-REV-/RCX-/DEC-Grenzen.

## Goals / Non-Goals

**Goals:** Kleiner deterministischer Benchmark für genau ein Instrument je Experiment; nachprüfbare zeitliche Zulässigkeit, getrennte Simulationszustände und sichtbare BLOCKED-Ergebnisse.

**Non-Goals:** ResearchQuery lockern, Paper-Planner erweitern, eine generische Pluginengine oder Optimierung bauen. Keine Aussage über Gesamtmarktperformance oder historische Überlebendenverzerrung außerhalb des festgelegten SPY/QQQ-Universums.

## Decisions

### D1: Geschlossenes Strategieregister und Manifest

`packages/backtesting/` erhält reine Domain-, Strategie-, Simulator- und Metrikmodule. Registriert werden `flat-v1`, `buy-hold-v1` und `sma-cross-v1`; SMAparameter ganzzahlig, 2 <= fast < slow <= 100, LONG wenn fast-SMA > slow-SMA, sonst FLAT. Keine automatische Anpassung oder Trainingsoptimierung; Trainingsfenster sind für versionierte Entwicklungs-/Vergleichsprotokolle, Testfenster bleiben getrennt. Freier Pythoncode und Jev wären schwieriger reproduzierbar und vergrößerten Rechteflächen, daher ausgeschlossen.

Je Experiment genau SPY oder QQQ, raw IEX, 15Min, USD, Long/Flat. Manifest enthält Strategie-/Simulator-/Metrikversion, feste Parameter, Anfangscash, Gebühren pro Fill (USD), Slippage (Basispunkte), Mengenraster, Foldgrenzen, Snapshotversion/-zeit, sortierte IDs/Fingerprints der Entscheidungs- und Filldaten, Kalender-/Qualitätsrevisionen sowie Codeversion. Daten- und Parameterversionen bleiben unveränderlich. Neue Version erzeugt neues Manifest. Hashkanonisierung nutzt bestehende Decimal-/UTC-Regeln; Lauf-UUID und monotone Laufzeit stehen außerhalb des Ergebnishashs.

### D2: Datensatz einfrieren, Wissen pro Schritt begrenzen

Zu Auftragsannahme wird ein committed Snapshot gebunden, vor Ausführung ein unveränderliches Eingabemanifest materialisiert. Entscheidung t = Signalbarende + feste 60 Sekunden Beobachtungsverzögerung (Simulatorversion v1); valuation_at = Signalbarende und knowledge_at = t. Nur abgeschlossene Bars und bis t ingestierte/verfügbare Revisionen dürfen den Input bilden. Keine pauschale Verwendung heutigen Wissens. Fenster müssen vollständig sein; fehlender Warm-up blockiert statt Lücken zu überspringen. Qualitätsbefunde müssen ebenfalls damals belegbar sein.

Ein neuer interner read-only Datasetport materialisiert alle Revisionen des begrenzten Zeitraums unter einem festen Snapshotcap mit eigenem stabilen Keyset (instrument_id, provider, feed, interval, bar_start, bar_end, adjustment, local_revision, revision_id); revision_id ist der eindeutige Tie-breaker. Er verwendet nicht die vorhandenen ResearchQuery-Cursor über wechselnde Wissensstände. Maximal 100000 Revisionszeilen inklusive Warm-up und Korrekturen; begrenzte Seiten, Checkpoints und Abbruch innerhalb des Attemptbudgets. Chronologisch sortierte Ingestionereignisse werden einmal durchlaufen; je Signal erfolgt dieselbe konservative Revisionselektion wie im bestehenden Queryoracle. Der vollständige eingefrorene Bestand ist nur Simulatorintern; Strategieinputs werden je t neu begrenzt. Vergleichstests gegen tatsächliche Einzelzeitpunktqueries beweisen Gleichheit, späte Commits und Korrekturen bleiben ausgeschlossen. Paginationtests enthalten verschiedene natürliche Identitäten mit gleichem Barende und gleicher lokaler Revision an Seitengrenzen. Das Budget ist eine harte Grenze mit FAILED/BUDGET_EXHAUSTED, keine Laufzeitgarantie für jede Maximalkonfiguration.

Kalenderpläne brauchen einen separaten beobachtungsgebundenen Scheduleport: session.opens_at/closes_at beschreiben den geplanten Geltungszeitraum; das vorhandene ingested_at <= t und Snapshotcap bestimmen konservativ das damalige Wissen. Kein neues oder unbelegtes observed_at wird angenommen. Ein Sitzungsplan muss nicht bereits geschlossen sein. Der bestehende `calendar_storage`-Query mit closes_at <= valuation_at wird hierfür nicht verwendet und nicht gelockert. Intraday-, Folgesitzungs-, DST-/Earlyclose- und spätere Korrekturfälle werden mit echter PGpersistenz geprüft. Fehlt damals belegter Plan, bleibt BLOCKED. BKT-03 besitzt die nötige additive Coveragebelegmigration; BKT-06 baut darauf mit Job-/Ergebnistabellen auf.

Die gegenwärtige konservative FIRST_OBSERVED-Basis bedeutet: ältere heutige Backfills erlauben meist keinen historischen Lauf. Das ist ein erwartetes BLOCKED-Ergebnis. Für Abnahme liefern synthetische versionierte Zeitreihen explizite damalige Ingestion-/Kalenderbelege; Produktionsdaten werden niemals zurückdatiert. Ein gelockerter retrospektiver Modus wäre eine eigene spätere Entscheidung.

Entscheidungsdaten und spätere Ausführungs-/Bewertungsbars sind getrennte Sichten. Nur der Simulator darf spätere Ergebnisbars lesen. Für Ergebnisbar b gilt der versionierte Beobachtungscutoff b.end + 300 Sekunden: gewählt wird die letzte bis dahin tatsächlich ingestierte/verfügbare gültige Revision unter dem Snapshotcap. Fehlt diese, lautet der Lauf BLOCKED. Jede Ergebnisrevision und ihr Cutoff stehen im Manifest und Bericht; spätere Korrekturen dürfen keine idealisierten Preise ersetzen. Diese ex-post Preisproxyregel ist kein damals am Open beobachteter Preis und gelangt niemals in Signalinputs. Tests prüfen normale Nachschlussingestion sowie verspätete Korrekturen getrennt von Look-ahead.

### D3: Absichtlich einfaches Fillmodell

Signal entsteht 60 Sekunden nach Ende einer regulären 15Min-Kerze nach D2. Ausführung zum Open der ersten laut damals belegtem Kalender regulären Kerze mit bar_start >= t_signal; ein bereits begonnenes Intervall wird nicht rückwirkend gefüllt. Übernachtung ist erlaubt, verlängerte Handelszeiten sind ausgeschlossen. Kaufpreis = open * (1 + slippage_bps/10000), Verkaufpreis = open * (1 - slippage_bps/10000). Kosten je Fill werden ausdrücklich konfiguriert und sind kein behauptetes Alpaca-Gebührenmodell. Keine Teilfills, Limitorders, Liquiditätssimulation oder Marktimpactmodell; IEX-Open ist ein Simulationsproxy.

LONG hält maximal die vollständig aus Cash finanzierbare Menge, kaufseitig floor((cash - fee)/price/raster)*raster; nichtpositive verfügbare Beträge erzeugen Nullmenge. Bei bereits LONG wird nicht nachgekauft. FLAT verkauft die gehaltene Menge; Verkaufserlöse müssen Gebühren decken, sonst BLOCKED. Decimal precision=38 und ROUND_HALF_EVEN für festgelegte Geld-/Kennzahlskalen (USD 8, Renditen 12 Stellen); Rundungsreste bleiben Cash, Mengen ausschließlich am Manifestraster. Kein negativer Cash oder Bestand. Nach jeder Kerze Bewertung zu deren zulässigem Close. Am Testende keine Zwangsliquidation; offener Bestand wird markiert und ausgewiesen, letztes nicht mehr ausführbares Signal verfällt.

Nur Zeiträume mit belegter Kapitalmaßnahmenabdeckung und ohne Kapitalmaßnahme im Fenster sind zugelassen. Raw-Bars allein belegen diese Abwesenheit nicht. Erstes Release verwendet explizite immutable Coveragebelege für begrenzte offline zertifizierte Datensätze; fehlt ein echter Beleg, lautet der echte Lauf BLOCKED_CORPORATE_ACTIONS_UNKNOWN. Keine erfundene Spliterkennung allein aus Kursbewegungen; kein stiller Total-Return-Anspruch.

### D4: Chronologische Folds und Kennzahlen

Maximal 12 Folds, je Training vor Test, nichtleere disjunkte Testfenster. Warm-up wird vor Ausführung deterministisch aus registrierter Strategie und damals belegtem Kalender gebunden: sma-cross benötigt slow-1 unmittelbar vor testStart abgeschlossene reguläre Bars, die erste Testkerze vervollständigt das erste SMAfenster; flat/buy-hold benötigen 0. Manifest hält warmupStart, warmupEnd=testStart, erwartete Baridentitäten und Regelversion fest. Ein fehlender Bar blockiert; keine Ausweitung des Fensters. warmupStart muss >= trainingStart sein, trainingEnd <= testStart. Während Warm-up entstehen keine Fills. Fixe Parameter je Manifest, keine Suche. Training darf frühere Testperioden umfassen, sobald sie zeitlich vergangen sind; Ergebnisse werden dadurch nicht als unabhängige Experimente ausgegeben. Foldstart flat mit demselben konfigurierten Startcash. Keine verkettete Gesamtrendite über zurückgesetzte Folds.

Referenzen flat und buy-hold laufen auf denselben zulässigen Daten und Kosten. Buy-hold signalisiert am ersten Testbarende und füllt frühestens im folgenden Bar wie jede andere Strategie. Nettorendite = end_equity/start_cash - 1; Drawdown = max(1 - equity/running_peak), einschließlich Startcash. Turnover = Summe absoluter Fillnotionale/start_cash; Kosten = Summe Gebühren plus preisbezogener Slippageeffekte, separat ausgewiesen. Endbestand, Bewertungsart, Anzahl Entscheidungen/Fills und vollständige Datenabdeckung gehören zum Bericht. Keine Sharpe- oder Signifikanzbehauptung mit unzureichender Stichprobe. BLOCKED/FAILED-Läufe erhalten keine Gesamtkennzahlen; vollständig beendete Folds bleiben als Teilergebnisse sichtbar.

### D5: Bestehende Laufzeit, getrennte Rechte und Budgets

Additive Tabellen in eigenem Backtestschema: Strategierevisionen, Manifeste, Kapitalmaßnahmen-Coveragebelege, Requests/Leases, Foldresultate, Equity-/Fillzeilen und append-only Audit. Worker erhält read-only Researchzugriff und Schreibrecht nur auf Backtesttabellen; kein Execution-/Ledger-/Credentialzugriff. Bestehende Jobmuster werden wiederverwendet, nicht Ingestionaufträge zweckentfremdet. Eine Backtest-Warteschlange mit eigenem Pool und höchstens einem aktiven Job; maximal 366 Kalendertage, 100000 Eingabebars inklusive Warm-up, 300s pro Attempt, 3 Attempts. Zwischen Seiten/Folds Checkpoints, höherer Fence invalidiert alte Publikation. Erschöpfte Laufzeit/Versuche ergeben FAILED/BUDGET_EXHAUSTED; fachlich fehlende Daten BLOCKED. Ergebnisveröffentlichung ist atomar und pro Lauf eindeutig. Ergebnisse bleiben nach Recovery lesbar. Read-/Write-/Auditbudgets und Lasttest verhindern Handelsbeeinträchtigung. Defaultdispatch bleibt bis zur bewussten lokalen Researchaktivierung deaktiviert; kein Handelsstart.

### D6: Additiver Vertragsentwurf und UI

Geplante Operationen unter `/api/v1/research/backtests` (bestehender kanonischer APIpräfix, Ausgestaltung in BKT-01): GET `/strategies`, POST `/runs`, GET `/runs`, GET `/runs/{runId}`, GET `/runs/{runId}/results`. Katalog besitzt feste Strategieversionen/Parametergrenzen; POST verlangt Idempotency-Key und strikte Felder instrumentId, strategyId/version, parameters, startCash, feeUsd, slippageBps, quantityStep und folds mit trainingStart/trainingEnd/testStart/testEnd. Server bindet Code-/Daten-/Kalender-/Qualitätsversionen; vom Nutzer behauptete unverifizierte Manifestreferenzen werden abgewiesen. Zeiträume halb offen [start,end), UTC; Dezimalwerte als kanonische Strings. Positive cash <= 1000000000, fee 0..1000, slippage 0..100, quantityStep fest 0.000001. Kein arbitrary field oder URLinput.

POST liefert 202 und dieselbe runId bei identischer Wiederholung; Konflikt 409. Warm-up-Grenzen werden serverseitig nach D4 gebunden und im Manifest/Detail ausgegeben; sie sind keine frei erweiterbaren POSTfelder. Reads 200; ungültig 400, Sitzung 401, Origin 403, unbekannte ID 404, vorübergehend nicht verfügbar 503. Problemcodes typisiert; BLOCKED ist fachlicher Laufzustand und kein HTTPfehler. Liste/Resultatseiten limit 1..200, Default50, signierte an Filter und Snapshot gebundene opake Cursor. Resultattypen summary, equity, fills; solange nicht vollständig veröffentlicht nur Status/abgeschlossene Foldreferenzen, keine scheinbaren Gesamtresultate. Alle öffentlichen Antworten whitelistbasiert und ohne SQL-/Stack-/Providerdetails. Bestehende Auth/Origin-/Sessiongrenzen und Contractprüfungen bleiben verbindlich.

Researchoberfläche: Strategie, Parameter, Fenster, Startcash und Kosten; Auftrag nur bei gültigem Formular. Persistente Laufübersicht und Detail mit Manifest, Foldresultaten, Equitykurve und Rohzahlen, Referenzen und Blockiergründen. Explizite Anzeige „Lokale Simulation“, begrenztes Universum, raw IEX und ausgeschlossene Dividenden/Kapitalmaßnahmen. Keine Schaltfläche zur Paperübernahme.

## Risks / Trade-offs

- Strenge PIT-/Kalender-/Kapitalmaßnahmengrenzen können echte historische Läufe zunächst vollständig blockieren → offen sichtbar; Offlineabnahme und Produktnutzbarkeit ausdrücklich unterscheiden.
- IEX deckt nicht den Gesamtmarkt, Open-Fills bilden keine reale Ausführung ab → Feed-/Kosten-/Fillannahmen überall referenzieren, keine Brokerqualität behaupten.
- Neue CPU-/SQLlast → separate Queue/Pools, feste Budgets und tatsächlicher Pause-/Kill-/Reconciliationtest unter Last.
- Kleine Benchmarkstrategien belegen keine Profitabilität → keine automatische Optimierung oder Promotion; Folds und Annahmen vor Lauf fixieren.

## Migration Plan

Nach gesondertem Apply erst Contractpaket und strikte Fixtures, dann reine Domain/Simulation und additive Migrationen, danach Worker/API/UI. Versionsnummern erst gegen dann aktuellen Migrationshead vergeben. Keine bestehende Finanzzeile verändern. Deployment zunächst deaktiviert; Offlineabnahme in temporärem PostgreSQL mit synthetischen Daten, anschließend bewusste lokale Researchaktivierung. Rollback deaktiviert Dispatcher und entfernt UIentrypoint; additive Tabellen und Evidenz bleiben erhalten, keine destruktive Downgrade-/Volumeoperation.

## Readiness

Planungsannahmen: enges Eininstrumentuniversum, strikt beobachtungsgebundener Modus und ausschließlich feste Benchmarkstrategien. Keine Produktentscheidung benötigt neue externe Dienste. Vor Implementierung: unabhängiger Spec-/Safetyreview, eindeutige Requirement-/Testzuordnung, verifizierter Issue-Sync und gesonderter Apply-Auftrag. BKT-01 ist contract-first: kanonischer OAS-/Schema-/Cliententwurf wird als erstes eigenes Implementierungspaket abgenommen, bevor API/UIlogik beginnt. Persönliche Providerabnahme ist Voraussetzung echter Datenaufnahme, kein durch Agenten zu schließendes Offlinegate.
