# UX Architecture

## Ziel und bestehender Befund

Der geplante Einstieg folgt Anlegerfragen: Was habe ich? Was ist unklar? Welche Untersuchung läuft? Welche Entscheidung ist jetzt nötig? Bestehende Fachobjekte und Sicherheitsgrenzen bleiben erhalten. Alle zehn folgenden Screens sind Zielbilder, keine Behauptung bereits implementierter Funktionen.

Nachweisbarer Stand bei `0442f29571e53b983e9e08f2180c4bd3536e0b6a`:

- `apps/dashboard/src/main.ts:6` bootstrapped die Rootkomponente mit HTTP-Providern; kein Routerprovider. `src/app/app.html:1` enthält ausschließlich `<atl-supervisor />`.
- `supervisor/supervisor.component.ts:105` enthält technische Ansichten; `supervisor.component.html:52` verschachtelt Research in Übersicht/Paper. `research/research.component.html:2` öffnet Companion und Quant Lab verzögert, daneben Backtesting und Researchdaten.
- `research/agent-companion.component.ts:108` benennt fünf Testbarkeitsklassen; ab Zeile 150 werden Ideen-/Freigabe-/Reportquerys gelesen. Die vorhandene freie Eingabe ist keine beliebige ausführbare Strategie.
- `research/quant-lab/quant-lab-builder.component.html` zeigt registrierte Grids, Strict-PIT/retrospektive Wahl, raw/IEX/15Min und Long/Flat. `paper-workflow/paper-workflow.component.html` zeigt getrennten Ledger, ungeklärte Vorgänge und Kontrollen.
- `contracts/http/control-api.openapi.yaml` enthält bereits Research-, Agent-, Quant- und Paperverträge. Die unten benannten `/api/v1/experience/*`-Projektionen sind geplant und müssen vor Clientimplementierung kanonisch spezifiziert werden.

## Übergreifendes Screenmodell

Eine Shell enthält Kontext (Research/Paper, Verbindung, letzte Bestätigung), Seitentitel, fachliches Ergebnis und eine dominante nächste Aktion. Tiefe Belege, technische IDs und Betriebsdetails sind erreichbar, aber verdrängen das Ergebnis nicht. Pro Screen gilt: initiales Laden, ehrlich leer, erfolgreich, teilweise verfügbar/veraltet, fachlich blockiert, technischer Fehler und abgelaufene Sitzung sind unterscheidbar. Kein unbekannter Wert wird zu Null, kein Fehler zu Erfolg und kein Taskstatus zu Handelsfreigabe.

Gemeinsame Accessibility: ein Hauptinhalt, geordnete Überschriften, Sprunglink, sichtbarer Fokus, vollständige Tastaturbedienung, beschriftete Formfelder, Fehlerzuordnung und Textstatus zusätzlich zu Farbe. Charts bekommen Kennzahlen und zugängliche Datentabelle; Statusupdates sind zurückhaltend live angekündigt. Seitenwechsel fokussiert Titel, Dialogschluss den Auslöser. Mobile bleibt die Reihenfolge Kontext → Ergebnis → primäre Aktion → Belege erhalten; keine allein per Hover erreichbare Information. 320 CSS-Pixel und 200-%-Zoom gehören zur Abnahme.

### Gemeinsames Designsystem

Material-Theme und vorhandene Typografie bleiben Ausgangspunkt. IEF-01 definiert semantische Theme-Tokens, IEF-08 verwendet sie einheitlich: Hintergrund/Oberfläche, primäre Aktion, Text/sekundärer Text, Fokus, Erfolg, Warnung, Blockade und Fehler. Demo und Paper erhalten dauerhaft benannte Kontextchips; dieselbe Farbe allein unterscheidet keine fachlichen Zustände. Helle und vorhandene dunkle Darstellung werden gegen denselben Kontrastnachweis geprüft, kein zweites unabhängiges Theme.

Abstände folgen der Skala4/8/12/16/24/32px; Kartenpadding24px auf Desktop und16px mobil, Gruppenabstand24px. Inhaltsbreite maximal1280px, mindestens16px Seitenrand; unter768px einspaltig, ab768px zwei und ab1200px bis drei gleich priorisierte Karten. Navigation `side` ab1024px, darunter `over`; Breakpoints sind Layoutentscheidungen, keine Geräteerkennung. Primäre Interaktionsfläche mindestens44×44 CSS-Pixel, semantische Reihenfolge bleibt unverändert.

Textrollen sind Seitentitel28/36px, Abschnitt20/28px, Fließtext16/24px, Metadaten14/20px; relative skalierbare CSS-Einheiten erhalten Browserzoom. Geld/Mengen verwenden tabellarische Ziffern; deutsche Anzeigeformatierung rundet nur gekennzeichnet für Anzeige, die exakten Decimalstrings bleiben in Details und Bestätigungen vollständig lesbar. Einheiten und Währung stehen bei jedem Wert. Primary Button benennt konkrete Wirkung; Info-, Warn- und Fehlerflächen zeigen Grund und zulässigen nächsten Schritt ohne Marketingprognose. Keine Chartfläche ohne Werte-/Textalternative und keine dekorative Finanzkurve ohne Daten.

Abnahme: Kontrast für normalen Text mindestens4.5:1, große Schrift und relevante Bedienelementgrenzen mindestens3:1; sichtbarer Fokus, reduzierte Bewegung und keine blinkenden Statuswechsel. Das ist ein UI-Akzeptanzziel, keine Behauptung bereits zertifizierter Barrierefreiheit. Geprüft werden320/360/768/1440px und200-%-Zoom; horizontales Scrollen nur für ausdrücklich technische Detailtabellen, nie für Hauptaktionen.

## S1 – Guided Onboarding

**Nutzerziel:** lokalen Rahmen verstehen und nächsten Schritt wählen. **Hierarchie:** Research/Paper-Erklärung → lokale Sitzungsaktivierung → optionale Interessen/Erfahrung/Zeithorizont → tatsächliche Bereitschaft → Zusammenfassung. Keine vorausgesetzte reale Kontoverbindung.

**Primär:** „Zum persönlichen Überblick“ nach erforderlicher Sitzung. **Sekundär:** Präferenzen überspringen, später bearbeiten, Research lokal beginnen, vorhandene geschützte Einrichtung öffnen. **Zustände:** neuer Einstieg, gespeicherte Präferenzen, bewusst übersprungen, Einrichtung unvollständig, Sitzung abgelaufen. **Fehler:** Feldfehler inline; Versionskonflikt lädt aktuelle Präferenzen; fehlender Provider blockiert nur davon abhängige Funktionen, nicht den Einstieg. Kein Setupabschluss impliziert Betrieb.

**Mobile/a11y:** ein Schritt pro Abschnitt, sprechende Schrittüberschriften, zurück ohne Datenverlust; keine Pflicht zum horizontalen Stepper. Aktiver Schritt und Fehler erhalten Fokus. **API:** vorhandenes `activateLocalSession`, Integrations-/Providerbereitschaft; geplant `/experience/preferences` mit actor+version. **Übergänge:** S2 nach Abschluss/Überspringen; ergänzende Setupansicht und zurück zu demselben Schritt. Kein GET startet Probe oder Run.

## S2 – Personal Dashboard

**Nutzerziel:** den bestätigten Gesamtzustand und die nächste eigene Entscheidung erkennen. **Hierarchie:** Kontext/Frische → Kurzstand der explizit ausgewählten Demo-/Fake-/Paperquelle oder ehrlicher Leerzustand → Aufgaben mit Handlungsbedarf → Strategie-/Researcheinstieg → jüngste Aktivität. Eine ausgedachte Gesamtnote oder Renditeprognose ist ausgeschlossen.

**Primär:** kontextabhängig „Portfolio lokal analysieren“, „Research-Frage stellen“ oder „Offene Aufgabe ansehen“, immer genau eine dominante Aktion. **Sekundär:** Portfolio öffnen, Strategien entdecken, Einrichtung fortsetzen. **Zustände:** unkonfiguriert, leer, aktive Tasks, Nutzerentscheidung nötig, teilweise Daten, stale. **Fehler:** einzelne Karten dürfen fehlschlagen, ohne alle bestätigten Daten zu verdecken; Quelle und Alter je Teilprojektion. Keine zusammengesetzte Scheinkonsistenz aus unterschiedlichen Ständen.

**Mobile/a11y:** gestapelte Karten mit gleicher Priorität; Titel und Linktexte benennen ihr Ziel, Summen sind als Text lesbar. **API:** geplante preferences/portfolio/tasks/activity-Projektionen; keine neue Dashboard-Orchestrierung. **Übergänge:** S3, S5, S7/S8 und S10; Rückkehr behält zulässigen Filterkontext. Finanzielle Daten stammen ausschließlich aus der explizit gewählten bestätigten Demo-/Fake-/Paperquelle; DEMO verlangt demoRunId, Quellen werden nie gemischt.

## S3 – Portfolio Hub

**Nutzerziel:** bestätigte Positionen, Cash und offene Verpflichtungen der gewählten Demo-/Fake-/Paperquelle verstehen. **Hierarchie:** dauerhafter expliziter Demo-/Fake-/Paperkontext (DEMO mit demoRunId) → Zeitpunkt und verfügbare Reconciliation → Cash/Positionen/Reservation → Positiondetails → offene Orders und Herkunft. Kein echtes Multibrokerdepot.

**Primär:** „Portfolio untersuchen“ zu S4; bei fehlender Einrichtung „Paper einrichten“. **Sekundär:** Guided Paper Order, Position öffnen, Vorgänge/Trace lesen. **Zustände:** eingerichtet ohne Positionen, befüllt, ausstehende Operationen, unbewertbare Positionen, stale, unbekannter Bestand. **Fehler:** fehlende Bewertung hält bestätigte Mengen sichtbar; keine Nullpreise. Research- oder Demokonten werden nicht in Finanzsummen gemischt.

**Mobile/a11y:** Positionen als gestapelte Datensätze mit denselben Labels; Desktoptabellen behalten Spaltenüberschriften. Position ist per Tastatur wählbar. **API:** geplant `/experience/portfolio`, auf den bestehenden getrennten Demo-/Fake-Snapshotleseports oder `getPaperSnapshot`, passenden Orders/Fills/Operations und bestätigten Daten. Readmodel benennt Basis und nicht berechenbare Felder. **Übergänge:** S4, S9, S10; Positionsdetail bleibt untergeordneter Weg dieses Screens und bindet Ledger-/Instrumentkontext.

## S4 – Portfolio Insights

**Nutzerziel:** bestätigte Beobachtungen von Interpretationen unterscheiden. **Hierarchie:** Datengrundlage/Zeitraum → verfügbare Allokation und Konzentration → tatsächlich berechenbare Veränderungen → erklärende Grenzen → Forschungsfrage. Keine Empfehlung ohne belegte Grundlage und kein verborgenes Risikosignalmodell.

**Primär:** „Portfolio lokal analysieren“ erstellt nach ausdrücklicher Bestätigung einen snapshotgebundenen deterministischen Befund mit analysisId (kein Agentenrun). Optional „Zusätzlich von KI erklären lassen“ öffnet die gesonderte Zweck-/Daten-/Kostenvorschau. Eine autonome Portfolioanalystenrolle bleibt unavailable; sie ist nicht Voraussetzung für die lokale Analyse. **Sekundär:** Berechnung/Evidenz zeigen, Zeitraum ändern, zurück zum Bestand. **Zustände:** Daten ausreichend, Teilanalyse, keine Historie, keine Preise, Snapshot überholt. **Fehler:** unberechenbare Kennzahl zeigt Grund und fehlenden Input; Prozentanteile müssen Bezugsbasis nennen und unvollständige Bewertung ausweisen. V1 liefert Positionsmarktwert/unrealisiertes P&L mangels Bewertungsvertrag sowie Tages-/Gesamtrendite/Drawdown mangels Performancehistorie ausdrücklich nicht; siehe `portfolio-read-model.md`. Keine erfundene Tages-/Gesamtrendite oder diversifikationsbereinigte Risikoquote.

**Mobile/a11y:** Charts skalieren, ergänzende Tabelle und Textinterpretation bleiben verfügbar; Information hängt nicht von Farbe ab. **API:** geplant POST `/experience/portfolio-analyses`, GET `/experience/portfolio-analyses/{analysisId}` und `/experience/insights` mit versions-/zeitgebundener expliziter Demo-/Fake-/Paperquelle und zulässigen vorhandenen Berechnungen; fehlende Berechnungsfähigkeit bleibt fehlend. **Übergänge:** S8 mit bestätigbarem Quellenentwurf, S3 für Positionen, S10 für Herkunft. Entwurf löst weder Providerverarbeitung noch Handel aus.

## S5 – Strategy Discovery

**Nutzerziel:** tatsächlich registrierte Untersuchungsansätze entdecken und Grenzen vergleichen. **Hierarchie:** verfügbare Katalogbasis → verständliche Strategiekarten → fachliche Filter → Anforderungen/Verfügbarkeit. „Passt zu meinen Interessen“ ist eine Darstellungspräferenz, keine Geeignetheitsprüfung.

**Primär:** „Strategie verstehen“ auf ausgewählter Karte. **Sekundär:** begrenzter Vergleich, Filter zurücksetzen, freie Frage stellen. **Zustände:** Katalog lädt, Ergebnisse, kein Filtertreffer, Katalog leer, Strategie vorhanden aber Daten blockiert. **Fehler:** nicht verfügbarer Katalog erzeugt kein lokal erfundenes Angebot; Filter ohne Treffer bleibt von fehlenden Daten unterscheidbar.

**Mobile/a11y:** Karten zuerst, Filter in beschriftetem aufklappbarem Bereich, Auswahlstatus als Text. Vergleich stapelt Kriterien statt horizontalem Zwang. **API:** geplant `/experience/strategies`; vorhandenes `getQuantLabStrategies` und `listBacktestStrategies` bleiben getrennte kataloggebundene Quellen und werden nicht semantisch vereinheitlicht. **Übergänge:** S6 mit stabiler Strategie-/Versionsbindung, S8 für freie Idee. Historische Research- und Paperstrategien bleiben getrennt beschriftet.

## S6 – Strategy Detail

**Nutzerziel:** verstehen, welche konkrete Hypothese eine registrierte Strategie tatsächlich prüfen kann. **Hierarchie:** verständliche These → Regel und Parameter → Instrument-/Daten-/Modusgrenzen → vorhandene Ergebnisse mit Version/Herkunft → nächste zulässige Untersuchung.

**Primär:** „Research-Experiment entwerfen“, wenn der Katalog den Pfad trägt; sonst „Fehlende Voraussetzungen ansehen“. **Sekundär:** Vergleich, vorhandenen Report öffnen, freie Idee klären. **Zustände:** lesbar, keine Ergebnisse, vorhandene positive/negative/blockierte Ergebnisse, nicht mehr verfügbare Version. **Fehler:** Ergebnisausfall verdeckt Definition nicht; alte Ergebnisse werden nie still der aktuellen Version zugeordnet. Keine Strategiepromotion oder Paperübernahme aus Rendite allein.

**Mobile/a11y:** Parameter in Label-Wert-Gruppen, Formeln zusätzlich sprachlich erklärt; Versions-/Modushinweise stehen vor Startaktion. **API:** geplantes Strategiedetail innerhalb `/experience/strategies`, vorhandene Quant-/Backtestdefinitionen und Berichte. Nur vorhandene Werte darstellen; neue Definitionen fachlich im Contract klären. **Übergänge:** vorhandener Quant-Entwurf, S8 mit Referenz und S5; explizite Approximation führt in bestätigbaren Ideendialog, niemals direkt zum Test der Originalidee.

## S7 – AI Task Center

**Nutzerziel:** eigene Researchaufträge und erforderliche Entscheidungen wiederfinden. **Hierarchie:** eigene offene Entscheidungen → aktive Aufgaben → abgeschlossene/blockierte Aufgaben → Filter/Historie. „Aufgabe“ ist ein Nutzerbegriff für vorhandene gebundene Ideen, Dialoge und Runs, kein neuer autonomer Agent.

**Primär:** „Research-Frage stellen“. **Sekundär:** Aufgabe öffnen, nach Status filtern, bestätigten Zustand aktualisieren. **Zustände:** keine Aufgaben, Entwurf, wartend auf Eingabe/Freigabe, geplant/aktiv, abgeschlossen, blockiert, fehlgeschlagen, abgebrochen; genaue Abbildung folgt Fachstatus. **Fehler:** leere Ergebnisse nur nach erfolgreicher Abfrage; Pagination und Teilfehler werden sichtbar. Eine ausstehende Modellentscheidung ist keine Paperentscheidung.

**Mobile/a11y:** Aufgabenkarten nennen Ziel, Status, letzte Aktualisierung und nächsten Schritt; Listenänderung verschiebt nicht ungefragt Fokus. **API:** geplant `/experience/tasks`, Projektion vorhandener Runzustände mit versionierten Intent-Metadaten; bestehende Ideen-/Dialog-/Approval-/Reportreads. Kein zweiter Scheduler. **Übergänge:** S8 per Task-ID, S10 für Verlauf, ergänzendes Quantdetail für gebundene Batch. Wiederaufnahme und Deep Link lesen ausschließlich.

## S8 – AI Task Detail

**Nutzerziel:** Ziel, Interpretation, Fortschritt, Ergebnis und nächste eigene Entscheidung verstehen. **Hierarchie:** Auftrag/Original → bestätigte Interpretation/offene Fragen → nächster Schritt → fachlicher Status → Ergebnis mit Grenzen → Belege und technische Details.

**Primär:** zustandsgebunden „Antworten bestätigen“, „Sendefassung prüfen“, „Experiment prüfen“ oder „Ergebnis ansehen“; nie ein kontextloses „Ausführen“. **Sekundär:** lokal weiterklären, ablehnen, vorhandenen zulässigen Runabbruch anfordern, Historie/Definition öffnen. **Zustände:** lokaler Entwurf, Klärung, Preview wartet/abgelaufen, externer Versand unklar, Testbarkeit blockiert, Experiment gebunden, laufend, terminal. **Fehler:** Versionskonflikt verlangt erneute fachliche Prüfung; Providerfehler zeigt getrennt Verarbeitungs- und Versandzustand. Ergebnis und Original bleiben historisch gebunden.

**Mobile/a11y:** einspaltiger Dialog mit expliziten Sprecher-/Statuslabels; neue Nachricht stiehlt keinen Fokus, Review erhält gezielten Fokus. Belege sind aufklappbar, Grenzen sichtbar. **API:** geplantes Taskdetail plus bestehende `/research/agent/dialogues`, Ideen, Processing-Previews/-Decisions, Approvals, Runs, Reports und Quantkommandos. Neue Taskmetadata ändert keine Autorität. **Übergänge:** S6, Quantresultat, S7, S10; Paper nur als getrennter expliziter Nutzerweg, nie automatische Fortsetzung.

## S9 – Guided Paper Order

**Nutzerziel:** eine konkrete menschliche LIMIT-Anforderung bewusst vorbereiten und ihren echten Zustand verfolgen. **Hierarchie:** Paper-/Kontokontext → Voraussetzungen → Backendplan → menschlicher Review → bestätigte Operation → Orders/Fills und unabhängige Kontrollen.

**Primär:** „Plan prüfen“, dann „Diese Paperanforderung bestätigen“ ausschließlich bei gültiger Bindung. **Sekundär:** zurück, verwerfen, vorhandenen Vorgang prüfen, zulässige Stornierung; Pause/Kill bleiben unabhängig erreichbar. **Zustände:** nicht eingerichtet, Prüfung nötig, gesperrt, gültiger Plan, angenommen/pending, ungeklärt, teilgefüllt, terminal, Cancel pending. **Fehler:** stale oder abgelaufener Plan sperrt neue Bestätigung; unklarer Submit führt zum bestehenden Vorgang mit ursprünglichem Schlüssel. Cancel bestätigt keinen Fill und keine Budgetfreigabe.

**Mobile/a11y:** klar beschrifteter Review mit allen Decimalwerten; keine Swipe-Bestätigung. Dialog kann vor Absenden ohne Aktion geschlossen werden. **API:** vorhandene Paperprofil-, Kontoprüfungs-, Kontroll-, `createPaperLimitOrderRequest`-, Cancel- und Lesekommandos. Neue gebundene Preview `/api/v1/paper/order-previews` und einmalig konsumierte `/api/v1/paper/guided-order-requests` sind geplant, nicht bereits vorhandene APIs; siehe `guided-order-design.md`. Keine Experience-API darf ein Ordergate ersetzen. **Übergänge:** S3 und S10, ergänzender Papertrace. Research-/Strategieergebnisse können Kontext liefern, keine Genehmigung.

## S10 – Activity Timeline

**Nutzerziel:** nachvollziehen, was passiert ist, wer entschieden hat und was noch offen ist. **Hierarchie:** offene/ungeklärte Vorgänge → zeitlicher Verlauf → Ereignisdetail → gebundener Fachkontext und Trace. Research, Alpaca Paper und ergänzende Fake-/Demoansichten haben sichtbare Quelllabels und bleiben finanziell getrennt.

**Primär:** „Vorgang öffnen“. **Sekundär:** Filter nach Quellkontext/Zeitraum, weitere Seite, konkrete Belege öffnen. **Zustände:** leer, befüllt, unvollständige Fortsetzung, unbekannter Quellzustand, verspätete Aktualisierung. **Fehler:** eine fehlende Quelle wird als Teilfehler gezeigt; begrenzter Trace bedeutet nicht vollständige Auditkette. Zeitgleichheit allein erzeugt keine Kausalverbindung.

**Mobile/a11y:** semantische Ereignisliste, lesbare UTC-Zeit, Herkunft und Status als Text; Fokus bleibt bei „Weitere laden“. **API:** geplant `/experience/activity` als begrenzte actor-/quellgebundene Projektion vorhandener Events/Operations; vorhandene Papertraces und Supervisorhistorien ergänzend. Keine Rohpayloads, Credentials oder privaten Provideridentitäten. **Übergänge:** S8 für Research, S9 für Orderstatus, ergänzender Betriebs-/Auditweg; browserseitiges Zurück stellt Filter und Position wieder her.

## Ergänzende Wege und verbindliche Grenzen

Bestehende Researchdaten, Readiness, Quant Builder/Ergebnisse, Providerverwaltung, Fake-Supervisor, Demo, Audit, Risk-Policy und Worker/Kosten bleiben gezielt über „Daten und Betrieb“ erreichbar. Diese Wege sind keine zusätzlichen primären Pflichtscreens. Scopewechsel zeigt explizit den Zielkontext; vorhandene Pause-/Killkontrollen bleiben auch bei Researchlast zugänglich. Kein Screen führt Keys in Querys, Routerstate, Analytics, Screenshots oder Modellkontext ein. Nur der bestehende menschliche geschützte Einrichtungsdialog darf die eng begrenzte Formulareingabe verarbeiten.




## Verbindlicher Erstnutzerpfad S1–S4

S1 erklärt überspringbare Präferenzen und Demo ohne Schlüssel. S2 zeigt den ausdrücklich gewählten demoRunId-Snapshot; vorhandenen Demolauf wählen oder eine ausdrückliche Demoanlage verwenden, nie GET- oder Navigationseffekte. S3 erhält exakt denselben Namespace/Run-/Snapshotkontext. S4 erstellt nur auf bewusste lokale Analyseaktion einen persistenten deterministischen Befund; sourceType PORTFOLIO_ANALYSIS, analysisId und Methoden-/Quellrevision bleiben nach Reload in S7/S8 wieder auffindbar. Das ist der vollständige J1-Weg. Paperquellen und der zusätzliche Researchdialog J4 sind getrennte mögliche Einstiege. Im positiven No-Key-Test wird externes I/O null gezählt; fehlende Preise und Historie sind verständliche Grenzen, keine erfundenen Resultate.
