import pytest

from kaibot.common.ratelimit import TokenBucket


def test_bucket_allows_capacity_then_blocks():
    b = TokenBucket(capacity=3, per_seconds=60)
    assert all(b.try_acquire() for _ in range(3))
    assert not b.try_acquire()


def test_bucket_rejects_bad_config():
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, per_seconds=1)
