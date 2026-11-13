# Spec Delta

## ADDED Requirements

### Requirement: ARP-K01 Research-Schlüssellebenszyklus

Ein authentifizierter lokaler Betreiber MUST einen Research-Schlüssel flüchtig über einen geschützten Same-Origin-Setup-Pfad anlegen, rotieren und lokal widerrufen können. Nur der Setup-Dienst MUST schreiben können, nur der Gateway MUST den Schlüssel für Providerrequests konsumieren.

#### Scenario: Überprüfbares Verhalten

- **WHEN** ein Schlüssel hinterlegt oder rotiert wird
- **THEN** Dashboard und Datenbank erhalten ausschließlich Metadaten und eine Generation

### Requirement: ARP-K02 Sperre und Widerruf

Rotation, Deaktivierung und Widerruf MUST die bisherige Sendeberechtigung sofort am Dispatchpunkt sperren. Antworten bereits gesendeter Versuche MUST bei Widerruf von der fachlichen Veröffentlichung ausgeschlossen bleiben.

#### Scenario: Überprüfbares Verhalten

- **WHEN** ein alter Auftrag oder eine verspätete Antwort eintrifft
- **THEN** kein neuer Versand und keine Veröffentlichung sind möglich
