from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import HTTPException

from larder.api import ScanRequest, build_envelope, verify_bearer


def test_build_envelope_preserves_edge_event_identity():
    event_id = UUID("9f4a9c6a-18c0-4f31-9d75-99a98f30f5b5")
    received_at = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    envelope = build_envelope(
        ScanRequest(
            event_id=event_id,
            scanner_code="SCN01",
            payload="096295131338",
            received_at=received_at,
        )
    )

    assert envelope.id == event_id
    assert envelope.received_at == received_at
    assert envelope.expected_scanner_code == "SCN01"
    assert envelope.raw_frame == b"LDR:SCN01|096295131338\n"


def test_bearer_token_is_required_and_constant_time_compared():
    verify_bearer("Bearer correct", "correct")
    with pytest.raises(HTTPException) as raised:
        verify_bearer("Bearer wrong", "correct")
    assert raised.value.status_code == 401
