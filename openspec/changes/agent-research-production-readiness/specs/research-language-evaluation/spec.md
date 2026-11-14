# Spec Delta

## Purpose

Bewertet die fachliche Qualität deutscher Researchinterpretationen reproduzierbar für jede Modell- und Promptversion.

## ADDED Requirements

### Requirement: ARP-E01 Unabhängige Referenz

Versionierte deutsche Goldfälle MUST fachlich getrennt von der Modellentwicklung bestätigt werden. Der KI-Pfad MUST zwei isolierte Prüfschritte, deterministische Kontrollen und dokumentierte Unabhängigkeitsstufen enthalten. Menschliche Fachprüfung ist optional. Ungeklärte kritische Widersprüche oder unbelegte Quant-Behauptungen MUST den Fall sperren.

#### Scenario: ARP-E01-S01 – Unabhängige Referenzrevision

- **GIVEN** ein neuer deutscher Goldfall mit alternativespezifischen Pflichtfeldern
- **WHEN** ein isolierter KI-Reviewer und ein zweiter getrennt kontextualisierter Prüfschritt die Referenz prüfen und die deterministischen Prüfungen bestehen
- **THEN** ID, Revision, zulässige Auslegungen und Entscheidungsbegründung sind versioniert
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md) zugeordnet

#### Scenario: ARP-E01-S02 – Unzureichende Pflichtabdeckung

- **GIVEN** ein Katalog mit weniger als 48 Fällen, weniger als zwölf Fällen oder weniger als sechs Holdoutfällen in einer der vier Gruppen oder ohne anwendbaren Holdoutfall für eine Pflichtmetrik ihrer Schicht
- **WHEN** die Freigabeprüfung startet
- **THEN** das erste Modellgate bleibt gesperrt und weist die Lücke aus
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

#### Scenario: ARP-E01-S03 – Ablehnung ungeklärter Referenzen

- **GIVEN** zwei isolierte Referenzprüfungen mit widersprüchlichen Annotationen oder einer unbelegten Quant-Behauptung
- **WHEN** die deterministische Prüfung keine fachlich gültige Entscheidung belegen kann
- **THEN** der betroffene Fall bleibt außerhalb des versiegelten Holdouts
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md) zugeordnet

### Requirement: ARP-E02 Getrennte Abnahme

Die Auswertung MUST Semantik, Protokoll, deterministische Fachvalidierung, Security und Erklärung getrennt ausweisen. Reale Läufe MUST gesondert autorisiert, budgetiert und auf synthetische Inhalte beschränkt sein. Eine fachliche Freigabe MUST einen getrennt verwahrten Holdout, vollständige Läufe und dokumentierte Regressionsprüfung nachweisen. Die Offline-Abnahme MUST getrennt von einer tatsächlichen modellgebundenen Fachfreigabe ausgewiesen werden.

#### Scenario: ARP-E02-S01 – Gültige und mehrdeutige Interpretation

- **GIVEN** ein eindeutiger Referenzfall und ein Fall ohne Instrument
- **WHEN** die Antworten Hypothese und Zeitrelation korrekt extrahieren beziehungsweise nach dem Instrument fragen
- **THEN** die erste Interpretation und die gezielte Rückfrage werden gemäß Referenz gewertet
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md) zugeordnet

#### Scenario: ARP-E02-S02 – Fehlende Modellantwort

- **GIVEN** ein anwendbarer autorisierter Holdoutfall
- **WHEN** Timeout oder fehlende Antwort auftritt
- **THEN** der Fall bleibt als Fehler im Modellnenner sichtbar; erwartete lokale Blocks werden separat gezählt
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md) zugeordnet

#### Scenario: ARP-E02-S03 – Erfundene oder veränderte Angaben

- **GIVEN** ein Fall mit bestätigtem EMA-Parameter und unveränderlicher Registry-Version
- **WHEN** die Antwort still Parameter ändert oder Evidenz erfindet
- **THEN** Rohfehler und lokale Ablehnung erscheinen getrennt; ein kritischer Fehler blockiert die Freigabe
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md) zugeordnet

#### Scenario: ARP-E02-S04 – Kritischer Fehler trotz hoher Scores

- **GIVEN** ein Lauf über den Qualitätsschwellen
- **WHEN** ein Modell unerlaubte Toolaktionen versucht oder eine unzulässige Übertragung erfolgt
- **THEN** der kritische Einzelbefund blockiert unabhängig vom Durchschnitt
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

#### Scenario: ARP-E02-S05 – Paarweise Regression

- **GIVEN** zwei vorab registrierte Bindungen auf denselben Fällen und Wiederholungen
- **WHEN** der Kandidat häufiger unnötig rückfragt oder schlechter abstainiert
- **THEN** die verschlechterten Fälle werden benannt und verlangen unabhängige Entscheidung; Holdout-Optimierung erfordert einen neuen Holdout
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

#### Scenario: ARP-E02-S06 – Erste modellgebundene Freigabe

- **GIVEN** ein vollständig annotierter Goldstandard mit versiegeltem Holdout und registrierter Baseline oder Vergleichsbindung
- **WHEN** drei vollständig autorisierte Holdoutläufe die Pflichtschwellen erreichen und die Regressionsentscheidungen mit Review-Provenienz vorliegen
- **THEN** die fachliche Freigabe gilt für die geprüfte Bindung. Fehlt eine Autorisierung, ein vollständiger Lauf oder eine notwendige Prüfentscheidung, bleibt sie gesperrt.
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

### Requirement: ARP-E03 Registrierte einmalige Holdout-Freigabe

Entwicklung, KI-Review und deterministischer Custodian MUST getrennte Vertrauensgrenzen haben. Vor Öffnung MUST der Custodian einen extern verankerten Registrierungsnachweis, die versionierte Ed25519-Attestation und eine separat erteilte Betreiberfreigabe auf dieselbe Modell-, Prompt-, Schema-, Scorer- und Datenbindung prüfen. Eine gültige Freigabe MUST einmalig sein. Der Betreiber MUST den Custodian-Schlüssel unabhängig pinnen.

#### Scenario: ARP-E03-S01 – Registrierte Öffnung

- **GIVEN** ein versiegelter Holdout, ein unabhängig gepinnter Schlüssel und ein verifizierbarer, vor Öffnung verankerter Registrierungsnachweis
- **WHEN** Attestation, Bindung, belegte Reviewisolation und Betreiberentscheidung übereinstimmen
- **THEN** der Custodian gibt den Holdout genau einmal für den gebundenen Lauf frei
- **AND** das Audit enthält keine vertraulichen Fallinhalte
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

#### Scenario: ARP-E03-S02 – Fehlende Vertrauensgrundlage oder Replay

- **GIVEN** geänderte Bindungsdaten, falsche Signatur, unbekannter oder widerrufener Schlüssel, fehlende oder manipulierte Registrierung, verfrühte Öffnung, fehlende Isolation, fehlende Betreiberentscheidung oder Replay
- **WHEN** die Freigabe geprüft wird
- **THEN** der Holdout bleibt gesperrt und das Audit enthält ausschließlich redigierte Metadaten
- **AND** der Nachweis ist [ARP-06](../../work-packages/ARP-06.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet
