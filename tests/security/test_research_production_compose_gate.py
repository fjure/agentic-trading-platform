"""Require the production Compose isolation test in the Research DB gate."""

import os

import pytest

from tests.security.research_production_compose import main


def test_research_production_compose():
    if not os.environ.get("ATL_RESEARCH_TEST_POSTGRES"):
        pytest.skip("Run scripts/test-research-db.py for the Docker integration gate.")
    main()


def test_anthropic_research_production_compose():
    if not os.environ.get("ATL_RESEARCH_TEST_POSTGRES"):
        pytest.skip("Run scripts/test-research-db.py for the Docker integration gate.")
    main("ANTHROPIC")
