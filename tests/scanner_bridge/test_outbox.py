import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[2] / "scanner-logger"))

from outbox import ScanOutbox  # noqa: E402


def test_scan_is_committed_before_delivery(tmp_path):
    outbox = ScanOutbox(str(tmp_path / "outbox.sqlite3"))
    scan = outbox.enqueue("SCN01", "096295131338")

    due = outbox.due()
    assert [item.event_id for item in due] == [scan.event_id]
    assert due[0].payload == "096295131338"
    assert outbox.pending_count() == 1

    outbox.mark_delivered(scan.event_id)
    assert outbox.pending_count() == 0


def test_failed_delivery_is_retried_with_same_event_id(tmp_path):
    outbox = ScanOutbox(str(tmp_path / "outbox.sqlite3"))
    scan = outbox.enqueue("SCN01", "4006381333931")
    outbox.mark_retry(scan.event_id, 1, "offline")

    assert outbox.pending_count() == 1
