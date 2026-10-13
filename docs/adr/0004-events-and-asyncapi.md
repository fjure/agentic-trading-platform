# ADR 0004: Outbox und JSON-Schemas, AsyncAPI-Einführungsschwelle

Status: geplant, 2026-10-12.

Der lokale MVP verwendet transaktionale PostgreSQL-Outbox und versionierte JSON-Schemas für Envelope/Payload. Zustellung mindestens einmal, Consumer deduplizieren atomar. Kein End-to-End Exactly-once-Versprechen. Alternative Kafka/AsyncAPI fügt ohne unabhängige externe Consumer zusätzliche Betriebs- und Vertragskosten hinzu.

AsyncAPI wird in einem neuen Change eingeführt, sobald unabhängig betriebene Producer/Consumer, externe Consumer oder ein Messagebroker entstehen. Konsequenz: Eventschemas, Reihenfolge, Cursor und Recovery bleiben schon jetzt ausführbar beschrieben; spätere Brokerwahl darf keine stillen Semantikänderungen erzeugen.
