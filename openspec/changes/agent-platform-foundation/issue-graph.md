# Issue-Graph

Ziel: `fjure/agentic-trading-platform`. Die zehn Work Packages sind offene Implementierungsaufträge. Der Planungschange wird als eigenes Paket auf `codex/apf-agent-platform-foundation` veröffentlicht. Die Implementierungspakete folgen erst nach Apply-Freigabe.

| Paket | Issue | Blockiert durch | Blockiert |
| --- | --- | --- | --- |
| APF-01 | [#86](https://github.com/fjure/agentic-trading-platform/issues/86) | – | #87, #88, #89 |
| APF-02 | [#87](https://github.com/fjure/agentic-trading-platform/issues/87) | #86 | #88, #89, #90, #91 |
| APF-03 | [#88](https://github.com/fjure/agentic-trading-platform/issues/88) | #86, #87 | #89, #91 |
| APF-04 | [#89](https://github.com/fjure/agentic-trading-platform/issues/89) | #86, #87, #88 | #90, #91 |
| APF-05 | [#90](https://github.com/fjure/agentic-trading-platform/issues/90) | #87, #89 | #92, #93 |
| APF-06 | [#91](https://github.com/fjure/agentic-trading-platform/issues/91) | #87, #88, #89 | #93 |
| APF-07 | [#92](https://github.com/fjure/agentic-trading-platform/issues/92) | #90 | #93 |
| APF-08 | [#93](https://github.com/fjure/agentic-trading-platform/issues/93) | #90, #91, #92 | #94 |
| APF-09 | [#94](https://github.com/fjure/agentic-trading-platform/issues/94) | #93 | #95 |
| APF-10 | [#95](https://github.com/fjure/agentic-trading-platform/issues/95) | #94 | – |

Die 17 gerichteten Kanten wurden gegen die Work Packages aus den nativen Endpunkten `blocked_by` und `blocking` paginiert zurückgelesen. APF-01 ist Wurzel, APF-10 die abschließende Abnahme. Jede Kante weist auf ein früheres Paket, und alle Pakete sind von APF-01 erreichbar und führen zu APF-10. [REST-Referenz](https://docs.github.com/en/rest/issues/issue-dependencies).

Der Connector erlaubte Suche, wies aber die Issue-Erstellung mit HTTP 403 ab und bietet keine native Abhängigkeitsoperation. Die autorisierte GH CLI legte das fehlende Change-Label, zehn Issues und die 17 Kanten an. Die Issues wurden einzeln auf Titel, vollständigen Inhalt, Marker, offene Zustände und Labels geprüft. Vor dem Schreiben wurden alle offenen und geschlossenen Issues auf bestehende Identitäten geprüft. Die [Mapping-Datei](github-issues.json) enthält URLs, Inhalts-Hashes, UTC-Zeit und native Kanten.

Die Prüfung erfasst Planungsissues. Alle Implementierungscheckboxen bleiben offen.

Der zweite repositoryweite, rein lesende Abgleich fand dieselben zehn Issues und 17 Kanten ohne Drift. Er erforderte keine Schreiboperationen.
