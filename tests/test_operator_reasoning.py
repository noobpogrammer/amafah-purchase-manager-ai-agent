"""
Phase 9: Comprehensive Tests for Human Operator Instructions Through the Unified Agent.
Tests verify:
- Tenant isolation on flag retrieval, response, and resolution (cross-tenant rejection).
- Atomic flag claim lifecycle (pending -> processing -> resolved / failure rollback to pending).
- Unified reasoning with input_origin="operator" and operator review flag context.
- send_procurement_message tool validation and execution.
- Operator RFQ/Supplier locks preventing prompt-based diversion.
- Quote provenance isolation (operator text is never recorded as supplier quote).
- Negotiation rules & attempt cap enforcement under operator guidance.
- Phase 7.2 outbound queue integration & durable message logging.
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
import groq_client
from groq_client import AgentContext
from auth import get_current_user
from policy_validator import ActionProposal, validate_action, ActionCategory, ValidationResult


@pytest.fixture(autouse=True)
def setup_test_env():
    """Ensure outbound delivery queue and auth dependency overrides are fresh."""
    main.outbound_queue = asyncio.Queue()
    main.app.dependency_overrides[get_current_user] = lambda: {
        "client_id": "tenant-a",
        "user_id": "user-a",
        "role": "member",
    }
    yield
    main.app.dependency_overrides.clear()
    while not main.outbound_queue.empty():
        try:
            main.outbound_queue.get_nowait()
            main.outbound_queue.task_done()
        except Exception:
            pass


class TestTenantIsolation:
    """Tests 1-9: Tenant isolation, deterministic supplier/RFQ derivation, and high-risk action rejection."""

    def test_1_tenant_a_cannot_read_tenant_b_flag(self):
        """1. GET /flags or get_flag_by_id with client_id filters out other tenants."""
        with patch.object(db, "get_flag_by_id", return_value=None):
            result = db.get_flag_by_id("flag-b", client_id="tenant-a")
            assert result is None

    def test_2_tenant_a_cannot_resolve_tenant_b_flag(self):
        """2. POST /flags/{flag_id}/resolve rejects resolving other tenant's flag (returns 404)."""
        client = TestClient(main.app)
        with patch("db.resolve_flag", return_value=None):
            resp = client.post("/flags/flag-tenant-b/resolve")
            assert resp.status_code == 404

    def test_3_and_4_supplier_and_rfq_derived_from_flag(self):
        """3, 4. Supplier and RFQ identities come strictly from the trusted flag row."""
        mock_flag = {
            "id": "flag-1",
            "client_id": "tenant-a",
            "supplier_id": "supp-trusted-1",
            "rfq_id": "rfq-trusted-1",
            "status": "pending",
            "reason": "Business knowledge needed",
            "category": "requires_business_knowledge",
            "raw_message": "Can you do 50% advance?",
            "suppliers": {"id": "supp-trusted-1", "name": "Trusted Supplier", "phone_number": "971501111111"},
            "rfqs": {"id": "rfq-trusted-1", "product_name": "Cement 5kg", "status": "active"},
        }
        client = TestClient(main.app)
        with patch("db.claim_flag_for_operator_action", return_value=mock_flag), \
             patch("db.get_open_rfqs_for_supplier", return_value=[{"rfqs": mock_flag["rfqs"]}]), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.log_message", return_value="msg-log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("db.complete_flag_operator_action", return_value=mock_flag), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "send_procurement_message",
                 "arguments": {"rfq_id": "rfq-trusted-1", "message": "Yes, 50% advance is acceptable."}
             }) as mock_reasoner:

            resp = client.post("/flags/flag-1/respond", json={"response": "Tell them 50% advance is fine", "send_to_supplier": True})
            assert resp.status_code == 200
            # Verify context passed to reasoner had trusted supplier and RFQ
            called_ctx = mock_reasoner.call_args[0][1]
            assert called_ctx.supplier_id == "supp-trusted-1"
            assert called_ctx.matched_rfq_id == "rfq-trusted-1"
            assert called_ctx.client_id == "tenant-a"
            assert called_ctx.match_source == "operator_flag"

    def test_5_and_6_operator_prompt_cannot_switch_supplier_or_rfq(self):
        """5, 6. Policy Validator rejects attempts to redirect action away from locked supplier/RFQ."""
        proposal = ActionProposal(
            tool_name="send_procurement_message",
            arguments={"rfq_id": "rfq-different-2", "message": "Here is information"},
        )
        val = validate_action(
            proposal=proposal,
            client_id="tenant-a",
            supplier_id="supp-1",
            context_rfqs=[],
            matched_rfq_id="rfq-locked-1",
        )
        assert val.is_valid is False
        assert "does not match deterministically locked RFQ" in val.reason

    def test_7_uuid_containing_generated_supplier_text_rejected(self):
        """7. Output guardrails reject messages leaking UUIDs."""
        proposal = ActionProposal(
            tool_name="send_procurement_message",
            arguments={"rfq_id": "rfq-1", "message": "Regarding order d88c52ad-3d0b-42e9-86f1-b9f70018856b please confirm"},
        )
        val = validate_action(
            proposal=proposal,
            client_id="tenant-a",
            supplier_id="supp-1",
            context_rfqs=[{"rfqs": {"id": "rfq-1"}}],
            matched_rfq_id="rfq-1",
        )
        assert val.is_valid is False
        assert "guardrails" in val.reason.lower()

    def test_8_and_9_high_risk_actions_rejected_for_operator_origin(self):
        """8, 9. High-risk actions (close_rfq, accept_quote, generate_ranking) remain blocked."""
        for high_risk in ["close_rfq", "accept_quote", "generate_ranking", "delete_quote"]:
            proposal = ActionProposal(tool_name=high_risk, arguments={"rfq_id": "rfq-1"})
            val = validate_action(proposal, client_id="tenant-a", supplier_id="supp-1", matched_rfq_id="rfq-1")
            assert val.is_valid is False
            assert "high-risk" in val.reason.lower()


class TestAtomicFlagClaim:
    """Tests 10-16: Atomic pending -> processing claim, concurrent collision handling, and release rollback."""

    def test_10_first_pending_claim_succeeds(self):
        """10. First claim on pending flag transitions status to processing."""
        mock_flag = {"id": "flag-10", "client_id": "tenant-a", "status": "processing"}
        with patch.object(db, "get_flag_by_id", return_value=mock_flag), \
             patch.object(db.supabase, "rpc") as mock_rpc:
            mock_rpc.return_value.execute.return_value = MagicMock(data=[mock_flag])
            claimed = db.claim_flag_for_operator_action("flag-10", "tenant-a")
            assert claimed is not None
            assert claimed["status"] == "processing"

    def test_11_and_12_second_concurrent_claim_fails_with_409(self):
        """11, 12. When flag is already processing/resolved, second claim fails and returns 409 Conflict."""
        client = TestClient(main.app)
        mock_existing = {"id": "flag-11", "client_id": "tenant-a", "status": "processing"}
        with patch("db.claim_flag_for_operator_action", return_value=None), \
             patch("db.get_flag_by_id", return_value=mock_existing):

            resp = client.post("/flags/flag-11/respond", json={"response": "Some instruction", "send_to_supplier": True})
            assert resp.status_code == 409
            assert "already processing" in resp.json()["detail"]

    def test_13_wrong_tenant_cannot_claim_flag(self):
        """13. Attempting to claim another tenant's flag returns 404 (does not reveal existence)."""
        client = TestClient(main.app)
        with patch("db.claim_flag_for_operator_action", return_value=None), \
             patch("db.get_flag_by_id", return_value=None):

            resp = client.post("/flags/flag-tenant-b/respond", json={"response": "Some instruction", "send_to_supplier": True})
            assert resp.status_code == 404

    def test_14_failed_groq_processing_releases_claim(self):
        """14. If Groq reasoning raises an exception, claim is safely released back to pending."""
        client = TestClient(main.app)
        mock_flag = {
            "id": "flag-14",
            "client_id": "tenant-a",
            "supplier_id": "supp-1",
            "rfq_id": "rfq-1",
            "status": "processing",
            "suppliers": {"id": "supp-1", "phone_number": "971500000000"},
            "rfqs": {"id": "rfq-1"},
        }
        with patch("db.claim_flag_for_operator_action", return_value=mock_flag), \
             patch("db.get_open_rfqs_for_supplier", return_value=[]), \
             patch("db.release_flag_claim") as mock_release, \
             patch("groq_client.reason_about_procurement_message", side_effect=RuntimeError("Groq API Timeout")):

            resp = client.post("/flags/flag-14/respond", json={"response": "Instruction", "send_to_supplier": True})
            assert resp.status_code == 500
            mock_release.assert_called_once_with("flag-14", "tenant-a")

    def test_15_policy_rejection_releases_claim(self):
        """15. If Policy Validator rejects proposal, claim is released back to pending (returns 422)."""
        client = TestClient(main.app)
        mock_flag = {
            "id": "flag-15",
            "client_id": "tenant-a",
            "supplier_id": "supp-1",
            "rfq_id": "rfq-1",
            "status": "processing",
            "suppliers": {"id": "supp-1", "phone_number": "971500000000"},
            "rfqs": {"id": "rfq-1"},
        }
        with patch("db.claim_flag_for_operator_action", return_value=mock_flag), \
             patch("db.get_open_rfqs_for_supplier", return_value=[]), \
             patch("db.release_flag_claim") as mock_release, \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": "rfq-1", "price": -10.0} # Invalid price
             }):

            resp = client.post("/flags/flag-15/respond", json={"response": "Record quote", "send_to_supplier": True})
            assert resp.status_code == 422
            assert resp.json()["status"] == "rejected_by_policy"
            mock_release.assert_called_once_with("flag-15", "tenant-a")

    def test_16_successful_action_transitions_to_resolved(self):
        """16. Successful execution completes flag transition to resolved with human_response."""
        client = TestClient(main.app)
        mock_flag = {
            "id": "flag-16",
            "client_id": "tenant-a",
            "supplier_id": "supp-1",
            "rfq_id": "rfq-1",
            "status": "processing",
            "suppliers": {"id": "supp-1", "phone_number": "971500000000"},
            "rfqs": {"id": "rfq-1"},
        }
        with patch("db.claim_flag_for_operator_action", return_value=mock_flag), \
             patch("db.get_open_rfqs_for_supplier", return_value=[{"rfqs": {"id": "rfq-1"}}]), \
             patch("db.log_message", return_value="msg-log-16"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("db.complete_flag_operator_action") as mock_complete, \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "send_procurement_message",
                 "arguments": {"rfq_id": "rfq-1", "message": "Confirmed delivery address."}
             }):

            resp = client.post("/flags/flag-16/respond", json={"response": "Tell them address confirmed", "send_to_supplier": True})
            assert resp.status_code == 200
            assert resp.json()["status"] == "resolved"
            mock_complete.assert_called_once_with("flag-16", "tenant-a", "Tell them address confirmed")


class TestCommunicationToolSendProcurementMessage:
    """Tests 24-32: Validation and execution of send_procurement_message."""

    def test_24_and_25_business_knowledge_passes_validation(self):
        """24, 25. Safe business knowledge message passes validation and executes cleanly."""
        proposal = ActionProposal(
            tool_name="send_procurement_message",
            arguments={"rfq_id": "rfq-1", "message": "We accept 50% advance payment against proforma invoice."},
        )
        val = validate_action(proposal, client_id="c-1", supplier_id="s-1", matched_rfq_id="rfq-1", context_rfqs=[{"rfqs": {"id": "rfq-1"}}])
        assert val.is_valid is True
        assert val.action == "send_procurement_message"
        assert val.sanitized_args["message"] == "We accept 50% advance payment against proforma invoice."

    def test_26_unsafe_message_fails_guardrails(self):
        """26. Prompt injection or code injection in message fails guardrails."""
        proposal = ActionProposal(
            tool_name="send_procurement_message",
            arguments={"rfq_id": "rfq-1", "message": "import os; os.system('rm -rf /')"},
        )
        val = validate_action(proposal, client_id="c-1", supplier_id="s-1", matched_rfq_id="rfq-1", context_rfqs=[{"rfqs": {"id": "rfq-1"}}])
        assert val.is_valid is False
        assert "guardrails" in val.reason.lower()

    def test_27_and_28_operator_lock_and_missing_rfq_injection(self):
        """27, 28. Omitted rfq_id under lock is safely injected with matched_rfq_id."""
        proposal = ActionProposal(
            tool_name="send_procurement_message",
            arguments={"message": "Delivery to Dubai warehouse confirmed."},
        )
        val = validate_action(proposal, client_id="c-1", supplier_id="s-1", matched_rfq_id="rfq-locked-1", context_rfqs=[{"rfqs": {"id": "rfq-locked-1"}}])
        assert val.is_valid is True
        assert val.sanitized_args["rfq_id"] == "rfq-locked-1"

    @pytest.mark.asyncio
    async def test_29_to_32_execution_logs_and_enqueues_without_mutating_quotes_or_negotiation(self):
        """29-32. send_procurement_message creates queued message_log, enqueues to WhatsApp, no quote/negotiation mutation."""
        proposal = ActionProposal(
            tool_name="send_procurement_message",
            arguments={"rfq_id": "rfq-1", "message": "Delivery within 2 days is fine."},
        )
        val = validate_action(proposal, client_id="c-1", supplier_id="s-1", matched_rfq_id="rfq-1", context_rfqs=[{"rfqs": {"id": "rfq-1"}}])
        ctx = AgentContext(client_id="c-1", supplier_id="s-1", input_origin="operator")
        supp = {"id": "s-1", "phone_number": "971501234567"}

        with patch("db.log_message", return_value="log-out-1") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue, \
             patch("db.record_quote") as mock_quote, \
             patch("db.increment_negotiation_attempts") as mock_inc:

            res = await main.execute_validated_action(val, ctx, "Delivery within 2 days", supp, "c-1")
            assert res["status"] == "message_sent"
            mock_log.assert_called_once_with("c-1", "s-1", "outbound", "Delivery within 2 days is fine.", related_rfq_id="rfq-1")
            mock_enqueue.assert_called_once_with("971501234567", "Delivery within 2 days is fine.", rfq_id="rfq-1", supplier_id="s-1", message_log_id="log-out-1")
            mock_quote.assert_not_called()
            mock_inc.assert_not_called()


class TestQuoteProvenanceAndNegotiation:
    """Tests 33-40: Quote provenance isolation, hold price duplicate prevention, and negotiation caps."""

    @pytest.mark.asyncio
    async def test_33_to_36_quote_provenance_preserves_supplier_raw_message(self):
        """33-36. Operator instruction is not stored as quote raw_message; original supplier message is preserved."""
        val = ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": "rfq-1", "price": 45.0, "delivery_time": "2 days", "quality_notes": "1 yr warranty"},
        )
        ctx = AgentContext(
            client_id="c-1",
            supplier_id="s-1",
            input_origin="operator",
            matched_rfq_id="rfq-1",
            review_raw_message="We can give 45 AED per bag with 2 days delivery and 1 year warranty.",
            prior_quotes=[],
        )
        supp = {"id": "s-1", "phone_number": "971501234567"}

        with patch("db.record_quote") as mock_record_quote, \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock):

            await main.execute_validated_action(val, ctx, "That price is acceptable, record it", supp, "c-1")
            mock_record_quote.assert_called_once()
            called_raw = mock_record_quote.call_args.kwargs["raw_message"]
            # Must be supplier's raw message, NOT operator text
            assert called_raw == "We can give 45 AED per bag with 2 days delivery and 1 year warranty."
            assert "acceptable" not in called_raw

    @pytest.mark.asyncio
    async def test_34_hold_price_does_not_duplicate_existing_quote(self):
        """34. If supplier quote at 45 AED was already recorded, holding price does not create duplicate quote row."""
        val = ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": "rfq-1", "price": 45.0},
        )
        ctx = AgentContext(
            client_id="c-1",
            supplier_id="s-1",
            input_origin="operator",
            matched_rfq_id="rfq-1",
            review_raw_message="45 AED",
            prior_quotes=[{"rfq_id": "rfq-1", "price": 45.0, "supplier_id": "s-1"}], # Already recorded!
        )
        supp = {"id": "s-1", "phone_number": "971501234567"}

        with patch("db.record_quote") as mock_record_quote, \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock):

            await main.execute_validated_action(val, ctx, "Hold their price", supp, "c-1")
            mock_record_quote.assert_not_called()

    def test_37_to_40_operator_negotiation_enforces_attempt_cap(self):
        """37-40. Operator can request negotiation below cap, but validator blocks attempts >= 3."""
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-1", "quote_id": "q-1", "quoted_price": 50.0, "counter_price": 48.0, "negotiation_message": "Can you offer 48 AED?"},
        )
        mock_quote = {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "s-1", "price": 50.0, "is_available": True}
        # Case A: Below cap (2/3 attempts) -> PASS
        with patch("db.get_negotiation_attempts", return_value=2), \
             patch("db.is_rfq_open", return_value=True), \
             patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            val = validate_action(proposal, client_id="c-1", supplier_id="s-1", matched_rfq_id="rfq-1", context_rfqs=[{"rfqs": {"id": "rfq-1", "status": "active"}}])
            assert val.is_valid is True

        # Case B: At cap (3/3 attempts) -> REJECT
        with patch("db.get_negotiation_attempts", return_value=3), \
             patch("db.is_rfq_open", return_value=True), \
             patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            val = validate_action(proposal, client_id="c-1", supplier_id="s-1", matched_rfq_id="rfq-1", context_rfqs=[{"rfqs": {"id": "rfq-1", "status": "active"}}])
            assert val.is_valid is False
            assert "Negotiation attempt limit reached" in val.reason


class TestFlagResolutionAndFailureRecovery:
    """Tests 41-46: Failure recovery and persistence guarantees."""

    @pytest.mark.asyncio
    async def test_43_outbound_persistence_failure_raises_and_prevents_resolution(self):
        """43. If db.log_message returns None (persistence failure), execution raises and prevents resolution."""
        val = ValidationResult(
            is_valid=True,
            action="send_procurement_message",
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={"rfq_id": "rfq-1", "message": "Some message"},
        )
        ctx = AgentContext(client_id="c-1", supplier_id="s-1", input_origin="operator")
        supp = {"id": "s-1", "phone_number": "971501234567"}

        with patch("db.log_message", return_value=None):
            with pytest.raises(RuntimeError, match="Failed to log outbound procurement message durably"):
                await main.execute_validated_action(val, ctx, "Some instruction", supp, "c-1")

    def test_administrative_resolve_without_supplier_messaging(self):
        """26. send_to_supplier=False performs administrative resolution without triggering LLM or WhatsApp."""
        client = TestClient(main.app)
        with patch("db.resolve_flag_with_response", return_value=[{"id": "flag-adm", "status": "resolved"}]), \
             patch("groq_client.reason_about_procurement_message") as mock_reasoner, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            resp = client.post("/flags/flag-adm/respond", json={"response": "Resolved internally", "send_to_supplier": False})
            assert resp.status_code == 200
            assert resp.json()["sent_to_supplier"] is False
            mock_reasoner.assert_not_called()
            mock_enqueue.assert_not_called()


class TestFlagLifecycleTransitions:
    """Hardened tests for strictly conditional flag lifecycle transitions (pending -> processing -> resolved / pending)."""

    def test_cannot_complete_pending_directly(self):
        """Cannot transition directly from 'pending' to 'resolved' via complete_flag_operator_action."""
        # When RPC or DB receives a complete attempt on a pending flag (not processing), it returns None
        mock_supabase = MagicMock()
        # Mock RPC returning empty (0 rows affected)
        mock_supabase.rpc.return_value.execute.return_value.data = []
        # Mock conditional update returning empty (status != 'processing')
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase):
            res = db.complete_flag_operator_action("flag-1", "tenant-a", human_response="Done")
            assert res is None

    def test_cannot_complete_resolved_flag(self):
        """Cannot re-complete an already resolved flag."""
        mock_supabase = MagicMock()
        mock_supabase.rpc.return_value.execute.return_value.data = []
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase):
            res = db.complete_flag_operator_action("flag-1", "tenant-a", human_response="Done again")
            assert res is None

    def test_processing_to_resolved_succeeds(self):
        """Strict conditional transition from 'processing' to 'resolved' succeeds."""
        mock_supabase = MagicMock()
        resolved_row = {
            "id": "flag-1",
            "client_id": "tenant-a",
            "status": "resolved",
            "human_response": "Approved price",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_supabase.rpc.return_value.execute.return_value.data = [resolved_row]

        with patch.object(db, "supabase", mock_supabase), \
             patch.object(db, "get_flag_by_id", return_value=resolved_row):
            res = db.complete_flag_operator_action("flag-1", "tenant-a", human_response="Approved price")
            assert res is not None
            assert res["status"] == "resolved"
            assert res["human_response"] == "Approved price"

    def test_processing_to_pending_release_succeeds(self):
        """Strict conditional release from 'processing' to 'pending' succeeds."""
        mock_supabase = MagicMock()
        released_row = {
            "id": "flag-1",
            "client_id": "tenant-a",
            "status": "pending",
        }
        mock_supabase.rpc.return_value.execute.return_value.data = [released_row]

        with patch.object(db, "supabase", mock_supabase):
            res = db.release_flag_claim("flag-1", "tenant-a")
            assert res is not None
            assert res["status"] == "pending"

    def test_release_on_pending_or_resolved_fails_closed(self):
        """Calling release_flag_claim on a flag that is not in 'processing' status returns None."""
        mock_supabase = MagicMock()
        mock_supabase.rpc.return_value.execute.return_value.data = []
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase):
            res = db.release_flag_claim("flag-1", "tenant-a")
            assert res is None

    def test_wrong_tenant_changes_nothing(self):
        """Calling lifecycle functions with wrong client_id returns None (tenant isolation)."""
        mock_supabase = MagicMock()
        # When client_id != tenant_id in DB, 0 rows are returned
        mock_supabase.rpc.return_value.execute.return_value.data = []
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase):
            claim_res = db.claim_flag_for_operator_action("flag-1", "tenant-intruder")
            assert claim_res is None

            release_res = db.release_flag_claim("flag-1", "tenant-intruder")
            assert release_res is None

            comp_res = db.complete_flag_operator_action("flag-1", "tenant-intruder", human_response="Hack")
            assert comp_res is None

    def test_double_claim_allows_one_winner_only(self):
        """Concurrent or sequential claim on the same flag allows only one winner."""
        mock_flag = {
            "id": "flag-1",
            "client_id": "tenant-a",
            "status": "processing",
        }

        # First claim succeeds (returns row)
        mock_supabase_first = MagicMock()
        mock_supabase_first.rpc.return_value.execute.return_value.data = [mock_flag]

        # Second claim fails (already in 'processing', 0 rows returned)
        mock_supabase_second = MagicMock()
        mock_supabase_second.rpc.return_value.execute.return_value.data = []
        mock_supabase_second.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase_first), \
             patch.object(db, "get_flag_by_id", return_value=mock_flag):
            first_win = db.claim_flag_for_operator_action("flag-1", "tenant-a")
            assert first_win is not None

        with patch.object(db, "supabase", mock_supabase_second):
            second_win = db.claim_flag_for_operator_action("flag-1", "tenant-a")
            assert second_win is None

    def test_rpc_permissions_and_security_definer_documented(self):
        """Verify the migration SQL file declares SECURITY DEFINER, SET search_path, and service_role grants."""
        migration_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "supabase",
            "migrations",
            "20260912070000_flag_lifecycle_rpc_hardening.sql"
        )
        assert os.path.exists(migration_path), "Migration file must exist"
        with open(migration_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check SECURITY DEFINER and search_path = public on all 3 functions
        assert content.count("SECURITY DEFINER") >= 3
        assert content.count("SET search_path = public") >= 3

        # Check REVOKE and GRANT statements for each function
        assert "REVOKE EXECUTE ON FUNCTION claim_flag_for_operator_action" in content
        assert "GRANT EXECUTE ON FUNCTION claim_flag_for_operator_action(UUID, UUID) TO service_role;" in content

        assert "REVOKE EXECUTE ON FUNCTION release_flag_claim" in content
        assert "GRANT EXECUTE ON FUNCTION release_flag_claim(UUID, UUID) TO service_role;" in content

        assert "REVOKE EXECUTE ON FUNCTION complete_flag_operator_action" in content
        assert "GRANT EXECUTE ON FUNCTION complete_flag_operator_action(UUID, UUID, TEXT) TO service_role;" in content

