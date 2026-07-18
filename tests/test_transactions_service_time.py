import unittest
from datetime import datetime, timedelta, timezone

from services.transactions_service import _resolve_sale_transaction_time


PH_TIMEZONE = timezone(timedelta(hours=8))


class SaleTransactionTimeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 18, 15, 30, 45, tzinfo=PH_TIMEZONE)

    def test_automatic_timestamp_uses_philippine_server_time_for_every_sale_mode(self):
        for transaction_class in ("QUICK_SALE", "NEW_SALE", "MECHANIC_SUPPLY"):
            with self.subTest(transaction_class=transaction_class):
                resolved = _resolve_sale_transaction_time(
                    {
                        "transaction_class": transaction_class,
                        "transaction_date": "2026-07-19T00:01:00",
                        "transaction_date_manually_changed": False,
                    },
                    now_obj=self.now,
                )

                self.assertEqual(resolved, "2026-07-18 15:30:45")

    def test_manual_backdated_timestamp_is_preserved(self):
        resolved = _resolve_sale_transaction_time(
            {
                "transaction_date": "2026-07-17T09:15",
                "transaction_date_manually_changed": True,
            },
            now_obj=self.now,
        )

        self.assertEqual(resolved, "2026-07-17 09:15:00")

    def test_manual_timestamp_within_five_minute_tolerance_is_allowed(self):
        resolved = _resolve_sale_transaction_time(
            {
                "transaction_date": "2026-07-18T15:35:45",
                "transaction_date_manually_changed": True,
            },
            now_obj=self.now,
        )

        self.assertEqual(resolved, "2026-07-18 15:35:45")

    def test_manual_future_timestamp_beyond_tolerance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ahead of Philippine system time"):
            _resolve_sale_transaction_time(
                {
                    "transaction_date": "2026-07-18T15:35:46",
                    "transaction_date_manually_changed": True,
                },
                now_obj=self.now,
            )

    def test_manual_aware_timestamp_is_converted_to_philippine_time(self):
        resolved = _resolve_sale_transaction_time(
            {
                "transaction_date": "2026-07-18T10:00:00+03:00",
                "transaction_date_manually_changed": True,
            },
            now_obj=datetime(2026, 7, 18, 15, 1, tzinfo=PH_TIMEZONE),
        )

        self.assertEqual(resolved, "2026-07-18 15:00:00")

    def test_invalid_manual_timestamp_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid transaction date"):
            _resolve_sale_transaction_time(
                {
                    "transaction_date": "not-a-date",
                    "transaction_date_manually_changed": True,
                },
                now_obj=self.now,
            )


if __name__ == "__main__":
    unittest.main()
