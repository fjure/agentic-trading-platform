# Alpaca-Paperkonto für das lokale Lab vorbereiten

Diese Anleitung bereitet ein Alpaca-Paperkonto für einen menschlich kontrollierten Lab-Ablauf vor. Im Dashboard gibt es **Übersicht → Demo & Einrichtung → Alpaca einrichten**. Der Dialog speichert die private Konfiguration und aktiviert keinen Handel. Der neue Ablauf trennt Speichern, Kontoprüfung, finanzielle Vorbereitung und Betriebsfreigabe. Seine geführte Oberfläche ist noch in Umsetzung; der aktuelle Dialog führt beim Speichern keine Kontoprüfung aus. Für sichtbare Trades ohne Konto zunächst die [Dashboard-Demo](dashboard-demo.md) verwenden; der separate Paper-Smoke bleibt in [paper-smoke.md](paper-smoke.md) beschrieben.

## 1. Paperkonto bei Alpaca anlegen

1. Öffne die offizielle [Paper-Trading-Seite](https://docs.alpaca.markets/us/docs/paper-trading) und folge dem dortigen Link zur Kontoerstellung. Alpaca erlaubt Paper-Konten mit einer E-Mail-Adresse; ein bestehendes Konto muss dafür nicht gelöscht oder zurückgesetzt werden.
2. Melde dich im [Alpaca-Dashboard](https://app.alpaca.markets/account/login) an und wähle in der Kontoauswahl **Paper Trading**. Falls du ein separates Konto benötigst, verwende **Open New Paper Account**. Alpacas neu angelegte Paperkonten starten standardmäßig mit 100.000 USD virtuellem Cash; das ist eine simulierte Paper-Basis, kein echtes Geld.
3. Erzeuge im Paper-Dashboard unter **API Keys** einen Key und sein Secret. Verwende ausschließlich die Paper-Zugangsdaten. Alpaca trennt sie von Live-Zugangsdaten und verwendet für Paper-Trading den Host `paper-api.alpaca.markets`; `data.alpaca.markets` ist der Marktdatenhost. Der Lab-Adapter verwaltet die festen Hosts selbst. Ändere keine URL, um Live-Zugangsdaten oder einen Live-Host einzutragen. [Alpaca-Authentifizierung](https://docs.alpaca.markets/us/docs/authentication)

## 2. Konto-ID und private Datei vorbereiten

**Empfohlener Weg über die Oberfläche:** Im Alpaca-Dialog Paper-Key, Paper-Secret und erwartete Account-UUID eingeben und speichern. Die Felder werden anschließend geleert; es gibt keine Rücklesefunktion. Die private Datei liegt in einem ownergeschützten Docker-Volume und wird ausschließlich Execution lesend zugeordnet. Du musst dafür keine Datei manuell bearbeiten. Den Account-Wert nicht mit der sichtbaren Kontonummer verwechseln.

Die folgenden Hinweise zur manuellen Datei gelten nur für den bisherigen separaten Operator-/Paper-Smoke-Ablauf. Eine manuelle Altdatei und der Dialogspeicher werden nicht automatisch ineinander kopiert; bei späterer expliziter Provideraktivierung hat eine über den Dialog gespeicherte Konfiguration Vorrang.

Alpacas API-Key ist nicht die Konto-ID. Für `expected_account_id` wird die UUID des Kontos benötigt, die Alpacas authentifizierter `GET /v2/account`-Endpunkt als `id` zurückgibt; `account_number` ist ein anderes Feld. Lass die Konto-ID von dir selbst mit einem privaten, lokalen API-Client abrufen, der Paper-Host und deine Paper-Anmeldedaten verwendet. Gib weder Key noch Secret in Chat, Browser-Konsole, Dashboard-Notiz, Shell-Befehlszeile oder Logs ein. Kopiere auch keine privaten Antworten in diese Unterhaltung. [Get Account](https://docs.alpaca.markets/us/reference/getaccount-1)

Lege selbst die Datei `.tooling/application/runtime/alpaca.json` an. Sie muss genau die drei öffentlichen Feldnamen unten enthalten; ersetze die Platzhalter lokal durch die Paper-Werte. Speichere keine echten Werte in Git oder einer geteilten Datei.

```json
{
  "api_key": "<dein Paper API Key>",
  "api_secret": "<dein Paper API Secret>",
  "expected_account_id": "<UUID aus dem id-Feld von GET /v2/account>"
}
```

Beschränke die Datei auf dein Benutzerkonto: unter Windows über die Dateisicherheit (ACL), unter Linux mit Modus `0600`. Im Container läuft Execution als UID/GID `10001`; bei Linux muss diese Identität die bereitgestellte Datei lesen können, ohne sie für andere Nutzer freizugeben. Die Compose-Paper-Überschreibung mountet diese Datei als Secret ausschließlich in Execution. Keine zusätzlichen Felder, eigene Base-URL oder Credentials in Runtime-Konfiguration aufnehmen. Beim Start liest Execution nur `api_key`, `api_secret` und `expected_account_id` aus `/run/secrets/alpaca`.

## 3. Vor einem Verbindungsversuch

Das Speichern der Datei bedeutet nur **Zugangsdaten vorhanden**. **Konto verbunden** ist erst belegt, wenn der dafür vorgesehene menschliche Ablauf die Paper-Kontoidentität erfolgreich gegen die erwartete UUID geprüft hat. **Handel aktiviert** ist ein weiterer, separater Zustand mit zusätzlichen Voraussetzungen und expliziter menschlicher Bestätigung. Ein leeres Dashboard beweist keine erfolgreiche Verbindung.

Das bestehende Lab-Ledger startet mit einer eingefrorenen Basis von 10.000 USD; Alpacas neues Paperkonto startet üblicherweise mit 100.000 USD. Bei einer Abweichung darf kein Smoke-Lauf beginnen. Ein bestehendes Ledger oder Volume nicht löschen, zurücksetzen oder „rebased“ starten, und `InitialCash` ändert keine bestehende Finanzbasis. Eine passende dedizierte Paper-/Laborbasis muss separat und sicher eingerichtet werden, bevor der Ablauf weitergehen kann. Der Mensch muss außerdem ein sauberes Paperkonto ohne Positionen und offene oder unbekannte Orders verwenden.

Die normale lokale Startverknüpfung startet zusätzlich ein getrenntes, zunächst inaktives Paperpaket mit eigener Datenbank. Das bestehende Fake-Lab bleibt erhalten. Ein Neustart allein verbindet das Konto nicht und startet keinen Handel. Der separate Paper-Smoke ist ein manueller Einmalablauf mit privaten Terminals und mehreren Schutzprüfungen; führe ihn nur gemäß [paper-smoke.md](paper-smoke.md) und dessen Vorbedingungen aus. Bis Kontoprüfung, einmalige finanzielle Vorbereitung und explizite Betriebsfreigabe abgeschlossen sind, bleiben neue Einstiege geschlossen.

## Neue Schlüsselgeneration und Kontoprüfung

Der normale authentifizierte Dialog veröffentlicht eine unveränderliche neue Schlüsselgeneration im privaten Einrichtungsdienst. Das schließt zuerst neue Einstiege und entwertet bisherige Freigaben. Bestehende aktive Dateien bleiben für Recovery erhalten. Ein Speichervorgang bestätigt ausschließlich die Speicherung; insbesondere sind `CONFIGURED` und eine erfolgreiche HTTP-Annahme keine bestätigte Brokerverbindung.

Eine anschließend ausdrücklich angeforderte Kontoprüfung wird als dauerhafter Vorgang von Execution bearbeitet. Execution prüft die erwartete Konto-UUID, USD, Kontosperren und den IEX-Feedzugriff über feste Paper-/Marktdatenhosts. Öffentliche Antworten zeigen nur ein redigiertes Kontolabel und feste Fehlercodes. Eine erfolgreiche Prüfung allein bereitet weder einen Ledger vor noch gibt sie Handel frei. Alte Quotes außerhalb der Marktzeiten können Feedzugriff belegen; sie gelten nicht als frische Handelspreise.

Bei Rotation muss die neue Generation dasselbe bereits gebundene Konto belegen. Ungeklärte Brokerverpflichtungen verhindern die Umschaltung; die vorherige aktive Generation wird nicht überschrieben. Bei einer verlorenen Speicherantwort kann derselbe Vorgang die zuerst veröffentlichte Generation erneut bestätigen. Der Server vergleicht dabei keine Schlüsselwerte. Für andere Schlüssel muss ein neuer Speichervorgang angelegt werden. Ein Prozessabbruch genau zwischen Dateiveröffentlichung und Aufräumen eines temporären Hardlinks bleibt sicher gesperrt; ein neuer Speichervorgang mit neuer Generation ermöglicht die Wiederaufnahme. Unsichere Dateimetadaten werden niemals automatisch repariert.

Die vollständige Oberfläche und Betriebsabnahme dieses neuen Ablaufs folgen in APW-09/10. Die [APW-03-Evidenz](../evidence/apw-03.md) beschreibt die abgenommene Kontoprüfung; die [APW-04-Evidenz](../evidence/apw-04.md) trennt bereits geprüfte Vorbereitung von noch offener Integration. Agenten führen Kontoprüfungen ausschließlich gegen lokale Testdoubles mit synthetischen Schlüsseln aus.

## Finanzielle Vorbereitung (APW-04 lokal abgenommen)

Die Vorbereitung übernimmt einmalig belegtes Cash und erlaubte SPY-/QQQ-Longpositionen in den getrennten Paperledger. Offene Orders, widersprüchliche Aufnahmen oder fehlende Rohkostenbasis verhindern die Bindung. Zwei vollständige Aufnahmen müssen übereinstimmen. Vorhandene Anfangspositionen sind keine neu gesendeten Orders; alte Aktivitäten werden über unveränderliche finanzielle Historienanker gebunden. Ein vorhandener Ledger darf durch erneute Einrichtung niemals neu bewertet oder zurückgesetzt werden. `PREPARED` öffnet keine Handelsgates.

Für Paper zählt der konservativ belegte Verlust seit UTC-Tagesbeginn, bereinigt um nachgewiesene externe Ein-/Auszahlungen. Frühere Buchgewinne sind kein heutiger Gewinn. Die historische Equityquelle beschreibt Minutenfenster; der Endwert des letzten Fensters vor UTC-Mitternacht dient als Grenzbeleg. Da die Quelle Centwerte ohne zugesagtes Rundungsverfahren meldet, wird ein Intervall von einem Cent in beide Richtungen verwendet. Finanzierungsaktivitäten müssen in vollständiger und gefilterter Historie über IDs und finanzielle Hashes übereinstimmen. Unbekannte Transfers oder unvollständige Evidenz sperren neue Einstiege.

Ein belegbar nach UTC-Mitternacht angelegtes, bisher ungehandeltes Konto darf die erste konsistente Bindung als ausdrücklich gekennzeichnete Ersttagsreferenz verwenden. Es braucht keine Positionen, keine ausgeführten Trades oder Erträge und zwei übereinstimmende Cashbelege. Ausschließlich abgelehnte, stornierte oder abgelaufene Versuche mit ausdrücklich belegter Nullfüllung sind zulässig. Fehlende Fillfelder sind kein Nullfüllungsbeleg. Die Ausnahme endet exakt an der nächsten UTC-Mitternacht; anschließend ist eine belegte Tagesgrenze erforderlich.

Der vom Broker gemeldete gesamte `cost_basis` bleibt unabhängig vom gerundeten `avg_entry_price` erhalten. Teilverkäufe verteilen Kosten mit Decimal bei18Nachkommastellen und ROUND_HALF_EVEN; die Restkosten bleiben an der Restposition, der letzte Verkauf verbraucht sie vollständig. Fehlende oder abweichende Kostenbelege sperren die Reconciliation.

Quellen für diese lokale Implementierung: [Equityhistorie](https://docs.alpaca.markets/us/reference/getaccountportfoliohistory-1), [Zeitfensterbeschreibung im offiziellen SDK](https://github.com/alpacahq/alpaca-py/blob/master/alpaca/trading/models.py), [Aktivitäten und Filter](https://docs.alpaca.markets/us/docs/account-activities) und [Kostenbasisverfahren](https://docs.alpaca.markets/us/docs/position-average-entry-price-calculation). Diese Quellen und lokale Transportdoubles ersetzen keine spätere menschliche Providerabnahme.

## Offizielle Quellen

Die Alpaca-Seiten wurden am 18.10.2026 geprüft: [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading), [Start Paper Trading](https://alpaca.markets/learn/start-paper-trading), [Authentication](https://docs.alpaca.markets/us/docs/authentication) und [Get Account](https://docs.alpaca.markets/us/reference/getaccount-1).
