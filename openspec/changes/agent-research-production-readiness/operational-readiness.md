# Operational Readiness Checklist

- [x] Installation aus versionierten Compose- und Installationsartefakten mit gepinnten Images, dokumentierter Hostplattform und getrennten Zustandsverzeichnissen belegt.
- [x] Provider-Setup erreicht ausschließlich den LLM-Schlüsselspeicher. Mounts, Prozesse, Hostdateirechte, interne Netze und tatsächlicher Egress geprüft.
- [x] Default-deny bei leerer Konfiguration und Neustart, Rotation/Widerruf unter Last sowie Restore ohne Rückkehr alter Freigaben geprüft.
- [x] Dashboard zeigt Serverstatus, Budget, Datenvorschau, Test- und Verarbeitungsentscheidungen. Zeitplan, Datenstand und blockierte Läufe sind nachvollziehbar.
- [x] Release-CI vollständig grün, einschließlich Browser, Safety, Recovery und Produktionskomposition gegen TLS-Emulator.
- [ ] Konto-, Provider-, Modell- und Datenrechte vor realem Betrieb separat belegt. Real-Smoke und Evaluation separat autorisiert und mit begrenztem Budget ausgeführt.
- [x] Kill, Reconciliation und null Brokerorders unter Researchlast belegt. Kontrollierter Rollback dokumentiert.

- [ ] Smoke-Nachweis enthält Requestbindung, Tarif und belegte Kostenreserve, Zeit- und Outputgrenze, technischen Verbindungsstatus und separat nachvollziehbare Abrechnung. Die Fachfreigabe enthält Mindestabdeckung, drei autorisierte Holdout-Läufe und dokumentierte Regressionsentscheidung.

- [ ] Tatsächliche KI-Reviewläufe haben belegte Trennung, Modell-, Provider- und Prompt-Provenienz. Referenzentscheidung und Einschränkungen sind dokumentiert. Offline-Doubles zählen für die technische Abnahme.
- [ ] Custodian-Identität, Dateirechte, Verschlüsselung, Backup und Restore sind betrieblich geprüft. Registrierungsanker, unabhängig gepinnter Ed25519-Schlüssel, Rotation, Widerruf und einmalige Öffnung nach Betreiberentscheidung sind nachgewiesen.
