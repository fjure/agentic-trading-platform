# Personas

## Geltung und Evidenz

Diese Personas sind Entwurfsannahmen für `investment-experience-foundation`, keine erhobenen Nutzungsdaten. Sie steuern Priorisierung und Abnahmegespräche; sie verleihen weder Anlageberatung noch Handelsrechte. Ist-Stand: `apps/dashboard/src/app/app.ts` und `app.html` mounten ausschließlich den Supervisor. Dessen Navigation (`supervisor/supervisor.component.ts:105`) beginnt mit Übersicht und technischen Historien; Research wird innerhalb von Übersicht und Alpaca Paper eingebettet (`supervisor.component.html:52`). Die Produktvision beschreibt eine spätere Agentenorganisation, keinen bereits verfügbaren autonomen Vermögensverwalter.

## P1 – Orientierende Anlegerin

**Situation:** Eine Person mit ETF-/Aktiengrundwissen will verstehen, was sie besitzt, wo Unsicherheit liegt und welche sinnvolle Untersuchung als Nächstes möglich ist. Sie kennt keine Run-, Batch- oder Trace-IDs.

**Auftrag:** „Zeig mir meinen bestätigten Paperbestand und hilf mir, eine Idee zu prüfen.“ Erfolg heißt: nach Anmeldung Kontext, Datenalter und nächsten Schritt erkennen; eine Aufgabe ohne Kenntnis technischer IDs öffnen und später wiederfinden. Sie braucht keine ständig sichtbaren Worker- oder Rohdatentabellen.

**Hürden:** Verwechselt Simulation mit Echthandel, Rendite mit Prognose oder einen Modellvorschlag mit einer freigegebenen Aktion. Deshalb zeigen Dashboard und Portfolio dauerhaft Paper/Research, Stand, Quelle und Grenzen. Ein leeres Portfolio lädt zum Einrichten oder Forschen ein; es erfindet kein Vermögen. Budget, Zeithorizont und Interessen bleiben freiwillige Präferenzen und ändern keine Risk Policy.

**Prioritäten:** Guided Onboarding, Personal Dashboard, Portfolio Hub, Portfolio Insights. Primär verständliche Zusammenfassungen, sekundär Belege. Auf Mobilgeräten muss die erste Ansicht dieselbe Sicherheits- und Datenfrischeinformation zeigen. Erfolgskriterium: kann den Unterschied zwischen „keine Positionen“ und „Bestand unbekannt“ sowie zwischen „Idee prüfen“ und „Paperorder bestätigen“ erklären.

## P2 – Forschender Privatanleger

**Situation:** Hat eine freie Vermutung wie „Nach schlechten Quartalszahlen erholen sich überverkaufte Techaktien“ und will die Bedeutung gemeinsam schärfen. Er startet mit einer Frage, nicht mit einem RSI-Parameter.

**Auftrag:** Original erhalten, Ereignis, Universum, Signal, Zielgröße, Zeitfenster und Vergleich klären; exakte Testbarkeit und fehlende Daten ehrlich sehen. Erfolg kann ein begründetes „derzeit nicht prüfbar“ sein. Er braucht einen lokalen Weg ohne externen Modellversand und eine getrennte Freigabe für eine konkrete befristete Sendefassung.

**Hürden:** Ein technischer Proxy könnte als Bestätigung der Originalidee erscheinen. Die UI muss Original, Interpretation und akzeptierte Ersatzhypothese sichtbar trennen. Die vorhandenen Testbarkeitsklassen in `research/agent-companion.component.ts:108` bleiben maßgeblich; neue Sprache ändert die Klassifikation nicht.

**Prioritäten:** AI Task Center/Detail, Strategy Discovery/Detail und wiederaufnehmbare Ergebnisse. Versionen und Belege werden auf Wunsch eingeblendet, fachliche Einschränkungen direkt. Erfolgskriterium: kann vor Start angeben, welche Hypothese und Daten tatsächlich geprüft werden, und nach negativem Resultat ohne erneute Ausführung zur Idee zurückkehren.

## P3 – Technische Expertin und kontrollorientierte Paperbetreiberin

**Situation:** Betreibt bewusst einen getrennten Alpaca-Paperledger und prüft Budget, frische Reconciliation, offene Verpflichtungen und Orderstatus. Sie erwartet auch bei Netzfehlern klare Handlungsfähigkeit.

**Auftrag:** Eine menschlich bestätigte LIMIT-Anforderung genau einmal nachvollziehen, bei Unklarheit dieselbe Verpflichtung wiederfinden, neue Einstiege pausieren und den Kill-Switch erreichen. Erfolg heißt bestätigte Backendzustände und nachvollziehbare Fills, nicht ein optimistisch animierter Kauf.

**Hürden:** Timeout wirkt wie Ablehnung, Cancel wie garantierte Stornierung oder erneutes Anmelden wie Neustart. Die UI zeigt `ungeklärt`, `CANCEL_PENDING`, letzte Bestätigung und bestehende Reservation ausdrücklich. Navigation, Setup und Reload erzeugen niemals Start oder Order.

**Prioritäten:** Guided Paper Order, Portfolio Hub, Activity Timeline, ergänzender Betriebsbereich. Research-Ergebnisse sind keine Paperfreigabe. Erfolgskriterium: nach Reload offene Vorgänge finden und von dort Trace/Order öffnen, ohne neue Anforderung zu erzeugen.

**Advancedbedarf:** Strategieparameter, Forschungsmodi, Revisionen, Experimentprotokolle, Agentenläufe, Providerbudgets, Freigabebindungen und Audit müssen über dieselben Objekte erreichbar bleiben. Die Expertin kann Belege bis zur konkreten Quell-/Policyversion verfolgen und unbekannte Usage von bestätigten Kosten unterscheiden. Der Advancedzugang ist zusätzliche Offenlegung derselben Anwendung, keine zweite Produkt- oder Berechtigungsebene.

## Gemeinsame Grenzen

Eine Person kann zwischen diesen Rollen wechseln; keine Persona ist eine technische Berechtigungsrolle. Nutzung erfolgt lokal und authentifiziert. Screens zeigen ausschließlich tatsächliche Quellen und Fähigkeiten. LIVE, automatische Strategiepromotion, frei generierter Strategiecode, steuerliche Auswertung, echte Kontenaggregation und ein vollwertiger persönlicher Finanzplan gehören nicht zu diesem Zielbild. Accessibility ist für alle Rollen Teil derselben Kernwege, kein separater vereinfachter Modus.
