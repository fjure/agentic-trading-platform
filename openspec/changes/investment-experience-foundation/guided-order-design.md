# Geführte Paper-LIMIT-Anfrage

Planungsentwurf auf `0442f29`, keine Orderausführung und kein Apply. Der Ablauf ist eine menschlich bestätigte Paperanforderung auf bestehendem Risk-/Executionpfad. LIVE bleibt technisch unerreichbar; Researchagenten erstellen oder genehmigen keine Brokerorders.

## Beobachtete Bausteine

`services/control_api/paper_dto.py:Terms/Limit` und OAS `PaperLimitOrderRequest` verlangen credentialGeneration, accountGeneration, policyVersion, profileVersion, limitPolicyVersion, budgetVersion, riskBudget, USD, expiresAt sowie Symbol SPY/QQQ, BUY/SELL, exakte quantity/limitPrice, LIMIT, DAY, REGULAR und `PLACE_PAPER_LIMIT`. `POST /api/v1/paper/order-requests` nimmt asynchron eine `PaperOperation` an; das ist noch keine Brokerbestätigung. Statusreads und `POST /api/v1/paper/orders/{orderId}/cancel` existieren bereits.

`packages/domain/paper_workflow.py` definiert `HumanOrderRequest`, `PaperLimitTerms`, `PaperTradeProposal` und Herkunft `HUMAN_REQUEST`. `services/worker/paper_human_limit.py:assess_human_limit` prüft generation-/policy-/portfolio-/evidencegleiche Belege, TTL, Quotealter, frische Fähigkeiten, reguläre Session, Preis-/Mengenschritte und Budget. BUY bei bereits gehaltener Position wird derzeit als `POLICY_MISMATCH` abgelehnt; Guided darf kein Nachkaufen versprechen. SELL ist auf Longbestand abzüglich offener Exitverpflichtungen begrenzt. Bestätigte Mengen und Preise werden nicht still geändert.

`packages/risk/paper_limits.py` prüft menschliche Herkunft und unveränderliche Proofs zusätzlich zu unabhängigen normalen Riskgates. `services/execution/paper_submit.py` erhält die Submitfence vor I/O und schreibt bei ungewisser Antwort `UNKNOWN`; `services/execution/paper_reconciliation.py` klärt bestehende Verpflichtungen. Normativ: REQ-APW-006–010 in `openspec/specs/alpaca-paper-workflow/spec.md`.

## Nutzerablauf und additive Previewfamilie

1. Dauerhaft markierter Alpaca-Paperkontext zeigt tatsächlichen Setup-/Operatingstatus, letzte Reconciliation und belegte Finanzbasis. Keine Konfiguration, Verbindungsprüfung oder Preview startet Paperbetrieb.
2. Mensch wählt unterstütztes Symbol und BUY/SELL; gibt Menge und LIMITpreis als exakte Dezimalwerte ein. Schritte, Fristen und fehlende Fähigkeiten werden verständlich erklärt. Kein Modellergebnis füllt eine handelbare Bestätigung aus.
3. Vorgeschlagenes `POST /api/v1/paper/order-previews` akzeptiert `PaperLimitPreviewRequestV1` mit exakten Terms und erwarteter Finanz-/Marktevidenzbindung. Es führt ausschließlich serverseitige Prüfung/Decimalrechnung aus, keine Reservation, keine Riskfreigabe, kein Broker-I/O. Antwort `PaperLimitPreviewV1` enthält previewId/version, requestHash, generation-/policy-/profile-/budget-/limitPolicyversionen, portfolioLedgerVersion, marketStateId, quoteId, evidenceHash, observedAt/validUntil, exakte ursprüngliche Terms, availability, blockers und serverseitige Kosten-/Kapazitätswerte.
4. Bestätigung zeigt USD-Paper, Symbol, Richtung, Menge, LIMIT, DAY/REGULAR, Gültigkeit, bekannte Notionalobergrenze und separat Gebühren-/Reserveannahmen. `quantity × limitPrice` ist bei BUY eine Bruttonotionalobergrenze, bei SELL eine Bruttonotionaluntergrenze; weder ist garantierter Ausführungserlös, Nettobetrag oder Portfolioertrag. Fehlende Gebührenevidenz ist unbekannt und sperrt, soweit bestehende Gates es verlangen. Jeder Budget-/Kostenwert benennt seine Bedeutung; Riskbudget ist weder Kontostand noch freier Cash.
5. Nur der authentifizierte Mensch bestätigt diese konkrete Fassung. Additiver Entwurf `POST /api/v1/paper/guided-order-requests` mit `PaperGuidedLimitOrderRequestV1` bindet previewId, expectedPreviewVersion, requestHash und `PLACE_PAPER_LIMIT`, plus Idempotency-Key. Der Server konsumiert die gültige Previewbindung atomar höchstens einmal und leitet dieselben exakten Terms in den bestehenden menschlichen Anforderungspfad; keine zweite Executionimplementierung. Die bisherige `/paper/order-requests`-Familie bleibt semantisch unverändert.
6. Antwort ist dieselbe `PaperOperation`-Semantik; anschließend werden der gespeicherte Vorgang, verknüpfte Order, Fills und Trace gelesen. Eine verlorene Antwort führt zur Abfrage bzw. zum Replay desselben Schlüssels, niemals zu einem Ersatzauftrag mit neuem Schlüssel.

Preview-Dauer ist durch das früheste Ablaufdatum der gebundenen Quote, Fähigkeit, Session, Approval und bestehenden TTL begrenzt. Eine Preview ist kein Paperstart, keine Tradingapproval und kein vorweggenommener erfolgreicher Riskentscheid. Der neue Vertrag ist vor Apply gegen kanonischen OAS und Clientgeneration zu prüfen; bestehende DTOs werden nicht heimlich erweitert.

## Bestätigung, Konkurrenz und frische Gates

Preview und Submit prüfen aktuellen Kontext, Account-/Credentialgeneration, Policy-, Profil-, Budget- und Limitversion, Ledger-/Proofbindung, Entrygate, Reconciliation, Session, Quotealter, Fähigkeiten, verfügbare Longmenge sowie alle Reservierungen. Stale Readmodel oder abgelaufene Preview sperrt Bestätigung. Die finale Prüfung ist serverseitig und unter derselben Account-/Idempotenzschutzgrenze wie der vorhandene Anforderungspfad; ein UIbutton ersetzt sie nicht.

Geänderte Terms, Fill, Rotation, Pause/Kill, Profiländerung, neues Budget, Fristablauf oder veränderte Evidenz invalidieren die Preview. Die UI erklärt die Differenz und verlangt frische Preview plus neue bewusste Bestätigung. Kein stilles Repricing, Resizing, TTLverlängern, Rundungswechsel oder Freigabeübertrag auf einen anderen Auftrag. Derselbe Schlüssel mit identischer Bindung liefert denselben Vorgang; andere Nutzlast ergibt `IDEMPOTENCY_CONFLICT`. Zwei Schlüssel können dieselbe einmalige Preview nicht zweimal konsumieren.

## Echte Zustände und UIphasen

`PaperOperation.status` bleibt `ACCEPTED|RUNNING|SUCCEEDED|BLOCKED|FAILED`. Setupstate und Operatingstate bleiben getrennt. Orderzustände aus `PaperOrder` sind `CREATED`, `RISK_RESERVED`, `SUBMITTING`, `ACKNOWLEDGED`, `PARTIALLY_FILLED`, `FILLED`, `REJECTED`, `CANCEL_PENDING`, `CANCELED`, `UNKNOWN`. UIphasen wie Eingabe, Vorschau und Bestätigen sind lokale Darstellungsphasen, keine neuen Brokerzustände. `SUCCEEDED` für einen Vorgang garantiert keinen vollständigen Fill.

Bei `UNKNOWN` zeigt die UI „Brokerstatus ungeklärt“, vorhandene IDs, letzte Beobachtung und Reconciliationbedarf; neue Einstiege bleiben gesperrt und Reservationen gebunden. Keine Wiederholen-Kauf-Aktion, keine Erfolgsmeldung und keine Nullposition aus fehlender Antwort. Cancel ist ein separater menschlicher Auftrag und darf weder einen Fill noch die Freigabe einer Reservation behaupten; bei Fill-/Cancelrennen bleibt die bestätigte Ledgerwahrheit maßgeblich. Pause/Kill bleiben unabhängig erreichbar, ohne automatische Liquidation.

## Geplante Nachweise

Doppelklick und zwei Schlüssel auf eine Preview; geänderter Requesthash; Sessionverlust; Accountrotation/Policywechsel; konkurrierender Fill/Reservation; stale Quote/Sessionende zwischen Preview und Submit; BUY bei bestehender Position; fehlende Fractionalbestätigung; kein stilles Runden; Timeout nach möglicher Brokerannahme und Restart ohne Blindresubmit; Cancel gegen Teilfill; Preview-GET-/POST-Pfad ohne Broker-I/O und ohne Handelsaktivierung. Negative Prüfungen behalten Decimal-/UTCgenauigkeit, Herkunft und bestehende Risk-/Executiongates bei.
