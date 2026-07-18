import unittest

from services.cash_service import (
    PHYSICAL_CASH_CATEGORIES,
    _SALE_CASH_EFFECTIVE_DATE_SQL,
    _get_sales_cash,
)


class _Result:
    def fetchall(self):
        return []


class _RecordingConnection:
    def __init__(self):
        self.query = None
        self.params = None

    def execute(self, query, params=None):
        self.query = query
        self.params = params
        return _Result()


class CashSaleDateRegressionTests(unittest.TestCase):
    def test_cash_in_keeps_transaction_date_for_normal_sales_and_later_voids(self):
        normalized = " ".join(_SALE_CASH_EFFECTIVE_DATE_SQL.split())

        self.assertIn("ELSE s.transaction_date", normalized)
        self.assertIn("s.voided_at < s.transaction_date", normalized)
        self.assertNotIn("s.voided_at <= s.transaction_date", normalized)

    def test_cash_in_uses_void_date_only_when_void_predates_sale(self):
        normalized = " ".join(_SALE_CASH_EFFECTIVE_DATE_SQL.split())

        self.assertIn("COALESCE(s.is_voided, FALSE) = TRUE", normalized)
        self.assertIn("THEN s.voided_at", normalized)

    def test_range_filters_and_display_use_the_same_effective_date(self):
        conn = _RecordingConnection()

        _get_sales_cash(
            conn,
            date_from="2026-07-18",
            date_to="2026-07-19",
        )

        normalized_query = " ".join(conn.query.split())
        normalized_expression = " ".join(_SALE_CASH_EFFECTIVE_DATE_SQL.split())

        self.assertEqual(normalized_query.count(normalized_expression), 4)
        self.assertIn(f"{normalized_expression} AS created_at", normalized_query)
        self.assertIn(f"DATE({normalized_expression}) >= %s", normalized_query)
        self.assertIn(f"DATE({normalized_expression}) <= %s", normalized_query)
        self.assertIn(
            f"GROUP BY s.id, s.sales_number, s.customer_name, {normalized_expression}",
            normalized_query,
        )
        self.assertEqual(
            conn.params,
            [list(PHYSICAL_CASH_CATEGORIES), "2026-07-18", "2026-07-19"],
        )


if __name__ == "__main__":
    unittest.main()
