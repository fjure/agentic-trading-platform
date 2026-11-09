# APF-02 · Researchlauf und Budget

Arbeitsbranch `codex/ptv-87-apf-runtime` auf `df0377a193ead455636024160c5b950b1fdef011`. APF-01 ist auf `main` integriert, der Contracts-Lauf ist grün und Issue #86 geschlossen.

| Anforderungen | Implementierte Grenze |
| --- | --- |
| APF-R01/R02 | Das unveränderliche Register liefert `quant-research-agent-v1` auf Autonomiestufe 0. Die isolierte Relation hält Run-Bindung, Zustand, UTC-Frist, Lease, Fence und Checkpoint. Run-Anlage und Zustandswechsel committen mit dem Audit. Der Supervisor wählt aus serverseitig gebundenen Schritten. |
| APF-R03 | Das Werkzeugjournal prüft Version und Fence, bucht Versuche und liefert bei identischem Replay die gespeicherte Referenz. Lokale Effekte und Receipts committen gemeinsam. Abgelehnte Aktionen werden mit einem stabilen Code und ohne Rohparameter auditiert. |
| APF-R04/R06 | Das Modellledger bindet jeden Versuch an die Lauf- und Profilversion, sperrt Budgetumfänge in deterministischer Reihenfolge und reserviert Token und Kosten vor der Zulassung. Ungeklärte Beträge halten die Reserve. Eine nachträgliche Kostenabstimmung hat keine Publikationsrechte. |
| APF-R07 | Die flüchtige Projektion bleibt im Prozessspeicher. Die Datenbank speichert Bindung und Receipt ohne Projektionsbytes oder Sitzung. Die Sendefreigabe und der reservierte Versuch werden gemeinsam konsumiert. Widerruf und Ergebnisannahme sperren dieselbe Freigabe. |

## Prüfstand

Der isolierte PostgreSQL-Lauf für Migrationen `0037` bis `0039` bestand in 64,15 Sekunden. Er prüfte idempotente Laufanlage, Lease-Übernahme, Ablehnung einer alten Fence, Freigabe der Lease beim Warten, Abbruch, Tool-Replay und redigierte Ablehnungsbelege. Die Research-Rolle konnte `public.jobs` nicht lesen. Der Privilegienvergleich für die bestehenden Finanzrelationen und die Wiederherstellung aus einem Dump waren erfolgreich.

Der Datenbanklauf prüfte auch den Neustart mit verlorener Vorschau, die atomare Einmalzulassung für die exakten synthetischen Bytes, Widerruf vor Ergebnisannahme, unbekannte Usage mit weiter gebundener Reserve und spätere Kostenabstimmung ohne Ergebnisfreigabe. Zwei konkurrierende Reservierungen beanspruchten den letzten freien globalen Budgetplatz. Genau eine wurde zugelassen. Der Run behielt sein ursprüngliches UTC-Fenster. Nach einem simulierten Lease-Verlust erhielt der Nachfolger die bereits gespeicherte Ergebnisreferenz.

Ruff und 75 gezielte Unit- und Regressionstests bestanden, einschließlich vorhandener Trading-Budget- und Research-Job-Tests. Der unabhängige Review identifizierte Budgeterschöpfung und Leaseverlust als reguläre Supervisorpfade sowie eine zu frühe Zeitmessung bei PostgreSQL-Sperrwartezeit. Der Supervisor persistiert Budgeterschöpfung terminal und verwirft stale Ergebnisse. Die Laufzeitabrechnung verwendet den Zeitpunkt der Mutation.

Ein weiterer unabhängiger Review führte zur erneuten Prüfung des aktuellen Agentenregisters, zur Bindung des UTC-Budgetfensters, zum getrennten Status `RESULT_UNAVAILABLE` und zur vertrauenswürdigen Tarifbindung. Der Datenbanktest weist die Ablehnung eines manipulierten Tarifs nach. Der Credential-Scan meldete null Befunde.

## Abschließende Paketabnahme

Der PostgreSQL-Lauf mit Migrationen `0037` bis `0040` bestand in 65,84 Sekunden einschließlich Backup/Restore. Die automatische Recovery gibt beim belegbar ungesendeten Altversuch nur dessen Token- und Geldreserve frei. Ein möglicherweise gesendeter Versuch bleibt als unbekannt gebunden und blockiert weitere Schritte. Der Supervisor erhält abgeschlossene Ergebnisreferenzen zusammen mit der ursprünglichen Schritt-ID. Ein konkurrierender Dispatch desselben Laufs und eine Budgetbuchung mit altem Fence wurden abgewiesen.

Migration `0040` bucht Schritte und Toolaufrufe in derselben Transaktion gegen Run, Agent, Agentenversion, Arbeitsklasse, globales Fenster und bei geplanten Läufen gegen den Zeitplan. Die gewählten technischen Startgrenzen sind 2.400 Schritte und 1.600 Toolaufrufe pro globalem beziehungsweise Agentenfenster, mit kleineren Arbeitsklassenlimits. Eine fehlende Zeitplanpolicy sperrt den Lauf. Der Datenbanktest bestätigte genau einen erfolgreichen konkurrierenden Toolaufruf sowie die Sperre weiterer Läufe beim globalen und beim Agentenlimit. Ein ausgeschöpftes Schrittkontingent verhinderte den Claim und hinterließ null gebuchte Runschritte.

76 gezielte Unit- und Regressionstests sowie 2.429 Contract-Tests bestanden. Ruff, Credential-Scan und strikte OpenSpec-Validierung waren erfolgreich. Der semantische Vertragsvergleich gegen `df0377a193ea` meldete null Fehler und die bestehende Readiness-`allOf`-Warnung.

Nach HTTP 403 des GitHub-Connectors synchronisierte die autorisierte GitHub CLI den Nachweis in Issue #87. Der Read-back verifizierte den Managed Block, Labels, Identität und die nativen Abhängigkeiten.

Öffentliche Authentisierung und autoritative Dialogversions- und Rechteprüfung gehören zur Control-Integration. Der Gateway-Transport prüft die Bytegleichheit und die Providergrenze. Die experimentbezogene Quote wird mit der atomaren Quant-Admission verbunden. Das ausgelieferte Register aktiviert kein Providermodell. Die Tests verwenden ausschließlich synthetische Bindungen.
