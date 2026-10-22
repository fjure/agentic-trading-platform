# Entscheidungsvorlage: Tagesverlust bei importierten Paperbeständen

Status21.10.2026: Vorschlag zur Nutzerentscheidung, noch nicht freigegebene Anforderung. APW-03 kann unabhängig fertig geprüft werden.

## Befund

Die vorhandene Riskberechnung addiert realisierten Tagesertrag und gesamten unrealisierten Positionsgewinn. Bei importierten Positionen kann dieser Gewinn aus früheren Tagen stammen; eine Übernahme würde heutige Verluste verdecken oder historische Verluste erneut zählen. Alpacas `last_equity` bezeichnet den vorherigen US-Handelsschluss und belegt keine UTC-Tagesgrenze.

Die offizielle Portfoliohistorie verwendet links beschriftete Zeitfenster mit Eigenkapital am Fensterende. Ein abgeschlossenes Minutenfenster mit Beschriftung23:59UTC kann daher die folgende UTC-Tagesgrenze belegen. Geldwerte sind auf zwei Dezimalstellen gerundet; das Rundungsverfahren wird nicht angegeben. Ein neu angelegtes Paperkonto erhält üblicherweise100000USD, aber ein passender erster Einzahlungsvorgang oder ein historischer Mitternachtspunkt wird nicht zugesichert.

## Vorgeschlagene Ergänzung zu ADR-APW-003

Für Paper wird eine eigene versionierte Tagesverlustberechnung eingeführt. Sie misst die Veränderung des belegten Eigenkapitals seit der UTC-Tagesgrenze, bereinigt um nachgewiesene externe Ein-/Auszahlungen. Dividenden, Gebühren und Handelsergebnisse bleiben Ertrag beziehungsweise Verlust. Unklare Transfers oder unvollständige Belege sperren neue Einstiege. Fake und Demo behalten ihre bisherige Berechnung.

Historische Geldwerte werden mit ihrer tatsächlichen Genauigkeit gespeichert. Für auf Cent gerundete Werte gilt als ausdrücklich festgelegte konservative Zertifizierungsregel ein Intervall von einem Cent unter bis einem Cent über dem gemeldeten Wert. Die Verlustprüfung verwendet die ungünstigste belegte Kombination; der tägliche Kapitalnenner bleibt eingefroren und wird durch spätere Einzahlungen nicht automatisch erhöht. Alle bisherigen Risiko-, Freshness-, Budget-, Reconciliation- und Executiongates bleiben erforderlich. Tatsächliche Providersemantik bleibt bis zur menschlichen Abnahme unverifiziert.

Für ein nach UTC-Mitternacht neu angelegtes, nachweislich bisher ungehandeltes Konto darf die erste konsistente finanzielle Bindung als ausdrücklich gekennzeichnete Ersttagsreferenz dienen: verifizierter Erstellungszeitpunkt, keine Positionen, vollständige leere Handels-/Ertragshistorie und zwei übereinstimmende Cashbelege. Das ist eine Referenz ab Erstbindung, keine behauptete historische Mitternachtsbilanz. Diese Ausnahme gilt ausschließlich bis zur nächsten UTC-Tagesgrenze. Danach wird eine neue belegte UTC-Tagesreferenz verlangt; bei fehlenden Belegen bleiben Einstiege gesperrt.

Importierte Positionen speichern die tatsächliche `cost_basis` unabhängig von `avg_entry_price`. Immutable Herkunft und künftige Kostenbasisfortschreibung werden gemeinsam mit der einmaligen Anfangsbasis erweitert; es gibt kein Rebase und keine erfundenen realisierten Gewinne als Ausgleich für historische Buchgewinne.

## Alternative

Nur eine vollständig belegte UTC-Tagesgrenze zulassen. Neu angelegte Konten ohne diese Evidenz bleiben zunächst gesperrt. Das vermeidet eine Ersttagsausnahme, kann aber die normale Ersteinrichtung bis zum nächsten belegbaren UTC-Tageswechsel verzögern. Auch diese Variante benötigt die korrigierte Paper-Tagesverlustberechnung für importierte Positionen.

## Quellen

- [Offizielles PortfolioHistory-Modell](https://github.com/alpacahq/alpaca-py/blob/master/alpaca/trading/models.py): Fensterbeginn als Zeitstempel, Eigenkapital am Fensterende.
- [Portfolio History](https://docs.alpaca.markets/us/reference/getaccountportfoliohistory-1): Parameter, Cashflows und Centgenauigkeit.
- [Kontobewegungen](https://docs.alpaca.markets/us/docs/account-activities) und [Filtersemantik](https://docs.alpaca.markets/us/reference/getaccountactivities-2): Aktivitätstypen und Zeitbezüge.
- [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading): initialer virtueller Kontostand.
- [Kostenbasis](https://docs.alpaca.markets/us/docs/position-average-entry-price-calculation): unterschiedliche Kostenbasismethoden und Tageswechsel.
