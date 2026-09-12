"""
Phase 7.3: Tests for Reliable RFQ Finalization, Atomic Closure Claim, Idempotent Ranking & Recovery
Verifies atomic finalization claim authority, multi-instance safety, idempotent ranking persistence (UPSERT),
event-key based closure notification deduplication, crash recovery across all failure points,
and reminder suppression on expired RFQs.
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

import main
import db


@pytest.fixture(autouse=True)
def reset_queue():
    """Ensure the outbound queue is fresh for each test."""
    main.outbound_queue = asyncio.Queue()
    yield
    while not main.outbound_queue.empty():
        try:
            main.outbound_queue.get_nowait()
            main.outbound_queue.task_done()
        except Exception:
            pass


class TestAtomicFinalizationAuthority:
    """Tests 1-5: Atomic finalization claim authority via claim_rfq_for_finalization RPC."""

    def test_1_first_finalization_claim_succeeds(self):
        """1. First finalization claim for expired active RFQ succeeds via RPC."""
        with patch.object(db.supabase, "rpc") as mock_rpc:
            mock_rpc.return_value.execute.return_value = MagicMock(data=True)

            claimed = db.claim_rfq_for_finalization("rfq-expired-1")
            assert claimed is True
            mock_rpc.assert_called_once_with("claim_rfq_for_finalization", {"p_rfq_id": "rfq-expired-1"})

    def test_2_second_claim_of_same_rfq_fails(self):
        """2. Second claim of already claimed/closed RFQ returns False."""
        with patch.object(db.supabase, "rpc") as mock_rpc:
            mock_rpc.return_value.execute.return_value = MagicMock(data=False)

            claimed = db.claim_rfq_for_finalization("rfq-already-closed")
            assert claimed is False

    def test_3_rpc_exception_fails_closed(self):
        """3. When RPC encounters a database or network error, claim strictly fails closed (returns False)."""
        with patch.object(db.supabase, "rpc", side_effect=Exception("Database timeout")):
            claimed = db.claim_rfq_for_finalization("rfq-err")
            assert claimed is False

    def test_4_and_5_empty_or_none_rfq_id_rejected(self):
        """4, 5. Invalid or empty rfq_id rejected without executing RPC."""
        with patch.object(db.supabase, "rpc") as mock_rpc:
            assert db.claim_rfq_for_finalization("") is False
            assert db.claim_rfq_for_finalization(None) is False
            mock_rpc.assert_not_called()


class TestRankingIdempotency:
    """Tests 6-9: Ranking evaluation and UPSERT idempotency."""

    def test_6_ranking_uses_latest_quote_per_supplier(self):
        """6. get_quotes_for_rfq filters historical revisions to the latest effective quote per supplier."""
        historical_quotes = [
            {"id": "q-1", "supplier_id": "sup-A", "price": 120.0, "created_at": "2026-09-01T10:00:00Z"},
            {"id": "q-2", "supplier_id": "sup-B", "price": 110.0, "created_at": "2026-09-01T11:00:00Z"},
            {"id": "q-3", "supplier_id": "sup-A", "price": 100.0, "created_at": "2026-09-01T12:00:00Z"},  # Revised quote from sup-A
        ]
        with patch.object(db.supabase, "table") as mock_tbl:
            mock_order2 = MagicMock()
            mock_order2.execute.return_value = MagicMock(data=[
                historical_quotes[2], # q-3 (newest)
                historical_quotes[1], # q-2
                historical_quotes[0], # q-1 (oldest)
            ])
            (
                mock_tbl.return_value.select.return_value
                .eq.return_value
                .order.return_value
                .order.return_value
            ) = mock_order2

            quotes = db.get_quotes_for_rfq("rfq-1")
            assert len(quotes) == 2
            # sup-A has effective price 100, sup-B has 110
            sup_a = next(q for q in quotes if q["supplier_id"] == "sup-A")
            assert sup_a["price"] == 100.0

    def test_7_and_8_save_ranking_upserts_on_rfq_id(self):
        """7, 8. save_ranking executes an upsert with on_conflict='rfq_id' preventing duplicate rows."""
        with patch.object(db.supabase, "table") as mock_tbl:
            mock_upsert = MagicMock()
            mock_upsert.execute.return_value = MagicMock(data=[{"id": "rank-1", "rfq_id": "rfq-100"}])
            mock_tbl.return_value.upsert.return_value = mock_upsert

            result = db.save_ranking("rfq-100", "sup-1", "Best price", {"winner": "sup-1"})
            assert result == [{"id": "rank-1", "rfq_id": "rfq-100"}]
            mock_tbl.return_value.upsert.assert_called_once_with({
                "rfq_id": "rfq-100",
                "best_supplier_id": "sup-1",
                "reasoning": "Best price",
                "ranking_json": {"winner": "sup-1"},
            }, on_conflict="rfq_id")

    @pytest.mark.asyncio
    async def test_9_ranking_failure_leaves_rfq_in_processing_state(self):
        """9. When generate_ranking raises an exception, finalization job aborts before marking completed."""
        rfq = {
            "id": "rfq-fail-rank",
            "product_name": "Cement 50kg",
            "client_id": "client-1",
            "status": "closed",
            "finalization_status": "processing",
        }

        with patch("db.get_quotes_for_rfq", return_value=[{"supplier_id": "sup-1", "price": 50.0}]), \
             patch("db.ranking_exists", return_value=False), \
             patch("main.generate_ranking", side_effect=Exception("Groq API 503 Outage")), \
             patch("db.mark_rfq_finalization_completed") as mock_complete, \
             patch("db.log_webhook_error") as mock_log_err:

            await main.finalize_rfq_job(rfq)

            # Did NOT mark finalization completed
            mock_complete.assert_not_called()
            mock_log_err.assert_called_once()


class TestIdempotentClosureNotifications:
    """Tests 10-14: Event-key notification deduplication and recovery."""

    @pytest.mark.asyncio
    async def test_10_closure_notification_uses_deterministic_event_key(self):
        """10. Closure notifications are logged with deterministic event_key 'rfq_closed:{rfq_id}:{supplier_id}'."""
        rfq = {
            "id": "rfq-clean-1",
            "product_name": "Steel Bars",
            "client_id": "client-1",
            "rfq_suppliers": [
                {"supplier_id": "sup-1", "suppliers": {"id": "sup-1", "phone_number": "+971500000001", "client_id": "client-1"}},
            ],
        }

        with patch("db.get_quotes_for_rfq", return_value=[]), \
             patch("db.get_message_by_event_key", return_value=None), \
             patch("db.log_message", return_value="msg-log-new") as mock_log_msg, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("db.mark_rfq_finalization_completed", return_value=True) as mock_comp:

            await main.finalize_rfq_job(rfq)

            mock_log_msg.assert_called_once_with(
                "client-1",
                "sup-1",
                "outbound",
                "RFQ for 'Steel Bars' is now closed as the deadline has passed. Thank you!",
                related_rfq_id="rfq-clean-1",
                event_key="rfq_closed:rfq-clean-1:sup-1",
            )
            mock_enq.assert_called_once_with(
                "+971500000001",
                "RFQ for 'Steel Bars' is now closed as the deadline has passed. Thank you!",
                rfq_id="rfq-clean-1",
                supplier_id="sup-1",
                message_log_id="msg-log-new",
            )
            mock_comp.assert_called_once_with("rfq-clean-1")

    @pytest.mark.asyncio
    async def test_11_existing_event_key_skips_duplicate_notification_insert(self):
        """11. When event_key already exists, no duplicate message_log row is created or enqueued."""
        rfq = {
            "id": "rfq-clean-1",
            "product_name": "Steel Bars",
            "client_id": "client-1",
            "rfq_suppliers": [
                {"supplier_id": "sup-1", "suppliers": {"id": "sup-1", "phone_number": "+971500000001"}},
            ],
        }

        with patch("db.get_quotes_for_rfq", return_value=[]), \
             patch("db.get_message_by_event_key", return_value={"id": "msg-already-logged"}), \
             patch("db.log_message") as mock_log_msg, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("db.mark_rfq_finalization_completed", return_value=True) as mock_comp:

            await main.finalize_rfq_job(rfq)

            mock_log_msg.assert_not_called()
            mock_enq.assert_not_called()
            mock_comp.assert_called_once_with("rfq-clean-1")

    @pytest.mark.asyncio
    async def test_12_crash_after_one_supplier_notification_only_enqueues_missing(self):
        """12. If previous run crashed after notifying supplier 1, recovery notifies only supplier 2."""
        rfq = {
            "id": "rfq-part-notif",
            "product_name": "Copper Wire",
            "client_id": "client-1",
            "rfq_suppliers": [
                {"supplier_id": "sup-1", "suppliers": {"id": "sup-1", "phone_number": "+971500000001"}},
                {"supplier_id": "sup-2", "suppliers": {"id": "sup-2", "phone_number": "+971500000002"}},
            ],
        }

        def fake_get_msg_by_event_key(key):
            if "sup-1" in key:
                return {"id": "msg-sup-1-already-sent"}
            return None

        with patch("db.get_quotes_for_rfq", return_value=[]), \
             patch("db.get_message_by_event_key", side_effect=fake_get_msg_by_event_key), \
             patch("db.log_message", return_value="msg-sup-2-new") as mock_log_msg, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("db.mark_rfq_finalization_completed", return_value=True) as mock_comp:

            await main.finalize_rfq_job(rfq)

            # Only sup-2 is logged and enqueued
            mock_log_msg.assert_called_once_with(
                "client-1",
                "sup-2",
                "outbound",
                "RFQ for 'Copper Wire' is now closed as the deadline has passed. Thank you!",
                related_rfq_id="rfq-part-notif",
                event_key="rfq_closed:rfq-part-notif:sup-2",
            )
            mock_enq.assert_called_once_with(
                "+971500000002",
                "RFQ for 'Copper Wire' is now closed as the deadline has passed. Thank you!",
                rfq_id="rfq-part-notif",
                supplier_id="sup-2",
                message_log_id="msg-sup-2-new",
            )
            mock_comp.assert_called_once_with("rfq-part-notif")

    def test_13_log_message_with_event_key_fails_closed_without_unkeyed_fallback(self):
        """13. When insert fails and lookup returns None, db.log_message strictly fails closed (returns None, no fallback)."""
        with patch.object(db.supabase, "table") as mock_tbl, \
             patch("db.get_message_by_event_key", return_value=None):

            # Initial insert with event_key raises Exception
            mock_tbl.return_value.insert.side_effect = Exception("Unique violation / DB error")

            msg_id = db.log_message(
                client_id="client-1",
                supplier_id="sup-1",
                direction="outbound",
                body="Test fail-closed",
                event_key="rfq_closed:rfq-1:sup-1",
            )

            # Must return None (fail closed)
            assert msg_id is None
            # Must have only attempted the insert with event_key, NEVER fallback without event_key
            assert mock_tbl.return_value.insert.call_count == 1
            call_payload = mock_tbl.return_value.insert.call_args[0][0]
            assert "event_key" in call_payload
            assert call_payload["event_key"] == "rfq_closed:rfq-1:sup-1"

    def test_14_log_message_event_key_unique_conflict_recovers_winner_id(self):
        """14. When insert raises unique conflict and lookup finds existing row, returns winning row ID."""
        with patch.object(db.supabase, "table") as mock_tbl, \
             patch("db.get_message_by_event_key", side_effect=[None, {"id": "msg-winner-id"}]):

            mock_tbl.return_value.insert.side_effect = Exception("23505 duplicate key value violates unique constraint")

            msg_id = db.log_message(
                client_id="client-1",
                supplier_id="sup-1",
                direction="outbound",
                body="Test race recovery",
                event_key="rfq_closed:rfq-1:sup-1",
            )

            assert msg_id == "msg-winner-id"

    @pytest.mark.asyncio
    async def test_14b_finalization_fails_closed_if_notification_logging_returns_none(self):
        """14b. If log_message returns None for a supplier notification, message is NOT enqueued and RFQ is NOT completed."""
        rfq = {
            "id": "rfq-fail-close-notif",
            "product_name": "Steel",
            "client_id": "client-1",
            "rfq_suppliers": [
                {"supplier_id": "sup-1", "suppliers": {"id": "sup-1", "phone_number": "+971500000001"}},
            ],
        }

        with patch("db.get_quotes_for_rfq", return_value=[]), \
             patch("db.get_message_by_event_key", return_value=None), \
             patch("db.log_message", return_value=None) as mock_log_msg, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("db.mark_rfq_finalization_completed") as mock_comp:

            await main.finalize_rfq_job(rfq)

            # Did NOT enqueue without durable row
            mock_enq.assert_not_called()
            # Did NOT mark finalization completed
            mock_comp.assert_not_called()


class TestCrashRecoveryAndScheduler:
    """Tests 15-20: Scheduler sections A, B, and C."""

    @pytest.mark.asyncio
    async def test_15_scheduler_section_b_recovers_processing_rfqs(self):
        """15. Scheduler Section B fetches processing RFQs and resumes finalization without re-claiming."""
        processing_rfq = {
            "id": "rfq-proc-1",
            "product_name": "Pipes",
            "client_id": "client-1",
            "status": "closed",
            "finalization_status": "processing",
            "rfq_suppliers": [],
        }

        with patch("db.get_active_rfqs_past_deadline", return_value=[]), \
             patch("db.get_rfqs_pending_finalization_recovery", return_value=[processing_rfq]), \
             patch("db.claim_rfq_for_finalization") as mock_claim, \
             patch("main.finalize_rfq_job", new_callable=AsyncMock) as mock_finalize, \
             patch("db.get_active_rfq_suppliers_with_deadlines", return_value=[]):

            await main.check_deadlines_and_reminders()

            # Claim is NOT called for processing recovery
            mock_claim.assert_not_called()
            # finalize_rfq_job was called on the recovered RFQ
            mock_finalize.assert_called_once_with(processing_rfq)

    @pytest.mark.asyncio
    async def test_16_reminders_skipped_for_expired_rfq(self):
        """16. Reminders are never triggered if percentage >= 100 or RFQ is no longer open."""
        now = datetime.now(timezone.utc)
        expired_sent_at = (now - timedelta(hours=25)).isoformat()

        item = {
            "id": "item-expired",
            "sent_at": expired_sent_at,
            "reminder_count": 2,
            "rfqs": {
                "id": "rfq-1",
                "status": "active",
                "deadline_hours": 24,
                "due_by": (now - timedelta(hours=1)).isoformat(),
            },
            "suppliers": {"id": "sup-1", "phone_number": "+971500000001", "name": "Sup1"},
        }

        with patch("db.get_active_rfqs_past_deadline", return_value=[]), \
             patch("db.get_rfqs_pending_finalization_recovery", return_value=[]), \
             patch("db.get_active_rfq_suppliers_with_deadlines", return_value=[item]), \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("db.update_rfq_supplier_reminder") as mock_upd_rem:

            await main.check_deadlines_and_reminders()

            # No reminder enqueued or updated
            mock_enq.assert_not_called()
            mock_upd_rem.assert_not_called()

    @pytest.mark.asyncio
    async def test_17_multi_instance_concurrent_scheduler_simulation(self):
        """17. Two concurrent scheduler runs result in exactly ONE finalization execution via atomic claim."""
        rfq = {
            "id": "rfq-race-1",
            "product_name": "Valves",
            "client_id": "client-1",
            "status": "active",
            "rfq_suppliers": [{"supplier_id": "sup-1", "suppliers": {"id": "sup-1", "phone_number": "+971500000001"}}],
        }

        # Instance A claim succeeds (True), Instance B claim fails (False)
        claim_results = [True, False]

        def fake_claim(rfq_id):
            return claim_results.pop(0) if claim_results else False

        with patch("db.get_active_rfqs_past_deadline", return_value=[rfq]), \
             patch("db.get_rfqs_pending_finalization_recovery", return_value=[]), \
             patch("db.claim_rfq_for_finalization", side_effect=fake_claim), \
             patch("main.finalize_rfq_job", new_callable=AsyncMock) as mock_finalize, \
             patch("db.get_active_rfq_suppliers_with_deadlines", return_value=[]):

            # Run Instance A
            await main.check_deadlines_and_reminders()
            # Run Instance B
            await main.check_deadlines_and_reminders()

            # Exactly one finalization job executed
            assert mock_finalize.call_count == 1

    @pytest.mark.asyncio
    async def test_18_crash_after_all_records_marks_completed(self):
        """18. When ranking exists and all event_keys exist, recovery proceeds directly to mark completed."""
        rfq = {
            "id": "rfq-all-records-exist",
            "product_name": "Drills",
            "client_id": "client-1",
            "rfq_suppliers": [
                {"supplier_id": "sup-1", "suppliers": {"id": "sup-1", "phone_number": "+971500000001"}},
            ],
        }

        with patch("db.get_quotes_for_rfq", return_value=[{"supplier_id": "sup-1", "price": 100.0}]), \
             patch("db.ranking_exists", return_value=True), \
             patch("main.generate_ranking") as mock_gen_rank, \
             patch("db.get_message_by_event_key", return_value={"id": "msg-already-logged"}), \
             patch("db.log_message") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("db.mark_rfq_finalization_completed", return_value=True) as mock_comp:

            await main.finalize_rfq_job(rfq)

            # No ranking generated, no new notifications created
            mock_gen_rank.assert_not_called()
            mock_log.assert_not_called()
            mock_enq.assert_not_called()
            # Marked completed
            mock_comp.assert_called_once_with("rfq-all-records-exist")

    def test_19_quote_after_due_by_rejected_regardless_of_finalization_state(self):
        """19. Quotes submitted after due_by are strictly rejected by is_rfq_open regardless of finalization status."""
        now = datetime.now(timezone.utc)
        expired_rfq = {
            "id": "rfq-expired",
            "status": "active",
            "finalization_status": "pending",
            "due_by": (now - timedelta(seconds=1)).isoformat(),
        }
        assert db.is_rfq_open(expired_rfq) is False

    @pytest.mark.asyncio
    async def test_20_closure_notification_starts_as_queued_in_phase_7_2(self):
        """20. New closure notifications are logged with status='queued' and propagated to outbound queue."""
        with patch.object(db.supabase, "table") as mock_tbl:
            mock_insert = MagicMock()
            mock_insert.execute.return_value = MagicMock(data=[{"id": "msg-log-phase-7-2"}])
            mock_tbl.return_value.insert.return_value = mock_insert
            mock_tbl.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

            msg_id = db.log_message(
                client_id="client-1",
                supplier_id="sup-1",
                direction="outbound",
                body="RFQ closed",
                related_rfq_id="rfq-1",
                event_key="rfq_closed:rfq-1:sup-1",
            )

            assert msg_id == "msg-log-phase-7-2"
            insert_call = mock_tbl.return_value.insert.call_args[0][0]
            assert insert_call["status"] == "queued"
            assert insert_call["event_key"] == "rfq_closed:rfq-1:sup-1"
            assert insert_call["direction"] == "outbound"
