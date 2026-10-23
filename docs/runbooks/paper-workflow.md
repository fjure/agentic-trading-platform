# Alpaca-Paperworkflow

Stand22.10.2026: Kontoprüfung, einmalige finanzielle Vorbereitung und begrenzte LIMIT-Planung sind lokal abgenommen. Der Paperlebenszyklus ist in APW-05 lokal abgenommen; menschliche LIMIT-Anfragen sind in APW-07 als Backendpfad abgenommen; Cancels und die Bedienoberfläche folgen in APW-08/09; diese Anleitung behauptet noch keinen vollständig bedienbaren Handelsablauf. Das normale Lab startet weiterhin inaktiv. Die vollständige Offline- und spätere private menschliche Providerabnahme werden in APW-10 getrennt dokumentiert.

## Einrichtung und Zustand

Kontoeröffnung bleibt ein externer menschlicher Schritt. Schlüssel werden ausschließlich im privaten authentifizierten Einrichtungsdialog eingegeben. Speicherung, Kontoprüfung und Vorbereitung aktivieren keinen Handel. Details: [Alpaca-Einrichtung](alpaca-setup.md). Eine angenommene HTTP-Anforderung ist kein erfolgreicher Abschluss; der dauerhafte Vorgang und der tatsächliche Paperzustand sind maßgeblich.

Der Paperledger bleibt getrennt vom Fake-/Demolab. Die Anfangsbasis übernimmt belegtes Cash und erlaubte SPY-/QQQ-Longpositionen einschließlich Rohkostenbasis; ein erneutes Setup setzt diese Werte nicht zurück. Ein Risikobudget begrenzt zusätzliche Exposure und ersetzt kein Kontocash. Zu kleiner oder ungeklärter Handlungsspielraum erzeugt keine automatische Liquidation.

## Begrenzte Planung

Policyversion1 erzeugt ausschließlich USD-LIMIT-Vorschläge mit DAY und regulärer US-Session. Der Worker hat keinen Brokerport. Frische Bid-/Ask-Quoten und bestätigte Fähigkeiten werden an einen unveränderlichen MarketState gebunden. Die Quote darf höchstens30Sekunden, Capabilityevidenz höchstens120Sekunden alt sein; der Vorschlag läuft spätestens nach30Sekunden oder früher bei Quellen-/Session-/Freigabeablauf aus.

BUY rundet die Ask auf das gültige Preisraster auf, SELL die Bid ab. Der Tickabstand zur Quote ist auf0,1Prozent begrenzt. Die Menge wird stets abgerundet und berücksichtigt Cash, das aktuelle Risikobudget, offene Verpflichtungen, mindestens1Prozent Puffer und die gegebenenfalls engere Positionsgrenze. Verkäufe überschreiten keinen freien Longbestand. Confidence verändert diese Berechnung nicht.

Bruchteile mit höchstens9Nachkommastellen erfordern eine frische ausdrückliche Bestätigung für fractionable Asset und DAY-LIMIT-Kombination. Nicht bestätigte Fähigkeiten, zu kleine Mengen, veraltete Daten, ABSTAIN oder FLAT ohne Bestand erzwingen keine Order. Es gibt keinen MARKET-BUY-Ersatz. Der verständliche Nichtplanungsgrund wird zusätzlich auditiert, ohne eine Modellentscheidung umzuschreiben.

Der tatsächliche ursprüngliche SQL-Planungsbeleg, Digest, Quote und sämtliche Versionsbezüge bleiben erhalten. Risk prüft Preis und Menge unabhängig vor Reservation und Submit; Execution ändert den Vorschlag nicht. Unbekannte Brokerergebnisse bleiben reserviert und werden aufgeklärt, bevor eine Freigabe möglich ist. Reale Paperproviderfähigkeit ist durch die lokale Zertifizierung nicht bewiesen: [Capabilityevidenz](../evidence/apw-06-capabilities.md), [Replay](../evidence/apw-06-replay.md), [Abnahme](../evidence/apw-06.md).

## Expliziter Paperbetrieb

APW-05 ist mit lokaler tatsächlicher SQL-/HTTPdouble-Laufzeitkette abgenommen. Unter `/api/v1/paper/trading-profile` besitzt Paper eine eigene Budget-/Strategiekonfiguration. Ohne Konfiguration stehen Budgetnull und Profil-/Budgetversion0. Speichern benötigt erwartete Profilversion, Konto-/Credentialgeneration, Idempotenz und ausdrückliche Bestätigung. Es schließt die Einstiegssperre. Der bisherige Fake-Profilendpunkt bleibt getrennt.

Der Gebührenumfang gilt ausschließlich für ein ausdrücklich menschlich bestätigtes direktes Retail-Paper-US-Aktienkonto. Ein separater unveränderlicher Beleg bindet ihn an Anfangsbasis, Konto und Profilversion; die ursprüngliche Basis wird nicht umgeschrieben. Neue Einstiege verlangen aktuelle Generation und Bestätigung. Recovery darf die frühere belegte Gebührenautorität für dasselbe gepinnte Konto während einer Schlüsselrotation behalten. Tatsächlich beobachtete Gebühren, Fremdaktivität oder ungeklärte Bestände werden dadurch nicht als Null behandelt.

Ein bestätigter START bindet alle aktuellen Versionen und einen höchstens30Sekunden gültigen Zeitraum. Annahme bedeutet zunächst nur einen gespeicherten Vorgang. Ausschließlich Execution öffnet nach aktuellen Quellen-, Reconciliation-, Tages- und Workerbelegen. Ablauf, Neustart, Profil-/Schlüsseländerung, Pause, Stop oder Kill erteilen keine automatische neue Freigabe. Wiederholung verlängert keinen Zeitraum. Kill bleibt dauerhaft; es gibt keine automatische Liquidation.

Kommandoverarbeitung, Heartbeat und Reconciliation laufen getrennt. Providerwartezeiten halten keine finanziellen Kontosperren; Pause und Kill schließen die finanziellen Gates vor ihrer HTTP-Annahmebestätigung. Ein bereits zuvor dauerhaft autorisierter Submitversuch kann noch beantwortet werden; seine Aufklärung und Reservation bleiben erhalten. Nach einem unklaren Commit oder einer unklaren Providerantwort wird dieselbe Verpflichtung ausschließlich gesucht und abgeglichen, niemals blind erneut gesendet.

Tagesbelege werden ohne Rebase erneuert. Die Ersttagsreferenz behält ihren ursprünglichen Beginn und Kapitalnenner; spätere Finanzierung wird ab diesem Beginn belegt. Nach UTC-Ablauf ist ein echter neuer Tagesstartbeleg erforderlich. Bei unverändertem Bestand darf eine neue unveränderliche Portfolio-Beobachtung dessen tatsächliche Reconciliationzeit dokumentieren, ohne Cash, Kostenbasis oder Anfangshistorie zu verändern.

Versionierte Profile, Start-/Pause-/Stop-/Killtakte, fortlaufende Tagesbelege und nachvollziehbare Gebührenautorität sind implementiert. Menschliche Order-/Canceldialoge und die vollständige Browserabnahme folgen in APW-07bis10. Fehlende Voraussetzungen bleiben gesperrt. Keine Anleitung hier fordert Agenten zu echten Brokerorders auf; die spätere menschliche Alpaca-Abnahme bleibt privat und ausdrücklich vom lokalen Testdouble getrennt.


## Menschliche LIMIT-Anforderung

Der authentifizierte Paperendpunkt `/api/v1/paper/order-requests` nimmt eine bestätigte einzelne BUY-/SELL-LIMIT-DAY-Anforderung entgegen. Menge und Preis bleiben exakt; ungültige Schritte, Ticks, Fähigkeiten, Versionen oder Fristen werden abgewiesen. Es entsteht keine Modellentscheidung. Die Anfrage teilt dieselbe Riskprüfung, Kontosperre und Reservation mit automatischen Vorschlägen. Kosten einschließlich Puffer müssen ins aktuelle Budget passen; SELL überschreitet keinen unreservierten Longbestand.

HTTP202 bedeutet angenommen, nicht ausgeführt. Der dauerhafte Vorgang liefert erst nach tatsächlichem Risk-/Brokerbeleg einen Abschluss und die abgeleitete Order-ID. UNKNOWN bleibt laufend und reserviert; dieselbe Verpflichtung wird gesucht und abgeglichen, niemals blind wieder gesendet. Doppelte identische Idempotenzschlüssel erzeugen keinen zweiten Auftrag, abweichender Inhalt wird zurückgewiesen. Budget-/SELL-Races erscheinen als BUDGET_EXCEEDED bzw. INSUFFICIENT_POSITION.

Ein tatsächlicher Fill kann die gebundene Portfolio-Version verändern und damit die alte Freigabe schließen. Neue Orders benötigen dann einen neuen expliziten START mit aktuellen Belegen. Ein bereits belegter Auftrag kann nach Pause, Rotation oder Neustart abgeschlossen werden, ohne neuen START oder neuen Brokerrequest. Mit den derzeitigen15Minutenkerzen-/120Sekunden-Frischegrenzen bleiben neue Einstiege außerhalb eines frischen belegten Datenfensters geschlossen; Recovery läuft weiter. Diese Grenzen wurden für Tests nicht gelockert.

Die vollständige Materialbedienung für diese Backendfunktion folgt in APW-09; Cancellation folgt in APW-08. [Lokale Abnahme und Betriebsgrenzen](../evidence/apw-07.md).
