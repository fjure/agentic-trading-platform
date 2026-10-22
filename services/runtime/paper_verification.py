"""Claimed read-only verification; all provider I/O occurs outside SQL transactions."""

from __future__ import annotations

import uuid
from dataclasses import asdict

from services.control_api.paper_commands import PaperCommands

from .common import Clock


class PaperVerificationRuntime:
    def __init__(
        self,
        engine=None,
        *,
        commands=None,
        reader_factory=None,
        verifier_factory=None,
        clock=None,
        owner=None,
    ):
        self.commands = commands if commands is not None else PaperCommands(engine)
        self.reader_factory, self.verifier_factory = reader_factory, verifier_factory
        self.clock, self.owner = clock or Clock(), owner or str(uuid.uuid4())

    def tick(self):
        claim = self.commands.claim(self.owner)
        if claim is None:
            return "READY"
        error, public, private = None, {}, None
        if claim["operationType"] in {"PAUSE", "STOP", "KILL"}:
            # Their durable latch was already closed at acceptance. Completion
            # acknowledges that control, without setup or provider dependencies.
            pass
        elif claim["operationType"] != "VERIFY_CONNECTION":
            error = "LEDGER_NOT_PREPARED"
        else:
            # No credentials module or file access before an explicit durable
            # VERIFY command. The API/worker never receive these private values.
            from services.execution.paper_credentials import (
                PaperCredentialsUnavailable,
                PaperGenerationReader,
            )
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
            except PaperCredentialsUnavailable:
                error = "CREDENTIALS_MISSING"
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
