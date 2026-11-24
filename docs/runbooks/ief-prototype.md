# IEF-Prototyp zur formativen Prüfung

Diese Anleitung betrifft ausschließlich den gekennzeichneten synthetischen IEF-01-Prototyp. Seine Klicks sind Entwurfsübergänge, keine persistierten Portfolioanalysen, Researchläufe, Freigaben oder Paperanforderungen. Die bestehenden Funktionen unter „Daten und Betrieb“ sind getrennt; für die menschliche Prototypprüfung ausschließlich die unten beschriebene synthetische Gegenstelle verwenden.

## Vorbereitung

Arbeitsverzeichnis: `.worktrees/ief-01`, Branch `codex/ptv-107-ief-foundation`, Ausgangs-SHA `82139cd2f4a87d88b92612b5fae55d8caa367d4a`. Den tatsächlichen Prüfstand einschließlich unveröffentlichter Dateihashes in `docs/evidence/ief-01.md` und der zugehörigen Manifestdatei festhalten. Ein unveränderter Git-HEAD allein identifiziert keine uncommitteten Änderungen.

Node 22.23.3 und die gesperrten Dashboardabhängigkeiten verwenden. Nach verfügbarer gepinnter Toolchain:

```powershell
npm --prefix apps/dashboard ci --no-audit --no-fund
npm --prefix apps/dashboard run build
node tests/e2e/server.mjs
```

Der letzte Befehl stellt den echten Produktionsbuild über die vorhandene ausschließlich synthetische Loopback-Gegenstelle bereit. Währenddessen keine parallelen Browsertests auf Port 4200 starten. Es sind keine Broker-/Providerkonten, API-Schlüssel oder echte Finanzdaten erforderlich. Die Gegenstelle importiert keine Brokeradapter oder Anwendungsdienste. Sie ersetzt weder die später vorgeschriebene API-/PostgreSQL-Abnahme noch einen echten Betrieb.

## Menschliche Prüfung

1. `http://127.0.0.1:4200/onboarding` öffnen. Die sichtbare Prototypkennzeichnung und synthetische Quelle vor Beginn prüfen.
2. Nach [Prüfprotokoll](../evidence/ief-human-ux-protocol.md) mindestens fünf fachfremde Personen testen, mindestens zwei Sitzungen auf Mobile und zwei auf Desktop. Alle Personen bearbeiten alle sechs Aufgaben. Keine Navigationshinweise geben.
3. S1–S10, Strategie → historischer Beleg → separate Eignung → manuelle Vorschau → unverbindliche Risk-Vorprüfung → simulierte Bestätigung → Status → Aktivität prüfen. Stale muss zur neuen Prüfung, UNKNOWN zum bestehenden Vorgang führen.
4. Tastatur, Fokus, Screenreader, 320/360-Pixel-Ansichten und 200-Prozent-Zoom zusätzlich prüfen. Automatisierte Befunde getrennt von menschlichen Antworten führen.
5. Nur pseudonyme, redigierte Bewertungen und Aggregate in die leeren Protokollvorlagen übernehmen. Einwilligung und Rohdaten privat aufbewahren. Kritische Befunde korrigieren und mit frischen Personen nachtesten.
6. UX- und Produktverantwortung geben genau die geprüfte Fassung frei. Die drei menschlichen Runden sind in IEF-09 gebündelt und werden getrennt ausgewertet. Der Flow kann nach technischer Prüfung an IEF-02–07 übergeben werden.

## Offene Freigabe und Wiederanlauf

Der Nutzer hat bestätigt, dass derzeit keine echten Testpersonen verfügbar sind. Status: **HUMAN_UX_VALIDATION_PENDING**. Die offene formative Prüfung IEF-T-C06 wird gemeinsam mit IEF-T-O05 und IEF-T-C08 in IEF-09 durchgeführt. Die technischen Paketabschlüsse IEF-01–08 können vorher erfolgen.

Prototypdaten sind keine Finanzhistorie. Ein Reload darf nur die gekennzeichneten synthetischen Ausgangszustände beziehungsweise gebundenen Referenzen zeigen, keine ausgedachte serverseitige Persistenz. Freie Texte und Geheimnisse gehören weder in URLs noch Browserpersistenz. Beenden der lokalen Gegenstelle mit Strg+C verändert keine Finanz-, Audit- oder Researchdaten.

## Technischer Nachlauf der PostgreSQL-Rollentests

Mit einem verfügbaren Linux-Docker-Engine führt der Research-Runner die vier offenen Rollenfälle separat aus. Er erstellt dafür eine temporäre PostgreSQL-Instanz in einem internen Compose-Netz ohne veröffentlichte Ports und verwendet generierte Testzugangsdaten.

```powershell
python scripts/test-research-db.py --roles-only --junitxml .tooling/ief-01-roles.xml
```

Der Exitcode 2 bedeutet, dass die Testumgebung nicht verfügbar war. Ein erfolgreicher Nachweis erfordert einen Lauf mit vier bestandenen Fällen im JUnit-Bericht. Die formative UX-Freigabe bleibt ein eigener Prüfschritt.
