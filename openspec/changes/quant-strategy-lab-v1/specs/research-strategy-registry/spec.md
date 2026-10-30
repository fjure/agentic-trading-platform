# Spec Delta

## Purpose

Diese Fähigkeit beschreibt geprüfte lokale Strategieversionen mit deterministischen Signalen, dokumentierten Datenanforderungen und reproduzierbarer Initialisierung.

## ADDED Requirements

### Requirement: REQ-QSR-001 Unveränderliches Register
Das Register SHALL ID, Version, Name, Beschreibung, Kategorie, Instrumente/Timeframes, Inputtypen, Parametergrenzen, Warm-up, Implementierungsfingerprint, Simulationsmodi und Einschränkungen liefern. Verwendete Versionen MUST unveränderlich und ohne dynamische Imports auflösbar bleiben; Altmanifeste und alte Hashpräimages MUST kompatibel bleiben.

#### Scenario: T-QSR-001 Versionsänderung
WHEN fachliche Implementierung oder Initialisierung geändert wird THEN benötigt sie eine neue Version; ein abweichender Fingerprint unter bestehender Version wird vor Ausführung abgewiesen. Ein altes Manifest löst seine ursprüngliche registrierte Komponente auf oder endet explizit VERSION_UNAVAILABLE, niemals auf latest.

#### Scenario: T-QSR-002 Geschlossene Eingabe
WHEN freie Programme, Uploads, unbekannte Parameter/Versionen, boolesche Perioden, nicht endliche Decimalwerte, fremde Märkte oder SHORT angefragt werden THEN scheitert die Validierung ohne Jobanlage und externe Aktion. Bestehende flat/buy-hold/SMA-Aufträge behalten ihr Verhalten.

### Requirement: REQ-QSR-002 Deterministische rekursive Indikatoren
EMA und Wilder-RSI SHALL die Initialisierung, Decimalarithmetik, Signalgleichheit und Warm-up-Regeln aus design.md D2 exakt erfüllen. Jede Phase MUST ihren eigenen gebundenen Initialisierungsbereich besitzen; nur historisch zulässige abgeschlossene Closes dürfen den Signalzustand verändern. Fehlende Pflichtbars MUST blockieren.

#### Scenario: T-QSR-003 EMA-Referenz und Zukunft
WHEN EMA(2,3) auf Closes 10,11,12 und danach 13 fortgeschrieben wird THEN sind beim dritten Close EMAfast=11.5 und EMAslow=11, beim vierten EMAfast=12.5 und EMAslow=12. Spätere Bars ändern diese Werte nicht; Gleichheit erzeugt FLAT. Zwischenzeitlich begrenzte Datenfenster säen den Zustand nicht neu.

#### Scenario: T-QSR-004 RSI-Randfälle
WHEN n=2 und Closes 10,11,10,11 verwendet werden THEN ist der erste RSI=50 und der nächste RSI=75. Nur Gewinne ergeben 100, nur Verluste 0, konstante Preise 50. FLAT geht bei RSI<=entry in LONG, LONG bei RSI>=exit in FLAT, sonst bleibt der vorherige Zielzustand erhalten. Zustand startet pro Phase FLAT und Warm-up erzeugt keine Orders.

#### Scenario: T-QSR-005 Gemeinsamer Warm-up
WHEN Kandidaten unterschiedliche Historien brauchen THEN gilt derselbe maximale vorab gebundene Initialisierungsbereich für alle; gleiche Testgrenzen und gleicher Startcash bleiben erhalten. Fehlende erwartete Warm-up-Bars blockieren den ganzen Vergleich, auch wenn flat keine Historie benötigt. Unvollständige Historie verschiebt keine Testgrenze.
