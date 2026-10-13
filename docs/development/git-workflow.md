# Issue-Branches und Integration

Der Nutzerauftrag vom 2026-10-13 ersetzt das frühere Commit-/Push-Verbot für geprüfte Issue-Implementierungen auf fjure/agentic-trading-platform. Der Hauptagent führt Git-Mutationen aus. Ein weiterer ausdrücklicher Nutzerauftrag erlaubt bei fehlendem offiziellen GitHub-MCP die installierte offizielle GH CLI für dieselben begrenzten Issue-/Label-Operationen. Details: github-cli-sync.md. Keine Pull Requests oder Kommentare erstellen; Git und GH CLI erhalten dadurch keine Broker- oder Credentialrechte.

## Ablauf je Issue

1. Source of Truth, Task-ID und Issue-Zuordnung prüfen. Branch `codex/ptv-NN-kurztitel` von dem verifizierten main-Stand anlegen. Unabhängige lokale Subagentenpakete besitzen getrennte Dateien; ihre Änderungen werden nur dem passenden Issue-Commit zugeordnet.
2. Umsetzung samt relevanten Tests, Vertragsprüfungen und Safetyreview abschließen. Der Commit enthält nur zugehörige geprüfte Dateien und erforderliche Evidenz. Keine teilweise implementierten Nachbaraufgaben, temporären Generatorausgaben, Secrets oder privaten Laufzeitdateien aufnehmen. Staging-Diff vor Commit prüfen.
3. OpenSpec-Abschluss und Issue-Sync samt Read-back im selben Durchlauf verifizieren. Bevorzugt offiziellen MCP, alternativ ausdrücklich freigegebene GH CLI verwenden. Bei fehlender Authentifizierung, fehlendem freigegebenem Transport oder Drift bleibt der Task offen; historische Read-backs werden nicht als neue Prüfung ausgegeben.
4. Issue-Commit erstellen und den Issue-Branch ohne Force-Push nach origin pushen. Remote-Branch-SHA lesend mit dem lokalen geprüften SHA vergleichen.
5. Remote-main erneut lesen/fetchen. Abweichungen integrieren und betroffene Prüfungen wiederholen. Auf main den Issue-Branch mit einem nachvollziehbaren Merge integrieren (`git merge --no-ff codex/ptv-NN-kurztitel`). main regulär pushen. Branchschutz wird eingehalten; verlangt er einen Pull Request, ist die direkte Integration blockiert und wird gemeldet. Dieser Auftrag autorisiert keine eigenmächtige Pull-Request-Erstellung.
6. Remote-main-SHA und Erreichbarkeit des Issue-Commits erneut prüfen. Erst dann ist die Git-Integration bestätigt. Gepushte Issue-Branches bleiben erhalten. Keine Branchlöschung, kein Reset fremder Arbeit, kein Force-Push.

## Erstinitialisierung

Am Beginn dieses Auftrags besitzt das lokale Repository noch keinen Commit, und der Remote hat keine Branches. Deshalb ist zuerst eine geprüfte Repository-/Planungsbasis auf `codex/repository-bootstrap` erforderlich. Nach deren Push wird main auf diesen ersten Basiscommit initialisiert und regulär gepusht. Dies ist eine Initialisierung, kein behaupteter Issue-Merge. Bereits lokal fertig geprüfte Issue-Pakete werden danach getrennt auf ihren jeweiligen Issue-Branches committed, gepusht und nach main integriert. Offene Pakete bleiben außerhalb dieser Commits.

## Zugriffe und Blocker

Git nutzt den vorhandenen privaten Credential-Manager; Agenten lesen keine Tokens, Credentialdateien oder Prozessumgebungen. Der bisher für MCP bestätigte Fine-grained PAT besitzt Contents read-only und genügt selbst nicht für Git-Pushes. Falls Git-Schreibauthentifizierung fehlt, muss der Nutzer sie privat für genau das Zielrepository einrichten; kein Token im Chat. Ein Commit mit CI-Workflowdateien benötigt zusätzlich die vom GitHub-Authentifizierungsweg vorgeschriebene Workflowberechtigung. Globale MCP-/Git-Einstellungen werden nicht eigenmächtig erweitert.

Der offizielle GitHub-MCP ist im Sitzungskatalog nicht mehr vorhanden (2026-10-13). Die anschließend ausdrücklich freigegebene GH CLI2.100.0 ist als fjure authentifiziert und hat das Zielrepository/main lesend bestätigt. Sie hebt den reinen Transportblocker auf; Scope und Read-back-Pflicht bleiben erhalten.

## Ausgeführte Integration

Bootstrapbasis und die sechs bereits synchronisierten Issue-Pakete PTV-01/02/03/04/06/08 wurden mit getrennten Branches regulär gepusht und nach main gemergt. Remote-HEAD ist main. Alle Remote-Issue-SHAs entsprechen den geprüften lokalen Commits; main enthält sie. Commit-/Merge-Zuordnung: git-issue-integrations.json. Bestehende private Git-Authentifizierung genügte einschließlich CI-Workflow-Push; keine Tokenwerte oder zusätzlichen Credentialrechte wurden angefordert. Remote-CI-Ergebnisse wurden in diesem Durchlauf nicht geprüft.

Vor dem ersten Basiscommit lehnte die automatische Freigabeprüfung den zu breiten Befehl `git add --renormalize .` ab. Stattdessen wurden die explizit ausgewählten Basisdateien geprüft; Anwendungspakete blieben ungestaged. Nur beim späteren PTV-03-Paket wurde der konkrete bereits ausgewählte Generatorpfad neu gestaged, damit Git dessen 65 Dateien bytegenau erhält. Alle Folgeprüfungen bestanden; keine Umgehung von Approval-, Branchschutz- oder Secretgrenzen.
