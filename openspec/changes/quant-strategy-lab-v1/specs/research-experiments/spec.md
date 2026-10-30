# Spec Delta

## Purpose

Diese Fähigkeit führt begrenzte Forschungsbatches auf unveränderlichen Grundlagen aus und trennt Parametersuche nachvollziehbar von unberührten Testdaten.

## ADDED Requirements

### Requirement: REQ-QEX-001 Vollständig validiertes Grid
Ein versionierter Entwurf SHALL Hypothese, Strategien, endliche Parameterwertelisten, Modus, Zeitprotokoll und Simulationsannahmen enthalten. Das vollständige kartesische Grid MUST vor Jobanlage validiert, kanonisch geordnet und duplikatfrei sein; ungültige Kombinationen MUST den gesamten Entwurf abweisen. Höchstens 32 Suchkandidaten plus zwei Referenzen sind zulässig.

#### Scenario: T-QEX-001 Enumeration und ungültiges Grid
WHEN Fast=[5,10,15] und Slow=[30,40,50] gewählt werden THEN entstehen genau neun stabile Konfigurationen. Wiederholte Werte/Definitionen, fast>=slow oder das 33. Suchmitglied scheitern vor einem Workerjob; ungültige Kombinationen werden nicht still entfernt.

### Requirement: REQ-QEX-002 Gemeinsame unveränderliche Grundlage
Alle Kandidaten SHALL denselben eingefrorenen Snapshot mit konkreten Revisionen, Modus/Policy, gemeinsamen Fenstern, Warm-up, Kalender/Trust, Kosten, Fill-/Metrik-/Codeversionen verwenden. Snapshot und Kandidatenmanifeste MUST kanonisch gehasht sein und alle gebundenen Revisionen erhalten bleiben. Neue Daten oder Korrekturen MUST neue Experimentversionen erfordern.

#### Scenario: T-QEX-002 Korrektur zwischen Kandidaten
WHEN nach dem ersten Kandidaten neue Bars oder Korrekturen committen THEN verwendet jeder weitere Kandidat und Restart nur die vorher gebundenen IDs/Fingerprints. Fehlende gebundene Revisionen blockieren statt latest zu lesen; Altresultate und Reliabilityreports bleiben unverändert.

### Requirement: REQ-QEX-003 Chronologische Auswahl ohne OOS-Zugriff
Das unveränderliche Protokoll SHALL ein bis vier chronologische Training-/Validation-/Testtripel und einen späteren finalen Holdout binden. Kandidatenwahl MUST ausschließlich die vorher gebundene Validationregel verwenden und vor Testberechnung dauerhaft eingefroren sein. Der finale Kandidat MUST ausschließlich aus Validationresultaten gewählt werden; OOS-Ergebnisse dürfen keine Neuauswahl auslösen.

#### Scenario: T-QEX-003 Verbotenes Leakage
WHEN ein Training/Validationfenster in sein Testfenster reicht, Phasen sich entgegen D3 überlappen oder der Auswahlcode ein Testresultat anfordert THEN wird der Vorgang abgewiesen. Perturbation beliebiger späterer Test-Outcomes ändert die eingefrorene Auswahl nicht. Gleiche Auswahlmetriken werden deterministisch nach Kandidaten-ID aufgelöst.

#### Scenario: T-QEX-004 Holdout und Wiederverwendung
WHEN ein finaler Holdout ausgeführt wird THEN erhält nur die bereits eingefrorene Auswahl einen finalen Test; Testdaten gehen nicht in Sensitivitätsranglisten ein. Eine weitere Experimentrunde mit überlappender Testzeit wird dauerhaft mit allen früheren angeforderten Runden und sichtbarem Selection-Bias-Risiko verbunden, auch nach Abbruch oder unter neuer Hypothese. Es entsteht kein Frischezertifikat.

### Requirement: REQ-QEX-004 Gemeinsame Ressourcenbarriere
Experimente MUST D5 einschließlich gewichtetem Queue100, einer aktiven globalen Lease, maximal vier Walk-forward-Folds plus Holdout, 366 Tagen, 100000 Datenzeilen, 100000 Simulationsbarschritten und 12MiB Gesamtpublikation einhalten. Admission, Rechen-/Speichergrenzen und Restartbudgets MUST batchweit gelten; Kandidaten dürfen keine zusätzliche unabhängige Queue erzeugen.

#### Scenario: T-QEX-005 Überlast vor Arbeit
WHEN 32 Kandidaten plus zwei Referenzen bei 67 belegten Queueslots eintreffen oder die konservative Arbeitsabschätzung das Budget überschreitet THEN scheitert die Zulassung atomar. Ungültige Entwürfe reservieren nichts. Unter zulässiger maximaler Forschungslast bleiben Pause/Kill/Reconciliation bedienbar; Erschöpfung erzeugt kein Erfolgsergebnis.

### Requirement: REQ-QEX-005 Dauerhafte fenced Veröffentlichung
Experimentversionen, Snapshot, Kandidaten, Auswahlentscheidungen, Resultatreferenzen und Korrekturvorgänger SHALL dauerhaft verknüpft sein. Gebundene Inhalte MUST append-only und abgeschlossene Ergebnisse unveränderlich bleiben. Idempotenz, Lease/Fence, höchstens drei Versuche und atomarer Resultat-/Fortschrittscommit MUST doppelte oder fremde Veröffentlichungen verhindern.

#### Scenario: T-QEX-006 Crash und Altworker
WHEN ein Worker vor/nach einem Resultatcommit stirbt THEN übernimmt der Nachfolger denselben Snapshot, dieselbe Auswahl und bestätigten Fortschritt. Alte Fences und Altbinärversionen können neue Jobs weder claimen noch publizieren. Budgets und bisherige Versuchszähler werden nicht zurückgesetzt; terminale Teilfehler werden nicht als vollständiger Batch ausgegeben.

### Requirement: REQ-QEX-006 Unveränderte Research- und Safetygrenzen
STRICT_PIT MUST Standard bleiben und sämtliche bisherigen Evidencegates erhalten. RETROSPECTIVE_EXPLORATORY MUST explizit gebunden sein; automatische Fallbacks sind verboten. Negative Lizenz-/Trust-/Maßnahmenbefunde MUST Ausführung/Publikation sperren. Research MUST credentialfrei, ohne Provider-/Broker-/Jev-Aufrufe und ohne Trading-/Risk-/LIVE-Rechte bleiben.

#### Scenario: T-QEX-007 Quality und finanzielle Isolation
WHEN Strict mit demselben heutigen Backfill blockiert oder später eine strukturelle Maßnahme/Denied-Lizenz bekannt wird THEN erfolgt keine implizite Modusumschaltung und kein freigegebenes Ranking. Echte Rollen-/Containertests belegen verweigerte Finanzwrites, null externe Fähigkeiten und unveränderte Ledger; hohe Rendite kompensiert keine Sperre.
