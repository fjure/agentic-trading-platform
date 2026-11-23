# Spec Delta

## Purpose

Erschließt das vorhandene geschlossene Strategieregister durch verständliche Karten, überprüfbare Resultate und sichere Übergänge zu Research und zulässigem Paper.

## ADDED Requirements

### Requirement: IEF-S01 Verständliche versionierte Strategien
Strategiekarten SHALL Name, Prinzip, Instrumente, Datenanforderungen, Version, Prüfzeit und Einschränkungen aus belegtem Registerkontext zeigen. Ideen und nicht implementierte Strategien MUST von ausführbaren Versionen unterscheidbar bleiben. Fehlende oder widerrufene Fähigkeiten MUST ausführende Aktionen sperren.

#### Scenario: IEF-T-S01 Vorhandene Indikatoren
- **WHEN** SMA, EMA, RSI oder Buy-and-Hold angezeigt werden
- **THEN** stammen verfügbare Versionen und Instrumentgrenzen aus dem installierten Register; eine Researchimplementierung erteilt keine Paperfähigkeit.

#### Scenario: IEF-T-S02 Neue Ereignisstrategie
- **WHEN** eine freie Idee eine nicht registrierte Ereignisstrategie benötigt
- **THEN** erscheint sie als Researchidee mit Testbarkeitsgrund; es gibt keinen Start einer erfundenen Strategie und keine stille Ersetzung durch RSI.

### Requirement: IEF-S02 Evidenzgebundener Status und Vergleich
Die Oberfläche SHALL Forschungsstand und aktuelle Paperzulässigkeit getrennt projizieren. Historische Ergebnisse MUST Datasetrevision, Zeitraum, Researchmodus, Auswahl-/OOS-/Holdoutrollen, Gebühren, Slippage und Einschränkungen zeigen. Nicht vergleichbare Ergebnisse MUST als solche erkennbar sein; ein Backtest MUST keine Empfehlung oder automatische Promotion auslösen.

#### Scenario: IEF-T-S03 Unvergleichbare Berichte
- **WHEN** zwei Resultate unterschiedliche Zeitrollen, Datensichten oder Kostenannahmen besitzen
- **THEN** werden Unterschiede vor einem Vergleich erklärt und kein gemeinsames Erfolgsranking angezeigt.

#### Scenario: IEF-T-S04 Veraltete Zulässigkeit
- **WHEN** ein historischer Validierungsnachweis existiert, aber aktuelle Paperpolicy oder Fähigkeiten fehlen
- **THEN** bleibt der historische Bericht lesbar und der Paperübergang gesperrt; ein Statusbadge ersetzt keine aktuelle Prüfung.

### Requirement: IEF-S03 Geführte Forschungsübergänge
Der Nutzer SHALL aus Strategieansichten einen gebundenen Backtest- oder Researchentwurf vorbereiten können. Alle notwendigen Hypothesen-, Daten-, Budget- und Quantfreigaben MUST vor Wirkungen separat geprüft werden; Ansichtswechsel MUST ursprüngliche Agentenherkunft und Versionsbindung erhalten.

#### Scenario: IEF-T-S05 Backtest vorbereiten
- **WHEN** der Nutzer eine Strategie zur Untersuchung wählt
- **THEN** sieht er vor dem Start Instrument, Datenmodus, Zeitraum, Ressourcenbedarf und bestehende Blocker; Vorbereitung startet kein Experiment.
