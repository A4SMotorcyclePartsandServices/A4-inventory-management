import unittest

from services.reports_service import _build_mechanic_maps, _build_sale_receivable_map


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _PureUtangConnection:
    def __init__(self):
        self.query = ""

    def execute(self, query, params):
        self.query = query
        return _Rows([{"sale_id": 123, "receivable_created": 380}])


class UtangReportRegressionTests(unittest.TestCase):
    def test_pure_utang_uses_the_sale_payment_method_when_no_split_payment_exists(self):
        conn = _PureUtangConnection()

        result = _build_sale_receivable_map(conn, [123])

        self.assertEqual(result, {123: 380.0})
        self.assertIn("original_pm.category = 'Debt' THEN s.total_amount", conn.query)

    def test_later_paid_utang_does_not_create_a_backdated_regular_payout(self):
        sale = {
            "id": 123,
            "status": "Paid",  # Its live status changed after collection.
            "mechanic_id": 9,
            "mechanic_name": "Mechanic",
            "commission_rate": 0.8,
            "applies_quota_topup": 1,
        }
        services = {
            123: [{
                "price": 500,
                "mechanic_id": 9,
                "mechanic_name": "Mechanic",
                "commission_rate": 0.8,
                "applies_quota_topup": 1,
                "mechanic_payout_exempt": 0,
            }]
        }

        mechanic_map, debt_map = _build_mechanic_maps(
            [sale],
            [],
            services,
            {},
            report_paid_sale_ids=set(),
        )

        self.assertEqual(mechanic_map, {})
        self.assertEqual(debt_map, {})


if __name__ == "__main__":
    unittest.main()
