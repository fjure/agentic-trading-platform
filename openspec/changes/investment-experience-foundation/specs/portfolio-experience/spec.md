# Spec Delta

## Purpose

Ermöglicht verständliche Portfolioansichten und nachvollziehbare Analysen aus dem bestehenden Ledger mit sichtbaren Quellen, Revisionsbindungen und Datenlücken.

## ADDED Requirements

### Requirement: IEF-P01 Konsistente Portfolioansicht
Die Ansicht SHALL Finanzwerte ausschließlich aus einer konsistenten serverseitigen Projektion des gewählten Ledgers beziehen. Demo/Fake, Paper, Brokerabgleich und historischer Bewertungsstand MUST unterscheidbar bleiben. Decimalstrings, UTC, Quellreferenz, Ledgerrevision, Bewertungszeit und Qualitätsstatus MUST erhalten bleiben; Lesezugriff MUST ohne Provider-I/O bleiben.

#### Scenario: IEF-T-P01 Getrennte Quellen
- **WHEN** der Betreiber zwischen Demo und Paper wechselt
- **THEN** ändern sich Kontext und belegte Projektion atomar; Cash, Positionen und Zeitreihen verschiedener Ledger werden niemals kombiniert und Brokerabgleich wird mit Prüfzeit statt als zeitlose Garantie gezeigt.

#### Scenario: IEF-T-P02 Gleichzeitiger Fill
- **WHEN** während einer paginierten Ansicht ein Fill eintrifft
- **THEN** bleiben alle Seiten an ihre Snapshotrevision gebunden oder verlangen explizit einen Neustart; offene Verpflichtungen werden nicht mit einer anderen Cashrevision verrechnet.

### Requirement: IEF-P02 Verfügbarkeit statt Ersatzwerte
Jede Kennzahl SHALL Wert, Einheit, Methode und Verfügbarkeit besitzen. Fehlende Preise, FX, Kostenbasis, Gebühren, Kapitalmaßnahmen oder historische Flüsse MUST betroffene Kennzahlen begründet unverfügbar machen. Unbekannt MUST von null unterschieden werden; aktuelle Bestände MUST keine erfundene historische Rendite erzeugen.

#### Scenario: IEF-T-P03 Lückenhafte Kostenbasis
- **WHEN** Kostenbasis oder Gebühren fehlen
- **THEN** bleiben davon abhängige realisierte oder unrealisierte Nettoergebnisse unbekannt; bestätigte Mengen und Cash bleiben mit eigener Qualität sichtbar.

#### Scenario: IEF-T-P04 Währung und Historie
- **WHEN** eine Bewertung mehrere Währungen ohne belegten zeitgleichen FX-Kurs oder unvollständige Fluss-/Split-/Dividendenhistorie enthält
- **THEN** erscheinen getrennte Währungswerte und Gründe; Gesamtrendite, Total Return, historischer Drawdown und Vergleich werden nicht erfunden.

### Requirement: IEF-P03 Deterministische nachvollziehbare Insights
Konzentration, Gewichte, Frische, Vollständigkeit und verfügbare Performance SHALL deterministisch mit versionierter Methode berechnet werden. Jeder Insight MUST Eingaberevisionen, Methode, Zeitraum, Grenzen und erklärbare nächste Aktion referenzieren. Rankings, Zukunftsrenditen und Risikoscores ohne definierte Methode MUST ausgeschlossen bleiben.

#### Scenario: IEF-T-P05 Konzentration
- **WHEN** alle bewerteten Positionen und der definierte Nenner verfügbar sind
- **THEN** zeigt die Analyse exakt reproduzierbare Gewichte mit Nenner und Datenstand; bei fehlendem Nenner zeigt sie keinen scheinbar vollständigen Prozentwert.

#### Scenario: IEF-T-P06 ETF-Überschneidung ohne Holdings
- **WHEN** zwei ETFs vorliegen, aber keine zulässigen Holdingsdaten
- **THEN** wird eine Überschneidung höchstens als offene Forschungsfrage dargestellt, ohne quantifizierten Befund oder erfundene Durchschau.

### Requirement: IEF-P04 Getrennte optionale KI-Erklärung
KI-Erklärungen SHALL nur ausdrücklich freigegebene minimierte Projektionen verwenden. Daten- und Kostenfreigabe MUST getrennt prüfbar sein. Jede Tatsachenaussage MUST Evidenz referenzieren, Hypothesen MUST gekennzeichnet sein. Ein Modell MUST Finanzwerte und deterministische Befunde weder erzeugen noch überschreiben.

#### Scenario: IEF-T-P07 Lokale Analyse ohne Modell
- **WHEN** Provider deaktiviert oder keine Sendefreigabe vorhanden ist
- **THEN** bleiben deterministische Kennzahlen und feste Erklärungen verfügbar; eine zusätzliche KI-Erklärung wird mit begründetem Freigabeschritt angeboten.

#### Scenario: IEF-T-P08 Unbelegte Modellaussage
- **WHEN** eine Modellantwort eine nicht referenzierte Rendite oder Kaufempfehlung enthält
- **THEN** wird sie nicht als belegter Portfolio Insight veröffentlicht; die lokale Analyse bleibt unverändert und die Validierungsablehnung ist nachvollziehbar.
