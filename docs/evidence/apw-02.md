# APW-02: Isoliertes inaktives Paperdeployment

Stand21.10.2026. REQ-APW-003/004/005/010. Umsetzung und lokale Offlineabnahme bestanden. Die vom Nutzer freigegebene gemeinsame Vertragsabnahme und spätere main-Integration bleiben getrennt; dieser Task aktiviert weder Kontoprüfung noch Handel.

## Umsetzung

Feste Datenbank `lab_paper_runtime` und vier eigene NOLOGIN-/Loginpaare `atl_paper_*`. Runtimekonfiguration akzeptiert ausschließlich bekannte Deployment-/DB-/Rollenbindungen und prüft Datenbank, SET-Rolle und Login bei jeder neuen Verbindung. Das Paperpaket startet ausschließlich DISABLED ohne Providerkomposition, Credentialleser oder finanzielle Seedfunktion. Vier Dienste laufen mit eigenen DBnetzen und privaten Kontrollpfaden; nur Execution/Worker besitzen ihren jeweiligen read-only Providermount und festen Egressproxy. Paper-API und Watchdog besitzen keinen Providermount. Kein Docker-Socket, Hostshell, DDL oder frei wählbares Datenbankziel.

Der normale Launcher provisioniert die leere Paperschemadatenbank und startet das zusätzliche Paket neben Fake-/Demolab. Bestehende Volumes bleiben erhalten. Feste HBA-Regeln sperren die vier Paperlogins gegen andere Datenbanken und Replikation vor den bestehenden Authentifizierungsregeln. Die Provisionierung akzeptiert nur die erwarteten Rollenattribute/Mitgliedschaften; zusätzliche Objekt-ACLs oder Eigentum werden vor Veränderungen abgewiesen. Zukünftige Workflowrechte benötigen eine neue explizit geprüfte versionierte Allowlist.

## Tatsächliche Evidenz

- `tests/integration/test_paper_provision.py`: bestanden in eigenem temporärem PostgreSQL18.6-Cluster auf internem UUID-Testnetz. Ausschließlich Quellverzeichnisse und Testskript read-only gemountet, keine private Repositoryablage. Wiederholung ohne Schlüsselrotation, leere Finanztabellen, keine DDL-/TEMP-/Fremdrollenrechte und gesperrte Fremdledger belegt. Zusätzliche Rollenattribute, Mitgliedschaften, Tabellen-/Spalten-/Schema-/Sequenz-/Funktionsrechte und Objekteigentum werden ohne automatische Reparatur abgewiesen.
- Unabhängiger Safetyreview: ein P2 zu erhaltenen direkten Objektrechten gefunden, behoben und nach erneutem tatsächlichem PostgreSQLlauf geschlossen. Kein weiterer offener Befund.
- Runtime-/Sicherheitsregressionen:46bestanden,6kontextabhängige Skips. Darunter16neue Identitäts-/Inaktivitätstests; Linux-/Deploymentfälle anschließend tatsächlich separat geprüft.
- Normaler `scripts/start-local-lab.ps1` samt Produktionsbuild und Containerbuild: bestanden mit vorhandenen Volumes. PostgreSQL, bisherige sechs Rollen und alle vier Paperrollen/zwei Proxies gesund; `/ready` tatsächlich200. Kein Aktivierungscode angefordert.
- `PAPER_DOCKER=1 DEMO_DOCKER=1 pytest tests/security/test_paper_deployment.py tests/security/test_demo_deployment.py`:10bestanden in91,56s. Tatsächliche UID-/Readonly-/Capability-/Netz-/Mountgrenzen, Proxyverweigerung gegen localhost und16HBA-Datenbankwechselverbote geprüft. Probes lesen keine privaten Providerdateien oder Umgebungswerte.
- Zwei vorhandene Finanzdatenbanken (Fake und Demo), jeweils neun Finanztabellen: Hashvergleich vor/nach normalem Launcherstart identisch. Hashartefakte enthalten keine Tabelleninhalte.
- Erneuter `start-local-lab.ps1 -SkipBuild -ProvisionOnly`: bestanden mit denselben Volumes, Fake/DISABLED. Keine Neuinitialisierung oder implizite Rotation.
- Ruff für alle betroffenen Pythondateien, PowerShellparser, Composekonfiguration für normale und bestehende Profile sowie Whitespaceprüfung bestanden.
- OpenSpec1/10, APW-02 abgeschlossen. Issue#33 per autorisiertem GH-CLI-Fallback geschlossen; vollständiger Zehn-Issue-Read-back und idempotente Wiederholung0Updates/10unverändert bestanden. Lokaler Issuebranch `codex/ptv-33-isolated-paper-runtime`; Push/main-Integration wartet auf die ausdrücklich erlaubte gemeinsame Abnahme.

## Grenzen

Kein Providerrequest oder Brokerauftrag durch Agenten. Paperdienste sind gesund und bewusst inaktiv. Kontoprüfung, sichere Generationen, Basisimport und Betriebsfreigabe folgen in APW-03/04/05. APW-01 bleibt bis zur vollständigen Runtime-/Vertragsgleichheit offen. Issue-/Gitabschluss wird nur anhand tatsächlichen Read-backs dokumentiert.
