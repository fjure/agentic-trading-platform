"""Consume one fenced Research handoff using the existing Quant Control role."""

from datetime import datetime

from sqlalchemy import text

from packages.backtesting.jobs import BacktestError
from packages.persistence.transaction import ScopedConnection


def prepare_next_quant(quant) -> bool:
    with quant.storage.transaction() as connection:
        request = connection.scalar(text("""
            SELECT agent_research.claim_quant_preparation(NULL::uuid)
        """))
        if request is None:
            return False
        run = request["runId"]
        try:
            with connection.begin_nested():
                draft, validation = _prepare(quant, ScopedConnection(connection), request)
        except BacktestError:
            # Discard partial writes and defer this job without starving the queue.
            draft, validation = None, None
        connection.execute(text("""
            SELECT agent_research.record_quant_preparation(:run,:definition,:validation)
        """), {"run": run, "definition": draft, "validation": validation})
        return True


def _prepare(quant, scope, request):
    run = request["runId"]
    identity = {
        "actor": "local-operator", "trace": request["traceId"], "transaction": scope,
    }
    draft = quant.create_agent_experiment(
        request["definition"], key=f"prepare-draft:{run}", run_id=run,
        idea_id=request["ideaId"], idea_version=request["ideaVersion"], **identity,
    )
    validation = quant.validate_agent_experiment(draft["id"], {
        "schemaVersion": "quant-lab-v1", "definitionVersion": draft["definitionVersion"],
        "definitionHash": draft["definitionHash"],
    }, key=f"prepare-validation:{run}",
        input_deadline=datetime.fromisoformat(request["deadlineAt"]), **identity)
    return draft["id"], validation["id"]
