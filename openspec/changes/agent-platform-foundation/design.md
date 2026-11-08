# Design

## Context

Die [Discovery](discovery.md) trennt den geprüften Basisstand von neuen Schnittstellen. Der vorhandene Quant-Pfad bindet Strategien, Datenrevisionen, Forschungssplits und Admission bereits unabhängig vom Agenten.

## Goals / Non-Goals

Ein deterministischer Supervisor koordiniert genau eine ausführbare Rolle `quant-research-agent-v1`. Market Intelligence v1 ist eine deterministische Beobachtungsprojektion, Idea Engine eine Workflowfähigkeit. Portfolio Planner und externe Quellen bleiben spätere Changes. Risk und Execution bleiben separat. Research erhält keine Finanz- oder Handelsbefugnisse.

## Decisions

1. **Ein Research-Datenmodell.** Versionierte Ideen, Dialoge, Läufe, Checkpoints, Approvals, Nutzung und Reportreferenzen liegen in einem getrennten Schema derselben PostgreSQL-Datenbank lab_paper_runtime. Bounded Quant-Tools greifen auf bestehende fachliche Ports zu. Quant-Ergebnisse bleiben im Lab und werden nur referenziert. Diese Trennung erhält eine atomare Starttransaktion mit der vorhandenen Admission.
2. **Geschlossene Orchestrierung.** Der Supervisor steuert eine feste Schrittabfolge und registrierte Researchtools. Das LLM liefert strukturierte Vorschläge innerhalb begrenzter Interpretationsschritte. Die bestehende Handelsqueue filtert den Claim nicht nach Jobtyp und bleibt vom neuen Researchjobtyp getrennt. Bestehende Fencing- und Lease-Semantik wird übernommen. Siehe [ADR 01](adr/0001-orchestration.md) und [Runtime und Budget](runtime-budget.md).
3. **Separates Gateway.** Researchruntime ohne Providerkeys oder Internetzugriff. Gateway mit eigenem Credentialmount und eng begrenztem Provider-Egress, ohne Zugriff auf PostgreSQL oder Finanznetze. Ein privater Supervisor-RPC konsumiert unmittelbar vor Versand ein One-shot-Permit gegen aktuelle Fence-, Pause-, Rechte- und Budgetbindungen. Dieser Commit ist der Linearisierungspunkt für Dispatch gegenüber Pause, Abbruch und Widerruf. Bereits autorisierte Versuche gelten als in-flight. Die vorhandene Jev-/Alpaca-Ausnahme bleibt eng. APF verlangt eine eigene Freigabe für Eingabe, Speicherung, Egress und Datenausleitung. Siehe [ADR 02](adr/0002-provider-boundary.md) und [Threat Model](threat-model.md).
4. **Ein Startweg pro Berechtigung.** Control API startet nach frischer Betreiberfreigabe über den gebundenen APF-Admission-Pfad im Quant Lab. Der bestehende Storage-Wrapper eröffnet eine eigene Transaktion. APF ruft dieselbe DB-Admission über einen transaktionsgebundenen Port auf. Approval-Verbrauch, agentenspezifisches Kontingent, bestehender Quant-Start und Batchbezug committen gemeinsam. Kein Agententool kann den manuellen Startweg aufrufen. Der bestehende direkte manuelle Quant-Workflow behält seine eigene Betreiberautorisierung. Agentengebundene Entwürfe werden für diesen Startweg serverseitig ausgeschlossen, sodass kein UI-Wechsel die APF-Freigabe umgeht.
5. **Getrennte Anzeige und Fachbindung.** Guided und Advanced sind Projektionen derselben Version. Der Bericht hält typisierte Fakten und belegte Metriken getrennt von Interpretation. Quellen-, Ergebnis- und Testfensterbezüge bleiben unveränderlich, aktuelle Widerrufe erscheinen in einer separaten Projektion. Siehe [Quant-Integrität](quant-integrity.md).
6. **Browserunabhängiger Researchlauf.** Supervisor und PostgreSQL stellen Tageszeitplan, Checkpoint und Deduplikation bereit. Der LLM-Aufruf folgt dem Evidenztrigger. Proaktive Vorschläge können kein Experiment starten. Siehe [Pipeline](proactive-design.md).

## Risks / Trade-offs

- Unklarer Providerzustand → maximale Reservierung konservativ halten, kein automatischer Wiederholungsversand und kein stiller Providerwechsel.
- Langsame oder fehlerhafte externe Antworten → zeitlich begrenzte Gateway-Requests, feste Parallelität, getrennte Research- und Trading-Worker, sichtbare Blockierung.
- Freitext und externe Daten → Herkunft und Rechte vor Versand prüfen, Kontext minimieren, Ausgabe deterministisch validieren und im Browser als Text darstellen.
- Quant-Proxy verzerrt Nutzeridee → Original und Interpretation separat bewahren, Approximation explizit annehmen, fehlende Strategien sichtbar machen.
- Neue Evidenz nach Abschluss → alte Bindungen reproduzierbar erhalten und aktuelle Einschränkungen separat kennzeichnen.

## Migration Plan

Additive OpenAPI- und Datenbankmigrationen zuerst. Neue Tabellen, Rollen und Funktionen ohne Änderung bestehender Schlüssel und APIs einführen. Gateway und Scheduler sind deaktiviert und die Runtime läuft in Offline-Tests mit lokalen HTTP-Doubles. Nach getrennter Freigabe kann ein Betreiber eine zulässige Providerkonfiguration und Zeitplan aktivieren. Rollback per Deaktivierung, Sperre neuer Claims und Egress, Widerruf offener Approvals und Erhalt persistierter Evidenz. Laufende Quant Batches folgen ihrer bestehenden Recovery. Abhängige persistierte Tabellen werden bei einem Rollback weder automatisch geleert noch gelöscht.
