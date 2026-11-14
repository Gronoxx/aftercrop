# Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
"""Tests never touch the network.

Tests marked ``data`` read the files that ``make data`` downloaded into
data/raw/ (network needed once, before the tests). Without that cache they are
skipped, not failed. Every test runs with AFTERCROP_OFFLINE=1.
"""

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "data: reads data/raw/ filled by `make data` (network once); skipped when the cache is absent",
    )


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("AFTERCROP_OFFLINE", "1")


@pytest.fixture(scope="session")
def offline_session():
    """Session-wide offline switch, for fixtures that build once from the cache."""
    old = os.environ.get("AFTERCROP_OFFLINE")
    os.environ["AFTERCROP_OFFLINE"] = "1"
    yield
    if old is None:
        os.environ.pop("AFTERCROP_OFFLINE", None)
    else:
        os.environ["AFTERCROP_OFFLINE"] = old


@pytest.fixture(autouse=True, scope="session")
def no_working_tree_selection(tmp_path_factory):
    """precompute.choice_seasons reads data/interim/fields_selection.json (protocol-v1.1). Tests
    never read the working tree's selection: they see none unless they write their own. Session
    scope, so module fixtures built before any test see it too."""
    try:
        from pipeline.decision import precompute
    except Exception:  # the engine venv has no PyYAML: nothing to patch there
        yield
        return
    missing = tmp_path_factory.mktemp("no_selection") / "fields_selection.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(precompute, "selection_file", lambda: missing)
        yield
