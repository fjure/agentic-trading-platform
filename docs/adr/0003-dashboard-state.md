# ADR 0003: Angular Material und begrenzter SignalStore

Status: angenommen und in PTV-20 umgesetzt, 2026-10-16; Version/Primärquellen in `docs/development/technology-baseline.md`.

Angular Material erfüllt Tabellen, Formulare, Dialoge, Navigation, Status und responsive Layouts als kostenfreie Open-Source-Komponenten unter MIT. PrimeNG ist nicht zulässig. Native Einzelkomponenten wären mehr Accessibility-/Interaktionsarbeit; zusätzliche UI-Library wird vermieden.

NgRx SignalStore übernimmt ausschließlich geteilten Snapshot, Modus, Portfolio, Verbindungszustand, Cursor und Filter. Alternative reine Komponentenservices würde Reconnect-/Snapshotzustand mehrfach verwalten; klassischer globaler NgRx Store wäre für diesen kleinen Scope unnötig umfangreich. Formular-/Dialogzustand bleibt lokal. Backend bleibt Source of Truth, der Store enthält weder Risiko-/Brokerlogik noch Secrets und persistiert keine DTOs im Browser. Events/Selektoren und Resetregeln stehen in D-08. Ein Store wird erst nach verifizierter Versionskompatibilität installiert.

PTV-20 pinnt `@ngrx/signals`22.0.1; dessen veröffentlichte Peers verlangen Angular`^22.0.0` und RxJS`^6.5.3 || ^7.4.0`. Angular/Material/Forms22.2.2 und RxJS7.8.2 erfüllen dies. Geprüft am 2026-10-16 anhand des offiziellen npm-Paketregisters und der [SignalStore-Dokumentation](https://ngrx.io/guide/signals/signal-store), ergänzend über Context7. Die vorhandene Node22.23.3-Toolchain bleibt maßgeblich.

Der gemeinsame Store wird von Statusleiste, Übersicht, Historientabellen und Kontrollformularen gelesen. REST benutzt ausschließlich den unveränderten generierten Client, mit leerem BASE_PATH für Same-Origin. SSE verwendet einen getrennten Fetch-Reader, damit auch Kommentarheartbeats beobachtbar sind. Formulare und Dialoge bleiben Komponentenstatus; der einmalige Aktivierungscode wird nach Übergabe sofort aus dem Eingabefeld entfernt und gehört weder zum Store noch zur Browserpersistenz. Keine Devtools-/Storage-Erweiterung wird aktiviert.
