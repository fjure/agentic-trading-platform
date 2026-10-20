from tests.integration.demo_support import host


def test_real_isolated_demo_pipeline_and_recovery():
    assert "PASS isolated Demo" in host()
