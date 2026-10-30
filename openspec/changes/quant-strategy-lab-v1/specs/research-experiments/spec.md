# Spec Delta

## Purpose

Diese Fähigkeit führt begrenzte Forschungsbatches auf unveränderlichen Grundlagen aus und trennt Parametersuche nachvollziehbar von späteren isolierten Testdaten; unbekannte oder wiederverwendete Testkenntnis wird offengelegt.

## ADDED Requirements

### Requirement: REQ-QEX-001 Vollständig validiertes Grid
Ein versionierter Entwurf SHALL Hypothese, Strategien, endliche Parameterwertelisten, Modus, Zeitprotokoll, strategySelectionPolicy samt sämtlichen Parametern und Simulationsannahmen enthalten. Das vollständige kartesische Grid MUST vor Jobanlage validiert, kanonisch geordnet und duplikatfrei sein; ungültige Kombinationen MUST den gesamten Entwurf abweisen. Höchstens 32 Suchkandidaten plus zwei Referenzen sind zulässig.

#### Scenario: T-QEX-001 Enumeration und ungültiges Grid
WHEN Fast=[5,10,15] und Slow=[30,40,50] gewählt werden THEN entstehen genau neun stabile Konfigurationen. Wiederholte Werte/Definitionen, fast>=slow oder das 33. Suchmitglied scheitern vor einem Workerjob; ungültige Kombinationen werden nicht still entfernt.

### Requirement: REQ-QEX-002 Gemeinsame unveränderliche Grundlage
Alle Kandidaten SHALL denselben eingefrorenen Snapshot mit konkreten Revisionen, Modus/Datenselektionspolicy, strategySelectionPolicy und Parametern, gemeinsamen Fenstern, Warm-up, Kalender/Trust, Kosten, Fill-/Metrik-/Codeversionen verwenden. Snapshot und Kandidatenmanifeste MUST kanonisch gehasht sein und alle gebundenen Revisionen erhalten bleiben. Neue Daten, Policyänderungen oder Korrekturen MUST neue Experimentversionen erfordern.

#### Scenario: T-QEX-002 Korrektur zwischen Kandidaten
WHEN nach dem ersten Kandidaten neue Bars oder Korrekturen committen THEN verwendet jeder weitere Kandidat und Restart nur die vorher gebundenen IDs/Fingerprints. Fehlende gebundene Revisionen blockieren statt latest zu lesen; Altresultate und Reliabilityreports bleiben unverändert.

### Requirement: REQ-QEX-003 Chronologische Auswahl ohne OOS-Zugriff
Das unveränderliche Protokoll SHALL ein bis vier chronologische Training-/Validation-/Testtripel und einen späteren Holdout binden. Alle Fold- und finalen Auswahlen MUST ausschließlich aus gebundener Validation und vor jeder OOS-Berechnung dauerhaft eingefroren sein. OOS darf keine Neuauswahl auslösen. Phasenrollen MUST research-protocol.md entsprechen.

#### Scenario: T-QEX-003 Verbotenes Leakage
WHEN ein Training/Validationfenster in sein Testfenster reicht, Phasen sich entgegen D3 überlappen oder der Auswahlcode ein Testresultat anfordert THEN wird der Vorgang abgewiesen. Perturbation beliebiger späterer Test-Outcomes ändert die eingefrorene Auswahl nicht. Gleiche Auswahlmetriken werden deterministisch nach Kandidaten-ID aufgelöst.

#### Scenario: T-QEX-004 Holdout und Wiederverwendung
WHEN ein finaler Holdout ausgeführt wird THEN erhält nur die bereits eingefrorene Auswahl einen finalen Test; Testdaten gehen nicht in Sensitivitätsranglisten ein. Eine weitere Experimentrunde mit überlappender Testzeit wird dauerhaft mit allen früheren angeforderten Runden und sichtbarem Selection-Bias-Risiko verbunden, auch nach Abbruch oder unter neuer Hypothese. Es entsteht kein Frischezertifikat.

Verbindliche Prüfdetails: Das unveränderliche Protokoll SHALL ein bis vier chronologische Training-/Validation-/Testtripel und einen späteren finalen Holdout binden. Kandidatenwahl MUST ausschließlich die vorher gebundene Validationregel verwenden; alle Fold- und finalen Entscheidungen MUST vor irgendeiner Testberechnung dauerhaft eingefroren sein. Der finale Kandidat MUST ausschließlich aus Validationresultaten gewählt werden; OOS-Ergebnisse dürfen keine Neuauswahl auslösen. Fachliche Phasenrollen MUST gemäß research-protocol.md kenntlich sein.

### Requirement: REQ-QEX-007 Gebundene Strategy Selection Policy
Das Experiment SHALL vor Ergebnissichtung eine geschlossene Policy aus selection-policy.md samt Parametern/Fingerprint unveränderlich in Definition, Manifest und Selectionhash binden. Gates, finale Zulässigkeit in jeder Validation und deterministische Vergleichsschlüssel MUST exakt dieser Methodik entsprechen. Keine zulässige Wahl MUST NO_ELIGIBLE_CANDIDATE ohne Ersatzsieger ergeben; technische Fehler MUST davon getrennt bleiben.

#### Scenario: T-QEX-008 Rendite und Kapitalerhaltung
WHEN A Return0.005/DD0.01 und B Return0.12/DD0.08 bei DD-Limit0.10 und vollständigem Umfang antreten THEN wählt die Standardpolicy B, die ausdrücklich gebundene Kapitalerhaltung A. Flat0/0 gewinnt nicht gegen positiven zulässigen Return, darf aber gegen negative Returns oder unter Kapitalerhaltung gewinnen. Ohne explizites Aktivitätsminimum entsteht kein Aktivitätsbonus. Alle numerischen Fälle S1..S12 aus selection-policy.md werden unabhängig geprüft.

#### Scenario: T-QEX-009 Nichtwahl, Grenzen und unveränderliche Identität
WHEN alle Kandidaten ein Limit verletzen THEN entstehen dokumentierte Nichtwahl und keine Ersatz-/Flatstrategie; bei einem fehlgeschlagenen Kandidaten dagegen INCOMPLETE/BLOCKED oder FAILED ohne verkleinerte Auswahl. DD genau am Limit ist zulässig, darüber nicht, einschließlich eines vor Darstellungsrundung höheren Werts. Fehlende Pflichtmetrik blockiert, nicht benötigte Sharpe N/A nicht. Änderung nur der Policy oder eines Parameters verändert alle gebundenen Identitäten; Mutationsversuch nach Start scheitert409. Gleiche vollständige Inputs ergeben dieselben Ties/Hashes unabhängig von Reihenfolge und OOSwerten.

Verbindliche Prüfdetails: Das Experiment SHALL genau eine geschlossene versionierte Policy aus selection-policy.md vor Ergebnissichtung binden. MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1 MUST Standard sein; CAPITAL_PRESERVATION_DRAWDOWN_FIRST_V1 darf nur ausdrücklich als Kapitalerhaltungsziel gewählt werden. Policy, Parameter und Selektorfingerprint MUST in Definition-/Manifest-/Selectionhash eingehen. Vollständigkeit, Mindestumfang, inklusive DD-Grenze und optionale Aktivitätsminima MUST vor Ranking geprüft werden; finale Zulässigkeit MUST jede Validationphase erfüllen. Alle Entscheidungen MUST Gates, Werte, Gründe und deterministische Vergleichsschlüssel enthalten. Kein zulässiger Kandidat MUST NO_ELIGIBLE_CANDIDATE mit null Gewinner und ohne Kandidaten-OOS ergeben; technische Fehler MUST von vollständiger Nichtwahl unterschieden bleiben.

### Requirement: REQ-QEX-008 Ehrliche Forschungsrollen
Protokoll und Berichte SHALL die vier Forschungsrollen gemäß research-protocol.md trennen und nicht trainierbare Strategien NOT_TRAINABLE nennen. In-Sample MUST deskriptiv, Validation MUST als Optimierungsdatensatz gelten. OOS MUST das eingefrorene Verfahren und Holdout den vorher final gewählten Kandidaten beschreiben; daraus dürfen keine statistische Sicherheit oder Handelsfreigabe abgeleitet werden.

#### Scenario: T-QEX-010 Fehlende Phase und schlechter Holdout
WHEN eine Phase fehlt, leer, falsch geordnet oder überlappend ist THEN scheitert das Protokoll vor Jobanlage. Ein gültiges gleiches Protokoll liefert dieselben Auswahlen; NOT_TRAINABLE wird anstelle TRAINED_MODEL gezeigt. Schlechter Holdout erzeugt keine erneute Auswahl/Optimierung; neuer Hypothesenauftrag erfordert neue Version und Expositionswarnung. In-Sampleänderungen der Performance und spätere OOS-Perturbation beeinflussen keine Validationauswahl.

Verbindliche Prüfdetails: Register, Protokoll und Berichte SHALL In-Sample Analysis, Parameter Selection Window, Walk-forward OOS und Final Holdout gemäß research-protocol.md trennen. Deterministische nicht trainierbare Strategien MUST NOT_TRAINABLE tragen. In-Sample MUST deskriptiv bleiben; Validation MUST als Optimierungsdatensatz der Parametersuche gelten. OOS MUST das eingefrorene Verfahren und Holdout den vorher final gewählten Kandidaten beschreiben; weder geringe Stichprobe noch hoher Ertrag darf statistische Sicherheit oder Handelsfreigabe behaupten.

### Requirement: REQ-QEX-009 Multiple Testing und rollenübergreifende Vorkenntnis
Forschungsberichte SHALL Kandidaten-/Rundenstatus, Multiple Testing und rollenübergreifende Wiederverwendung gemäß research-protocol.md offenlegen. Admission MUST den Vorgängerstand atomar lesen/registrieren. Fehlversuche MUST sichtbar und aktuelle Warnungen von unveränderlichem Kenntnisstand getrennt bleiben. Externe Kenntnis MUST unbekannt bleiben; kein Treffer darf Unabhängigkeit garantieren.

#### Scenario: T-QEX-011 Neue Hypothese auf bekannter Periode
WHEN ein neues OOS-/Holdoutfenster eine frühere Validation/Analyse/Testperiode oder eine neue Validation eine frühere Testperiode überlappt THEN zeigt REGISTERED_REUSE beide Rollen, IDs, Intervalle und Umfang auch bei neuer Hypothese, Actor, Modus oder Revision. Parallele Admission kann die frühere akzeptierte Runde nicht übersehen. Retries zählen einmal; fehlgeschlagene/verdrängte Runden bleiben in Statuszahlen. Ohne Treffer wird NO_REGISTERED_PRIOR_USE plus EXTERNAL_KNOWLEDGE_UNKNOWN gezeigt; spätere Warnungen ändern alte Hashes nicht.

Upgrade mit nichtleeren historischen Strict-/Retrospective-Einzelruns MUST deren bekannte Phasen einschließlich FAILED/BLOCKED konservativ übernehmen; unrekonstruierbare Rollen bleiben UNKNOWN. Gleichzeitig eintreffende neue Legacy-/QSLadmission MUST eine gemeinsame atomare Expositionsregistrierung verwenden. Direkte SQLports und alte Writer dürfen diese Barriere nicht umgehen; ohne abgeschlossenen Backfill/Kompatibilitätsguard startet kein neuer QSLbatch.

Verbindliche Prüfdetails: Forschungsberichte SHALL vorab geplante und tatsächliche Kandidatenstatuszahlen, Policy, frühere Runden einschließlich Fehlversuchen und Wiederverwendung sämtlicher ausgewerteter Phasenrollen nach research-protocol.md offenlegen. Admission MUST den Vorgängerstand atomar lesen/registrieren; abgebrochene/verworfene Runden dürfen nicht gelöscht oder als frische Tests umgedeutet werden. Aktuelle Warnungen MUST von unveränderlich gebundenem Kenntnisstand getrennt bleiben. Externe Kenntnis MUST ausdrücklich unbekannt bleiben; fehlende registrierte Treffer dürfen kein Unabhängigkeitszertifikat erzeugen.

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
