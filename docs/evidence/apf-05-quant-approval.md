# APF-05 · Atomare Quant-Freigabe

Die Arbeit baut auf `73fd7f5d5f079d8f0fd1c7a6fb8e65cdb1a1d317` auf. Die unveränderliche Herkunft eines Agentenentwurfs entsteht mit seiner Definition. Vorgänger und abgelöste Batches vererben diese Bindung. Der manuelle Startpfad meldet für gebundene Entwürfe `APPROVAL_REQUIRED`. Unabhängige manuelle Entwürfe verwenden weiterhin die bestehende Quant-Admission.

Der private Startpfad prüft die aktuelle Betreiberautorisierung auch beim Replay. Die SQL-Transaktion bindet Run, Ideen- und Hypothesenfassung, Entwurf, Validierung, Policy, Quellenstand, Ressourcen und Ablauf. Eine Approximation benötigt eine gesonderte Entscheidung über den gespeicherten Vorschlag für genau diesen Entwurf. Quotenprüfung, kanonischer Batch, Exposure-Register, Freigabeverbrauch und Run-Übergang werden gemeinsam festgeschrieben. Ein identischer Replay liefert dieselbe Batch-ID.

Die Research-Rolle und der Backtest-Worker können den privaten Startport nicht ausführen. Der Worker erhält über einen begrenzten Leseport das genehmigte Ressourcenbudget. Er prüft nach der Datenbindung Quellenrechte und effektive Simulationsgrenzen. PostgreSQL deckelt zusätzlich Slots, Simulationsschritte und Publikationsbytes für freigegebene Agenten-Batches.

## Abnahme

- 94 fokussierte Approval-, Worker-, Quant-Service-, Grid- und Protokolltests bestanden. Ruff und `git diff --check` waren erfolgreich.
- Drei bestehende PostgreSQL-Tests für Quant-Speicherung und Worker bestanden. Die separate Freigabeprüfung bestand in 71,37 Sekunden einschließlich Backup und Restore. Sie prüft mit synthetischen Daten die kanonische Admission, Rollen, vererbte Entwurfsbindung, Quote, geänderte Policy und Quellenrechte, Teilrollback, parallelen Replay sowie den exakten Approximationsvorschlag.
- Striktes OpenSpec, Schema- und Beispielprüfung, semantischer Vertragsvergleich und 2.429 Vertragstests bestanden. Der unveränderte Vertrag erzeugte die bekannte `allOf`-Warnung. Der Credential-Quellscan meldete null Befunde.

Die Abnahme nutzt einen isolierten PostgreSQL-Harness. Öffentliche Sitzungseinbindung und produktive Paper-Provisionierung sind gesonderte Schritte. Der neue Admission-Port erhält im regulären Betrieb seine Berechtigungen über die dafür vorgesehene private Rolle.
