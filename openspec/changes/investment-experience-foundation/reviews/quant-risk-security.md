# Unabhängiger Planungsreview: Quant/Risk und Security/Privacy

Stand: 23.11.2026, IEF-Planung auf Baseline `0c26def`. Zwei getrennte fachliche Lesedurchgänge; keine Anwendung geändert, kein Git verändert, keine Laufzeitzertifizierung. Gegenstand waren `design.md`, sämtliche sechs Capability-Deltas und `work-packages/IEF-01.md` bis `IEF-09.md`; zusätzlich wurden die drei Modellentwürfe und vorhandene Runtime-/Migrationsanker gelesen. Die selbst verfassten Sicherheits-/Testdokumente waren nicht alleiniger Reviewgegenstand.

## Durchgang 1: Quant und Risk

**Kein bestätigter P1. Der anfängliche P2 ist nach Nachprüfung geschlossen:**

- **[P2] Optionale unbekannte Gewichte dürfen den Paperweg nicht pauschal sperren.** `work-packages/IEF-07.md:17` verlangt „unbekannte Werte blockieren“, ebenso `tasks.md:31`. Dagegen unterscheidet `specs/guided-paper-experience/spec.md:10` ausdrücklich zwischen fehlenden obligatorischen Gategrundlagen und optional unbekannten Gewichten. Da das Paperreadmodel V1 keine belastbaren Positionsmarktwerte liefert, kann die pauschale Paketformulierung den ansonsten zulässigen J5-Weg dauerhaft blockieren. Korrektur: nur unbekannte **vorgeschriebene Gategrundlagen** sperren; optionale Gewichte und reine Darstellungsauswirkungen bleiben begründet unverfügbar. Positivtest: alle bestehenden Human-/Risk-/Executionbelege gültig, Gewicht unbekannt → Preview und bestätigter lokaler Harnessweg weiterhin möglich. Tasktext bei Anpassung im selben Durchlauf mit Issue synchronisieren.

**Nachprüfung:** `work-packages/IEF-07.md:17` und `tasks.md:31` begrenzen die Sperre jetzt ausdrücklich auf fehlende obligatorische Gategrundlagen; optionale Gewichte bleiben unverfügbar. `work-packages/IEF-07.md:28` fordert den Positivfall mit ansonsten gültiger Vorschau. Damit ist der Dokumentwiderspruch behoben; der konkrete Laufzeittest bleibt Aufgabe des Apply. Issue-Sync wird vom Hauptagenten separat nachgewiesen.

Geprüfte Entscheidungen ohne weiteren bestätigten kritischen Befund:

| Bereich | Konkrete Evidenz | Ergebnis des Planungsreviews |
| --- | --- | --- |
| BUY/SELL-Bruttogrenze | `design.md` D5, `guided-order-design.md` Schritte 3–5, IEF-G01/T-G01 | BUY Obergrenze und SELL Mindestbruttoerlös sind korrekt getrennt; keine Netto-/Fillgarantie. |
| Human-/Risk-/Executionkette | IEF-G02/G03, `guided-order-design.md`, IEF-07 | Preview hat keine Reservation oder Brokerwirkung; einmaliger Commit geht in vorhandene Humananforderung. Finale frische Gateprüfung und Nachkaufsperre bleiben bestehen. |
| Ungewisse Wirkung | IEF-G03/T-G04/T-G05, IEF-A04, IEF-07/09 | UNKNOWN hält Reserve; Status-/Reconciliationweg statt Blindresubmit; Fill/Cancel und Pause/Kill berücksichtigt. |
| Finanzdaten und Quant | IEF-P01–P03, IEF-S01–S03, IEF-03/05 | Konsistente Quellen, nullable Kennzahlen, PIT-/Kosten-/Holdoutgrenzen und keine automatische Promotion; kein erfundener Registryersatz. |
| Lokaler Analyseauftrag | `design.md:43`, IEF-A01/T-A01, IEF-04/06 | Bounded Analyse bleibt eigene sourceId, `sourceType=PORTFOLIO_ANALYSIS`, `runId=null`; keine zweite Queue, kein künstlicher Agentenrun. |

## Durchgang 2: Security und Privacy

**Kein bestätigter P1/P2 im geprüften Planungsumfang.**

| Schwerpunkt | Konkrete Evidenz | Ergebnis und notwendige Apply-Abnahme |
| --- | --- | --- |
| Neuer Portfoliozweck | `design.md:47` und `design.md:84`, IEF-01/04, IEF-P04 | `PORTFOLIO_EXPLANATION_V1` ist eigene Projektion/Policy/Rolle/Schema, kein Missbrauch des strengeren Hypothesenkanals. Kontoreferenzen, rohe Positionstabellen und Präferenzprofile ausgeschlossen. Tatsächliche allowlist und Klassifikation später im Contract-/Policytest nachweisen. |
| Preview/Decision/Status/Revoke | `design.md:84`, IEF-A03/A04, IEF-01/04 | Zweckdiskriminierte bestehende Vertragsfamilie; konkrete Analyse-/Versions-/Provider-/Budget-/Fristbindung; inhaltsfreier Status; Widerruf sperrt Dispatch beziehungsweise späte Annahme an bestehender Commitgrenze. Keine universelle Zustimmung. |
| RunKind und Persistenz | IEF-04:17, `design.md:84`; Bestand `packages/agent_research/runs.py`, Migrationen 0037/0041 und 0039 | Enum-/SQL-Constraintänderung ist ausdrücklich eingeplant. Hypothesenschema bleibt erhalten. Migration mit alten und neuen Runs sowie Restore-/Downgradegrenzen im Apply prüfen. |
| Actor und getrennte Entscheidungen | IEF-O02, IEF-A03, `design.md` D1/D2, IEF-02/06 | Serveractor, Session/Origin/CSRF und Versions-/Idempotenzschutz; sechs unabhängige Autoritäten. Keine Mehrbenutzerfähigkeit behauptet. |
| Datenminimierung und Output | IEF-P04/T-P08, IEF-C03, `design.md` D2/D3 | Keine Freitextbiographie/Finanzautorität aus Präferenzen; kein Überschreiben deterministischer Zahlen durch Modell; passive Darstellung und quellengebundene Tatsachen. |
| Readiness und externe Wirkung | `design.md` D7, IEF-C04, IEF-04/06/09 | ARP-10/#105 und konkrete Betreiberfreigaben bleiben Sperren; Offline-Doubles gelten nicht als reale Provider-/Betriebsfreigabe. |

Bei der Umsetzung muss die Projektionsbindung das vorhandene private MAC-/Nonceverfahren erhalten: Der Ausdruck „Projektionshash“ in D3 ist keine Erlaubnis für öffentliche ungeschützte Hashes kurzer sensibler Inhalte. Alte Hypothesen-/Grantprüfungen dürfen nicht durch Verallgemeinerung abgeschwächt werden. Diese Punkte sind Implementierungsprüfungen, keine im Plan bestätigten neuen Schwachstellen.

## Evidenzgrenze

Angewendet wurde der vorhandene `trading-safety-review`-Ablauf für Spezifikationsgrenzen. LIVE unerreichbar, Agenten ohne Broker-Submit, fail-closed/unsicherer Neustart, Decimal/UTC, unabhängige Pause, Credentialtrennung, unveränderliche Proofs, atomare Reservierung sowie Unknown-/Reconciliation-/Fillrennen sind in der Planung erfasst. Sämtliche IEF-Laufzeitnachweise bleiben **unverifiziert**, bis die entsprechenden Implementierungstests tatsächlich ausgeführt wurden. Kein Zugriff auf echte Credentials oder Handelsendpunkte.

## Aktualisierung auf veröffentlichte ARP-10-Baseline

Der Hauptagent hat den neuen veröffentlichten Stand `0442f29`, ARP-10/#105 geschlossen sowie erfolgreiche [Research integration 45190975614](https://github.com/fjure/agentic-trading-platform/actions/runs/45190975614) und [Contracts 45190976026](https://github.com/fjure/agentic-trading-platform/actions/runs/45190976026) verifiziert. Der frühere offene ARP-10-/rote-Contracts-Befund ist damit historisch und kein aktueller technischer Blocker. `docs/evidence/arp-10-integrated-readiness.md` unterscheidet ausdrücklich Implementierungs-/Offline-/CI-/Securitynachweise von realer Providerfreigabe: **RealProviderVerified = Nein** für alle aufgeführten Bereiche. Diese Unterscheidung bleibt für IEF verbindlich; ein geschlossenes Issue aktiviert keine realen Modellprofile.

Zusätzlich gelesen wurden `services/runtime/research_evaluation.py`, `services/llm_gateway/{__main__,provider_installation}.py` und die Installations-/Evaluierungsverdrahtung in `services/runtime/agent_service.py`. Die veröffentlichte Baseline besitzt nun den privaten Evaluierungseinstieg auf der normalen Grant-/Budget-/Fence-/Gatewaykette. Seine Factory bindet Settings-/Installations-/Prompt-/Schema-/Profil-/Modellwerte an die Operatorfreigabe, weist Probe-/Discoveryinstallationen und abgelaufene Freigaben zurück und hält Setupwiderruf über den vorhandenen signierten Kanal erreichbar. Fallkontext liegt im privaten flüchtigen Projektionspfad, nicht als neuer Companionfall; unbestätigte Geldkosten bleiben konservativ reserviert. Das ist eine begrenzte Code-/Planungsprüfung, kein erneuter Laufzeitnachweis.

Die Gatewayinstallation ist ein expliziter privater Profilstand; fehlende Installation startet weiterhin den Offlinepfad. Die neuen Evaluierungsports sind `app.state`-interne Kompositionsports, keine öffentliche universelle Execute-/Consent-API. Diese Erweiterung stellt **keine** vorhandene Implementierung von `PORTFOLIO_EXPLANATION_V1` dar. Insbesondere dürfen Evaluierungsfallencoding `EVALUATION_CASE_V1`, privater Evaluierungsactor und dessen Operatorfreigabe weder Portfoliozweck, Datenklassen, Browserpreview noch getrennte Nutzerentscheidungen ersetzen. Der geplante neue Purpose/RunKind, dessen SQL-Constraints, Projektor, Outputvalidator sowie Preview/Decision/Status/Revoke bleiben eigene IEF-01/04-Arbeit; alte Hypothesengrenzen bleiben erhalten.

**Abschluss nach Nachprüfung:** Keine offenen bestätigten P1/P2 im Quant-/Risk- und Security-/Privacy-Planungsumfang. Keine Runtimeabnahme, reale Providerfreigabe oder Rechtsfreigabe erteilt.
