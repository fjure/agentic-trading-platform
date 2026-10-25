# Isolierte Paper-Prozessuhr

Ausschließlich Testwerkzeug: Produktionscode importiert dieses Paket nicht. Die
Images ergänzen `/opt/test-clock`, aktivieren standardmäßig aber keine Uhr.
Hostzeit, Docker-Engine, Produktionsimages, echte Credentialverzeichnisse und
normale Datenbanken werden weder verändert noch eingebunden.

Zuerst `scripts/bootstrap-paper-test-clock.ps1`, danach
`.venv/Scripts/python.exe -m pytest tests/integration/test_paper_test_clock.py -q`
ausführen. Der Bootstrap lädt genau ein offizielles Commitarchiv nach
`.tooling/output/paper-clock/`, prüft SHA256 und kompiliert mit dem signierten
Debian-Snapshot `20261005T000000Z`. Nur dessen explizite Archivablaufprüfung ist
deaktiviert; Signaturprüfung bleibt aktiv. Basisimages sind per Digest gepinnt.

Die Kompatibilitätsprüfung verwendet die unveränderlichen Referenzen aus
`runtime.py`. Ein neu gebautes Image mit anderer OCI-Identität benötigt einen
neuen geprüften Pin und eine erneute Abnahme. Der Bootstrap zeigt Identitäten
und ändert Pins nicht selbst. Compilerinventar, Quellversion, COPYING und
Bibliothekshashes liegen unter `/opt/test-clock`.

## Schnittstelle

Auf dem Host `shared_offset`, `clock_environment`, `PYTHON_IMAGE` und
`POSTGRES_IMAGE` aus `tests.integration.paper_clock.runtime` importieren. Ein
Gast darf dieses Verzeichnis ausschließlich lesend einbinden.

`offset = shared_offset()` genau einmal auf dem unveränderten Host berechnen
und denselben Wert allen ausgewählten temporären Backend-/Providerprozessen
übergeben. Defaultziel: Montag `2026-10-26T14:30:05Z`, gültig im unveränderten
Produktionskalender. Der vorzeichenbehaftete Sekundenoffset läuft mit normaler
Geschwindigkeit1x. Keine pro Child neu berechneten Offsets verwenden.
`clock_environment(offset, python=True)` ist für mehrthreadiges Python, die
Defaultvariante für PostgreSQL. UTC-Darstellung ändert keine IANA-Marktzeitzone.

Wegen `LC_ALL=C` jede neue Anwendungs-PostgreSQL ausdrücklich mit
`POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C.UTF-8` initialisieren; sonst
kann SQL_ASCII entstehen und psycopg/SQLAlchemy empfängt Bytes statt Text. Die
reine Timingprobe verwendet ASCII-SQL und bestätigt diese Anwendungseigenschaft
allein noch nicht. Multiplikatoren, Start-at-Direktiven und externe
Konfigurationspfade sind verboten und werden vom Helper abgewiesen.

Aktivierung nur für den einzelnen Prozess über `LD_PRELOAD`, `FAKETIME`,
`FAKETIME_DONT_FAKE_MONOTONIC=1`, `NO_FAKE_STAT=1`, `TZ=UTC`, `LC_ALL=C`.
Kein SYS_TIME, globales Preload, eigene SQL-Uhrfunktion, Produktions-
Zeitzonenoverride, fingierter Readybeleg oder finanzieller Seed.

## Native Fristen und Grenzen

Die Compileroptionen ergänzen `-UFAKE_STAT -UFAKE_UTIME -UFAKE_SLEEP` zu den
Upstreamdefaults. Dateisystemzeiten und blockierende Sleep-/Pollaufrufe bleiben
nativ. `-Wno-unused-function` betrifft ungenutzte Upstreamhelper nach Abschalten
der optionalen Sleep-Hooks; sonstige Warn-/Fehlerprüfungen bleiben aktiv.
Upstreamquellen wurden nicht gepatcht; normale und MT-Bibliothek bleiben erhalten.

Der erste Build mit optionalen Sleep-Hooks scheiterte am PostgreSQL-Statement-
Timeout und ist nicht zertifiziert. Keine Timeoutassertion wurde gelockert. Der
begrenzte Build besteht die echten200ms-Statement-/Lockfristen und nativen Wartezeiten.

Erst nach bestandener Probe auf der tatsächlichen Plattform einsetzen. Die
16 Proben prüfen Python-/PG-Gleichlauf, neue Prozesse/Backends, Threads,1x-Verlauf,
echtes Monotonic/stat/utime, Python-/PG-Sleep, Event, asyncio-/HTTPfristen,
SQL-Statement-/Lockfristen und Artefaktintegrität. Browserworkflow, produktive
Netzisolation, Brokerverhalten und finanzielle Gates brauchen getrennte Evidenz.
Chromium erhält kein Preload und verwendet die bestehende Serverzeit `observedAt`.

Jedes Szenario besitzt ein neues Wegwerfcluster. Positive Einstiege unterliegen
weiterhin120s-Kerzenfrische und30s-Freigaben. Keine Marktgates ändern und keine
historische Admission als neuen Einstieg ausgeben.

Primärquellen: [gepinntes Upstream-README](https://raw.githubusercontent.com/wolfcw/libfaketime/v0.9.13/README),
[Compilerflags](https://raw.githubusercontent.com/wolfcw/libfaketime/v0.9.13/src/Makefile),
[Releasecommit](https://github.com/wolfcw/libfaketime/commit/86b37fde2fed7336ea2d0c17928e3015a55d9b4a).
