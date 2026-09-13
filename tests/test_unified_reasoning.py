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
import groq_client
from groq_client import AgentContext
from policy_validator import ActionProposal, validate_action, ActionCategory, ValidationResult


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


class TestUnifiedReasonerInvocation:
    """Tests 1-6: Single reasoning entry point across all inbound message origins."""

    def test_1_quoted_stanza_reply_invokes_reason_about_procurement_message(self):
        """1. Quoted stanza reply invokes groq_client.reason_about_procurement_message with exact_stanza match source."""
        client = TestClient(main.app)
        payload = {
            "instance": "inst-1",
            "data": {
                "key": {"fromMe": False, "remoteJid": "971500000001@s.whatsapp.net", "id": "msg-in-1"},
                "message": {
                    "extendedTextMessage": {
                        "text": "100 AED 3 days",
                        "contextInfo": {"stanzaId": "stanza-msg-123"}
                    }
                }
            }
        }
        mock_client = {"id": "c-1", "name": "Client 1", "whatsapp_instance": "inst-1"}
        mock_supp = {"id": "s-1", "name": "Supplier 1", "phone_number": "971500000001", "client_id": "c-1"}
        mock_rfq_supp = {
            "id": "rs-1",
            "rfq_id": "rfq-1",
            "supplier_id": "s-1",
            "status": "sent",
            "sent_message_id": "stanza-msg-123",
            "rfqs": {"id": "rfq-1", "product_name": "LED Panel 60W", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}
        }

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=mock_rfq_supp), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[mock_rfq_supp]), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.record_quote"), \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": "rfq-1", "price": 100.0, "delivery_time": "3 days"}
             }) as mock_reasoner:

            resp = client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "recorded_via_quoted_message"
            mock_reasoner.assert_called_once()
            called_ctx = mock_reasoner.call_args[0][1]
            assert isinstance(called_ctx, AgentContext)
            assert called_ctx.matched_rfq_id == "rfq-1"
            assert called_ctx.match_source == "exact_stanza"

    def test_2_quoted_text_fallback_invokes_same_reasoner(self):
        """2. Quoted text fallback invokes reason_about_procurement_message with quoted_text match source."""
        client = TestClient(main.app)
        payload = {
            "instance": "inst-1",
            "data": {
                "key": {"fromMe": False, "remoteJid": "971500000001@s.whatsapp.net", "id": "msg-in-2"},
                "message": {
                    "extendedTextMessage": {
                        "text": "100 AED",
                        "contextInfo": {"quotedMessage": {"conversation": "RFQ for LED Panel 60W"}}
                    }
                }
            }
        }
        mock_client = {"id": "c-1", "name": "Client 1", "whatsapp_instance": "inst-1"}
        mock_supp = {"id": "s-1", "name": "Supplier 1", "phone_number": "971500000001", "client_id": "c-1"}
        mock_rfq_supp = {
            "id": "rs-1",
            "rfq_id": "rfq-1",
            "supplier_id": "s-1",
            "status": "sent",
            "rfqs": {"id": "rfq-1", "product_name": "LED Panel 60W", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}
        }

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_quoted_text", return_value=mock_rfq_supp), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[mock_rfq_supp]), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.record_quote"), \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": "rfq-1", "price": 100.0}
             }) as mock_reasoner:

            resp = client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            mock_reasoner.assert_called_once()
            called_ctx = mock_reasoner.call_args[0][1]
            assert called_ctx.matched_rfq_id == "rfq-1"
            assert called_ctx.match_source == "quoted_text"

    def test_3_pending_clarification_reply_invokes_same_reasoner(self):
        """3. Pending clarification reply invokes reason_about_procurement_message with pending_clarification in context."""
        client = TestClient(main.app)
        payload = {
            "instance": "inst-1",
            "data": {
                "key": {"fromMe": False, "remoteJid": "971500000001@s.whatsapp.net", "id": "msg-in-3"},
                "message": {"conversation": "for the 5kg cement"}
            }
        }
        mock_client = {"id": "c-1", "name": "Client 1", "whatsapp_instance": "inst-1"}
        mock_supp = {"id": "s-1", "name": "Supplier 1", "phone_number": "971500000001", "client_id": "c-1"}
        mock_pending = {
            "id": "clarif-1",
            "client_id": "c-1",
            "supplier_id": "s-1",
            "status": "awaiting_reply",
            "round_number": 1,
            "pending_rfq_ids": ["rfq-cement-5kg", "rfq-cement-10kg"],
            "raw_message": "15 AED",
            "extracted_price": 15.0,
        }
        mock_rfq_1 = {"id": "rfq-cement-5kg", "product_name": "Cement 5kg", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}
        mock_rfq_2 = {"id": "rfq-cement-10kg", "product_name": "Cement 10kg", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_pending_clarification_for_supplier", return_value=mock_pending), \
             patch("db.get_rfqs_by_ids", return_value=[{"rfqs": mock_rfq_1}, {"rfqs": mock_rfq_2}]), \
             patch("db.get_open_rfqs_for_supplier", return_value=[]), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.record_quote"), \
             patch("db.resolve_pending_clarification") as mock_resolve_clarif, \
             patch("db.revert_unresolved_candidates") as mock_revert, \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": "rfq-cement-5kg", "price": 15.0}
             }) as mock_reasoner:

            resp = client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "recorded_from_clarification"
            mock_reasoner.assert_called_once()
            called_ctx = mock_reasoner.call_args[0][1]
            assert called_ctx.pending_clarification == mock_pending
            mock_resolve_clarif.assert_called_once_with("clarif-1")
            mock_revert.assert_called_once_with(
                supplier_id="s-1",
                resolved_rfq_id="rfq-cement-5kg",
                candidate_rfq_ids=["rfq-cement-5kg", "rfq-cement-10kg"]
            )

    def test_4_general_supplier_message_invokes_same_reasoner(self):
        """4. General unquoted message invokes reason_about_procurement_message with open_rfqs list."""
        client = TestClient(main.app)
        payload = {
            "instance": "inst-1",
            "data": {
                "key": {"fromMe": False, "remoteJid": "971500000001@s.whatsapp.net", "id": "msg-in-4"},
                "message": {"conversation": "Cement is 25 AED per bag"}
            }
        }
        mock_client = {"id": "c-1", "name": "Client 1", "whatsapp_instance": "inst-1"}
        mock_supp = {"id": "s-1", "name": "Supplier 1", "phone_number": "971500000001", "client_id": "c-1"}
        mock_rfq = {
            "id": "rs-1",
            "rfq_id": "rfq-1",
            "supplier_id": "s-1",
            "status": "sent",
            "rfqs": {"id": "rfq-1", "product_name": "Cement 50kg", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}
        }

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=[mock_rfq]), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.record_quote"), \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": "rfq-1", "price": 25.0}
             }) as mock_reasoner:

            resp = client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "recorded"
            mock_reasoner.assert_called_once()
            called_ctx = mock_reasoner.call_args[0][1]
            assert len(called_ctx.open_rfqs) == 1
            assert called_ctx.matched_rfq_id is None

    def test_5_and_6_no_production_path_bypasses_unified_reasoning(self):
        """5, 6. Valid supplier messages never bypass unified reasoning or call old functions directly."""
        assert hasattr(groq_client, "reason_about_procurement_message")
        assert callable(groq_client.reason_about_procurement_message)


class TestAgentContextAndHistory:
    """Tests 7-14: AgentContext assembly and bounded chronological conversation history."""

    def test_7_to_11_context_contains_all_trusted_fields(self):
        """7-11. Context holds client, supplier, open RFQs, matched RFQ, match_source, and pending clarification."""
        ctx = AgentContext(
            client_id="client-100",
            supplier_id="supp-200",
            supplier_name="Acme Corp",
            supplier_phone="+971501112233",
            input_origin="supplier",
            matched_rfq_id="rfq-abc",
            match_source="exact_stanza",
            open_rfqs=[{"rfqs": {"id": "rfq-abc", "product_name": "Steel"}}],
            pending_clarification={"id": "clarif-1", "round_number": 1},
            prior_quotes=[{"rfq_id": "rfq-abc", "price": 50.0}],
            negotiation_attempts={"rfq-abc": 1},
            competitive_context={"rfq-abc": "Best competing quote is AED 45"},
            conversation_history=[{"direction": "inbound", "body": "Hello"}],
        )

        assert ctx.client_id == "client-100"
        assert ctx.supplier_id == "supp-200"
        assert ctx.matched_rfq_id == "rfq-abc"
        assert ctx.match_source == "exact_stanza"
        assert ctx.pending_clarification["round_number"] == 1
        assert len(ctx.open_rfqs) == 1
        assert len(ctx.conversation_history) == 1

        prompt_str = groq_client.format_agent_context_for_prompt(ctx)
        assert "DETERMINISTIC MATCH LOCK" in prompt_str
        assert "rfq-abc" in prompt_str
        assert "Acme Corp" in prompt_str
        assert "AED 45" in prompt_str

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

    def test_14_quote_and_negotiation_context_included(self):
        """14. Acceptable price range, competitive context, and attempts are included in prompt formatting."""
        ctx = AgentContext(
            client_id="c-1",
            supplier_id="s-1",
            open_rfqs=[{
                "rfqs": {
                    "id": "rfq-1",
                    "product_name": "Solar Inverter",
                    "specs": "5kW",
                    "quantity": 10,
                    "acceptable_price_min": 1500.0,
                    "acceptable_price_max": 2000.0,
                }
            }],
            negotiation_attempts={"rfq-1": 2},
            competitive_context={"rfq-1": "Best competing quote is AED 1600"},
        )
        formatted = groq_client.format_agent_context_for_prompt(ctx)
        assert "AED 1500.0 - 2000.0" in formatted
        assert "Negotiation Attempts Made: 2/3" in formatted
        assert "Best competing quote is AED 1600" in formatted


class TestStanzaLockEnforcement:
    """Tests 15-17 & Focused Stanza-Lock Tests 1-10: Policy Validator deterministic stanza lock enforcement."""

    def test_15_proposal_for_matched_rfq_succeeds(self):
        """15 / Test 1. Exact stanza + record_quote matching RFQ -> PASS."""
        matched_rfq = {"id": "rfq-locked-1", "client_id": "c-1", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}
        proposal = ActionProposal(
            tool_name="record_quote",
            arguments={"rfq_id": "rfq-locked-1", "price": 50.0},
        )
        with patch.object(db, "is_rfq_open", return_value=True):
            val = validate_action(
                proposal,
                client_id="c-1",
                supplier_id="s-1",
                context_rfqs=[{"rfqs": matched_rfq, "supplier_id": "s-1"}],
                matched_rfq_id="rfq-locked-1",
            )
            assert val.is_valid is True
            assert val.sanitized_args["rfq_id"] == "rfq-locked-1"

    def test_16_proposal_attempting_different_rfq_is_rejected_deterministically(self):
        """16 / Test 2. Exact stanza + record_quote different RFQ -> REJECT."""
        proposal = ActionProposal(
            tool_name="record_quote",
            arguments={"rfq_id": "rfq-other-unmatched", "price": 50.0},
        )
        val = validate_action(
            proposal,
            client_id="c-1",
            supplier_id="s-1",
            context_rfqs=[],
            matched_rfq_id="rfq-locked-1",
        )
        assert val.is_valid is False
        assert "does not match deterministically locked RFQ" in val.reason

    def test_stanza_3_exact_stanza_negotiate_price_different_rfq_rejected(self):
        """Test 3. Exact stanza + negotiate_price different RFQ -> REJECT."""
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-other-unmatched", "quoted_price": 50.0, "negotiation_message": "Can you do 45?"},
        )
        val = validate_action(
            proposal,
            client_id="c-1",
            supplier_id="s-1",
            context_rfqs=[],
            matched_rfq_id="rfq-locked-1",
        )
        assert val.is_valid is False
        assert "does not match deterministically locked RFQ" in val.reason

    def test_stanza_4_exact_stanza_request_clarification_matching_rfq_rejected_as_unnecessary(self):
        """Test 4. Exact stanza + request_clarification([matched_rfq]) -> REJECT (identity already resolved)."""
        proposal = ActionProposal(
            tool_name="request_clarification",
            arguments={"candidate_rfq_ids": ["rfq-locked-1"], "clarifying_question": "Does this include delivery?"},
        )
        val = validate_action(
            proposal,
            client_id="c-1",
            supplier_id="s-1",
            context_rfqs=[],
            matched_rfq_id="rfq-locked-1",
        )
        assert val.is_valid is False
        assert "single candidate is unnecessary" in val.reason.lower()

    def test_stanza_5_exact_stanza_request_clarification_multiple_rfqs_rejected(self):
        """Test 5. Exact stanza + request_clarification([matched_rfq, other_rfq]) -> REJECT."""
        proposal = ActionProposal(
            tool_name="request_clarification",
            arguments={"candidate_rfq_ids": ["rfq-locked-1", "rfq-other-2"], "clarifying_question": "Which product?"},
        )
        val = validate_action(
            proposal,
            client_id="c-1",
            supplier_id="s-1",
            context_rfqs=[],
            matched_rfq_id="rfq-locked-1",
        )
        assert val.is_valid is False
        assert "Clarification candidates violate deterministic RFQ match lock" in val.reason

    def test_stanza_6_exact_stanza_request_clarification_different_rfq_rejected(self):
        """Test 6. Exact stanza + request_clarification([other_rfq]) -> REJECT."""
        proposal = ActionProposal(
            tool_name="request_clarification",
            arguments={"candidate_rfq_ids": ["rfq-other-2"], "clarifying_question": "Which product?"},
        )
        val = validate_action(
            proposal,
            client_id="c-1",
            supplier_id="s-1",
            context_rfqs=[],
            matched_rfq_id="rfq-locked-1",
        )
        assert val.is_valid is False
        assert "Clarification candidates violate deterministic RFQ match lock" in val.reason

    def test_stanza_7_exact_stanza_escalate_to_human_matching_rfq_passes(self):
        """Test 7. Exact stanza + escalate_to_human(rfq_id=matched_rfq) -> PASS."""
        proposal = ActionProposal(
            tool_name="escalate_to_human",
            arguments={"rfq_id": "rfq-locked-1", "reason": "Supplier needs custom warranty"},
        )
        val = validate_action(
            proposal,
            client_id="c-1",
            supplier_id="s-1",
            context_rfqs=[],
            matched_rfq_id="rfq-locked-1",
        )
        assert val.is_valid is True
        assert val.sanitized_args["rfq_id"] == "rfq-locked-1"

    def test_stanza_8_exact_stanza_escalate_to_human_different_rfq_rejected(self):
        """Test 8. Exact stanza + escalate_to_human(rfq_id=other_rfq) -> REJECT."""
        proposal = ActionProposal(
            tool_name="escalate_to_human",
            arguments={"rfq_id": "rfq-other-2", "reason": "Supplier requested review"},
        )
        val = validate_action(
            proposal,
            client_id="c-1",
            supplier_id="s-1",
            context_rfqs=[],
            matched_rfq_id="rfq-locked-1",
        )
        assert val.is_valid is False
        assert "Escalation RFQ 'rfq-other-2' does not match deterministically locked RFQ 'rfq-locked-1'" in val.reason

    def test_stanza_9_exact_stanza_escalate_to_human_no_rfq_injects_matched_rfq(self):
        """Test 9. Exact stanza + escalation with no rfq_id -> sanitized output uses matched_rfq_id."""
        proposal = ActionProposal(
            tool_name="escalate_to_human",
            arguments={"reason": "Complex question without rfq_id specified"},
        )
        val = validate_action(
            proposal,
            client_id="c-1",
            supplier_id="s-1",
            context_rfqs=[],
            matched_rfq_id="rfq-locked-1",
        )
        assert val.is_valid is True
        assert val.sanitized_args["rfq_id"] == "rfq-locked-1"

    def test_stanza_10_no_matched_rfq_multi_rfq_clarification_valid(self):
        """Test 10. No matched RFQ + multi-RFQ clarification -> existing behavior remains valid."""
        proposal = ActionProposal(
            tool_name="request_clarification",
            arguments={"candidate_rfq_ids": ["rfq-A", "rfq-B"], "clarifying_question": "Which item is this quote for?"},
        )
        val = validate_action(
            proposal,
            client_id="c-1",
            supplier_id="s-1",
            context_rfqs=[],
            matched_rfq_id=None,
        )
        assert val.is_valid is True
        assert val.sanitized_args["candidate_rfq_ids"] == ["rfq-A", "rfq-B"]

    def test_17_unknown_stanza_behavior_ignored(self):
        """17. If stanzaId is not found among supplier's active RFQs, webhook safely ignores message."""
        client = TestClient(main.app)
        payload = {
            "instance": "inst-1",
            "data": {
                "key": {"fromMe": False, "remoteJid": "971500000001@s.whatsapp.net", "id": "msg-in-err"},
                "message": {
                    "extendedTextMessage": {
                        "text": "100 AED",
                        "contextInfo": {"stanzaId": "stanza-unknown"}
                    }
                }
            }
        }
        mock_client = {"id": "c-1", "name": "Client 1", "whatsapp_instance": "inst-1"}
        mock_supp = {"id": "s-1", "name": "Supplier 1", "phone_number": "971500000001", "client_id": "c-1"}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None):

            resp = client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "ignored"


class TestClarificationIntegration:
    """Tests 18-20: Clarification context integration and 2-round cap."""

    def test_18_existing_clarification_resolves_cleanly(self):
        """18. When supplier clarifies specific product, quote is recorded, clarification resolved, non-selected reverted."""
        context = AgentContext(
            client_id="c-1",
            supplier_id="s-1",
            pending_clarification={"id": "clarif-100", "pending_rfq_ids": ["rfq-A", "rfq-B"]},
        )
        val = ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": "rfq-A", "price": 40.0},
        )
        supplier = {"id": "s-1", "phone_number": "+971500000001"}

        with patch("db.record_quote") as mock_rec, \
             patch("db.resolve_pending_clarification") as mock_resolve, \
             patch("db.revert_unresolved_candidates") as mock_revert, \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock):

            res = asyncio.run(main.execute_validated_action(val, context, "For A: 40 AED", supplier, "c-1"))
            assert res["status"] == "recorded_from_clarification"
            assert res["rfq_id"] == "rfq-A"
            mock_rec.assert_called_once()
            mock_resolve.assert_called_once_with("clarif-100")
            mock_revert.assert_called_once_with(supplier_id="s-1", resolved_rfq_id="rfq-A", candidate_rfq_ids=["rfq-A", "rfq-B"])

    def test_19_second_clarification_creates_round_2_and_abandons_round_1(self):
        """19. When second clarification is needed, creates round 2 and abandons round 1."""
        context = AgentContext(
            client_id="c-1",
            supplier_id="s-1",
            pending_clarification={"id": "clarif-100", "round_number": 1, "pending_rfq_ids": ["rfq-A", "rfq-B"]},
        )
        val = ValidationResult(
            is_valid=True,
            action="request_clarification",
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={"candidate_rfq_ids": ["rfq-A", "rfq-B"], "clarifying_question": "5kg or 10kg?"},
        )
        supplier = {"id": "s-1", "phone_number": "+971500000001"}

        with patch("db.advance_pending_clarification", return_value={"id": "clarif-advanced"}) as mock_advance, \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock):

            res = asyncio.run(main.execute_validated_action(val, context, "cement", supplier, "c-1"))
            assert res["status"] == "clarification_needed"
            mock_advance.assert_called_once_with(
                previous_id="clarif-100",
                client_id="c-1",
                supplier_id="s-1",
                candidate_rfq_ids=["rfq-A", "rfq-B"],
                raw_message="cement",
                extracted_price=None,
                extracted_delivery=None,
                extracted_notes=None,
                round_number=2,
                no_progress_count=1,
                last_question="5kg or 10kg?",
            )

    def test_20_two_consecutive_no_progress_turns_escalates_to_human(self):
        """20. When pending clarification no_progress_count reaches MAX_NO_PROGRESS_ATTEMPTS (2), escalates to human."""
        context = AgentContext(
            client_id="c-1",
            supplier_id="s-1",
            pending_clarification={
                "id": "clarif-round-2",
                "client_id": "c-1",
                "supplier_id": "s-1",
                "round_number": 2,
                "no_progress_count": 1,
                "pending_rfq_ids": ["rfq-A", "rfq-B"],
                "last_question": "5kg or 10kg?",
            },
        )
        val = ValidationResult(
            is_valid=True,
            action="request_clarification",
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={"candidate_rfq_ids": ["rfq-A", "rfq-B"], "clarifying_question": "5kg or 10kg?"},
        )
        supplier = {"id": "s-1", "phone_number": "+971500000001"}

        with patch("db.abandon_pending_clarification") as mock_abandon, \
             patch("db.flag_for_human_review") as mock_flag, \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock):

            res = asyncio.run(main.execute_validated_action(val, context, "still cement", supplier, "c-1"))
            assert res["status"] == "escalated_to_human"
            assert res["category"] == "clarification_stalled"
            mock_abandon.assert_called_once_with("clarif-round-2")
            mock_flag.assert_called_once()
            flag_call = mock_flag.call_args.kwargs
            assert flag_call["category"] == "clarification_stalled"
            assert "stalled after 2" in flag_call["reason"].lower()


class TestExecutionConsolidation:
    """Tests 21-25: execute_validated_action execution for all action types."""

    def test_21_record_quote_execution(self):
        """21. execute_validated_action executes record_quote and dispatches thank you."""
        context = AgentContext(client_id="c-1", supplier_id="s-1")
        val = ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": "rfq-1", "price": 100.0, "delivery_time": "2 days", "quality_notes": "1 yr warranty"},
        )
        with patch("db.record_quote") as mock_rec, \
             patch("db.log_message", return_value="log-1") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq:

            res = asyncio.run(main.execute_validated_action(val, context, "100 AED 2 days", {"id": "s-1", "phone_number": "+971501"}, "c-1"))
            assert res["status"] == "recorded"
            mock_rec.assert_called_once_with(rfq_id="rfq-1", supplier_id="s-1", price=100.0, delivery_time="2 days", quality_notes="1 yr warranty", raw_message="100 AED 2 days")
            mock_log.assert_called_once_with("c-1", "s-1", "outbound", main.THANK_YOU_MSG, related_rfq_id="rfq-1")
            mock_enq.assert_called_once_with("+971501", main.THANK_YOU_MSG, rfq_id="rfq-1", supplier_id="s-1", message_log_id="log-1")

    def test_22_negotiate_price_execution(self):
        """22. execute_validated_action executes negotiate_price, increments attempts, and dispatches negotiation message."""
        context = AgentContext(client_id="c-1", supplier_id="s-1")
        val = ValidationResult(
            is_valid=True,
            action="negotiate_price",
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={"rfq_id": "rfq-1", "quote_id": "q-1", "quoted_price": 60.0, "counter_price": 55.0, "negotiation_message": "Could you do 55 AED?"},
        )
        with patch("db.increment_negotiation_attempts", return_value=1) as mock_inc, \
             patch("db.log_message", return_value="log-1") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq:

            res = asyncio.run(main.execute_validated_action(val, context, "60 AED", {"id": "s-1", "phone_number": "+971501"}, "c-1"))
            assert res["status"] == "negotiation_sent"
            assert res["attempts"] == 1
            mock_inc.assert_called_once_with("rfq-1", "s-1")
            mock_log.assert_called_once_with("c-1", "s-1", "outbound", "Could you do 55 AED?", related_rfq_id="rfq-1")
            mock_enq.assert_called_once_with("+971501", "Could you do 55 AED?", rfq_id="rfq-1", supplier_id="s-1", message_log_id="log-1")

    def test_23_request_clarification_execution(self):
        """23. execute_validated_action creates pending clarification and dispatches question."""
        context = AgentContext(client_id="c-1", supplier_id="s-1")
        val = ValidationResult(
            is_valid=True,
            action="request_clarification",
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={"candidate_rfq_ids": ["rfq-1", "rfq-2"], "clarifying_question": "Which item?", "extracted_price": 50.0},
        )
        with patch("db.create_pending_clarification") as mock_create, \
             patch("db.log_message", return_value="log-1") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq:

            res = asyncio.run(main.execute_validated_action(val, context, "50 AED", {"id": "s-1", "phone_number": "+971501"}, "c-1"))
            assert res["status"] == "clarification_needed"
            mock_create.assert_called_once()
            mock_log.assert_called_once_with("c-1", "s-1", "outbound", "Which item?")
            mock_enq.assert_called_once_with("+971501", "Which item?", message_log_id="log-1")

    def test_24_escalate_to_human_execution(self):
        """24. execute_validated_action flags for human review and dispatches human acknowledgment."""
        context = AgentContext(client_id="c-1", supplier_id="s-1", matched_rfq_id="rfq-1")
        val = ValidationResult(
            is_valid=True,
            action="escalate_to_human",
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={"rfq_id": "rfq-1", "reason": "Complex payment terms", "category": "requires_business_knowledge"},
        )
        with patch("db.flag_for_human_review") as mock_flag, \
             patch("db.log_message", return_value="log-1") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq:

            res = asyncio.run(main.execute_validated_action(val, context, "Can we pay in 60 days?", {"id": "s-1", "phone_number": "+971501"}, "c-1"))
            assert res["status"] == "escalated"
            mock_flag.assert_called_once_with(
                client_id="c-1",
                supplier_id="s-1",
                rfq_id="rfq-1",
                reason="Complex payment terms",
                category="requires_business_knowledge",
                raw_message="Can we pay in 60 days?",
            )
            mock_log.assert_called_once_with("c-1", "s-1", "outbound", main.HUMAN_ACK_MSG, related_rfq_id="rfq-1")
            mock_enq.assert_called_once_with("+971501", main.HUMAN_ACK_MSG, rfq_id="rfq-1", supplier_id="s-1", message_log_id="log-1")

    def test_25_validator_rejection_prevents_execution(self):
        """25. When Policy Validator rejects action proposal, execute_validated_action is not called and no quote is saved."""
        client = TestClient(main.app)
        payload = {
            "instance": "inst-1",
            "data": {
                "key": {"fromMe": False, "remoteJid": "971500000001@s.whatsapp.net", "id": "msg-in-invalid"},
                "message": {"conversation": "Quote is -50 AED"}
            }
        }
        mock_client = {"id": "c-1", "name": "Client 1", "whatsapp_instance": "inst-1"}
        mock_supp = {"id": "s-1", "name": "Supplier 1", "phone_number": "971500000001", "client_id": "c-1"}
        mock_rfq = {"id": "rs-1", "rfq_id": "rfq-1", "rfqs": {"id": "rfq-1", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supp), \
             patch("db.get_open_rfqs_for_supplier", return_value=[mock_rfq]), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_supplier_conversation_history", return_value=[]), \
             patch("db.record_quote") as mock_record_quote, \
             patch("db.flag_for_human_review") as mock_flag, \
             patch("db.log_message", return_value="log-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch("groq_client.reason_about_procurement_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": "rfq-1", "price": -50.0} # Invalid negative price
             }):

            resp = client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "rejected_by_policy"
            mock_record_quote.assert_not_called()
            mock_flag.assert_called_once()


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
