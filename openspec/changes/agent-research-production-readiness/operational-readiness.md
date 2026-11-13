# Operational Readiness Checklist

- [ ] Installation aus versionierten Compose- und Installationsartefakten mit gepinnten Images, dokumentierter Hostplattform und getrennten Zustandsverzeichnissen belegt.
- [ ] Provider-Setup erreicht ausschließlich den LLM-Schlüsselspeicher. Mounts, Prozesse, Hostdateirechte, interne Netze und tatsächlicher Egress geprüft.
- [ ] Default-deny bei leerer Konfiguration und Neustart, Rotation/Widerruf unter Last sowie Restore ohne Rückkehr alter Freigaben geprüft.
- [ ] Dashboard zeigt Serverstatus, Budget, Datenvorschau, Test- und Verarbeitungsentscheidungen. Zeitplan, Datenstand und blockierte Läufe sind nachvollziehbar.
- [ ] Release-CI vollständig grün, einschließlich Browser, Safety, Recovery und Produktionskomposition gegen TLS-Emulator.
- [ ] Konto-, Provider-, Modell- und Datenrechte vor realem Betrieb separat belegt. Real-Smoke und Evaluation separat autorisiert und mit begrenztem Budget ausgeführt.
- [ ] Kill, Reconciliation und null Brokerorders unter Researchlast belegt. Kontrollierter Rollback dokumentiert.

- [ ] Smoke-Nachweis enthält Requestbindung, Tarif und belegte Kostenreserve, Zeit- und Outputgrenze, technischen Verbindungsstatus und separat nachvollziehbare Abrechnung. Die Fachfreigabe enthält Mindestabdeckung, drei autorisierte Holdout-Läufe und dokumentierte Regressionsentscheidung.
