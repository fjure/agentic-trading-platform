# ARP-04 · Lokaler Prüfstand

Basis `3737c6f7b896594608b1557ac645767f279a502b`. GitHub bestätigte #97 und #98 als abgeschlossene Voraussetzungen von #99.

- `pytest tests/integration/test_arp_model_attempts.py -q --tb=short` bestand auf temporärem PostgreSQL. Die Prüfung belegt konkurrierende Reservierungen mit zwei Testleases, UTC-Periodenwechsel, weiter gebundene unbekannte Kosten, spätere Überschreitungsabrechnung und die Sperre eines alten Credentialstands. Der reguläre Scheduler bleibt bei einer gleichzeitigen Lease.
- 130 fokussierte Vertrags-, Route- und Rollenprüfungen bestanden. Der separate Regressionslauf bestätigte 23 bestehende Research-, Recovery- und Rollentests. Der darin noch fehlerhafte neue Ledger-Gastaufruf wurde korrigiert und separat erfolgreich wiederholt.
- OpenSpec Strict Validation, Ruff sowie die zweifache Angular-Clientgenerierung und TypeScript-Kompilierung bestanden.

- Der optionale Authority-Overlay verbindet Setup und Supervisor über ein dediziertes internes Netz. Der signierte Metadatenkanal blockiert die alte Credentialautorität vor der lokalen Mutation und attestiert anschließend die neue Version. Der Kanal transportiert Providerkennung, Generation, Version und Metadatenhash. 45 gezielte Setup-, Kanal- und Vertragsprüfungen sowie die Compose-Konfigurationsprüfung bestanden.

- Die Supervisor-Authority liest die aktuelle Tarifevidenz aus PostgreSQL und prüft den exakten Bindungshash, die Tarifrevision und die konservative Kostenobergrenze. Der PostgreSQL-Test belegt die Sperre bei fehlender sowie nachträglich gelöschter Evidenz.
- Die signierte Admission trennt Dialog und Discovery. Discovery bindet den einmaligen Grant an den Attempt und die exakte Projektion und prüft die eigene Zweckfreigabe vor der Publikation erneut. Elf fokussierte Discovery-, Gateway-, TLS- und Setup-Authority-Tests bestanden.

- Die optionalen Supervisor- und Gateway-Einstiegspunkte laden denselben versionsgebundenen Installationsdeskriptor. Der Supervisor verlangt ein exakt passendes aktives Dialogprofil aus PostgreSQL. Der Gateway bindet Capability, Credentialgeneration, Metadatenhash und persistentes Journal. Die kombinierte Compose-Konfiguration besteht die statische Prüfung. Zehn PostgreSQL-, Gateway-, Discovery- und Setup-Authority-Tests bestanden nach der Verdrahtung. Die PostgreSQL-Prüfung bestätigt auch, dass der Einstieg ein STAGED-Profil nicht als aktives Dialogprofil laden kann.

- Der erweiterte PostgreSQL-Gasttest prüft den Setup-Pfad mit echtem POSIX-Credential-Speicher, signierter HTTP-Authority und der migrierten Datenbank. Speichern und Widerruf bestanden. Der abschließende Datenbankzustand war Version 3, Generation 1, ohne Credential-Digest und mit aktiver Sperre.

- `pytest tests/recovery/test_discovery_runtime.py -q --tb=short` bestand auf temporärem PostgreSQL. Der Test aktivierte den Discovery-Zweck über den persistierten Profillebenszyklus mit einer synthetischen Settlement-Fixture. Die signierte Gateway- und Admission-Kette nahm genau eine gebundene Discovery-Projektion an und sandte sie über echtes Loopback-CONNECT und verifiziertes TLS an eine synthetische Providergegenstelle. Der Supervisor veröffentlichte die Idee. Weitere Szenarien prüften Neustart, Deduplikation, Datenrechtsentzug und unbekannte Usage über den bestehenden Offline-Port.

- Der Supervisor-Einstieg erzeugt bei explizitem Discovery-Deskriptor die Konfiguration und den signierten Gateway-Port aus PostgreSQL und der versionsgebundenen Quelldefinition. Die Quelldefinition bindet Quell-ID und Fingerprint sowie die freigegebenen Zitate an den Profilhash. Der Discovery-Test lädt den Deskriptor über den Produktionsloader und prüft den gesamten Weg bis zur Veröffentlichung. Die kombinierte Compose-Konfiguration wurde statisch validiert.

Offen bleiben der integrierte Compose-Start mit aktivem Testprofil und die Abnahme des exakten Abschlusscommits. Externer Providerdispatch bleibt im Standardprofil gesperrt. #99 bleibt offen.
