# Menschliche UX-Prüfung IEF-01 / IEF-02 / IEF-09

Status: **HUMAN_UX_VALIDATION_PENDING**. Dies ist ein ausführbares Protokoll mit leeren Vorlagen, kein Sitzungs- oder Freigabenachweis. Keine Personen wurden für dieses Dokument simuliert; keine menschlichen Ergebnisse liegen vor.

Normative Grundlage: `openspec/changes/investment-experience-foundation/ux-validation.md`, `ux-architecture.md`, `traceability.md` und die Arbeitspakete IEF-01, IEF-02, IEF-09. Bei Änderungen dieser Dokumente das Protokoll erneut abgleichen.

## Drei getrennte Runden innerhalb von IEF-09

| Runde | Prüfgegenstand und Zuordnung | Stichprobe | Bestehenskriterium |
| --- | --- | --- | --- |
| IEF-01 | Versionierter zusammenhängender klickbarer S1–S10-Prototyp, J1–J6; IEF-T-C06 | Mindestens fünf freiwillige fachfremde Personen, jede durchläuft alle sechs Wege; mindestens zwei Mobile- und zwei Desktopsitzungen | Alle sechs Kernwege klickbar; sichere Entscheidungen in simulierten Sicherheitsfällen bei 100 Prozent; 80 Prozent je Aufgabe als formative Diagnose. Gesperrte Kernwege und kritische Befunde erst nach Korrektur und menschlichem Nachtest freigeben. |
| IEF-02 | Funktionsfähige J1-Onboarding-Teilstrecke bis erkanntem lokalen Analyse-Einstieg; IEF-T-O05 | Eigene getrennte Evidenz, mindestens fünf Personen, mindestens zwei Mobile- und zwei Desktopsitzungen | Mindestens 80 Prozent erreichen ohne Hilfe binnen drei Minuten das markierte Demo-Cockpit und erkennen die lokale Analyse als nächsten Schritt. Kein vollständiger Analyse-/Reloadnachweis aus dieser Teilstrecke ableitbar. |
| IEF-09 | Integrierter Build, alle sechs Aufgaben und vier Begriffsfragen; IEF-T-C08/C07 | Mindestens fünf frische fachfremde Personen; jede absolviert alle Aufgaben/Fragen; mindestens zwei Mobile- und zwei Desktopsitzungen | Je Aufgabe und je Frage mindestens 80 Prozent, auf ganze Personen aufgerundet; Sicherheitsfälle bei jeder Person korrekt. |

Fachfremd bedeutet ohne Projektkenntnis und ohne Berufserfahrung in Trading, Research oder Finanzsoftware. Grobe Selbstauskunft zum Vorwissen erfassen. Bei fünf Personen entsprechen 80 Prozent vier Erfolgen; allgemein `ceil(0.8 × n)` pro Aufgabe/Frage berechnen. Den Nenner und Abbrüche sichtbar erhalten, keine erfolglosen Personen nachträglich aus der Stichprobe entfernen. Für Nachtests frische fachfremde Personen in gleicher Stichprobengröße und mit gleicher Schwelle einsetzen.

## Vorbereitung und Durchführung

UX- und Produktverantwortung benennen, Einwilligung privat einholen und pseudonyme Sitzungscodes vergeben. Keine Namen, Einwilligungsdokumente, Rohaufzeichnungen, echten Finanzdaten oder Nutzerfreitexte im Repository. Nur redigierte Bewertungen und aggregierte Befunde versionieren. Keine echten Providerzugriffe, API-Schlüssel oder Brokerorders. J5 verwendet im Prototyp gekennzeichnete Simulation und im integrierten Build ausschließlich synthetischen lokalen Paper-Harness.

Vor jeder Runde Prototypversion beziehungsweise Build-SHA, Fixtureversion/-hash, vorab feste technische Wartezeiten, Ausgangszustände, Geräte/Viewport und Browser-/Hilfsmittelversion festhalten. Bis zum Start unveränderte Aufgabenformulierungen festlegen. Die Uhr beginnt mit Aufgabenübergabe; die Moderation gibt keine Navigationshinweise. Hilfe, Timeout und falscher Abschluss zählen als Misserfolg. Technische Wartezeiten nicht nachträglich aus den Zeiten entfernen oder verändern. Vorbereitete synthetische Ausgangszustände sind zulässig.

Die für die formative Runde gesicherte Referenzfassung aus IEF-01 umfasst: Einstieg, Hauptaktion, Rückweg, Fehler und Wiederaufnahme je Weg; S5 → S6 → Evidenz → Eignung → S9 → Vorschau → Risk-Vorprüfung → Bestätigung → Status → S10; Fokusfolge/-wechsel, Statuslabels, Zeitpunkt und Inhalt technischer Details sowie Material-Tokens annotiert. Simulierte Übergänge deutlich als Prototyp kennzeichnen. UX-/Produktverantwortung dokumentieren Revision, Befunde, Änderungen und Freigabe. IEF-08 gleicht später gegen diese technisch geprüfte Version ab.

## Aufgabenkarte und Beobachtungsrubrik

Die Aufgabenkarte wird vorgelesen; die Beobachtungskriterien bleiben bei der Moderation. Bei IEF-02 ausschließlich die J1-Teilstrecke verwenden, alle anderen Felder als nicht im Rundenumfang markieren.

| ID | Alltagssprachliche Aufgabenkarte ohne Navigationshinweis | Erwartetes unaufgefordertes Ergebnis | Frist |
| --- | --- | --- | --- |
| J1 | „Sie möchten ohne Kontoverbindung zuerst sehen, was hier möglich ist. Finden Sie heraus, welche Beispieldaten angezeigt werden und wie Sie sie lokal untersuchen können. Finden Sie anschließend Ihren Befund nach einem Neuladen wieder.“ | Optionale Einrichtung übersprungen, Demo erkannt und lokaler Analysebefund samt Quelle nach Reload wiedergefunden. IEF-02 endet nach erkanntem Analyse-Einstieg. | 3 Minuten bis Demo/Analysestart erkannt; insgesamt 6 Minuten in IEF-01/09 |
| J2 | „Erklären Sie anhand dieser Übersicht zwei Werte, die Sie verlässlich kennen, und einen, den Sie nicht kennen. Untersuchen Sie den Bestand lokal.“ | Zwei bestätigte Werte und ein unbekannter Wert korrekt zugeordnet; lokaler Analyseweg gewählt. | 4 Minuten |
| J3 | „Suchen Sie eine Strategie, die Sie näher verstehen möchten. Erklären Sie deren Idee und den Zeitraum ihres historischen Ergebnisses. Finden Sie heraus, wie Sie ihre Verwendbarkeit für Paper prüfen würden.“ | Strategie erklärt, historischer Bericht nach Zeitraum eingeordnet, separater Weg zur Paper-Eignung gefunden. | 5 Minuten |
| J4 | „Stellen Sie Ihre vorbereitete Frage zur Erholung von Technologieaktien. Erklären Sie, was noch fehlt oder nicht möglich ist, und wählen Sie einen erlaubten nächsten Schritt.“ | Freie Frage erfasst; Rückfrage oder Blocker verstanden; zulässiger nächster Schritt ohne Ersatzstart. | 5 Minuten |
| J5 | „Prüfen Sie den vorbereiteten Paper-Vorgang. Erklären Sie, was mit welchem Konto und welchen Angaben geschehen würde. Führen Sie ihn nur aus, wenn Sie diese Wirkung bewusst möchten, und erklären Sie anschließend den Status.“ | Eignung/manuelle Vorschau geprüft, exakte Terms und Konto erklärt, Paperkontext und Übermittlungswirkung vor Bestätigung korrekt benannt, bewusst bestätigt und Status korrekt zugeordnet. | 6 Minuten |
| J6 | „Bei diesen zwei vorbereiteten Vorgängen haben sich Angaben geändert beziehungsweise ist der Ausgang unklar. Erklären Sie jeweils die Lage und wählen Sie den sicheren nächsten Schritt.“ | Stale und UNKNOWN erkannt; bei Stale keine alte Bestätigung, zurück zur Prüfung; bei UNKNOWN vorhandenen Status öffnen, keine neue Ausführung. | 4 Minuten |

## Vier offene Begriffsfragen

Ohne Antwortvorgabe nach den Aufgaben stellen; verpflichtend für IEF-09, bei formativer Nutzung in IEF-01 getrennt ausweisen. Keine vier Fragen als IEF-02-Abnahmepflicht erfinden.

| Frage | Bewertungsmaßstab |
| --- | --- |
| Was tut Research? | Untersuchung ohne Order. |
| Welche Daten und Zeit zeigt ein Backtest? | Simulation auf historischen Daten. |
| Mit welcher Kontowirkung arbeitet Paper? | Simuliertes Handelskonto ohne echtes Kapital; eine Anforderung kann an die Paper-Infrastruktur übermittelt werden. |
| Ab wann ist eine tatsächliche Orderausführung belegt? | Erst bestätigter Fill der konkreten Order; Annahme, Übermittlungsversuch und UNKNOWN genügen nicht. |

## Sicherheits- und Accessibility-Freigabe

Bei jeder Person müssen Paperkontext und Übermittlungswirkung vor Bestätigung korrekt sein. Stale verlangt Rückkehr zur Prüfung ohne alte Bestätigung, UNKNOWN die vorhandene Statusabfrage. Jede Verwechslung mit Gefahr von falschem Konto, unbewusster Bestätigung, Doppelauftrag oder riskanter Wiederholung blockiert. Kritische Missverständnisse vor Abschluss von IEF-09 mit Menschen nachtesten. Reproduzierbare Kernwegsperren per Tastatur, Screenreader oder unterstütztem Mobilelayout sowie unterschrittene verbindliche Schwellen blockieren. Nach Korrektur betroffene Aufgaben mit frischen fachfremden Personen, gleicher Stichprobengröße und Schwelle nachtesten.

Technischen Accessibility-Nachweis separat führen: Fokus und Statusansagen, lesbare Feldfehler, Kontrast mindestens 4.5:1 für normalen Text und 3:1 für große Schrift/relevante Bedienelementgrenzen, 200-Prozent-Zoom, reduzierte Bewegung, 320/360/768/1440 CSS-Pixel, offene Detailpfade. Kernaktionen ohne horizontale Navigation und ohne Hoverabhängigkeit; technische Detailtabellen dürfen horizontal scrollen. Zwei Mobile- und zwei Desktopsitzungen ersetzen diese technischen Prüfungen nicht. KI-/Expertenreviews und Browserautomation ersetzen keine Testpersonen.

## Leere Erfassungsvorlagen

Runde: _offen_; Datum UTC: _offen_; Build-SHA/Prototyprevision: _offen_; Fixtureversion/-hash und feste Wartezeiten: _offen_; UX-/Produktverantwortung: _offen_; geplantes n und Mindestzahl: _offen_; Freigabe: **HUMAN_UX_VALIDATION_PENDING**.

| Sitzungscode | grobes Vorwissen/Teilnahmekriterien erfüllt | Gerät/Viewport | Browser/Hilfsmittel | J-ID | Zeit Sekunden | Hilfe/Timeout/falscher Abschluss | Erfolg | redigierte Bewertung | Sicherheitsfall korrekt |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| _nicht erhoben_ | | | | | | | | | |

| Sitzungscode | Frage | richtige Antwort ja/nein | redigierte Bewertung |
| --- | --- | --- | --- |
| _nicht erhoben_ | | | |

| Aufgabe/Frage/Sicherheitsfall | n | Erfolge | erforderliche Erfolge | Schwelle erreicht | Evidenzreferenz |
| --- | --- | --- | --- | --- | --- |
| _nicht erhoben_ | | | | | |

| Befund-ID | Runde/Revision | betroffene Aufgabe | redigierter Befund | Schweregrad/Freigabeblocker | Korrektur/Revision | menschlicher Nachtest mit frischer Stichprobe | Ergebnis |
| --- | --- | --- | --- | --- | --- | --- | --- |
| _keine Befunde erhoben_ | | | | | | | |

Abnahmevermerk: _nicht erteilt_. Prototypübergabe, IEF-02-Teilstrecke und IEF-09-Produktabnahme jeweils separat unterschreiben beziehungsweise mit datiertem freigegebenem Vermerk bestätigen. Leere Tabellen bedeuten fehlende Evidenz, niemals Erfolg.
