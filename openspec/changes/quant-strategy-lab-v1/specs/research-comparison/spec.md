# Spec Delta

## Purpose

Diese Fähigkeit stellt methodisch begrenzte Forschungsergebnisse mit vergleichbaren Grundlagen, erklärten Kennzahlen und dauerhaften authentifizierten Ansichten dar.

## ADDED Requirements

### Requirement: REQ-QCO-001 Explizite Vergleichbarkeit
Vergleiche SHALL vollständige gemeinsame Fingerprints einschließlich Strategy Selection Policy nach D6 prüfen. Nur vergleichbare vollständige Validationresultate dürfen eine Rangliste bilden. Reliability, aktuelle Warnungen, Metrikstatus und Policyzulässigkeit MUST getrennt sichtbar bleiben; OOS bleibt separat und gültige Cashresultate sichtbar.

#### Scenario: T-QCO-001 Abweichende Basis oder Teilfehler
WHEN Snapshot, Kosten oder Phase abweichen oder ein Kandidat/Fold unvollständig ist THEN entsteht INCOMPARABLE beziehungsweise INCOMPLETE mit Gründen statt eines unmarkierten gemeinsamen Rankings. Kurven verschiedener zurückgesetzter Folds werden nicht zu einer Portfolioequity verkettet. Ein fehlgeschlagener Kandidat wird nicht still aus der Auswahlmenge entfernt.

Verbindliche Prüfdetails: Vergleiche SHALL Snapshot, Zeit-/Phasenprotokoll, gemeinsamen Warm-up, Modus, Instrument/Feed, Kosten/Fill, Benchmark, Auswahlpolicy einschließlich vorab gebundener Zulässigkeitsgrenzen und Methodenfingerprints prüfen. Nur vollständig vergleichbare Resultate dürfen dieselbe Validationrangliste bilden. Reliability und aktuelle Warnungen MUST sichtbar bleiben. OOS-Ergebnisse nur gewählter Kandidaten MUST von Validationvergleichen getrennt sein. Metrikstatus, Warnung und Policyzulässigkeit MUST getrennt ausgewiesen sein; gültige Cashresultate dürfen nicht still unterdrückt werden.

### Requirement: REQ-QCO-002 Definierte Kennzahlen und Nichtauswertbarkeit
Berichte SHALL die phasegetrennten Kennzahlen und Status aus metrics-methodology.md deterministisch berechnen. Nichtauswertbares MUST null mit Grund sein. Selektion MUST Decimal38-Werte vor Darstellungsrundung verwenden und exakte kanonische selectionValues samt finalen Medianen publizieren. Legacywerte MUST kompatibel bleiben; statistische Gewissheit darf nicht behauptet werden.

#### Scenario: T-QCO-002 Numerische Randfälle
WHEN Varianz null, die Stichprobe zu klein oder Equity nicht positiv ist THEN liefern betroffene Kennzahlen erklärte N/A-Status statt künstlicher Null, NaN oder Infinity. Null Fills lassen valide price-only Nettorendite/Drawdown und Exposure0 zu, erzeugen jedoch keine Trade-Win-Rate. Unabhängige Referenzen prüfen negative Returns, Gebühren, Foldreset, unregelmäßige Frequenz, 19/20 vollständige Sitzungen, partielle Randsitzungen, ungültige Ledgerzustände und zwei identisch gerundete Werte auf verschiedenen Seiten einer Zulässigkeitsgrenze.

Verbindliche Prüfdetails: Berichte SHALL Bestandsmetriken und die exakt definierten v1-Erweiterungen aus metrics-methodology.md berechnen. Jede Metrik MUST Methode, Frequenz, Beobachtungszahl und Status mitführen; nicht auswertbare Werte sind null mit Reason Code. Decimalberechnung/Rundung und Kostenbehandlung MUST deterministisch sein; statistische Kennzahlen dürfen keine Gewinnwahrscheinlichkeit behaupten. Return, Drawdown, Kosten, Turnover, Aktivität und Exposure MUST pro unabhängig gestarteter Phase getrennt bleiben. Auswahlgrenzen und Ties MUST ungerundete Decimal38-Werte nutzen; auf12 Stellen ausgegebene Werte dürfen weder Zulässigkeit noch Rangfolge verändern. Exakte kanonische selectionValues einschließlich finaler Komponentenmediane MUST zusätzlich nachvollziehbar publiziert werden; der Bestandsrechner und dessen zwölfstellige Legacywerte bleiben kompatibel.

### Requirement: REQ-QCO-003 Versionierte authentifizierte Researchverträge
Die additiven versionierten Researchverträge SHALL sämtliche Ressourcen und Fachzustände aus D7 einschließlich Policy, Nichtwahl, Aktivität und Forschungsrollen abbilden. Authentifizierung, Origin/CSRF, Idempotenz und gebundene Pagination MUST vor Seiteneffekten gelten. Bestehende API/Altmanifeste MUST kompatibel bleiben; der Client wird ausschließlich generiert. Vertragsimplementierung benötigt einen gesonderten Apply-Auftrag.

#### Scenario: T-QCO-003 Vertrag und Angriff
WHEN Sitzung fehlt, Origin fremd, Draftversion veraltet, Cursor fremd oder Idempotenznutzlast abweichend ist THEN entsteht kein neuer Auftrag; stabile 400/401/403/409/422/429/503-Fehler entsprechen dem kanonischen Vertrag. Zwei Clientgenerierungen sind bytegleich und Breakingchecks zeigen keine Bestandsbrüche.

Verbindliche Prüfdetails: Katalog, Entwurfsversion, Validierung, expliziter Start, Status, Kandidatenergebnisse und Vergleich SHALL über additive versionierte Verträge gemäß design.md D7 nutzbar sein. Geplante Entwurfs-/Validierungsverträge MUST die geschlossene Auswahlpolicy und deren vorab gebundene Gates enthalten; Ergebnisverträge MUST Exposure/Aktivität, vollständige Bar-/Sitzungszahlen, Warnungen, Zulässigkeitsgründe und NO_ELIGIBLE_CANDIDATE ohne fingierten Sieger darstellen können. Authentifizierung, Origin/CSRF, Idempotenz und gebundene Pagination MUST vor Seiteneffekten gelten. Bestehende API/Altmanifeste MUST kompatibel bleiben; der Angularclient wird ausschließlich generiert. Die Vertragsimplementierung erfolgt erst nach gesondertem Apply-Auftrag.

### Requirement: REQ-QCO-004 Material-Forschungsworkflow
Die Materialoberfläche SHALL den vollständigen Workflow aus D7 und research-protocol.md samt Policy/Gates, Phasenrollen, Aktivität/Exposure, Wiederverwendung und Einschränkungen verständlich zeigen. Finanzberechnung MUST serverseitig bleiben. Reload MUST Zugriff erhalten; Sessionverlust MUST Polling und sitzungsgebundene Zustände beenden. Nichtwahl und Aktivitätswarnungen MUST ohne Ersatzsieger darstellbar sein.

#### Scenario: T-QCO-004 Echter Reload und Sessionverlust
WHEN ein echter PG/API/Workerbatch über den Browser gestartet und die Seite neu geladen wird THEN sind derselbe Batch und paginierte Reports wieder erreichbar. Nach Sessionverlust stoppen alle Polls einschließlich Reportdialog und spätere Antworten können keine alten sensitiven Zustände wiederherstellen. Builderänderungen invalidieren Validierung und Startbindung.

Verbindliche Prüfdetails: Die Oberfläche SHALL Katalog, Builder mit Kandidaten-/Budgetvorschau, Start, dauerhafte Übersicht sowie Equity-/Drawdown-, Metrik-, Sensitivitäts- und Foldansichten bieten. Modus, Auswahlstatus, Wiederverwendung, Reliability und Einschränkungen MUST sichtbar sein. Der Builder MUST MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1 voreinstellen, eine explizite Drawdowngrenze und minValidationSessions verlangen und die alternative Drawdown-first-Policy nur als ausdrückliche Wahl anbieten. Optionale Aktivitätsgrenzen MUST erkennbar deaktivierbar sein. Resultate MUST Return/Drawdown neben investiertem Baranteil, durchschnittlicher/maximaler Bruttoexposure, tatsächlichen Fills/Entries/Exits und offenem Endbestand zeigen; finanzielle Exposure und Wiederverwendung von Testdaten dürfen nicht verwechselt werden. Warnungen und INELIGIBLE-Gründe MUST getrennt lesbar sein; NO_ELIGIBLE_CANDIDATE zeigt die konkreten Gründe ohne Ersatzsieger. Finanz-/Indikatorberechnung MUST serverseitig bleiben; Reload erhält Zugriff, Sessionverlust beendet Polling und entfernt sitzungsgebundene Zustände.

### Requirement: REQ-QCO-005 Ledgergebundene Exposure und Aktivität
Analytics SHALL tatsächliche Fills und Barclose-Positionen gemäß metrics-methodology.md phaseweise auswerten: investierte Bars, Bruttoexposure, Fillseiten, Entries/Exits und offener Endbestand. Baranteile MUST ohne Kalenderzeitbehauptung bleiben. Fehlende/widersprüchliche Evidenz MUST INCOMPLETE/INVALID_INPUT statt Nullfüllung ergeben. Offene Positionen MUST mark-to-market bleiben; Roundtripmetriken sind ausgeschlossen.

#### Scenario: T-QCO-005 Tatsächliche Aktivität, offene Position und leere Cashphase
WHEN die unabhängige Fünf-Bar-Referenz BUY5/SELL5 mit zwei investierten Closes aus metrics-methodology.md ausgewertet wird THEN entstehen investedBarRatio0.400000000000, meanGrossExposureRatio0.194736842105, maxGrossExposureRatio0.500000000000, entryCount1, exitCount1 und endOpenPositionCount0. Ohne SELL entstehen drei investierte Bars, ein offener Endbestand und kein erfundener Exit. Eine vollständige Cashphase zeigt gültige Nullen mit NO_FILLS/NO_EXPOSURE; eine unvollständige Phase zeigt keine scheinbar gültigen Nullen. Vorab aktivierte Mindestaktivität kann die Cashphase ausschließen, deaktivierte Mindestaktivität lässt ihre sonstige Policyprüfung bestehen. Kein Test-/Holdoutwert verändert diese Prüfung.

Verbindliche Prüfdetails: Analytics SHALL ausschließlich tatsächliche Fills und Barclose-Positionen des bestehenden Long/Flat-Ledgers nach metrics-methodology.md verwenden. investedBarCount, investedBarRatio, meanGrossExposureRatio, maxGrossExposureRatio, buyFillCount, sellFillCount, entryCount, exitCount und endOpenPositionCount MUST vollständig definiert und pro Phase ausgewiesen sein. Eine LONG-Entscheidung ohne Fill MUST keine Entryzählung erzeugen. Baranteile MUST gleiche Barbeobachtungen gewichten und dürfen keine Kalenderzeitexposure behaupten. Fehlende Bars, unbekannte Fillevidenz oder widersprüchliche Mengenfortschreibung MUST erklärte INCOMPLETE/INVALID_INPUT-Werte erzeugen; keine Nullfüllung und kein zweites Finanzledger. Offene Endpositionen MUST mark-to-market bleiben; Roundtripzahl, Win Rate und Profit Factor bleiben zurückgestellt.
