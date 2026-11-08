# Additiver HTTP-Vertrag

Kanonisches Ziel für Apply ist `contracts/http/control-api.openapi.yaml` zusammen mit `contracts/schemas/` und `contracts/examples/`. Die Tabelle beschreibt geplante Operationen und DTO-Grenzen. Versions- und Rückwärtskompatibilität werden vor Transportimplementierung mit Repo-Validatoren geprüft.

Basis `/api/v1/research/agent`. Alle Listen mit begrenztem cursor-basiertem Zugriff, sortiert nach stabilem Schlüssel. Mutation mit Idempotency-Key, aktueller Session, Origin und CSRF. Versionierte Änderungen enthalten erwartete Version. Gleicher Schlüssel und Payload liefert gespeicherte Antwort, abweichende Payload 409. Unbekannte oder nicht sichtbare IDs 404, fehlende Session 401, verbotene Aktion 403, stale Bindung 409, ungültige Felder 422, ausgeschöpftes Kontingent 429, fehlende Abhängigkeit 503. Fehler verwenden bestehendes Problemformat mit geschlossenem Reason Code und Korrelations-ID. Zeitstempel UTC, Decimal als verlustfreie Strings.

| Methode und Ressource | Bindung und Antwort | Requirements |
| --- | --- | --- |
| GET `/ideas`, GET `/ideas/{id}`, POST `/ideas`, POST `/ideas/{id}/revisions` | Liste, immutable IdeaVersion, Origin, Testability, EvidenceRef, Reviewentscheidung und erwartete Version | Q01, Q03, Q05, D03, C01 |
| POST `/dialogues`, GET `/dialogues/{id}`, POST `/dialogues/{id}/turns`, POST `/dialogues/{id}/decisions` | Dialog-ID, Turn-ID, optional Run-ID, Hypothesenrevision, Annahmen, explizite Approximation; angenommene asynchrone Turns 202 | Q02–Q04, C01 |
| GET `/registry`, GET `/runs`, GET `/runs/{id}`, GET `/runs/{id}/events`, POST `/runs/{id}/cancel` | AgentVersion, redigierter RunStatus, paginierte Tool-/Auditprojektion, Usage und Cancel-Empfang | R01–R04, R06, C03 |
| GET/PUT `/schedule`, POST `/schedule/commands` | ScheduleVersion, TZ/Uhrzeit, nächste UTC-Zeit, Caps, Zustand und erwartete Version; typisierte Pause, Resume und explizite Neubewertung | D01–D04, C01 |
| GET `/approvals`, GET `/approvals/{id}`, POST `/approvals/{id}/decision` | Angeforderte Bindung, Approve+Start oder Reject. Atomare Quant-Admission liefert Batch-ID oder strukturierten Ablehnungsgrund | R05, C03 |
| GET `/reports`, GET `/reports/{id}` | ReportVersion, gebundene Lab-IDs, getrennte Claim-Typen und aktuelle Rechtehinweise | Q06, C02, C04 |
| GET `/providers`, POST `/providers/{provider}/probe` | Capabilities, Generation, safe Status, quotierte Probe, kein Provider-Rohtext | P01–P04, R06, C03 |

BYOK-Write-only-Endpunkte werden getrennt unter dem lokalen Setup-Pfad für Set/Rotate/Revoke geplant. Nur typisierter Status verlässt den Setup-Pfad. Aktivierung, Speicherung und Egress bleiben bis zur gesonderten Autorisierung deaktiviert. Provider-IDs geschlossen. Kein Key im generierten Zustandsmodell oder in Protokollen. Ausgaben ohne Token-/Kostenwert tragen einen expliziten unbekannten Zustand.

Materialnavigation: Research → Discovery, Companion und Quant Lab. Guided zeigt Klärung → Entwurf → Freigabe erforderlich → In Warteschlange/Läuft → Bericht oder begründete Blockierung. Die Schaltfläche Experiment freigeben und starten führt genau eine atomare Aktion aus. Reload und Moduswechsel erhalten die Bindung. Einsteiger sehen eine Frage je Klärungsschritt, die benannte Abweichung vor Freigabe und danach Bericht mit separaten Forschungsrollen. Experten öffnen dieselben gebundenen Details in Advanced. Supervisor → Agents, Runs, Approvals und Providerstatus. Bestehende Quant Lab Screens und Navigationslinks werden wiederverwendet. Polling ist begrenzt, an Session und Lifecycle gebunden und verwirft verspätete Ergebnisse nach Logout. Bestehendes SSE wird nicht um APF-Payloads erweitert. Deep Links, Reload, Tastatur, Fokus und verständliche Statusansagen gehören zur Abnahme.

Contract-first-Abnahme: OpenSpec vor OAS, geschlossene Schemata und positive/negative Beispiele, Breaking-Diff gegen main, pinned Generator zweimal ausführen und Drift prüfen, FastAPI↔BFF↔Browser-Tests einschließlich 401/403/409/422/429/503. Die kanonische Vertragsdatei und der Client sind Apply-Aufgaben.
