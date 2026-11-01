"""Shared dependencies for the read-only API (D1).

get_db() opens a per-request Database (runs the P6 pragma +
migration verification) and closes it after the request. A database
that fails to initialize — corrupt file, unknown schema version,
failed pragma verification — yields HTTP 503 (never 200): an
unverifiable database is never reported as healthy. Error details
describe storage/schema state only; they never include credentials
or tokens (D1 has no authentication and logs no secrets).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

from fastapi import HTTPException, Request

from core.database import Database
from core.db_migrations import DatabaseSchemaError

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "storage/hyper_engine.db"
DB_PATH_ENV_VAR = "HYPER_ENGINE_DB_PATH"
KILL_PATH_ENV_VAR = "HYPER_ENGINE_KILL_PATH"


def resolve_db_path(app_state_path: str | None = None) -> str:
    """Database file for API requests: explicit app path (tests) wins,
    then the HYPER_ENGINE_DB_PATH environment variable, then the same
    default the engine itself uses."""
    if app_state_path:
        return app_state_path
    env_path = os.environ.get(DB_PATH_ENV_VAR)
    if env_path:
        return env_path
    return DEFAULT_DB_PATH


def resolve_kill_path() -> str | None:
    """Kill-file override for isolated tests, via the
    HYPER_ENGINE_KILL_PATH environment variable only.

    Returns None in production (the engine's configured kill file is
    then used). The path is never taken from query parameters, so
    remote callers cannot probe arbitrary files. The override is
    read-only: nothing in the API writes or clears the kill file."""
    return os.environ.get(KILL_PATH_ENV_VAR)


def _unavailable(reason: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"Risk database unavailable ({reason}); refusing to serve data.",
    )


def get_db(request: Request) -> Iterator[Database]:
    """Per-request Database handle. Raises 503 when the database
    cannot be opened and verified."""
    db_path = resolve_db_path(getattr(request.app.state, "db_path", None))
    try:
        db = Database(db_path=db_path)
    except DatabaseSchemaError as e:
        logger.error("API database failed verification: %s", e)
        raise _unavailable(str(e)) from e
    except Exception as e:
        logger.error("API database failed to open: %s", type(e).__name__)
        raise _unavailable(type(e).__name__) from e
    try:
        yield db
    finally:
        db.close()
