# Investment Experience Foundation: Apply-Stand

## Auftrag und geprüfte Basis

Der eingefügte Nutzerauftrag erteilt OpenSpec Apply für alle neun Pakete. Der anschließende Nutzerhinweis bestätigt, dass echte Testpersonen derzeit nicht verfügbar sind. Der Nutzer hat anschließend die Bündelung aller drei menschlichen Prüfungen in IEF-09 beauftragt. Der technische Dependencygraph bleibt unverändert.

Verifizierter Remote-main und lokaler Issue-Branch-HEAD: `82139cd2f4a87d88b92612b5fae55d8caa367d4a`. Isolierter Arbeitsbaum: `.worktrees/ief-01`, Branch `codex/ptv-107-ief-foundation`. Der ursprüngliche main-Checkout und seine fremden Änderungen bleiben erhalten. Die aktuelle IEF-01-Implementierung liegt im Arbeitsbaum; dieser Ausgangs-HEAD allein bezeichnet ihre Änderungen nicht. Die abschließende Quellenmanifestdatei und Paketnachweise grenzen den geprüften Stand ab.

## Paketstatus und konkrete Blocker

| Paket / Issue | Tatsächlicher Stand | Abschlussblocker |
| --- | --- | --- |
| IEF-01 / #107 | Additive Contracts, generierter Client und Router/Prototyp lokal umgesetzt. Contract-Suite und ausgewählte Browserregression bestanden. [Paketnachweis](ief-01.md) | Technisch abgeschlossen und nach main integriert. Die formative Prüfung gehört zu IEF-09. |
| IEF-02 / #108 | Technisch abgeschlossen und nach main integriert. [Paketnachweis](ief-02.md) | Die menschliche Prüfung IEF-T-O05 gehört zu IEF-09. |
| IEF-03 / #109 | Offen. Neue Finanz-Readmodels stehen aus. | IEF-01 und ARP-10 sind technisch erfüllt. |
| IEF-04 / #110 | Nicht begonnen; keine neue lokale Analyse oder externe Erklärung verfügbar | IEF-03; damit mittelbar IEF-01. ARP-10 ist technisch erfüllt, ersetzt aber keine reale Zweck-/Provider-/Kostenfreigabe |
| IEF-05 / #111 | Offen. Die Registerprojektion steht aus. | IEF-01 ist technisch erfüllt. |
| IEF-06 / #112 | Nicht begonnen; keine neue persistente Taskprojektion | IEF-01 und IEF-04; bestehender Supervisor bleibt zuständig |
| IEF-07 / #113 | Nicht begonnen; neue Preview-/Guided-Laufzeit nicht implementiert | IEF-01, IEF-03 und IEF-05; bestehender Human-/Risk-/Executionpfad unverändert |
| IEF-08 / #114 | Nicht begonnen; frühe Shell und synthetischer Prototyp sind keine integrierte Fachoberfläche | IEF-02–07 und deren geprüfte echte Quellen |
| IEF-09 / #115 | Nicht ausgeführt; keine integrierte J1–J6-Abnahme behauptet | IEF-01–08; danach echte Angular/API/tempPG/Runtime-Gates und alle drei menschlichen Prüfungen IEF-T-C06, IEF-T-O05 und IEF-T-C08 |

IEF-01–08 werden nach ihren technischen Kriterien abgeschlossen. IEF-09 bündelt alle drei menschlichen Prüfungen gemäß ux-validation.md. Sie bleiben zusätzliche Voraussetzungen für die Produktfreigabe.

## Statusbegriffe für den gesamten Change

| Kennzeichen | Ergebnis |
| --- | --- |
| `TECHNICAL_IMPLEMENTATION_COMPLETE` | **Nein**: die Featurepakete IEF-03–09 stehen noch aus |
| `AUTOMATED_ACCEPTANCE_PASSED` | **Nein**: lokale Teilprüfungen ersetzen keine vollständige integrierte Abnahme oder Remote-CI am Implementierungs-SHA |
| `HUMAN_UX_VALIDATION_PENDING` | **Ja**, vom Nutzer bestätigt; keine simulierten menschlichen Ergebnisse |
| `PRODUCT_READINESS_APPROVED` | **Nein** |
| `PRODUCT_READINESS_BLOCKED` | **Ja**, durch offene technische und menschliche Nachweise |

## Ausführbare Aktionen und Grenzen

Der frühe Prototyp verbindet die zehn Screens mit klar gekennzeichneten synthetischen Interaktionen. Die bestehende Anwendung bleibt unter „Daten und Betrieb“ erreichbar. Die neuen API-Operationen sind Vertragsvorläufe und keine ausgeführten Fähigkeiten. Keine Auftragserstellung oder Navigation aktiviert einen Provider, erhöht Budgets oder erteilt Paper-/LIVE-Rechte. Keine echte Modellanfrage und keine Brokerorder gehören zu diesem Apply-Prüfstand.

Die tatsächlichen lokalen Tests, Reviews und UI-Aufnahmen werden im [IEF-01-Nachweis](ief-01.md), [Contractnachweis](ief-01-contracts.md), [Baseline](ief-01-baseline.md), [Review](ief-01-review.md) und [Safetyreview](ief-01-safety.md) dokumentiert. Nicht ausgeführte, abgebrochene oder übersprungene Prüfungen gelten nicht als bestanden. Die späteren integrierten Nachweise gegen temporäres PostgreSQL und echte Fachkomponenten bleiben ausstehend.

## GitHub und Veröffentlichung

Read-back am 23.11.2026: neun offene Issues, neun exakte Managedblockhashes, Marker/Titel/Labels und 26 native Kanten in beiden Richtungen geprüft; ARP-10/#105 geschlossen. Nach tatsächlichem MCP-Updatefehler 403 wurde der autorisierte GH-CLI-Fallback verwendet: neun Managedblocks aktualisiert, keine neuen Issues, keine Schließungen, keine Änderungen an manuellen Außenblöcken, Labels oder Kanten. Der zweite vollständige Lauf ergab **0 Updates, 9 unverändert**, erneut 26 Kanten beidseitig bestätigt. Aktuelle Hashes/UTC-Zeiten und beide Läufe stehen in `openspec/changes/investment-experience-foundation/github-issues.json`. IEF-01 und IEF-02 sind technisch abgeschlossen. Sieben Taskcheckboxen bleiben offen.

Nach der Verschiebung der menschlichen Prüfungen wurden am 24.11.2026 erneut alle neun verwalteten Issue-Abschnitte synchronisiert. Neun offene Issues und alle 26 nativen Kanten wurden in beiden Richtungen zurückgelesen. Details stehen im aktuellen GitHub-Mapping.

Kein Push, Merge oder Remote-CI-Erfolg für einen noch nicht vollständig abgenommenen IEF-Issueabschluss wird behauptet. Remote-main wurde erneut als `82139cd2f4a87d88b92612b5fae55d8caa367d4a` gelesen. Seine [Contracts45199328469](https://github.com/fjure/agentic-trading-platform/actions/runs/45199328469) und [Research integration45199328281](https://github.com/fjure/agentic-trading-platform/actions/runs/45199328281) sind tatsächlich `completed/success`; beide sind **Baseline, keine IEF-Implementierungsabnahme**. Branchschutz und globale Konfiguration werden nicht verändert.

## Nächste Nutzerabnahme und Produktphase

Die [Start- und Abnahmeanleitung](../runbooks/ief-prototype.md) und das [menschliche Testprotokoll](ief-human-ux-protocol.md) beschreiben Vorbereitung, Stichprobe, Aufgaben, Zeitlimits, Sicherheitskriterien, Redaktion, Korrektur und Nachtest. IEF-02/03/05 können entlang des bestehenden Graphen beginnen. Die drei menschlichen Runden schließen den Change gemeinsam mit der integrierten technischen Abnahme in IEF-09 ab.

IEF-01 ist mit Commit `3eb6e97eb03c50bfb5c46c1c7ba774e4e44d7ba0` geprüft und als `41db26fa10cc69800753bec4a8998612166d7534` nach main integriert. Der Remote-Read-back bestätigt den Merge und der Merge-Baum stimmt bytegleich mit dem Issue-Commit überein. Beide Branch-Pflichtworkflows sind erfolgreich: [Contracts](https://github.com/fjure/agentic-trading-platform/actions/runs/45362409582) und [Research integration](https://github.com/fjure/agentic-trading-platform/actions/runs/45362409461).
