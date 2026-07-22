import pytest

from app.rate_limit import OperationRateLimitExceeded, OperationRateLimiter


def test_operation_rate_limiter_releases_expired_slots():
    limiter = OperationRateLimiter()
    limiter.reserve("tenant:user:analysis", limit=2, window_seconds=60, now_value=10)
    limiter.reserve("tenant:user:analysis", limit=2, window_seconds=60, now_value=20)
    with pytest.raises(OperationRateLimitExceeded):
        limiter.reserve("tenant:user:analysis", limit=2, window_seconds=60, now_value=30)

    limiter.reserve("tenant:user:analysis", limit=2, window_seconds=60, now_value=71)
