# ADR 0002: OpenAPI 3.1.2 für die lokale Control API

Status: angenommen für `paper-trading-vertical-slice` (2026-10-12).

## Entscheidung

Der kanonische HTTP-Vertrag unter `contracts/http/control-api.openapi.yaml` verwendet OpenAPI **3.1.2**. Der dokumentierte Generator ist OpenAPI Generator **7.26.0**, Ziel `typescript-angular`. Ein Wechsel zu OAS 3.2.x oder einer anderen Generatorversion erfordert einen eigenen OpenSpec-Change mit erneuter Validierung von FastAPI, Linter, Beispielen, generiertem Client und Breaking-Change-Bericht. Die ersten Verträge werden als Initialvertrag festgehalten; ein erfolgreicher Vergleich mit einem nicht vorhandenen Basisvertrag wird nicht behauptet.

## Gründe

OAS [3.2.1](https://spec.openapis.org/oas/v3.2.1.html) wurde am 2026-09-10 veröffentlicht. FastAPI dokumentiert derzeit `openapi_version=3.1.0` für die Schemagenerierung ([FastAPI](https://fastapi.tiangolo.com/how-to/extending-openapi/)). [Redocly CLI](https://redocly.com/redocly-cli) nennt Unterstützung bis OAS 3.1. Der [OpenAPI Generator](https://github.com/OpenAPITools/openapi-generator#11-compatibility) führt 3.1 als Betaunterstützung und nennt 3.2 nicht; der `typescript-angular`-Generator ist als stabiler Clientgenerator ausgewiesen ([Dokumentation](https://openapi-generator.tech/docs/generators/typescript-angular/)). Damit ist Ende-zu-Ende-Unterstützung für 3.2.1 nicht belegt. OAS 3.1.2 ist die jüngste Patchversion des 3.1-Feature-Sets laut [OAS-Versionsliste](https://spec.openapis.org/oas/).

Versionen und Quellen wurden am **2026-10-12** geprüft. Die konkrete Generator-Kompatibilität dieses Vertrags muss durch zweimalige Generierung, Hashvergleich und TypeScript-Kompilierung nachgewiesen werden; die allgemeine Upstream-Angabe allein genügt nicht. Die im lokalen PATH vorhandene Java-Laufzeit ist JRE 8 und erfüllt die [CLI-Anforderung JDK 11+](https://github.com/OpenAPITools/openapi-generator-cli). Das Prüftooling verwendet deshalb eine auf `.tooling/contracts/jdk/` begrenzte, anhand SHA-256 verifizierte Temurin-JDK-17-Installation.

## Vertragsgrenzen

OpenSpec definiert fachliches Verhalten. Der OpenAPI-Vertrag beschreibt ausschließlich den lokalen Supervisor-Transport unter `/api/v1`; Broker-Submit und Rohpayloads von Alpaca oder Jev erscheinen dort nicht. Transport-DTOs sind stabil und vermeiden Secrets sowie Chain-of-Thought. Geld, Preise und Mengen sind Dezimal-Strings, technische Zeiten UTC. RFC-9457-Probleme tragen zusätzliche stabile `code`-Werte. Schreibende Operationen benötigen Idempotency-Key; Policyänderungen zusätzlich If-Match. ETags sind opake Versionstoken, keine Geheimnisse.

Der Zugriff ist auf einen lokalen Loopback-Origin und eine explizit konfigurierte lokale Benutzer-Authentisierung begrenzt. Ein vertrauenswürdiger lokaler Launcher zeigt dem Operator einen einmaligen, kurzlebigen Aktivierungscode; `POST /api/v1/session` tauscht ihn über ein Same-Origin-Formular gegen ein serverseitiges HttpOnly-Cookie. Der Code darf nicht in URL, Logs oder dauerhaften Browserzustand gelangen. Host-/Origin-Prüfung, Rate Limit, SameSite=Strict und CSRF-Abwehr sind vor Implementierung zu testen. Broker- und Jev-Schlüssel gelangen dabei nicht zum Browser. Cross-Origin-Zugriffe sind standardmäßig nicht erlaubt. SSE überträgt nur gesäuberte Event-DTOs. Sein Cursor ist opak, Heartbeats sind reine Verbindungszeichen und der Client führt nach Cursor-Verfall erneut einen Snapshot aus.

Interne Outbox-/Eventpayloads sind versionierte JSON Schemas. AsyncAPI wird eingeführt, wenn unabhängige Producer/Consumer, externe Consumer oder ein Message Broker hinzukommen. Im ersten Change mit lokaler transaktionaler Outbox würde es denselben Vertrag duplizieren, ohne eine zusätzliche Transportgrenze zu dokumentieren.

## Konsequenzen

Der kanonische Vertrag und interne JSON Schemas werden zusammen mit Beispielen validiert. Contract-Lint, Ref- und Beispielprüfung sowie Vergleich gegen einen später vorhandenen Basisvertrag blockieren Drift. Generierter Angular-Code bleibt ein temporäres, ignoriertes Prüfartefakt und wird nie manuell geändert. FastAPI-Adapter und Laufzeit-/Negativtests folgen erst in einem ausdrücklich autorisierten Implementierungsdurchlauf.
