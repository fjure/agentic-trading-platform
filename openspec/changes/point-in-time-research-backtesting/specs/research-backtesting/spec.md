# Spec Delta

## Purpose

Diese Fähigkeit prüft begrenzte Strategien lokal und reproduzierbar mit nachweisbar verfügbaren historischen Daten und macht Simulationsannahmen, Datenlücken und Ergebnisgrenzen sichtbar.

## ADDED Requirements

### Requirement: REQ-BKT-001 Unveränderliche Experimentgrundlage
Ein Experiment SHALL Strategieversion, Parameter, Instrument, Zeitraum, Startcash, Kosten-/Fillmodell, Kalender-, Qualitäts-, Code- und Datenversionen mit Fingerprint unveränderlich binden. Zulässig MUST ausschließlich registrierte deterministische Long/Flat-Strategien für SPY oder QQQ, raw IEX und 15Min ohne Leverage sein; freie Programme und externe Modellaufrufe MUST abgewiesen werden.

#### Scenario: T-BKT-001 Reproduzierbarer Lauf
WHEN derselbe gebundene Datensatz mit demselben Manifest wiederholt ausgewertet wird THEN stimmen Entscheidungen, simulierte Fills, Cashverlauf und fachlicher Ergebnishash überein; Lauf-ID und Laufzeitmessung gehören nicht zum fachlichen Hash.

#### Scenario: T-BKT-002 Unzulässige Strategie
WHEN ein Auftrag freie Programme, SHORT, andere Märkte, Leverage oder nicht registrierte Versionen enthält THEN wird er vor Jobanlage ohne Netzwerk- oder Handelsaktion abgewiesen.

### Requirement: REQ-BKT-002 Fortschreitender belegter Wissensstand
Jede Entscheidung MUST nur abgeschlossene gültige Evidenz innerhalb ihres historischen Bewertungs- und Wissenszeitpunkts und des gebundenen Snapshots verwenden. Spätere Ingestion oder Korrekturen MUST ausgeschlossen bleiben. Fehlende Warm-up-, Kalender- oder Pflichtdaten MUST den Lauf mit genauer Ursache blockieren; heutige Backfills MUST keine historische Verfügbarkeit begründen.

#### Scenario: T-BKT-003 Backfill und Korrektur
WHEN eine alte Kerze erst nach dem Entscheidungszeitpunkt ingestiert oder korrigiert wurde THEN bleibt diese Revision aus dem Entscheidungsinput ausgeschlossen; ohne vollständiges zulässiges Fenster entsteht BLOCKED statt einer Renditebehauptung.

#### Scenario: T-BKT-004 Zukünftige Informationen
WHEN Daten nach einem historischen Entscheidungszeitpunkt geändert werden THEN bleibt dessen Inputhash und Entscheidung unverändert; offene Bars und die spätere Fillkerze sind für die Entscheidung unlesbar. Die feste Beobachtungsverzögerung beträgt 60 Sekunden nach Signalbarende; eine schon begonnene Kerze ist kein zulässiges Fillziel.

### Requirement: REQ-BKT-003 Explizite lokale Ausführungssimulation
Die Simulation SHALL ein eigenes Decimal-Cash-/Positionsledger und versionierte Gebühren-, Slippage-, Rundungs- und Fillannahmen verwenden. Ein Signal MUST frühestens an der folgenden zulässigen Kerze wirken, nur wenn es bis zu deren Beginn vorliegt. Fehlende Filldaten, Kalenderlücken oder unbelegte Kapitalmaßnahmen MUST den Lauf blockieren. Reale Konten oder Orders MUST unberührt bleiben.

#### Scenario: T-BKT-005 Kosten und Cashgrenze
WHEN ein LONG-Signal mit nächster gültiger Kerze ausgeführt wird THEN berücksichtigt die Simulation Slippage, Gebühren und abgerundete Stückelung ohne negatives Cash oder negative Position; Nullmenge bleibt ohne Fill. Ergebnispreise sind getrennte ex-post Proxys mit Beobachtungscutoff Barende plus 300 Sekunden; spätere Korrekturen sind ausgeschlossen und das Modell wird sichtbar ausgewiesen.

#### Scenario: T-BKT-006 Fehlende nächste Kerze
WHEN eine innerhalb des Auswertungsfensters erwartete nächste Fillkerze fehlt THEN entsteht BLOCKED mit Datenreferenz statt eines Fills am Signalpreis; am regulären Fensterende verfällt ein Signal ohne weitere Kerze und die Position wird markiert, nicht zwangsliquidiert.

### Requirement: REQ-BKT-004 Vorab gebundene zeitliche Evaluation
Experimentprotokolle SHALL Trainings-, Warm-up- und disjunkte Testfenster vor Ausführung binden. Walk-forward MUST nur chronologisch vorangehende Trainingsdaten verwenden. Änderungen von Parametern oder Fenstern MUST neue Experimente erzeugen. Testdaten MUST weder Auswahl noch Anpassung von Parametern steuern; überlappende Testfenster und unzulässige Grenzen MUST abgewiesen werden.

#### Scenario: T-BKT-007 Zukunft im Training
WHEN ein Trainingsfenster in ein Testfenster reicht oder zwei Testfenster überlappen THEN wird das Protokoll vor Jobanlage abgewiesen.

#### Scenario: T-BKT-008 Warm-up und Foldgrenzen
WHEN ein vorab gebundenes Walk-forward-Protokoll läuft THEN nutzt jeder Fold nur davor verfügbare Warm-up-Daten und feste Parameter, startet mit eigenem Startcash und flacher Position und weist sein Testergebnis getrennt aus. SMA benötigt slow-1 kalendergemäß unmittelbar vor Testbeginn abgeschlossene Bars, flat/buy-hold keinen Warm-up; Grenzen und Baridentitäten werden vor Ausführung gebunden, Lücken blockieren.

### Requirement: REQ-BKT-005 Ehrliche Ergebnisdarstellung
Ergebnisse SHALL pro Testfenster Nettorendite, maximalen Drawdown, Turnover, simulierte Fills, Kosten und Equityverlauf sowie Cash- und Buy-and-hold-Referenzen unter identischen Daten-/Kostenannahmen ausweisen. Datenabdeckung und Modellgrenzen MUST sichtbar bleiben. Blockierte Läufe MUST keine vollständigen Erfolgskennzahlen zeigen; Ergebnisse MUST weder Profitabilitätsnachweis noch Handelsfreigabe behaupten.

#### Scenario: T-BKT-009 Unabhängig berechnete Kennzahlen
WHEN eine kleine Fixture mit bekannten Kursen und Kosten ausgewertet wird THEN entsprechen Kennzahlen und Referenzen unabhängig berechneten Decimalwerten; Drawdown berücksichtigt den anfänglichen Cashwert.

#### Scenario: T-BKT-010 Unvollständige Auswertung
WHEN ein späterer Fold blockiert THEN bleibt der Gesamtlauf BLOCKED; frühere vollständige Folds sind separat als Teilergebnisse sichtbar und erhalten keine zusammengefasste Gesamtrendite.

### Requirement: REQ-BKT-006 Dauerhafte begrenzte Aufträge
Aufträge SHALL idempotent mit ACCEPTED, RUNNING, SUCCEEDED, BLOCKED oder FAILED dauerhaft abfragbar sein. Nutzlastkonflikte MUST abgewiesen werden. Leases, Fencing, begrenzte Wiederaufnahme und feste Ressourcenbudgets MUST doppelte Ergebnisveröffentlichung verhindern und Pause, Kill sowie Reconciliation bei Auslastung erhalten. Nur vollständige Resultate MUST SUCCEEDED erhalten.

#### Scenario: T-BKT-011 Wiederholung und Crash
WHEN derselbe Auftrag wiederholt oder nach Workerabsturz übernommen wird THEN bleibt genau ein Auftrag mit höchstens einem veröffentlichten Ergebnis bestehen; ein alter Worker kann nach Fencewechsel nichts veröffentlichen.

#### Scenario: T-BKT-012 Ressourcenbudget
WHEN Zeit-, Daten- oder Parallelitätsgrenzen erreicht werden THEN bleibt der Auftrag nachvollziehbar begrenzt und Handelskontrollen bleiben bedienbar; Grenzfehler werden nicht als Erfolg dargestellt.

### Requirement: REQ-BKT-007 Authentifizierte Verträge und Oberfläche
Strategiekatalog, Auftragserstellung, Laufübersicht, Detail und paginierte Resultate SHALL über authentifizierte additive Verträge und die Researchoberfläche verfügbar sein. Unzulässiger Origin, fehlende Sitzung und ungültige Grenzen MUST vor Seiteneffekten abgewiesen werden. Reload MUST den dauerhaften Zustand zeigen; Fingerprints, Annahmen, Zeitfenster und Blockiergründe MUST sichtbar sein.

#### Scenario: T-BKT-013 Zugang und Wiederladen
WHEN eine Sitzung fehlt oder ein fremder Origin einen Auftrag sendet THEN entsteht kein Job; nach zulässiger Erstellung und Browserreload ist derselbe echte Lauf mit seinem dauerhaften Zustand sichtbar.

#### Scenario: T-BKT-014 Vertragstreue
WHEN Verträge und Client neu erzeugt und geprüft werden THEN bleiben vorhandene APIoperationen kompatibel; ungültige Decimal-/UTCwerte, fremde Cursor und manipulierte Manifestreferenzen werden abgewiesen.

### Requirement: REQ-BKT-008 Isolation und Audit
Backtests MUST ohne Provider-, Broker-, Jev- oder Credentialzugriff laufen und ausschließlich ihre eigene Ergebnispersistenz beschreiben. Experimente, Zustandswechsel und Ergebnisreferenzen SHALL redigiert korrelierbar sein. Paperledger, Freigaben, Risklimits und Executionrechte MUST unverändert bleiben; LIVE MUST technisch unerreichbar bleiben.

#### Scenario: T-BKT-015 Tatsächliche Isolation
WHEN ein vollständiger Backtest und ein absichtlich fehlerhafter Strategieauftrag laufen THEN erfolgen null Broker-/Provider-/Jev-Aufrufe, kein Zugriff auf private Mounts und keine Änderung der Handelsledger oder Betriebsfreigaben; der Audit enthält nur zulässige Referenzen.
