# IEF-01 Sicherheitsgrenzen

Prüfumfang: additive Verträge, generierter Client, Router/Shell und ausdrücklich synthetischer früher Prototyp auf Basis `82139cd2f4a87d88b92612b5fae55d8caa367d4a`. Die neue Preview-, Analyse-, Processing- und Guided-HTTP-Laufzeit wird hier nicht implementiert. Vertragsfelder oder simulierte Klicks sind kein Nachweis ihrer späteren serverseitigen Durchsetzung.

## Checkliste nach trading-safety-review

| Invariante | Konkreter Prüfgegenstand | Grenze der Aussage |
| --- | --- | --- |
| Agenten ohne Broker-Submit | Bestehende Isolationstests in `tests/security/test_execution_isolation.py`; neue UI navigiert und simuliert ausdrücklich | Kein neuer Runtime-/Brokerpfad in IEF-01; spätere J5-Harnesskette bleibt IEF-07/09 |
| Deterministischer Riskentscheid und Executiongates | Paper-Vorschauvertrag unterscheidet unverbindliche Vorprüfung von Human-Anforderung und tatsächlichem Fill | Finale Revalidierung, Reservation und Submitfence des neuen Guided Commit erst in IEF-07 beweisbar |
| LIVE unerreichbar | Bestehender Test `test_live_is_not_a_supported_mode`; dauerhafte UI-Kennzeichnung | Kein neuer Betriebsmodus oder Brokerrecht |
| DISABLED und fail-closed Wiederanlauf | Unveränderte `services/runtime/config.py` und bestehende Wiederanlaufprüfungen | Prototyp führt keinen Restart oder Resume aus |
| Decimal und UTC | Bestehende kanonische Decimal-/UtcTimestamp-Schemas; neue nullable Kennzahlen und unveränderliche Terms | Keine Finanzberechnung durch Prototyp; unbekannte Bewertung/Historie bleibt unbekannt |
| Unabhängiges Pause/Kill | Direkter Shelllink zu vorhandenen Paperkontrollen; bestehende Kontrollen bleiben zuständig | Ein sichtbarer Link ist noch kein vollständiger Last-/Recoverynachweis; spätere integrierte Abnahme bleibt erforderlich |
| Execution-Credentials und Redaktion | Kein Credentialfeld in Experiencepräferenzen, Vorschau oder Routerstate; Quellscan und unveränderte write-only Einrichtung | Keine echten Schlüssel gelesen, eingegeben oder für Tests verwendet |
| Unveränderliche Proposal-/Risk-/Auditreferenzen | Previewhash, Versionen, Herkunft und Eignungsreferenzen im additiven Vertrag; bestehende Human-Anforderungssemantik unverändert | Atomarer Einmalverbrauch und echte Auditkette sind erst mit neuer serverseitiger Implementation prüfbar |
| Atomare Budgetreservation | Bestehende Budget-/Riskimplementierung unverändert; neue Vorschau hat keine Reservation | Konkurrenz von zwei Guided Commits ist in IEF-07/09 noch zu beweisen |
| Ablauf und Änderung vor Submit | Vertrag bindet Gültigkeit, Konto-/Policy-/Portfolio-/Evidenzversionen; Prototyp zeigt Stale mit neuer Prüfung | Neue serverseitige Revalidierung wird nicht durch einen deaktivierten Button ersetzt |
| UNKNOWN, Timeout und Reconciliation | Prototyp führt zum bestehenden Vorgang; kein Ersatzauftrag; Vertrag erhält bestehende Operation-/Ordersemantik | Neue echte Crash-/Timeoutkette und Reservationsbindung bleiben IEF-07/09 offen |
| Partial Fill und Cancel/Fill-Rennen | Unveränderte bestehende Zustände und kontrollierte Anzeige im Prototyp | Keine neue synthetische Anzeige als Beleg eines echten Fills oder freier Reserve verwenden |
| Providerfehler dürfen Reconciliation/Pause nicht sperren | Bestehende Prozessgrenzen unverändert; lokaler Prototyp benötigt keinen Provider | Integrierter Mischlastnachweis nach IEF-08/09 bleibt offen |

Die tatsächlich ausgeführten Kommandos, Ergebnisse und offenen Prüfungen stehen in [IEF-01](ief-01.md), [Baseline](ief-01-baseline.md) und [unabhängigem Review](ief-01-review.md). **UNVERIFIED** bleibt jede neue serverseitige Sicherheitswirkung, die erst spätere Pakete implementieren. Diese Datei erteilt weder eine Handelsberechtigung noch eine Zertifizierung des noch fehlenden Guided-Runtimepfads.

## Externer Abnahmeblocker

**HUMAN_UX_VALIDATION_PENDING**: Der Nutzer bestätigt fehlende Testpersonen. Betroffen sind die formative Flow-Freigabe IEF-T-C06/#107 und mittelbar die Übergabe an IEF-02–07. Die gesonderten funktionalen und integrierten menschlichen Nachweise IEF-T-O05 und IEF-T-C08 sind ebenfalls nicht erbracht. Das [Testprotokoll](ief-human-ux-protocol.md) enthält keine erfundenen Sitzungen oder Ergebnisse. Ohne diese Nachweise keine Produktfreigabe und keine Schließung der betroffenen Issues.

**Aktueller Abnahmezeitpunkt:** Nach dem späteren Nutzerauftrag werden alle drei menschlichen Prüfungen in IEF-09 durchgeführt. Frühere Aussagen in diesem Nachweis über eine menschliche Freigabe als technisches IEF-01- oder Übergabegate sind historisch. Der Prüfstatus bleibt HUMAN_UX_VALIDATION_PENDING.
