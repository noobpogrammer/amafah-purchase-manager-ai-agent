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
from datetime import datetime, timezone

import main
import db


@pytest.fixture(autouse=True)
def reset_queue():
    """Ensure the outbound queue is fresh for each test's event loop."""
    main.outbound_queue = asyncio.Queue()
    yield
    while not main.outbound_queue.empty():
        try:
            main.outbound_queue.get_nowait()
            main.outbound_queue.task_done()
        except Exception:
            pass


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


@pytest.mark.asyncio
class TestWorkerExecutionLifecycle:
    """Worker processing tests for success, acquisition protection, Evolution edge-cases, and errors."""

    async def test_worker_processes_successful_send(self):
        """Worker claims queued message, sends, marks sent, and updates rfq_suppliers.sent_message_id."""
        with patch("main.send_whatsapp_message") as mock_send, \
             patch("db.mark_message_sending", return_value=True) as mock_claim, \
             patch("db.mark_message_sent") as mock_sent, \
             patch("db.update_rfq_supplier_sent_message_id") as mock_update_rfq:

            mock_send.return_value = {"key": {"id": "EVO_STANZA_101"}}

            await main.enqueue_message(
                phone_number="+971500000001",
                message="Test RFQ invitation",
                rfq_id="rfq-1",
                supplier_id="sup-1",
                message_log_id="msg-log-1",
            )

            with patch("main.OUTBOUND_MIN_DELAY", 0.01), patch("main.OUTBOUND_MAX_DELAY", 0.01):
                worker_task = asyncio.create_task(main.outbound_worker())
                await main.outbound_queue.join()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            mock_claim.assert_called_once_with("msg-log-1")
            mock_send.assert_called_once_with("+971500000001", "Test RFQ invitation")
            mock_sent.assert_called_once_with("msg-log-1", evolution_message_id="EVO_STANZA_101")
            mock_update_rfq.assert_called_once_with("rfq-1", "sup-1", "EVO_STANZA_101")

    async def test_10_worker_skips_send_when_claim_fails(self):
        """10. If mark_message_sending returns False (already claimed/sent), worker does NOT call Evolution API."""
        with patch("main.send_whatsapp_message") as mock_send, \
             patch("db.mark_message_sending", return_value=False) as mock_claim, \
             patch("db.mark_message_sent") as mock_sent, \
             patch("db.mark_message_failed") as mock_failed, \
             patch("db.mark_message_unknown") as mock_unknown:

            await main.enqueue_message(
                phone_number="+971500000001",
                message="Duplicate queue item",
                message_log_id="msg-duplicate",
            )

            with patch("main.OUTBOUND_MIN_DELAY", 0.01), patch("main.OUTBOUND_MAX_DELAY", 0.01):
                worker_task = asyncio.create_task(main.outbound_worker())
                await main.outbound_queue.join()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            mock_claim.assert_called_once_with("msg-duplicate")
            mock_send.assert_not_called()
            mock_sent.assert_not_called()
            mock_failed.assert_not_called()
            mock_unknown.assert_not_called()

    async def test_worker_skips_send_when_claim_raises_exception(self):
        """Worker safely skips send and task_done() is called when mark_message_sending raises an exception."""
        with patch("main.send_whatsapp_message") as mock_send, \
             patch("db.mark_message_sending", side_effect=Exception("PostgREST network error")) as mock_claim, \
             patch("db.mark_message_sent") as mock_sent, \
             patch("db.mark_message_failed") as mock_failed, \
             patch("db.mark_message_unknown") as mock_unknown:

            await main.enqueue_message(
                phone_number="+971500000001",
                message="Error prone queue item",
                message_log_id="msg-err",
            )

            with patch("main.OUTBOUND_MIN_DELAY", 0.01), patch("main.OUTBOUND_MAX_DELAY", 0.01):
                worker_task = asyncio.create_task(main.outbound_worker())
                await main.outbound_queue.join()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            mock_claim.assert_called_once_with("msg-err")
            mock_send.assert_not_called()
            mock_sent.assert_not_called()
            mock_failed.assert_not_called()
            mock_unknown.assert_not_called()

    async def test_worker_skips_send_when_rpc_fails_closed(self):
        """End-to-end worker behavior with db layer: when supabase.rpc raises an exception, worker does not call Evolution."""
        with patch("main.send_whatsapp_message") as mock_send, \
             patch.object(db.supabase, "rpc", side_effect=Exception("Database connection timeout")), \
             patch.object(db.supabase, "table") as mock_tbl, \
             patch("db.mark_message_sent") as mock_sent, \
             patch("db.mark_message_failed") as mock_failed, \
             patch("db.mark_message_unknown") as mock_unknown:

            await main.enqueue_message(
                phone_number="+971500000001",
                message="RPC down queue item",
                message_log_id="msg-rpc-down",
            )

            with patch("main.OUTBOUND_MIN_DELAY", 0.01), patch("main.OUTBOUND_MAX_DELAY", 0.01):
                worker_task = asyncio.create_task(main.outbound_worker())
                await main.outbound_queue.join()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            mock_send.assert_not_called()
            mock_sent.assert_not_called()
            mock_failed.assert_not_called()
            mock_unknown.assert_not_called()
            # Ensure no fallback table query was executed
            mock_tbl.assert_not_called()

    async def test_11_evolution_success_with_valid_json_no_id(self):
        """11. HTTP success with valid JSON but no Evolution message ID marks sent with evolution_message_id=None."""
        with patch("main.send_whatsapp_message") as mock_send, \
             patch("db.mark_message_sending", return_value=True), \
             patch("db.mark_message_sent") as mock_sent, \
             patch("db.update_rfq_supplier_sent_message_id") as mock_update_rfq:

            # Evolution returned HTTP 200 with JSON without message ID
            mock_send.return_value = {"status": "SUCCESS", "message": "Message received"}

            await main.enqueue_message(
                phone_number="+971500000001",
                message="Valid text without ID",
                rfq_id="rfq-1",
                supplier_id="sup-1",
                message_log_id="msg-no-id",
            )

            with patch("main.OUTBOUND_MIN_DELAY", 0.01), patch("main.OUTBOUND_MAX_DELAY", 0.01):
                worker_task = asyncio.create_task(main.outbound_worker())
                await main.outbound_queue.join()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            mock_sent.assert_called_once_with("msg-no-id", evolution_message_id=None)
            mock_update_rfq.assert_not_called()

    async def test_12_evolution_success_with_non_json_response(self):
        """12. HTTP success with non-JSON response marks sent with evolution_message_id=None without crashing worker."""
        with patch("main.send_whatsapp_message") as mock_send, \
             patch("db.mark_message_sending", return_value=True), \
             patch("db.mark_message_sent") as mock_sent, \
             patch("db.update_rfq_supplier_sent_message_id") as mock_update_rfq:

            # Evolution returned raw text fallback dict
            mock_send.return_value = {"status": "ok", "raw": "OK - message accepted"}

            await main.enqueue_message(
                phone_number="+971500000001",
                message="Raw text response",
                message_log_id="msg-raw-text",
            )

            with patch("main.OUTBOUND_MIN_DELAY", 0.01), patch("main.OUTBOUND_MAX_DELAY", 0.01):
                worker_task = asyncio.create_task(main.outbound_worker())
                await main.outbound_queue.join()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            mock_sent.assert_called_once_with("msg-raw-text", evolution_message_id=None)
            mock_update_rfq.assert_not_called()

    async def test_worker_handles_http_failure_and_continues(self):
        """HTTP error marks message failed and worker continues processing next messages."""
        response_mock = MagicMock(status_code=400)
        http_err = requests.exceptions.HTTPError("400 Client Error", response=response_mock)

        with patch("main.send_whatsapp_message") as mock_send, \
             patch("db.mark_message_sending", return_value=True), \
             patch("db.mark_message_failed") as mock_failed, \
             patch("db.mark_message_sent") as mock_sent:

            # First send raises HTTPError, second succeeds
            mock_send.side_effect = [http_err, {"key": {"id": "EVO_SUCCESS_2"}}]

            await main.enqueue_message("+971500000001", "Failing message", message_log_id="msg-fail")
            await main.enqueue_message("+971500000002", "Succeeding message", message_log_id="msg-succ")

            with patch("main.OUTBOUND_MIN_DELAY", 0.01), patch("main.OUTBOUND_MAX_DELAY", 0.01):
                worker_task = asyncio.create_task(main.outbound_worker())
                await main.outbound_queue.join()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            mock_failed.assert_called_once()
            assert "400" in mock_failed.call_args[0][1]
            mock_sent.assert_called_once_with("msg-succ", evolution_message_id="EVO_SUCCESS_2")

    async def test_worker_handles_timeout_as_unknown(self):
        """Timeout error marks message unknown and worker continues safely."""
        timeout_err = requests.exceptions.Timeout("Connection timed out after 30 seconds")

        with patch("main.send_whatsapp_message", side_effect=timeout_err), \
             patch("db.mark_message_sending", return_value=True), \
             patch("db.mark_message_unknown") as mock_unknown:

            await main.enqueue_message("+971500000001", "Timeout message", message_log_id="msg-timeout")

            with patch("main.OUTBOUND_MIN_DELAY", 0.01), patch("main.OUTBOUND_MAX_DELAY", 0.01):
                worker_task = asyncio.create_task(main.outbound_worker())
                await main.outbound_queue.join()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            mock_unknown.assert_called_once()
            assert "Timeout" in mock_unknown.call_args[0][1]

    async def test_generic_outbound_message_audited_without_rfq(self):
        """Generic outbound messages without RFQ/supplier IDs update message_log properly."""
        with patch("main.send_whatsapp_message") as mock_send, \
             patch("db.mark_message_sending", return_value=True), \
             patch("db.mark_message_sent") as mock_sent, \
             patch("db.update_rfq_supplier_sent_message_id") as mock_update_rfq:

            mock_send.return_value = {"id": "EVO_GENERIC_9"}

            await main.enqueue_message(
                phone_number="+971500000001",
                message="Generic clarification question",
                message_log_id="msg-generic-1",
            )

            with patch("main.OUTBOUND_MIN_DELAY", 0.01), patch("main.OUTBOUND_MAX_DELAY", 0.01):
                worker_task = asyncio.create_task(main.outbound_worker())
                await main.outbound_queue.join()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

            mock_sent.assert_called_once_with("msg-generic-1", evolution_message_id="EVO_GENERIC_9")
            mock_update_rfq.assert_not_called()


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

    @pytest.mark.asyncio
    async def test_lifespan_recovers_queued_messages(self):
        """Lifespan startup recovers queued messages and places them on outbound_queue once."""
        queued_records = [
            {
                "id": "rec-1",
                "body": "Recovered RFQ message",
                "related_rfq_id": "rfq-1",
                "supplier_id": "sup-1",
                "suppliers": {"phone_number": "+971500000001"},
            }
        ]

        with patch("db.get_queued_outbound_messages", return_value=queued_records), \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("main.scheduler") as mock_sched, \
             patch("main.outbound_worker", new_callable=AsyncMock):

            async with main.lifespan(main.app):
                mock_enq.assert_called_once_with(
                    "+971500000001",
                    "Recovered RFQ message",
                    rfq_id="rfq-1",
                    supplier_id="sup-1",
                    message_log_id="rec-1",
                )
