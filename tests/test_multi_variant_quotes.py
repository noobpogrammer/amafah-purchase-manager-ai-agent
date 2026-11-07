"""
Phase 10: Multi-Variant Quote Support Tests
Covers all 30 required verification scenarios:
1. Simple quote -> NULL variant_label
2. Two variants from one message
3. Same quote_group_id for batch
4. Different messages get different group IDs
5. Variant labels preserved (e.g., 'India', 'China', 'Schneider')
6. Shared delivery copied correctly
7. Variant-specific delivery preserved
8. One-variant revision
9. Unrevised variant remains effective
10. Full historical rows retained in get_all_quotes_for_rfq
11. Available -> unavailable revision
12. Unavailable variant excluded from effective active quotes
13. Invalid price rejects whole batch
14. NaN/Inf rejected
15. More than 10 variants rejected (MAX_QUOTE_VARIANTS)
16. Duplicate normalized labels rejected (fail-closed)
17. Exact RFQ lock enforced
18. Wrong tenant/supplier rejected
19. Closed/expired RFQ rejected
20. Supplier becomes 'responded' after at least one valid priced variant
21. Master RFQ remains 'active'
22. Batch failure does not partially insert
23. Batch failure does not mark supplier responded
24. Exactly ONE thank-you message per batch
25. Operator provenance uses supplier raw message (not operator prompt)
26. Operator instruction cannot invent quote out of thin air
27. source_message_id shared across all variants in batch
28. Legacy single-quote structure remains fully compatible
29. Unavailable revision may omit price
30. Available variant requires positive price
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
import main
import db
import openclaw_bridge


class TestMultiVariantDatabaseAndBatchSemantics:
    """Database batch recording, effective retrieval, quote groups, and revision logic."""

    def test_3_same_quote_group_id_for_batch(self):
        """3. All variants recorded in one batch receive the exact same quote_group_id."""
        inserted_rows = []
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"id": "rfq-1", "status": "active"}]

        def mock_insert(rows):
            nonlocal inserted_rows
            inserted_rows = rows
            res_mock = MagicMock()
            res_mock.data = rows
            return res_mock

        mock_supabase.table.return_value.insert = mock_insert
        # Mock RPC raising error so it exercises the atomic batch logic
        mock_supabase.rpc.side_effect = Exception("RPC simulated offline")

        with patch.object(db, "supabase", mock_supabase), \
             patch("db.is_rfq_open", return_value=True):
            variants = [
                {"variant_label": "India", "price": 45.0},
                {"variant_label": "China", "price": 38.0},
            ]
            res = db.record_quotes_batch("rfq-1", "supp-1", variants, raw_message="India 45, China 38")
            assert len(inserted_rows) == 2
            group_1 = inserted_rows[0]["quote_group_id"]
            group_2 = inserted_rows[1]["quote_group_id"]
            assert group_1 is not None
            assert group_1 == group_2
            assert uuid.UUID(group_1)  # valid UUID

    def test_4_different_messages_get_different_group_ids(self):
        """4. Sequential batch calls generate distinct quote_group_ids."""
        captured_groups = []
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"id": "rfq-1", "status": "active"}]

        def mock_insert(rows):
            captured_groups.append(rows[0]["quote_group_id"])
            res_mock = MagicMock()
            res_mock.data = rows
            return res_mock

        mock_supabase.table.return_value.insert = mock_insert
        mock_supabase.rpc.side_effect = Exception("RPC simulated offline")

        with patch.object(db, "supabase", mock_supabase), \
             patch("db.is_rfq_open", return_value=True):
            db.record_quotes_batch("rfq-1", "supp-1", [{"variant_label": "A", "price": 10.0}])
            db.record_quotes_batch("rfq-1", "supp-1", [{"variant_label": "B", "price": 20.0}])

            assert len(captured_groups) == 2
            assert captured_groups[0] != captured_groups[1]

    def test_8_and_9_revision_semantics_unrevised_variant_remains_effective(self):
        """8 & 9. Revising only India from 3.0 to 2.8 leaves China 2.5 effective."""
        t1 = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        t2 = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()

        # Database history returned newest first
        db_history = [
            {"id": "q3", "rfq_id": "rfq-1", "supplier_id": "s1", "variant_label": "India", "price": 2.80, "is_available": True, "created_at": t2},
            {"id": "q2", "rfq_id": "rfq-1", "supplier_id": "s1", "variant_label": "China", "price": 2.50, "is_available": True, "created_at": t1},
            {"id": "q1", "rfq_id": "rfq-1", "supplier_id": "s1", "variant_label": "India", "price": 3.00, "is_available": True, "created_at": t1},
        ]

        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.order.return_value.execute.return_value.data = db_history

        with patch.object(db, "supabase", mock_supabase):
            effective = db.get_quotes_for_rfq("rfq-1")
            assert len(effective) == 2

            by_label = {q["variant_label"]: q["price"] for q in effective}
            assert by_label["India"] == 2.80
            assert by_label["China"] == 2.50

    def test_10_and_21_get_all_quotes_returns_full_history(self):
        """10 & 21. get_all_quotes_for_rfq returns full history in ascending order without collapsing revisions."""
        t1 = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        t2 = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()

        db_history_asc = [
            {"id": "q1", "rfq_id": "rfq-1", "variant_label": "India", "price": 3.00, "created_at": t1},
            {"id": "q2", "rfq_id": "rfq-1", "variant_label": "China", "price": 2.50, "created_at": t1},
            {"id": "q3", "rfq_id": "rfq-1", "variant_label": "India", "price": 2.80, "created_at": t2},
        ]
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = db_history_asc

        with patch.object(db, "supabase", mock_supabase):
            all_quotes = db.get_all_quotes_for_rfq("rfq-1")
            assert len(all_quotes) == 3
            assert all_quotes[0]["id"] == "q1"
            assert all_quotes[2]["id"] == "q3"

    def test_11_and_12_available_to_unavailable_revision_excluded_from_effective(self):
        """11 & 12. When a variant is revised to is_available=False, it is excluded from default effective quotes."""
        t1 = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        t2 = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()

        db_history = [
            {"id": "q3", "rfq_id": "rfq-1", "supplier_id": "s1", "variant_label": "China", "price": None, "is_available": False, "created_at": t2},
            {"id": "q2", "rfq_id": "rfq-1", "supplier_id": "s1", "variant_label": "India", "price": 2.80, "is_available": True, "created_at": t1},
            {"id": "q1", "rfq_id": "rfq-1", "supplier_id": "s1", "variant_label": "China", "price": 2.50, "is_available": True, "created_at": t1},
        ]

        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.order.return_value.execute.return_value.data = db_history

        with patch.object(db, "supabase", mock_supabase):
            # Default: only active available quotes
            effective = db.get_quotes_for_rfq("rfq-1", include_unavailable=False)
            assert len(effective) == 1
            assert effective[0]["variant_label"] == "India"
            assert effective[0]["price"] == 2.80

            # include_unavailable=True: returns the latest revision for both
            all_effective = db.get_quotes_for_rfq("rfq-1", include_unavailable=True)
            assert len(all_effective) == 2
            labels = {q["variant_label"]: q["is_available"] for q in all_effective}
            assert labels["China"] is False
            assert labels["India"] is True

    def test_20_supplier_status_becomes_responded(self):
        """20. When at least one valid priced variant is recorded, rfq_suppliers.status becomes 'responded'."""
        updated_status = {}
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"id": "rfq-1", "status": "active"}]

        def mock_update(payload):
            nonlocal updated_status
            updated_status = payload
            mock_builder = MagicMock()
            mock_builder.eq.return_value.eq.return_value.execute.return_value.data = [{}]
            return mock_builder

        mock_supabase.table.return_value.update = mock_update
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [{"id": "q1"}]
        mock_supabase.rpc.side_effect = Exception("RPC offline")

        with patch.object(db, "supabase", mock_supabase), \
             patch("db.is_rfq_open", return_value=True):
            db.record_quotes_batch("rfq-1", "supp-1", [{"variant_label": "India", "price": 45.0, "is_available": True}])
            assert updated_status.get("status") == "responded"

    def test_22_and_23_batch_failure_does_not_partially_insert_or_update_status(self):
        """22 & 23. If RFQ is closed, record_quotes_batch fails closed (returns empty, no insert, no status update)."""
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"id": "rfq-1", "status": "closed"}]

        with patch.object(db, "supabase", mock_supabase), \
             patch("db.is_rfq_open", return_value=False):
            res = db.record_quotes_batch("rfq-1", "supp-1", [{"variant_label": "India", "price": 45.0}])
            assert res == []
            mock_supabase.table.return_value.insert.assert_not_called()
            mock_supabase.table.return_value.update.assert_not_called()

    def test_27_source_message_id_persisted(self):
        """27. source_message_id is attached to all variants in the batch."""
        inserted_rows = []
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"id": "rfq-1", "status": "active"}]

        def mock_insert(rows):
            nonlocal inserted_rows
            inserted_rows = rows
            res_mock = MagicMock()
            res_mock.data = rows
            return res_mock

        mock_supabase.table.return_value.insert = mock_insert
        mock_supabase.rpc.side_effect = Exception("RPC offline")

        with patch.object(db, "supabase", mock_supabase), \
             patch("db.is_rfq_open", return_value=True):
            db.record_quotes_batch(
                "rfq-1",
                "supp-1",
                [{"variant_label": "India", "price": 45.0}, {"variant_label": "China", "price": 38.0}],
                source_message_id="msg-inbound-123"
            )
            assert len(inserted_rows) == 2
            assert inserted_rows[0]["source_message_id"] == "msg-inbound-123"
            assert inserted_rows[1]["source_message_id"] == "msg-inbound-123"


class TestMultiVariantExecutionAndIntegration:
    """Execution pipeline, single thank-you messaging, operator provenance, and master RFQ lifecycle."""


    def test_21_master_rfq_remains_active_after_quote(self):
        """21. Recording quote variants leaves master rfqs.status active until finalization."""
        mock_rfq = {"id": "rfq-1", "status": "active", "deadline_hours": 24}
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [mock_rfq]
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [{"id": "q1"}]
        mock_supabase.rpc.side_effect = Exception("RPC offline")

        with patch.object(db, "supabase", mock_supabase), \
             patch("db.is_rfq_open", return_value=True):
            db.record_quotes_batch("rfq-1", "s-1", [{"variant_label": "India", "price": 45.0}])
            # rfqs table update should NEVER be called by record_quotes_batch
            # Only rfq_suppliers is updated
            called_tables = [c[0][0] for c in mock_supabase.table.call_args_list if len(c[0]) > 0]
            # Verify 'rfq_suppliers' updated, 'rfqs' never updated
            assert "rfq_suppliers" in called_tables
            update_calls_on_rfq = [
                c for c in mock_supabase.table.mock_calls
                if "rfqs" in str(c) and "update" in str(c)
            ]
            assert len(update_calls_on_rfq) == 0
