import unittest
from datetime import UTC, datetime, timedelta

from larder.domain.context import ContextValues, expire_context
from larder.domain.enums import ScannerMode
from larder.domain.retry import retry_delay_seconds
from larder.domain.stock import automatic_required_quantity


class ContextTests(unittest.TestCase):
    def test_expiry_boundary_is_inclusive_and_independent(self):
        now = datetime(2026, 7, 13, 12, tzinfo=UTC)
        values = ContextValues(
            mode=ScannerMode.ADD,
            mode_expires_at=now,
            store_id="store-1",
            store_expires_at=now + timedelta(seconds=1),
            storage_location_id="freezer",
            storage_location_expires_at=now,
        )
        result = expire_context(values, now)
        self.assertIsNone(result.mode)
        self.assertIsNone(result.mode_expires_at)
        self.assertEqual(result.store_id, "store-1")
        self.assertIsNone(result.storage_location_id)
        self.assertIsNone(result.storage_location_expires_at)

    def test_context_before_boundary_remains_active(self):
        now = datetime(2026, 7, 13, 12, tzinfo=UTC)
        values = ContextValues(
            mode=ScannerMode.TRASH,
            mode_expires_at=now + timedelta(microseconds=1),
            store_id=None,
            store_expires_at=None,
        )
        self.assertEqual(expire_context(values, now), values)


class StockPolicyTests(unittest.TestCase):
    def test_below_equal_and_above_reorder_point(self):
        self.assertEqual(automatic_required_quantity(on_hand=1, reorder_point=2, target_stock=5), 4)
        self.assertEqual(automatic_required_quantity(on_hand=2, reorder_point=2, target_stock=5), 0)
        self.assertEqual(automatic_required_quantity(on_hand=3, reorder_point=2, target_stock=5), 0)

    def test_disabled_policy_has_no_demand(self):
        self.assertEqual(
            automatic_required_quantity(on_hand=0, reorder_point=2, target_stock=5, enabled=False),
            0,
        )

    def test_invalid_policy_values_fail(self):
        with self.assertRaises(ValueError):
            automatic_required_quantity(on_hand=0, reorder_point=4, target_stock=3)


class RetryTests(unittest.TestCase):
    def test_retry_schedule_caps_at_daily(self):
        self.assertEqual(
            [retry_delay_seconds(value) for value in range(1, 8)],
            [60, 300, 1800, 7200, 43200, 86400, 86400],
        )


if __name__ == "__main__":
    unittest.main()
