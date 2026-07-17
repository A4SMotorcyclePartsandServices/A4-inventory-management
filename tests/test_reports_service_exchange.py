import unittest

from services.reports_service import (
    _build_mechanic_maps,
    _calculate_mechanic_payouts,
    _get_mechanic_payout_sales,
)


class ExchangeReportRegressionTests(unittest.TestCase):
    def test_item_swap_keeps_original_sale_eligible_for_mechanic_payout(self):
        original_sale = {
            "id": 10,
            "status": "Paid",
            "transaction_class": "NEW_SALE",
            "mechanic_id": 1,
            "mechanic_name": "Moises",
            "commission_rate": 0.8,
            "applies_quota_topup": 1,
            "original_exchange_number": "SW-0010",
        }
        replacement_sale = {
            "id": 11,
            "status": "Paid",
            "transaction_class": "NEW_SALE",
            "mechanic_id": None,
            "replacement_exchange_number": "SW-0010",
        }
        mechanic_supply = {
            "id": 12,
            "status": "Paid",
            "transaction_class": "MECHANIC_SUPPLY",
            "mechanic_id": 1,
        }

        payout_sales = _get_mechanic_payout_sales(
            [original_sale, replacement_sale, mechanic_supply]
        )

        self.assertEqual([sale["id"] for sale in payout_sales], [10, 11])

        mechanic_map, debt_map = _build_mechanic_maps(
            payout_sales,
            [],
            {
                10: [{
                    "price": 210,
                    "mechanic_id": 1,
                    "mechanic_name": "Moises",
                    "commission_rate": 0.8,
                    "applies_quota_topup": 1,
                    "mechanic_payout_exempt": 0,
                }],
            },
            {},
        )
        summary, totals = _calculate_mechanic_payouts(mechanic_map, debt_map)

        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["service_sales_total"], 210.0)
        self.assertEqual(summary[0]["mechanic_cut"], 168.0)
        self.assertEqual(summary[0]["shop_commission_share"], 42.0)
        self.assertEqual(summary[0]["shop_topup"], 332.0)
        self.assertEqual(summary[0]["total_payout"], 500.0)
        self.assertEqual(totals["total_mech_cut"], 168.0)


if __name__ == "__main__":
    unittest.main()
