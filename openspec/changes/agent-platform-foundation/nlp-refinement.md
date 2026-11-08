# Natürlichsprachliche Hypotheseninterpretation

## Problem und Entscheidung

Die bisherige ausschließlich typisierte Extraktion verwirft gerade die Bedeutung, die der Companion erkennen soll. In „Technologieaktien überreagieren nach schlechten Quartalszahlen und erholen sich“ sind die Relation zum Ereignis, die Richtung und der Gegensatz zwischen anfänglicher Bewegung und späterer Erholung entscheidend. Ein Indicator-Formular bildet diese Informationen nicht ab.

| Variante | Eignung und Kosten |
| --- | --- |
| A · Freigegebener externer Ausschnitt | Erhält Satzbedeutung, implizite Zusammenhänge und Mehrdeutigkeit. Benötigt einen zugelassenen Provider, eine eng definierte Datenklasse, lokale Minimierung und eine sichtbare Sendefreigabe pro Turn. Providerverfügbarkeit und Verarbeitungskonditionen bleiben externe Abhängigkeiten. |
| B · Lokales Modell | Kann den Kontext ohne externen Egress semantisch auswerten, solange Modellbetrieb und Telemetrie lokal isoliert sind. Strukturierte Ausgaben sind möglich. Deutsche Semantik, Zuverlässigkeit, Kontextgrenzen, RAM/VRAM, Latenz und verfügbare Hardware hängen vom Modell und Host ab. Modellfingerprint, Sandbox und getrennte Ressourcenbudgets wären eigene Betriebsaufgaben. [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs), [Ollama Context Length](https://docs.ollama.com/context-length). |
| C · Lokale strukturierte Klärung | Offline, deterministisch, billig und für jeden Turn verfügbar. Führt mit verständlichen Fragen zu einer Hypothese. Zeigt explizit, dass komplexe Semantik unaufgelöst sein kann. |

V1 kombiniert A und C. C ist der Standard ohne Sendefreigabe. B bleibt ein Erweiterungspunkt hinter demselben Interpretenvertrag und denselben Output- und Autoritätsgrenzen. Es gibt keinen automatischen Wechsel des Verarbeitungswegs. Externe Anbieter und das spätere lokale Modell sind untrusted. Keine produktive Variante wird durch diesen Change aktiviert.

## Beispiel · Einsteigerdialog

1. Der Betreiber schreibt: „Ich glaube, dass Technologieaktien nach schlechten Quartalszahlen häufig überreagieren und sich anschließend erholen. Vielleicht könnte man daraus eine Strategie machen.“
2. Guided bietet „Lokal klären“ und, nur bei zulässigem Profil, „Sendekontext ansehen“ an. Die Vorschau zeigt den vollständigen minimierten Forschungsinhalt, den Empfänger und den Zweck. Der Betreiber gibt genau diesen Turn frei.
3. Der Companion antwortet: „Du vermutest, dass Technologieaktien nach enttäuschenden Quartalszahlen zunächst stark fallen und danach einen Teil des Rückgangs aufholen. Was soll als Enttäuschung zählen: ein Ergebnis unter den Erwartungen oder ein Rückgang gegenüber dem Vorjahresquartal?“
4. Der Betreiber bestätigt zunächst Markt, Quartalsereignis und vermutete Gegenbewegung als einzelne Bezugspunkte. Diese Zwischenentscheidung ist weder die finale Hypothesenbestätigung noch eine Sendefreigabe. Dann präzisiert er: „Unter den Erwartungen, und die Erholung soll innerhalb der nächsten Woche liegen.“ Eine neue Freigabe zeigt diesen minimierten Ausschnitt und ausschließlich notwendige, bereits bestätigte strukturierte Felder. „Nächste Woche“ bleibt eine offene Auslegung, bis der Betreiber zum Beispiel fünf Handelstage bestätigt.
5. Die Ansicht zeigt Ereignis, Signal, Horizont und offene Messdefinition getrennt. Die fachliche Bestätigung bindet die ausgewählte Interpretation. Frühere Versionen bleiben lesbar, ihre Bestätigungen und Freigaben gelten nicht für die Nachfolgeversion. Der lokale Quant-Abgleich erklärt, dass zeitpunktgebundene Ergebnis-/Konsensdaten und eine ereignisbasierte Strategie fehlen. Er zeigt die Datenlücke und den Strategiemangel gesondert. Ein RSI-Test kann allenfalls als klar abgegrenzte Ersatzhypothese angeboten werden, sofern er für eine vom Betreiber gewählte andere Frage fachlich geeignet ist. Die ursprüngliche Idee bleibt erhalten.
6. Bei Ablehnung der Übertragung fragt der lokale Weg nach Ereignis, Richtung und Messfenster und zeigt, welche Eingaben noch keine strukturierte Bedeutung haben. Der Betreiber kann über alltagssprachliche Auswahlfelder, eigene Bezeichnungen und offene Werte fortfahren. Dieselben Testbarkeitsregeln gelten.

## Vertrauensgrenzen und Speicherplan

Control/Research besitzt den Privacy-Projektionsdienst. Er prüft Eingabe und Herkunft lokal, bildet aus einem vom Betreiber verfassten Forschungsabschnitt und den erforderlichen, ausdrücklich sichtbaren typisierten Feldern die kanonische Projektion. Er darf Beziehungen im Ausschnitt erhalten, ohne Wörter in vorgefertigte Strategieparameter zu zwingen. Keine automatische Zusammenfassung vor dem Modell. Unsichere Herkunft oder Klassifikation verhindert eine Vorschau zur Sendung. Der Nutzer kann eine eigenständige, bereinigte Formulierung vorlegen oder lokal weiterarbeiten. Der Nutzer kann gesperrte Klassen nicht überstimmen.

Das Gateway besitzt keinen Dialogspeicher. Es prüft die versiegelte Projektion und sendet sie nach privater Supervisor-Zulassung mit ausschließlich gebundenem statischem Systemtext, Modellparametern und Ausgabeschema. Die Vorschau zeigt alle dynamischen Forschungsfelder und nennt die feste technische Hülle. Eine Normalisierung oder Neuprojektion nach der Freigabe erfordert eine neue Vorschau. Die Provideranfrage ist zustandslos und referenziert keine Provider-Konversation.

| Objekt | Haltung |
| --- | --- |
| Zulässige Originalidee | Zugriffsgeschützt im lokalen Dialog, bei unsicherem Inhalt ausschließlich typisierte lokale Klärung ohne Übernahme des Eingabetextes. |
| Minimierte Sendefassung | Kurzlebig im Speicher, höchstens zehn Minuten und bis zur Nutzung oder Invalidierung. Kein vollständiger Providerprompt in Datenbank, Outbox, Audit oder Browserpersistenz. |
| Sendefreigabe | Dauerhaftes Receipt aus ID, Zeit, Status, Zweck, Empfänger, Policy-/Profilgeneration und opaker Bindung. Keine rekonstruierbaren Textkopien. |
| Modellvorschlag | Nur validierte und begrenzte strukturierte Darstellung. Rohantworten werden verworfen. Vorschlag bleibt getrennt von Betreiberentscheidung und bestätigter Hypothese. |
| Bestätigte Hypothese und Quant-Draft | Eigene unveränderliche Versionen mit getrennten Referenzen. Änderungen erzeugen neue Versionen und erfordern Bestätigung der geänderten fachlichen Bindung. |

Die technische Prüfung dokumentiert und erzwingt zulässige Datenklassen. Sie stellt keine Rechtsgrundlage her und kann Secret-/PII-Erkennung nicht garantieren. Vor produktiver Aktivierung sind Verarbeitungszweck, Empfänger, Konditionen und Rechtsgrundlage gesondert zu belegen.

## Paketgrenzen

APF-01 definiert die gemeinsamen Verträge. APF-02 und APF-03 prüfen Lauf und Gateway mit künstlichen gebundenen Projektionen. APF-04 prüft die fachliche Dialog- und Interpretationsgrenze gegen den Vertragsport mit einem Testdouble. APF-08 liefert die produktionsförmige Privacy-Projektion und verbindet die vorhandenen Ports. Dadurch entsteht keine Rückwärtsabhängigkeit von APF-04 zu APF-08. APF-09 und APF-10 prüfen die vollständige Kette über die reale lokale Control API.
