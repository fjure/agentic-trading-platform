# Strategy Selection Policies v1

Normativ für REQ-QEX-007; ergänzt D3. Reine geschlossene Selektoren, keine ausführbaren Ausdrücke, Zielgewichte oder Frontendformeln. Strategy Selection ist von der bestehenden **Datenselektionspolicy** StrictPitPolicyV1/RetrospectivePolicyV1 verschieden; deren Feld `selection_policy_version` und Legacyhashes bleiben unverändert. Neue Bindung heißt `strategySelectionPolicy`.

## Problem und Alternativen

Drawdown-first bevorzugt bei (Return=0.005, DD=0.01) gegen (0.12, 0.08) immer den ersten Kandidaten; Flat mit DD=0 kann dominieren. Das ist eine Kapitalerhaltungspräferenz, keine allgemeine Researchoptimierung.

| Alternative | Bewertung und Entscheidung |
|---|---|
| A: Nettorendite unter Risikobedingungen | Standard. Direkter, kostenbereinigter Ertrag auf identischem Startcash und identischen Fenstern; keine instabile Quotientenbildung. Drawdown ist eine historische Zulässigkeitsbedingung, kein zukünftiges Risikoversprechen. |
| B: Sharpe oder Return/Drawdown | In v1 kein Selektor. 20 Sitzungen sind keine statistische Absicherung; zeitliche Abhängigkeit, geringe Exposition und annualisierte Schätzer können täuschen. Nullvarianz/Null-DD erzeugen keinen unendlichen Score. Negative Returns und kleine Nenner machen Quotienten schwer interpretierbar. Sharpe bleibt deskriptiv mit N/A ZERO_VARIANCE/INSUFFICIENT_OBSERVATIONS; Return/DD wird nicht eingeführt. Keine Ersatznull, kein Ausweichen auf eine andere Zielfunktion bei N/A. |
| C: Drawdown-first | Als ausdrücklich wählbare CAPITAL_PRESERVATION_DRAWDOWN_FIRST_V1 erhalten. Flat kann bewusst gewinnen; UI nennt das Kapitalerhaltungsziel und die mögliche Inaktivität. Nicht Standard und kein universelles Qualitätsurteil. |

Geringe Exposition ist eine eigenständige Beschreibung, kein automatisch schlechter Score. Returns werden weder durch investierte Zeit geteilt noch auf Vollinvestition hochgerechnet. Nettorendite enthält bereits Gebühren und Slippage des gemeinsamen Simulators. Cash als Flat ist ein zulässiger historischer Ausgang; eine positive Rendite ist keine Pflichtbedingung und negative Kandidaten werden nicht verschwiegen.

## Geschlossene Konfiguration und Bindung

`strategySelectionPolicy` enthält genau `policyId`, `maxDrawdown`, `minValidationSessions`, `minimumEntryCount`, `minimumInvestedBarRatio`. `policyId` ist einer der beiden vollständigen Versionsnamen unten. `maxDrawdown` ist ein erforderlicher endlicher Decimalstring 0..1 einschließlich; kein universeller versteckter Grenzwert. `minValidationSessions` ist eine erforderliche exakte ganze Zahl 20..252, keine bool. Mindestaktivität ist je Feld explizit null (=deaktiviert) oder `minimumEntryCount` eine ganze Zahl 0..100000 bzw. `minimumInvestedBarRatio` ein Decimalstring 0..1. Decimalparameter höchstens zwölf Nachkommastellen; unbekannte Felder, NaN, Infinity, fehlende Pflichtfelder und ungültige Typen werden vor Jobanlage abgewiesen. Standard ist die Policy-ID MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1; der Builder verlangt die sichtbare Bestätigung aller Parameter, insbesondere der Risikogrenze. Eine spätere Änderung erzeugt eine neue unveränderliche Experimentversion vor neuem Start, niemals eine Änderung des laufenden Protokolls.

Definitionhash und quant-experiment-v1-Manifest binden Policy-ID/-Version, sämtliche Parameter einschließlich null, Methoden-/Selektorfingerprint, Kandidatenmenge, Phasen und den Forschungsprotokollstand. Selectionidentität bindet zusätzlich Scope (Fold/final), vollständige Validationinputhashes und Ergebnis. Andere Policy-ID oder anderer Parameter verändert Definition-/Manifest-/Selectionidentität auch bei identischem Gewinner. Recovery benutzt dieselben Bindungen, keine erneute Parametereingabe.

## Zulässigkeit vor Ranking

Zuerst vollständige Suchmenge prüfen: ein fehlgeschlagener, blockierter, fehlender oder ungültig berechneter Kandidat beziehungsweise Pflichtwert verhindert jede Auswahl im Batch (BLOCKED/FAILED, INCOMPLETE). Keine Entfernung aus dem Nenner der Suche. N/A einer **nicht benötigten** deskriptiven Sharpe blockiert dagegen nicht den kosten-/drawdownbasierten Selektor.

Für jede vollständige Kandidat/Validation-Zelle gelten:

1. Bestandsdaten-/Reliabilitygates sind erfüllt; sämtliche erwarteten Seed-, Entscheidungs- und Outcome-Bars liegen vollständig vor (Vollständigkeit exakt 1, nicht konfigurierbar absenkbar). Kalender/Snapshot müssen nachgewiesen sein; hohe Rendite kompensiert keine Sperre.
2. Mindestens `minValidationSessions` vollständige bestätigte Handelssitzungen innerhalb der Validation. Eine Sitzung zählt nur bei vollständig enthaltenem Sitzungsbeginn/-ende und allen erwarteten Bars; Randteilsitzungen liefern keine Sitzung, bleiben aber in Return/DD enthalten. Es werden keine fehlenden Bars entfernt. Das ist ein Mindestumfang, keine Zahl unabhängiger statistischer Beobachtungen.
3. Status VALID für Nettorendite, Maximum Drawdown und Turnover nach Methodik; keine günstigen Ersatzwerte. Nichtpositive Equity/ungültige numerische Inputs sind unvollständige/ungültige Forschung und keine normale Grenzverletzung.
4. `max_drawdown <= maxDrawdown`, exakt einschließlich Gleichheit. Drawdown ist eine nichtnegative Verlustquote (0.08 = 8%). Vergleich benutzt die deterministischen unquantisierten Decimal38-Werte der QSL-Methodik, vor Darstellungsrundung. Exakte kanonische `selectionValues` werden zusätzlich zu den zwölfstelligen Anzeigewerten publiziert und gehasht; Legacy-FoldMetrics bleiben unverändert. Auch DD0.1000000000004 verletzt Limit0.1, obwohl die Anzeige auf0.100000000000 rundet.
5. Sofern aktiviert: `entryCount >= minimumEntryCount` und `investedBarRatio >= minimumInvestedBarRatio`, ebenfalls einschließlich Gleichheit und vor Darstellungsrundung. Ohne diese expliziten Parameter besteht kein Aktivitätsfilter und kein Aktivitätsbonus.

Unzureichender vollständiger Stichprobenumfang oder eine verletzte konfigurierte Risiko-/Aktivitätsgrenze ergibt INELIGIBLE mit allen Gründen und Messwerten, nicht FAILED. Finale Zulässigkeit verlangt sämtliche Bedingungen in **jeder** Validationphase; ein guter Median darf keinen schlechten Fold verdecken. Referenzen flat/buy-hold sind nie automatisch Suchkandidaten. Explizite identische Suchkandidaten werden wie alle anderen behandelt.

## Reihenfolge und finales Ergebnis

| Policy | Foldweise Vergleichsschlüssel, aufsteigend lexikographisch |
|---|---|
| MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1 | (-net_return, max_drawdown, turnover, candidateId) |
| CAPITAL_PRESERVATION_DRAWDOWN_FIRST_V1 | (max_drawdown, -net_return, turnover, candidateId) |

Finaler Selektor benutzt dieselbe Policy auf den komponentenweisen Medianen der vollständigen Validationmetriken des Kandidaten über alle Folds, nach Prüfung **aller** Einzelgrenzen. Median bei gerader Anzahl = (unterer+oberer Mittelwert)/2 in Decimal38, ohne Zwischenquantisierung vor Vergleich; kanonischer exakter Selektionswert plus zwölfstellige half-even-Anzeige. Kein annualisierter Return; innerhalb eines Folds identische Fenster, final gleiche Foldmenge je Kandidat. Unterschiedliche Foldlängen und der Median sind bewusst deskriptiv; keine optimale Portfoliogewichtung oder statistische Robustheitsgarantie. Turnover ist nur später Tie-Breaker, kein Aktivitätsanreiz. Kandidaten-ID ist der kanonische SHA256 als kleingeschriebener Hexstring, Vergleich ASCII.

Auswahlstatus SELECTED enthält Gewinner-ID; NO_ELIGIBLE_CANDIDATE enthält `selectedCandidateId=null`. Keine Ersatzwahl, kein Rückfall auf Flat/buy-hold oder eine andere Policy. Ohne Foldgewinner keine Kandidaten-OOS-Simulation in diesem Fold; ohne finalen Gewinner keine Kandidaten-Holdout-Simulation. Feste Referenzen dürfen weiterlaufen; Kandidatenstatus NOT_EVALUATED_NO_SELECTION ist von NOT_EVALUATED_BY_PROTOCOL für nicht gewählte Kandidaten verschieden. Ein vollständig durchgeführtes Protokoll einschließlich dokumentierter Nichtauswahl und Referenzen kann SUCCEEDED sein; technische/data Fehler bleiben BLOCKED/FAILED. Fehlende OOS-Degradation erhält NO_SELECTED_CANDIDATE, nicht null als Rendite. Alle Fold- und finale Entscheidungen werden vor **irgendeiner** OOS-Berechnung atomar eingefroren; OOS ist kein Selektorinput.

Jede Entscheidung enthält Policybindung/hash, vollständige Kandidatenzahlen/-IDs, je Kandidat alle geprüften Gates (Grenzwert, Wert, Status, Gründe), Foldmetriken, finale Mediane, sortierte Vergleichsschlüssel, Sieger oder Nichtauswahl und Validationinputhash. Budget-/Seiten-/12MiB-Grenzen gelten auch für diese Evidenz. Fehlgeschlagene Kandidaten bleiben sichtbar.

## Numerische Akzeptanzorakel

Alle folgenden Werte sind Validation, vollständige 20 Sitzungen und identische Kosten-/Datenbasis; DD-Limit0.10, minValidationSessions20, Aktivitätsminima null, sofern nicht anders angegeben.

| Fall | Eingabe / erwartetes Ergebnis |
|---|---|
| S1 Return/DD-Abwägung | A Return0.005/DD0.01, B Return0.12/DD0.08 -> Standard B; Kapitalerhaltung A. |
| S2 Flat/aktiv | Flat0/0/Turnover0, B0.12/0.08 -> Standard B, Kapitalerhaltung Flat; kein versteckter Aktivitätsfilter. Alle aktiven Kandidaten negativ -> Flat darf Standard gewinnen, falls explizites Suchmitglied. |
| S3 Negative Returns | A -0.03/DD0.05, B -0.01/DD0.08 -> Standard B; Aussage ausdrücklich negative historische Nettorendite. |
| S4 Grenze | Return0.20/DD0.100000000001 -> INELIGIBLE; DD0.100000000000 -> zulässig. DD0.1000000000004 ist ebenfalls unzulässig trotz gerundeter Anzeige0.100000000000; Auswahl vor Darstellungsrundung. |
| S5 Keine Wahl | Alle DD0.11 bei Limit0.10 -> NO_ELIGIBLE_CANDIDATE, null ID, keine Kandidaten-OOS-Rechnung. |
| S6 Nicht benötigte Sharpe | Flat mit ZERO_VARIANCE oder 20 vollständige Sitzungen plus Teilrandtag mit INCOMPLETE_DAILY_SERIES -> kein künstlicher Score; gültige Return/DD-Auswahl bleibt möglich. Fehlende Pflicht-DD dagegen blockiert. |
| S7 Tie | Gleicher Return/DD/Turnover -> kleinste kanonische ID, unabhängig von Eingabereihenfolge/Restart. |
| S8 Bindung | Nur Policy-ID oder Limit oder optionales Minimum ändern -> anderer Definition-/Manifest-/Selectionhash; laufende Änderung409. |
| S9 Isolation | Beliebige OOS-/Holdoutreturns nach Einfrieren verändern -> Auswahl/Selektorinputhash unverändert; OOS-Portaufruf durch Selektor abgewiesen. |
| S10 Alle Folds | A DD=[0.01,0.19], B=[0.08,0.08], Limit0.10 -> A final INELIGIBLE trotz Median0.10; B zulässig. Vollständige Renditen A=[0.20,0.20], B=[0.10,0.12] -> B final Medianreturn0.11. |
| S11 Stichprobe/Aktivität | 19 vollständige Sitzungen -> INELIGIBLE; 20 -> Umfang erfüllt. Flat mit minimumEntryCount1 -> explizit INELIGIBLE; mit null zulässig. Ratio genau Minimum zulässig, einen Decimal38-Schritt darunter unzulässig. |
| S12 Fehler vs Nichtwahl | Ein FAILED-Kandidat unter neun -> keine Auswahl/kein vollständiges Ranking; acht schöne Ergebnisse dürfen nicht gewinnen. Vollständig berechnete neun Grenzverletzer dagegen dokumentierte Nichtwahl. |

Diese Orakel sind Testanforderungen für Apply, keine Behauptung neuer ausgeführter Produkttests.
