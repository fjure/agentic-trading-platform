"""Execution-only Research composition with a separate SQL pool and safety priority."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
from functools import wraps
from threading import Lock, Thread

from sqlalchemy import text

from packages.brokers.alpaca_history import AlpacaHistorySource, ProviderPolicy
from packages.research.jobs import ResearchJobs
from packages.research.limits import SharedAccountHostLimiter, install_process_limiter
from packages.research.ports import LicenseStatus, SourceError, SourceErrorCode
from packages.research.quality_storage import observe as observe_quality
from packages.research.registry import register
from services.execution.research_ingestion import ResearchIngestion


class SafetyPriority:
    def __init__(self, limiter):
        self.limiter, self._lock, self._count = limiter, Lock(), 0

    @contextmanager
    def work(self):
        with self._lock:
            self._count += 1
            self.limiter.set_safety_busy(True)
        try:
            yield
        finally:
            with self._lock:
                self._count -= 1
                self.limiter.set_safety_busy(self._count > 0)


def safety_lane(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        priority = getattr(self, "research_priority", None)
        with priority.work() if priority else nullcontext():
            return method(self, *args, **kwargs)

    return run


class PaperResearchRuntime:
    def __init__(self, config, coordinator, stop):
        self.config, self.coordinator, self.stop = config, coordinator, stop
        self.limiter = SharedAccountHostLimiter("paper", config.research_get_limit)
        install_process_limiter(self.limiter if config.research_ingestion_enabled else None)
        coordinator.research_priority = SafetyPriority(self.limiter)
        self.policy = ProviderPolicy(
            LicenseStatus.CONFIRMED if config.research_license_confirmed else LicenseStatus.UNKNOWN,
            config.research_policy_version,
            max_gets_per_minute=config.research_get_limit,
        )
        self.engine, self.thread = None, None

    def start(self):
        # Defaults never open a provider client or read a provider generation.
        from services.runtime.config import database_engine

        self.engine = database_engine("execution", deployment="paper", isolated_research=True)
        with self.engine.begin() as connection:
            register(connection)
            connection.execute(
                text("""
              UPDATE research.source_status SET state=:state,license_status=:license,
                policy_version=:version,provider_verification='UNVERIFIED',observed_at=clock_timestamp()
              WHERE singleton
            """),
                dict(
                    state=(
                        "DISABLED"
                        if not self.config.research_ingestion_enabled
                        else "IMPLEMENTED"
                        if self.config.research_license_confirmed
                        else "BLOCKED"
                    ),
                    license=self.policy.license_status.value,
                    version=self.policy.policy_version,
                ),
            )
        if not self.config.research_ingestion_enabled:
            return
        dispatcher = ResearchIngestion(
            ResearchJobs(self.engine),
            self.source,
            self.limiter,
            enabled=True,
            source_observer=self._verified,
            quality_observer=observe_quality,
            error_observer=self._failed,
        )

        def loop():
            async def run():
                while not self.stop.is_set():
                    try:
                        await dispatcher.tick()
                    except Exception:
                        pass  # Independent safety lanes and fail-closed job leases remain usable.
                    await asyncio.sleep(1)

            asyncio.run(run())

        self.thread = Thread(target=loop, daemon=True, name="research-ingestion")
        self.thread.start()

    @staticmethod
    def _failed(connection, lease, code):
        if code in {
            SourceErrorCode.BLOCKED_CREDENTIALS,
            SourceErrorCode.BLOCKED_LICENSE,
            SourceErrorCode.BLOCKED_ENTITLEMENT,
        }:
            connection.execute(
                text("""UPDATE research.source_status SET state='BLOCKED',
              provider_verification='BLOCKED',observed_at=clock_timestamp() WHERE singleton""")
            )

    @staticmethod
    def _verified(connection, lease, count):
        connection.execute(
            text("""UPDATE research.source_status SET
          provider_verification='VERIFIED',state='IMPLEMENTED',observed_at=clock_timestamp()
          WHERE singleton""")
        )

    async def source(self):
        if self.policy.license_status != LicenseStatus.CONFIRMED:
            raise SourceError(SourceErrorCode.BLOCKED_LICENSE)
        from services.execution.paper_credentials import PaperGenerationReader

        generation = None
        with self.engine.connect() as connection:
            generation = connection.execute(text("SELECT research.credential_generation()"))
            generation = generation.scalar_one_or_none()
        if generation is None:
            raise SourceError(SourceErrorCode.BLOCKED_CREDENTIALS)
        try:
            private = PaperGenerationReader().load(generation)
            source = AlpacaHistorySource(
                api_key=private.api_key,
                api_secret=private.api_secret,
                enabled=True,
                policy=self.policy,
                egress_proxy=True,
            )
            del private
            return source
        except Exception:
            pass
        raise SourceError(SourceErrorCode.BLOCKED_CREDENTIALS)

    def close(self):
        if self.thread:
            self.thread.join(timeout=7)
        if self.engine:
            self.engine.dispose()
        install_process_limiter(None)
