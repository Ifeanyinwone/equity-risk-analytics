"""Small in-memory sliding-window limiter for the single-instance public app."""
from __future__ import annotations

from collections import OrderedDict, deque
from math import ceil
from threading import Lock
from time import monotonic


class AnalysisRateLimiter:
    """Limit analysis requests per client and across the whole service instance."""

    def __init__(
        self,
        *,
        per_client_limit: int = 5,
        global_limit: int = 20,
        window_seconds: int = 60,
        max_clients: int = 5000,
    ) -> None:
        if min(per_client_limit, global_limit, window_seconds, max_clients) < 1:
            raise ValueError("rate limits and window must be positive")
        self.per_client_limit = per_client_limit
        self.global_limit = global_limit
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._clients: OrderedDict[str, deque[float]] = OrderedDict()
        self._global: deque[float] = deque()
        self._lock = Lock()

    def allow(self, client_id: str, now: float | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` using a monotonic clock."""
        current = monotonic() if now is None else now
        cutoff = current - self.window_seconds
        identity = (client_id or "unknown")[:128]
        with self._lock:
            while self._global and self._global[0] <= cutoff:
                self._global.popleft()
            for key in list(self._clients):
                events = self._clients[key]
                while events and events[0] <= cutoff:
                    events.popleft()
                if not events:
                    del self._clients[key]

            events = self._clients.get(identity)
            if events is not None:
                self._clients.move_to_end(identity)
            else:
                events = deque()

            retry_at: float | None = None
            if len(events) >= self.per_client_limit:
                retry_at = events[0] + self.window_seconds
            elif len(self._global) >= self.global_limit:
                retry_at = self._global[0] + self.window_seconds
            if retry_at is not None:
                return False, max(1, ceil(retry_at - current))

            events.append(current)
            self._clients[identity] = events
            self._clients.move_to_end(identity)
            self._global.append(current)
            while len(self._clients) > self.max_clients:
                self._clients.popitem(last=False)
            return True, 0
