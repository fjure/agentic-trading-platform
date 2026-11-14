"""Local operator console for explicit synthetic smoke preview and consent."""

import argparse
import json
from uuid import UUID, uuid4

from packages.agent_research.provider_authority import ProviderAuthorityStore
from services.llm_gateway.provider_installation import load_installation

from .agent_service import _database_engine
from .provider_profiles import check_persisted_evidence
from .research_smoke import configured_smoke


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preview", "approve", "verify"))
    parser.add_argument("--grant", type=UUID)
    parser.add_argument("--commitment")
    args = parser.parse_args()
    if args.command != "preview" and args.grant is None:
        parser.error("--grant is required")
    if args.command == "approve" and args.commitment is None:
        parser.error("--commitment is required for explicit approval")
    installation = load_installation()
    if installation is None or installation.probe_output_tokens is None:
        raise ValueError("PROVIDER_SMOKE_UNAVAILABLE")
    engine = _database_engine()
    try:
        authority = ProviderAuthorityStore(engine, check_evidence=check_persisted_evidence)
        smoke, agent = configured_smoke(engine, authority, installation, None)
        grants = smoke.authority
        if args.command == "preview":
            grant = grants.preview("local-operator", args.grant or uuid4())
            print(json.dumps(grant.public(), sort_keys=True))
        elif args.command == "approve":
            grant = grants.lookup(args.grant)
            if grant is None:
                raise ValueError("PROVIDER_SMOKE_UNAVAILABLE")
            run = grants.approve(
                grant, "local-operator", args.commitment,
                key=f"smoke:{args.grant.hex}", trace_id=uuid4().hex, agent=agent,
            )
            print(json.dumps({"runId": str(run)}))
        else:
            grant = grants.lookup(args.grant)
            if grant is None:
                raise ValueError("PROVIDER_SMOKE_UNAVAILABLE")
            grants.verify(grant)
            print(json.dumps({"state": "VERIFIED"}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError):
        raise SystemExit("Provider smoke operation denied.") from None
