# ADR 0001: Modulare Prozesse und Credentialgrenzen

Status: geplant, 2026-10-12. Entscheidung D-01/D-04/D-06 im Change.

Control API, Worker, Execution und Watchdog teilen typisierte Domainverträge und PostgreSQL, besitzen aber getrennte Prozessrechte. Nur Execution erhält Paper-Brokercredentials; falls Marktdata dieselbe Brokeridentität benötigt, liegt der read-only MarketData-Gateway innerhalb dieser Grenze. Worker erhält normalisierte Daten, keine Schlüssel. Jev-Key nur im Decisionadapter.

Alternative ein Prozess würde die Credentialgrenze gegenüber Agenten nicht durchsetzen. Vollständige Microservices/Kafka/Kubernetes sind für lokalen Betrieb unnötig. Konsequenz: getrennte Secretmounts/Egress/DB-Rollen müssen getestet werden; gemeinsamer Pythoncode ist keine Berechtigungsgrenze. Risk und Submitgate sind deterministisch; LIVE weder Enum noch konfigurierbarer Host. Fake/Replay laufen ohne externe Credentials.
