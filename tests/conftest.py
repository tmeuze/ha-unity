"""Shared fixtures. The two script-style suites for the blueprints are not
pytest tests; run them directly (python tests/policy_model.py, ...)."""
collect_ignore = ["policy_model.py", "render_blueprints.py"]

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant's test harness load custom_components/unity."""
    yield
