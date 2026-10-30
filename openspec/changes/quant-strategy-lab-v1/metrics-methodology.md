# Performance-Methodik v1

Normativ für REQ-QCO-002. Methodenversion quant-metrics-v1. Keine Kennzahl ist eine Zukunftsprognose, Gewinnwahrscheinlichkeit oder Handelsfreigabe. raw/IEX/15Min, USD, price-only, keine Dividendenreinvestition; IEX ist kein konsolidiertes Marktportfolio.

## Eingaben und numerische Regeln

Ausschließlich vollständige bestehende SimulationResult mit Startcash E0>0, zeitlich geordneten Equitypunkten, Fills und exakt gebundenem Kalender. Equity umfasst Kosten bereits; Gebühren/Slippage niemals nochmals von Return abziehen. Offene Endpositionen sind mark-to-market und nicht liquidiert. Decimal precision38, ROUND_HALF_EVEN, feste Formelreihenfolge. sqrt mit Decimal.sqrt im selben Kontext; keine floats. Endausgabe Return/Ratios/Volatilität/Turnover auf12 Nachkommastellen half-even; Geld auf bestehende8. Zwischenwerte nicht quantisieren. Ausgabe: value als Decimalstring oder null, status, reasonCode, observationCount, frequency, methodVersion, limitations. Null/N/A ist kein Nullmesswert.

## Kernmetriken

| Metrik | Verbindliche Berechnung | Minimum / Sonderfälle |
|---|---|---|
| Nettorendite | E_last/E0-1 | mindestens1 gültiger Equitypunkt; keine Trades bei konstantem Cash ergibt echte0 |
| Maximum Drawdown | max_t(1-E_t/max(E0,E_1..E_t)), Startcash gehört dazu | mindestens1 Equitypunkt; flat ergibt echte0 |
| Turnover | sum(abs(fill.notional))/E0 | null Fills ergibt echte0; nicht pro Jahr umdeuten |
| Kosten | Fees=sum(fee); Slippage=sum(slippage_cost); Total=Fees+Slippage; CostBurden=Total/E0 | null Fills ergibt echte0; Bestandsgeldrundung erhalten |
| Counts | decisionCount, fillCount, equityBarCount | Fillanzahl heißt nicht Tradeanzahl; keine Roundtripzählung in v1 |
| Benchmarkdifferenz | NettoReturn_Kandidat-NettoReturn_buyHold sowie zu flat auf derselben Phase | nur bei komplett identischen Vergleichsbindungen; Prozentpunkte, kein Alpha oder Total Return |
| Tägliche Volatilität annualisiert | r_d=E_close_d/E_close_(d-1)-1; für ersten Tag E0 als Vorgänger; mean=sum(r)/N; s²=sum((r-mean)²)/(N-1); sqrt(s²)*sqrt(252) | N>=20 vollständige bestätigte Handelssitzungen; Phase muss am ersten Sitzungsbeginn starten und am letzten Sitzungsende enden; jeder erwartete 15Min-Close vorhanden, sonst N/A INCOMPLETE_DAILY_SERIES |
| Sharpe | (mean/sqrt(s²))*sqrt(252), risikofreier Tageszins fest0 | dieselbe Tagesreihe, N>=20; s²=0 ->N/A ZERO_VARIANCE, auch bei positiver Rendite; keine Verwendung als Auswahlkriterium |
| OOS Degradation | mean(r_validation)-mean(r_oos), positiv bedeutet niedrigere tägliche mittlere OOSrendite | nur für denselben eingefrorenen Kandidaten mit zwei gültigen Tagesreihen N>=20 je Phase; keine Division durch kleine/negative Validierungsrendite |

Die Tagesdefinition schließt Overnight-Änderungen im Return zum nächsten Sitzungsclose ein, niemals Nachtbars erfinden. Bestätigte Feiertage werden ausgelassen; Early-close-Sitzungen zählen als eine Sitzung. 252 ist eine vereinfachende versionierte Konvention für das eng begrenzte US-ETF-Universum, kein aus15Min abgeleiteter Frequenzfaktor. Teilphasentage werden nicht still entfernt: die täglichen Kennzahlen sind dann N/A. Signal-/Equitygrenzen und ihre +60s/+300s-Beobachtungsregeln bleiben unverändert; Kalenderende bezieht sich auf die Marktbar, nicht auf Ingestionzeit.

Bei E<=0 an irgendeinem Equitypunkt: neue Verhältnis-/Tagesmetriken N/A NON_POSITIVE_EQUITY, keine ln/Division/Infinity. Der bestehende Long/Flat-Simulator sollte dies bereits verhindern; der adversariale Analyticstest prüft defensive Behandlung. Ungültige/nicht endliche Eingangswerte liefern INVALID_INPUT, nicht einen scheinbar erfolgreichen Report. Bei BLOCKED/FAILED nur ausdrücklich vollständige Phasen sichtbar, kein Gesamtwert.

OOS-Degradation wird ausschließlich zwischen gepaarter Validation- und Testphase desselben Folds für dessen gewählten Kandidaten berechnet. Für den finalen Holdout ist sie N/A NO_PAIRED_VALIDATION; die Auswahl über mehrere Validationsmediane erzeugt keine einzelne zugehörige Tagesreihe.

## Fold- und Suchdarstellung

Jede Training/Validation/Testphase wird separat berechnet. Protokoll besitzt unabhängig gestartete Folds; sie bilden kein investierbares durchgehendes Portfolio. Aggregierte Walk-forward-Sicht: vollständige Anzahl, Auswahl-IDs, arithmetischer Median/Minimum/Maximum der OOS-Nettorenditen und Drawdowns, Anzahl positiver OOS-Folds, Range als deskriptive Instabilität. Keine verkettete Rendite, kein gemittelter Sharpe, kein Konfidenzintervall oder p-Wert. Bei weniger als3 vollständigen OOS-Folds Status SMALL_FOLD_SAMPLE; Rohwerte bleiben sichtbar, keine statistische Stabilitätsbehauptung. Finaler Holdout separat, niemals in die Validationauswahl oder den Foldmedian mischen.

Sensitivität zeigt vollständige Validationzellen mit Parametern und Metriken, keine interpolierten/unberechneten Werte. Foldübergreifender Validationsmedian nach D3. Fehlende Werte sind fehlende Zellen; keine Nullfüllung. Training/OOS besitzen getrennte Anzeigen und kein gemeinsames Ranking. Tests und finaler Holdout sind für nicht gewählte Kandidaten NOT_EVALUATED_BY_PROTOCOL.

## Bewusst zurückgestellt

Sortino, Calmar/CAGR, Profit Factor, Win Rate, Roundtrip Trade Count, probabilistischer/deflationierter Sharpe, Multiple-Testing-Signifikanz und Total Return. Dafür fehlen teilweise definierte Tradepaarung, Jahreszeitbasis und ausreichende Stichprobe; sie werden nicht als künstliche Nullfelder angeboten. Kosten/Drawdown/Turnover und deterministische Validationauswahl bilden den v1-Kern; hohe Sharpe kann Reliabilitymängel nicht aufheben.

## Unabhängige Orakel

Handfixture E0=1000, E=[1000,900,990] ->Return=-0.01, MaxDD=0.1. Fees2, Slippage3 ->Kosten5, CostBurden0.005. Notional100+90 ->Turnover0.19. Konstante20-Tages-Cashreihe ->Volatilität0, Sharpe N/A ZERO_VARIANCE. Tagesreturns [0.01,-0.01,0.02,-0.02] zyklisch fünfmal ->mean0, Summe Quadrate0.005, Varianz0.005/19, Sharpe0; Volatilität=sqrt(0.005/19)*sqrt(252). Equity dazu unabhängig mit E_d=E_(d-1)*(1+r_d) konstruieren; Hand-/Rationalorakel ohne Aufruf des Produktrechners. 19 Tage ->INSUFFICIENT_OBSERVATIONS, 20 Tage Grenze gültig. Referenzen prüfen hohe Gebühren, negative Mittelwerte, Earlyclose/DST, fehlende Tagesbars, null Equity und unterschiedlich lange Val/OOSreihen.
