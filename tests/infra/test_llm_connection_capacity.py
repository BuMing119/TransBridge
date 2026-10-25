import pytest

from transbridge.infra.llm_client import AnthropicClient, OpenAICompatibleClient


@pytest.mark.parametrize("client_type", [OpenAICompatibleClient, AnthropicClient])
def test_default_transport_supports_100_and_cancel_rebuilds_and_closes_old_pool(client_type, monkeypatch):
    # Exercise the real local HTTP transport without constructing an SDK or
    # sending any network traffic. Private pool inspection is an integration
    # assertion about the installed httpx dependency, not production behavior.
    monkeypatch.setattr(client_type, "_make_client", lambda self: object())
    options = {"base_url": "https://example.invalid/v1"} if client_type is OpenAICompatibleClient else {}
    client = client_type(api_key="test", model="test", **options)
    old = client._http_client
    try:
        assert old._transport._pool._max_connections >= 100
        client.cancel()
        assert old.is_closed
        assert client._http_client is not old
        assert client._http_client._transport._pool._max_connections >= 100
        assert client._active_requests == 0
    finally:
        old.close()
        client._http_client.close()
