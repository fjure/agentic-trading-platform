# Alpaca-Paper-Workflow: Abnahmeübersicht

OpenSpec-Change: `alpaca-paper-workflow`. Fachliche Source of Truth:
[Spezifikation](../../openspec/changes/alpaca-paper-workflow/specs/alpaca-paper-workflow/spec.md),
[Design](../../openspec/changes/alpaca-paper-workflow/design.md) und
[Tasks](../../openspec/changes/alpaca-paper-workflow/tasks.md).

| Paket | Konkreter Nachweis |
|---|---|
| APW-01 Verträge | [Vertragsbasis](apw-01.md) |
| APW-02 Isolation | [Deployment](apw-02.md) |
| APW-03 private Einrichtung | [Prüfung/Rotation](apw-03.md) |
| APW-04 Anfangsbasis | [Kostenbasis/Tagesrisiko](apw-04.md) |
| APW-05 explizite Freigabe | [Lifecycle](apw-05.md) |
| APW-06 Modellplanung | [LIMIT-Planung](apw-06.md) und [Replay](apw-06-replay.md) |
| APW-07 menschlicher LIMIT | [Gemeinsamer Risk-/Executionpfad](apw-07.md) |
| APW-08 Cancel/Recovery | [Dauerhafte Aufklärung](apw-08.md) |
| APW-09 Oberfläche | [Material-UI und Leseverträge](apw-09.md) |
| APW-10 Gesamtkette | [Aktuelle Prüfevidenz](apw-10.md), [Prozessuhr](apw-10-clock.md) und [unabhängige Verifikation](apw-10-verification.md) |

Stand25.10.2026: Alle10Tasks lokal abgenommen, Issues geschlossen und regulär
integriert. APW-10-Implementierungsbranch5ee178b und main1c686cd besitzen denselben
geprüften Dateibaum; CI40177274551 erfolgreich. Tatsächliche HUMAN-/MODEL-/UTC-
und OS-Crashketten sowie bestehende SQL-/Recoveryregression sind belegt. Überlappende Testzahlen
werden nicht addiert. Die [Integrationszuordnung](../development/git-paper-workflow-integrations.json)
und der [Issue-Read-back](../development/github-paper-workflow-issue-sync.json)
enthalten die jeweils tatsächlich bestätigten Stände.

Keine echten Providerorders durch Agenten. Tatsächliche Alpaca-Fähigkeit bleibt
unverifiziert bis zur gesonderten privaten menschlichen T020-Abnahme; LIVE bleibt
technisch unerreichbar. Wiederholbarer lokaler Ablauf:
[Paper-Runbook](../runbooks/paper-workflow.md).
