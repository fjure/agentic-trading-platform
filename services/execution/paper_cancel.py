"""Independent bounded Cancel lane; no entry approval or financial write authority.

Only fixed SQL ports can claim, authorize or finish an obligation. Provider I/O
never holds a ledger transaction. SQL retains uncertain attempts across restarts;
only the normal account-wide financial reconciliation can establish completion.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
from packages.domain import models as m
from services.execution.paper_credentials import PaperGenerationReader


class CancelFenced(RuntimeError):
    def __init__(self):
        super().__init__("PAPER_CANCEL_FENCED")


class PaperCancelService:
    def __init__(self, ledger, *, reader_factory=None, broker_factory=None, clock=None,
                 worker_id=None):
        from services.runtime.common import Clock, Ids

        self.ledger = ledger
        self.reader_factory, self.broker_factory = reader_factory, broker_factory
        self.clock, self.ids = clock or Clock(), Ids()
        self.worker_id = str(uuid.uuid4()) if worker_id is None else worker_id
        if str(uuid.UUID(self.worker_id)) != self.worker_id:
            raise ValueError("INVALID_CANCEL_WORKER")

    def _call(self, statement, params):
        with self.ledger.engine.begin() as connection:
            return connection.execute(text(statement), params).scalar_one_or_none()

    def tick(self, *, owner_id, fencing_token):
        """Process at most one obligation, using a dedicated closed-after-tick adapter."""
        broker = None
        try:
            claim = self._call(
                "SELECT public.paper_cancel_claim(:exec_owner,:exec_fence,"
                "CAST(:worker AS uuid),:seconds)",
                {"exec_owner": owner_id, "exec_fence": fencing_token,
                 "worker": self.worker_id, "seconds": 30},
            )
            if claim is None:
                return "READY"
            if type(claim) is not dict or type(claim.get("lookupOnly")) is not bool:
                raise CancelFenced()
            authority = {
                "cancel": claim["cancelId"], "worker": self.worker_id,
                "lease_fence": claim["leaseFence"], "exec_owner": owner_id,
                "exec_fence": fencing_token,
            }
            arguments = ("CAST(:cancel AS uuid),CAST(:worker AS uuid),:lease_fence,"
                         ":exec_owner,:exec_fence")
            if self._call(
                "SELECT public.paper_cancel_finish(" + arguments + ")", authority,
            ) is True:
                return "READY"
            lookup = self._call(
                "SELECT public.paper_cancel_begin_lookup(" + arguments + ")", authority,
            )
            if type(lookup) is not dict or "error" in lookup:
                raise CancelFenced()
            lookup_id = lookup["attemptId"]
            # This reads immutable local intent only. bind_intent also performs
            # no provider I/O; the committed LOOKUP marker precedes every GET.
            with self.ledger.transaction() as tx:
                intent = tx.get(m.OrderIntent, claim["intentId"])
            if (type(intent) is not m.OrderIntent
                    or intent.client_order_id != claim["clientOrderId"]):
                raise CancelFenced()
            reader = self.reader_factory() if self.reader_factory else PaperGenerationReader()
            private = reader.load(lookup["credentialGeneration"])
            if private.expected_account_id != lookup["pinnedAccountId"]:
                raise CancelFenced()
            broker = (self.broker_factory or AlpacaPaperBrokerAdapter)(
                self.clock, self.ids, api_key=private.api_key, api_secret=private.api_secret,
                expected_account_id=private.expected_account_id, paper_enabled=True,
                egress_proxy=self.broker_factory is None, proxy_deployment="paper",
            )
            del private
            broker.bind_intent(intent, attempted=True)
            delete_id = None

            def record(attempt_id, outcome):
                if self._call(
                    "SELECT public.paper_cancel_record(CAST(:attempt AS uuid),"
                    "CAST(:worker AS uuid),:lease_fence,:exec_owner,:exec_fence,"
                    "CAST(:outcome AS jsonb))",
                    {**authority, "attempt": attempt_id,
                     "outcome": json.dumps(outcome.as_record())},
                ) is not True:
                    raise CancelFenced()

            def before_delete():
                nonlocal delete_id
                result = self._call(
                    "SELECT public.paper_cancel_begin_delete(" + arguments
                    + ",CAST(:lookup AS uuid))", {**authority, "lookup": lookup_id},
                )
                if type(result) is not dict or "error" in result:
                    raise CancelFenced()
                delete_id = result["attemptId"]

            outcome = broker.cancel_with_outcome(
                intent.client_order_id, expected_broker_order_id=claim["brokerOrderId"],
                on_lookup=lambda observed: record(lookup_id, observed),
                before_delete=before_delete, allow_delete=not claim["lookupOnly"],
            )
            if delete_id is not None:
                record(delete_id, outcome)
            finished = self._call(
                "SELECT public.paper_cancel_finish(" + arguments + ")", authority,
            )
            return "READY" if finished is True else "PENDING"
        except Exception:
            # The SQL attempt marker, never an exception string or local flag,
            # controls recovery. Uncertain commits must remain lookup-only.
            return "UNAVAILABLE"
        finally:
            if broker is not None:
                try:
                    broker.close()
                except Exception:
                    pass  # Durable SQL authority is unaffected by local transport cleanup.
