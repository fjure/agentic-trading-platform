# Spec Delta

## Purpose

Diese Fähigkeit bewahrt überprüfbare Researchgrundlagen und ihre Verfügbarkeit, damit spätere Auswertungen keine zukünftigen Informationen einschleusen.

## ADDED Requirements

### Requirement: REQ-REV-001 Typisierte nachvollziehbare Evidenz
Researchevidenz SHALL ID, Typ, Quelle, Instrument-/Marktbezug, Ereigniszeit, belegte oder unbekannte Veröffentlichungszeit, Verfügbarkeitszeit, Ingestionzeit, normalisierten Inhalt, Qualität, Quellreferenz, Schema-/Datenrevision und reproduzierbaren Fingerprint besitzen. Optionale Modellkonfidenz MUST getrennt von Datenqualität bleiben.

#### Scenario: T-REV-001 Reproduzierbare Evidenz
WHEN semantisch identische normalisierte Quelldaten als Wiederholung des aktuellen Stands oder derselben bestätigten Seite eingehen THEN besitzen sie denselben Inhaltsfingerprint und dieselbe idempotente Evidenzidentität. Eine Rückkorrektur nach einer zwischenzeitlichen Änderung ist ein neues Revisionsvorkommen.

#### Scenario: T-REV-002 Konfidenz bei schlechter Qualität
WHEN eine abgeleitete Einschätzung hohe Konfidenz und die Quelle schlechte Qualität hat THEN bleibt die Qualität unverändert sichtbar und die Konfidenz erzeugt keine gültige Datenfreigabe.

### Requirement: REQ-REV-002 Konservative Point-in-Time-Selektion
Historische Abfragen MUST Bewertungszeit und Wissensstand explizit binden und ausschließlich bis dahin verfügbare, gültige Revisionen liefern. Unbelegte frühere Verfügbarkeit MUST ausgeschlossen sein. Ein heutiger Backfill MUST nicht allein wegen alter Ereigniszeit als damals bekannt gelten.

#### Scenario: T-REV-003 Heutiger Backfill
GIVEN eine gestern abgeschlossene Kerze, die erst heute ohne belegte historische Publikationszeit importiert wurde WHEN gestern als Wissensstand abgefragt wird THEN bleibt sie ausgeschlossen und fehlende damalige Evidenz wird ausgewiesen.

#### Scenario: T-REV-004 Zeitgrenze
WHEN Evidenz exakt zum Wissensstand verfügbar ist THEN ist sie eingeschlossen; später veröffentlichte, später ingestierte oder noch offene Bars bleiben ausgeschlossen.

### Requirement: REQ-REV-003 Unveränderliche Revisionen und Adjustierungen
Korrekturen SHALL neue unveränderliche Revisionen mit Vorgängerbezug und Verfügbarkeitszeit erzeugen. Historische Abfragen MUST die damals bekannte Revision wählen. Rohe und adjustierte Preise MUST getrennt bleiben; Adjustierung ohne damals belegte Faktoren MUST von Point-in-Time-Resultaten ausgeschlossen werden.

#### Scenario: T-REV-005 Spätere Korrektur
GIVEN eine ursprüngliche und eine später ingestierte korrigierte Kerze WHEN ein Zeitpunkt vor der Korrektur ausgewertet wird THEN erscheint ausschließlich die ursprüngliche Revision und deren Fingerprint.

#### Scenario: T-REV-009 Rückkorrektur auf früheren Inhalt
GIVEN ein natürlicher Barkey besitzt nacheinander die Inhalte A und B WHEN der Provider erneut A als aktuellen Inhalt liefert THEN entsteht eine dritte unveränderliche Revision mit dem Inhaltsfingerprint von A und einem neuen Vorgängerbezug auf B; Wissensstände zwischen den Korrekturen liefern weiterhin ihre damalige Revision, und eine unmittelbare Wiederholung der dritten Revision erzeugt kein viertes Vorkommen.

#### Scenario: T-REV-006 Späterer Split
WHEN eine heute splitadjustierte Historie für einen Zeitpunkt vor Bekanntwerden der Faktoren angefragt wird THEN wird kein PIT-gültiger adjustierter Preis erfunden oder aus der aktuellen Historie zurückdatiert.

### Requirement: REQ-REV-004 Dauerhafter begrenzter Queryzugriff
Evidenz SHALL nach Instrument, Ereigniszeitraum, Typ und Wissensstand mit stabiler begrenzter Pagination abfragbar sein. ID-Lookups MUST dieselben Verfügbarkeits- und Qualitätsgrenzen einhalten. Jede Antwort SHALL Datenversion, Herkunft und explizite Unvollständigkeit ausweisen.

#### Scenario: T-REV-007 Lookup ohne Zeitfilterumgehung
WHEN eine bekannte Evidenz-ID für einen früheren Wissensstand abgefragt wird THEN ist eine erst später verfügbare Revision nicht lesbar und auch im Context nicht referenziert.

#### Scenario: T-REV-008 Pagination bei neuer Ingestion
GIVEN ein gebundener Querysnapshot WHEN zwischen zwei Seiten weitere Daten ingestiert werden THEN entstehen weder doppelte noch übersprungene Einträge in diesem Snapshot.
