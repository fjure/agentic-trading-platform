# APF-01 · Additiver HTTP-Vertrag

Der verpflichtende [Contracts-Preflight auf `415abd8e35ddcfedaf510aad35ff1b0fd1af5115`](https://github.com/fjure/agentic-trading-platform/actions/runs/42471008009/attempts/2) bestand nach Neustart über die GitHub CLI. Python-Setup, gepinnter Tool-Bootstrap, Schema- und Beispielprüfung, Breaking Gate, reproduzierbarer Client, 2331 Contract-Tests, Credential-Scan, Angular-Produktionsbuild und Browserprüfung liefen erfolgreich. Der ursprüngliche Python-Downloadfehler erforderte keine weitere Änderung am Workflow.

Im Arbeitsbranch `codex/ptv-86-apf-contracts` sind die drei Operationen für die flüchtige Verarbeitungsvorschau, den inhaltsfreien Status und die getrennte Entscheidung hinzugefügt. Der Vertrag übernimmt die bestehende LocalSession- und Same-Origin-JSON-Grenze. Die Vorschau hat einen sitzungsgebundenen Idempotenzvertrag und `Cache-Control: no-store`. SEND, DECLINE und REVOKE akzeptieren jeweils nur ihre eigenen Bindungen. Eine angenommene Sendefreigabe liefert ein Receipt. Die Modelle für dauerhaft abrufbare Antworten enthalten keine dynamische Projektion.

Ein unabhängiger Review identifizierte fehlende Modellgeneration und Kostenstatus, ein optionales Receipt bei angenommener Sendefreigabe und die Möglichkeit einer Vorschau ohne Deklaration des Ausschnitts. Diese Befunde sind im Vertrag und in den gezielten Prüfungen berücksichtigt. Ob `CONFIRMED_RESEARCH_FIELDS` genau zu den tatsächlich belegten Kontextfeldern passt, ist eine dokumentierte serverseitige Bindungsprüfung. Die lokale Contract-Suite prüfte 31 Fälle gegen das kanonische OpenAPI-Dokument und das eigenständige JSON Schema. Redocly und Schema-/Beispielvalidierung waren erfolgreich. Der semantische Diff meldete keinen Breaking Change und die bestehende `allOf`-Warnung beim Readiness-Endpunkt.

## Vollständiger Vertragsumfang

| Anforderungen | Vertragsgrenze und Prüfung |
| --- | --- |
| APF-C01 | Session und Same-Origin-JSON-Grenze, typisierte Fehler, Cursor, Versionsbindung und Idempotency-Key. Die Vertragstests prüfen Parameter, geschlossene Eingaben und sichere Antwortformen. |
| APF-C04 | Geschlossene Browserprojektionen für Ideen, Registry, Läufe, Zeitplan, Quellen, Approvals und Berichte. Der Report trennt Fakten, gebundene Kennzahlen, Interpretation und unbekannte Angaben. Vier benannte Forschungsphasen verhindern Duplikate und unterbinden Kennzahlen für nicht verfügbare Phasen. |
| APF-P02 | Speichern, synthetische Probe, Aktivieren, Rotieren und Widerrufen bleiben getrennte Operationen. Credential-Werte sind ausschließlich in den beiden Write-only-Requests der lokalen Einrichtung erlaubt. |
| APF-R05 | Freigabeprojektion mit Quellen und konkreter Run-, Ideen-, Hypothesen-, Quant-, Budget- und Richtlinienbindung. Die Betreiberentscheidung referenziert die vorhandene Version und den Bindungshash. APF verwendet die bestehenden Quant-Lab-Definitionen und Zulassungsverträge. |
| APF-C05 und APF-Q07 | Getrennte Verträge für lokale Antworten, freien Researchtext, fachliche Bestätigung und explizite Approximation. Expertenvorgaben referenzieren die gebundene Quant-Definition. Der Modelloutput enthält keine Autoritätsfelder und ist von der serverseitigen Vorschlagsbindung getrennt. |
| APF-P07 und APF-R07 | Flüchtige Vorschau, `no-store`, genau eine sitzungsgebundene Sendefreigabe, sichere Receipts und unabhängiger Widerruf. NLP-05 und NLP-12 prüfen die statische Form der Bindung und die Trennung der Entscheidungen. |

Die unabhängigen Reviews der getrennten Vertragsteile führten zu expliziten Bindungen für lokale Antworten, Expertenparameter und Approximationen sowie zur Ergänzung von Discovery-Frische, Priorität und Aufwand. Die Freigabeprojektion enthält Quellen und unterscheidet Zustände mit und ohne Batch-ID. Berichtsphasen sind vollständig und einzeln benannt.

Die Vertragstests prüfen Form und Trennung der Berechtigungen. Sessionprüfung, atomarer Verbrauch, Ablauf, Replay und die Bytegleichheit des tatsächlichen Dispatchs werden in den nachgelagerten Laufzeitpaketen geprüft.

## Prüfungen

- `validate-contracts.py` bestätigte 111 Operationen, 30 JSON-Schemas, 29 Schema-Beispiele und 312 OpenAPI-Antwortbeispiele. Redocly-Lint lief ohne Befund. Der semantische Vergleich gegen `415abd8e35ddcfedaf510aad35ff1b0fd1af5115` meldete null Fehler und die unveränderte Readiness-`allOf`-Warnung.
- `pytest tests/contracts -q` lief mit 2.429 bestandenen Tests. Nach der Trennung des Setup-Clients wurden alle 98 gezielten APF-Tests erneut grün ausgeführt. Der Credential-Scan meldete null Befunde.
- Der fest gepinnte Generator reproduzierte 489 Dateien bytegenau. Der Vergleich gegen die Anwendungsdateien und die TypeScript-Prüfung waren erfolgreich. Direkte Imports der vorhandenen Supervisor-Services halten die neuen APF-Services aus dem Startbundle heraus. Der Produktionsbuild blieb mit 977,54 kB unter der unveränderten 1,00-MB-Fehlergrenze.
- Der lokale Chromium-Lauf meldete 38 bestanden und 22 übersprungen. Die übersprungenen Fälle erfordern die getrennte Laufzeit-Testumgebung. Dieser Lauf ist Browserregression für die vorhandene Oberfläche.
- Der GitHub-Connector wies das Update des Managed Blocks von Issue #86 mit HTTP 403 ab. Die autorisierte GitHub CLI synchronisierte den Nachweis. Der anschließende Read-back verifizierte Inhalt, Identität, Labels und die drei nativen Nachfolger #87, #88 und #89.
