import unittest
from decimal import Decimal

from supplier_message_planner import (
    AmbiguousSupplierQuote,
    BundleItem,
    format_supplier_bundle,
    plan_supplier_bundles,
    validate_multi_rfq_quotes,
)


class SupplierMessagePlannerTests(unittest.TestCase):
    def test_twenty_five_items_are_split_without_duplicates(self):
        items = [BundleItem(rfq_id=str(i), product_name=f"Product {i}") for i in range(25)]
        batches = plan_supplier_bundles(items)
        self.assertEqual([len(batch) for batch in batches], [8, 8, 8, 1])
        self.assertEqual(len({x.rfq_id for batch in batches for x in batch}), 25)

    def test_duplicate_item_is_sent_once(self):
        item = BundleItem(rfq_id="rfq-1", product_name="Brush", quantity=24)
        self.assertEqual(plan_supplier_bundles([item, item]), [[item]])

    def test_bundle_has_explicit_numbers_and_response_instructions(self):
        msg = format_supplier_bundle([
            BundleItem("a", "Noora Brush", 24, deadline_hours=24),
            BundleItem("b", "PVC Pipe", 12),
        ])
        self.assertIn("1. Noora Brush", msg)
        self.assertIn("2. PVC Pipe", msg)
        self.assertIn("each item number", msg)

    def test_multi_quote_must_map_to_real_candidate(self):
        with self.assertRaises(AmbiguousSupplierQuote):
            validate_multi_rfq_quotes(
                [{"candidate_rfq_id": "wrong", "price": 12, "confidence": 0.99}],
                ["correct"],
            )

    def test_confidence_gate_is_fail_closed(self):
        with self.assertRaises(AmbiguousSupplierQuote):
            validate_multi_rfq_quotes(
                [{"candidate_rfq_id": "a", "price": 12, "confidence": 0.3}],
                ["a"],
            )

    def test_two_product_quotes_preserve_correct_price(self):
        result = validate_multi_rfq_quotes([
            {"candidate_rfq_id": "a", "price": 15, "confidence": 0.92},
            {"candidate_rfq_id": "b", "price": 45, "confidence": 0.95},
        ], ["a", "b"])
        self.assertEqual([(x.rfq_id, x.price) for x in result],
                         [("a", Decimal("15")), ("b", Decimal("45"))])


if __name__ == "__main__":
    unittest.main()
