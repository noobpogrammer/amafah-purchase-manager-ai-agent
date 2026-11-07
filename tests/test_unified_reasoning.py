"""
Phase 8: Tests for Unified Full-Context Reasoning Architecture
Verifies that:
- Quoted stanza, quoted text, pending clarification, and general supplier messages all invoke ONE reason_about_procurement_message.
- AgentContext builds complete trusted context and bounded chronological conversation history.
- Exact stanza lock is enforced deterministically by Policy Validator.
- Pending clarification is integrated as context rather than a fragmented reasoning branch.
- execute_validated_action cleanly handles all approved action execution.
- is_rfq_open strictly enforces rfqs.status == 'active' while keeping rfq_suppliers participation states decoupled.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock, ANY

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
import main
import db
import openclaw_bridge


class TestAgentContextAndHistory:
    """Tests 7-14: AgentContext assembly and bounded chronological conversation history."""


    def test_12_and_13_conversation_history_is_chronological_and_bounded(self):
        """12, 13. db.get_supplier_conversation_history queries desc limit 10, then returns reversed (oldest->newest)."""
        mock_table = MagicMock()
        mock_res = MagicMock(data=[
            {"id": "msg-3", "direction": "inbound", "body": "Latest message", "created_at": "2026-09-12T10:02:00Z"},
            {"id": "msg-2", "direction": "outbound", "body": "Middle message", "created_at": "2026-09-12T10:01:00Z"},
            {"id": "msg-1", "direction": "inbound", "body": "Oldest message", "created_at": "2026-09-12T10:00:00Z"},
        ])
        mock_table.select.return_value.eq.return_value.eq.return_value.neq.return_value.order.return_value.limit.return_value.execute.return_value = mock_res

        with patch.object(db.supabase, "table", return_value=mock_table):
            history = db.get_supplier_conversation_history("c-1", "s-1", limit=10, exclude_message_id="current-msg-id")
            # Must be reversed: oldest first
            assert len(history) == 3
            assert history[0]["id"] == "msg-1"
            assert history[1]["id"] == "msg-2"
            assert history[2]["id"] == "msg-3"


class TestIsRfqOpenStrictness:
    """Tests 26-33: is_rfq_open strict master status invariant."""

    def test_26_active_before_deadline_is_open(self):
        """26. rfqs.status == 'active' and now < due_by returns True."""
        future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        assert db.is_rfq_open({"status": "active", "due_by": future}) is True

    def test_27_active_after_deadline_is_closed(self):
        """27. rfqs.status == 'active' and now >= due_by returns False."""
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        assert db.is_rfq_open({"status": "active", "due_by": past}) is False

    def test_28_and_29_closed_or_cancelled_is_closed(self):
        """28, 29. rfqs.status in ('closed', 'cancelled') returns False regardless of due_by."""
        future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        assert db.is_rfq_open({"status": "closed", "due_by": future}) is False
        assert db.is_rfq_open({"status": "cancelled", "due_by": future}) is False

    def test_30_to_32_supplier_statuses_rejected_as_master_rfq_status(self):
        """30-32. rfq_suppliers participation states ('sent', 'clarifying', 'responded') are not valid master RFQ statuses."""
        future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        assert db.is_rfq_open({"status": "sent", "due_by": future}) is False
        assert db.is_rfq_open({"status": "clarifying", "due_by": future}) is False
        assert db.is_rfq_open({"status": "responded", "due_by": future}) is False

    def test_33_responded_supplier_can_still_revise_when_master_rfq_is_active(self):
        """33. rfq_suppliers.status = 'responded' allows quote revisions because underlying master RFQ is active."""
        future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        rfq_supplier_entry = {
            "id": "rs-1",
            "status": "responded",
            "rfqs": {"id": "rfq-1", "status": "active", "due_by": future}
        }
        # The joined RFQ is checked by is_rfq_open, which passes
        assert db.is_rfq_open(rfq_supplier_entry["rfqs"]) is True
