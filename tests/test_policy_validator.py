"""
Unit and integration tests for Phase 5: Policy & Validator Layer.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

import db
import main
import groq_client
from policy_validator import (
    ActionProposal,
    ValidationResult,
    ActionCategory,
    validate_action,
)


@pytest.fixture
def mock_supabase():
    with patch.object(db, "supabase") as mock_sb:
        yield mock_sb


class TestPhase5PolicyValidator:
    """Test suite for deterministic Policy & Validator Layer."""

    def test_1_valid_record_quote_approved(self, mock_supabase):
        """Test 1: Valid structured proposal with active RFQ, valid price, and correct supplier -> Approved."""
        mock_rfq = {
            "id": "rfq-1",
            "client_id": "client-1",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat(),
        }
        mock_rfq_supp = {
            "id": "rs-1",
            "rfq_id": "rfq-1",
            "supplier_id": "supp-1",
            "status": "sent",
            "rfqs": mock_rfq,
        }

        proposal = ActionProposal(
            tool_name="record_quote",
            arguments={"rfq_id": "rfq-1", "price": 100.0, "delivery_time": "2 days"},
            confidence=0.95,
        )

        res = validate_action(
            proposal=proposal,
            client_id="client-1",
            supplier_id="supp-1",
            context_rfqs=[mock_rfq_supp],
        )

        assert res.is_valid is True
        assert res.category == ActionCategory.MUTATION
        assert res.action == "record_quote"
        assert res.sanitized_args["price"] == 100.0
        assert res.sanitized_args["rfq_id"] == "rfq-1"

    def test_2_expired_rfq_rejected(self, mock_supabase):
        """Test 2: LLM proposes record_quote but RFQ deadline has passed -> Rejected."""
        mock_rfq = {
            "id": "rfq-expired",
            "client_id": "client-1",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        }
        mock_rfq_supp = {
            "id": "rs-expired",
            "rfq_id": "rfq-expired",
            "supplier_id": "supp-1",
            "status": "sent",
            "rfqs": mock_rfq,
        }

        proposal = ActionProposal(
            tool_name="record_quote",
            arguments={"rfq_id": "rfq-expired", "price": 90.0},
            confidence=0.98,
        )

        res = validate_action(
            proposal=proposal,
            client_id="client-1",
            supplier_id="supp-1",
            context_rfqs=[mock_rfq_supp],
        )

        assert res.is_valid is False
        assert "closed or deadline has passed" in res.reason

    def test_3_wrong_supplier_not_associated_rejected(self, mock_supabase):
        """Test 3: LLM proposes quote for RFQ A + Supplier B, but Supplier B is not associated -> Rejected."""
        mock_rfqs_table = MagicMock()
        mock_rfqs_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[{
            "id": "rfq-foreign",
            "client_id": "client-1",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat(),
        }])

        mock_supp_table = MagicMock()
        # No rfq_suppliers row for supp-unassociated
        mock_supp_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        def table_router(t):
            if t == "rfqs":
                return mock_rfqs_table
            elif t == "rfq_suppliers":
                return mock_supp_table
            return MagicMock()

        mock_supabase.table.side_effect = table_router

        proposal = ActionProposal(
            tool_name="record_quote",
            arguments={"rfq_id": "rfq-foreign", "price": 50.0},
        )

        res = validate_action(
            proposal=proposal,
            client_id="client-1",
            supplier_id="supp-unassociated",
            context_rfqs=[],
        )

        assert res.is_valid is False
        assert "not associated with RFQ" in res.reason

    def test_4_cross_tenant_rfq_rejected(self, mock_supabase):
        """Test 4: Proposal references an RFQ belonging to a different client -> Rejected."""
        mock_rfq = {
            "id": "rfq-other-client",
            "client_id": "client-DIFFERENT-TENANT",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat(),
        }
        mock_rfq_supp = {
            "id": "rs-cross",
            "rfq_id": "rfq-other-client",
            "supplier_id": "supp-1",
            "status": "sent",
            "rfqs": mock_rfq,
        }

        proposal = ActionProposal(
            tool_name="record_quote",
            arguments={"rfq_id": "rfq-other-client", "price": 80.0},
        )

        res = validate_action(
            proposal=proposal,
            client_id="client-1",
            supplier_id="supp-1",
            context_rfqs=[mock_rfq_supp],
        )

        assert res.is_valid is False
        assert "Cross-tenant violation" in res.reason

    def test_5_responded_supplier_revision_approved(self, mock_supabase):
        """Test 5: Supplier already has status 'responded', proposes new revision while RFQ active -> Approved."""
        mock_rfq = {
            "id": "rfq-active-1",
            "client_id": "client-1",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
        }
        mock_rfq_supp = {
            "id": "rs-resp",
            "rfq_id": "rfq-active-1",
            "supplier_id": "supp-1",
            "status": "responded",
            "rfqs": mock_rfq,
        }

        proposal = ActionProposal(
            tool_name="record_quote",
            arguments={"rfq_id": "rfq-active-1", "price": 88.0, "delivery_time": "1 day"},
        )

        res = validate_action(
            proposal=proposal,
            client_id="client-1",
            supplier_id="supp-1",
            context_rfqs=[mock_rfq_supp],
        )

        assert res.is_valid is True
        assert res.sanitized_args["price"] == 88.0

    def test_6_closed_rfq_rejected(self, mock_supabase):
        """Test 6: Proposal against a manually or automatically closed RFQ -> Rejected."""
        mock_rfq = {
            "id": "rfq-closed",
            "client_id": "client-1",
            "status": "closed",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
        }
        mock_rfq_supp = {
            "id": "rs-closed",
            "rfq_id": "rfq-closed",
            "supplier_id": "supp-1",
            "status": "responded",
            "rfqs": mock_rfq,
        }

        proposal = ActionProposal(
            tool_name="record_quote",
            arguments={"rfq_id": "rfq-closed", "price": 75.0},
        )

        res = validate_action(
            proposal=proposal,
            client_id="client-1",
            supplier_id="supp-1",
            context_rfqs=[mock_rfq_supp],
        )

        assert res.is_valid is False
        assert "closed or deadline has passed" in res.reason

    def test_7_high_risk_action_blocked(self):
        """Test 7: LLM proposing high risk actions (close_rfq, accept_quote, generate_ranking) -> Blocked."""
        high_risk_proposals = [
            ActionProposal(tool_name="close_rfq", arguments={"rfq_id": "rfq-1"}),
            ActionProposal(tool_name="accept_quote", arguments={"rfq_id": "rfq-1", "supplier_id": "supp-1"}),
            ActionProposal(tool_name="generate_ranking", arguments={"rfq_id": "rfq-1"}),
            ActionProposal(tool_name="delete_quote", arguments={"quote_id": "q-1"}),
        ]

        for prop in high_risk_proposals:
            res = validate_action(
                proposal=prop,
                client_id="client-1",
                supplier_id="supp-1",
            )
            assert res.is_valid is False
            assert res.category == ActionCategory.HIGH_RISK
            assert "high-risk" in res.reason

    def test_8_invalid_payload_rejected(self):
        """Test 8: Missing or malformed required quote fields -> Rejected safely."""
        invalid_proposals = [
            ActionProposal(tool_name="record_quote", arguments={"rfq_id": None, "price": 100}),
            ActionProposal(tool_name="record_quote", arguments={"rfq_id": "rfq-1", "price": -10}),
            ActionProposal(tool_name="record_quote", arguments={"rfq_id": "rfq-1", "price": "free"}),
            ActionProposal(tool_name="record_quote", arguments={"rfq_id": "rfq-1", "price": float("nan")}),
            ActionProposal(tool_name="request_clarification", arguments={"candidate_rfq_ids": [], "clarifying_question": ""}),
        ]

        for prop in invalid_proposals:
            res = validate_action(
                proposal=prop,
                client_id="client-1",
                supplier_id="supp-1",
            )
            assert res.is_valid is False

    def test_9_high_confidence_cannot_bypass_policy(self, mock_supabase):
        """Test 9: Extremely high confidence score (0.999) cannot bypass deterministic policy violations."""
        mock_rfq = {
            "id": "rfq-expired",
            "client_id": "client-1",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
        }
        mock_rfq_supp = {
            "id": "rs-expired",
            "rfq_id": "rfq-expired",
            "supplier_id": "supp-1",
            "status": "sent",
            "rfqs": mock_rfq,
        }

        proposal = ActionProposal(
            tool_name="record_quote",
            arguments={"rfq_id": "rfq-expired", "price": 99.0},
            confidence=0.9999,
        )

        res = validate_action(
            proposal=proposal,
            client_id="client-1",
            supplier_id="supp-1",
            context_rfqs=[mock_rfq_supp],
        )

        assert res.is_valid is False
        assert "closed or deadline has passed" in res.reason

    @pytest.mark.asyncio
    async def test_10_webhook_policy_rejection_flow(self, mock_supabase):
        """Integration test: Webhook intercepts LLM proposal violating policy and flags for human review without DB mutation."""
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "923362853198@s.whatsapp.net", "fromMe": False, "id": "msg-policy-test"},
                "message": {"conversation": "Quote for expired RFQ"}
            }
        }
        mock_client = {"id": "client-1", "name": "Test Client", "whatsapp_instance": "Mohammad"}
        mock_supplier = {"id": "supp-1", "client_id": "client-1", "name": "Test Supplier", "phone_number": "923362853198"}
        
        # RFQ is expired
        mock_expired_rfq_supp = [
            {
                "id": "rs-1",
                "status": "sent",
                "rfq_id": "rfq-expired",
                "supplier_id": "supp-1",
                "rfqs": {"id": "rfq-expired", "product_name": "Panels", "status": "active", "due_by": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
            }
        ]

        request = MagicMock()
        request.json = AsyncMock(return_value=payload)

        with patch.object(db, "get_client_by_instance", return_value=mock_client), \
             patch.object(db, "get_supplier_by_phone", return_value=mock_supplier), \
             patch.object(db, "get_pending_clarification_for_supplier", return_value=None), \
             patch.object(db, "get_open_rfqs_for_supplier", return_value=mock_expired_rfq_supp), \
             patch.object(db, "get_supplier_prior_quotes", return_value=[]), \
             patch.object(groq_client, "route_supplier_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": "rfq-expired", "price": 90.0}
             }), \
             patch.object(db, "record_quote") as mock_record_quote, \
             patch.object(db, "flag_for_human_review") as mock_flag, \
             patch.object(db, "log_message") as mock_log, \
             patch.object(main, "enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            response = await main.whatsapp_webhook(request)

            assert response["status"] == "rejected_by_policy"
            mock_record_quote.assert_not_called()
            mock_flag.assert_called_once()
            flag_args = mock_flag.call_args.kwargs
            assert "Policy Validator rejection" in flag_args["reason"]
            mock_enqueue.assert_called_once_with("923362853198", main.HUMAN_ACK_MSG, message_log_id=mock_log.return_value)
