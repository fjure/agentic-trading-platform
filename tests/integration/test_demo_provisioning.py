"""Actual PG membership drift is rejected before any demo/normal database mutation."""

from tests.integration.demo_provisioning_support import host


def test_existing_demo_provisioner_membership_drift_postgres():
    assert "PASS: 5 isolated provisioner drift scenarios" in host()
