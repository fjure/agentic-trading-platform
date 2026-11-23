# User Journeys

## Gemeinsamer Rahmen

Die sechs Journeys beschreiben geplantes UX-Verhalten. Bestehende Kommandos und Gates bleiben erhalten; Experience-Readmodels sind geplant. Jede Journey enthält Eintritt, Schritte, persistente Bindung, Abbruch-/Fehlerweg und beobachtbares Ergebnis. Navigation oder GET erzeugt keine Verarbeitung und keine Order. Technische Zeitstempel bleiben UTC, Geld und Mengen exakte Decimalstrings. Wiederaufnahme liest bestätigten Serverzustand. „Fortsetzen“ ist nur eine zulässige konkrete Fachaktion, kein universelles Resume.

## J1 – Erster Einstieg bis zum persönlichen Überblick

**Persona/Ziel:** P1 versteht den lokalen Research-/Paperkontext und findet ihren ersten sinnvollen Schritt. **Vorbedingung:** Vertrauenswürdiger Launcher und lokale Sitzung; Providerkonfiguration ist keine Voraussetzung für bloßes Lesen.

1. Guided Onboarding erklärt Research, Paper und unerreichbares LIVE, aktiviert die bestehende lokale Sitzung und fragt freiwillig nach Interessen, Erfahrungsniveau und Zeithorizont. Kein Formular fragt nach realen persönlichen Vermögensdaten.
2. Nutzerin speichert versionierte Präferenzen oder überspringt sie. Für externe Verarbeitung und Paperhandel wird dabei nichts freigegeben.
3. Statusprüfung unterscheidet Sitzung, Providerbereitschaft und Paperkonto. „Nicht eingerichtet“ bleibt eine zulässige Lage; Schlüssel können ausschließlich Menschen im bestehenden geschützten lokalen Dialog eingeben.
4. Nutzerin wählt ausdrücklich Demo und eine vorhandene demoRunId oder legt über den vorhandenen Demo-Befehl einen isolierten synthetischen Lauf an; bloße Navigation legt nichts an. Das Dashboard zeigt nur dessen bestätigten Snapshot mit Quelle, Datenstand und begründet unbekannten Feldern. Ein leeres Paperkonto bleibt getrennt.
5. „Portfolio lokal analysieren“ zeigt die konkrete Demoquelle und Snapshotversion. Erst ausdrückliches Bestätigen führt `POST /api/v1/experience/portfolio-analyses` mit Idempotency-Key aus. Die begrenzte deterministische Analyse persistiert einen Qualitäts-/Bestandsbefund mit analysisId, Methodenrevision und Quellbindung; weder Modell noch Agentenrolle werden benötigt.
6. Die Nutzerin liest bestätigte Cash-/Mengen-/Qualitätsbefunde und verständliche Grenzen. Fehlende Bewertungs-/Performancegrundlagen bleiben unbekannt. Über Task Center/Detail und nach Reload findet sie dieselbe analysisId und sichere Folgeaktionen wieder. Ein zusätzlicher lokaler Researchdialog gehört zu J4 und ersetzt diese Portfolioanalyse nicht.

**Persistenz/Wiederaufnahme:** Geplante Preferences-Projektion actor+version; kein Credential und kein Aktivierungscode. Bei Reload wird der gespeicherte Abschluss gelesen; übersprungene Präferenzen bleiben optional. **Fehler:** 401 führt zur Anmeldung; Versionskonflikt lädt aktuellen Stand und bietet bewusste erneute Auswahl. Netzwerkfehler lässt keinen ungesicherten Abschluss vermuten. **Abnahme:** Nutzerin gelangt ohne Shell zum Dashboard; es gibt weder Broker-I/O durch Lesen noch automatische Ausführung. Übergang zu J2/J4/J5.

## J2 – Paperportfolio verstehen und eine Beobachtung untersuchen

**Persona/Ziel:** P1 erkennt Konzentration und fehlende Evidenz. **Vorbedingung:** Expliziter Demo-/Fake-/Paperkontext mit bestätigten Lesedaten oder ehrlich leerer Zustand.

1. Dashboard → Portfolio Hub: Kontext, bestätigtes Cash, Positionen, offene Verpflichtungen und Datenalter erscheinen vor Detailhistorien.
2. Auswahl einer Position öffnet ihre Details im Portfolio Hub; weiter zu Portfolio Insights bindet dieselben Ledger-/Bewertungsstände.
3. Insights erklärt verfügbare Kennzahlen und Berechnungsbasis. Fehlende frische Preise oder Historie ergeben „nicht berechenbar“, keinen Nullwert. Unvollständige Allokation bleibt als solche beschriftet.
4. „Portfolio lokal analysieren“ erstellt nach ausdrücklicher Bestätigung den snapshotgebundenen deterministischen Befund. Das AI Task Detail zeigt sourceType PORTFOLIO_ANALYSIS und analysisId, keinen künstlichen Run.
5. Optional öffnet „Zusätzlich von KI erklären lassen“ eine minimierte Vorschau zu PORTFOLIO_EXPLANATION_V1. Nur nach gesondert gültiger Daten-/Kostenentscheidung und aktueller Provider-/Zweck-/Budgetbindung verarbeitet die vorhandene Runtime die Erklärung. Ohne diese Rechte bleibt die lokale Erklärung vollständig nutzbar. Rohe Portfoliotabellen gehen nicht in den Hypothesenkanal.
6. Folgeforschung ist ein neuer begrenzter Entwurf und benötigt ihre eigenen Entscheidungen. Eine nicht implementierte autonome Portfolioanalystenrolle bleibt unavailable; sie ist von dieser lokalen Analyse und eng begrenzten Erklärung verschieden.

**Persistenz/Wiederaufnahme:** Portfolio/Insights sind geplante Readmodels über explizite vorhandene Demo-/Fake-/Paperquellen; kein neues echtes Depot. Task-Entwurf und späterer Run behalten die konkrete Quellenbindung. **Fehler/Abbruch:** Teilweise fehlende Insights sperren nicht den Bestandsblick; stale sperrt neue Handelsfreigaben. Unbestätigten Task kann sie verlassen; eine serverseitig erstellte Aufgabe bleibt im AI Task Center sichtbar. **Abnahme:** angezeigte Summen sind rekonstruierbar, fehlende Daten erklärt; Aufgabe nennt dieselbe Beobachtung und bleibt Research. Übergang zu J3 oder Activity Timeline.

## J3 – Verfügbare Strategie entdecken, vergleichen und belastbar auswerten

**Persona/Ziel:** P2 prüft eine im aktuellen Katalog angebotene Long/Flat-Strategie. **Vorbedingung:** Quellen-/Katalogstatus lesbar; tatsächliche Ausführung setzt Datenzulässigkeit und Dispatchbereitschaft voraus.

1. Strategy Discovery zeigt verfügbare registrierte Varianten und ihre Datenanforderungen, keine erfundene personalisierte Empfehlung. Filterinteressen beeinflussen nur Darstellung.
2. Strategy Detail erklärt Auslöser, Signal, Ziel und Grenzen; Nutzer wählt eine konkrete Version und öffnet einen Experimententwurf im vorhandenen Quantweg.
3. Explizite Parameter, Instrument, Phasen, Kostenannahmen und Forschungsmodus werden geprüft. Strict PIT bleibt Vorgabe. Retrospektive Exploration benötigt bewusste Wahl und behält Warnung und Herkunft; sie heilt keinen PIT-Blocker.
4. Validierung bindet Definition, Versionen, Datenstand und Budget. Erst ausdrücklicher Start erzeugt eine Batch. AI Task Detail zeigt Dauerzustand und bestehende Run-/Experimentverweise.
5. Nach Abschluss zeigt Ergebnisansicht im AI Task Detail/ergänzenden Quantweg In-Sample, Validierung, Walk-forward/OOS, Holdout, Drawdown und Reliability soweit tatsächlich vorhanden. Negative oder nicht interpretierbare Ergebnisse stehen gleichberechtigt neben positiven; fehlende Phasen sind kein Erfolg.
6. Nutzer vergleicht kompatible Ergebnisse mit sichtbaren Kosten-/Daten-/Modusabweichungen und kehrt zur ursprünglichen Strategie oder Idee zurück. Keine Promotion-/„Jetzt automatisch handeln“-Aktion.

**Persistenz/Wiederaufnahme:** Experiment/Batch/Ergebnisversionen bleiben die bestehenden Fachobjekte; Task projiziert sie. **Fehler/Abbruch:** deaktivierter Dispatch oder Datenblocker zeigt Voraussetzungen statt Spinner ohne Ende; nach unbekannter Startantwort bestehenden Vorgang prüfen. Bei Cancel werden tatsächlicher Zwischenzustand und bereits entstandene Ergebnisse erhalten. **Abnahme:** Reload öffnet dieselbe Batch, Ergebnisse nennen Herkunft und Grenzen; kein Paperstart. Übergang zu J5 ausschließlich als neuer menschlicher Auftrag.

## J4 – Freie Tech-Erholungsfrage mit ehrlichem blockiertem Ergebnis

**Persona/Ziel:** P2 prüft „Erholen sich Techaktien nach starken Kursrückgängen binnen einer Woche?“ **Vorbedingung:** Lokale Sitzung; kein Daten- oder Modellzugang wird unterstellt. Bestehende fachliche Grundlage: APF-Q-T03/T19 in `agent-platform-foundation/specs/research-companion/spec.md`.

1. AI Task Center → „Research-Frage stellen“ → AI Task Detail. Originaltext wird separat von der vorgeschlagenen Interpretation gezeigt. Lokale Eingabeprüfung erfolgt vor Persistenz.
2. Lokale Klärung fragt: Welche Techinstrumente? Was bedeutet „stark“ als Kursrückgangsschwelle? Auf welchen Beobachtungszeitraum bezieht sich der Rückgang? Bedeutet „binnen einer Woche“ fünf Handelstage oder sieben Kalendertage nach dem Signal? Wie wird Erholung gemessen, gegen welche Benchmark und unter welchen Kostenannahmen? Offene Antworten bleiben offen.
3. Optionaler Modellweg: vor Versand zeigt die UI konkrete redigierte Sendefassung, Provider-/Modellbindung, Datenrichtlinie, Kosten-/Budgetgrenze und Ablaufzeit. Nutzer kann ablehnen und lokal weiterarbeiten. Speichern einer Idee ist keine Sendefreigabe; geänderte Fassung benötigt neue Freigabe.
4. Nutzer bestätigt die konkrete Interpretation; Original, Entscheidungen und Version bleiben abrufbar. Die Datenprüfung weist ungeklärtes Techuniversum, fehlende Datenabdeckung, nicht registrierte ereignisgenaue Rückgangs-/Erholungsregeln oder fehlende historische Verfügbarkeit aus. SPY/QQQ-raw-IEX-Bars werden nicht als individuelles Techuniversum ausgegeben. Earnings-/Erwartungsdaten sind für diese preisbasierte Originalfrage keine zusätzliche Pflicht; nur die separate Erweiterungsfrage „nach schlechten Quartalszahlen“ würde diese Daten benötigen.
5. Ergebnis lautet je Befund `INSUFFICIENT_DATA`, `REQUIRES_NEW_STRATEGY` oder `UNSUPPORTED`; es gibt keinen gestarteten Originaltest. Falls eine preisbasierte Ersatzhypothese sinnvoll ist, zeigt Strategy Detail exakt die verlorene Ereignissemantik. Nur separate bewusste Annahme erzeugt eine gebundene Nachfolgefassung; ein RSI-Test bestätigt nie automatisch die definierte Rückgangs-/Ein-Wochen-Erholung. Ein exakt passender Katalog-/Datennachweis kann dagegen EXACTLY_TESTABLE ergeben; Blockade ist keine vorab erzwungene Antwort.

**Persistenz/Wiederaufnahme:** Versionsgebundene Idee/Dialog/Turn und bestehende Processing-Entscheidung; Task ist die geplante Projektion mit gebundenem Intent. **Fehler:** abgelaufene Vorschau muss neu vorbereitet werden; Provider-Timeout wird als unklarer Versand gezeigt, nicht still wiederholt; lokaler Weg bleibt erreichbar. Widerspruch in Folgebeitrag zeigt Diff und verlangt gezielte Bestätigung der betroffenen Annahmen. **Abnahme:** Originalfrage bleibt nach Reload erkennbar, Blocker nennt benötigte Daten/Strategie und nächste zulässige Option; kein semantisch falscher erfolgreicher Test. Übergang zu J3, für eine abweichende Strategie nur nach expliziter Neufassung.

## J5 – Bewusst begrenzte Paperorder vorbereiten und verfolgen

**Persona/Ziel:** P3 stellt eine menschliche LIMIT-Anforderung. **Vorbedingung:** Bestehende Paperkontoprüfung, bestätigtes Profil/Budget, frische Reconciliation und alle bestehenden serverseitigen Gates. Agenten senden niemals Orders.

1. Portfolio Hub → Guided Paper Order zeigt isolierten Paperkontext. Falls Einrichtung fehlt, führt ein Schritt zum vorhandenen geschützten Setup; Rückkehr liest Zustand erneut und startet nichts.
2. Nutzerin wählt ein tatsächlich zulässiges Instrument, Seite und LIMIT-Bedingungen. Die geplante additive Preview bindet Menge, Preis, Budget, Evidenz und Gültigkeit; sie ist noch keine Reservation oder Riskfreigabe. UI rechnet keine Riskfreigabe selbst.
3. Review zeigt Verpflichtung, menschliche Herkunft, Bedingungen, Datenstand und verbleibende Ungewissheiten. BUY quantity × LIMIT ist eine Bruttonotionalobergrenze, SELL quantity × LIMIT eine Bruttonotionaluntergrenze; Gebühren/Reserve bleiben getrennt und tatsächlicher Ausführungserlös unbekannt. Mensch bestätigt genau diese Anforderung ausdrücklich über den geplanten einmalig previewgebundenen Guided-Anforderungspfad. Jede materielle Änderung braucht neuen Plan/Review.
4. Async-Vorgang wird angenommen; Activity Timeline und Paperdetail zeigen bestätigt/pending statt sofort „gekauft“. Fills werden erst nach Backendbestätigung Bestand.
5. Nutzerin kann neue Einstiege pausieren, Kill anfordern und eine zulässige offene eigene Order stornieren. Cancel ist keine Liquidation; `CANCEL_PENDING` bedeutet noch kein freies Budget.

**Persistenz/Wiederaufnahme:** vorhandene Paperoperations-, Order-/Client-/Tracebindung und idempotente Kommandos. **Fehler:** Timeout nach möglichem Submit zeigt ungeklärt; erneute Prüfung nutzt unveränderten Schlüssel und Verpflichtung, kein Ersatzkauf. Stale/Rate-Limit/Fremdaktivität zeigen konkrete Sperre und letzte bestätigte Zeit. **Abnahme:** Reload findet offene Operation vor abgeschlossenen; keine Doppelorder, kein LIVE und kein automatischer Verkauf. Übergang zu J6 bei Störung.

## J6 – Nach Sitzungsverlust und ungeklärtem Vorgang sicher zurückfinden

**Persona/Ziel:** P3/P2 versteht nach Netzverlust, was tatsächlich geschah. **Vorbedingung:** Aufgabe oder Paperoperation wurde zuvor serverseitig angenommen oder der Status ist unklar.

1. Verbindungsabbruch markiert Daten als veraltet, sperrt neue Freigaben und zeigt letzte Bestätigung; unabhängige vorhandene Pause-/Killwege bleiben anforderbar, ohne Erfolg vorwegzunehmen.
2. Bei 401 werden sensible flüchtige Felder und lokale Sendefassungen geleert. Nach erneuter lokaler Anmeldung kehrt die Route zum lesenden Detail zurück, führt aber kein Kommando aus.
3. Activity Timeline listet offene/ungeklärte Vorgänge und kennzeichnet Quellkontext Research, Paper oder getrennte Demo/Fake-Historie. Auswahl öffnet AI Task Detail oder Guided Paper Order in Statusansicht.
4. UI lädt autoritativen Zustand/Version und Tracebelege. Fehlende Fortsetzungsseiten bedeuten „noch nicht vollständig“, nicht „es existiert nichts“.
5. „Vorgang erneut prüfen“ fragt denselben vorhandenen Vorgang ab beziehungsweise nutzt ausschließlich den bereits vorgesehenen unveränderten idempotenten Retry. Veraltete Freigaben werden nicht wiederverwendet. Eine fachliche Wiederaufnahme braucht weiterhin ihre eigenen aktuellen Gates.

**Persistenz/Wiederaufnahme:** Timeline ist geplante begrenzte Projektion vorhandener Events, keine neue Orchestrierung. **Fehler/Abbruch:** bei fortgesetztem Backendausfall bleiben Zustand und Blocker unbekannt/veraltet; keine erfundenen Endresultate. Fehlende fremde ID ergibt sichere Nichtverfügbarkeit ohne fremde Daten. **Abnahme:** Nutzer kann Quelle, letzten bestätigten Zustand, offene Verpflichtung und nächsten zulässigen Schritt benennen; keine doppelte Verarbeitung oder Order durch Navigation.



### Verbindliche Fehler- und Wiederaufnahmematrix

Die folgende Matrix gehört zu J6; jeder Fall ist einzeln mit persistiertem Zustand, Reload und erlaubter Folgeaktion abzunehmen. Ein Blocker ist ein sichtbares Ergebnis, kein endloser Spinner.

| Pflichtfall | Verständlicher Befund | Erlaubte Folgeaktion / Wiederaufnahme | Ausgeschlossen |
| --- | --- | --- | --- |
| Provider deaktiviert oder nicht eingerichtet | Externe Verarbeitung nicht verfügbar, lokaler Dialog bleibt nutzbar | Lokal klären, Hypothese bestätigen und begründete Testbarkeit lesen; menschliche Einrichtung optional separat | Verdeckter Providerwechsel oder Versand |
| Budget ausgeschöpft | Betroffener Run/Provider hat kein verfügbares Budget; mögliche offene Reserven sichtbar | Bestehende Ergebnisse lesen, lokal weiterklären; Budget nur über tatsächlich erlaubte gesonderte menschliche Aktion ändern | Automatische Budgeterhöhung, Neurun oder falsche Kostenfreiheit |
| Veraltete Daten | Letzte bestätigte Beobachtung samt fehlendem Frischenachweis | Gespeicherte Daten lesen; vorhandenen Aktualisierungs-/Readinessweg öffnen; Gates neu prüfen | Aktuelles Datum als Beweis frischer Daten oder neue Paperfreigabe |
| Daten-/Nutzungsrechte fehlen | Konkrete fehlende/unverifizierte Rechte und betroffene Fähigkeit | Vorhandene zulässige lokale Befunde lesen; Voraussetzungen erklären | Lizenzannahme, Providerabfrage oder Start trotz Sperre |
| Hypothese/Markt/Strategie nicht unterstützt | Original und konkrete Fähigkeitsgrenze erhalten | Lokal präzisieren, verwerfen oder getrennte Ersatzhypothese ausdrücklich annehmen | Stille Umdeutung zu RSI/ETF oder dynamische Strategieausführung |
| Quantgrant ungültig, abgelaufen oder Bindung verändert | Freigabe passt nicht mehr zu Version, Daten, Budget oder Definition | Aktuelle Definition neu prüfen, neue gültige Freigabe ausdrücklich entscheiden | Alte Freigabe übertragen, Batch automatisch starten |
| Paper pausiert oder HALTED | Tatsächlicher Betriebszustand und Sperrgrund | Lesen, Reconciliation prüfen, zulässiges Cancel/Pause/Kill; Fortsetzung nur über bestehenden expliziten gegateten Pfad | Forschungsergebnis als Resume, automatisches Entsperren oder Liquidieren |
| Sitzung abgelaufen | Anmeldung nötig, flüchtige sensible Felder geleert | Lokale Sitzung aktivieren und vorhandenen lesenden Detailstand laden | Automatischer Replay von Freigabe, Versand oder Order |
| Provider OUTCOME_UNKNOWN / Broker UNKNOWN | Versand-/Brokerstatus ungeklärt, letzte Bestätigung und bestehende Bindung sichtbar | Vorhandenen Vorgang prüfen; begrenzte Recovery/Reconciliation und idempotente Regeln befolgen | Ersatzrun/-order, Erfolg behaupten, Reserven vorzeitig freigeben |

**Erstnutzernachweis ohne Schlüssel:** J1 → explizite Demoauswahl mit demoRunId → bestätigter synthetischer Snapshot → bewusste lokale Analyseaktion → persistierter Qualitäts-/Bestandsbefund mit analysisId, Methoden- und Quellrevision → S7/S8 und S10 nach Reload. Externes I/O wird mit Erwartung null gezählt. Fehlende optionale Bewertung/Historie bleibt unbekannt; vorhandene Cash-/Mengen-/Qualitätsdaten ergeben einen tatsächlichen lokalen Befund. Der zusätzliche Hypothesendialog J4 ersetzt diesen Weg nicht. Weder Providerprobe noch externe Budgetfreigabe ist erforderlich.
