# Spec Delta

## Purpose

Diese Fähigkeit ermöglicht explizite historische Forschung auf heute verfügbaren unveränderlichen Daten, ohne historische Point-in-Time-Verfügbarkeit zu behaupten oder die strengen Bestandsregeln abzuschwächen.

## ADDED Requirements

### Requirement: REQ-RRB-001 Explizite getrennte Modi
Das System SHALL `STRICT_PIT` und `RETROSPECTIVE_EXPLORATORY` unterscheiden. Fehlende Modusauswahl MUST Strict PIT erhalten. Strict-PIT-Datenselektion, Fehlercodes und historische Belegregeln MUST unverändert bleiben; automatischer Moduswechsel MUST ausgeschlossen sein.

#### Scenario: T-RRB-001 Heutiger Backfill
WHEN dieselben alten Bars heute ingestiert wurden THEN bleibt der ausgelassene oder explizite Strict-Modus wie zuvor blockiert; nur ein neuer ausdrücklich retrospektiver Auftrag kann unter dessen Qualitätsregeln laufen; alle tatsächlichen Ingestionzeiten bleiben identisch.

#### Scenario: T-RRB-002 Unbekannter Modus und Fallback
WHEN ein Modus unbekannt ist oder ein Strict-Lauf blockiert THEN wird der ungültige Request vor Jobanlage abgewiesen beziehungsweise derselbe Lauf blockiert; es entsteht kein retrospektiver Folgeauftrag.

### Requirement: REQ-RRB-002 Eingefrorene revisionsgebundene Datensicht
Ein retrospektiver Auftrag MUST vor Auswertung einen begrenzten unveränderlichen Snapshot mit konkreten Bar-, Kalender-, Qualitäts- und Evidenzrevisionen binden. Nur innerhalb dieses Snapshots vollständig sichtbare Revisionen SHALL ausgewählt werden. Korrekturen MUST neue Datensatzversionen erzeugen.

#### Scenario: T-RRB-003 Parallelcommit und spätere Korrektur
WHEN ein Writer zwischen zwei Datasetseiten oder nach Manifestabschluss korrigierte Bars oder Kalender publiziert THEN bleiben Auswahl und Wiederholung des alten Manifests unverändert; ein neuer Snapshot darf neue Revisionen wählen und erhält einen anderen Manifesthash. Ein nach Snapshotabschluss commitender Writer bleibt ausgeschlossen, auch wenn sein Zeitstempel früher ist.

#### Scenario: T-RRB-004 Mehrdeutigkeit und Budget
WHEN Revisionsordnung widersprüchlich ist, Referenzen fehlen, Snapshotgrenzen verletzt werden oder das vorhandene Daten-/Zeit-/Bytebudget erschöpft wird THEN endet die Auswertung begrenzt ohne Erfolgsveröffentlichung und ohne aktuelle Daten nachzuladen.

### Requirement: REQ-RRB-003 Chronologische Signale und getrennte Ergebnisse
Retrospektive Entscheidungen MUST ausschließlich abgeschlossene Marktpreisereignisse bis zum simulierten Wissenszeitpunkt verwenden. Tatsächliche Beobachtung und Datensatzerstellung MUST getrennt erhalten bleiben. Signalverzögerung, nächste zulässige Fillkerze und Foldregeln SHALL dem Bestandsmodell entsprechen.

#### Scenario: T-RRB-005 Signal und Fill
WHEN ein Signal 60 Sekunden nach Signalbarende vorliegt THEN darf nur eine danach beginnende reguläre Kerze füllen; die schon begonnene Kerze und spätere Fill-/Bewertungspreise bleiben für das Signal unlesbar. Historische +300s-Ergebnisbeobachtung ist retrospektiv keine behauptete Empfangszeit.

#### Scenario: T-RRB-006 Zukunftsperturbation
WHEN in einer zweiten Fixture ausschließlich Bars mit Marktende nach T verändert werden THEN bleiben Signalinputhash und Entscheidung bei T gleich; die Equity danach darf sich ändern. Eine Korrektur einer älteren Bar innerhalb eines neuen Snapshots darf frühere Entscheidungen ändern und wird ausdrücklich als Ex-post-Revisionsrisiko ausgewiesen.

### Requirement: REQ-RRB-004 Vollständiges versioniertes Experimentmanifest
Manifeste MUST Modus-/Policyversion, Strategie/Parameter, Instrument/Quelle/Feed/Adjustierung, Folds/Warm-up, Snapshot/Revisionen, Kalender, Qualitätsannahmen, Kosten/Slippage/Fillmodell, Simulator/Metriken und Buildfingerprint binden. Geld und Mengen MUST Decimal, technische Zeiten UTC bleiben.

#### Scenario: T-RRB-007 Determinismus und Identität
WHEN zwei Läufe dasselbe vollständige Manifest auswerten THEN stimmen fachlicher Hash, Cashverlauf, Fills, Kennzahlen und Benchmarkgrundlage überein; Lauf-ID, Bearbeitungszeit und Workerlease beeinflussen den fachlichen Hash nicht. Ein Modus-/Policywechsel ändert Experimentidentität und Idempotenznutzlast.

#### Scenario: T-RRB-008 Legacyidentität
WHEN alte Strict-Manifeste und bestehende Requests wiederholt werden THEN bleiben ihre bisherigen Hashes und Idempotenzbindungen gültig; neu eingeführte Modusfelder werden nicht rückwirkend in alte Hashpräimages aufgenommen.

### Requirement: REQ-RRB-005 Dauerhafte begrenzte Veröffentlichung
Retrospektive Jobs SHALL die bestehenden Zustände, Idempotenz, Leases, Fences und Ressourcenbudgets erhalten. Snapshot und Vorabprüfung MUST vor Simulation dauerhaft gebunden werden; Ergebnis, Manifest und vollständiger Reliability Report MUST atomar veröffentlicht werden.

#### Scenario: T-RRB-009 Crash und Vorschau
WHEN ein Worker nach Snapshotbindung abstürzt THEN verwendet sein Nachfolger denselben Snapshot; ein alter Fence kann nichts publizieren. Eine erfolgreiche Vorschau startet keinen Lauf und ersetzt keine erneute Prüfung oder ausdrückliche Erstellung mit derselben Snapshotbindung.

### Requirement: REQ-RRB-006 Typisierte authentifizierte Oberfläche
API und Dashboard SHALL Modus, Experimentversion und Snapshot eindeutig zeigen. Retrospektive Detail-/Ergebnisseiten MUST explorative Einschränkungen und den Reliability Report sichtbar verknüpfen. Authentifizierung, Origin, Cursorbindung und Reload MUST erhalten bleiben.

#### Scenario: T-RRB-010 Vollständige Kennzeichnung
WHEN ein Nutzer retrospektiv erstellt und neu lädt THEN erscheinen derselbe dauerhafte Job, Modus und Snapshot in Liste, Detail, Benchmarks, Equity und Fills; jede Ergebnisseitenantwort identifiziert den explorativen Modus. Erfolgskennzahlen machen ihn niemals Strict-PIT-zertifiziert.

#### Scenario: T-RRB-011 Zugang und Eingabegrenzen
WHEN Sitzung/Origin ungültig, Cursor fremd, Snapshot manipuliert oder Felder/Versionen unbekannt sind THEN entstehen keine Jobs; unbekannte Programme, dynamische Imports und andere Instrumente werden abgewiesen. Bisherige Requests bleiben gültig.

### Requirement: REQ-RRB-007 Sichere Forschungsgrenze
Backtesting MUST ohne Netzwerk-, Broker-, Jev-/LLM-, Credential- oder Finanzschreibrechte bleiben. Es MUST keine Strategie-Promotion, Risklimitänderung oder Betriebsaktivierung auslösen. LIVE MUST technisch unerreichbar und Handelskontrollen unter Researchlast bedienbar bleiben.

#### Scenario: T-RRB-012 Tatsächliche Isolation unter Last
WHEN echte PostgreSQL-, HTTP- und Browserketten beide Modi samt Restart, Fehlern und Maximallast ausführen THEN bleiben Broker-/Provider-/Jev-Aufrufzähler null, private Mounts unlesbar und nichtleere Handelsledger, Riskgates und Aktivierung unverändert; Pause/Kill/Reconciliation erfüllen die bestehenden Kontrollfristen.
