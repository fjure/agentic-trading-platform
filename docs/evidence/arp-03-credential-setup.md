# ARP-03 · Separater lokaler Research-Setup

Basis `4aa43c9fb85cb2d5a197faf1f4d5eb7b21c9ce39`. GitHub bestätigte #96 als abgeschlossene Voraussetzung. Der neue OpenAPI-Pfad und der generierte Client verwenden einen einmaligen, an Provider und Aktion gebundenen Reauth-Grant. Der Frontend-Proxy prüft die lokale Browsersitzung und streamt den begrenzten Body über den Unix-Socket. Setup prüft Origin und Grant eigenständig. Die Antworten enthalten ausschließlich redigierte Statusdaten.

Setup schreibt atomar in das dedizierte POSIX-Volume. Gateway erhält ausschließlich einen Read-only-Mount. Der Reader prüft vor der Schlüsselfreigabe Generation, gebundene Metadaten und aktuelle Autorisierung. Rotation und Widerruf entwerten die alte Generation. Die autoritative Supervisor-Transaktion für Send-Commit und Publikationsschutz folgt in ARP-04 beziehungsweise ARP-09. Das Deployment bleibt für externen Modellversand gesperrt.

- 71 fokussierte HTTP-, Vertrags- und Sicherheitsprüfungen bestanden. Sechs POSIX-Speichertests bestanden in einem netzlosen Linux-Container.
- `python -m tests.security.research_setup_compose` prüft UID/GID, Schreibgrenzen, private Reauth-Sockets und den synthetischen Lifecycle im separaten Compose-Projekt.
- OpenSpec Strict, Vertragsvalidierung, Redocly, semantischer Vergleich, zweifache Clientgenerierung und TypeScript-Kompilierung bestanden. Der semantische Vergleich meldete die bestehende `allOf`-Warnung. Es wurden ausschließlich synthetische Testschlüssel verwendet.
