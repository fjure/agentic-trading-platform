"""Shared provider request budgets and fail-closed research admission."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from packages.research.limits import SharedAccountHostLimiter

DATA_HOST = "data.alpaca.markets"
PAPER_HOST = "paper-api.alpaca.markets"


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def limiter(cap=120, clock=None):
    return SharedAccountHostLimiter(
        "acct-scope-paper-01",
        cap,
        clock=clock or Clock(),
    )


def test_host_allowlist_and_opaque_scope_are_fail_closed():
    value = limiter()
    assert value.allow("DATA.ALPACA.MARKETS", research=False)
    with pytest.raises(ValueError, match="allowlist"):
        value.allow("data.alpaca.markets:443", research=False)
    with pytest.raises(ValueError, match="allowlist"):
        value.allow("evil.example", research=True)
    with pytest.raises(ValueError, match="opaque"):
        SharedAccountHostLimiter("https://account", 120)
    assert "acct-scope-paper-01" not in repr(value)
    assert "acct-scope-paper-01" not in repr(value.stats())


def test_rolling_total_budget_is_shared_across_hosts_and_request_classes():
    clock = Clock()
    value = limiter(clock=clock)
    for index in range(120):
        host = DATA_HOST if index % 2 == 0 else PAPER_HOST
        assert value.allow(host, research=False)
    assert not value.allow(DATA_HOST, research=True)
    assert not value.allow(PAPER_HOST, research=False)

    # Exactly 60 seconds expires the initial requests from the rolling window.
    clock.advance(60)
    assert value.allow(DATA_HOST, research=False)


def test_research_is_capped_at_sixty_per_minute_and_one_per_second():
    clock = Clock()
    value = limiter(clock=clock)
    assert value.allow(DATA_HOST, research=True)
    assert not value.allow(DATA_HOST, research=True)
    clock.advance(0.999)
    assert not value.allow(DATA_HOST, research=True)
    clock.advance(0.001)
    assert value.allow(DATA_HOST, research=True)

    for _ in range(58):
        clock.advance(1)
        assert value.allow(DATA_HOST, research=True)
    assert value.stats().admitted_research == 60
    clock.advance(1)
    assert value.allow(DATA_HOST, research=True)  # t=60 expires the t=0 request.


@pytest.mark.parametrize("cap", [0, 1, 60])
def test_small_confirmed_entitlement_preserves_safety_reserve(cap):
    value = limiter(cap)
    assert not value.allow(DATA_HOST, research=True)
    assert value.stats().denied_research_budget == 1
    assert value.allow(DATA_HOST, research=False) is (cap > 0)


def test_reduced_entitlement_caps_research_after_reserving_sixty_existing_slots():
    clock = Clock()
    value = limiter(100, clock)
    for _ in range(40):
        assert value.allow(DATA_HOST, research=True)
        clock.advance(1)
    assert not value.allow(DATA_HOST, research=True)
    assert value.stats().admitted_research == 40


def test_unknown_entitlement_allows_existing_reads_but_no_research_slot():
    value = SharedAccountHostLimiter("acct-paper", None, clock=Clock())
    assert value.allow(PAPER_HOST, research=False)
    assert not value.allow(PAPER_HOST, research=True)
    assert value.stats().denied_unknown_entitlement == 1


def test_safety_busy_blocks_research_without_blocking_existing_gets():
    value = limiter()
    value.set_safety_busy(True)
    assert not value.allow(DATA_HOST, research=True)
    assert not value.allow(PAPER_HOST, research=True, safety_busy=True)
    assert value.allow(DATA_HOST, research=False)
    assert value.stats().denied_safety_busy == 2


def test_retry_after_is_capped_and_blocks_research_for_only_that_host():
    clock = Clock()
    value = limiter(clock=clock)
    value.block_research(DATA_HOST, retry_after=600)
    assert not value.allow(DATA_HOST, research=True)
    assert value.allow(DATA_HOST, research=False)
    assert value.allow(PAPER_HOST, research=True)
    assert value.stats().denied_provider_backoff == 1
    clock.advance(60)
    assert value.allow(DATA_HOST, research=True)


def test_nonblocking_admission_is_atomic_under_concurrent_existing_and_research_gets():
    existing = limiter()
    with ThreadPoolExecutor(max_workers=32) as workers:
        admitted_existing = list(
            workers.map(lambda _: existing.allow(DATA_HOST, research=False), range(240))
        )
    assert sum(admitted_existing) == 120

    research = limiter()
    with ThreadPoolExecutor(max_workers=32) as workers:
        admitted_research = list(
            workers.map(lambda _: research.allow(PAPER_HOST, research=True), range(80))
        )
    assert sum(admitted_research) == 1
