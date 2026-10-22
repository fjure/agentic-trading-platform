# Alpaca-Paperworkflow

Stand22.10.2026: Kontoprüfung, einmalige finanzielle Vorbereitung und begrenzte LIMIT-Planung sind lokal abgenommen. Der vollständige Lebenszyklus und die Bedienoberfläche folgen in APW-05/07/08/09; diese Anleitung behauptet noch keinen vollständig bedienbaren Handelsablauf. Das normale Lab startet weiterhin inaktiv. Die vollständige Offline- und spätere private menschliche Providerabnahme werden in APW-10 getrennt dokumentiert.

## Einrichtung und Zustand

Kontoeröffnung bleibt ein externer menschlicher Schritt. Schlüssel werden ausschließlich im privaten authentifizierten Einrichtungsdialog eingegeben. Speicherung, Kontoprüfung und Vorbereitung aktivieren keinen Handel. Details: [Alpaca-Einrichtung](alpaca-setup.md). Eine angenommene HTTP-Anforderung ist kein erfolgreicher Abschluss; der dauerhafte Vorgang und der tatsächliche Paperzustand sind maßgeblich.

Der Paperledger bleibt getrennt vom Fake-/Demolab. Die Anfangsbasis übernimmt belegtes Cash und erlaubte SPY-/QQQ-Longpositionen einschließlich Rohkostenbasis; ein erneutes Setup setzt diese Werte nicht zurück. Ein Risikobudget begrenzt zusätzliche Exposure und ersetzt kein Kontocash. Zu kleiner oder ungeklärter Handlungsspielraum erzeugt keine automatische Liquidation.

## Begrenzte Planung

Policyversion1 erzeugt ausschließlich USD-LIMIT-Vorschläge mit DAY und regulärer US-Session. Der Worker hat keinen Brokerport. Frische Bid-/Ask-Quoten und bestätigte Fähigkeiten werden an einen unveränderlichen MarketState gebunden. Die Quote darf höchstens30Sekunden, Capabilityevidenz höchstens120Sekunden alt sein; der Vorschlag läuft spätestens nach30Sekunden oder früher bei Quellen-/Session-/Freigabeablauf aus.

BUY rundet die Ask auf das gültige Preisraster auf, SELL die Bid ab. Der Tickabstand zur Quote ist auf0,1Prozent begrenzt. Die Menge wird stets abgerundet und berücksichtigt Cash, das aktuelle Risikobudget, offene Verpflichtungen, mindestens1Prozent Puffer und die gegebenenfalls engere Positionsgrenze. Verkäufe überschreiten keinen freien Longbestand. Confidence verändert diese Berechnung nicht.

Bruchteile mit höchstens9Nachkommastellen erfordern eine frische ausdrückliche Bestätigung für fractionable Asset und DAY-LIMIT-Kombination. Nicht bestätigte Fähigkeiten, zu kleine Mengen, veraltete Daten, ABSTAIN oder FLAT ohne Bestand erzwingen keine Order. Es gibt keinen MARKET-BUY-Ersatz. Der verständliche Nichtplanungsgrund wird zusätzlich auditiert, ohne eine Modellentscheidung umzuschreiben.

Der tatsächliche ursprüngliche SQL-Planungsbeleg, Digest, Quote und sämtliche Versionsbezüge bleiben erhalten. Risk prüft Preis und Menge unabhängig vor Reservation und Submit; Execution ändert den Vorschlag nicht. Unbekannte Brokerergebnisse bleiben reserviert und werden aufgeklärt, bevor eine Freigabe möglich ist. Reale Paperproviderfähigkeit ist durch die lokale Zertifizierung nicht bewiesen: [Capabilityevidenz](../evidence/apw-06-capabilities.md), [Replay](../evidence/apw-06-replay.md), [Abnahme](../evidence/apw-06.md).

## Noch offene Betriebsfunktionen

Explizite versionierte Budget-/Profilkonfiguration, normale Start-/Pause-/Stop-/Killtakte, fortlaufende Tagesbelege, nachvollziehbare Gebührenautorität, menschliche Order-/Canceldialoge und die vollständige Browserabnahme werden in den nachfolgenden Tasks integriert. Fehlende Voraussetzungen bleiben gesperrt. Keine Anleitung hier fordert Agenten zu echten Brokerorders auf; die spätere menschliche Alpaca-Abnahme bleibt privat und ausdrücklich vom lokalen Testdouble getrennt.
