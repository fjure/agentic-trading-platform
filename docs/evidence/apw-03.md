# APW-03: Dauerhafte Kontoprüfung und Schlüsselgeneration

Stand21.10.2026: lokale Implementierung und verpflichtende Abnahme abgeschlossen. REQ-APW-002/003/005. Noch keine vollständige Handelsfreigabe oder tatsächliche Alpaca-Abnahme.

## Implementierter Weg

Die authentifizierte normale Control-API übermittelt ausschließlich Kommandometadaten an den privaten Paper-API-Dienst. Dessen PostgreSQL-Funktionen speichern Vorgang, Idempotenz, Generation, Besitzer, Lease, Fence und Auditereignisse dauerhaft. Die Handelsrollen besitzen keine direkten Tabellenrechte auf diesen Zustand. Öffentliche Status-/Vorgangsprojektionen enthalten weder private Konto-UUID noch Credentials oder Providerantworttexte.

Execution beansprucht eine explizite Kontoprüfung und führt ausschließlich begrenzte GET-Anfragen an die festen Paperkonto- und IEX-Quote-Endpunkte aus. Vorher gibt es keinen Credentialleser oder Providerrequest. UUID, USD, Kontostatus, Sperren und Feedzugriff werden geprüft. Das Ergebnis gelangt erst nach erneuter Prüfung von Besitzer, Fence, Generation und Kontroll-Epoch in den dauerhaften Zustand. PAUSE/STOP/KILL schließen ihre Latch vor Annahmebestätigung und benötigen keine Providerverbindung.

Beim Speichern neuer Schlüssel werden zuerst Einstieg und bisherige Freigaben dauerhaft invalidiert. Der separate Einrichtungsdienst veröffentlicht eine unveränderliche Pendingdatei; aktive Recoverydateien bleiben erhalten. Nur Execution liest eine explizit beanspruchte Generation. Promotion verlangt dieselbe gepinnte Kontoidentität und verweigert ungeklärte Brokerverpflichtungen. Ein wiederholter Speichervorgang bestätigt die zuerst veröffentlichte, sicher geprüfte Generation ohne deren Inhalte zu lesen, zu vergleichen oder zu überschreiben. Speichern, Prüfung und Neustart aktivieren keinen Handel.

Migration0013 erhält getrennte öffentliche SQL-Funktionen für API und Execution sowie ein explizites versioniertes Provisionierungsmanifest. Zusätzliche oder fehlende Rechte und Eigentumsdrift werden abgewiesen. Es gibt weiterhin keine finanzielle Paperbasis und keinen freigegebenen Submitpfad.

## Bisherige Prüfevidenz

- Vollständige lokale Vertrags-/Domain-/Verifikations-/Setupmatrix:694bestanden,10kontextabhängige Skips,7Subtests,6,98s. POSIX- und PostgreSQLprüfungen werden separat ausgeführt; Skips gelten nicht als deren Ersatz.
- Tatsächliche PostgreSQL-Kommandoprüfungen und ASGI-Transportprüfung:16bestanden; konkurrierende Idempotenz, Leaseablauf, Besitzer-/Fenceprüfung, Rotation/Kill während Prüfung, ungültige Belege, sichere Zählergrenzen und private Projektionsrechte geprüft.
- Separater Provisionierungslauf in temporärem PostgreSQL18.6-Cluster: Migration0012→0013 mit exaktem Funktionsrechte-Manifest bestanden.
- Durchgehender Test `tests/integration/test_paper_chain.py`: bestanden mit tatsächlicher normaler API, privater Paper-API, SQL-Funktionen, privater Setup-ASGI-Anwendung, `FixedSetupClient`, POSIX-0600-Dateispeicher, Generationenleser, Runtime und Verifier. Nur Provider-HTTP ist synthetisch. Promotion/Kontobindung, dauerhafte Idempotenz, falsches Konto,401/503, Rotation/Kill während GET und Wiederholung nach verlorener Speicherantwort geprüft. Keine Finanzbasis oder Orders erzeugt; öffentliche Antworten und dauerhafte Kommando-/Receipt-/Auditprojektionen ohne Canaries/private UUID. Gemeinsame Wiederholung von Kommando- und Kettentest:2bestanden/82,52s.
- Zwölf POSIX-Datei-/Generationenprüfungen im Offline-Linuxcontainer bestanden, ohne Repositorywurzel oder private Ablagen zu mounten. Abbruch zwischen Hardlinkveröffentlichung und temporärem Unlink bleibt absichtlich gesperrt; neue Generation ermöglicht Recovery ohne Änderung alter Dateien.
- Tatsächlicher normaler Launcher mit bestehenden Volumes: Installation, Produktions-/Containerbuild, Provisionierung und sämtliche Dienste gesund. Paperrevision0013, ZustandPAUSED, aktive Generation0, Credentialzähler0 und jeweils0Konten/Orders/Kommandos. Kein Aktivierungscode angefordert. Zwei bestehende Fake-/Demo-Finanzdatenbanken mit je neun Tabellen haben unveränderte Hashes.
- Zehn tatsächliche Paper-/Demo-Deploymentprüfungen bestanden/92,52s. Health am fest konfigurierten Host `127.0.0.1:8000` liefert200; anonyme Paperstatusabfrage401 `AUTHENTICATION_REQUIRED`; abweichender Host wird400 abgewiesen. Kein privater Providerdateizugriff durch diese Prüfungen.
- Tatsächliche SQL-Rotation mit vollständig verknüpften synthetischen Risk-/Intent-/Brokerorderfixtures: bekannte ACK-Verpflichtung erlaubt dieselbe verifizierte Kontoidentität; UNKNOWN, SUBMITTING und HELD_UNKNOWN blockieren die Pendingpromotion. Aktive Generation, Kontoidentität und Reservation bleiben erhalten. Wiederhergestellter bekannter Status erlaubt Promotion; fremde Konto-UUID bleibt gesperrt. Eigener PostgreSQLlauf41,24s bestanden.
- Unabhängiger Review: NULL-Fencing, Generationenrennen, ungültige Verifikationsbelege, Zählergrenzen und zwei Orchestrierungsbefunde behoben und geschlossen. Unabhängige Runtime-/Clientregression35bestanden.
- Issues#32/#33 geschlossen; vollständiger Zehn-Issue-Read-back mit anschließend0Updates/10unverändert. APW-03 bleibt offen.

## Abschluss und Grenzen

Abschließender Kommando-SQL-Test mit gleichzeitig konkurrierenden Besitzern bestanden/41,26s: genau eine Lease, falscher Besitzer, NULL und veralteter Fence abgewiesen. Unabhängiger abschließender Review ohne offene Codebefunde; neue SQL-Testabdeckung geschlossen. Ruff und Whitespaceprüfung bestanden. Vertragsvalidator39Operationen/18Schemas, striktes OpenSpec und redigierter Sourcescan555Dateien/0Befunde bestanden. Die Betriebsanleitung beschreibt Speicher-/Prüf-/Freigabetrennung, unveränderliche Generationen und den begrenzten Hardlink-Crashfall.

Task APW-03 abgeschlossen, OpenSpec3/10. Issue#34 geschlossen und sämtliche zehn APW-Issues vollständig zurückgelesen; idempotente Wiederholung0Updates/10unverändert. Eigener lokaler Branch `codex/ptv-34-paper-verification`. Basisinitialisierung, Tagesverlustmodell und Handels-/Browserabnahme bleiben nachfolgende Aufgaben. APW-04 benötigt die Entscheidung aus `risk-basis-decision.md`; dieser Vorschlag ist noch keine freigegebene Anforderung.

Alle Credentials und Providerantworten in Tests sind synthetisch. Keine echten Schlüssel gelesen, keine echten Provideranfragen oder Brokerorders ausgelöst.
