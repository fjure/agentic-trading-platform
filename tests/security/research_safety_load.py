"""Fail the signed private gateway while the Paper safety operations run."""

from threading import Event


def dispatch_during_safety(active: Event, release: Event) -> None:
    from fastapi.testclient import TestClient
    from starlette.concurrency import run_in_threadpool

    from services.llm_gateway.app import GatewayDispatchClient, create_gateway_app
    from services.llm_gateway.provider_wire import ResultState
    from services.llm_gateway.supervisor_rpc import AdmissionAuthentication
    from tests.unit.test_llm_offline_gateway import make_request

    request = make_request()
    dispatched = Event()

    class UnavailableGateway:
        def dispatch(self, command):
            if command == request:
                dispatched.set()
            raise RuntimeError("synthetic gateway failure")

        def cancel(self, _command):
            raise AssertionError("Paper safety operations must never use the LLM gateway")

    auth = AdmissionAuthentication(bytes(range(32)))
    app = create_gateway_app(UnavailableGateway(), auth)

    @app.middleware("http")
    async def delay_failure_delivery(incoming, call_next):
        response = await call_next(incoming)
        assert dispatched.is_set()
        assert response.status_code == 503
        active.set()
        assert await run_in_threadpool(release.wait, 20), "Paper safety calls did not finish"
        return response

    with TestClient(app) as client:
        result = GatewayDispatchClient(client, auth).dispatch(request)
    assert result.state is ResultState.OUTCOME_UNKNOWN
    assert result.output is None and result.usage is None


_CRASH_EXIT = 86


def crash_research_worker(database, run_id, owner):
    import subprocess
    import sys

    from packages.agent_research.runs import Claim

    result = subprocess.run(
        [sys.executable, "-m", "tests.security.research_safety_load",
         database, str(run_id), str(owner)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == _CRASH_EXIT, result.stderr
    fence, version = map(int, result.stdout.split())
    return Claim(run_id, owner, fence, version)


if __name__ == "__main__":
    import os
    import sys
    from uuid import UUID

    from packages.agent_research.runs import RunStore
    from tests.security.test_quant_lab_isolation import _engine

    database, run_id, owner = sys.argv[1:]
    claim = RunStore(_engine("atl_agent_research", database)).claim(UUID(owner))
    assert claim is not None and claim.run_id == UUID(run_id)
    print(claim.fence, claim.version, flush=True)
    os._exit(_CRASH_EXIT)
