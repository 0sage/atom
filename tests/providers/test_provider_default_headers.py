from atom.config.schema import ProviderConfig
from atom.providers.factory import _provider_extra_headers
from atom.providers.registry import find_by_name


def test_provider_without_default_headers_sends_none() -> None:
    """No built-in provider ships default headers, so the merge yields nothing."""
    spec = find_by_name("openai")

    assert spec is not None
    assert _provider_extra_headers(spec, ProviderConfig()) is None


def test_provider_config_extra_headers_are_forwarded() -> None:
    spec = find_by_name("openai")
    provider = ProviderConfig.model_validate({
        "extraHeaders": {
            "User-Agent": "custom-client/1.0",
            "X-Test": "1",
        },
    })

    assert _provider_extra_headers(spec, provider) == {
        "User-Agent": "custom-client/1.0",
        "X-Test": "1",
    }
