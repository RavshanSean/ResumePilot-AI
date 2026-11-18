"""Legacy screening tests use a test-only authentication bypass."""

import pytest


@pytest.fixture(autouse=True)
def screening_test_environment(monkeypatch):
    monkeypatch.setenv("RESUMEPILOT_TEST_BYPASS_AUTH", "1")
