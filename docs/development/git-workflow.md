# Issue-Branches und Integration

Der Nutzerauftrag vom 2026-10-13 ersetzt das frühere Commit-/Push-Verbot für geprüfte Issue-Implementierungen auf fjure/agentic-trading-platform. Der Hauptagent führt Git-Mutationen aus. Die Issue-/Label-Synchronisierung läuft weiterhin ausschließlich über den offiziellen GitHub-MCP. Es werden keine Pull Requests, Labels oder Issues über Git-CLI/REST erstellt; Git erhält durch diesen Auftrag keine Broker- oder Credentialrechte.

## Ablauf je Issue

1. Source of Truth, Task-ID und Issue-Zuordnung prüfen. Branch `codex/ptv-NN-kurztitel` von dem verifizierten main-Stand anlegen. Unabhängige lokale Subagentenpakete besitzen getrennte Dateien; ihre Änderungen werden nur dem passenden Issue-Commit zugeordnet.
2. Umsetzung samt relevanten Tests, Vertragsprüfungen und Safetyreview abschließen. Der Commit enthält nur zugehörige geprüfte Dateien und erforderliche Evidenz. Keine teilweise implementierten Nachbaraufgaben, temporären Generatorausgaben, Secrets oder privaten Laufzeitdateien aufnehmen. Staging-Diff vor Commit prüfen.
3. OpenSpec-Abschluss und offiziellen Issue-Sync samt Read-back im selben Durchlauf verifizieren. Bei fehlendem MCP oder Drift bleibt der Task offen; historische Read-backs werden nicht als neue Prüfung ausgegeben.
4. Issue-Commit erstellen und den Issue-Branch ohne Force-Push nach origin pushen. Remote-Branch-SHA lesend mit dem lokalen geprüften SHA vergleichen.
5. Remote-main erneut lesen/fetchen. Abweichungen integrieren und betroffene Prüfungen wiederholen. Auf main den Issue-Branch mit einem nachvollziehbaren Merge integrieren (`git merge --no-ff codex/ptv-NN-kurztitel`). main regulär pushen. Branchschutz wird eingehalten; verlangt er einen Pull Request, ist die direkte Integration blockiert und wird gemeldet. Dieser Auftrag autorisiert keine eigenmächtige Pull-Request-Erstellung.
6. Remote-main-SHA und Erreichbarkeit des Issue-Commits erneut prüfen. Erst dann ist die Git-Integration bestätigt. Gepushte Issue-Branches bleiben erhalten. Keine Branchlöschung, kein Reset fremder Arbeit, kein Force-Push.

## Erstinitialisierung

Am Beginn dieses Auftrags besitzt das lokale Repository noch keinen Commit, und der Remote hat keine Branches. Deshalb ist zuerst eine geprüfte Repository-/Planungsbasis auf `codex/repository-bootstrap` erforderlich. Nach deren Push wird main auf diesen ersten Basiscommit initialisiert und regulär gepusht. Dies ist eine Initialisierung, kein behaupteter Issue-Merge. Bereits lokal fertig geprüfte Issue-Pakete werden danach getrennt auf ihren jeweiligen Issue-Branches committed, gepusht und nach main integriert. Offene Pakete bleiben außerhalb dieser Commits.

## Zugriffe und Blocker

Git nutzt den vorhandenen privaten Credential-Manager; Agenten lesen keine Tokens, Credentialdateien oder Prozessumgebungen. Der bisher für MCP bestätigte Fine-grained PAT besitzt Contents read-only und genügt selbst nicht für Git-Pushes. Falls Git-Schreibauthentifizierung fehlt, muss der Nutzer sie privat für genau das Zielrepository einrichten; kein Token im Chat. Ein Commit mit CI-Workflowdateien benötigt zusätzlich die vom GitHub-Authentifizierungsweg vorgeschriebene Workflowberechtigung. Globale MCP-/Git-Einstellungen werden nicht eigenmächtig erweitert.

Der offizielle GitHub-MCP ist im aktuellen Sitzungskatalog nicht mehr vorhanden (2026-10-13). Letzter erfolgreicher vollständiger Read-back: sechs abgeschlossene Tasks, 23 Issues, kein Drift. Weitere Taskabschlüsse bleiben bis zur Wiederherstellung des MCP ausgesetzt. Lokale Implementierung, Tests, Reviews und die Vorbereitung des Git-Ablaufs bleiben möglich.
