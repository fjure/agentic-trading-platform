# ARP-05 · Research-Integration in GitHub Actions

Der Workflow `Research integration` führt Fast-, PostgreSQL- und Chromium-Gates aus und prüft sie im stabilen Job `Research required evidence`. Er läuft bei Pull Requests nach `main`, Merge-Queue-Prüfungen, Pushes nach `main`, Release-Tags `v*` und manueller Releasevalidierung. Die Browserkette läuft bei jedem dieser Anlässe. Dadurch erfassen auch Änderungen an gemeinsamen Abhängigkeiten den vollständigen Pfad.

`python scripts/test-research-db.py --junitxml <Datei>` und `python scripts/test-research-browser.py --junitxml <Datei>` starten isolierte synthetische Testumgebungen. Chromium prüft den echten Angular-Build, explizite Freigabe, persistierte Hypothese, Quant-Freigabe, Worker und Bericht. Die SQL-Nachprüfung prüft unter anderem null Brokerorders. Beide Providerprotokolle werden mit tatsächlichen Loopback-TCP-/TLS-Verbindungen durch den CONNECT-Proxy geprüft. Die vollständige Transport- und Recovery-Topologie für die Produktionsabnahme ist ARP-09 zugeordnet. Der Browserjob baut aus dem unveränderten Lockfile des geprüften Checkouts.

Der Aggregator bindet redigiertes JUnit an Quell-SHA, Manifest-SHA und Artefaktprüfsumme. Fehlende Umgebung, übersprungene Fälle, fehlende IDs, doppelte Pflichtfälle, fehlende Artefakte und abweichende SHAs verhindern einen positiven Check. Neue Offline-Suiten werden mit einem Fragment in `scripts/research-evidence/` und der Testauswahl des zugeordneten Gate-Runners registriert.

GitHub meldete für `main` keine geltende Branchschutzregel und kein Ruleset. Wer Merge-Schutz erzwingen will, muss im Repository-Ruleset den Check `Research required evidence` aus GitHub Actions für `main` verpflichtend machen. Bestehende Regeln wurden lesend geprüft.
