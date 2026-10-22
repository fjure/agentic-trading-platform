"""Claimed read-only verification; all provider I/O occurs outside SQL transactions."""

from __future__ import annotations

import uuid
from dataclasses import asdict

from services.control_api.paper_commands import ERROR_CODES, PaperCommandError, PaperCommands

from .common import Clock


class PaperVerificationRuntime:
    def __init__(
        self,
        engine=None,
        *,
        commands=None,
        reader_factory=None,
        verifier_factory=None,
        preparation_factory=None,
        clock=None,
        owner=None,
    ):
        self.commands = commands if commands is not None else PaperCommands(engine)
        self.reader_factory, self.verifier_factory = reader_factory, verifier_factory
        self.preparation_factory = preparation_factory
        self.clock, self.owner = clock or Clock(), owner or str(uuid.uuid4())

    def tick(self):
        # Verification plus preparation each has a 30-second provider deadline.
        # The completion still rechecks this bounded lease and owner fence in SQL.
        claim = self.commands.claim(self.owner, lease_seconds=90)
        if claim is None:
            return "READY"
        error, public, private = None, {}, None
        if claim["operationType"] in {"PAUSE", "STOP", "KILL"}:
            # Their durable latch was already closed at acceptance. Completion
            # acknowledges that control, without setup or provider dependencies.
            pass
        elif claim["operationType"] not in {"VERIFY_CONNECTION", "PREPARE_LEDGER"}:
            error = "LEDGER_NOT_PREPARED"
        else:
            # No credentials module or file access before an explicit durable
            # verification/preparation command. API/worker never receive these values.
            from services.execution.paper_credentials import (
                PaperCredentialsUnavailable,
                PaperGenerationReader,
            )
            from services.execution.paper_initial_evidence import InitialEvidenceUnavailable
            from services.execution.paper_verification import PaperAccountVerifier

            try:
                reader = self.reader_factory() if self.reader_factory else PaperGenerationReader()
                generation = claim["credentialGeneration"]
                credentials = reader.load(generation)
                verifier = (self.verifier_factory or PaperAccountVerifier)(
                    credentials=reader,
                    clock=self.clock,
                    expected_account_id=credentials.expected_account_id,
                    pinned_account_id=claim["pinnedAccountId"],
                )
                del credentials
                result = verifier.verify(generation)
                public = asdict(result.public)
                if result.public.blockers:
                    error = result.public.blockers[0]
                elif result.private is None:
                    error = "ACCOUNT_IDENTITY_MISMATCH"
                else:
                    private = result.private.provider_account_id
                    if claim["operationType"] == "PREPARE_LEDGER":
                        from services.execution.paper_daily_evidence import (
                            PaperPreparationCollector,
                        )
                        from services.execution.paper_initialization import complete_preparation

                        collector = (self.preparation_factory or PaperPreparationCollector)(
                            credentials=reader,
                            clock=self.clock,
                            expected_account_id=private,
                            pinned_account_id=claim["pinnedAccountId"],
                        )
                        evidence = collector.collect(generation, result.private)
                        # SQL atomically installs the complete basis and marks
                        # this command succeeded; generic finish must not run again.
                        complete_preparation(self.commands, claim, self.owner, evidence)
                        return "READY"
            except PaperCredentialsUnavailable:
                error = "CREDENTIALS_MISSING"
            except InitialEvidenceUnavailable as failure:
                code = str(failure)
                error = {
                    "INITIAL_OPEN_ORDERS": "OPEN_ORDERS_PRESENT",
                    "INITIAL_POSITION_UNSUPPORTED": "UNSUPPORTED_POSITION",
                    "INITIAL_COST_BASIS_MISSING": "COST_BASIS_MISSING",
                    "INITIAL_EVIDENCE_CHANGED": "SNAPSHOT_CHANGED",
                    "INITIAL_EVIDENCE_STALE": "RECONCILIATION_REQUIRED",
                    "INITIAL_HISTORY_INCOMPLETE": "RECONCILIATION_REQUIRED",
                    "INITIAL_EVIDENCE_INVALID": "RECONCILIATION_REQUIRED",
                    "DAILY_REFERENCE_UNPROVEN": "RECONCILIATION_REQUIRED",
                    "EXTERNAL_FLOW_UNPROVEN": "RECONCILIATION_REQUIRED",
                }.get(code, code if code in ERROR_CODES else "PROVIDER_UNAVAILABLE")
            except PaperCommandError as failure:
                error = failure.code
            except Exception:
                error = "PROVIDER_UNAVAILABLE"
        self.commands.finish(
            claim["operationId"],
            self.owner,
            claim["fence"],
            public=public,
            private_account_id=private,
            error_code=error,
        )
        return "READY"
