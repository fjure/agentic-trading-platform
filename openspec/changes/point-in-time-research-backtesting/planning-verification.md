# Planungsnachweis

Stand: 26.10.2026. Umfang: Planung von `point-in-time-research-backtesting`; keine Anwendungsimplementierung und kein Apply-Auftrag. Proposal, neue Capability, Design und neun offene Tasks sind vorhanden. Bestehende Änderungen an README.md und apps/dashboard/angular.json gehören nicht zu diesem Planungsdurchlauf.

## Abgleich und Review

Die vorhandenen Researchquery-, Kalender-, Domain-, Job-, Replay- und APIquellen sowie Research-/Replay-Spezifikationen wurden gelesen. Die neue Capability lässt deren Anforderungen unverändert. Ein unabhängiger Review fand und prüfte die Korrekturen folgender Punkte:

- Eigener beobachtungsgebundener Kalenderplanport ohne unzulässigen Sitzungsschlussfilter; vorhandenes ingested_at bleibt konservative Wissensgrenze.
- Materialisierung aller begrenzten Revisionen unter festem Snapshot mit eindeutigem Keyset; fortschreitende Wissensselektion erfolgt anschließend getrennt je Entscheidung.
- Feste 60s Entscheidungsverzögerung; spätere Ergebnisbars sind getrennte ex-post Proxys mit 300s Beobachtungscutoff und niemals Signalinput.
- Deterministisch gebundener slow-1-Warm-up, vollständige Kalenderfenster und keine Umgehung von Lücken.
- Zuständigkeit für Coveragebelegmigration in BKT-03; Job-/Ergebnispersistenz in BKT-06.

Nach begrenztem Nachreview dieser Korrekturen blieb kein konkreter Planungsblocker. Acht Requirements und 15 Szenarien sind den neun Tasks zugeordnet; Abhängigkeiten sind zyklusfrei. Das ist ein Planungsreview, kein Nachweis implementierter Sicherheit oder Performance.

## Tatsächlich ausgeführte Planungsprüfungen

- `./scripts/openspec.ps1 validate point-in-time-research-backtesting --strict`: bestanden.
- `./scripts/openspec.ps1 validate --all --strict`: 16 bestanden, 0 fehlgeschlagen.
- `./scripts/openspec.ps1 status --change point-in-time-research-backtesting`: 4/4 Planungsartefakte vorhanden; neun Implementierungsaufgaben offen.
- GitHub-Sync über die in AGENTS.md erlaubte GH-CLI-Alternative: Zielrepository und Login geprüft, alle offenen/geschlossenen Issues auf eindeutige IDs durchsucht, Vorschau erzeugt, neun Tasks angelegt und vollständig einzeln sowie repositoryweit zurückgelesen. Mapping, Hashs, Links und aktuelles Wiederholungsergebnis: `docs/development/github-backtesting-issue-sync.json`.

## Übergang zu Apply

Der Vorschlag ist reviewbar; der nächste Auftrag ist ein gesondertes Apply für diesen Change. Dessen erstes Paket BKT-01 liefert kanonischen OAS-/Schemaentwurf, Beispiele, Clientgenerierung und Vertragsprüfungen, bevor API-/UIlogik implementiert wird. Keine Anwendungstests, Migrationen, Providerabrufe, Brokeraktionen, Commits oder Pushes wurden für diese Planung ausgeführt. Echte historische Nutzbarkeit bleibt durch nachgewiesene damalige Verfügbarkeit einschließlich Kalender-/Kapitalmaßnahmenbelegen begrenzt; heutiger Backfill kann korrekt BLOCKED ergeben.
