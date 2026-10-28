"""Literal Strict-PIT identities captured before RRB domain changes.

These values were independently repeated on the verified RRB-01 baseline.
New policies must not reinterpret old experiments or invalidate old identities.
"""

from packages.backtesting.evaluation import evaluate
from packages.backtesting.simulator import simulate
from tests.unit.test_backtest_models import config, manifest
from tests.unit.test_backtest_simulator import dataset


def test_legacy_config_and_manifest_keep_their_original_preimages():
    assert config().input_hash == (
        "82ec4a7121c7bf3ade8aeb031f23ce60f3df28d1306924d35a8d863cd71c6cd6"
    )
    assert manifest().manifest_hash == (
        "8c51a2f3e7868b8c90e0eb86bdf152177dbbbf3c7f05e20f5c69f13d0bd3a330"
    )


def test_legacy_simulation_and_evaluation_keep_their_original_identities():
    assert simulate(config(), dataset()).result_hash == (
        "1315bbf1eb23b50cc9ceb537eb85a52780dfdf6186b8ec780ec4fa746e25efaa"
    )
    assert evaluate(config(), (dataset(),)).result_hash == (
        "c8d0f0d56ce8682a0bde49c23bc227e9301beded6798742f25c21c583048eeaf"
    )
