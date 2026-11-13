# Spec Delta

## ADDED Requirements

### Requirement: ARP-K01 Research-Schlüssellebenszyklus

Ein authentifizierter lokaler Betreiber MUST einen Research-Schlüssel flüchtig über einen geschützten Same-Origin-Setup-Pfad anlegen, rotieren und lokal widerrufen können. Nur der Setup-Dienst MUST schreiben können, nur der Gateway MUST den Schlüssel für Providerrequests konsumieren.

#### Scenario: ARP-K01-S01 – Einmalige Schlüsseleingabe

- **GIVEN** eine authentifizierte lokale Setup-Sitzung mit gültigem Origin und CSRF-Schutz
- **WHEN** der Betreiber einen synthetischen Testschlüssel einmalig hinterlegt
- **THEN** nur Setup schreibt; UI und Datenbank sehen Status und Generation
- **AND** der Nachweis ist [ARP-03](../../work-packages/ARP-03.md) zugeordnet

#### Scenario: ARP-K01-S02 – Ungültiger Setup-Request

- **GIVEN** ein fremder Origin, falscher CSRF-Wert oder verbrauchter Grant
- **WHEN** der Request erneut eintrifft
- **THEN** kein Credentialwrite und keine Generationserhöhung erfolgen
- **AND** der Nachweis ist [ARP-03](../../work-packages/ARP-03.md) zugeordnet

#### Scenario: ARP-K01-S03 – Prozesszugriffsgrenze

- **GIVEN** Setup, Gateway, Supervisor und Worker in der Produktionskomposition
- **WHEN** ihre tatsächlichen Mountrechte geprüft werden
- **THEN** nur Setup hat Schreibzugriff, nur Gateway konsumiert den Key und übrige Prozesse können den Schlüssel nicht lesen
- **AND** der Nachweis ist [ARP-03](../../work-packages/ARP-03.md), [ARP-09](../../work-packages/ARP-09.md) zugeordnet

### Requirement: ARP-K02 Sperre und Widerruf

Rotation, Deaktivierung und Widerruf MUST die bisherige Sendeberechtigung sofort am Dispatchpunkt sperren. Antworten bereits gesendeter Versuche MUST bei Widerruf von der fachlichen Veröffentlichung ausgeschlossen bleiben.

#### Scenario: ARP-K02-S01 – Rotation entwertet alte Generation

- **GIVEN** eine aktive Bindung für Generation N
- **WHEN** der Betreiber auf N+1 rotiert und ein alter Auftrag eintrifft
- **THEN** der alte Auftrag bleibt ungesendet und N+1 startet inaktiv
- **AND** der Nachweis ist [ARP-03](../../work-packages/ARP-03.md), [ARP-04](../../work-packages/ARP-04.md) zugeordnet

#### Scenario: ARP-K02-S02 – Widerruf vor Commit und verspätetes Ergebnis

- **GIVEN** ein validierter Auftrag oder schon gestarteter Attempt
- **WHEN** der Betreiber widerruft
- **THEN** ein neuer Send-Commit scheitert; eine verspätete Antwort wird fachlich nicht veröffentlicht
- **AND** der Nachweis ist [ARP-03](../../work-packages/ARP-03.md), [ARP-04](../../work-packages/ARP-04.md), [ARP-09](../../work-packages/ARP-09.md) zugeordnet
