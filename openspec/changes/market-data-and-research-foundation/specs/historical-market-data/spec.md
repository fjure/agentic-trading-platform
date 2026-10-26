# Spec Delta

## Purpose

Diese Fähigkeit beschafft nachvollziehbare historische Marktdaten, bewahrt ihre Versionen und macht Datenlücken sowie Providergrenzen sichtbar.

## ADDED Requirements

### Requirement: REQ-HMD-001 Eindeutige Instrumente und Werte
Das System SHALL stabile Instrumentidentitäten, Assetklasse, Handelsplatz, Währung sowie zeitlich gültige Providersymbole unterscheiden. Preise und Mengen MUST exakte Dezimalwerte und Zeitstempel UTC sein. Unbekannte oder mehrdeutige Identitäten, Einheiten und Intervalle MUST zurückgewiesen werden.

#### Scenario: T-HMD-001 Symbolwechsel
GIVEN zwei zeitlich getrennte Symbolzuordnungen desselben Instruments WHEN historische Daten abgefragt werden THEN bleibt die Instrumentidentität stabil und die zeitlich passende Providerzuordnung nachvollziehbar.

#### Scenario: T-HMD-002 Mehrdeutige oder ungültige Daten
WHEN eine Symbolzuordnung überlappt, eine Währung fehlt oder ein OHLCV-Wert nicht endlich ist THEN wird der Datensatz abgewiesen und kein gültiger Researchbestand behauptet.

### Requirement: REQ-HMD-002 Echter historischer Provider
Das System SHALL mindestens einen echten historischen Provider für einen begrenzten freigegebenen Aktien-/ETF-Umfang anbieten. Zeitraum, Intervall, Feed und Adjustierung MUST explizit sein. Fehlende Berechtigung oder Datenrechte MUST als Blocker ausgewiesen werden; Fixtures MUST als Testdaten erkennbar bleiben.

#### Scenario: T-HMD-003 Mehrseitige Historie
WHEN ein zulässiger historischer Abruf mehrere Seiten liefert THEN werden alle Seiten innerhalb des Jobbudgets verarbeitet und jeder Datensatz trägt Provider, Feed, Anfragebezug und Adjustierung.

#### Scenario: T-HMD-004 Fehlende Berechtigung
WHEN der Provider eine Berechtigung verweigert THEN bleibt der Job blockiert und es gibt weder Feedwechsel, Dummy-Erfolg noch Rückfall auf erfundene Daten.

### Requirement: REQ-HMD-003 Dauerhafte idempotente Ingestion
Das System SHALL Backfill und inkrementelle Updates dauerhaft verfolgen und sicher wiederaufnehmen. Identische Wiederholungen MUST keine doppelten Daten erzeugen. Datenpersistenz und Fortschritt MUST konsistent sein; abgelaufene Jobinhaber MUST keine gültigen Fortschritte mehr schreiben können.

#### Scenario: T-HMD-005 Crash und Wiederholung
GIVEN ein Crash zwischen Seitenabruf und Fortschrittsbestätigung WHEN der Job übernommen wird THEN bleiben alle bestätigten Daten erhalten und die wiederholte Seite erzeugt keine Duplikate.

#### Scenario: T-HMD-006 Korrektur und konkurrierende Inhaber
WHEN ein alter Jobinhaber verspätet schreibt oder ein neuer Providerwert denselben Datenpunkt korrigiert THEN wird der alte Inhaber abgewiesen und die Korrektur als neue nachvollziehbare Revision gespeichert.

#### Scenario: T-HMD-013 Explizite idempotente Jobwiederaufnahme
GIVEN ein Job steht dauerhaft auf PARTIAL oder BLOCKED WHEN ein authentifizierter lokaler Betreiber ihn mit neuer Idempotenzkennung ausdrücklich wiederaufnimmt THEN wird derselbe Job mit erhaltenem Daten-/Cursorfortschritt und frischem Laufbudget fortgesetzt; identische Wiederholung liefert denselben Job, andere Schlüssel für denselben Zustand starten keine parallelen Jobs, und ungültige Zielzustände werden abgewiesen.

#### Scenario: T-HMD-014 Gemeinsamer Operationsnamensraum für Idempotenz
WHEN derselbe lokale Idempotenzschlüssel bereits für einen Erstellungs- oder Wiederaufnahmevorgang verwendet wurde THEN wird ein Vorgang der jeweils anderen Art mit `IDEMPOTENCY_CONFLICT` abgewiesen und erzeugt keinen zweiten Auftrag.

### Requirement: REQ-HMD-004 Begrenztes Fehlerverhalten
Das System MUST Timeouts, Paginationzyklen, Antwortgrößen und Retrybudgets begrenzen. Rate Limits und temporäre Fehler SHALL mit begrenzter Wartezeit behandelt werden. Dauerhafte Fehler MUST ohne unendliche Wiederholung und ohne Preisgabe privater Transportdaten sichtbar sein.

#### Scenario: T-HMD-007 Rate Limit und Erschöpfung
WHEN Rate Limits oder temporäre Providerfehler auftreten THEN wird die nächste zulässige Wiederholung dauerhaft eingeplant und bei ausgeschöpftem Budget ein redigierter Fehlerzustand sichtbar.

#### Scenario: T-HMD-008 Defekte Pagination
WHEN ein Provider ein wiederholtes Seitentoken oder eine zu große Antwort liefert THEN endet der Abruf begrenzt ohne vollständige Coverage zu behaupten.

### Requirement: REQ-HMD-005 Qualität und Kalendercoverage
Das System SHALL OHLC-Plausibilität, Reihenfolge, Duplikate, fehlende Bereiche und Kalenderabdeckung prüfen. Jede Coverageaussage MUST Instrument, Feed, Intervall, Zeitraum, Qualitätsregel und Kalenderstand nennen. Unbekannte Kalenderbereiche und handelsfreie Intervalle MUST von bestätigten Datenlücken unterscheidbar bleiben.

#### Scenario: T-HMD-009 Kalendergrenze und Feiertag
WHEN ein Zeitraum Feiertage, verkürzte Sitzungen oder unbekannte Kalenderdaten enthält THEN werden nur nachweisbar erwartete Bars als fehlend markiert und unbekannte Bereiche als ungeprüft ausgewiesen.

#### Scenario: T-HMD-010 Unplausible Kerze
WHEN High unter Low liegt oder widersprüchliche identische Providerrevisionen eintreffen THEN bleiben diese Daten als Qualitätskonflikt sichtbar und von gültigen Researchresultaten ausgeschlossen.

### Requirement: REQ-HMD-006 Getrennte sichere Speicherung
Researchdaten und Ingestionstatus MUST dauerhaft getrennt von Handelsledger und frischer Handelsevidenz gespeichert werden. Research MUST keine Brokerorders, Risikoänderungen oder Betriebsaktivierung auslösen können. Nur Execution SHALL Alpaca-Credentials konsumieren; Jobs, APIs und Logs MUST credentialfrei bleiben.

#### Scenario: T-HMD-011 Isolierter Backfill
WHEN ein Backfill verarbeitet wird THEN bleiben Orders, Risikokonfiguration, Aktivierungszustand und Fake-/Demo-/Paperledger unverändert und historische Bars gelten nicht als frische Tradefreigabe.

#### Scenario: T-HMD-012 Rechte und Canaries
WHEN Research, Worker oder Control auf Brokercredentials oder Handelsschreibrechte zugreifen wollen THEN wird der Zugriff verweigert und private Canarywerte erscheinen in keiner öffentlichen Ausgabe.
