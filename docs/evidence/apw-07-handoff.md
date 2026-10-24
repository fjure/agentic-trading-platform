# APW-07: geprüfter Übergabestand24.10.2026

APW-07 ist vollständig lokal abgenommen; OpenSpec7/10. Finale tatsächliche öffentliche HTTPkette23.10.18:45UTC bestanden1Test/748,54s einschließlich Wartezeit. Separater unveränderter echter90Sekunden-Reclaim ebenfalls bestanden. Ausführliche Evidenz und Safetycheckliste: [APW-07](apw-07.md).

1614lokale Tests,12bestehende tatsächliche SQLregressionsfälle,3neue Head18-Provenienz-/Rollen-/Konkurrenztests und18Deploymenttests bestanden. Normaler Launcher18 gesund/inaktiv,12Laufzeitquellen identisch,0Schlüsselgenerationen/Konten/Orders/Kommandos.2bestehende Fake-/Demo-Datenbanken/9Finanztabellen unverändert. Migration17 bleibt angewendet, letzte Härtung nur vorwärts über18. Keine echten Schlüssel, Providerrequests oder Orders. Finale unabhängige Reviews ohne P1/P2.

Branch `codex/ptv-38-paper-manual-limit` ab remoteverifiziertem main d28d8d20c31413435cd11ead47f2e779a890d5e6. Taskabschluss und Issue38-Sync/Read-back bestätigt (Wiederholung0Änderungen/10unverändert). Branch16852af4855c45fb4e118ce78e8555c0184a128e regulär nach main78896a809b888c3d00b568e65235e18ea581d8fe integriert, Remotezuordnung und identischer Baum geprüft; CI40040855953 erfolgreich. APW-05 bereits Branch2f2ada6/main d28d8d2/CI39934500495 erfolgreich.

Nächster Umfang: APW-08/Issue39, eigene dauerhafte Cancelqueue ohne Starvation durch RUNNING-Manualkommandos, Audit-before-DELETE, Pending/Unknownauflösung und belegte Reservationsfreigabe; nächste Migration19. Anschließend APW-09 Materialoberfläche und APW-10 gemeinsame Browser-/Offlineabnahme. Keine Archivierung oder Spec-Synchronisierung beauftragt.

Dateizuständigkeiten: Hauptagent Runtime/Integration/Dokumentation, SQLagent Migrationen/Manifest/Ledgerloader/human_sql, Domainagent Humanvalidator/Risk/Gates, Contractsagent ausschließlich human_orders-HTTPfixture. Keine Gitmutationen durch Subagenten.

Gehalten unverändert außerhalb dieses Pakets: Nutzer `apps/dashboard/angular.json`; APW-10 `apps/dashboard/package.json`, `package-lock.json`, `docs/evidence/apw-10.md`. Nicht committen, zurücksetzen oder stashen.
