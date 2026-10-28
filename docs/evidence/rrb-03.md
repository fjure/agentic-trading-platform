# RRB-03 / #71: additive Snapshot- und Reportpersistenz

Stand 28.10.2026. Apply freigegeben; Basis main `a8f3ecccbc1003eb4eb9725e82f300a195530d9a`, Issuebranch `codex/ptv-71-retrospective-storage`. Aktuelle Test- und Reviewnachweise sind unten erfasst; Issue-/Gitintegration wird im Abschlussdurchlauf gesondert geprüft.

## Implementiertes Verhalten

REQ-RRB-002/004/005/007/012, T-RRB-003/004/008/009/012/020: additive Migration 0033 ohne Veränderung alter Migrationen oder physischer Legacyzeilen. Neue Assessment-/Dataset-/Report-/Receipt-/Audit-/Revisionsbindungstabellen im backtest-Schema. Unveränderliche Bindungen speichern konkrete Identitäten und Fingerprints; Fremdschlüssel erhalten gewählte Bar-, Kalender-, Qualitäts- und Evidenzrevisionen. Replay prüft exakte Referenzen, niemals spätere latest-Auswahl.

Vorschauaufnahme bindet DB-UTC, Commitcap, Build und Truststand sowie eine unveränderliche lokale Lizenzreceipt. Das feste schmale View projiziert ausschließlich Alpaca-Providerkennung, normierten Lizenzstatus und vorhandene Metadaten; kein breiter Zugriff auf source_status oder Finanz-/Credentialtabellen. UNKNOWN bleibt unbekannt, CONFIRMED ist eine Deklaration, BLOCKED wird DENIED. Aktueller Portausfall oder Denied verhindern positive Publikation.

Strict-Runs, Strict-Readiness und retrospektive Jobs teilen Queue100, oldest-job-Admission, aktive Workerbarriere und vorhandene Lease-/Versuchsgrenzen. Leaseerwerb bleibt reine Buchführung: ein inzwischen verweigerter Lauf kann eine wahrheitsgemäße BLOCKED-Veröffentlichung abschließen und die Queue freigeben. Die spätere RRB-06-Ausführung muss aktuelle Rechte/Trust vor CPUarbeit prüfen. Transaktionslokaler Policykontext und Fences schützen neue Varianten auch gegen das unveränderte alte ungefilterte Claim-SQL. Alte Strictclaims brauchen keinen neuen Marker.

Publikation bindet vollständigen kanonischen Report, Manifest und Ergebnis atomar. Ein interner unveränderlicher Envelope enthält Übersicht, Befunde und Revisionsreferenzen; der DBfingerprint umfasst alle drei Teile. Getrennte JSONB-Spalten erlauben spätere Pagination ohne Rekonstruktion aus latest-Daten. Kanonische Itemvalidierung, eindeutige Identitäten, exakte nullsichere Zähler und das gemeinsame 12MiB-Limit werden vor und bei der Speicherung geprüft. Schemafeld overallStatus steuert die Zulässigkeit; ein früher irrtümlich benutztes eligibility wird nicht als alternatives Reportformat akzeptiert. SQL blockiert direkte terminale Inserts, Modusdowngrade zu Strict, gefälschte Claims, ungeleaste Zustandsmanipulation und nachträgliche Verlängerung/Zurücksetzung von Lease-/Start-/Versuchsbudgets. Historische Reports und Receipts bleiben append-only.

## Laufzeitverpackung

Die bereits gepinnte jsonschema-Version 4.26.0 ist nun Produktionsabhängigkeit; Artefaktversionen und Hashes bleiben unverändert. uv lock --check --offline --python .venv/Scripts/python.exe bestätigt 72 Pakete. Das Runtimeimage kopiert ausschließlich die selbstständige kanonische backtesting.schema.json; schmale dockerignore-Ausnahmen erlauben deren Pfad. Ihr Inhalt ist verpflichtender Teil des tatsächlichen Build-Fingerprints, fehlt er, scheitert der Versionsnachweis geschlossen. Zwei bestehende Versionierungstests decken Schemaänderung und Verlust ab.

## Lokale Abnahmeinfrastruktur

Der bestehende Entwicklungscontainer hatte PostgreSQL selbst als PID 1. Technische Logs zeigten wiederholt einen unbekannten Kindprozess mit Exitcode 2 und unmittelbar folgende Reinitialisierung. Der [offizielle PostgreSQL-18-Quellcode](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/postmaster/postmaster.c) behandelt unbekannte Kindprozesse mit anderen Exitcodes als 0/1 als Crash; [Docker Init](https://docs.docker.com/reference/compose-file/services/#init) übernimmt Signalweitergabe und Reaping. Ausschließlich postgres.init wurde aktiviert; Image, Rechte, Secrets, Ports und Produktvolumes sind unverändert.

Da das bestehende Volume noch in Recovery war, erfolgt die aktuelle Abnahme auf einem separaten leeren UUID-Testvolume unter temporärem, nicht versioniertem Composeoverride. Ein struktureller Vergleich bestätigt, dass ausschließlich die Datenquelle abweicht. Das Originalvolume agentic-trading-lab-dev_postgres_data bleibt erhalten. Nach Abschluss der gesamten Testkette wird die Basiskonfiguration wiederhergestellt; keine Reinitialisierung, Volumeprune oder Änderung an Recoverydateien.

Tatsächlicher Runtime-Negativtest: PID 1 ist docker-init; ein harmloser verwaister Shellprozess endet mit Code 2. PostgreSQL-Startzeit bleibt unverändert und der Container bleibt healthy. Kein PostgreSQL-Prozess wurde dabei beendet. Unabhängiger Sicherheitsreview der einzeiligen Composeänderung bestanden.

## Abschließende Prüfevidenz

- Aktuelle PostgreSQL-/ACL-/Backup-/Restorematrix nach sämtlichen Guard- und Envelopekorrekturen: **2 passed, 222.30s**.
- Bestehende PostgreSQL-/Recoveryregressionen: **6 passed, 278.52s**, seriell nach der neuen Matrix: Backtestjobs, Readinessjobs-Recovery, Readinessstorage, Backtestdataset, Backtestrollen und Readinessrollen.
- Die neue Matrix prüft echte Commitrennen, Bindungsidentitäten aller vier Quellarten, Fremdrollen, alte und manipulierte Claims, Mode-Downgrade, Lease-/Budgetmanipulation, Lizenzportausfall/Denied, nichtleere Befunde/Referenzen, vollständige Fingerprints, exakte Zähler/Items, Unveränderlichkeit und atomaren Rollback. Tabelleninhalte und Rollen-ACL werden nach Backup/Restore verglichen.
- 290 lokale Vertrags-/Backtest-Storageprüfungen bestanden. Hauptagent zusätzlich 48 Reliability-/Strict-Hash-/Buildversionstests bestanden; vier wörtliche Strict-Hashorakel unverändert.
- Ruff auf sämtlichen acht neuen/angepassten Pythonpfaden bestanden; unabhängige Reviews der SQL-/Port-/Envelope-/Paketierungs-/Initänderungen ohne offene P1/P2.
- Finales Produktionsimage mit uv sync --frozen --no-dev erfolgreich gebaut, Manifest cef9ce5ae34168c0ffddd780cbc04282cd88a4d1b76118dcb8b1e21b3c99b58e. Anschließender Smoke ohne Netzwerk, Mounts oder Credentials bestanden: alle drei kanonischen Report-/Itemvalidatoren importierbar, Schema vorhanden, source_version gültig, deaktivierter Worker öffnet keine SQLverbindung.

Frühere Läufe mit Docker-/Recoveryfehlern oder inkonsistenten Testfixtures werden nicht als aktuelle Erfolge gezählt. Die oben genannten finalen PGdurchläufe endeten beide mit Exitcode 0, auf demselben gesunden isolierten Cluster, ohne Quelländerungen zwischen den Läufen. Der separate PID-1-Negativtest belegt stabile PostgreSQL-Startzeit beim verwaisten Exitcode 2.

Worker/API, Browser, Lastisolation und vollständige Handelsledger-/Pause-/Reconciliation-Abnahme folgen RRB-06..08. Keine neuen Provider-/Broker-/Jevaufrufe, Credentials, Finanzrechte, Paper-/LIVE-Aktivierung oder Archivierung. Die separate aktuelle Ausführungsprüfung darf in RRB-06 einen Lauf blockieren, ohne historische Lizenzreceipt oder Snapshotbefunde umzuschreiben.
