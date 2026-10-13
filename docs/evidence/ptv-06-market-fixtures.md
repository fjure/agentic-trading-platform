# PTV-06: Evidenz für synthetische Marktfixtures

Die Paper-Fixture verwendet einen auf 2026 begrenzten Kalender der regulären
NYSE-Sitzung in `config/markets/paper.yaml`. Die [offiziellen Handelszeiten und
Feiertage der NYSE](https://www.nyse.com/trade/hours-calendars) nennen 09:30 bis
16:00 Uhr Eastern Time als Kernhandelszeit, zehn Börsenfeiertage für 2026 sowie
einen Schluss um 13:00 Uhr Eastern Time am 27. November und 24. Dezember. Die
Quelle wurde am 12.10.2026 geprüft. Die Konfiguration enthält diese Tage
ausdrücklich und sperrt Aktiensitzungen außerhalb des Jahres 2026.

Die IANA-Zeitzone `America/New_York` bildet beide Zeitumstellungen des Jahres
2026 ab. Tage außerhalb der Kalenderabdeckung, Wochenenden, Feiertage,
Entscheidungen nach Sitzungsschluss sowie fehlende, doppelte, falsch sortierte,
nicht zum 15-Minuten-Raster passende, zukünftig verfügbare oder veraltete
Kerzen sperren den gesamten Marktstatus. Ein zu großer Spread sperrt ihn
ebenfalls. Die Fähigkeiten der Fixture weisen News, Papers und einen externen
historischen Datenfeed ausdrücklich als nicht verfügbar aus. Die synthetische
Quelle dient reproduzierbarem Replay und belegt keine Live-Datenberechtigung.

Das benötigte Fenster umfasst zwei abgeschlossene 15-Minuten-Kerzen. Nach der
Verfügbarkeit der jüngsten Kerze beträgt die Frischegrenze 120 Sekunden.
Evidenz- und Marktstatus-IDs hängen von sämtlichen Eingabewerten, der
Konfigurationsversion und einem kanonischen Hash des Konfigurationsinhalts ab.
Eine korrigierte Kerze oder geänderte Einstellung erzeugt daher eine neue,
unveränderliche Evidenzidentität. Präzisionskritische Summen und die
Spreadgrenze werden mit ausreichender Decimal-Präzision berechnet; Werte
außerhalb `NUMERIC(38,18)` werden abgelehnt. Normale CI nutzt ausschließlich
diese synthetischen Fixtures; es gibt keinen Broker- oder Orderaufruf.

Die gezielten Tests in `tests/unit/test_market_state.py` decken T-MKT-001..008
ab: beide Zeitumstellungen, Feiertag, Halbtag, 15-Minuten-Raster, Look-ahead,
veraltete Daten, Lücken, Duplikate, Spread, Quellenfähigkeiten, geänderte
Kerzen und Konfiguration sowie Decimal-Grenzwerte. Pytest und Ruff wurden
erfolgreich ausgeführt.
