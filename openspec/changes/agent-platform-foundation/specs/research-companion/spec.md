## Purpose

Überführt Nutzerideen und evidenzgebundene Vorschläge in versionierte Hypothesen, prüfbare Experimente und verständliche Berichte.

## ADDED Requirements

### Requirement: APF-Q01 Versionierte Forschungsidee

Das System SHALL Forschungsideen unabhängig von Strategien mit stabiler ID, unveränderlichen Inhaltsversionen und nachvollziehbaren Entscheidungen speichern. Originalformulierung und Interpretation SHALL getrennt bleiben. Statusübergänge und Verweise SHALL serverseitig geprüft werden. Änderungen mit veralteter Basisversion MUST scheitern.

#### Scenario: APF-Q-T01 Original und Interpretation bleiben nachvollziehbar

- **WHEN** ein Betreiber eine Vermutung eingibt und anschließend eine abweichende Konkretisierung ausdrücklich übernimmt
- **THEN** bleiben Original, bisherige Hypothese und Annahmen lesbar und die neue Version verweist auf Vorgänger und Entscheidung

#### Scenario: APF-Q-T02 Keine vorgezogene Testbestätigung

- **WHEN** ein veralteter Dialogstand einen Entwurf ändern will oder ein Modell eine gestartete, fehlgeschlagene oder unverknüpfte Batch als Beweis nennt
- **THEN** wird die konkurrierende Mutation abgewiesen und die Idee erhält keinen TESTED-Übergang

#### Scenario: APF-Q-T12 Vollständige Ideenbindung

- **WHEN** eine Idee gespeichert oder versioniert wird
- **THEN** enthält die Inhaltsversion Vorgänger, Titel, verständliche Beschreibung, strukturierte Hypothese, Original oder Quellverweis, getrennte Interpretation, offene Fragen, Herkunft USER oder AGENT, Initiator, ggf. Agent-Run- und Dialogreferenz, UTC-Erstellung, Beobachtungszeitraum, Instrumente, revisionierte Evidenz, Qualitätsbefunde, Annahmen, Unsicherheiten, begründete Testbarkeit, vorgeschlagene Entwürfe, zugehörige Experimente und verwandte Ideen
- **AND** Signal, Zielgröße, Auswertungszeitraum, Baseline und Auswertungskriterium sind gebunden oder ausdrücklich offen und referenziell geprüfte Ereignisse machen Verweise, Status und Entscheidungen historisch rekonstruierbar

#### Scenario: APF-Q-T13 Zulässige Lebenszyklusübergänge

- **WHEN** eine ResearchIdea verarbeitet wird
- **THEN** AGENT beginnt intern in DISCOVERED und erreicht PROPOSED erst nach validierter Veröffentlichung, USER beginnt in PROPOSED
- **AND** der Betreiber kann PROPOSED als REVIEWED annehmen oder verwerfen und REVIEWED, EXPERIMENT_DRAFTED, BLOCKED und UNSUPPORTED verwerfen
- **AND** erfolgreiche Validierung führt nur von REVIEWED zu EXPERIMENT_DRAFTED und ein vollständig abgeschlossener zugehöriger Ergebnisbericht führt nur von EXPERIMENT_DRAFTED zu TESTED, auch bei negativer Schlussfolgerung
- **AND** die deterministische Prüfung kann DISCOVERED, PROPOSED, REVIEWED oder EXPERIMENT_DRAFTED bei behebbaren Voraussetzungen zu BLOCKED, bei grundsätzlich fehlender Testbarkeit zu UNSUPPORTED überführen
- **AND** eine erneute Prüfung von BLOCKED, UNSUPPORTED, REJECTED oder TESTED arbeitet auf einer Nachfolgeversion und eine erneute Empfehlung zu REJECTED benötigt neue begründete Evidenz
- **AND** alle sonstigen Übergänge werden abgelehnt und kein Status verleiht Handels- oder Promotionsrechte

### Requirement: APF-Q02 Mehrstufiger Dialog mit bestätigten Annahmen

Der Companion SHALL komplexe freie Forschungsfragen in einem begrenzten, wiederaufnehmbaren Dialog verarbeiten. Externe semantische Verarbeitung MUST APF-P04 und APF-P07 erfüllen. Ohne wirksame Sendefreigabe SHALL eine lokale strukturierte Klärung verfügbar sein. Fachlich bindende Annahmen MUST aus expliziten Betreiberentscheidungen hervorgehen.

#### Scenario: APF-Q-T03 Freie komplexe Alltagshypothese

- **WHEN** ein Betreiber ohne Strategievokabular einen Text zu schlechten Quartalszahlen, einer vermuteten Überreaktion bei Technologieaktien und einer späteren Erholung ausdrücklich zur semantischen Auswertung freigibt
- **THEN** erkennt der Companion Ereignis, Überreaktion und Erholung als unterschiedliche Elemente, schlägt eine verständliche und strukturierte Lesart vor und fragt gezielt nach fehlenden Definitionen für Ereignis, Zielgröße, Messfenster oder Vergleich
- **AND** der erforderliche Datentyp für Quartalsereignisse bleibt sichtbar und fehlende Quellen werden lokal erklärt; die Antwort schlägt keine beliebige technische Regel als exakten Test vor

#### Scenario: APF-Q-T04 Wiederaufnahme mit widersprüchlichem Folgebeitrag

- **WHEN** der Betreiber nach einem Neustart oder erneuter Anmeldung eine bestätigte Hypothese öffnet und ein neuer Beitrag der bisherigen Annahme widerspricht
- **THEN** bleiben bestätigte Annahmen und Hypothesenversion abrufbar; der Companion zeigt die Abweichung und fragt nur die betroffenen Punkte erneut ab
- **AND** ein Replay desselben Turns erzeugt weder eine zweite Entscheidung noch ein neues Experiment

#### Scenario: APF-Q-T14 Getrennte lokale Persistenz und Modellverarbeitung

- **WHEN** der Betreiber einen neuen Turn einreicht
- **THEN** erfolgt vor jeder Persistenz eine konservative lokale Prüfung; unzulässige oder unklare Eingaben werden verworfen, ohne Inhalt in Antwort, Audit, Fehlerprotokoll oder Outbox zu übernehmen
- **AND** nur zur lokalen Speicherung klassifizierter Originaltext bleibt geschützt beim Betreiber. Dauerhaft gebunden werden Turn, Versionsreferenzen, sichere strukturierte Vorschläge, Bestätigung und redigierte Freigabenachweise
- **AND** die freigegebene Sendefassung bleibt eine befristete, gesonderte Projektion. Providerprompt, Sendetext und Rohantwort gehören weder in dauerhafte Dialog- noch in Auditdaten
- **AND** der Kontext ist begrenzt und jede fortgeführte Annahme an ihre Bestätigung gebunden. Alte Dialogtexte und freie Zusammenfassungen werden nie automatisch an den Provider übertragen

#### Scenario: APF-Q-T19 Lokale Klärung ohne Provider

- **WHEN** ein Betreiber die Übertragung ablehnt oder der Dispatch vor Versand an Klassifikation, Berechtigungen, Budget oder Providerstatus scheitert
- **THEN** erfolgt kein externer Versand und die Oberfläche bietet einen lokalen Weg mit gezielten Fragen zu Ereignis, Instrument, Signal, Zielgröße, Zeitraum und Vergleich
- **AND** der Companion kennzeichnet die strukturierte Klärung als solche, zeigt ungeklärte Elemente und erlaubt die Bestätigung eines vollständigen lokalen Entwurfs. Nach einem Providerfehler mit unklarem Versandstatus bleibt der lokale Weg verfügbar und der Status wird gemäß APF-R04 angezeigt

#### Scenario: APF-Q-T20 Semantische Änderung und gezielte Bestätigung

- **WHEN** der Betreiber das auslösende Ereignis oder den Messhorizont nach einer Bestätigung ändert
- **THEN** zeigt der Companion den Unterschied vor einer neuen Bestätigung und erzeugt nach expliziter Annahme eine verknüpfte Nachfolgeversion mit eigener Entscheidung
- **AND** geänderte Bedeutungen übernehmen keine früheren Bestätigungen. Unveränderte Annahmen bleiben mit ihren Quellen nachvollziehbar. Alte Ideenfassungen und Quant-Entwürfe behalten ihre historische Bindung

### Requirement: APF-Q03 Geschlossene Testbarkeitsbewertung

Das System SHALL Testbarkeit deterministisch aus versioniertem Katalog, Hypothesenabbildung und Datenzulässigkeit begründen. Eine Approximation MUST vor Start getrennt akzeptiert und an die Ideenfassung gebunden sein. Unvollständige Ideen bleiben in Klärung. Nicht registrierte Strategien und dynamischer Strategiecode MUST abgewiesen werden.

#### Scenario: APF-Q-T05 Abweichende Signalsemantik

- **WHEN** eine Hypothese genau drei Verlusttage und anschließend zwei Tage Folgerendite erfordert und der Katalog nur einen RSI-Regeltest ermöglicht
- **THEN** ist dieser RSI-Test höchstens eine ausdrücklich beschriebene Ersatzhypothese und ohne passende Abbildung oder akzeptierte Ersatzhypothese bleibt die Originalidee erhalten und ungestartet

#### Scenario: APF-Q-T06 Fehlende Rechte, Daten oder Strict-PIT-Nachweise

- **WHEN** die Strategie abbildbar ist, aber historische Verfügbarkeit oder Datenrechte für den gewählten Test fehlen
- **THEN** erhält die Idee INSUFFICIENT_DATA mit Begründung; der Companion stellt keine unbelegten Daten bereit und wechselt den Research Mode nicht automatisch

#### Scenario: APF-Q-T15 Geschlossene Klassifikation

- **WHEN** eine vollständige Hypothese bewertet wird
- **THEN** bedeutet EXACTLY_TESTABLE eine vollständige Abbildung von Signal, Horizont, Zielmaß, Vergleich und Constraints im versionierten Katalog und Quant-Protokoll
- **AND** TESTABLE_WITH_DECLARED_APPROXIMATION bedeutet eine unterstützte Ersatzhypothese mit ausgewiesener Abweichung vom Original und ihren Folgen
- **AND** REQUIRES_NEW_STRATEGY bedeutet eine quantifizierbare Frage im Forschungsumfang ohne passende aktuelle Strategie oder tragfähige gewählte Approximation
- **AND** INSUFFICIENT_DATA bedeutet eine abbildbare Hypothese mit fehlender, qualitativ ungeeigneter oder unzulässiger Datenbasis und UNSUPPORTED bedeutet eine Anforderung außerhalb des Forschungs- oder Sicherheitsumfangs
- **AND** jede Einstufung zeigt stabile Gründe, belegte Einschränkungen und Optionen; offene Pflichtfelder lösen eine begründete Rückfrage aus und erhalten keinen testbaren Status

### Requirement: APF-Q04 Geführter und fortgeschrittener Quant-Lab-Ablauf

Guided und Advanced SHALL dieselben Quant-Lab-Definitionen, Verträge, Berechnungen und Sicherheitsgrenzen verwenden. Der Companion MUST gültige Expertenvorgaben erhalten. Ein Experimentstart SHALL nur mit einer konkreten versionsgebundenen Freigabe über die vorhandene Admission erfolgen. Proaktive Läufe haben keinen Startpfad.

#### Scenario: APF-Q-T07 Durchgehender Guided-Ablauf

- **WHEN** ein Betreiber eine abbildbare Idee bestätigt, den gebundenen Entwurf erfolgreich validiert und genau diesen Start freigibt
- **THEN** wird ein Quant-Lab-Batch über die bestehende Admission angelegt, der reguläre Worker berechnet ihn und der Companion ordnet den gespeicherten Bericht derselben Idee und Batch zu

#### Scenario: APF-Q-T08 Expertenvorgaben und Ansichtswechsel

- **WHEN** ein Betreiber gültige Strategieversion, Grid, Phasen und Policy vorgibt und nach Validierung zwischen Guided und Advanced wechselt
- **THEN** sind kanonische Definition und Validierungsbindung identisch; nicht unterstützte Parameter werden mit Grund abgelehnt, anstatt durch Modellvorschläge ersetzt zu werden

#### Scenario: APF-Q-T16 Gleiche fachliche Bindung in beiden Ansichten

- **WHEN** der Betreiber einen Entwurf vor einer Startentscheidung prüft
- **THEN** erklärt Guided Hypothese, Daten, Strategie, Parameter, Annahmen, Approximation, Aufwand, Kosten und methodische Grenzen und Advanced zeigt dieselbe Definition samt Versionen, Parametern, Grenzen, Research Mode, Selection Policy, Zeitprotokoll, Manifest und vollständigen serverseitigen Metriken
- **AND** beide Ansichten nutzen dieselben Research-Datenprüfungen, die vorhandene Strategy Registry und bestehende Quant-Lab-Funktionen für Entwurf, Validierung, Admission, Ergebnisabruf und Vergleich; Finanzrechnung erfolgt serverseitig im Quant Lab
- **AND** die Runtime bindet Freigabe an validierten Entwurf, akzeptierte Annahmen und ggf. Approximation, Research Mode und Ressourcenbudget und die Admission prüft ihre Ressourcen-, Qualitäts- und Reuse-Gates
- **AND** der bestehende direkte Betreiberpfad behält seine eigenen Sicherheits- und Validierungsregeln und steht dem Agenten nicht als Ersatzpfad zur Verfügung

### Requirement: APF-Q05 Forschungshistorie und vergleichbare Evidenz

Der Companion SHALL vor dem Entwurf verwandte Ideen, exakte Wiederholungen und einschlägige Experimente ausweisen. Er SHALL Wiederverwendung anhand der Quant-Lab-Überlappungsprüfung aller Performancephasen vor Start sowie des bei Admission gebundenen Registerstands zeigen. Quant-Lab-Vergleichsregeln bleiben maßgeblich.

#### Scenario: APF-Q-T09 Wiederverwendeter Holdout

- **WHEN** ein Betreiber eine umbenannte Hypothese mit revidierten Daten und einem bereits früher als Holdout ausgewerteten Zeitraum einreicht
- **THEN** zeigt das System die alte und neue Phasenrolle, Überlappung, Registerbindung und Unabhängigkeitsgrenze; ein besseres Ergebnis gilt nicht als neue unabhängige Bestätigung

#### Scenario: APF-Q-T17 Register und Vergleichsgrenzen

- **WHEN** eine neue Idee bewertet oder ein Bericht angezeigt wird
- **THEN** erkennt eine versionierte Signatur der strukturierten Hypothese exakte Wiederholung, während Textähnlichkeit lediglich auf Verwandtschaft hinweist
- **AND** spätere Reuse-Warnungen bleiben sichtbar, fehlende registrierte Vornutzung sagt nur etwas über das lokale Register aus und externe Vorkenntnis bleibt unbekannt
- **AND** neue Namen, Datenrevisionen und Research Modes erzeugen keine unabhängigen Fenster; ungeeignete Vergleiche zeigen einen erklärten Ausschlussgrund

### Requirement: APF-Q06 Persistierter und belegbarer Forschungsbericht

Das System SHALL einen unveränderlichen, versionierten und evidenzgebundenen Bericht speichern. Ergebnisbehauptungen MUST serverseitig gegen persistierte Resultate validiert werden. Der Bericht SHALL Fakten, Berechnung, Interpretation, Hypothese und Unbekanntes trennen. OOS und Holdout dürfen keine neue Auswahl oder automatische Folgeexperimente auslösen.

#### Scenario: APF-Q-T10 Ungültiger oder unbelegter Modellbericht

- **WHEN** ein Modell eine nicht existierende Experiment-ID, einen fehlenden Sharpe-Wert oder einen falsch dargestellten Holdout referenziert
- **THEN** erscheint keine dieser Behauptungen im veröffentlichten Bericht; belegte Fakten und fehlende Auswertbarkeit bleiben sichtbar und die Ablehnung ist auditierbar

#### Scenario: APF-Q-T11 Persistierter Bericht nach erneutem Öffnen

- **WHEN** eine gültige Batch einen in Validation gewählten Kandidaten und ein negatives Holdout-Ergebnis geliefert hat und der Betreiber neu lädt
- **THEN** zeigt derselbe Bericht den gewählten Kandidaten, die getrennten Phasen, die negative Beobachtung und ihre Grenzen mit den ursprünglichen Resultatverweisen; weder Auswahl noch Run-Anzahl ändern sich

#### Scenario: APF-Q-T18 Belegbare Phasen und Berichtsfelder

- **WHEN** der Companion ein abgeschlossenes Experiment berichtet
- **THEN** bindet der Bericht Ideen-, Entwurfs-, Batch- und Reportversion, Forschungsfrage, Hypothese, Annahmen, Datenbasis, Quellen und Evidenz, Strategieversionen, Experiment und Batch, Manifest, Parameter, Selection Policy, Kennzahlen, Aktivität, Drawdown, Gebühren, Benchmarks, Reliability, Wiederverwendung, Auswahlverzerrung, nicht überprüfbare Aussagen, Schlussfolgerung und weitere Forschungsoptionen
- **AND** In-Sample-Analyse, Validation zur Auswahl, Walk-forward-OOS und finaler Holdout bleiben getrennt; Nichtwahl, fehlende oder nicht auswertbare Phasen, unzulässige Vergleiche und Retrospective-Grenzen werden kenntlich gemacht
- **AND** der Server rendert quantitative Aussagen nur über autorisierte persistierte Referenzen mit Feldpfad, Phase, Kandidat oder Referenz, Einheit und Manifestbindung und prüft Richtungsbehauptungen und Vergleiche anhand der gebundenen Werte
- **AND** bei ungültiger Interpretation bleibt der Faktenbericht mit erklärter Lücke verfügbar, aktuelle Warnungen erscheinen getrennt und historische Resultate bleiben unverändert
- **AND** der Bericht enthält keine zukünftige Profitabilitätszusage, Handelsaktion oder Strategiepromotion

### Requirement: APF-Q07 Validierte semantische Interpretation

Der Companion SHALL eine geschlossene, versionierte ResearchHypothesisInterpretationV1 als unbestätigten Vorschlag lokal validieren. Die autoritative Testbarkeit MUST erst aus einer bestätigten Hypothese deterministisch nach APF-Q03 folgen. Der Vorschlag SHALL weder Betreiberentscheidungen ersetzen noch Tool-, Daten-, Strategie- oder Startrechte erteilen.

#### Scenario: APF-Q-T21 Begrenzter Vorschlag und ungebundene Werte

- **WHEN** ein Modell eine Antwort zum ausgewählten Turn liefert
- **THEN** bindet der Server die Originalreferenz und aktuelle Dialogversion, prüft Schema, Werte, Herkunft, erlaubte Referenzen und Feldgrenzen. Vor Persistenz oder Anzeige prüft er die Datenpolicy auf sensible oder nicht klassifizierbare Inhalte gemäß APF-P05 und speichert nur einen zulässigen strukturierten Vorschlag
- **AND** der Vorschlag enthält eine verständliche Hypothese, Instrument oder Markt, Triggerereignis, Signaldefinition, Beobachtungsfenster, erwartetes Ergebnis, Messhorizont, Benchmark, erforderliche Datentypen, offene Fragen, ausdrückliche Annahmen, nicht unterstützte Aspekte, Interpretationssicherheit und vorgeschlagene Testbarkeit
- **AND** unbekannte oder nicht unterstützte Elemente bleiben sichtbar und blockieren jede exakte Abbildung. Interpretationssicherheit beschreibt nur die Selbsteinschätzung des Modells und entscheidet weder fachliche Korrektheit noch Testbarkeit

#### Scenario: APF-Q-T22 Gültige Interpretation und unzulässige Aktion

- **WHEN** die gültige Interpretation zusammen mit einem Toolvorschlag oder Startwunsch eintrifft
- **THEN** weist die Plattform die unzulässige Aktion gemäß Toolpolicy ab. Die Interpretation kann als separater Vorschlag übernommen werden, wenn sie ihre eigenen Validierungsregeln erfüllt
- **AND** eine eingeschleuste Aktion innerhalb des Hypothesenobjekts oder ein fremder Bindungsbezug führt zur Ablehnung des Objekts

#### Scenario: APF-Q-T23 Explizite Expertenwerte haben Vorrang

- **WHEN** die Ausgabe einer gültigen vorgegebenen Strategieversion, einem Parameter, Markt, Research Mode oder Zeitprotokoll widerspricht
- **THEN** bleiben die strukturiert gebundenen Expertenvorgaben unverändert. Der Companion kennzeichnet den Konflikt und verarbeitet keine implizite Änderung
