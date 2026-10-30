# Spec Delta

## Purpose

Diese Fähigkeit stellt methodisch begrenzte Forschungsergebnisse mit vergleichbaren Grundlagen, erklärten Kennzahlen und dauerhaften authentifizierten Ansichten dar.

## ADDED Requirements

### Requirement: REQ-QCO-001 Explizite Vergleichbarkeit
Vergleiche SHALL Snapshot, Zeit-/Phasenprotokoll, gemeinsamen Warm-up, Modus, Instrument/Feed, Kosten/Fill, Benchmark und Methodenfingerprints prüfen. Nur vollständig vergleichbare Resultate dürfen dieselbe Rangliste bilden. Reliability und aktuelle Warnungen MUST sichtbar bleiben. OOS-Ergebnisse nur gewählter Kandidaten MUST von Validationvergleichen getrennt sein.

#### Scenario: T-QCO-001 Abweichende Basis oder Teilfehler
WHEN Snapshot, Kosten oder Phase abweichen oder ein Kandidat/Fold unvollständig ist THEN entsteht INCOMPARABLE beziehungsweise INCOMPLETE mit Gründen statt eines unmarkierten gemeinsamen Rankings. Kurven verschiedener zurückgesetzter Folds werden nicht zu einer Portfolioequity verkettet. Ein fehlgeschlagener Kandidat wird nicht still aus der Auswahlmenge entfernt.

### Requirement: REQ-QCO-002 Definierte Kennzahlen und Nichtauswertbarkeit
Berichte SHALL Bestandsmetriken und die exakt definierten v1-Erweiterungen aus metrics-methodology.md berechnen. Jede Metrik MUST Methode, Frequenz, Beobachtungszahl und Status mitführen; nicht auswertbare Werte sind null mit Reason Code. Decimalberechnung/Rundung und Kostenbehandlung MUST deterministisch sein; statistische Kennzahlen dürfen keine Gewinnwahrscheinlichkeit behaupten.

#### Scenario: T-QCO-002 Numerische Randfälle
WHEN Varianz null, die Stichprobe zu klein oder Equity nicht positiv ist THEN liefern betroffene Kennzahlen erklärte N/A-Status statt künstlicher Null, NaN oder Infinity. Null Trades lassen valide price-only Nettorendite/Drawdown zu, erzeugen jedoch keine Trade-Win-Rate. Unabhängige Referenzen prüfen negative Returns, Gebühren, Foldreset und unregelmäßige Frequenz.

### Requirement: REQ-QCO-003 Versionierte authentifizierte Researchverträge
Katalog, Entwurfsversion, Validierung, expliziter Start, Status, Kandidatenergebnisse und Vergleich SHALL über additive versionierte Verträge gemäß design.md D7 nutzbar sein. Authentifizierung, Origin/CSRF, Idempotenz und gebundene Pagination MUST vor Seiteneffekten gelten. Bestehende API/Altmanifeste MUST kompatibel bleiben; der Angularclient wird ausschließlich generiert.

#### Scenario: T-QCO-003 Vertrag und Angriff
WHEN Sitzung fehlt, Origin fremd, Draftversion veraltet, Cursor fremd oder Idempotenznutzlast abweichend ist THEN entsteht kein neuer Auftrag; stabile 400/401/403/409/422/429/503-Fehler entsprechen dem kanonischen Vertrag. Zwei Clientgenerierungen sind bytegleich und Breakingchecks zeigen keine Bestandsbrüche.

### Requirement: REQ-QCO-004 Material-Forschungsworkflow
Die Oberfläche SHALL Katalog, Builder mit Kandidaten-/Budgetvorschau, Start, dauerhafte Übersicht sowie Equity-/Drawdown-, Metrik-, Sensitivitäts- und Foldansichten bieten. Modus, Auswahlstatus, Wiederverwendung, Reliability und Einschränkungen MUST sichtbar sein. Finanz-/Indikatorberechnung MUST serverseitig bleiben; Reload erhält Zugriff, Sessionverlust beendet Polling und entfernt sitzungsgebundene Zustände.

#### Scenario: T-QCO-004 Echter Reload und Sessionverlust
WHEN ein echter PG/API/Workerbatch über den Browser gestartet und die Seite neu geladen wird THEN sind derselbe Batch und paginierte Reports wieder erreichbar. Nach Sessionverlust stoppen alle Polls einschließlich Reportdialog und spätere Antworten können keine alten sensitiven Zustände wiederherstellen. Builderänderungen invalidieren Validierung und Startbindung.
