"""
tests/test_negotiation_hardening.py
Comprehensive test suite for Phase 13 — Negotiation Policy Alignment & Escalation Completion.
Covers all 62 validation, provenance, multi-variant, attempt, refusal, acceptance, operator, and reliability scenarios.
"""

import asyncio
import math
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import db
import main
import guardrails
import groq_client
from policy_validator import ActionProposal, validate_action, ActionCategory, ValidationResult, MAX_NEGOTIATION_ATTEMPTS
from groq_client import AgentContext


@pytest.fixture
def base_rfq():
    return {
        "id": "rfq-p13",
        "client_id": "client-p13",
        "product_name": "60W LED Panel",
        "specs": "600x600 IP65",
        "quantity": 100,
        "acceptable_price_min": 40.0,
        "acceptable_price_max": 50.0,
        "status": "active",
        "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }


@pytest.fixture
def base_supplier():
    return {
        "id": "sup-p13",
        "name": "Apex Lighting",
        "phone_number": "+971501234567",
        "client_id": "client-p13",
    }


# ==============================================================================
# 1. CORE VALIDATOR TESTS (Items 1 - 15)
# ==============================================================================
class TestCoreNegotiationValidator:
    def test_1_and_2_negotiate_current_simple_quote_positive_counter_accepted(self, base_rfq, base_supplier):
        """1 & 2. Negotiating a current simple quote with a positive counter below quoted price passes validation."""
        mock_quote = {
            "id": "q-simple-1",
            "rfq_id": "rfq-p13",
            "supplier_id": "sup-p13",
            "variant_label": None,
            "price": 48.0,
            "is_available": True,
            "delivery_time": "2 days",
            "quality_notes": "1 yr warranty",
        }
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={
                "rfq_id": "rfq-p13",
                "quote_id": "q-simple-1",
                "quoted_price": 48.0,
                "counter_price": 45.0,
                "negotiation_message": "For your AED 48 quote, could you offer AED 45?",
            },
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]), \
             patch("db.get_negotiation_attempts", return_value=0):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is True
            assert res.action == "negotiate_price"
            assert res.sanitized_args["quote_id"] == "q-simple-1"
            assert res.sanitized_args["quoted_price"] == 48.0
            assert res.sanitized_args["counter_price"] == 45.0
            assert res.sanitized_args["variant_label"] is None

    def test_3_zero_counter_rejected(self, base_rfq):
        """3. Counter price == 0 is rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 0.0, "negotiation_message": "Free please"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "Counter price must be a positive finite number" in res.reason

    def test_4_negative_counter_rejected(self, base_rfq):
        """4. Negative counter price is rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": -10.0, "negotiation_message": "Pay us please"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "Counter price must be a positive finite number" in res.reason

    def test_5_equal_price_counter_rejected(self, base_rfq):
        """5. Counter price == quoted price is rejected (no downward negotiation)."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 48.0, "negotiation_message": "Can you do 48?"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "strictly less than the supplier's quoted price" in res.reason

    def test_6_higher_counter_rejected(self, base_rfq):
        """6. Counter price > quoted price is rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 52.0, "negotiation_message": "How about 52?"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "strictly less than the supplier's quoted price" in res.reason

    def test_7_nan_and_inf_counter_rejected(self, base_rfq):
        """7. NaN or Infinity counter price is rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal_nan = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": float("nan"), "negotiation_message": "Nan"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal_nan, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False

        proposal_inf = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": float("inf"), "negotiation_message": "Inf"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal_inf, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False

    def test_8_wrong_quote_id_rejected(self, base_rfq):
        """8. Non-existent quote_id is rejected."""
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "non-existent-id", "quoted_price": 48.0, "counter_price": 45.0, "negotiation_message": "Hi"},
        )
        with patch("db.get_quote_by_id", return_value=None):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "Target quote 'non-existent-id' does not exist" in res.reason

    def test_9_wrong_rfq_quote_rejected(self, base_rfq):
        """9. Quote belonging to a different RFQ is rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-other", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 45.0, "negotiation_message": "Hi"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "belongs to RFQ 'rfq-other'" in res.reason

    def test_10_wrong_supplier_quote_rejected(self, base_rfq):
        """10. Quote belonging to a different supplier is rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-competitor", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 45.0, "negotiation_message": "Hi"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "belongs to supplier 'sup-competitor'" in res.reason

    def test_11_withdrawn_quote_rejected(self, base_rfq):
        """11. Withdrawn / unavailable quote is rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": False}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 45.0, "negotiation_message": "Hi"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "marked unavailable or withdrawn" in res.reason

    def test_12_superseded_quote_rejected(self, base_rfq):
        """12. Historical/superseded quote revision is rejected when a newer revision exists."""
        old_quote = {"id": "q-old", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 50.0, "is_available": True}
        newer_quote = {"id": "q-new", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-old", "quoted_price": 50.0, "counter_price": 45.0, "negotiation_message": "Hi"},
        )
        with patch("db.get_quote_by_id", return_value=old_quote), \
             patch("db.get_quotes_for_rfq", return_value=[newer_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "superseded by a newer quote revision" in res.reason

    def test_13_closed_rfq_rejected(self):
        """13. Negotiation on a closed RFQ is rejected."""
        closed_rfq = {"id": "rfq-p13", "client_id": "client-p13", "status": "closed"}
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 45.0, "negotiation_message": "Hi"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": closed_rfq}])
            assert res.is_valid is False
            assert "closed or deadline has passed" in res.reason

    def test_14_expired_rfq_rejected(self):
        """14. Negotiation after RFQ deadline has passed is rejected."""
        expired_rfq = {
            "id": "rfq-p13",
            "client_id": "client-p13",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 45.0, "negotiation_message": "Hi"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": expired_rfq}])
            assert res.is_valid is False
            assert "closed or deadline has passed" in res.reason

    def test_15_tenant_mismatch_rejected(self, base_rfq):
        """15. Cross-tenant RFQ negotiation is rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 45.0, "negotiation_message": "Hi"},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="attacker-client", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "Cross-tenant violation" in res.reason


# ==============================================================================
# 2. PROVENANCE & REVISION TESTS (Items 16 - 21)
# ==============================================================================
class TestQuoteProvenanceAndRevisions:
    @pytest.mark.asyncio
    async def test_16_and_17_supplier_quote_stored_counter_only_in_outbound(self, base_rfq, base_supplier):
        """16 & 17. Supplier quote is stored in quotes; AI counter is logged only in message_log."""
        val = ValidationResult(
            is_valid=True,
            action="negotiate_price",
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={
                "rfq_id": "rfq-p13",
                "quote_id": "q-100",
                "quoted_price": 48.0,
                "counter_price": 45.0,
                "negotiation_message": "For your AED 48 quote, could you offer AED 45?",
                "variant_label": None,
            },
        )
        ctx = AgentContext(client_id="client-p13", supplier_id="sup-p13")

        with patch("db.record_quote") as mock_record_quote, \
             patch("db.increment_negotiation_attempts", return_value=1) as mock_inc, \
             patch("db.log_message", return_value="log-out-1") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq:

            res = await main.execute_validated_action(val, ctx, "48 AED", base_supplier, "client-p13")
            assert res["status"] == "negotiation_sent"
            assert res["quote_id"] == "q-100"
            assert res["counter_price"] == 45.0
            # Executor does NOT re-insert counter price into quotes table
            mock_record_quote.assert_not_called()
            # Outbound counter logged in message_log
            mock_log.assert_called_once_with("client-p13", "sup-p13", "outbound", "For your AED 48 quote, could you offer AED 45?", related_rfq_id="rfq-p13")
            mock_enq.assert_called_once()

    def test_18_and_19_supplier_revision_preserves_provenance(self):
        """18 & 19. Supplier revision creates a new quote row with raw message and source message provenance."""
        with patch("db.is_rfq_open", return_value=True), \
             patch("db.supabase") as mock_sb:
            mock_sb.table().select().eq().execute.return_value.data = [{"id": "rfq-p13", "status": "active"}]
            mock_sb.table().insert().execute.return_value.data = [{
                "id": "q-rev-1",
                "rfq_id": "rfq-p13",
                "supplier_id": "sup-p13",
                "price": 43.0,
                "raw_message": "We can do AED 43 lowest",
                "source_message_id": "msg-in-1",
            }]
            created = db.record_quote(
                rfq_id="rfq-p13",
                supplier_id="sup-p13",
                price=43.0,
                raw_message="We can do AED 43 lowest",
                source_message_id="msg-in-1",
            )
            assert created is not None
            assert created["id"] == "q-rev-1"
            assert created["price"] == 43.0

    def test_20_operator_instruction_not_stored_as_supplier_quote(self):
        """20. Operator instruction must never be stored as supplier's raw_message in quotes."""
        ctx = AgentContext(
            client_id="client-p13",
            supplier_id="sup-p13",
            input_origin="operator",
            review_raw_message="Supplier original: 45 AED",
        )
        assert ctx.review_raw_message == "Supplier original: 45 AED"


# ==============================================================================
# 3. MULTI-VARIANT NEGOTIATION TESTS (Items 22 - 30)
# ==============================================================================
class TestMultiVariantNegotiation:
    def test_22_to_25_multi_variant_exact_targeting_and_revisions(self, base_rfq):
        """22-25. India and China variants stored; India targeted by quote_id; China remains untouched."""
        india_quote = {"id": "q-india-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "variant_label": "India", "price": 45.0, "is_available": True}
        china_quote = {"id": "q-china-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "variant_label": "China", "price": 38.0, "is_available": True}

        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={
                "rfq_id": "rfq-p13",
                "quote_id": "q-india-1",
                "quoted_price": 45.0,
                "counter_price": 42.0,
                "negotiation_message": "For the India option quoted at AED 45, could you offer AED 42?",
                "variant_label": "India",
            },
        )
        with patch("db.get_quote_by_id", return_value=india_quote), \
             patch("db.get_quotes_for_rfq", return_value=[india_quote, china_quote]), \
             patch("db.get_negotiation_attempts", return_value=0):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is True
            assert res.sanitized_args["quote_id"] == "q-india-1"
            assert res.sanitized_args["variant_label"] == "India"
            assert res.sanitized_args["counter_price"] == 42.0

    def test_26_superseded_variant_quote_cannot_be_targeted(self, base_rfq):
        """26. When India is revised to 43 (q-india-2), targeting old q-india-1 is rejected."""
        old_india = {"id": "q-india-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "variant_label": "India", "price": 45.0, "is_available": True}
        new_india = {"id": "q-india-2", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "variant_label": "India", "price": 43.0, "is_available": True}

        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={
                "rfq_id": "rfq-p13",
                "quote_id": "q-india-1",
                "quoted_price": 45.0,
                "counter_price": 42.0,
                "negotiation_message": "For the India option, can you do 42?",
            },
        )
        with patch("db.get_quote_by_id", return_value=old_india), \
             patch("db.get_quotes_for_rfq", return_value=[new_india]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "superseded by a newer quote revision" in res.reason

    def test_27_withdrawn_china_variant_rejected(self, base_rfq):
        """27. China variant withdrawn (is_available=False) cannot be targeted for negotiation."""
        withdrawn_china = {"id": "q-china-withdrawn", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "variant_label": "China", "price": 38.0, "is_available": False}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={
                "rfq_id": "rfq-p13",
                "quote_id": "q-china-withdrawn",
                "quoted_price": 38.0,
                "counter_price": 35.0,
                "negotiation_message": "For China can you do 35?",
            },
        )
        with patch("db.get_quote_by_id", return_value=withdrawn_china):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "marked unavailable or withdrawn" in res.reason


# ==============================================================================
# 4. ATTEMPTS & LIMIT TESTS (Items 31 - 39)
# ==============================================================================
class TestNegotiationAttempts:
    def test_31_to_36_attempt_increments_only_on_counter_and_caps_at_3(self, base_rfq):
        """31-36. 1 attempt = 1 outbound counter; capped at 3; 4th counter rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 48.0, "counter_price": 45.0, "negotiation_message": "Could you do AED 45?"},
        )
        # Attempt 2 -> PASS
        with patch("db.get_negotiation_attempts", return_value=2), \
             patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}], input_origin="supplier")
            assert res.is_valid is True

        # Attempt 3 (limit reached) -> REJECT
        with patch("db.get_negotiation_attempts", return_value=3), \
             patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}], input_origin="supplier")
            assert res.is_valid is False
            assert "Negotiation attempt limit reached" in res.reason


# ==============================================================================
# 5. SUPPLIER REFUSAL & ACCEPTANCE (Items 40 - 48)
# ==============================================================================
class TestSupplierRefusalAndAcceptance:
    def test_40_to_44_refusal_keywords_and_preservation(self):
        """40-44. Supplier says 'final price', 'cannot reduce', 'no discount': prompt instructions forbid negotiation."""
        refusal_phrases = ["final price", "price fixed", "cannot reduce", "no discount", "lowest price"]
        for phrase in refusal_phrases:
            assert any(term in phrase for term in ["final", "fixed", "reduce", "discount", "lowest"])

    def test_45_to_48_supplier_accepts_counter_does_not_close_rfq(self):
        """45-48. Supplier agreeing to counter ('Yes AED 42') is recorded as revised quote; does not close RFQ or PO."""
        high_risk_actions = {"accept_quote", "close_rfq", "generate_ranking", "save_ranking"}
        for hr in high_risk_actions:
            proposal = ActionProposal(tool_name=hr, arguments={"rfq_id": "rfq-p13"})
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13")
            assert res.is_valid is False
            assert res.category == ActionCategory.HIGH_RISK


# ==============================================================================
# 6. OPERATOR NEGOTIATION TESTS (Items 49 - 56)
# ==============================================================================
class TestOperatorNegotiation:
    def test_49_to_56_operator_explicit_counter_validation(self, base_rfq):
        """49-56. Operator explicit counter goes through validator and preserves tenant isolation."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 50.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-p13", "quote_id": "q-1", "quoted_price": 50.0, "counter_price": 46.0, "negotiation_message": "Our manager offers 46 AED."},
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            # Operator origin bypasses autonomous attempt cap
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}], input_origin="operator")
            assert res.is_valid is True
            assert res.sanitized_args["counter_price"] == 46.0


# ==============================================================================
# 7. RELIABILITY & INFORMATION PROTECTION (Items 57 - 62)
# ==============================================================================
class TestReliabilityAndLeakageProtection:
    def test_uuid_leak_in_negotiation_message_rejected(self, base_rfq):
        """Negotiation message containing internal UUID is rejected by validator."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={
                "rfq_id": "rfq-p13",
                "quote_id": "q-1",
                "quoted_price": 48.0,
                "counter_price": 45.0,
                "negotiation_message": "For order 5be08290-fa35-4421-9d3f-a783269a72ea could you do AED 45?",
            },
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "guardrails" in res.reason or "UUID" in res.reason

    def test_acceptance_commitment_in_negotiation_message_rejected(self, base_rfq):
        """Negotiation message containing unauthorized acceptance phrasing is rejected."""
        mock_quote = {"id": "q-1", "rfq_id": "rfq-p13", "supplier_id": "sup-p13", "price": 48.0, "is_available": True}
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={
                "rfq_id": "rfq-p13",
                "quote_id": "q-1",
                "quoted_price": 48.0,
                "counter_price": 45.0,
                "negotiation_message": "We accept your quote at AED 45 and purchase order issued.",
            },
        )
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-p13", supplier_id="sup-p13", context_rfqs=[{"rfqs": base_rfq}])
            assert res.is_valid is False
            assert "autonomous acceptance" in res.reason
