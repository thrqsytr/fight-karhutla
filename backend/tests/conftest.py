import pytest

from backend.config import get_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIXTURE_MODE", "true")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("VERIFIER_TOKEN", "test-token")
    monkeypatch.setenv("FIRMS_MAP_KEY", "")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests must pass without a network. Any real HTTP call is a test bug."""
    import httpx

    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a test")

    monkeypatch.setattr(httpx, "request", blocked)
