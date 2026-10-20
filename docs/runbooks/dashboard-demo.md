# Demo im Dashboard testen

Stand: 20.10.2026. Die Demo arbeitet mit synthetischen Kursen und FakeBroker, ohne Alpaca, Jev oder Zugangsdaten. Sie prüft den tatsächlichen Entscheidungs-, Risk-, Order-, Fill- und Ledgerpfad. Das ist eine Funktionssimulation, kein Nachweis einer profitablen Strategie.

## In wenigen Klicks

1. Falls nötig `scripts/start-local-lab.ps1` ausführen und [http://127.0.0.1:8000](http://127.0.0.1:8000) öffnen. Nach einem Neubau die Seite vollständig neu laden. Falls die API neu gestartet wurde, erneut mit dem privaten Einmalcode gemäß [README](../../README.md) anmelden.
2. In **Übersicht** den Abschnitt **Demo & Einrichtung** öffnen.
3. **Startbudget (USD)** auf beispielsweise `100` setzen und **Mock neutral** auswählen.
4. **Demo starten** anklicken und auf **Demo-Ergebnis: SUCCEEDED** warten.
5. Demo-Order, Demo-Cash, Position sowie die aufklappbaren Entscheidungs-, Risiko- und Auditbelege prüfen. Seite neu laden: Derselbe Lauf und seine Ergebnisse bleiben erhalten.

Der feste synthetische Erfolgsfall kauft einen Anteil zum Demopreis von 5 USD: Bei 100 USD Startbasis verbleiben 95 USD Cash und eine Position mit Menge 1. Der Preis ist ausdrücklich kein aktueller Börsenkurs. Der Lauf verarbeitet Teil- und Endfill durch die echten Anwendungskomponenten und bleibt anschließend pausiert. Die normalen Portfolio-/Modusanzeigen außerhalb des Demoabschnitts gehören weiterhin zum separaten Laborledger.

## Budget und Varianten vergleichen

Für einen weiteren unabhängigen Lauf ein anderes Budget, etwa `200`, oder **Deterministic neutral baseline** wählen und erneut starten. Jeder Lauf besitzt seine eigene finanzielle Datenbasis, Run-ID und Trace-ID. Unter **Vorherige Läufe** lassen sich die letzten 100 Läufe erneut auswählen. Es wird kein bestehender Cashbestand überschrieben und kein Volume gelöscht.

Die beiden Varianten sind die vorhandene Mock-Testvariante und deterministische neutrale Baseline. Sie sind keine frei programmierbaren Strategien. FLAT oder ABSTAIN erzwingt keinen Trade. Sehr kleine Beträge, beispielsweise `0.01`, können die normale Risiko-/Mindestmengenkontrolle nicht erfüllen: Der Lauf endet dann **FAILED**, ohne Order und weiterhin pausiert. Ungültige Beträge oder EUR werden abgewiesen; es findet keine automatische Währungsumrechnung statt.

Während **RUNNING** stehen **Demo pausieren** und **Demo stoppen** zur Verfügung. Diese Aktionen betreffen ausschließlich den ausgewählten Demolauf. Ein Stop bleibt gesetzt und wird durch erneutes Anklicken nicht aufgehoben; ein neuer unabhängiger Lauf verändert den alten nicht.

## Alpaca und Jev vorbereiten

Im selben Abschnitt befinden sich **Alpaca einrichten** und **Jev einrichten**. Die Dialoge nehmen Schlüssel ausschließlich als kurzfristige Eingabe entgegen; Felder werden beim Absenden, Abbruch und Schließen geleert. Gespeicherte Schlüssel werden nicht wieder angezeigt.

- Alpaca: Paper-Key, Paper-Secret und erwartete Account-UUID eingeben. Details zur Kontoerstellung und UUID stehen in [Alpaca einrichten](alpaca-setup.md).
- Jev: den vorhandenen Jev-API-Key eingeben und speichern. Die Demo benötigt Jev nicht.

**Gespeichert – Verbindung noch nicht geprüft** bedeutet genau das: keine Providerabfrage, keine automatische Aktivierung und keine Handelsfreigabe. Nur Execution kann die Alpaca-Datei als Brokerkonfiguration verwenden; nur Worker kann die Jev-Datei verwenden. Die eigentliche Paper-Aktivierung bleibt der gesonderte menschliche [Paper-Smoke-Ablauf](paper-smoke.md); ein bloßer Dienstneustart oder gespeicherter Key reicht nicht.

## Paperbudget

**Paper-Budget (USD)** ist eine zusätzliche Obergrenze für gehaltenes und reserviertes Exposure. Der angezeigte Vorgabewert ist erst nach **Paper-Budget speichern** wirksam. Er verändert weder den tatsächlichen Brokerkontostand noch die eingefrorene Kontobasis. Bestehende Risikolimits können dadurch nur strenger werden. Eine Senkung unter bestehendes Exposure oder Änderungen bei ungeklärten Orders werden abgewiesen.

Ein Budget von 100 USD ist zulässig, garantiert aber keinen handelbaren Auftrag: Instrumentpreis, ganze Stückzahlen und die übrigen Risikogates gelten weiterhin. Nicht unterstützte Bruchteile oder Market-Fallbacks werden nicht erzwungen. Ein gespeichertes Budget aktiviert keinen Handel.

## Wenn nichts erscheint

- **Demo & Einrichtung fehlt:** Seite vollständig neu laden; gegebenenfalls `scripts/start-local-lab.ps1` ohne `-SkipBuild` ausführen.
- **Dienst nicht verfügbar:** Docker Desktop prüfen und den Launcher erneut ausführen. Der Launcher startet auch `demo` und `provider-setup`.
- **Anmeldung erforderlich:** Nach einem API-Neustart ist ein neuer privater Einmalcode nötig; der Launcher erzeugt ihn nicht automatisch.
- **FAILED:** Fehlerstatus und vorhandene Belege prüfen; keine Gates umgehen. Zum Funktionsvergleich einen neuen expliziten Lauf mit 100 USD und Mock neutral verwenden.

Automatisierte echte Browser-/API-/PostgreSQL-Abnahme: `scripts/test-demo-browser.ps1`. Das prüft ausschließlich eigene Testressourcen und synthetische Schlüssel. Weitere Evidenz: [Demo-Browserabnahme](../evidence/demo-04-browser.md).
