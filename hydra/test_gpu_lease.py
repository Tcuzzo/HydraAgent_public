from __future__ import annotations

import json
from datetime import UTC, datetime

from hydra.gpu_lease import acquire_gpu_lease, release_gpu_lease


def test_gpu_lease_is_exclusive(tmp_path) -> None:
    lease_path = tmp_path / "gpu.lease"

    first = acquire_gpu_lease(lease_path=lease_path, runtime_id="hydra-test", ttl_seconds=60)
    second = acquire_gpu_lease(lease_path=lease_path, runtime_id="test-runtime", ttl_seconds=60)

    assert first.acquired is True
    assert second.acquired is False
    assert second.holder["runtimeId"] == "hydra-test"

    release_gpu_lease(first)
    third = acquire_gpu_lease(lease_path=lease_path, runtime_id="test-runtime", ttl_seconds=60)
    assert third.acquired is True
    release_gpu_lease(third)


def test_gpu_lease_reclaims_expired_holder(tmp_path) -> None:
    lease_path = tmp_path / "gpu.lease"
    lease_path.write_text(json.dumps({
        "runtimeId": "stale-runtime",
        "pid": 123,
        "createdAt": "2026-06-09T00:00:00.000Z",
        "expiresAt": "2026-06-09T00:00:01.000Z",
    }))

    lease = acquire_gpu_lease(
        lease_path=lease_path,
        runtime_id="hydra-test",
        now=lambda: datetime(2026, 6, 9, 0, 0, 2, tzinfo=UTC),
        ttl_seconds=60,
    )

    assert lease.acquired is True
    release_gpu_lease(lease)
