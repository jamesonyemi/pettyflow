import pytest

from src.infrastructure.idempotency.store import (
    ProviderEventConflictError,
    SQLiteIdempotencyStore,
)


def test_provider_event_replay_is_noop_and_conflict_is_rejected(tmp_path):
    store = SQLiteIdempotencyStore(str(tmp_path / "idempotency.sqlite3"))

    assert store.claim_provider_event("tenant-a", "bank", "evt-1", "payload-a")
    assert not store.claim_provider_event("tenant-a", "bank", "evt-1", "payload-a")

    with pytest.raises(ProviderEventConflictError):
        store.claim_provider_event("tenant-a", "bank", "evt-1", "payload-b")
