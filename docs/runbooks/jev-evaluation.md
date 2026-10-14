# Jev-Shadowevaluation

Stand 2026-10-14; PTV-15. Das [Wireprofil](../development/jev-wire-contract.md) benennt die offiziellen Quellen und Modellversion. `config/agents/jev.yaml` bleibt standardmäßig deaktiviert und erlaubt ausschließlich SHADOW. Der Adapter liefert auch bei einer gültigen LONG-Klassifikation tatsächlich ABSTAIN; der Planner erzeugt daraus keinen Vorschlag. Keine Promotion aus diesen Tests ableiten.

## Lokale Prüfung

```powershell
.venv/Scripts/python.exe -m pytest tests/contracts/test_jev.py tests/contracts/test_jev_wire.py tests/integration/test_jev_shadow.py -q
.venv/Scripts/python.exe -m pytest tests/integration/test_jev_usage.py tests/integration/test_ledger.py -q
```

Die ersten Tests benötigen ausschließlich synthetische Antworten über HTTPX MockTransport. Die zweite Gruppe benötigt die vorhandene lokale PostgreSQL-Testinfrastruktur; ihre UUID-Datenbanken werden anschließend entfernt. Sie liest keine Broker-/Jev-Schlüssel. Keine Credentialdateien oder Prozessumgebungen zur Diagnose ausgeben.

`evaluation_summary()` in `tests/integration/test_jev_shadow.py` liefert einen JSON-kompatiblen Bericht. Corpus, Konfiguration und synthetische Eignungslabels besitzen getrennte Hashes. Der identische 100-Fall-Corpus läuft dreimal; 30 veraltete, konfliktbehaftete oder unvollständige Inputs werden vor I/O abgewiesen. 70 zugelassene Fälle erhalten dieselbe synthetische Antwort. Ihre Shadow-Klassifikation wird separat gegen die Baseline verglichen; die tatsächliche Entscheidung bleibt ABSTAIN. Brier und Kalibrierung beziehen sich ausdrücklich auf synthetische Input-Eignungslabels, nicht auf Gewinne oder reale Modellqualität. Kosten bleiben unbekannt, wenn nur Tokenzahlen vorliegen.

## Ausfall und Wiederholung

Timeout, Anbieterfehler, ungültiges JSON, fehlende Frage, niedrige Confidence, schwache Evidenz und Signalkonflikt müssen sichere Enthaltung ergeben. Unbekannte Modellversion, zusätzliche Antwort-IDs, doppelte JSON-Schlüssel, ungültige Verteilungen, abweichende Scorelegende, zu große Antworten und verspätete Ergebnisse werden ebenfalls abgewiesen. Keine automatische Wiederholung oder Umgehung der Validierung einführen. Pause, Risk und Reconciliation besitzen keinen Aufruf dieses Adapters.

Für Cache-Replay stets `JevConfig.replay_config(caller_version)` verwenden. Der gesamte Konfigurationsdigest bindet Modell, Fragen, Rubrik und Schwellenwerte. Veränderter Input oder geänderte Konfiguration darf keine alte Antwort freigeben. Normalisierte Tokenzahlen stehen paarweise auf der unveränderlichen DecisionResult-Zeile; der begrenzte In-Memory-Sidecar ist kein dauerhafter Verbrauchsnachweis.

## Grenzen einer späteren Anbieterprüfung

Reale Providerverfügbarkeit, Latenz, Abrechnung und Kalibrierung bleiben unverified. Eine solche Prüfung benötigt einen eigenen autorisierten Anwendungslauf mit sicherer Secret-Injektion, geeigneten unabhängigen Eignungslabels und separat gespeicherten Originalantworten. Codex fordert keine Schlüssel an und sendet keine Evaluationsrequests. HTTPX begrenzt Transportphasen; die zusätzliche Gesamtfrist verwirft verspätete Antworten, beendet aber keinen beliebig blockierenden synchronen Callback garantiert innerhalb fünf Sekunden. Confidence verändert weder Positionsgröße noch Risk-Limits.
