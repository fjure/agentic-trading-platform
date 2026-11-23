# Agentenaufgaben auf dem bestehenden Supervisor

Planung auf `0442f29`. Die Produktvision beschreibt eine künftige Rollenorganisation; sie belegt keine bereits implementierten Market-Intelligence- oder Portfolio-Planner-Agenten und erteilt keine Handelsrechte.

## Beobachtete Laufzeit und Verträge

`packages/agent_research/registry.py` enthält `AgentVersion`, `AgentLimits`, `ModelBinding`, das geschlossene Register `AGENTS` und `quant-research-agent-v1` auf Autonomiestufe 0; ein validierter Provideragent kann separat gebunden werden. Tools sind `RESEARCH_READ`, `QUANT_READ`, `IDEA_MANAGE`, `QUANT_PREPARE`, keine Broker-, Credential- oder freien Shelltools.

`packages/agent_research/runs.py` implementiert `RunStore`, `RunSnapshot`, `Claim`, `ClaimedInput`, RunKind `DIALOGUE|SCHEDULED|REEVALUATION|PROVIDER_PROBE`, dauerhafte Idempotenz, unveränderliche Agentbindung und lease-/fencegeschützte Arbeit. `services/runtime/agent_service.py` integriert die vorhandenen Dienste; `services/runtime/agent_status.py` enthält actor-scoped `AgentReadModels` und `_run`, einschließlich Budget, Usage, Provideroutcome und ausgewählten Gründen. `packages/domain/models.py:AgentRun` ist ein anderes bestehendes Domainobjekt; es wird nicht fälschlich zum Ersatz des Research-Supervisors erklärt.

Bestehende OAS-Familien: `/api/v1/research/agent/registry`, `/runs`, `/runs/{id}`, `/runs/{id}/events`, `/runs/{id}/cancel`, `/ideas`, `/dialogues`, `/schedule`, `/providers`, `/approvals`, `/approvals/{id}/decision`, `/reports`. Quant nutzt `/api/v1/research/experiments/{id}/validations`, `/experiments/{id}/batches` und `/experiment-batches/{id}`. `AgentQuantApprovalBindingV1` bindet den Operatorstart; dieser ist keine Paperfreigabe.

Normative Grundlagen sind APF-R01–R07 und APF-C01–C05 in `openspec/changes/agent-platform-foundation/specs/agent-research-runtime/spec.md` sowie `agent-research-control/spec.md`. Die bestehenden Supervisor-Safetykontrollen aus `openspec/specs/supervisor-control/spec.md` bleiben unabhängig.

## Tatsächliche Zustände und verständliche Projektion

Die exakten Zustände kommen aus `packages/agent_research/run_state.py`; kein UIlabel verändert eine Transition oder erzeugt ein neues Runtimeenum.

| Gespeicherter RunState | Vorgeschlagenes UIlabel / nächste erlaubte Orientierung |
| --- | --- |
| CREATED, QUEUED | Angelegt / Eingeplant; gespeicherten Auftrag und Grenzen zeigen |
| RUNNING | In Bearbeitung; tatsächliche letzte Aktivität und Frist zeigen |
| WAITING_FOR_INPUT | Rückfrage offen; bestehende Dialogaktion öffnen |
| WAITING_FOR_PROCESSING_DECISION | Sendefreigabe offen; exakte bestehende Vorschau/Entscheidung öffnen |
| WAITING_FOR_APPROVAL | Experimentfreigabe offen; konkretes Quantapproval öffnen |
| WAITING_FOR_BATCH | Experiment läuft oder wird abgewartet; gebundenen Batch zeigen |
| COMPLETED | Abgeschlossen; Ergebnis kann fehlen oder Enthaltung sein, kein automatisches Erfolgsetikett |
| BLOCKED | Blockiert; belegten Grund und erlaubten nächsten Schritt zeigen |
| FAILED | Fehlgeschlagen; gespeicherten sicheren Grund zeigen |
| CANCELLED | Abgebrochen; mögliche bereits verursachte Wirkungen/Kosten bleiben sichtbar |
| TIMED_OUT | Frist überschritten; keine automatische Neuauflage |
| BUDGET_EXHAUSTED | Budget ausgeschöpft; offene Reserven behalten |

Ein Run kann abgeschlossen und sein Ergebnis fachlich unbrauchbar sein. Fortschritt ist keine frei geschätzte Prozentzahl: Zähler und belegte Phasen werden gezeigt; ohne endliche bekannte Gesamtzahl ist percentage `null` mit `TOTAL_WORK_UNKNOWN`. Ein wartender Lauf hält keine Workerlease. Provider `OUTCOME_UNKNOWN` bleibt sichtbar und wird weder zu Erfolg noch zu kostenfrei umgedeutet.

## Additives Aufgabenmodell und Vertragsvorschlag

`GET /api/v1/experience/tasks` und `/api/v1/experience/tasks/{taskId}` liefern vorgeschlagene `InvestmentTaskPageV1`/`InvestmentTaskViewV1`: stabile taskId, kind, title, sourceType, sourceId, sourceVersion, runId nullable, rawState, displayState, resultAvailability, reasonCodes, createdAt/updatedAt/deadlineAt, lastActivityAt, agentId/version, policyVersion, boundedProgress, budgetSummary, approvalReferences, reportReferences, allowedActions und capabilityAvailability. Aufgaben sind serverseitige Projektionen vorhandener Quellen, kein zweiter Jobstore und keine zweite Queue.

V1 priorisiert den bestehenden Research-Run als Quelle. Produktrollen wie Portfolioanalyse und Marktbeobachtung erscheinen nur als noch nicht verfügbare Vorlagen mit explizitem Grund, bis sie eigenen belegten Registry-/Tool-/Policyumfang besitzen. Keine generische frei formulierte Agentenaufgabe wird ungeprüft dispatcht.

`allowedActions` ist eine zum Readzeitpunkt begrenzte Navigationshilfe zu bestehenden typisierten Operationen; sie enthält Aktionsart und autorisierten internen Ressourcenverweis, keine freien URLs oder Toolbefehle. UIaktionen brauchen weiterhin aktuelle Session, Origin/CSRF, expectedVersion, Idempotency-Key und die unveränderte autoritative Prüfung des Zieldienstes. Cancel nutzt den vorhandenen Run-Cancelpfad; eine Aufgabe neu öffnen oder eine Seite laden erzeugt keinen Retry. Ein späterer Start verwendet vorhandene Dialog-/Ideen-/Admissionpfade, kein universelles `execute`-API.

Listen sind actor-scoped, begrenzt und cursorpaginiert. Snapshot-/Quellversionen erlauben Reload und konsistente Details; die Projektion darf neue Ereignisse nicht als bereits bestätigte alte Version ausgeben. Titel, Begründungen und Modelltexte werden als untrusted Text dargestellt, Finanzmetriken kommen vom Backend. Secrets und rohe Prompt-/Providerdaten gehören nicht in Aufgabenmodelle.

## Abhängigkeit und Readiness

Der aktualisierte veröffentlichte Stand0442f29 führt ARP-10/10.1 abgeschlossen; Issue #105 wurde erneut CLOSED zurückgelesen. Die technische Offline-/CI-Readiness ist damit als Voraussetzung erfüllt, nicht die reale Providerfreigabe. `docs/evidence/arp-10-integrated-readiness.md` weist RealProviderVerified weiterhin Nein aus. Produktionsfunktionen bleiben an konkrete Zweck-/Provider-/Modell-/Credential-/Tarif- und Fachnachweise gebunden. Fehlen diese oder die implementierte Capability, sperrt die Projektion mit erklärbarem Grund. Eine registrierte Agentenversion allein beweist keine reale Betriebsbereitschaft. Der zunächst beobachtete offene ARP-10-Stand ist historisch.

## Geplante Nachweise

Exakte Stateprojektion für jeden bestehenden Zustand; keine neue Queue/Runtime; actor isolation; fehlender Bericht nach COMPLETED; unbekannte Kosten; Deadline ohne automatischen Retry; Cancel gegen späte Ergebnisse/alte Fence; stale expectedVersion; Doppelrequest; fehlende reale Betreiberfreigabe und trotz technisch abgeschlossenem ARP-10 keine unautorisierten Startaktionen. Budget, Freigabe und Brokerisolation bleiben unabhängig.
