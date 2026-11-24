# Design

## Context

Siehe proposal.md für Motivation, discovery.md für geprüften Istzustand. Basis: veröffentlichtes main `0442f29571e53b983e9e08f2180c4bd3536e0b6a`. Angular/Material 22.2.2 und TypeScript 6.0.2 sind gepinnt. Die Rootkomponente mountet den Supervisor ohne Angular Router. Research und Quant sind vorhanden, aber deren Deltas sind noch nicht vollständig in Main-Specs konsolidiert. IEF verändert deren Semantik nicht.

## Goals / Non-Goals

**Goals:** Eine gemeinsame Experience-Schicht über bestehenden Leseports und typisierten Befehlen; exakte Datenqualität; sichere Anfängerwege und weiterhin technische Detailansichten. Die Planung spezifiziert vollständig, welche API additiv entsteht und welche existierende Operation weiterverwendet wird.

**Non-Goals:** Zweite Portfolioquelle, zweiter Supervisor, generischer Agent-execute-Endpunkt, neue Brokerintegration, Vollhistorien-/FXengine oder automatische Strategiepromotion. Keine öffentliche Freigabe durch lokale Onboardingantworten.

## Decisions

### D1 Gemeinsame Experience-Schicht mit additiven Verträgen

`services/control_api` erhält eine begrenzte Experience-Projektion auf bestehenden Leseports; keine neuen direkten Handelsrechte. Die vorhandenen Komponenten `services/control_api`, `services/runtime` und `packages/agent_research` bleiben zuständig. Relevante DTO-/Schemafamilien stehen in den drei Modellentwürfen.

Geplante API unter `/api/v1/experience`:

| Pfad | Semantik / ausführendes Paket |
| --- | --- |
| `GET/PUT /preferences` | Betreiberpräferenzen, expectedVersion + Idempotency-Key / IEF-02 |
| `GET /portfolio?context=...&limit=&cursor=` | konsistente nullable Finanzprojektion und revisionsgebundene Positionsseiten / IEF-03 |
| `POST /portfolio-analyses`, `GET /portfolio-analyses/{analysisId}` | begrenzter lokaler Analyseauftrag / IEF-04 |
| `GET /insights?context=...&snapshotId=...` | deterministische versionierte Befunde / IEF-04 |
| `GET /strategies` und `/{id}/versions/{version}` | Register, Ergebnisreferenzen, getrennte Paperfähigkeit / IEF-05 |
| `GET /tasks` und `/{taskId}` | vorhandene Run-/Analysequellen mit UIstatus / IEF-06 |
| `GET /activity` | begrenzte, redigierte, quellengebundene Chronik / IEF-08 |

Alle neuen GETs haben begrenzte Responses und keine Providerwirkung. Listenlimit Standard20, Maximum100; opake kontext-/akteur-/filtergebundene Cursor, expliziter Ablauf mit Problemantwort und neuer Snapshotnavigation. Finanzwerte Decimalstrings, UTC, Fehler RFC9457, keine freien Ziel-URLs in Aktionsvorschlägen. Geplante Einzelportfoliokontexte: `DEMO`, `FAKE`, `ALPACA_PAPER`, mit jeweils eigenem Namespace und Snapshot. Noch nicht vorhandenes Read-only-Konto erscheint als Zukunft, niemals als aktive Datenquelle.

IEF-01 liefert erst den additiven OAS-Vertrag und reproduzierbaren generierten Client plus Versions-/Beispielfixtures. Keine Placebo-HTTPimplementation: Route-Parität wird im Integrationsgate nach Implementierung aller neuen Routen geprüft; Vertragsdateien allein beweisen keine laufende Fähigkeit. Bestehende Operationen und deren exakte Schemas bleiben kompatibel. Alternative clientseitige Zusammenführung verworfen, da sie Ledgerrevisionen und Autoritätsgrenzen verlieren würde.

### D2 Präferenzen sind keine finanzielle Autorität

Eine additive Präferenztabelle bindet `actorId` aus vorhandener Session, `schemaVersion`, optimistische `version`, `experienceLevel`, freiwillige strukturierte Ziele/Horizont/Verlusttoleranz, gewählten Darstellungskontext und `onboardingSteps` mit `COMPLETED|SKIPPED|UNSEEN`, UTC-Zeiten. Keine Freitextbiographie, kein Profiling für Kaufempfehlungen. GET ohne Datensatz liefert explizite Defaults/Version0. PUT verlangt expectedVersion, idempotenten Nutzlasthash und feste Wertelisten. Reset setzt Präferenzen zurück, löscht weder Finanzledger noch verpflichtende Auditbelege. Späteres Mehrbenutzersystem muss Rollen, Sessionidentitäten und Datentrennung separat definieren; keine vorgetäuschte Mandantenfähigkeit.

### D3 Ein Portfolioledger, zwei Ebenen von Erkenntnissen

`portfolio-read-model.md` ist die Feld-/Verfügbarkeitsentscheidung. V1 macht vorhandene Werte zugänglich; historische Rendite, Drawdown und unbekannte Marktwerte bleiben begründet unverfügbar. Deterministische Insights dürfen unmittelbar vorhandene Mengen, belegte Gesamt-Exposure/Cash und Qualitäts-/Reconciliationgründe erklären. Positionsgewichte oder Konzentration werden erst bei vollständig belegten kompatiblen Marktwerten/Nennern gerechnet; andernfalls lautet das Ergebnis „Für eine zuverlässige Gewichtung fehlen Bewertungsdaten“. Kein nackter Durchschnittspreis wird zum aktuellen Marktpreis.

IEF-04 persistiert bei ausdrücklich angeforderter lokaler Analyse einen unveränderlichen Befund mit actor/context, snapshotId, Methodenrevision, Eingabereferenzen, Kennzahlenverfügbarkeit und Trace. Das ist eine abgeleitete Analysequelle, kein Finanzledger und keine zweite Jobqueue. Ein synchron begrenzter deterministischer Analyseauftrag benötigt keine Agentenrolle oder Modellfreigabe und erscheint über `sourceType=PORTFOLIO_ANALYSIS` im Task Center; `runId=null`. IEF-06 verbindet diese Quelle und vorhandene Runs in einer gemeinsamen Leseprojektion. Ein dauerhaft laufender autonomer Portfolioagent bleibt außerhalb von IEF.

Für die lokale Analyse wird additiv `POST /experience/portfolio-analyses` mit snapshotId, expectedSnapshotVersion, Idempotency-Key geplant; `GET /experience/portfolio-analyses/{analysisId}` liefert das Ergebnis. Nach Replay entsteht derselbe Befund. Laufzeit/Output sind fest begrenzt; inkompatible Snapshotstände liefern Konflikt und neue Vorschau.

Optionale KI-Erklärung verwendet vorhandenen Gateway und Supervisor mit **neuer**, eng begrenzter Zweckversion `PORTFOLIO_EXPLANATION_V1`: ausschließlich freigegebene deterministische Befunde und minimierte Einheiten, keine Kontoreferenzen, rohen Positionstabellen oder Präferenzprofile. Bestehendes `HYPOTHESIS_INTERPRETATION_V1` wird nicht zweckentfremdet. Der neue Zweck benötigt versionierten Projektor, Schema, Policy, erlaubte Rolle/Tools, konkrete Datenvorschau, eigene Kostenentscheidung und Output-Evidenzvalidator. Ein spezialisierter begrenzter RunKind wird nur innerhalb der vorhandenen Runtime ergänzt. Fehlt diese Fähigkeit, bleibt lokale Erklärung vollständig nutzbar und Modellstart gesperrt. Kein Anspruch auf personalisierte Anlageberatung.

### D4 Aufgaben sind Quellenprojektionen vorhandener Arbeit

`agent-task-model.md` definiert reale Zustände und Aktionsverweise. Ergänzt werden versionierte Intent-Metadaten an bestehenden Researchquellen: Akteur, Typ, Zweck, Nutzerabsichtsrevision, Daten-/Toolallowlist, Agent-/Policy-/Modellbindungen und Ergebnisreferenzen. Fachliche Freigaben bleiben in ihren bisherigen Stores; UIlabels erzeugen keine neuen Transitions. Kein freies Taskexecute. Freie Researchanlage geht über bestehende Dialog-/Ideenbefehle; der Klick bereitet vor. IEF-04 ergänzt nur den begrenzten Erklärungszweck aus D3. Alternative universelle neue AgentTaskqueue verworfen, weil sie Budget, Fence und Approval duplizieren würde.

### D5 Paperpreview als dünne Ergänzung

`guided-order-design.md` beschreibt zwei additive Endpunkte `/paper/order-previews` und `/paper/guided-order-requests`. Preview ist read-/prüfungsseitig ohne Riskreservation oder Broker-I/O. Die zweite Operation konsumiert den Previewbeleg atomar und erzeugt die vorhandene Human-Anforderung unter denselben Idempotenz-/Accountgrenzen. Kein neuer Submitpfad; alte `/paper/order-requests` bleiben unverändert. BUY auf bereits gehaltene Position bleibt gesperrt. Cash-/Gewichtsauswirkungen bleiben nullable, falls passende Bewertung fehlt. LIMIT-SELL zeigt Mindestbruttoerlös, keine falsche Kostenobergrenze.

### D6 Routing, Darstellung und aktuelle Dokumentation

IEF-01 führt `@angular/router` passend zum gepinnten Angularstand als explizite neue Abhängigkeit ein; IEF-08 integriert Cockpit und Timeline. Bestehende Querylinks werden gelesen, in sichere interne Routen übersetzt und per Reload getestet. Keine Navigation startet eine Wirkung. Betrieb/Pause/Kill bleiben direkt erreichbar. Gemeinsamer Zustand beschränkt sich auf Kontext/Filter/Navigation; Finanz-/Riskwahrheit bleibt serverseitig. NgRx nur, wenn tatsächlich mehrere Views dasselbe UIstate benötigen.

Die zehn Screenverträge und der Flow stehen in ux-architecture.md, navigation-design.md und user-journeys.md. Material-Stepper erhält optionale Schritte, aussagekräftige Labels, Fehleransagen und kleine Displays mit vertikaler Darstellung. Quelle der aktuellen Planung: [offizielle Material-Stepper-Dokumentation](https://material.angular.dev/components/stepper/overview), per Context7 `/angular/material2-docs-content` am 23.11.2026 abgefragt. Der dortige Katalog ist kein Versionsnachweis; maßgeblich bleiben package.json/Lockfile22.2.2. Die neue Routerabhängigkeit muss im Apply gegen exakt diese Version und offizielle Routingdokumentation validiert werden.

### D7 Readiness und Veröffentlichung

IEF-04 (externe Erklärung), IEF-06 (Researchintegration) und IEF-09 benötigen die ARP-10-Nachweise. Diese technische Voraussetzung ist am aktualisierten veröffentlichten Stand0442f29 erfüllt: #105 ist CLOSED, Research45190975614 und Contracts45190976026 sind success. Die Nachweise in `docs/evidence/arp-10-integrated-readiness.md` wurden gelesen. Native Abhängigkeiten bleiben als Provenienz erhalten; reale Provider-, Zweck-, Modell-, Credential-, Tarif- und Fachfreigaben bleiben je Bindung gesondert. IEFs neuer Erklärungszweck ist durch ARP-10 nicht bereits autorisiert. Offline-Tests, echte Operatorprobe und modellgebundene Fachfreigabe bleiben getrennte Evidenzklassen. Alle späteren IEF-Änderungen benötigen ihre eigenen grünen Checks.

## Risks / Trade-offs

- [Wenig Performancewerte verfügbar] → Ehrliche Datenlücken und sinnvoller lokaler Qualitätsbericht; keine Historienengine im Experiencechange verstecken.
- [Gemeinsames Dashboard verwischt Herkunft] → persistenter Kontext, Ledger-/Quellrevision pro Kennzahl, keine gemischten Summen.
- [Neue Erklärung eröffnet Datenausleitung] → neuer Zweck mit eigenem Projektor, granularen Entscheidungen und Negativtests; Standard gesperrt.
- [Routermigration bricht alte Links] → Adapter und produktive Reload-/Back-/Forwardtests; Betriebskontrollen früh erhalten.
- [Einzelne Personenpräferenzen verleiten zu Geeignetheitsbehauptung] → feste Informations-/Researchtexte und öffentliche Freigabe erst nach gesonderter Rechtsprüfung; siehe security-and-permissions.md.

## Migration Plan

Nach separatem Applyauftrag zuerst IEF-01-Vertragsbasis; danach additive Präferenzen/Analysemetadaten und projektionseigene Tabellen mit neuen Migrationen nach dem dann tatsächlichen letzten Migrationsstand. Bestehende Finanz-/Runhistorie bleibt unverändert. Backfills erzeugen höchstens abgeleitete Quellenverweise, keine neue historische Performance oder nachträgliche Providerfreigabe. Neue Experience-UI bleibt bis kompatiblen Serverfähigkeiten ausgeblendet bzw. erklärt gesperrt. Rollback schaltet neue Routes/Projektoren ab und lässt additive persistierte Daten sowie bestehende Paper-/Researchpfade intakt; keine Löschung von Finanz-/Auditdaten. Wiederanlauf verwendet reale Run-/Orderzustände und hält Entrygates geschlossen.


### Vertragspräzisierung aus Architecture Review

DEMO bindet zwingend eine explizite demoRunId; Quelle ist der vorhandene Readport hinter `GET /api/v1/demo/runs/{runId}/snapshot`. FAKE verwendet den internen Readport hinter `/api/v1/snapshot`; ALPACA_PAPER verwendet die isolierten Paperleseports. Die bestehenden SupervisorSnapshot-Wires zeigen portfolioEquity, optional portfolioCash, Positionen, asOf/cursor, aber nicht alle Ledger-/Reconciliationrevisionen. IEF-03 erweitert die interne Projektion um ihre echte Quellrevision innerhalb konsistenter Lesetransaktion; fehlende Bindung bleibt UNAVAILABLE und kann niemals aus asOf erfunden werden. Kein Demo-Fallback auf ein beliebiges letztes Run. Preferences speichern eine explizite Kontextreferenz ohne Credentials.

Für `PORTFOLIO_EXPLANATION_V1` erweitert IEF-01 die vorhandenen `/api/v1/research/agent`-Verträge additiv um zweckdiskriminierte Preview-/Decision-/Status-/Widerrufsschemas. IEF-04 erweitert denselben Processingstore und Run-Supervisor um erlaubte Purpose-/RunKindwerte und deren SQL-Constraints; der alte Hypothesenvertrag und dessen strengere Klassifikation bleiben unverändert. Preview bindet analysisId/version, Projektionshash, Zweck-/Projektor-/Schemasversion, Agent-/Toolpolicy, Provider-/Modell-/Credentialgeneration, Preis-/Budgetpolicy und validUntil. Die Entscheidung referenziert genau diese Vorschau; Status liefert inhaltsfreien Verbrauchs-/Widerrufszustand. Widerruf sperrt Dispatch bzw. spätere Ergebnisannahme nach bestehender Commitgrenze. Es gibt keine persistierte Promptrekonstruktion, keinen neuen Transport und keinen universellen Zustimmungsgrant. Externe Erklärung ist optional und benötigt ARP-10; lokale Analyse bleibt unabhängig sinnvoll.
