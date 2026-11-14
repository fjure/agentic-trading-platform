# ARP-08 · Synthetischer Provider-Smoke

Der produktive Gatewaypfad kennt einen separaten Probe-Zweck. Das Gateway akzeptiert dafür ausschließlich die feste synthetische Projektion mit der festgelegten Ausgabeform. Signierte Admission und persistentes Einmaljournal bleiben im Sendepfad. Ein Probe-Deskriptor mit `probeOutputTokens` lädt den Supervisor für ein STAGED-Profil. Die Standardkomposition bleibt gesperrt.

Die optionale Compose-Ergänzung `compose.research-smoke.yaml` stellt dem Supervisor die private versionierte Kostenpolicy bereit. Der lokale Betreiber verwendet im Supervisor `python -m services.runtime.research_smoke_cli preview` und bestätigt genau die ausgegebene Kennung und Bindung mit `approve --grant <id> --commitment <hash>`. `verify --grant <id>` fordert die fachliche Verifikation der belegten Abrechnung an. Die Egress-Entscheidung ist separat. Der Smoke erteilt keine Freigabe für Dialog, Discovery oder Handel.

Die synthetische TLS-Prüfung verwendet den echten Gatewaytransport, einen isolierten Loopback-CONNECT-Proxy und eine Test-CA. Sie prüft die festen Requestbytes, den richtigen Wire-Pfad, verweigerten Versand, Replay, Redirect, Rate-Limit, abweichendes Modell und fehlendes TLS-Vertrauen. Ein separater Supervisor-Test prüft einen unbekannten Ausgang ohne erneuten Versand. Es wurde kein echter Provider aufgerufen.
