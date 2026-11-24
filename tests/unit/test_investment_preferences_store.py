"""The SQL port rejects unbounded preferences before opening a transaction."""

from copy import deepcopy

import pytest

from services.control_api.investment_preferences_store import (
    InvestmentPreferencesStore,
    PreferencesError,
    validate_update,
)


def preference_payload():
    return {
        "expectedVersion": 0, "reset": False, "experienceLevel": "UNSPECIFIED",
        "goals": [], "horizon": "UNSPECIFIED", "lossTolerance": "UNSPECIFIED",
        "contextReference": {"context": "FAKE", "namespace": "fake"},
        "onboardingSteps": [],
    }


@pytest.mark.parametrize("change", [
    {"expectedVersion": True}, {"expectedVersion": -1}, {"reset": "true"},
    {"experienceLevel": "EXPERT"}, {"horizon": None}, {"lossTolerance": "UNLIMITED"},
    {"goals": ["LEARN", "LEARN"]}, {"goals": ["BUY"]}, {"actor": "foreign"},
    {"contextReference": {"context": "FAKE", "namespace": "alpaca-paper"}},
    {"contextReference": {"context": "DEMO", "namespace": "demo"}},
    {"contextReference": {"context": "DEMO", "namespace": "demo", "demoRunId": "https://x"}},
    {"onboardingSteps": [{"step": "GOALS", "state": "DONE", "updatedAt": None}]},
    {"onboardingSteps": [{"step": "GOALS", "state": "SKIPPED", "updatedAt": "2026-01-01Z"}]},
    {"onboardingSteps": [{
        "step": "GOALS", "state": "SKIPPED", "updatedAt": "2026-02-30T00:00:00Z",
    }]},
    {"onboardingSteps": [{"step": "GOALS", "state": "SKIPPED", "updatedAt": None}] * 2},
])
def test_invalid_preferences_never_open_database(change):
    class NoDatabase:
        def transaction(self):
            raise AssertionError("invalid preference reached database")

    payload = preference_payload() | change
    with pytest.raises(PreferencesError) as caught:
        InvestmentPreferencesStore(NoDatabase()).update(
            "local-operator", payload, "key-000000000001",
        )
    assert (caught.value.status, caught.value.code) == (400, "INVALID_REQUEST")


def test_validation_copies_input_and_has_no_demo_lookup_or_mutation():
    original = preference_payload()
    copy = deepcopy(original)
    validated = validate_update(original)
    validated["goals"].append("LEARN")
    assert original == copy


@pytest.mark.parametrize("actor", [None, "", "foreign/actor", "operator\n"])
def test_actor_is_not_taken_from_untrusted_arbitrary_text(actor):
    with pytest.raises(PreferencesError) as caught:
        InvestmentPreferencesStore(None).read(actor)
    assert (caught.value.status, caught.value.code) == (403, "FORBIDDEN")


def test_database_failure_is_redacted():
    class Broken:
        def transaction(self):
            raise RuntimeError("SYNTHETIC_PRIVATE_DIAGNOSTIC")

    store = InvestmentPreferencesStore(Broken())
    for call in (lambda: store.read("local-operator"), lambda: store.update(
        "local-operator", preference_payload(), "key-000000000001"
    ), lambda: store.replay("local-operator", preference_payload(), "key-000000000001")):
        with pytest.raises(PreferencesError) as caught:
            call()
        assert str(caught.value) == "UNAVAILABLE"
        assert caught.value.status == 503
