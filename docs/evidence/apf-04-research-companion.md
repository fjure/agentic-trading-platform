# APF-04 · Versionierte Ideen und Dialoge

Die Arbeit baut auf `42f0c9ef27bf636c885fc4d0dbe983b68d66722c` auf. Migration 0042 legt die versionierte Companion-Historie im Research-Schema an. Die Research-Rolle hat für die neuen Tabellen ausschließlich Lese- und Einfügerechte. Der Store bindet Zugriff und Schreibschlüssel an den authentifizierten Betreiber und serialisiert Änderungen pro Idee. Ein unveränderter Replay liefert das gespeicherte Ergebnis. Eine geänderte Bindung unter demselben Schlüssel erzeugt einen Konflikt.

Die lokale Prüfung findet vor jeder Übernahme von Originaltext statt. Originale liegen getrennt von Dialogzustand und Audit. Der Dialog speichert Referenzen, datenschutzgeprüfte Vorschläge und explizite Bestätigungen mit Versionsbindung. Die strukturierte Klärung benötigt keinen Provider. Eine bestätigte Annahme bleibt erhalten, bis der Betreiber eine Änderung ausdrücklich übernimmt. Der Expertenverweis bindet die unveränderliche Quant-Definition. Modellvorschläge können einen Widerspruch anzeigen und die gebundenen Werte nicht ändern.

Die Offline-Sendevorschau übernimmt ausschließlich einen erlaubten Ausschnitt des aktuellen Originals und ausgewählte bestätigte Felder. Der Supervisor prüft den Turn beim Bestätigen, beim Dispatch und vor der Vorschlagsannahme erneut. Die Modellantwort durchläuft den geschlossenen V1-Vertrag und die lokale Datenpolicy. Die Annahme des Vorschlags und die Modellabrechnung erfolgen in derselben Transaktion. Ein separater Startversuch erzeugt nur einen redigierten Ablehnungscode. Ein Modellvorschlag verändert weder die Ideenfassung noch eine bestätigte Annahme. Erst eine eigene Betreiberentscheidung erzeugt eine neue Ideenfassung.

Die deterministische Bewertung liest bestätigte Felder und die gebundene Katalogversion. Sie unterscheidet offene Angaben, fehlende Datenrechte oder Strict-PIT-Nachweise, fehlende Abbildung, explizit gebundene Approximation und grundsätzlich nicht unterstützte Fragen. Die semantische Signatur erkennt exakte Wiederholungen innerhalb des Betreiberbereichs. Der History-Adapter nutzt die vorhandene Quant-Expositionsprojektion mit dem jeweiligen Registerstand.

Die Abnahme arbeitet ausschließlich mit synthetischen Daten und einem lokalen OpenAI-HTTP-Double. Sie prüft die Backendteile von E2E A und E. Der Quant-Draft samt Admission ist APF-05, die öffentliche API und Oberfläche sind APF-08.

## Prüfergebnisse

- 33 Companion-Unit-Tests und Ruff bestanden. Sie decken unter anderem unveränderte Expertenbindungen, geänderte Horizonte, ungeklärte Felder, explizite Annahmen, Modell-Injection und datenschutzwidrige schemafähige Antworten ab.
- Die PostgreSQL-Abnahme bestand in 66,04 Sekunden einschließlich Backup und Restore. Sie prüft Migration, Schreibrechte, lokale Klärung, Originalisolation, Replay, Wiederaufnahme, privaten Gateway-Dispatch, getrennte Vorschlagsannahme und Ideenbestätigung. Exakte Wiederholungen bleiben auf den jeweiligen Betreiber beschränkt. Die bestehende Control-Rolle liest die Quant-Exposition in einer schreibgeschützten Transaktion. Ein leeres Register weist externe Vorkenntnis als unbekannt aus.
- 34 Quant-Dienst-, Protokoll- und Rollentests sowie die bestehende PostgreSQL-Quant-Storage-Abnahme bestanden.
- Der Containerbau und das Laden des mitgelieferten Companion-Schemas im netzlosen Container bestanden.
- Strikte OpenSpec-Validierung, Schema- und Beispielprüfung, semantischer Vertragsvergleich und 2.429 Vertragstests bestanden. Der unveränderte Vertrag erzeugte die bekannte `allOf`-Warnung. Der Credential-Quellscan meldete null Befunde.

Ein unabhängiges Review prüfte Persistenz und Modellannahme. Die Umsetzung sperrt abgeleitete Felder bei der Ideenanlage, übernimmt geänderte Annahmen nur nach Bestätigung, bindet aktuelle Bewertung an die Ideenfassung und speichert den Replay-Schlüssel für die Ideenanlage dauerhaft.
