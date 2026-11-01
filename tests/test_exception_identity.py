"""exception-identity proof for the hierarchy consolidation.

`core.exceptions` is canonical. `hyperliquid.client` (and through it the
top-level `hyperliquid` package) re-exports the same classes so existing
import paths keep working. These tests pin that contract: identity
(`is`, not just equality), the mocked 429 path (canonical type,
status_code, message, APIError compatibility), and the mocked 500 path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

import hyperliquid
import hyperliquid.client
from core import exceptions as core_exceptions
from hyperliquid.client import HyperliquidClient


def make_mock_response(status_code: int, json_data: dict) -> MagicMock:
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.text = str(json_data)
    return mock


# ──────────────────────────────────────────────────────────────
# IDENTITY — same objects, not duplicates
# ──────────────────────────────────────────────────────────────


def test_client_errors_are_core_errors() -> None:
    assert hyperliquid.client.APIError is core_exceptions.APIError
    assert hyperliquid.client.RateLimitError is core_exceptions.RateLimitError
    assert hyperliquid.client.NetworkError is core_exceptions.NetworkError


def test_package_errors_are_core_errors() -> None:
    assert hyperliquid.APIError is core_exceptions.APIError
    assert hyperliquid.RateLimitError is core_exceptions.RateLimitError
    assert hyperliquid.NetworkError is core_exceptions.NetworkError


# ──────────────────────────────────────────────────────────────
# 429 PATH — canonical RateLimitError via mocked HTTP
# ──────────────────────────────────────────────────────────────


def test_429_raises_canonical_rate_limit_error(auth) -> None:
    """Mocked 429 must raise the canonical RateLimitError (status 429)."""
    from hyperliquid.client import RateLimitError as ClientRateLimitError

    client = HyperliquidClient(auth=auth, max_retries=0)
    mock_response = make_mock_response(429, {"error": "rate limited"})
    with patch("httpx.post", return_value=mock_response):
        with pytest.raises(core_exceptions.RateLimitError) as exc_info:
            client.info({"type": "meta"})
    assert type(exc_info.value) is ClientRateLimitError
    assert exc_info.value.status_code == 429
    assert "rate limited" in exc_info.value.message.lower()


def test_429_caught_as_canonical_api_error(auth) -> None:
    """Canonical RateLimitError must remain catchable as APIError."""
    client = HyperliquidClient(auth=auth, max_retries=0)
    mock_response = make_mock_response(429, {"error": "rate limited"})
    with patch("httpx.post", return_value=mock_response):
        with pytest.raises(core_exceptions.APIError):
            client.info({"type": "meta"})


# ──────────────────────────────────────────────────────────────
# 500 PATH — canonical NetworkError via mocked HTTP
# ──────────────────────────────────────────────────────────────


def test_500_raises_canonical_network_error(auth) -> None:
    """Mocked 500 must raise the canonical NetworkError."""
    from hyperliquid.client import NetworkError as ClientNetworkError

    client = HyperliquidClient(auth=auth, max_retries=0)
    mock_response = make_mock_response(500, {"error": "server error"})
    mock_response.text = "server error"
    with patch("httpx.post", return_value=mock_response):
        with pytest.raises(core_exceptions.NetworkError) as exc_info:
            client.info({"type": "meta"})
    assert type(exc_info.value) is ClientNetworkError
