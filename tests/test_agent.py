import os
import sys
import re
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock, ANY
from datetime import datetime, timezone, timedelta

# Ensure repo root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import target functions
import db
import main
import openclaw_bridge


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

        # Mock rfq RPC
        mock_supabase.rpc.return_value.execute.return_value = MagicMock(data=[rfq_inserted])

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
                if table_name == "rfq_suppliers":
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

            # Assert deadline_hours was passed to RPC
            rpc_args = mock_supabase.rpc.call_args[0]
            assert rpc_args[0] == "create_rfq_with_constraints_rpc"
            assert rpc_args[1]["p_deadline_hours"] == 24


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

    def test_log_webhook_error(self, mock_supabase):
        mock_table = MagicMock()
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "err-1"}])
        mock_supabase.table.return_value = mock_table

        db.log_webhook_error("Simulated Error", "Traceback details...", {"key": "val"})
        mock_supabase.table.assert_called_with("webhook_errors")
        mock_table.insert.assert_called_once_with({
            "error_message": "Simulated Error",
            "traceback": "Traceback details...",
            "raw_payload": {"key": "val"},
        })


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
        assert mock_rfq_table.update.call_args[0][0]["status"] == "closed"

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
        assert mock_rfq_table.update.call_args[0][0]["status"] == "closed"
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
        mock_query.in_.assert_called_with("status", ["sent", "clarifying", "responded"])

    def test_get_open_rfqs_includes_responded_status_for_active_rfq(self, mock_supabase):
        mock_data = [
            {"id": "rs-1", "status": "responded", "rfqs": {"id": "rfq-1", "status": "active"}},
            {"id": "rs-2", "status": "responded", "rfqs": {"id": "rfq-2", "status": "closed"}},
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

    def test_get_quotes_for_rfq_returns_only_latest_quote_per_supplier(self, mock_supabase):
        mock_quotes_data = [
            {
                "id": "q-revised",
                "supplier_id": "sup-1",
                "price": 40.0,
                "created_at": "2026-09-08T12:00:00Z",
                "suppliers": {"name": "Supplier 1"},
            },
            {
                "id": "q-initial",
                "supplier_id": "sup-1",
                "price": 50.0,
                "created_at": "2026-09-08T10:00:00Z",
                "suppliers": {"name": "Supplier 1"},
            },
            {
                "id": "q-sup2",
                "supplier_id": "sup-2",
                "price": 45.0,
                "created_at": "2026-09-08T11:00:00Z",
                "suppliers": {"name": "Supplier 2"},
            },
        ]
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=mock_quotes_data)
        mock_supabase.table.return_value = mock_query

        quotes = db.get_quotes_for_rfq("rfq-1")
        assert len(quotes) == 2
        # sup-1 should be the revised quote with price 40.0
        sup1_quote = next(q for q in quotes if q["supplier_id"] == "sup-1")
        assert sup1_quote["id"] == "q-revised"
        assert sup1_quote["price"] == 40.0
        # sup-2 should be present
        sup2_quote = next(q for q in quotes if q["supplier_id"] == "sup-2")
        assert sup2_quote["id"] == "q-sup2"
        assert sup2_quote["price"] == 45.0

    def test_quote_revision_lifecycle_and_post_closure_rejection(self, mock_supabase):
        # 1. Initial state: supplier has responded, RFQ is active
        mock_active_data = [
            {"id": "rs-1", "status": "responded", "supplier_id": "sup-1", "rfqs": {"id": "rfq-1", "status": "active"}}
        ]
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.in_.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=mock_active_data)
        mock_supabase.table.return_value = mock_query

        # Before closure: get_open_rfqs finds active RFQ for revision
        open_rfqs = db.get_open_rfqs_for_supplier("sup-1")
        assert len(open_rfqs) == 1
        assert open_rfqs[0]["rfqs"]["id"] == "rfq-1"

        # 2. Revised quote recorded: get_quotes_for_rfq returns the revised quote
        mock_quotes_data = [
            {"id": "q-revised", "supplier_id": "sup-1", "price": 42.0, "created_at": "2026-09-08T12:00:00Z"},
            {"id": "q-initial", "supplier_id": "sup-1", "price": 50.0, "created_at": "2026-09-08T10:00:00Z"},
        ]
        mock_quote_query = MagicMock()
        mock_quote_query.select.return_value = mock_quote_query
        mock_quote_query.eq.return_value = mock_quote_query
        mock_quote_query.order.return_value = mock_quote_query
        mock_quote_query.execute.return_value = MagicMock(data=mock_quotes_data)
        mock_supabase.table.return_value = mock_quote_query

        quotes = db.get_quotes_for_rfq("rfq-1")
        assert len(quotes) == 1
        assert quotes[0]["id"] == "q-revised"
        assert quotes[0]["price"] == 42.0

        # 3. RFQ is closed
        mock_closed_data = [
            {"id": "rs-1", "status": "responded", "supplier_id": "sup-1", "rfqs": {"id": "rfq-1", "status": "closed"}}
        ]
        mock_closed_query = MagicMock()
        mock_closed_query.select.return_value = mock_closed_query
        mock_closed_query.eq.return_value = mock_closed_query
        mock_closed_query.in_.return_value = mock_closed_query
        mock_closed_query.execute.return_value = MagicMock(data=mock_closed_data)
        mock_supabase.table.return_value = mock_closed_query

        # 4. Message sent after closure: no_open_rfq (empty list returned)
        closed_rfqs = db.get_open_rfqs_for_supplier("sup-1")
        assert len(closed_rfqs) == 0


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
            extracted_notes="warranty included",
            round_number=2,
        )

        mock_pending_table.insert.assert_called_once_with({
            "client_id": "client-1",
            "supplier_id": "supp-1",
            "pending_rfq_ids": ["rfq-1"],
            "raw_message": "10 aed 5 days",
            "extracted_price": 10.0,
            "extracted_delivery": "5 days",
            "extracted_notes": "warranty included",
            "round_number": 2,
            "no_progress_count": 0,
        })


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
                "status": "active",
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
                "status": "active",
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
                "status": "active",
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
    async def test_deadline_expiration_closes_rfq_and_ranks_at_rfq_level(self, mock_supabase):
        now = datetime.now(timezone.utc)
        expired_due_by = (now - timedelta(hours=1)).isoformat()

        mock_expired_rfq = {
            "id": "rfq-100",
            "product_name": "Cement 5kg",
            "deadline_hours": 24,
            "due_by": expired_due_by,
            "status": "active",
            "rfq_suppliers": [
                {
                    "id": "rfq-supp-1",
                    "status": "responded",
                    "suppliers": {"id": "supp-1", "name": "Supplier 1", "phone_number": "923362853198"},
                },
                {
                    "id": "rfq-supp-2",
                    "status": "no_response",
                    "suppliers": {"id": "supp-2", "name": "Supplier 2", "phone_number": "923362853199"},
                }
            ]
        }

        mock_quotes = [
            {"id": "q-1", "rfq_id": "rfq-100", "supplier_id": "supp-1", "price": 50.0, "suppliers": {"name": "Supplier 1"}}
        ]

        with patch.object(db, "get_active_rfqs_past_deadline", return_value=[mock_expired_rfq]), \
             patch.object(db, "get_active_rfq_suppliers_with_deadlines", return_value=[]), \
             patch.object(db, "claim_rfq_for_finalization", return_value=True) as mock_claim_rfq, \
             patch.object(db, "get_message_by_event_key", return_value=None), \
             patch.object(db, "log_message", return_value="msg-log-123") as mock_log, \
             patch.object(db, "get_quotes_for_rfq", return_value=mock_quotes), \
             patch.object(db, "ranking_exists", return_value=False), \
             patch.object(main, "generate_ranking") as mock_generate_ranking, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            await main.check_deadlines_and_reminders()

            # Verify participating suppliers were notified
            assert mock_enqueue.call_count == 2
            assert "closed as the deadline has passed" in mock_enqueue.call_args_list[0][0][1].lower()

            # Verify master RFQ claimed for finalization
            mock_claim_rfq.assert_called_once_with("rfq-100")

            # Verify final evaluation/ranking ran with latest quotes
            mock_generate_ranking.assert_called_once_with("rfq-100")

    @pytest.mark.asyncio
    async def test_rfq_with_all_suppliers_responding_remains_active_until_deadline(self, mock_supabase):
        """When all suppliers respond before deadline, RFQ remains active and is only closed when due_by passes."""
        now = datetime.now(timezone.utc)
        future_due_by = (now + timedelta(hours=5)).isoformat()

        mock_active_rfq = {
            "id": "rfq-200",
            "product_name": "Steel Rods",
            "deadline_hours": 24,
            "due_by": future_due_by,
            "status": "active",
        }

        # get_active_rfqs_past_deadline returns empty because due_by is in the future
        # get_active_rfq_suppliers_with_deadlines returns empty because all suppliers already responded
        with patch.object(db, "get_active_rfqs_past_deadline", return_value=[]), \
             patch.object(db, "get_active_rfq_suppliers_with_deadlines", return_value=[]), \
             patch.object(db, "close_rfq") as mock_close_rfq, \
             patch.object(main, "generate_ranking") as mock_generate_ranking:

            await main.check_deadlines_and_reminders()

            # RFQ must NOT be closed prematurely
            mock_close_rfq.assert_not_called()
            # Ranking must NOT run prematurely before deadline
            mock_generate_ranking.assert_not_called()

    @pytest.mark.asyncio
    async def test_rfq_with_some_suppliers_pending_sends_reminders_and_closes_at_due_by(self, mock_supabase):
        """Pending suppliers receive reminders, responded suppliers do not, and RFQ closes at deadline."""
        now = datetime.now(timezone.utc)
        sent_at = (now - timedelta(hours=12.5)).isoformat() # 52% of 24h

        pending_item = {
            "id": "rfq-supp-pending",
            "sent_at": sent_at,
            "reminder_count": 0,
            "rfqs": {"id": "rfq-300", "product_name": "Paints", "status": "active", "deadline_hours": 24},
            "suppliers": {"id": "supp-pending", "name": "Pending Supp", "phone_number": "11111111"},
        }

        with patch.object(db, "get_active_rfqs_past_deadline", return_value=[]), \
             patch.object(db, "get_active_rfq_suppliers_with_deadlines", return_value=[pending_item]), \
             patch.object(db, "log_message"), \
             patch.object(db, "update_rfq_supplier_reminder") as mock_update_reminder, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            await main.check_deadlines_and_reminders()

            # Reminder sent to pending supplier
            mock_enqueue.assert_called_once()
            assert "checking in" in mock_enqueue.call_args[0][1].lower()
            mock_update_reminder.assert_called_once_with("rfq-supp-pending", 1)

    def test_get_active_rfqs_past_deadline_filters_by_due_by(self, mock_supabase):
        now = datetime.now(timezone.utc)
        past_due = (now - timedelta(hours=2)).isoformat()
        future_due = (now + timedelta(hours=5)).isoformat()

        mock_data = [
            {"id": "rfq-expired", "status": "active", "due_by": past_due, "deadline_hours": 24},
            {"id": "rfq-active", "status": "active", "due_by": future_due, "deadline_hours": 24},
        ]
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=mock_data)
        mock_supabase.table.return_value = mock_query

        expired = db.get_active_rfqs_past_deadline()
        assert len(expired) == 1
        assert expired[0]["id"] == "rfq-expired"

    def test_supplier_status_does_not_affect_rfq_closure(self, mock_supabase):
        """An expired RFQ is closed regardless of whether suppliers are responded, clarifying, sent, or no_response."""
        now = datetime.now(timezone.utc)
        past_due = (now - timedelta(hours=1)).isoformat()

        mock_rfq = {
            "id": "rfq-all-responded-expired",
            "status": "active",
            "due_by": past_due,
            "rfq_suppliers": [
                {"id": "rs-1", "status": "responded"},
                {"id": "rs-2", "status": "responded"},
            ]
        }
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[mock_rfq])
        mock_supabase.table.return_value = mock_query

        expired = db.get_active_rfqs_past_deadline()
        assert len(expired) == 1
        assert expired[0]["id"] == "rfq-all-responded-expired"

    @pytest.mark.asyncio
    async def test_already_responded_suppliers_ignored_for_reminders(self, mock_supabase):
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[])
        mock_supabase.table.return_value = mock_query

        items = db.get_active_rfq_suppliers_with_deadlines()
        assert len(items) == 0
        mock_query.eq.assert_called_with("status", "sent")


class TestQuotedMessageMatching:
    def test_update_and_get_rfq_supplier_by_sent_message_id(self, mock_supabase):
        mock_table = MagicMock()
        mock_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_supabase.table.return_value = mock_table

        db.update_rfq_supplier_sent_message_id("rfq-1", "supp-1", "3EB012345")
        mock_supabase.table.assert_called_with("rfq_suppliers")

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.in_.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[
            {"id": "rfq-supp-1", "rfq_id": "rfq-1", "supplier_id": "supp-1", "sent_message_id": "3EB012345", "rfqs": {"id": "rfq-1", "status": "active", "product_name": "30W Panel"}}
        ])
        mock_supabase.table.return_value = mock_query

        res = db.get_rfq_supplier_by_sent_message_id("supp-1", "3EB012345")
        assert res is not None
        assert res["rfq_id"] == "rfq-1"
        assert res["rfqs"]["product_name"] == "30W Panel"

    def test_get_rfq_supplier_by_quoted_text(self, mock_supabase):
        open_rfqs = [
            {"id": "rs-1", "rfqs": {"id": "rfq-30w", "product_name": "led panel", "specs": "30 w", "status": "active"}},
            {"id": "rs-2", "rfqs": {"id": "rfq-60w", "product_name": "led panel", "specs": "60 w", "status": "active"}},
        ]
        with patch.object(db, "get_open_rfqs_for_supplier", return_value=open_rfqs):
            quoted_text = "Hi! Requesting quote for Product: led panel, Specs: 60 w"
            matched = db.get_rfq_supplier_by_quoted_text("supp-1", quoted_text)
            assert matched is not None
            assert matched["rfqs"]["id"] == "rfq-60w"

    def test_revert_unresolved_candidates(self, mock_supabase):
        db.revert_unresolved_candidates(supplier_id="supp-1", resolved_rfq_id="rfq-1", candidate_rfq_ids=["rfq-1", "rfq-2", "rfq-3"])
        mock_supabase.table.assert_not_called()


    def test_get_client_by_instance_lookup(self, mock_supabase):
        mock_clients = [
            {"id": "c-1", "name": "Amafah Dubai", "whatsapp_instance": "Mohammad"},
            {"id": "c-2", "name": "Al Nonn Hardware", "whatsapp_instance": "al-nonn"},
        ]
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=[mock_clients[0]])
        mock_supabase.table.return_value = mock_query

        client = db.get_client_by_instance("Mohammad")
        assert client is not None
        assert client["id"] == "c-1"

    def test_get_supplier_by_phone_any_client_formats(self, mock_supabase):
        mock_suppliers = [
            {"id": "s-1", "name": "Pak Supplier", "phone_number": "+92 3188012805"},
            {"id": "s-2", "name": "UAE Supplier", "phone_number": "971501234567"},
        ]
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        # First eq query for exact match fails (returns empty list)
        mock_query.execute.side_effect = [
            MagicMock(data=[]),            # exact match attempt
            MagicMock(data=mock_suppliers) # fallback scan
        ]
        mock_supabase.table.return_value = mock_query

        # Search with cleaned plain digits
        res = db.get_supplier_by_phone_any_client("923188012805")
        assert res is not None
        assert res["id"] == "s-1"


class TestRFQCreationValidation:
    def test_rfq_create_request_valid(self):
        req = main.RFQCreateRequest(
            product_name="  LED Panel 60W  ",
            category="  Building Materials  ",
            specs="  60W, 60x60cm  ",
            quantity=30,
            deadline_hours=24
        )
        assert req.product_name == "LED Panel 60W"
        assert req.category == "Building Materials"
        assert req.specs == "60W, 60x60cm"
        assert req.quantity == 30

    def test_rfq_create_request_missing_or_blank_specs(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            main.RFQCreateRequest(
                product_name="LED Panel",
                category="Hardware",
                specs="   "
            )
        assert "specs" in str(exc.value)

    def test_rfq_create_request_missing_or_blank_category(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            main.RFQCreateRequest(
                product_name="LED Panel",
                category="",
                specs="60W"
            )
        assert "category" in str(exc.value)

    def test_rfq_create_request_missing_or_blank_product_name(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            main.RFQCreateRequest(
                product_name="   ",
                category="Hardware",
                specs="60W"
            )
        assert "product_name" in str(exc.value)

    def test_rfq_create_request_missing_deadline_hours(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            main.RFQCreateRequest(
                product_name="LED Panel",
                category="Hardware",
                specs="60W",
                deadline_hours=None,
            )
        assert "deadline_hours" in str(exc.value)

    def test_bulk_import_falls_back_to_raw_description_when_cleaned_name_is_empty(self):
        csv_contents = "Description,Qty\n0 pcs,1\n"
        rows = main.parse_material_requisition_csv(csv_contents)
        assert len(rows) == 1
        assert rows[0]["product_name"] == "0 pcs"

    def test_bulk_import_handles_dash_placeholders_and_missing_numeric_values(self):
        csv_contents = "Description,Qty,Last Cost\n-,-,-\n"
        rows = main.parse_material_requisition_csv(csv_contents)
        assert len(rows) == 1
        assert rows[0]["product_name"] == "-"
        assert rows[0]["quantity"] is None
        assert rows[0]["last_quote"] is None

    @pytest.mark.asyncio
    async def test_bulk_create_rfq_endpoint_accepts_csv_file(self, mock_supabase):
        csv_contents = "Sl. #,Item Code,Description,Unit,Qty,Last Cost\n1,ITEM-001,""MULTI PURPOSE LADDER ALUMINIUM 4X5 0 pcs"",pcs,5,12\n"

        mock_rfq_data = [{"id": "rfq-1001", "product_name": "MULTI PURPOSE LADDER ALUMINIUM 4X5", "category": "Hardware"}]
        mock_suppliers = [{"id": "s-1", "name": "Supplier 1", "phone_number": "923362853198"}]

        with patch.object(db, "create_rfq_and_match_suppliers", return_value=(mock_rfq_data[0], mock_suppliers)) as mock_create, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue, \
             patch.object(db, "log_message") as mock_log:
            from fastapi.testclient import TestClient
            import auth as _auth
            client = TestClient(main.app)
            with patch.object(_auth, "verify_jwt", return_value={"sub": "user-1"}), \
                 patch.object(db, "get_profile_by_id", return_value={"id": "user-1", "client_id": "client-xyz", "role": "member"}):
                headers = {"Authorization": "Bearer faketoken"}
                response = client.post(
                    "/rfq/bulk-create",
                    files={"file": ("material_requisition.csv", csv_contents.encode("utf-8"), "text/csv")},
                    data={"category": "Hardware", "deadline_hours": "24"},
                    headers=headers,
                )

        assert response.status_code == 200, response.text
        assert response.json()["created_count"] == 1
        mock_create.assert_called_once()
        mock_log.assert_called_once()
        mock_enqueue.assert_called_once()
        enqueue_args = mock_enqueue.call_args[0]
        assert enqueue_args[0] == "923362853198"
        assert "MULTI PURPOSE LADDER ALUMINIUM 4X5" in enqueue_args[1]
        assert "Quote Required Within: 24 hour(s)" in enqueue_args[1]

    @pytest.mark.asyncio
    async def test_bulk_create_rfq_endpoint_uses_row_level_overrides_and_deadlines(self, mock_supabase):
        csv_contents = "Description,Qty,Last Cost\nPipe 1-inch,10,25\n"

        mock_rfq_data = [{"id": "rfq-2001", "product_name": "Updated Pipe", "category": "Electrical"}]
        mock_suppliers = [{"id": "s-2", "name": "Supplier 2", "phone_number": "923362853199"}]

        with patch.object(db, "create_rfq_and_match_suppliers", return_value=(mock_rfq_data[0], mock_suppliers)) as mock_create, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue, \
             patch.object(db, "log_message") as mock_log:
            from fastapi.testclient import TestClient
            import auth as _auth
            client = TestClient(main.app)
            with patch.object(_auth, "verify_jwt", return_value={"sub": "user-1"}), \
                 patch.object(db, "get_profile_by_id", return_value={"id": "user-1", "client_id": "client-xyz", "role": "member"}):
                headers = {"Authorization": "Bearer faketoken"}
                response = client.post(
                    "/rfq/bulk-create",
                    files={"file": ("material_requisition.csv", csv_contents.encode("utf-8"), "text/csv")},
                    data={
                        "category": "Hardware",
                        "deadline_hours": "24",
                        "row_updates": '[{"product_name":"Updated Pipe","quantity":99,"category":"Electrical","deadline_hours":12}]',
                    },
                    headers=headers,
                )

        assert response.status_code == 200, response.text
        assert response.json()["created_count"] == 1
        mock_create.assert_called_once_with(
            client_id="client-xyz",
            product_name="Updated Pipe",
            category="Electrical",
            deadline_hours=12,
            specs=None,
            quantity=99,
            flexibility=None,
            authorized_by=None,
            last_quote=25.0,
        )
        mock_log.assert_called_once()
        mock_enqueue.assert_called_once()
        enqueue_args = mock_enqueue.call_args[0]
        assert enqueue_args[0] == "923362853199"
        assert "Updated Pipe" in enqueue_args[1]
        assert "Quote Required Within: 12 hour(s)" in enqueue_args[1]


class TestPhase2ConversationLifecycle:
    """Tests for Phase 2: Responded supplier conversational activity, quote revisions, isolation, and thank-you continuity."""


    def test_is_rfq_open_helper(self):
        """Validates is_rfq_open handles active, expired, and closed RFQs."""
        now = datetime.now(timezone.utc)
        future_iso = (now + timedelta(hours=2)).isoformat()
        past_iso = (now - timedelta(hours=2)).isoformat()

        # Active with future deadline -> Open
        assert db.is_rfq_open({"status": "active", "due_by": future_iso}) is True

        # Active without due_by -> Open (fallback)
        assert db.is_rfq_open({"status": "active"}) is True

        # Active with past deadline -> Closed
        assert db.is_rfq_open({"status": "active", "due_by": past_iso}) is False

        # Status 'closed' -> Closed
        assert db.is_rfq_open({"status": "closed", "due_by": future_iso}) is False
        assert db.is_rfq_open({"status": "cancelled"}) is False
        assert db.is_rfq_open(None) is False


class TestPhase3QuoteRevisionsAndEffectiveQuotes:
    """Tests for Phase 3: Quote Revisions, History Preservation, Deterministic Effective Quote, and Ranking."""

    def test_record_quote_stores_separate_row_and_preserves_history(self, mock_supabase):
        """Every quote and revision is stored as a separate row in quotes table."""
        mock_quotes_table = MagicMock()
        mock_quotes_table.insert.return_value.execute.return_value = MagicMock(data=[])
        mock_supp_table = MagicMock()
        mock_supp_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_rfqs_table = MagicMock()
        mock_rfqs_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[{
            "id": "rfq-1",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        }])

        def table_router(t):
            if t == "quotes":
                return mock_quotes_table
            elif t == "rfq_suppliers":
                return mock_supp_table
            elif t == "rfqs":
                return mock_rfqs_table
            return MagicMock()

        mock_supabase.table.side_effect = table_router

        # 1. Initial quote
        db.record_quote(
            rfq_id="rfq-1",
            supplier_id="supp-1",
            price=100.0,
            delivery_time="3 days",
            quality_notes="1 yr warranty",
            raw_message="100 AED 3 days",
            confidence="high"
        )
        mock_quotes_table.insert.assert_called_with({
            "rfq_id": "rfq-1",
            "supplier_id": "supp-1",
            "price": 100.0,
            "delivery_time": "3 days",
            "quality_notes": "1 yr warranty",
            "raw_message": "100 AED 3 days",
            "confidence": "high"
        })
        mock_supp_table.update.assert_called_with({"status": "responded"})

        # 2. Revision 1
        db.record_quote(
            rfq_id="rfq-1",
            supplier_id="supp-1",
            price=95.0,
            delivery_time="2 days",
            quality_notes="1 yr warranty",
            raw_message="Revised: 95 AED 2 days",
            confidence="high"
        )
        assert mock_quotes_table.insert.call_count == 2
        mock_quotes_table.insert.assert_called_with({
            "rfq_id": "rfq-1",
            "supplier_id": "supp-1",
            "price": 95.0,
            "delivery_time": "2 days",
            "quality_notes": "1 yr warranty",
            "raw_message": "Revised: 95 AED 2 days",
            "confidence": "high"
        })

        # 3. Revision 2
        db.record_quote(
            rfq_id="rfq-1",
            supplier_id="supp-1",
            price=92.0,
            delivery_time="1 day",
            quality_notes="2 yr warranty",
            raw_message="Final offer: 92 AED 1 day",
            confidence="high"
        )
        assert mock_quotes_table.insert.call_count == 3
        # Supplier status remains 'responded'
        mock_supp_table.update.assert_called_with({"status": "responded"})

    def test_record_quote_directly_rejects_closed_or_expired_rfq(self, mock_supabase):
        """db.record_quote itself prevents persisting quotes if RFQ is closed or past deadline."""
        mock_quotes_table = MagicMock()
        mock_rfqs_table = MagicMock()
        # RFQ is past due_by deadline
        mock_rfqs_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[{
            "id": "rfq-expired",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        }])

        def table_router(t):
            if t == "quotes":
                return mock_quotes_table
            elif t == "rfqs":
                return mock_rfqs_table
            return MagicMock()

        mock_supabase.table.side_effect = table_router

        res = db.record_quote(
            rfq_id="rfq-expired",
            supplier_id="supp-1",
            price=90.0,
            raw_message="Late quote"
        )
        assert res is None
        mock_quotes_table.insert.assert_not_called()

    def test_effective_quote_secondary_ordering_on_tie(self, mock_supabase):
        """get_quotes_for_rfq breaks ties on identical created_at using id descending."""
        mock_quotes_db = [
            {"id": "q-99", "rfq_id": "rfq-1", "supplier_id": "supp-A", "price": 90.0, "created_at": "2026-09-11T10:00:00Z", "suppliers": {"name": "Supplier A"}},
            {"id": "q-01", "rfq_id": "rfq-1", "supplier_id": "supp-A", "price": 100.0, "created_at": "2026-09-11T10:00:00Z", "suppliers": {"name": "Supplier A"}},
        ]
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=mock_quotes_db)
        mock_supabase.table.return_value = mock_query

        quotes = db.get_quotes_for_rfq("rfq-1")
        assert len(quotes) == 1
        assert quotes[0]["id"] == "q-99"
        assert quotes[0]["price"] == 90.0
        # Verify query requested ordering by created_at desc AND id desc
        order_calls = mock_query.order.call_args_list
        assert order_calls[0].args == ("created_at",)
        assert order_calls[0].kwargs == {"desc": True}
        assert order_calls[1].args == ("id",)
        assert order_calls[1].kwargs == {"desc": True}

    def test_effective_quote_deterministic_multi_supplier(self, mock_supabase):
        """
        Tests that get_quotes_for_rfq returns only the latest effective quote per supplier
        when multiple suppliers have submitted multiple revisions.
        Supplier A: 100 -> 95 -> 92
        Supplier B: 110 -> 105
        Supplier C: 98
        Effective: A=92, B=105, C=98
        """
        mock_quotes_db = [
            # Ordered newest first (created_at desc)
            {"id": "q-a-3", "rfq_id": "rfq-1", "supplier_id": "supp-A", "price": 92.0, "delivery_time": "1 day", "created_at": "2026-09-11T12:00:00Z", "suppliers": {"name": "Supplier A"}},
            {"id": "q-b-2", "rfq_id": "rfq-1", "supplier_id": "supp-B", "price": 105.0, "delivery_time": "2 days", "created_at": "2026-09-11T11:00:00Z", "suppliers": {"name": "Supplier B"}},
            {"id": "q-a-2", "rfq_id": "rfq-1", "supplier_id": "supp-A", "price": 95.0, "delivery_time": "2 days", "created_at": "2026-09-11T10:00:00Z", "suppliers": {"name": "Supplier A"}},
            {"id": "q-c-1", "rfq_id": "rfq-1", "supplier_id": "supp-C", "price": 98.0, "delivery_time": "3 days", "created_at": "2026-09-11T09:00:00Z", "suppliers": {"name": "Supplier C"}},
            {"id": "q-b-1", "rfq_id": "rfq-1", "supplier_id": "supp-B", "price": 110.0, "delivery_time": "3 days", "created_at": "2026-09-11T08:00:00Z", "suppliers": {"name": "Supplier B"}},
            {"id": "q-a-1", "rfq_id": "rfq-1", "supplier_id": "supp-A", "price": 100.0, "delivery_time": "3 days", "created_at": "2026-09-11T07:00:00Z", "suppliers": {"name": "Supplier A"}},
        ]

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=mock_quotes_db)
        mock_supabase.table.return_value = mock_query

        # 1. Effective quotes (latest per supplier)
        effective_quotes = db.get_quotes_for_rfq("rfq-1")
        assert len(effective_quotes) == 3
        quote_map = {q["supplier_id"]: q for q in effective_quotes}

        assert quote_map["supp-A"]["id"] == "q-a-3"
        assert quote_map["supp-A"]["price"] == 92.0
        assert quote_map["supp-A"]["delivery_time"] == "1 day"

        assert quote_map["supp-B"]["id"] == "q-b-2"
        assert quote_map["supp-B"]["price"] == 105.0

        assert quote_map["supp-C"]["id"] == "q-c-1"
        assert quote_map["supp-C"]["price"] == 98.0

        # 2. All quotes history (all 6 rows preserved)
        all_quotes = db.get_all_quotes_for_rfq("rfq-1")
        assert len(all_quotes) == 6

    def test_ranking_uses_only_latest_effective_quotes(self, mock_supabase):
        """Final ranking evaluation uses exactly the latest effective quotes per supplier."""
        mock_effective_quotes = [
            {"id": "q-a-3", "rfq_id": "rfq-1", "supplier_id": "supp-A", "price": 92.0, "delivery_time": "1 day", "quality_notes": "2 yr warranty", "suppliers": {"name": "Supplier A"}},
            {"id": "q-b-2", "rfq_id": "rfq-1", "supplier_id": "supp-B", "price": 105.0, "delivery_time": "2 days", "quality_notes": "1 yr warranty", "suppliers": {"name": "Supplier B"}},
            {"id": "q-c-1", "rfq_id": "rfq-1", "supplier_id": "supp-C", "price": 98.0, "delivery_time": "3 days", "quality_notes": "Standard", "suppliers": {"name": "Supplier C"}},
        ]

        with patch.object(db, "get_quotes_for_rfq", return_value=mock_effective_quotes), \
             patch.object(openclaw_bridge, "rank_quotes", return_value={
                 "best_supplier_id": "supp-A",
                 "reasoning": "Supplier A offers lowest price of 92 AED with fastest 1-day delivery.",
                 "ranking": [
                     {"supplier_id": "supp-A", "rank": 1, "summary": "92 AED 1 day"},
                     {"supplier_id": "supp-C", "rank": 2, "summary": "98 AED 3 days"},
                     {"supplier_id": "supp-B", "rank": 3, "summary": "105 AED 2 days"}
                 ]
             }) as mock_rank_llm, \
             patch.object(db, "save_ranking") as mock_save_ranking:

            ranking_result = main.generate_ranking("rfq-1")

            assert ranking_result["best_supplier_id"] == "supp-A"
            # Verify quotes_summary passed to LLM only contains the 3 effective quotes
            llm_summary_arg = mock_rank_llm.call_args.kwargs["quotes_summary"]
            assert "92" in llm_summary_arg
            assert "105" in llm_summary_arg
            assert "98" in llm_summary_arg
            assert "100" not in llm_summary_arg # Old revisions excluded from ranking prompt
            assert "110" not in llm_summary_arg
            mock_save_ranking.assert_called_once()


class TestPhase4ConversationContinuityAndQuotedRouting:
    """Tests for Phase 4: WhatsApp Conversation Continuity, Quoted Routing, and Outbound Association."""


    def test_2_quote_revision_preserves_history_and_effective(self, mock_supabase):
        """
        Test 2 — Quote revision:
        Supplier X gives Quote 1 = AED 100, later sends Quote 2 = AED 95 while RFQ is active.
        Expected: both quotes exist as separate rows, latest is 95, supplier remains responded, RFQ active, no ranking/closure.
        """
        mock_quotes = [
            {"id": "q-2", "rfq_id": "rfq-1", "supplier_id": "supp-1", "price": 95.0, "created_at": "2026-09-11T12:00:00Z", "suppliers": {"name": "Supplier X"}},
            {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "supp-1", "price": 100.0, "created_at": "2026-09-11T10:00:00Z", "suppliers": {"name": "Supplier X"}},
        ]
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.execute.return_value = MagicMock(data=mock_quotes)
        mock_supabase.table.return_value = mock_query

        # Effective quote (latest per supplier)
        effective = db.get_quotes_for_rfq("rfq-1")
        assert len(effective) == 1
        assert effective[0]["id"] == "q-2"
        assert effective[0]["price"] == 95.0

        # Full audit history (all quotes preserved)
        all_quotes = db.get_all_quotes_for_rfq("rfq-1")
        assert len(all_quotes) == 2
