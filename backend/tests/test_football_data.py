import pytest

from app.providers.football_data import FootballDataProvider, NotConfigured


def test_load_raises_not_configured_without_token():
    provider = FootballDataProvider(token=None)
    with pytest.raises(NotConfigured, match="not configured"):
        provider.load()


def test_load_raises_not_configured_with_empty_token():
    provider = FootballDataProvider(token="")
    # Empty string is falsy, but the provider checks `self._token` which
    # falls back to settings. Override settings token as well.
    provider._token = ""
    with pytest.raises(NotConfigured, match="not configured"):
        provider.load()


def test_not_configured_is_a_runtime_error():
    assert issubclass(NotConfigured, RuntimeError)
