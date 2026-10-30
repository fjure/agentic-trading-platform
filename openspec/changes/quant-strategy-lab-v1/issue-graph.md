# GitHub-Issues und Abhängigkeiten

Alle Issues sind offen; Implementierung ausstehend. Transport: offizieller GitHub-MCP lesend erfolgreich, Issuecreate403; ausdrücklich autorisierter GH-CLI-Fallback für Issues/Labels/native Dependencies. Keine Kommentare, Pull Requests, Branchlöschungen oder fremden Repositories. Change-Label und openspec, kein Milestone.

| Task | Issue / Ziel | Depends on |
|---|---|---|
| QSL-01 | [#77 Additive Researchverträge](https://github.com/fjure/agentic-trading-platform/issues/77) | — |
| QSL-02 | [#78 Register und Experimentprimitive](https://github.com/fjure/agentic-trading-platform/issues/78) | #77 |
| QSL-03 | [#79 Additive Persistenz und globale Admission](https://github.com/fjure/agentic-trading-platform/issues/79) | #77 |
| QSL-04 | [#80 Rekursive EMA- und RSI-Signale](https://github.com/fjure/agentic-trading-platform/issues/80) | #78 |
| QSL-05 | [#81 Chronologisches Auswahlprotokoll](https://github.com/fjure/agentic-trading-platform/issues/81) | #78 |
| QSL-06 | [#82 Kennzahlen und Vergleichsmodell](https://github.com/fjure/agentic-trading-platform/issues/82) | #78 |
| QSL-07 | [#83 Worker und authentifizierte API](https://github.com/fjure/agentic-trading-platform/issues/83) | #79,#80,#81,#82 |
| QSL-08 | [#84 Material Strategy Lab](https://github.com/fjure/agentic-trading-platform/issues/84) | #83 |
| QSL-09 | [#85 Integrierte Quant- und Safetyabnahme](https://github.com/fjure/agentic-trading-platform/issues/85) | #77..#84 |

Neun Knoten,18 gerichtete native Kanten. Exaktes Vorwärts- und Rückwärts-Read-back jeder Adjazenzliste, Markereindeutigkeit über offene/geschlossene Repositoryissues, Titles/Managedblocks/Labels/Status und SHA256 sind in docs/development/github-quant-lab-issue-sync.json gespeichert. scripts/sync-quant-lab-issues-gh.py erzeugt ohne --apply ausschließlich Vorschau, mit --apply idempotenten Sync. Unerwartete Abhängigkeiten werden nicht gelöscht, sondern blockieren.

Planungsbranch `codex/quant-strategy-lab-v1-proposal`; zukünftige Issueimplementierungen erhalten nach gesondertem Applyauftrag eigene Branches gemäß AGENTS.md. Propose schließt keines dieser neun Issues.
