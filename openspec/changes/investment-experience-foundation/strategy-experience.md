# Strategy Experience

## Begriffe und bestehende Fähigkeit

Eine **Idee** formuliert eine Forschungsfrage und behält Original, Interpretation, Version und Unsicherheit. Eine **Strategiedefinition** ist eine registrierte deterministische Regel mit Parametern und erlaubten Instrument-/Datenbedingungen. Ein **Experiment** bindet konkrete Definitionen, Parameter, Phasen, Kosten und Forschungsmodus. Eine **Batch/Run** ist eine Ausführung; ein **Report** ist gebundene Ergebnisevidenz. Eine **AI-Aufgabe** ist das geplante verständliche Ziel-/Fortschrittsbild über diesen vorhandenen Fachobjekten, keine neue Executionautorität. Keine dieser Bezeichnungen verleiht Paper- oder LIVE-Rechte.

Belege: `research/quant-lab/quant-lab-builder.component.html` zeigt den bestehenden Gridaufbau und u. a. `ema-cross-v1`, `sma-cross-v1`, `rsi-mean-reversion-v1`, außerdem Strict PIT und explizite retrospektive Wahl. Verfügbarkeit im UI muss dennoch aus dem aktuellen Katalog kommen, nicht aus dieser beispielhaften Liste. `research/agent-companion.component.ts:108` zeigt die vorhandenen fünf Testbarkeitsklassen. Contracts enthalten `getQuantLabStrategies`, Experiment/Validation/Batch/Comparison/Results sowie Agentideen, Dialoge, Freigaben und Reports. Portfoliofähigkeiten, allgemeine Marktdeckung oder Earningszugang sind daraus nicht ableitbar.

## Von Discovery zum verständlichen Detail

Strategy Discovery präsentiert fachliche These, Regeltyp, benötigte Daten, verfügbare Instrumente, Forschungsmodus und konkrete Einschränkungen. Sortierung darf Interessen aufnehmen, aber keine unbelegte erwartete Rendite oder persönliche Geeignetheit behaupten. Fehlende Ergebnisse heißen „noch nicht ausgewertet“, Datenblocker „Voraussetzung fehlt“; beides ist keine negative Performance. Vergleich stellt dieselben Kriterien nebeneinander und kennzeichnet inkompatible Datenstände/Modi vor Renditezahlen.

Strategy Detail ordnet Inhalte in dieser Reihenfolge: Frage/These, Signal und Entscheidungsregel, gehaltene Position/Long-Flat-Grenzen, Parameter, Datenanforderungen, Kosten und Annahmen, vorhandene versionierte Ergebnisse, Grenzen und nächster zulässiger Schritt. Lesbare Texte werden an Katalog-ID und Version gebunden. Fachliche Erklärtexte dürfen keine im Katalog fehlenden Ereignisse, Märkte oder Fähigkeiten hinzufügen. Technische IDs, Formeln und Rohparameter sind ergänzend erreichbar. Sprache und Visualisierung verändern keine deterministischen Regeln.

## Testbarkeit und freie Ideen

| Fachklasse | Verständliche Anzeige | Zulässiger nächster Schritt |
| --- | --- | --- |
| `EXACTLY_TESTABLE` | Die bestätigte Idee ist mit vorhandenen Regeln und Daten exakt prüfbar | Konkreten gebundenen Experimententwurf prüfen; aktueller Daten-/Gatecheck bleibt nötig |
| `TESTABLE_WITH_DECLARED_APPROXIMATION` | Eine ausdrücklich beschriebene Ersatzhypothese ist prüfbar | Bedeutungsabweichung anzeigen und getrennt akzeptieren lassen; Original erhalten |
| `REQUIRES_NEW_STRATEGY` | Für diese Idee fehlt eine registrierte Regel | Begründung und benötigte Erweiterung dokumentieren; keinen dynamischen Code ausführen |
| `INSUFFICIENT_DATA` | Nötige Daten oder Zulässigkeitsnachweise fehlen | Fehlende Quelle, Umfang oder Nachweise erklären; vorhandenen Readinessweg anbieten |
| `UNSUPPORTED` | Derzeit innerhalb der freigegebenen Fähigkeiten nicht unterstützt | Grenze nennen und lokal weiterklären oder bewusst verwerfen |

„Schlechte Techquartalszahlen → Überreaktion → Erholung“ braucht Ereignis-/Erwartungsdaten, definiertes Universum, Trigger, Fenster und Vergleich. Vorhandene ETFbars oder ein RSI-Grid bilden das nicht exakt ab. Eine preisbasierte Alternative ist nur nach expliziter semantischer Neufassung eine eigene Hypothese. APF-Q-T03/T05/T19 und die ursprünglichen Versions-/Entscheidungsbindungen bleiben maßgeblich. Kein Marketingtext verkürzt „mit Approximation testbar“ zu „bestätigt“.

## Experiment und Ergebnis

Der Weg zum Experiment zeigt Definition/Version, Instrument, Intervalle, Phasen einschließlich unabhängiger OOS-/Holdoutanteile, Datenstand und Kostenannahmen. Strict PIT bleibt der voreingestellte Modus. Retrospektive Exploration ist eine eigene bewusste Entscheidung mit dauerhaft sichtbarer Bezeichnung; heutige Backfills werden dadurch nicht historisch PIT-gültig. Daten-, Kalender-, Kapitalmaßnahmen-, Rechte- und Verfügbarkeitsgrenzen werden nicht hinter Performance versteckt.

Validierung und ausdrücklicher Start bleiben getrennt. Ein zuvor gültiger Entwurf verleiht bei geänderter Daten-/Definition-/Freigabebindung keine Startberechtigung. Aktuelle Grenzen oder deaktivierter Dispatch werden vor Ausführung erklärt. Runstatus wird als Backendzustand mit letzter Bestätigung gezeigt; UI erfindet keine Prozentfertigstellung. Unklare Antwort führt zur bestehenden gebundenen Operation, nicht zu einem neuen Experiment auf Verdacht.

Ein Ergebnis zeigt zuerst, was geprüft wurde und wie belastbar es ist; dann tatsächliche Rendite-/Drawdown-/Handelsmetriken und Kosten, Phasen, Baselines sowie verfügbare Equity-/Drawdownreihen. Nicht berechenbare Kennzahlen sind begründet unbekannt. Ein vollständig negativer Test bleibt ein abgeschlossenes Ergebnis, ein fehlgeschlagener Run bleibt ohne bestätigte Aussage. Cross-Batch-Vergleich zeigt Unterschiede in Regeln, Zeiträumen, Instrumenten, Datenrevision, Kosten, Modus und Auswertung; inkompatible Reihen werden nicht zu einer Rangliste vermeintlich bester Strategien verschmolzen.

## Keine implizite Strategiepromotion

Es gibt in diesem Change kein automatisches Strategieregister mit Produktionspromotion, keine Selbständerung von Code, keinen Agentenbroker und keine Livefähigkeit. Strategy Detail und AI Task Detail können auf einen getrennten menschlichen Paperweg verweisen; vorhandene Paperstrategie muss unabhängig tatsächlich zulässig sein. Ein erfolgreicher Backtest ist weder Budgetbestätigung noch Kontoprüfung, Reconciliation oder Riskfreigabe. Parameter und Annahmen werden nicht still in einen Paperplan kopiert.

## API- und UI-Grenze

Geplant ist `/api/v1/experience/strategies` als additive verständliche Katalog-/Detailprojektion, nicht als neue Strategieengine. Vorhandene Quant-/Backtest- und Agentkommandos bleiben auf ihren tatsächlichen `/api/v1/research/...`-Pfaden bestehen; die Bezeichnung „Agentkommando“ erfindet keinen `/agents/...`-Endpoint. Alle zusätzlichen Experienceverträge müssen durch OpenAPI, generierten Client und negative Contractfälle belegt werden. UI-state enthält Filter und Darstellung; Hypothesenannahmen, Testbarkeit, Risk und Brokerlogik bleiben serverseitig.

Abnahmebeispiele: keine Providerdaten vorhanden; Filter ohne Treffer; alte Strategieversion; freie Earningsidee ohne Daten; ausdrücklich angenommene Ersatzhypothese; Strict-PIT blockiert/retrospektiv explorierbar; negatives OOS-Ergebnis; fehlende Reliabilityseite; konkurrierende Änderung; unbekannte Startantwort; Reload desselben Ergebnisses. In allen Fällen bleiben Original, Quelle, Version, Unsicherheit und nächster zulässiger Schritt sichtbar.

## Übergabe an Guided Paper Order

Nach Erklärung und Belegsicht zeigt S6 einen eigenen Eignungsschritt mit konkreter Strategieversion, Instrument, Eignungsrevision, Prüfzeit und Grund. Die Überschrift des historischen Berichts benennt ausdrücklich den historischen Zeitraum. Werte aus dem Bericht übernehmen keine Richtung, Menge oder Preise. Maßgeblich ist die serverseitig belegte Kompatibilität von Version und Symbol mit vorhandener Paper-Konfiguration und Capability. Fehlende Zuordnung sperrt den Weg ohne Profiländerung oder Promotion. Nur der bewusst gewählte, aktuell belegte Übergang öffnet den manuellen Weg mit den Kontextreferenzen aus [Guided Order Design](guided-order-design.md). Widerruf, Versionswechsel und fehlende Evidenz sperren den Übergang mit Rückweg zu S6.
