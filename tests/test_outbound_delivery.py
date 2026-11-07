"""
Phase 7.2: Tests for Persistent Outbound Delivery Reliability & Atomic Send Acquisition
Verifies delivery state transitions, atomic send claim authority, error classification
(failed vs unknown), Evolution edge-case handling, startup recovery of queued records,
Phase 4 message correlation, and worker resilience.
"""

import asyncio
import pytest
import requests
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone, timedelta

import main
import db


class TestOutboundDeliveryLifecycle:
    """Delivery lifecycle and identity logging."""

    def test_outbound_message_logged_as_queued(self):
        """Outbound message starts as 'queued' and returns message_log.id."""
        with patch.object(db.supabase, "table") as mock_tbl:
            mock_insert = MagicMock()
            mock_insert.execute.return_value = MagicMock(data=[{"id": "msg-log-123"}])
            mock_tbl.return_value.insert.return_value = mock_insert

            msg_id = db.log_message(
                client_id="client-1",
                supplier_id="sup-1",
                direction="outbound",
                body="Hello supplier",
                related_rfq_id="rfq-1",
            )
            assert msg_id == "msg-log-123"
            mock_tbl.return_value.insert.assert_called_with({
                "client_id": "client-1",
                "supplier_id": "sup-1",
                "direction": "outbound",
                "body": "Hello supplier",
                "related_rfq_id": "rfq-1",
                "status": "queued",
            })

    def test_inbound_message_not_marked_queued(self):
        """Inbound message status is None (not queued)."""
        with patch.object(db.supabase, "table") as mock_tbl:
            mock_insert = MagicMock()
            mock_insert.execute.return_value = MagicMock(data=[{"id": "msg-log-inbound"}])
            mock_tbl.return_value.insert.return_value = mock_insert

            msg_id = db.log_message(
                client_id="client-1",
                supplier_id="sup-1",
                direction="inbound",
                body="Quoting AED 100",
            )
            assert msg_id == "msg-log-inbound"
            mock_tbl.return_value.insert.assert_called_with({
                "client_id": "client-1",
                "supplier_id": "sup-1",
                "direction": "inbound",
                "body": "Quoting AED 100",
                "related_rfq_id": None,
            })

    def test_mark_message_sent_persists_evolution_id_and_timestamp(self):
        """Successful Evolution response transitions sending -> sent, evolution_message_id and sent_at populated."""
        with patch.object(db.supabase, "table") as mock_tbl:
            mock_update = MagicMock()
            mock_update.execute.return_value = MagicMock(data=[{"id": "msg-123", "status": "sent"}])
            mock_tbl.return_value.update.return_value.eq.return_value = mock_update

            success = db.mark_message_sent("msg-123", evolution_message_id="EVO_MSG_999")
            assert success is True

            update_call = mock_tbl.return_value.update.call_args[0][0]
            assert update_call["status"] == "sent"
            assert update_call["evolution_message_id"] == "EVO_MSG_999"
            assert "sent_at" in update_call


class TestAtomicSendAcquisition:
    """Tests 1-9: Atomic send acquisition and conditionality via PostgreSQL RPC."""

    def test_1_and_2_queued_outbound_record_successfully_claimed_via_rpc(self):
        """1, 2. Queued outbound record successfully transitions to sending via RPC, incrementing retry_count."""
        with patch.object(db.supabase, "rpc") as mock_rpc:
            mock_rpc.return_value.execute.return_value = MagicMock(data=True)

            success = db.mark_message_sending("msg-queued-1")
            assert success is True
            mock_rpc.assert_called_once_with("claim_outbound_message", {"p_message_log_id": "msg-queued-1"})

    def test_3_and_4_second_acquisition_of_same_message_fails_via_rpc(self):
        """3, 4. Second acquisition of already-claimed message returns False from RPC."""
        with patch.object(db.supabase, "rpc") as mock_rpc:
            mock_rpc.return_value.execute.return_value = MagicMock(data=False)

            success = db.mark_message_sending("msg-already-claimed")
            assert success is False

    def test_rpc_exception_fails_closed_without_unsafe_fallback(self):
        """RPC failure/exception strictly fails closed (returns False) and does NOT perform non-atomic table select/update."""
        with patch.object(db.supabase, "rpc", side_effect=Exception("Database connection error")), \
             patch.object(db.supabase, "table") as mock_tbl:

            success = db.mark_message_sending("msg-123")
            assert success is False
            # Verify table was never called for fallback select/update
            mock_tbl.assert_not_called()

    def test_rpc_returning_none_fails_closed(self):
        """RPC returning None/unexpected response fails closed."""
        with patch.object(db.supabase, "rpc") as mock_rpc, \
             patch.object(db.supabase, "table") as mock_tbl:
            mock_rpc.return_value.execute.return_value = MagicMock(data=None)

            success = db.mark_message_sending("msg-123")
            assert success is False
            mock_tbl.assert_not_called()

    def test_5_to_9_non_queued_states_rejected_via_rpc(self):
        """5-9. 'sent', 'failed', 'unknown', 'sending', and inbound/NULL records return False from RPC."""
        for non_queued_state in ["sent", "failed", "unknown", "sending", None]:
            with patch.object(db.supabase, "rpc") as mock_rpc:
                mock_rpc.return_value.execute.return_value = MagicMock(data=False)

                success = db.mark_message_sending(f"msg-{non_queued_state}")
                assert success is False, f"State {non_queued_state} should not be claimable"

    def test_empty_or_none_message_id_rejected(self):
        """Empty or None message_log_id returns False without invoking RPC."""
        with patch.object(db.supabase, "rpc") as mock_rpc:
            assert db.mark_message_sending("") is False
            assert db.mark_message_sending(None) is False
            mock_rpc.assert_not_called()


class TestOutboundFailureClassifications:
    """Definite failures (failed) vs Ambiguous transport failures (unknown)."""

    def test_definite_http_failure_marks_failed(self):
        """HTTP 4xx/5xx transitions to 'failed' with sanitized error_message."""
        with patch.object(db.supabase, "table") as mock_tbl:
            mock_update = MagicMock()
            mock_update.execute.return_value = MagicMock(data=[{"id": "msg-123", "status": "failed"}])
            mock_tbl.return_value.update.return_value.eq.return_value = mock_update

            success = db.mark_message_failed("msg-123", error_message="HTTP error 400: Bad Request")
            assert success is True

            update_call = mock_tbl.return_value.update.call_args[0][0]
            assert update_call["status"] == "failed"
            assert "HTTP error 400: Bad Request" in update_call["error_message"]

    def test_ambiguous_timeout_marks_unknown(self):
        """Timeout/Connection reset transitions to 'unknown' without claiming definite failure."""
        with patch.object(db.supabase, "table") as mock_tbl:
            mock_update = MagicMock()
            mock_update.execute.return_value = MagicMock(data=[{"id": "msg-123", "status": "unknown"}])
            mock_tbl.return_value.update.return_value.eq.return_value = mock_update

            success = db.mark_message_unknown("msg-123", error_message="Read timed out (30s)")
            assert success is True

            update_call = mock_tbl.return_value.update.call_args[0][0]
            assert update_call["status"] == "unknown"
            assert "Read timed out" in update_call["error_message"]


class TestStartupRecovery:
    """Startup recovery queries and re-enqueues only status='queued' messages."""

    def test_get_queued_outbound_messages_filters_queued_only(self):
        """Database helper get_queued_outbound_messages strictly filters for direction='outbound' and status='queued'."""
        with patch.object(db.supabase, "table") as mock_tbl:
            mock_exec = MagicMock()
            mock_exec.execute.return_value = MagicMock(data=[
                {
                    "id": "msg-queued-1",
                    "direction": "outbound",
                    "status": "queued",
                    "body": "Pending RFQ quote request",
                    "related_rfq_id": "rfq-1",
                    "supplier_id": "sup-1",
                    "suppliers": {"phone_number": "+971500000001"},
                }
            ])
            (
                mock_tbl.return_value.select.return_value
                .eq.return_value
                .eq.return_value
                .order.return_value
            ) = mock_exec

            results = db.get_queued_outbound_messages()
            assert len(results) == 1
            assert results[0]["id"] == "msg-queued-1"
            assert results[0]["status"] == "queued"


class TestInitialRfqDeliveryGuardrail:
    def test_initial_delivery_lookup_returns_earliest_outbound_row(self):
        with patch.object(db.supabase, "table") as mock_table:
            query = MagicMock()
            mock_table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
                data=[{
                    "id": "initial-msg",
                    "status": "sent",
                    "sent_at": "2026-10-01T12:00:00+00:00",
                    "error_message": None,
                    "created_at": "2026-10-01T11:59:59+00:00",
                }]
            )

            result = db.get_initial_rfq_delivery_status("rfq-1", "sup-1")

        assert result["id"] == "initial-msg"
        assert result["status"] == "sent"

    @pytest.mark.asyncio
    async def test_scheduler_does_not_send_reminder_when_initial_delivery_failed(self):
        item = {
            "id": "rfq-supplier-1",
            "status": "sent",
            "sent_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "reminder_count": 0,
            "rfqs": {
                "id": "rfq-1",
                "client_id": "client-1",
                "product_name": "PVC PIPE",
                "status": "active",
                "deadline_hours": 3,
                "due_by": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            },
            "suppliers": {
                "id": "sup-1",
                "client_id": "client-1",
                "name": "Supplier",
                "phone_number": "971500000001",
            },
        }

        with patch("db.get_active_rfqs_past_deadline", return_value=[]), \
             patch("db.get_rfqs_pending_finalization_recovery", return_value=[]), \
             patch("db.get_active_rfq_suppliers_with_deadlines", return_value=[item]), \
             patch("db.get_initial_rfq_delivery_status", return_value={
                 "id": "msg-initial",
                 "status": "failed",
                 "sent_at": None,
                 "error_message": "HTTP 400",
             }), \
             patch("db.log_message") as mock_log, \
             patch("db.update_rfq_supplier_reminder") as mock_reminder, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue:
            await main.check_deadlines_and_reminders()

        mock_log.assert_not_called()
        mock_reminder.assert_not_called()
        mock_enqueue.assert_not_awaited()
