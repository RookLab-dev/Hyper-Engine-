import logging
import time
from typing import Any

import httpx

from core.exceptions import APIError, NetworkError, RateLimitError
from hyperliquid.auth import HyperliquidAuth

logger = logging.getLogger(__name__)

TESTNET_URL = "https://api.hyperliquid-testnet.xyz"
MAINNET_URL = "https://api.hyperliquid.xyz"

DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0


# NOTE: APIError, RateLimitError, and NetworkError are the
# canonical classes from core.exceptions, re-exported here so the
# existing `hyperliquid.client` and top-level `hyperliquid` import paths
# keep working unchanged. Do not redefine them locally.


class HyperliquidClient:
    """
    HTTP client for the Hyperliquid REST API.

    Args:
        auth:        HyperliquidAuth instance for signing exchange requests.
                     Pass None for info-only usage (no trading).
        base_url:    API base URL. Defaults to testnet — always testnet first.
        timeout:     Request timeout in seconds. Default 10.
        max_retries: Max retry attempts for NetworkError and RateLimitError.
                     Default 3. Set to 0 to disable retries.
        base_delay:  Initial backoff delay in seconds. Doubles each retry.
                     Default 1.0s → retries at 1s, 2s, 4s.
        max_delay:   Maximum backoff delay cap in seconds. Default 30.0.

    Retry behaviour:
        Attempt 1 fails → wait 1s  → attempt 2
        Attempt 2 fails → wait 2s  → attempt 3
        Attempt 3 fails → wait 4s  → attempt 4 (last if max_retries=3)
        All attempts fail → raises the last exception
    """

    def __init__(
        self,
        auth: HyperliquidAuth | None = None,
        base_url: str = TESTNET_URL,
        timeout: float = 10.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
    ) -> None:
        self.auth = auth
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    # ──────────────────────────────────────────────────────────────
    # PUBLIC METHODS
    # ──────────────────────────────────────────────────────────────

    def info(self, payload: dict[str, Any]) -> Any:
        """
        POST to /info — read-only, no signature required.

        Args:
            payload: Request body. Must contain a "type" field.

        Returns:
            Parsed JSON response — dict or list depending on endpoint.
        """
        logger.debug("INFO request: %s", payload.get("type"))
        return self._post_with_retry("/info", payload)

    def exchange(
        self,
        action: dict[str, Any],
        vault_address: str | None = None,
    ) -> dict[str, Any]:
        """
        POST to /exchange — write operations, must be signed.

        Args:
            action:        The action dict. Example: {"type": "order", ...}
            vault_address: Optional vault/subaccount address.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            ValueError: If no auth instance was provided.
        """
        if self.auth is None:
            raise ValueError(
                "HyperliquidClient requires an auth instance for exchange requests. "
                "Pass auth=HyperliquidAuth(...) when creating the client."
            )

        signed_body = self.auth(action, vault_address=vault_address)
        logger.debug("EXCHANGE request: %s", action.get("type"))
        return self._post_with_retry("/exchange", signed_body)

    # ──────────────────────────────────────────────────────────────
    # RETRY LOGIC
    # ──────────────────────────────────────────────────────────────

    def _post_with_retry(self, endpoint: str, body: dict[str, Any]) -> Any:
        """
        Wraps _post() with exponential backoff retry logic.

        Retries on: NetworkError, RateLimitError
        Does NOT retry on: APIError (4xx except 429)

        Backoff: delay = min(base_delay * 2^attempt, max_delay)
        """
        last_exception: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                return self._post(endpoint, body)

            except RateLimitError as e:
                last_exception = e
                if attempt == self.max_retries:
                    break
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "Rate limited (429) on %s — retry %d/%d in %.1fs",
                    endpoint,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)

            except NetworkError as e:
                last_exception = e
                if attempt == self.max_retries:
                    break
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "Network error on %s: %s — retry %d/%d in %.1fs",
                    endpoint,
                    e,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                time.sleep(delay)

            except APIError:
                raise  # 4xx — don't retry

        logger.error(
            "All %d retries exhausted for %s — last error: %s",
            self.max_retries,
            endpoint,
            last_exception,
        )
        raise last_exception  # type: ignore

    def _backoff_delay(self, attempt: int) -> float:
        """
        Exponential backoff delay.

        attempt=0 → base_delay * 1
        attempt=1 → base_delay * 2
        attempt=2 → base_delay * 4
        Capped at max_delay.
        """
        return min(self.base_delay * (2**attempt), self.max_delay)

    # ──────────────────────────────────────────────────────────────
    # PRIVATE METHODS
    # ──────────────────────────────────────────────────────────────

    def _post(self, endpoint: str, body: dict[str, Any]) -> Any:
        """
        Single HTTP POST attempt. No retry logic here.
        Retry lives in _post_with_retry().
        """
        url = self.base_url + endpoint
        headers = {"Content-Type": "application/json"}

        try:
            response = httpx.post(
                url,
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.TimeoutException as e:
            raise NetworkError(f"Request timed out after {self.timeout}s: {e}") from e
        except httpx.ConnectError as e:
            raise NetworkError(f"Connection failed to {url}: {e}") from e

        logger.debug("Response %s from %s", response.status_code, endpoint)
        return self._handle_response(response)

    def _handle_response(self, response: httpx.Response) -> Any:
        """
        Parses response and raises typed errors per status code.

        200 → return parsed JSON
        429 → RateLimitError (retried by _post_with_retry)
        4xx → APIError (not retried)
        5xx → NetworkError (retried)
        """
        if response.status_code == 200:
            try:
                return response.json()
            except Exception as e:
                raise APIError(
                    200, f"Failed to parse JSON response: {response.text}"
                ) from e

        if response.status_code == 429:
            raise RateLimitError("Rate limited — backing off")

        if 400 <= response.status_code < 500:
            try:
                error_body = response.json()
                message = error_body.get("error", response.text)
            except Exception:
                message = response.text
            raise APIError(response.status_code, message)

        if response.status_code >= 500:
            raise NetworkError(f"Server error {response.status_code}: {response.text}")

        raise APIError(response.status_code, response.text)
