# Portfolio read model — beobachtet und vorgeschlagen

Planungsstand auf `0442f29`; keine Implementierungs- oder Handelsfreigabe. USD/Alpaca-Paper bleibt von Fake, Demo, Researchsimulation und LIVE getrennt. Verbindlicher Ist-Vertrag: `contracts/http/control-api.openapi.yaml`.

## Beobachtete Quellen

- `packages/domain/models.py`: `Position` hält quantity, average_price, realized_pnl, ledger_version; `PortfolioSnapshot` hält equity, cash, exposure, as_of, ledger_version, quality und reconciliation. Geld, Preis und Menge sind Decimal, Zeit ist UTC.
- `packages/persistence/ledger.py`: `PostgresLedger.transaction` setzt UTC; `LedgerUnit` speichert versionierte Finanzobjekte. `position_versions` bewahrt frühere Positionen. Das ist noch keine veröffentlichte vollständige Performancezeitreihe.
- `services/control_api/paper_dto.py`: `Portfolio`, `Position`, `Snapshot`, `MarketEvidence`, `Order`, `Fill`; OAS `PaperPortfolio`, `PaperPosition`, `PaperSnapshot`, `PaperMarketEvidence`, `PaperOrder`, `PaperFill`.
- `services/control_api/migrations/versions/0020_paper_read_models.py`: `paper_snapshot` liest die an `risk_account_context` gebundene Finanzbasis, positive Positionen und kostenbasisgleiche Ledgerstände; die Marktbelege werden separat gelesen. Ein leeres Positionsarray bedeutet keine gehaltene positive Position, `null` bedeutet keine finanzielle Basis. Queryzeit ist kein neuer Bewertungszeitpunkt.
- Bestehende Reads: `GET /api/v1/paper/snapshot`, `/paper/orders`, `/paper/fills`, `/paper/operations`, jeweils unter `/api/v1`; Einzelergebnis `/api/v1/paper/operations/{operationId}`. GET führt keinen Providerzugriff aus. Herkunft und Audit bleiben über bestehende Traceansichten erreichbar.

## Feld- und Verfügbarkeitsmatrix

| Nutzerwert | Beobachtete Quelle | IEF-Vorschlag / Verfügbarkeit |
| --- | --- | --- |
| Cash, Eigenkapital, Exposure, USD | `PaperSnapshot.portfolio` | Unverändert belegte Decimalstrings; bei fehlender Basis `null` + `LEDGER_NOT_PREPARED` |
| Menge, Durchschnittspreis, Kostenbasis, realisiertes P&L | `PaperPosition` | Bestehende Werte übernehmen; averagePrice/costBasis können fehlen; realisiertes P&L ist kein Tagesertrag |
| Bewertungszeit, Ledgerqualität, Reconciliation | portfolio.asOf/ledgerVersion/quality/reconciliation | Immer neben Finanzwerten; stale Wert bleibt sichtbar als letzter Beleg, abhängig nutzbare Aktionen bleiben gesperrt |
| Bid/Ask, Providerzeit, Ablauf | markets[].quote, capability, session | Separater Marktkontext, kein Beweis einer gleichzeitigen Portfoliobewertung |
| Positionsmarktwert, unrealisiertes P&L | Kein direktes Paper-DTO-Feld | V1 `null` + `VALUATION_NOT_AVAILABLE`; keine Browserrechnung aus aktuellem Quote und altem Ledger |
| Tages-/Gesamtrendite, Prozent-P&L, Drawdown, Performancechart | Kein veröffentlichter konsistenter Paper-Performancevertrag | V1 `null` + `PERFORMANCE_HISTORY_NOT_AVAILABLE`; keine Simulation als Kontoverlauf, keine erfundene Null |
| Verfügbares Kaufbudget / freie verkaufbare Menge nach Reservationen | Risk-/Plannerbelege, nicht einfach Cash bzw. Positionsmenge | Nur expliziter serverseitiger Previewwert mit eigener Bindung; sonst `null` + `ADMISSION_PREVIEW_REQUIRED` |
| Gebühren einer Ausführung | `PaperFill.fee` nullable | Unbekannt bleibt `null` + `FEE_EVIDENCE_MISSING`, nicht 0; keine Hochrechnung über unvollständige Seiten |
| Experimentmetrik | Research-/Quant-Resultate | Nur im eigenen Researchkontext mit Mode/Reliability, niemals als Paperergebnis |

Die neuen Codes in dieser Matrix sind vorgeschlagene Readmodel-Reasons, keine Behauptung bereits existierender `PaperErrorCode`-Werte. Finanzielle Null ist nur mit belegtem Nullwert zulässig. Prozentwerte benötigen benannten Nenner, Zeitraum, Cashflowmethode und Quellen; fehlende Methodik blockiert den Wert.

## Additiver Vertragsentwurf

Neue authentifizierte Readfamilie `GET /api/v1/experience/portfolio` mit `InvestmentPortfolioViewV1` und `GET /api/v1/experience/activity?context=ALPACA_PAPER&limit=&cursor=` mit `InvestmentActivityPageV1`. Diese Vorschläge erweitern den OAS erst in einem gesondert beauftragten Apply. Sie ändern weder existierende `additionalProperties`-Grenzen noch erzeugte Clients von Hand.

`InvestmentPortfolioViewV1`: `schemaVersion`, `context=ALPACA_PAPER`, `namespace=alpaca-paper`, `observedAt`, `snapshotId`, `snapshotVersion`, `accountGeneration`, `portfolioLedgerVersion` (nullable), `asOf` (nullable), `quality`, `reconciliation`, `metrics`, `positions`, `marketEvidence`, `blockers`, `sourceReferences`. `MetricValueV1` enthält `value: decimal-string|null`, `currency: USD`, `availability: AVAILABLE|STALE|UNAVAILABLE`, `reasonCodes`, `asOf`, `sourceReferences`; Gründe sind erforderlich bei fehlenden Werten. Positionen referenzieren ihren eigenen Ledgerstand. Für fehlende Basis bleiben positions und Finanzmetriken semantisch unbekannt, nicht leere Erfolgsergebnisse.

Die erste Version stellt nur belegte vorhandene Finanzwerte bereit. Zusätzliche Bewertungs- und Performancefelder sind nullable, ohne Hintergrundsammlung oder neue Bewertungslogik. Historische Returnberechnung benötigt einen späteren fachlichen Methodikentscheid.

## Konsistenz und Lesesemantik

Eine Readprojektion wird innerhalb einer konsistenten Datenbanklesesicht gebildet und erhält eine opake snapshotId plus monotone Projektionsversion. Generation, Portfolioledgerstand und Positionsledgerstände müssen zur gebundenen Finanzbasis passen; fehlende Zuordenbarkeit erzeugt `SNAPSHOT_INCONSISTENT`, unbekannte abgeleitete Werte und geschlossene Folgeaktionen. Quotealter wird separat ausgewiesen. observedAt bezeichnet ausschließlich die Readzeit.

Positionsseiten nutzen `limit` (Standard 20, Maximum 100) und den optionalen Rückgabewert `nextCursor`. Der signierte Cursor ist kurzlebig und an Betreiber, Kontext, Demo-ID, Limit und den Fingerabdruck der vollständigen Quellprojektion gebunden. Jede Seite liest dieselbe Quelle erneut konsistent. Bei einer Änderung oder Ablauf antwortet die API mit 409 und `CURSOR_EXPIRED`. Der optionale Wert `reconciliationAt` bezeichnet den letzten erfolgreichen Abgleich für genau die ausgewiesene Paper-Finanzrevision. Ohne einen solchen Beleg bleibt er null. Ohne Finanzbasis ist `portfolioLedgerVersion` null und `snapshotVersion` bezeichnet Version 1 der Antwort. Die Navigation bindet sich an die opake `snapshotId`. Belegte Einzelbeträge in Originalwährung stehen bei Bedarf separat als `nativeAmount`. Die USD-Kennzahl bleibt ohne passenden FX-Beleg unverfügbar.

Activityseiten binden signierte Cursor an Betreiber, Kontext, Accountgeneration, Filter und eine feste obere Ereignisgrenze; ein Reload startet einen neuen Cursor. Neue Fills verschieben keine bereits gelesenen Seiten. Aggregationen werden nicht aus der sichtbaren Seite gebildet. Kein GET startet Brokerpolling, Research, Reconciliation oder Handel.

Historische stale Werte dürfen zur Diagnose sichtbar bleiben, müssen aber klar gekennzeichnet sein. `CURRENT` und ein jüngster Queryzeitpunkt allein beweisen keine frische Bewertung. Preview und finale Admission prüfen ihre eigenen aktuellen serverseitigen Belege; das Readmodel erteilt keine Autorität.

## Geplante Prüfevidenz

Fehlende Basis vs. belegtes leeres Portfolio; fehlende Kostenbasis/Gebühr; stale Quote bei jüngster Queryzeit; Generationwechsel zwischen Reads; paralleler Fill während Projektion; Cursor über neue Ereignisse; unveränderte Decimalstrings und UTC; keine Demo-/Papervermischung; kein Provider-I/O durch GET. OAS-/Beispielvalidierung und Clientregeneration sind Teil des späteren Apply.


## Ergänzende Demo-/Fake-Kontexte

Die obige Feldmatrix beschreibt detailliert ALPACA_PAPER. Der Experiencevertrag unterstützt daneben FAKE mit dem vorhandenen internen Snapshotleseport (`/api/v1/snapshot`) und DEMO nur mit expliziter demoRunId über den Readport hinter `/api/v1/demo/runs/{runId}/snapshot`. SupervisorSnapshot enthält portfolioEquity, optional portfolioCash und Positionen mit instrument/quantity/optional averagePrice sowie asOf/cursor. Fehlende Kostenbasis, Revision und Reconciliation werden nicht als Paperwerte ergänzt. IEF-03 bindet die echte interne Quellrevision und Namespace in einer konsistenten Lesetransaktion; falls dies für einen Kontext nicht belegbar ist, bleiben die abhängigen Werte/Analysen begründet unverfügbar. Eine Demoauswahl darf niemals auf ein anderes letztes Run oder den Fakeledger ausweichen. Keine neue Brokerabfrage oder Finanzhistorie.
