# Spec Delta

## Purpose

Führt einen lokalen Betreiber verständlich zur ersten sicheren Analyse, ohne Anbieter-, Datenübertragungs- oder Handelsrechte aus persönlichen Angaben abzuleiten.

## ADDED Requirements

### Requirement: IEF-O01 Überspringbare Einrichtung
Das System SHALL Ziele, Erfahrung, freiwilligen Anlagehorizont und Verlusttoleranz als änderbare Präferenzen behandeln. Jeder Schritt MUST überspringbar sein. Demo und nicht eingerichtetes Paper MUST ohne Brokerkonto, Modellschlüssel oder Provideraktivierung erreichbar bleiben.

#### Scenario: IEF-T-O01 Einstieg ohne Schlüssel
- **WHEN** ein angemeldeter Einsteiger alle optionalen Fragen überspringt
- **THEN** erreicht er das gekennzeichnete Demo-Cockpit und eine lokale deterministische Analyse; Paper ohne bestätigte Basis zeigt einen Einrichtungszustand ohne Finanzwerte.

#### Scenario: IEF-T-O02 Unterbrochener Einstieg
- **WHEN** der Betreiber nach einem gespeicherten Schritt neu lädt
- **THEN** werden erledigte und übersprungene Schritte unterschieden, er kann fortsetzen oder direkt ins Cockpit wechseln.

#### Scenario: IEF-T-O05 Einstieg verständlich und schnell
- **GIVEN** eine fachfremde Testperson startet in der Einrichtungsansicht
- **WHEN** sie den Demo-Einstieg wählt und optionale Fragen überspringt
- **THEN** erreicht sie ohne Moderationshilfe in höchstens drei Minuten das gekennzeichnete Demo-Cockpit und erkennt den Einstieg zur lokalen Analyse. Für die Teilstrecke müssen mindestens vier von fünf Personen bestehen, bei größerer Stichprobe mindestens 80 Prozent.

### Requirement: IEF-O02 Versionierte Betreiberpräferenzen
Präferenzen SHALL dem authentifizierten Betreiber serverseitig zugeordnet und versioniert sein. Änderungen MUST idempotent sein und veraltete Versionen ablehnen. Präferenzen MUST weder finanzielle Wahrheit noch Autorisierung oder Geeignetheitsprüfung darstellen.

#### Scenario: IEF-T-O03 Parallele Änderung
- **WHEN** zwei Browserstände dieselbe Präferenzversion unterschiedlich ändern
- **THEN** wird nur die erste neue Version gespeichert; der zweite erhält einen erklärbaren Konflikt und lädt den aktuellen Stand.

#### Scenario: IEF-T-O04 Verlusttoleranz
- **WHEN** der Betreiber eine höhere Verlusttoleranz oder mehr Erfahrung angibt
- **THEN** ändern sich nur Darstellung und Erklärungskontext; Risklimits, Providerrechte, Budgets, Autonomiestufe und Paperfreigaben bleiben unverändert.
