# IEF-02 · Guided Onboarding

Ausgangs-HEAD `3351cababef20a9641974b28d81b6cc5c4687bfc`, Worktree `.worktrees/ief-02`, Branch `codex/ptv-108-ief-onboarding`.

Die Route `/onboarding` verwendet die vorhandene lokale Sitzungsaktivierung und die kanonischen GET-/PUT-Operationen für Betreiberpräferenzen. Strukturierte Antworten und die fünf Schrittzustände werden actorbezogen und versioniert gespeichert. Die Standardeinstellung verwendet `FAKE` als Darstellungskontext. Eine Demo muss ausdrücklich ausgewählt oder über den bestehenden synthetischen Demoablauf angelegt werden. Die Präferenzen erteilen keine finanziellen oder operativen Rechte.

Die lokale API ermittelt den Betreiber aus der Sitzung. Migration `0060_investment_preferences` trennt den Präferenzzustand und die Idempotenzbelege von Finanzdaten. Die Control-API-Rolle erhält lediglich Zugriff auf die vorgesehenen Funktionen. Die Funktion prüft Version und Idempotenzbeleg unter einer transaktionalen Sperre. Nach einem unklaren Ergebnis kann derselbe Auftrag mit gleichem Schlüssel wiederholt werden. Der belegte DEMO-Replay bleibt auch bei einem vorübergehend ausgefallenen Demodienst lesbar.

Nur die zwei Präferenzrouten wechseln vom Vertragsvorlauf in die Runtime. Der neue Endpunkt erfordert die Migration und vorhandene lokale Authentifizierung. Bei fehlender oder nicht erreichbarer Persistenz meldet die Oberfläche eine Störung. Die technische Umsetzung lokaler Portfolioanalysen gehört zu IEF-04. Den IEF-01-Prototyp für die formative Runde kann IEF-09 aus Commit `3eb6e97` reproduzieren. Die neue Onboarding-Teilstrecke bleibt für IEF-T-O05 in IEF-09 zugänglich.

## Rollback und Recovery

Die vorherige Anwendungsversion kann nach betrieblicher Prüfung ohne den Onboarding-Einstieg bereitgestellt werden. Die dedizierten Tabellen und Belege bleiben dabei erhalten. Die Migration verweigert einen automatischen Downgrade, der den gespeicherten Zustand löschen würde. Bei Sitzungsablauf sind lokale Eingaben zu verwerfen und der bestätigte Stand nach erneuter Anmeldung zu lesen. Bei Versionskonflikten ist zuerst der aktuelle Stand zu laden.

## Abnahme

Die menschliche Prüfung für IEF-T-O05 erfolgt in IEF-09. Automatisierte Ergebnisse werden separat dokumentiert.

## Lokale Prüfergebnisse

- Vollständige Contract-Suite: 2.485 bestanden. Nach dem Review-Fix erneuter gezielter Transport-/Staginglauf: 16 bestanden einschließlich exaktem DEMO-Replay bei ausgefallenem Demodienst. Kanonische OAS-/Beispielvalidierung bestanden.
- 22 Unit-Fälle zum Präferenzspeicher bestanden. Ruff-Prüfung der neuen API-, Store-, Migrations- und Testdateien bestanden.
- Frisches isoliertes PostgreSQL 18.6 mit regulär gebautem Testimage: 1 umfassender Integrationsfall in 5,80 Sekunden bestanden. Abgedeckt sind Versionierung, Parallelität, actorbezogener Replay, Reset und Datenbankrechte. Temporäre Ressourcen wurden entfernt.
- Angular-Produktionsbuild bestanden. 18 Chromium-Prüfungen zu Onboarding, Prototyp, Hosting und Barrierefreiheit bestanden. Der Browser verwendet einen synthetischen HTTP-Testserver. Der erste Lauf identifizierte einen veralteten Aktivierungspfad in der Testassertion. Nach Anpassung an den kanonischen Pfad bestanden alle 18 Tests. Die vorhandene Supervisor-CSS-Budgetwarnung bleibt bestehen.
- Unabhängiger Backend-Review fand einen P2 beim Replay während einer Demo-Störung. Die Prüfung des persistierten Belegs erfolgt jetzt vor der Demo-Abfrage. HTTP- und SQL-Nachtests bestätigen den korrigierten Pfad.
- JUnit-SHA256 des finalen PostgreSQL-Falls: `43bf2b947c32c181547ff853a434bac5dc5b7882ad023f9a7bf71d5a506c4928`.
