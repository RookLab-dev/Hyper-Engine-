"""Hyper-Engine read-only HTTP API (dashboard milestone, D1).

FastAPI backend serving the same SQLite persistence the engine writes
to. D1 scope is strictly read-only: health, sessions, and overview
endpoints only (GET — no POST/PUT/PATCH/DELETE anywhere in this
package). Every endpoint reuses src/dashboard/data.py read functions —
no duplicated SQL, no recalculated metrics.

Run locally (localhost only) with:
    poetry run uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000
"""

from __future__ import annotations
