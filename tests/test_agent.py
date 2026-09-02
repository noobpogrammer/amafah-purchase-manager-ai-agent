import os
import sys
import re
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone, timedelta

# Ensure repo root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import target functions
import db
import main
import groq_client


@pytest.fixture
def mock_supabase():
    with patch.object(db, "supabase") as mock_sb:
        yield mock_sb


class TestSupplierCategoryMatching:
    def test_get_suppliers_by_category(self, mock_supabase):
        mock_data = [
            {"id": "supp-1", "name": "Hardware Hub", "category": ["Hardware", "Plumbing"]},
            {"id": "supp-2", "name": "Tools Co", "category": ["Tools"]},
        ]
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.contains.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=mock_data)

        mock_supabase.table.return_value = mock_query

        res = db.get_suppliers_by_category("demo-client-id", "Hardware")
        assert len(res) == 2
        mock_supabase.table.assert_called_with("suppliers")
        mock_query.contains.assert_called_with("category", ["Hardware"])

    def test_create_rfq_and_match_suppliers(self, mock_supabase):
        rfq_inserted = {"id": "rfq-999", "product_name": "Cement 5kg", "category": "Building Materials"}

        # Mock rfq insert
        mock_rfq_table = MagicMock()
        mock_rfq_table.insert.return_value.execute.return_value = MagicMock(data=[rfq_inserted])

        # Mock category matching suppliers
        mock_suppliers = [
            {"id": "s1", "name": "Supplier 1", "phone_number": "123"},
            {"id": "s1", "name": "Supplier 1 Duplicate", "phone_number": "123"}, # duplicate id to test dedup
            {"id": "s2", "name": "Supplier 2", "phone_number": "456"},
        ]

        with patch.object(db, "get_suppliers_by_category", return_value=mock_suppliers):
            mock_rfq_supp_table = MagicMock()
            mock_rfq_supp_table.insert.return_value.execute.return_value = MagicMock(data=[])

            def table_router(table_name):
                if table_name == "rfqs":
                    return mock_rfq_table
                elif table_name == "rfq_suppliers":
                    return mock_rfq_supp_table
                return MagicMock()

            mock_supabase.table.side_effect = table_router

            rfq, matched = db.create_rfq_and_match_suppliers(
                client_id="demo-client-id",
                product_name="Cement 5kg",
                category="Building Materials",
                deadline_hours=24
            )

            assert rfq["id"] == "rfq-999"
            # Deduplication should reduce 3 matched suppliers to 2 unique IDs (s1, s2)
            assert len(matched) == 2
            assert [s["id"] for s in matched] == ["s1", "s2"]


class TestFlagForHumanReviewAndResolve:
    def test_flag_for_human_review(self, mock_supabase):
        mock_table = MagicMock()
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "flag-123", "status": "pending"}])
        mock_supabase.table.return_value = mock_table

        res = db.flag_for_human_review(
            client_id="demo-client",
            supplier_id="supp-1",
            reason="Price contradiction > 10%",
            category="contradictory_information",
            raw_message="Old $50 -> New $85"
        )
        assert res[0]["id"] == "flag-123"
        mock_supabase.table.assert_called_with("flagged_for_review")

    def test_resolve_flag(self, mock_supabase):
        mock_table = MagicMock()
        mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "flag-123", "status": "resolved", "resolved_at": "2026-09-02T00:00:00Z"}]
        )
        mock_supabase.table.return_value = mock_table

        result = db.resolve_flag("flag-123")
        assert result["status"] == "resolved"
        assert result["id"] == "flag-123"
        mock_supabase.table.assert_called_with("flagged_for_review")

    def test_update_rfq_status(self, mock_supabase):
        mock_rfq_table = MagicMock()
        mock_rfq_table.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "rfq-100", "status": "closed"}]
        )
        mock_supp_table = MagicMock()
        mock_supp_table.update.return_value.eq.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
        mock_pending_table = MagicMock()
        mock_pending_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        def table_router(t):
            if t == "rfqs":
                return mock_rfq_table
            elif t == "rfq_suppliers":
                return mock_supp_table
            elif t == "pending_clarifications":
                return mock_pending_table
            return MagicMock()

        mock_supabase.table.side_effect = table_router

        result = db.update_rfq_status("rfq-100", "closed")
        assert result["status"] == "closed"
        mock_rfq_table.update.assert_called_with({"status": "closed"})

    def test_close_rfq_cascades_status_updates(self, mock_supabase):
        mock_rfq_table = MagicMock()
        mock_rfq_table.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "rfq-100", "status": "closed"}]
        )
        mock_supp_table = MagicMock()
        mock_supp_table.update.return_value.eq.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
        
        mock_pending_table = MagicMock()
        mock_pending_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "p-1", "pending_rfq_ids": ["rfq-100", "rfq-200"], "status": "awaiting_reply"}]
        )
        mock_pending_table.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        def table_router(t):
            if t == "rfqs":
                return mock_rfq_table
            elif t == "rfq_suppliers":
                return mock_supp_table
            elif t == "pending_clarifications":
                return mock_pending_table
            return MagicMock()

        mock_supabase.table.side_effect = table_router

        result = db.close_rfq("rfq-100", "closed")
        assert result["id"] == "rfq-100"
        # Assert rfq_suppliers updated sent/clarifying to no_response
        mock_supp_table.update.assert_called_with({"status": "no_response"})
        # Assert pending_clarification abandoned
        mock_pending_table.update.assert_called_with({"status": "abandoned"})

    def test_closed_rfqs_filtered_out(self, mock_supabase):
        mock_data = [
            {"id": "rs-1", "rfqs": {"id": "rfq-1", "status": "active"}},
            {"id": "rs-2", "rfqs": {"id": "rfq-2", "status": "closed"}},
        ]
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.in_.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=mock_data)
        mock_supabase.table.return_value = mock_query

        open_rfqs = db.get_open_rfqs_for_supplier("supp-1")
        assert len(open_rfqs) == 1
        assert open_rfqs[0]["rfqs"]["id"] == "rfq-1"


class TestClarificationRoundsCap:
    def test_count_clarification_rounds(self, mock_supabase):
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            count=2, data=[{"id": "c1"}, {"id": "c2"}]
        )
        mock_supabase.table.return_value = mock_table

        count = db.count_clarification_rounds("supp-1", "client-1")
        assert count == 2

    def test_create_pending_clarification_with_extracted_fields(self, mock_supabase):
        mock_pending_table = MagicMock()
        mock_pending_table.insert.return_value.execute.return_value = MagicMock(data=[])
        mock_rfq_supp_table = MagicMock()
        mock_rfq_supp_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        def table_router(t):
            if t == "pending_clarifications":
                return mock_pending_table
            elif t == "rfq_suppliers":
                return mock_rfq_supp_table
            return MagicMock()

        mock_supabase.table.side_effect = table_router

        db.create_pending_clarification(
            client_id="client-1",
            supplier_id="supp-1",
            candidate_rfq_ids=["rfq-1"],
            raw_message="10 aed 5 days",
            extracted_price=10.0,
            extracted_delivery="5 days",
            extracted_notes="warranty included"
        )

        mock_pending_table.insert.assert_called_once_with({
            "client_id": "client-1",
            "supplier_id": "supp-1",
            "pending_rfq_ids": ["rfq-1"],
            "raw_message": "10 aed 5 days",
            "extracted_price": 10.0,
            "extracted_delivery": "5 days",
            "extracted_notes": "warranty included",
        })


class TestAntiUUIDClarificationRule:
    """Verifies that clarifying questions never contain internal UUID strings."""

    def test_clarifying_question_contains_no_uuids(self):
        rfq_context_stem = (
            "- RFQ ID: c8cc719d-19c0-422b-8a46-13e89cbd28bb | Product: Cement 5kg | Specs: Standard | Qty: 50\n"
            "- RFQ ID: 57656d76-d822-4a99-8ad5-75c97139e7ed | Product: Cement 10kg | Specs: Premium | Qty: 30"
        )

        res = groq_client.resolve_clarification(
            message_text="cement 10 aed",
            candidate_rfqs_context=rfq_context_stem,
            previous_message="Initial reply: 10 aed 5 days"
        )

        assert res["tool_name"] == "request_clarification"
        question = res["arguments"]["clarifying_question"]

        # Regex for standard 36-char UUID format (8-4-4-4-12 hex chars)
        uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
        assert not uuid_pattern.search(question), f"Clarifying question contained raw UUID: '{question}'"


class TestReminderSystemAudit:
    """Detailed audit tests for check_deadlines_and_reminders()."""

    @pytest.mark.asyncio
    async def test_reminder_50_percent_trigger(self, mock_supabase):
        now = datetime.now(timezone.utc)
        # RFQ created 12.5 hours ago with 24h deadline = 52% elapsed
        sent_at = (now - timedelta(hours=12.5)).isoformat()

        mock_active_item = {
            "id": "rfq-supp-1",
            "sent_at": sent_at,
            "reminder_count": 0,
            "rfqs": {
                "id": "rfq-100",
                "product_name": "Cement 5kg",
                "deadline_hours": 24,
                "created_at": sent_at,
            },
            "suppliers": {
                "id": "supp-1",
                "name": "Supplier 1",
                "phone_number": "923362853198",
            },
        }

        with patch.object(db, "get_active_rfq_suppliers_with_deadlines", return_value=[mock_active_item]), \
             patch.object(db, "log_message") as mock_log, \
             patch.object(db, "update_rfq_supplier_reminder") as mock_update, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            await main.check_deadlines_and_reminders()

            mock_enqueue.assert_called_once()
            assert "checking in" in mock_enqueue.call_args[0][1].lower()
            mock_update.assert_called_once_with("rfq-supp-1", 1)

    @pytest.mark.asyncio
    async def test_reminder_70_percent_trigger(self, mock_supabase):
        now = datetime.now(timezone.utc)
        # RFQ created 17.5 hours ago with 24h deadline = ~73% elapsed
        sent_at = (now - timedelta(hours=17.5)).isoformat()

        mock_active_item = {
            "id": "rfq-supp-1",
            "sent_at": sent_at,
            "reminder_count": 1,
            "rfqs": {
                "id": "rfq-100",
                "product_name": "Cement 5kg",
                "deadline_hours": 24,
                "created_at": sent_at,
            },
            "suppliers": {
                "id": "supp-1",
                "name": "Supplier 1",
                "phone_number": "923362853198",
            },
        }

        with patch.object(db, "get_active_rfq_suppliers_with_deadlines", return_value=[mock_active_item]), \
             patch.object(db, "log_message"), \
             patch.object(db, "update_rfq_supplier_reminder") as mock_update, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            await main.check_deadlines_and_reminders()

            mock_enqueue.assert_called_once()
            assert "when ready" in mock_enqueue.call_args[0][1].lower()
            mock_update.assert_called_once_with("rfq-supp-1", 2)

    @pytest.mark.asyncio
    async def test_reminder_90_percent_trigger(self, mock_supabase):
        now = datetime.now(timezone.utc)
        # RFQ created 22 hours ago with 24h deadline = ~91% elapsed
        sent_at = (now - timedelta(hours=22)).isoformat()

        mock_active_item = {
            "id": "rfq-supp-1",
            "sent_at": sent_at,
            "reminder_count": 2,
            "rfqs": {
                "id": "rfq-100",
                "product_name": "Cement 5kg",
                "deadline_hours": 24,
                "created_at": sent_at,
            },
            "suppliers": {
                "id": "supp-1",
                "name": "Supplier 1",
                "phone_number": "923362853198",
            },
        }

        with patch.object(db, "get_active_rfq_suppliers_with_deadlines", return_value=[mock_active_item]), \
             patch.object(db, "log_message"), \
             patch.object(db, "update_rfq_supplier_reminder") as mock_update, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            await main.check_deadlines_and_reminders()

            mock_enqueue.assert_called_once()
            assert "final reminder" in mock_enqueue.call_args[0][1].lower()
            mock_update.assert_called_once_with("rfq-supp-1", 3)

    @pytest.mark.asyncio
    async def test_deadline_expiration_100_percent(self, mock_supabase):
        now = datetime.now(timezone.utc)
        # RFQ created 25 hours ago with 24h deadline = 104% elapsed (expired)
        sent_at = (now - timedelta(hours=25)).isoformat()

        mock_active_item = {
            "id": "rfq-supp-1",
            "sent_at": sent_at,
            "reminder_count": 3,
            "rfqs": {
                "id": "rfq-100",
                "product_name": "Cement 5kg",
                "deadline_hours": 24,
                "created_at": sent_at,
            },
            "suppliers": {
                "id": "supp-1",
                "name": "Supplier 1",
                "phone_number": "923362853198",
            },
        }

        with patch.object(db, "get_active_rfq_suppliers_with_deadlines", return_value=[mock_active_item]), \
             patch.object(db, "log_message"), \
             patch.object(db, "close_rfq") as mock_close_rfq, \
             patch.object(main, "check_and_auto_rank") as mock_auto_rank, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            await main.check_deadlines_and_reminders()

            mock_enqueue.assert_called_once()
            assert "closed as the deadline has passed" in mock_enqueue.call_args[0][1].lower()
            mock_close_rfq.assert_called_once_with("rfq-100", "closed")
            mock_auto_rank.assert_called_once_with("rfq-100")

    @pytest.mark.asyncio
    async def test_already_responded_suppliers_ignored(self, mock_supabase):
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[])
        mock_supabase.table.return_value = mock_query

        items = db.get_active_rfq_suppliers_with_deadlines()
        assert len(items) == 0
        mock_query.eq.assert_called_with("status", "sent")
