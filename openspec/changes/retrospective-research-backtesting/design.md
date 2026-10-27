# Design

## Context

Siehe proposal.md und discovery.md. Basis ist `5bffd2488ad3b42b3a2284c75d9a6ff4db16437d`. Bestehende Implementierung und abgeschlossene BKT-/RDR-Deltas definieren den Strict-Pfad. Kalender, Qualität, Coverage und Simulator prüfen heute selbst historische Sichtbarkeit; ein weniger strenger SQLfilter allein genügt nicht.

## Goals / Non-Goals

**Goals:** nutzbarer expliziter lokaler Forschungsmodus, unveränderliche Datengrundlage, überprüfbare Grenzen, keine zusätzliche Handelsautorität.

**Non-Goals:** Total Return, neue Datenanbieter/Adjustierungen, autonome Agenten, freie Programme, Parameteroptimierung und produktive Freigaben. Keine Änderungen am kanonischen Vertrag während Propose.

## Decisions

### D1 / ADR-RRB-001 Gemeinsamer Rechenkern, getrennte Policies

| Alternative | Sicherheit / Reproduzierbarkeit | Testbarkeit / Wartbarkeit | Migration / Agentenverträge |
|---|---|---|---|
| Separate Engine | Isolation möglich, aber doppelte Fill-/Kostenlogik droht auseinanderzulaufen | doppelte arithmetische und Recoverytests | großer Scope, divergierende Ergebnisse |
| Gemeinsame Engine, getrennte typisierte Dataset-Policies | explizite Auswahl und unveränderte Strict-Gates, jede Policy versioniert | gemeinsame Arithmetik; Paritäts- und Verwechslungstests | additive Verträge, klar gebundene agententaugliche Ein-/Ausgaben |
| Strict-Materialisierer mit Lockerungsflag | versteckte Bypässe in SQL, Kalender, Coverage und Simulator | kombinatorische Flagtests | geringerer Erstaufwand, hohes Regressionsrisiko |

Gewählt: zweite Alternative. `StrictPitPolicyV1` bleibt Standard einschließlich bisheriger +60s/+300s-Sichtbarkeit und Fehlercodes. `RetrospectivePolicyV1` besitzt eigenen Resolver, Kalender-/Qualitätsassessor und Validierungskontext. Keine booleschen `ignore_pit`-Schalter. Gemeinsamer Simulator erhält eine geschlossene diskriminierte validierte Datasetvariante; Konstruktion nur über die jeweilige Policy, Revalidierung von Modus/Policy/Manifestbindung bei Auswertung. Keine frei vom Request übergebbare Validierungsfunktion. Strict-Simulatoraufrufe ohne neuen Kontext verwenden ausschließlich den bisherigen Strictpfad. Falsche Dataset-/Policykombination scheitert geschlossen. Gemeinsam bleiben Strategien, Ledgerarithmetik, Rundung, Fillzeitfolge, Metriken und Folds.

`BarRevision` wird weder geklont noch zeitlich verändert. `DataReference.cutoff` darf retrospektiv nicht als lokaler Empfangsnachweis umgedeutet werden: neue typisierte Bindung trennt simulierte Marktgrenze und tatsächliche Revisionsevidenz. Kein zusätzlicher ungesicherter Auswahlpfad im Strict-Port; Retrospektivresolver erhält ausschließlich read-only Researchports. Alte Readiness-ELIGIBLE-Semantik bleibt strikt.

### D2 / ADR-RRB-002 Snapshot und Identität

Der erste Vorschaujob bindet `research.version_state` und DB-UTC-Zeit sowie Code-/Trustversion in einer kurzen bestehenden Snapshottransaktion. Alle Seiten verwenden dieselbe Commitversion und Zeit; Ausschluss später commitender Writer folgt bestehendem versioniertem Commitprotokoll. Materialisiert wird je vollständiger Baridentität die höchste zulässige `local_revision`, innerhalb beider Snapshotgrenzen. Identische Revisionsnummern mit widersprüchlichem Inhalt sind Fehler, kein UUID-Tiebreak als fachliche Auflösung. Kalender, Qualität und Maßnahmen verwenden denselben Snapshot und explizite Revisions-/Trustreferenzen; negative widersprüchliche Befunde dürfen nicht durch einen positiven älteren Beleg verdeckt werden.

Die komplette benötigte Sicht umfasst Trainings-/Warm-up- und Testgrenzen, Instrument/Provider/Feed/Intervall/Adjustierung. Revisionen samt Fingerprints bleiben append-only erhalten. Ein persistiertes Datasetmanifest referenziert jede gewählte Revision und erwartete Baridentität; keine spätere Auswahl über `latest`. Wiederholung liest exakt diese Referenzen, prüft Fingerprints und scheitert bei Verlust. Retention darf gebundene Revisionen nicht löschen. CPUauswertung läuft außerhalb der Snapshottransaktion. Maximum 366 Tage, 12 disjunkte Folds, 100000 Revisions-/Metadatenzeilen, 12MiB Publikation und vorhandene Queue-/Lease-/Zeitlimits bleiben bestehen; die neue Vorschau teilt denselben Workerbudgetpool.

Neue Manifestversion `retrospective-experiment-v1` bindet `researchMode`, `selectionPolicyVersion`, Datasetrevisionen, echte Beobachtung/Verfügbarkeit, Snapshotversion/-zeit, Kalenderquelle/-version/-zweck, Qualitypolicy/-befunde, Truststand, Strategieversion/Parameter, Kosten und sämtliche bestehenden Simulator-/Metrik-/Warm-up-/Buildversionen. SHA256 über kanonische bestehende Decimal-/UTCserialisierung. Laufzeitmetadaten und aktuelle Warnungen liegen außerhalb des fachlichen Hashs. Gleicher gebundener Inhalt erzeugt gleiche Ergebnisse. Neuer Build oder neuer Snapshot ist ein neues Experiment.

Legacy-Strict-Manifeste und ihre Hashpräimages bleiben bytegleich. Neues Modusfeld ist an der Eingabe optional; fehlend und explizit STRICT_PIT normalisieren auf die bestehende Strict-Nutzlastidentität. Alte Idempotenzschlüssel bleiben wiederholbar. Retrospektive Nutzlast enthält expliziten Modus und Policyversion. Gleicher Schlüssel mit anderem Modus erzeugt 409 IDEMPOTENCY_CONFLICT.

### D3 / ADR-RRB-003 Vier Zeitachsen und Ex-post-Revisionen

1. `barStart/barEnd`: historisches Marktereignis.
2. Unveränderte `availableAt/ingestedAt`, optionale echte `firstObservedAt/publishedAt`: tatsächliche Evidenz; fehlende Providerpublikation bleibt unbekannt.
3. `snapshotAt/snapshotVersion`: heutige Datensatzerstellung mit vollständiger Commitsicht.
4. `valuationAt/knowledgeAt`: simulierte Entscheidung, nach Signalbarende +60s.

Retrospektiv dürfen Marktbarenden höchstens am jeweiligen Signalwissen liegen. Die danach bereits begonnene 15Min-Kerze darf nicht füllen; gemeinsame next-regular-bar-open-Regel bleibt. Ergebnisdaten sind gesondert zugänglich; eine historische +300s-Grenze bezeichnet keinen erfundenen lokalen Empfang. Historische Beobachtungsfristen gelten nur im Strict-Pfad. Retrospektiv gelten snapshotgebundene Ergebnisrevisionen, weiterhin getrennt von Strategiedaten.

Wichtig: Eine heute korrigierte ältere Bar kann im neuen retrospektiven Snapshot die damalige simulierte Entscheidung ändern. Das ist unvermeidbares Ex-post-Revisionsrisiko und wird im Report genannt. Verboten sind zukünftige Marktbarinformationen und die Änderung eines bereits eingefrorenen Experiments; keine falsche Zusage historischer Revisionsreinheit. Gleiche Trennung gilt für ex-post bekannte Kalender.

### D4 / ADR-RRB-004 Qualitäts- und Kapitalmaßnahmenpolicy

Alle Regeln gelten einheitlich für Strategie und Cash-/Buy-hold-Benchmarks, pro Fold vom gebundenen Warm-up bis Testende; keine automatisch abgeschnittenen schlechten Bereiche.

| Befund | Entscheidung v1 | Bessere Evidenz / spätere Voraussetzung |
|---|---|---|
| fehlende benötigte Bars, unplausible OHLCV, widersprüchliche Revisionen | BLOCKED | vollständige konfliktfreie neue Snapshotversion |
| unbekannte Kalendergrenzen oder Sitzungskonflikt | BLOCKED, Nenner unbekannt | vollständiger versionierter Kalender inklusive DST/Earlyclose |
| gemischter Feed/Adjustierung, ungeklärte Identität | BLOCKED | einheitliche belegte Semantik |
| bekannter Split, Reverse-Split, Spin-off oder sonstige nicht modellierte strukturelle Maßnahme | BLOCKED | späterer eigener Change für Eventledger und Quantity-/Cashanpassung |
| widersprüchliche Maßnahmenbelege | BLOCKED | explizite vertrauenswürdige konfliktauflösende neue Evidenz |
| fehlender Maßnahmenbeleg | EXPLORATORY_WITH_LIMITATIONS, UNKNOWN | vollständige glaubwürdige Ereignisabdeckung; kein Schluss aus Preisruhe |
| bekannte reine Bardividende | EXPLORATORY_WITH_LIMITATIONS, DIVIDENDS_EXCLUDED | Cashentitlement-/Reinvestmentmodell für späteres Total Return |
| Betrag von `open[n]/close[n-1]-1 >= 0.20` absolut, über alle benachbarten benötigten regulären Bars inklusive Sitzungswechsel | BLOCKED, `raw-discontinuity-v1` | Preisbruch aufklären; v1 hat keinen Override |
| fehlende damalige Verfügbarkeit, heutiger Kalender, heutige Korrekturen | stets EXPLORATORY_WITH_LIMITATIONS | unabhängige damalige Einzelbelege wären für Strict erforderlich |
| IEX-Teilmarkt, heutiges SPY-/QQQ-Universum | sichtbare Einschränkung | zugelassener umfassender Feed und historisches Universum wären separate Changes |
| Lizenz UNKNOWN | lokale interne Exploration mit Warnung; kein Export/neuer Abruf | Betreiber klärt reale Nutzung privat |
| Lizenz DENIED oder expliziter Nutzungswiderruf | BLOCKED | belegte erneute Erlaubnis, kein automatischer Fallback |

Der Sprungtest ist ausschließlich konservative Anomalieerkennung, kein Splitdetektor oder Maßnahmenfreiheitsbeleg. Er kann echte Marktbewegungen blockieren und kleine/andersartige Maßnahmen übersehen. Raw-only vermeidet unbekannte Adjustierungsrevisionen; Dividenden fehlen in Cash und Rendite. Auch bei NO_ACTIONS-Beleg bleibt Ergebnis price-only, explorativ und ohne Profitabilitäts- oder Handelsfreigabe. Lizenzwarnungen erteilen keine Rechte. Reale Datenweitergabe/Exports sind nicht Teil dieses Changes. Aktuelle Revokation negativer Evidenz verhindert neue Ausführung auf alter Vorschau; alte Ergebnisse bleiben unverändert und tragen getrennte aktuelle Warnungen. Bei nicht verfügbarer aktueller Trustprüfung wird kein neuer Lauf zugelassen.

### D5 Report und additive HTTPverträge

Geplant im kanonischen OpenAPI bei RRB-01, ohne hier dessen Änderung vorwegzunehmen:

| Operation / Schema | Additive Änderung |
|---|---|
| POST `/api/v1/research/backtests/runs` | optional `researchMode`; retrospektiv zwingend `selectionPolicyVersion=retrospective-v1`, `assessmentId`, `assessmentHash`; Strict lehnt retrospektive Zusatzfelder ab |
| bestehende Listen-/Detail-/Ergebnisseiten | optionale Schemafelder, serverseitig überall emittiert: researchMode, selectionPolicyVersion, experimentVersion, snapshotReference, reliabilityReportId, resultLimitations; alte Datensätze werden als Strict projiziert |
| POST `/api/v1/research/backtests/assessments` | eigener geschlossener retrospektiver Vorschauauftrag mit demselben Protokoll, Idempotenz, 202 und dauerhafter assessmentId; keine Simulation |
| GET `/api/v1/research/backtests/assessments/{assessmentId}` | Vorschauzustand, Eingabehash, Snapshot, Reportreferenz und Blocker |
| GET `/api/v1/research/backtests/reliability-reports/{reportId}` | paginierter strukturierter Report; limit 1..200, signierter report-/filtergebundener Cursor; Übersicht auf jeder Seite |

Katalog ohne Laufkontext bleibt modusneutral und benennt unterstützte Modi; jede laufbezogene Antwort trägt Modus. Bestehende Problemcodes/HTTPstatus bleiben; neue strukturierte retrospektive Blockiergründe innerhalb eigener Assessment-/Reportfelder, keine Umbenennung von Strict-Codes. Auth/Originprüfung erfolgt vor Seiteneffekten. Snapshot-/Assessment-/Protokollkonflikt ergibt 409 SNAPSHOT_CONFLICT, ungültige Version/Felder 400 INVALID_REQUEST, fehlende fremde Referenz 404, erschöpfter Dienst 503. Autorisierung und lokale Rollen wie Bestand; keinerlei freie URLs/Programme/SQLfelder.

`ReliabilityReportV1`: reportId/version/hash, assessmentId/runId soweit vorhanden, protocolHash, snapshotReference, mode/policy, pro Fold barCounts (expected/present/missing/unexpected, null bei unbekanntem Nenner), Revisionrefs, Findings mit Kategorie/Schwere/Zeitraum/Referenz, Dimensionen INTEGRITY/CALENDAR/CORPORATE_ACTIONS/HISTORICAL_AVAILABILITY/FEED/LICENSE. Qualitative Werte PASS/LIMITED/UNKNOWN/BLOCKED/NOT_EVALUATED; Gesamtzulässigkeit ausschließlich BLOCKED oder EXPLORATORY_WITH_LIMITATIONS. Keine gewichtete Note. Methodenabschnitt bindet vollständiges Manifest und Benchmarks. Stets Einschränkungen NOT_STRICT_PIT, PRICE_ONLY, DIVIDENDS_EXCLUDED, IEX_PARTIAL_MARKET, SURVIVORSHIP_BIAS, EX_POST_REVISIONS und NO_TRADING_AUTHORIZATION.

Vorschau verwendet dieselbe Policy wie Simulation und wird dauerhaft mit Snapshot gebunden. Retrospektive Erstellung referenziert exakt assessmentId/hash und identisches normalisiertes Protokoll. Änderungen im Formular verwerfen lokal die Vorschau; Worker prüft Bindung und aktuellen negativen Trust-/Rechtestand erneut. Er berechnet auf exakt diesem Snapshot und bindet einen unveränderlichen finalen Report an das Experiment. Keine Migration von Strict-Readiness-ELIGIBLE in einen retrospektiven Erfolgsstatus. Vorab- und Finalreports sind getrennte unveränderliche Versionen; keine Ergebnisveröffentlichung ohne vollständigen finalen Report.

### D5a Lizenz- und Maßnahmenports

Der bestehende mutable `research.source_status.license_status` liefert UNKNOWN/CONFIRMED/BLOCKED. Ein enges festes Read-only-SQLview projiziert ausschließlich Providerkennung, Status und vorhandene Änderungsmetadaten, niemals Credentials oder Providerantworten. Nur dieses View erhält SELECT für den Backtestworker, keine pauschalen Researchrechte. Eine unveränderliche `backtest`-Lizenzreceipt bindet den in derselben Vorschau-Snapshottransaktion gelesenen Zustand mit tatsächlicher Aufnahmezeit und Inhaltsfingerprint. Das ist keine erfundene historische Lizenzrevision. UNKNOWN bleibt UNKNOWN, BLOCKED wird DENIED, CONFIRMED erscheint als DECLARED_CONFIRMED mit weiterhin ungeprüften persönlichen Rechten. Vor Start/Publikation wird derselbe enge aktuelle Port erneut gelesen: BLOCKED verhindert neue Ausführung/Erfolg, Portausfall verhindert positive Freigabe. Eine historische Receipt wird dadurch nicht verändert. Bei alten Reports erscheinen aktuelle Warnungen separat. Diese minimale Leseberechtigung ist Teil der RRB-03-ACLprüfung, keine Finanz-/Credentialberechtigung.

Maßnahmenprüfung liest die konkreten typisierten `statement.actions` der gebundenen Evidenzrevisionen. Bestehendes `CoverageProof.no_actions` unterscheidet Split und CASH_DIVIDEND nicht ausreichend und darf retrospektiv nicht als alleinige Quelle dienen. Kalender-/Trustadapter prüfen heutige authentische Inhalte und aktuelle Widerrufe separat von nicht nachgewiesener historischer Zeitvertrauenswürdigkeit; fehlender Temporalbeleg darf Inhalt nicht als historisch bewiesen aufwerten oder retrospektiv pauschal sperren.

### D6 Persistenz, Runtime und UI

Additive Migration: nullable Modus-/Policy-/Manifestversion im Bestandsrun, neue Assessment-/Dataset-/Report-/Revisionbindungstabellen im `backtest`-Schema. Altzeilen bleiben physisch unverändert, Projektion deutet sie als Strict; veröffentlichte Althashes bleiben unverändert. Gebundene Inhalte append-only, Zustandsübergänge nur über bestehende fenced Jobports. Worker erhält nur Research-SELECT, das enge Lizenzview aus D5a und eigene Backtestwrites; keine globalen Tabellenrechte, Credentials, Netzwerke oder Mounts. SQLtests prüfen Ablehnung fremder Policykontexte und Manipulation der Bindungen. Control darf keine Evidenz/Barrevisionen schreiben. Vorbereitung künftiger Agenten besteht ausschließlich aus geschlossenen versionierten DTOs und sicheren Referenzen.

Die additive Migration erweitert die tatsächliche `guard_shared_worker`-DBbarriere aus Migration 0031 sowie die gemeinsame oldest-job-Zulassung um Assessmentjobs. Keine eigene unkoordinierte Parallelitätsgrenze: Runs, Strict-Readiness und retrospektive Assessments teilen Admission, Queue100, höchstens einen aktiven Workerjob und vorhandene Deadline-/Leasebudgets. DBseitiger Kompatibilitätstrigger verweigert vor Zustandsänderung jedes Claim/Publish einer neuen Variante ohne passenden transaktionslokalen Policyversionskontext. Neue Jobports setzen diesen explizit; alte Binärversionen setzen ihn nicht und können dadurch neue Zeilen auch mit ihrem unveränderten alten Claim-SQL nicht leasen oder verändern. Dies ist Versionskompatibilität, keine zusätzliche Autorisierungsrolle. Pflichtnegativtest führt exakt das alte Claim-SQL auf einer Queue mit retrospektivem ältestem Job aus und belegt rollback/keine Lease; gewöhnliche Strictjobs bleiben ohne Marker claimbar. Kein Mischbetrieb während Migration; Rollback stoppt/drainiert neue Worker, deaktiviert neue Erstellung und startet alte Worker nur nach separater Behandlung aller nichtterminalen neuen Jobs, ohne diese auf Strict umzudeuten.

UI: Materialselect Standard Strict, retrospektive explizite Auswahl mit Erklärung; Instrument/Strategie/Zeitraum aus Bestand. Vorschau erforderlich, Start erst nach vollständiger Rückmeldung. Modus, Snapshot, Data Quality, Corporate Action Status, Result Limitations und Experiment Version in Liste/Detail/Ergebnis sichtbar, Reportdialog mit Pagination. Keine voreingestellte rückwirkende Umdeutung alter Jobs. Sessionfehler, BLOCKED und unvollständige Reports verhindern Start. Reload über reale IDs, keine Browserpersistenz sensibler Daten. Ergebnisse und Qualitätsbewertung getrennt, ohne Profitfarbe als Zuverlässigkeitssignal.

## Risks / Trade-offs

- Unbekannte Maßnahmen können wirtschaftlich falsche Preisrenditen erzeugen → sichtbarer UNKNOWN, Preisbruchblocker, begrenztes Universum; kein Qualitätszertifikat.
- Heute korrigierte Vergangenheit enthält nicht rekonstruierbares Ex-post-Wissen → Snapshotreproduzierbarkeit ausdrücklich von historischem PIT getrennt.
- Gemeinsamer Simulator kann Strict-Regeln verwässern → geschlossene Typvarianten, unveränderter Default, Golden-Parität und adversariale Policyverwechslungstests.
- Alte abgeschlossene Changes fehlen in Main-Specs → explizite Basisreferenzen in discovery.md; Konsolidierung nur eigener späterer Auftrag.
- Providerrechte bleiben unbekannt → keine Beschaffung oder Weitergabe; Denied blockiert; reale Nutzbarkeit bleibt externe Voraussetzung.

## Migration Plan

Vertrag und Regressionfixtures zuerst, dann reine Policy-/Reportprimitive und additive Persistenz, anschließend gemeinsame Simulation, Worker/API und UI. Deployment ohne Aktivierung; Backtestdispatch bleibt standardmäßig aus. Rollback deaktiviert neue Erstellung/Dispatch und erhält unveränderliche Reports/Manifeste; keine destructive Downmigration gebundener Daten. Alte Binärversionen dürfen neue Varianten nicht übernehmen; Übernahmefilter sperrt unbekannte Manifest-/Policyversionen vor Lease. Wiederholung alter Strict-Läufe und alte Idempotenzschlüssel sind Pflichtabnahme.

## Open Questions

Keine offene fachliche Architekturentscheidung. Persönliche Provider-/Speicherrechte und reale historische Datenqualität bleiben extern zu klären; sie ändern weder die geplante Policy noch die Aussagegrenzen. Neue Provider, Lizenzkäufe oder Adjustierungen erfordern einen eigenen Change.
