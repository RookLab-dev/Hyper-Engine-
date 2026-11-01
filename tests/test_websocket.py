"""Placeholder for future exchange WebSocket coverage.

Exchange WebSocket integration is deferred to Phase 5. This module
intentionally contains no WebSocket tests and claims no coverage —
it exists only so the deferral is explicit instead of an empty file
that looks like missing coverage.

Internal event delivery and API-to-frontend WebSockets are evaluated
separately in Phase 3 only where justified.
"""

import pytest


@pytest.mark.skip(
    reason=(
        "Exchange WebSocket integration deferred to Phase 5; "
        "no implementation and no coverage claimed."
    )
)
def test_exchange_websocket_deferred() -> None:
    raise AssertionError("skipped placeholder must never run")
