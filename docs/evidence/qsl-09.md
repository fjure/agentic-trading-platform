# QSL-09 – integrierte Quant- und Safetyabnahme

Die Abnahme verwendet synthetische SPY-/QQQ-Daten und lokale Testidentitäten.
Die Zuordnung der 16 Anforderungen, 21 Szenarien und Methodenorakel S1–S12
steht in [Implementierungsverifikation](qsl-implementation-verification.md).

## Bereits ausgeführte Regressionen

- Python-Unit-, Property- und Contract-Suite am Ausgangsstand `7692388`:
  3868 bestanden, ein Linux-tmpfs-Test unter Windows übersprungen.
  Laufzeit 135,23 s. Der Testclient meldete eine Starlette-Abkündigungswarnung.
- Beide Contracts-Workflows von QSL-08 auf `main` waren erfolgreich.
  Die Run-IDs sind [42307704681](https://github.com/fjure/agentic-trading-platform/actions/runs/42307704681)
  und [42307978362](https://github.com/fjure/agentic-trading-platform/actions/runs/42307978362).
- OpenSpec-Validierung mit `--strict` erfolgreich.
- Vertrag mit 81 HTTP-Operationen und 60 Quant-Fixtures validiert. Der
  semantische Vergleich gegen `379ebff` ergab null Fehler und die in
  [QSL-07](qsl-07.md) untersuchte Readiness-Warnung. Zwei frische Generationen
  und der eingecheckte Angular-Client stimmen in allen 370 Dateien überein.
  Der TypeScript-Check war erfolgreich.
- Der eingefrorene Runtime-Container startete im Modus `DISABLED` mit
  0,5 CPU, 256 MiB, read-only Dateisystem, ohne Netzwerk und ohne Mounts.
  Der Smoke prüfte den Quellfingerprint und das gebündelte Quant-Schema.
- Der PostgreSQL-Harness für Migration, Queue, Recovery und Restore bestand
  in 78,98 s. Der echte Worker-Reader nimmt 99.999 und 100.000 Zeilen an und
  weist 100.001 ab. Die SQL-Checkpoint-Grenze persistiert dieselben zwei
  zulässigen Barschrittzahlen und weist den zusätzlichen Schritt ab.
  Bei synthetisch gesetztem Vorverbrauch lässt der echte Report-Trigger
  12 MiB minus ein Byte zu, persistiert den exakten Grenzwert über den
  normalen Publish-Pfad und weist ein Byte über der Grenze mit SQLSTATE
  `23514` ab. Zeilen-, Schritt- und Bytegrenze wurden separat geprüft.
- Die drei bestehenden Container-Isolationstests für Backtest, Readiness
  und Retrospective bestanden mit dem erweiterten Harness in 36,46 s.
- Backtest-, Readiness-, Retrospective-, Risk-Race-, Reconciliation-,
  Market-Gateway- und Rollenregressionen mit lokalen Testdatenbanken
  bestanden vollständig (39 Tests in 591,76 s).
- Die PostgreSQL-/HTTP-Admission prüfte ein kurzes RSI-Raster mit
  32 Suchkandidaten und zwei festen Referenzen. Der validierte Batch
  reservierte 34 Slots, zusätzlich zu einem vorhandenen Batch mit drei
  Slots. Die API-Prüfung einschließlich des 99-Slot-Falls bestand in 53,58 s.
- Der Credential-Pattern-Scan der 259 Change-Dateipfade meldete null Funde.
- Die ausgeführte Chromium-Kette über Produktionsdashboard, HTTP, PostgreSQL
  und Worker bestand mit den normativen Zeitfenstern und 26 Bars je Sitzung
  in 2,0 min (zwei Tests). Der Material-Entwurf startete neun
  EMA-Konfigurationen und zwei Referenzen in Strict PIT. Die explizite
  Mindestaktivität führte zur vollständigen Nichtwahl. SQL bestätigte
  9.178 Barschritte und null Brokerorders. Chromium prüfte paginierte
  Ergebnisse, Equity/Drawdown, Reliability, Reload, sichtbar wiederverwendete
  OOS- und Holdout-Rollen und das Verwerfen einer verspäteten Antwort nach
  Sitzungsverlust. Die kürzere Browser-Fixture
  und Legacy-Browserregression stehen in [QSL-08](qsl-08.md).

## Vollständiger synthetischer Forschungsbatch

Neun Kombinationen aus EMA-Fastperioden 5, 10 und 15 sowie Slowperioden 30,
40 und 50. Flat und Buy-and-hold laufen als separate Referenzen. Training,
Validation, Walk-forward-OOS und Holdout enthalten 130, 546, 104 und 130 Bars.
Jede Phase startet mit 49 gebundenen Seedbars. Die Validation umfasst
21 vollständige Sitzungen.

| Modus und Ergebnis | Schritte | Veröffentlichte Bytes | Worker-Wandzeit |
| --- | ---: | ---: | ---: |
| Retrospective Exploratory, Kandidat ausgewählt | 9.510 | 6.136.340 | 78,89 s |
| Strict PIT, vollständige Nichtwahl bei 50 Mindesteinstiegen | 9.178 | 5.909.123 | 91,06 s |

Die echte PostgreSQL-/HTTP-/Worker-Kette einschließlich Restore bestand in
257,83 s (ein Test). Beide Auswahldatensätze wurden vor dem ersten OOS-Child
persistiert. Der gewählte Kandidat verlor im Holdout, die Auswahl blieb
unverändert. Die kurzen Testphasen lieferten Sharpe ohne Wert und die
Foldübersicht `SMALL_FOLD_SAMPLE`. Im Nichtwahlzweig bleiben die festen
Referenzen auswertbar. Die wiederverwendeten Test- und Holdoutrollen sind
registriert, externe Vorkenntnis bleibt unbekannt. Die Messwerte stammen aus
der synthetischen Fixture. Sie liefern keine Prognose oder Handelsfreigabe.

## Ressourcen, Isolation und Wiederanlauf

Der kombinierte Docker-/PostgreSQL-Test bestand in 181,20 s. Paper und
Quant teilten sich `lab_paper_runtime`. Der Quant-Worker lief mit eigener
Login- und Gruppenrolle, 0,5 CPU, 256 MiB, UID/GID 10001, schreibgeschütztem
Root und ausschließlich im internen Worker-Netz. Der Test verwendete
synthetisches HBA-Trust für die isolierten Subnetze und belegte echte
HBA-Ablehnungen für die falsche Identität und Datenbank sowie fehlende
SQL-Rechte mit `42501`. Der produktive Authentifizierungsweg verwendet SCRAM.

- 9.510 Barschritte und 6.136.340 veröffentlichte Bytes. Die Worker-Wandzeit
  betrug 93,555 s, die CPU-Zeit 20,969 s und die cgroup-Speicherspitze
  66.068.480 Bytes (63,01 MiB).
- Die gemischte Queue reservierte 99 Quant-Slots und einen Legacy-Slot.
  Weitere Quant- und Legacy-Anfragen wurden bei 100 Slots abgewiesen.
  Am ersten echten Simulationsbar bestätigte der Worker die volle Queue,
  genau einen laufenden Batch und null ausgecheckte SQL-Verbindungen.
- Während der laufenden Simulation erreichte Paper `PAUSE_NEW` in
  0,002 s und `HALTED` nach dem Kill Switch in 0,002 s. Reconciliation
  benötigte 0,031 s. Elf stabile Finanzfingerprints, nichtleere Positionen,
  Cash und Equity blieben unverändert. Der Test bestätigte null Brokerorders
  und null externe HTTP-Aufrufe des Quant-Workers.
- Drei Kernel-OOMs am ersten Simulationsbar endeten jeweils mit
  `OOMKilled` und Exit 137. Ein synthetischer Zeitsprung am abgelaufenen
  Lease beschleunigte den Wiederanlauftest. Die drei Fences konservierten
  kumulativ 179, 358 und 537 Barschritte. Danach blieb der Batch
  `ATTEMPTS_EXHAUSTED` ohne vollständig publizierten Resultatlink.

## Unabhängige Reviews

Der Quant-/Leakage-Review bestätigte den phasenlokalen Validation-Evidenzhash,
die vollständige atomare Auswahl vor OOS und das Wiederverwenden des Freeze
nach Neustart. Der Architektur-/Security-Review prüfte die Workergrenzen,
die Rollen und HBA-Negative, die gemeinsam genutzte Datenbank, die
Finanzfingerprints und die tatsächliche OOM-/Fence-Kette. Die Reviews
hinterließen keine offenen kritischen Befunde. Die korrigierten
SQL-Grenzproben setzen den synthetischen Vorverbrauch als Migrationseigentümer
und führen den Report-Insert mit Workerrechten aus.
