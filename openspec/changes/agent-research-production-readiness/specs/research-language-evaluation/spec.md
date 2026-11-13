# Spec Delta

## Purpose

Bewertet die fachliche Qualität deutscher Researchinterpretationen reproduzierbar für jede Modell- und Promptversion.

## ADDED Requirements

### Requirement: ARP-E01 Unabhängige Referenz

Die Evaluation MUST versionierte deutsche Fälle mit zulässigen Interpretationen, Pflichtinformationen, offenen Fragen, unzulässigen Annahmen, Datenanforderungen, Testbarkeit und Begründung enthalten. Die Referenzen MUST unabhängig vom evaluierten Modell fachlich bestätigt werden.

#### Scenario: Überprüfbares Verhalten

- **WHEN** ein neuer oder geänderter Fall in den Goldstandard aufgenommen wird
- **THEN** Fallrevision, Entscheidung und unabhängige Prüfung sind nachvollziehbar

### Requirement: ARP-E02 Getrennte Abnahme

Die Auswertung MUST Semantik, Protokoll, deterministische Fachvalidierung, Security und Erklärung getrennt ausweisen. Reale Läufe MUST gesondert autorisiert, budgetiert und auf synthetische Inhalte beschränkt sein.

#### Scenario: Überprüfbares Verhalten

- **WHEN** ein Modell gute Semantik liefert und einen kritischen Sicherheitsfall verletzt
- **THEN** die Modellbindung besteht die Abnahme nicht
