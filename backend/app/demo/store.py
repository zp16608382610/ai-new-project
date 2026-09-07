"""In-memory demo session/run store (Phase 7A).

Single-process FastAPI demo only: keeps the Chat conversation history and the
Console approval lifecycle projection in memory. All business execution still
happens through the real AgentWorkflow / Risk / Approval / Service layers and
is persisted in the demo SQLite database where the domain model persists it
(orders, refunds, tickets, approval_requests).

Limitations (explicit, not hidden):
    - state is process-local; restarting uvicorn clears Chat history (the
      domain tables keep refunds / cancelled orders / resolved approvals);
    - use a single uvicorn worker for the demo (no multi-process guarantee).
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

JsonDict = dict[str, Any]


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def new_session_id() -> str:
    return f"session-{uuid.uuid4().hex[:10]}"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DemoRunStore:
    """Thread-safe map of demo agent runs (session -> request_id -> run)."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, JsonDict]] = {}
        self._approval_index: dict[int, str] = {}  # approval_id -> request_id
        self._lock = threading.RLock()

    def _put(self, run: JsonDict) -> None:
        request_id = str(run["request_id"])
        session_id = str(run["session_id"])
        run["updated_at"] = utcnow_iso()
        self._runs.setdefault(session_id, {})[request_id] = run
        approval_id = run.get("approval_id")
        if approval_id is not None:
            self._approval_index[int(approval_id)] = request_id

    def save(self, run: JsonDict) -> JsonDict:
        with self._lock:
            self._put(run)
            return run

    def get(self, request_id: str) -> JsonDict | None:
        with self._lock:
            for runs in self._runs.values():
                run = runs.get(request_id)
                if run is not None:
                    return run
            return None

    def list_session(self, session_id: str) -> list[JsonDict]:
        with self._lock:
            runs = list(self._runs.get(session_id, {}).values())
            return sorted(runs, key=lambda item: item.get("created_at") or "")

    def find_by_approval(self, approval_id: int) -> JsonDict | None:
        with self._lock:
            request_id = self._approval_index.get(int(approval_id))
            if request_id is None:
                return None
            return self.get(request_id)

    def resolved(self) -> list[JsonDict]:
        """Approval lifecycle projections resolved inside this process."""
        with self._lock:
            items: list[JsonDict] = []
            for runs in self._runs.values():
                for run in runs.values():
                    resolution = run.get("approval_resolution")
                    if resolution is not None:
                        item = dict(resolution)
                        item.setdefault("request_id", run.get("request_id"))
                        item.setdefault("session_id", run.get("session_id"))
                        item.setdefault("user_message", run.get("user_message"))
                        items.append(item)
            items.sort(key=lambda item: item.get("resolved_at") or "", reverse=True)
            return items


_store: DemoRunStore | None = None
_store_lock = threading.Lock()


def get_store() -> DemoRunStore:
    """Process-wide singleton demo store."""
    global _store
    with _store_lock:
        if _store is None:
            _store = DemoRunStore()
        return _store


def reset_store() -> None:
    """Test-only: drop the singleton store."""
    global _store
    with _store_lock:
        _store = None
