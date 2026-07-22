from collections import defaultdict, deque
from threading import Lock
import time


class OperationRateLimitExceeded(RuntimeError):
    pass


class OperationRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def reserve(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float = 60.0,
        now_value: float | None = None,
    ) -> None:
        if limit < 1:
            raise ValueError("rate limit must be positive")
        current = time.monotonic() if now_value is None else now_value
        cutoff = current - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                raise OperationRateLimitExceeded("模型功能调用过于频繁，请稍后重试")
            events.append(current)
