# Providerbewertung

Öffentliche offizielle Quellen am 27.10.2026 geprüft; keine authentifizierten Provideraufrufe oder persönlichen Vertragsdaten. Context7 resolve-library-id für Alpaca und GitHub CLI wurde zuerst versucht und meldete ausgeschöpfte Monatsquote. Fallback auf offizielle öffentliche Dokumentation; keine Neuinstallation oder MCP-Konfigurationsänderung.

## Entscheidung und Grenzen

Alpaca/raw/IEX/15Min bleibt bestehen. [Historische Bars](https://docs.alpaca.markets/us/reference/stockbars) unterscheiden raw von split-/dividend-/spin-off-/all-Adjustierung. Diese Optionen sind Preis-/Volumentransformationen; sie belegen keine historische Publikation der jeweiligen Revision. In diesem Change wird keine davon neu zugelassen. Es gibt keine Mischung adjustierter Signale mit raw-Fills und keine implizite Total-Return-Semantik.

Die [Market-Data-Übersicht](https://docs.alpaca.markets/us/docs/about-market-data-api) unterscheidet begrenzte IEX-Abdeckung und umfassendere bezahlte Abdeckung. SIP ist kein Beleg historischer lokaler Beobachtung oder realistischer Ausführbarkeit. Neue Planbuchung, Kosten und Providerwechsel sind nicht erforderlich und bleiben außerhalb des Scopes. Die konkreten persönlichen Feed-/Speicher-/Forschungs-/Weitergaberechte wurden nicht geprüft. Öffentliche Produktverfügbarkeit erteilt keine Lizenz.

Die [Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) nennt fehlende Berechtigungen als Fehlergrund. Keine Fehlerbehandlung darf automatisch Feed oder Provider wechseln. [Disclosures und Agreements](https://alpaca.markets/disclosures) sind der öffentliche Einstieg für geltende Bedingungen; ohne private Betreiberprüfung kann keine konkrete Vereinbarung bestätigt werden. Deshalb Report LICENSE UNKNOWN, bei expliziter Verweigerung BLOCKED. Unbekannte Rechte erlauben technisch nur vorhandene lokale interne Exploration mit Warnung; diese Funktion ist keine Rechtsfreigabe, kein Export und kein Beschaffungsauftrag.

Eine aktuell ausreichende authentische Kalenderquelle kann retrospektiv benutzt werden, bleibt aber ex-post. Fehlende vollständige Sitzungsgültigkeit blockiert. Unbekannte Maßnahmen werden als unbekannt ausgewiesen; bekannte nicht modellierte strukturelle Maßnahmen blockieren. Ein späterer belastbarerer Modus benötigt versionierte vollständige Eventdaten, Rechte und Zahlungs-/Quantitymodelle. Neue Corporate-Action-Ports oder Adjustierungsanbieter werden nicht behauptet oder benötigt.

## GitHubtransport

[GH api](https://cli.github.com/manual/gh_api) unterstützt strukturierte JSONdateien und paginierte Read-backs. Die offiziellen [Issue-Dependencies](https://docs.github.com/en/rest/issues/issue-dependencies) definieren GET/POST `issues/{number}/dependencies/blocked_by` mit numerischer `issue_id` des Blockers und Rückwärtsprüfung `dependencies/blocking`. Der aktuelle Nutzerauftrag autorisiert diese nativen Abhängigkeiten ausdrücklich und ersetzt für diesen Change die ältere lokale Beschränkung auf Textabhängigkeiten. Keine Hierarchien, Kommentare, PRs oder Löschungen.
