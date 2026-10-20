# Alpaca-Paperkonto für das lokale Lab vorbereiten

Diese Anleitung bereitet ein Alpaca-Paperkonto für einen menschlich kontrollierten Lab-Ablauf vor. Im Dashboard gibt es jetzt **Übersicht → Demo & Einrichtung → Alpaca einrichten**. Der Dialog speichert die private Konfiguration, prüft aber keine Providerverbindung und aktiviert keinen Handel. Für sichtbare Trades ohne Konto zunächst die [Dashboard-Demo](dashboard-demo.md) verwenden; der echte Paper-Smoke bleibt in [paper-smoke.md](paper-smoke.md) beschrieben.

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

Die normale lokale Startverknüpfung verweigert absichtlich eine Paper-Konfiguration. Ein Neustart allein verbindet das Konto nicht. Der gegenwärtige Paper-Smoke ist ein manueller Einmalablauf mit privaten Terminals und mehreren Schutzprüfungen; führe ihn nur gemäß [paper-smoke.md](paper-smoke.md) und dessen Vorbedingungen aus. Er ist kein autonomer Dashboard-Demo-Modus. Bis ein eigener, geprüfter Ablauf die Kontobindung bestätigt, bleibt die Verbindung unbestätigt und Trading geschlossen.

## Offizielle Quellen

Die Alpaca-Seiten wurden am 18.10.2026 geprüft: [Paper Trading](https://docs.alpaca.markets/us/docs/paper-trading), [Start Paper Trading](https://alpaca.markets/learn/start-paper-trading), [Authentication](https://docs.alpaca.markets/us/docs/authentication) und [Get Account](https://docs.alpaca.markets/us/reference/getaccount-1).
