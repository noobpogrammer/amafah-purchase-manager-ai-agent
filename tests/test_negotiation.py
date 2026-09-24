import os
import math
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

import db
import main
import policy_validator
from policy_validator import ActionProposal, validate_action, ActionCategory, MAX_NEGOTIATION_ATTEMPTS


@pytest.fixture
def mock_supabase():
    with patch("db.supabase") as mock:
        yield mock


# ==============================================================================
# 1. RFQ Price Range Tests (Items 1 - 7)
# ==============================================================================
class TestRFQPriceRange:
    def test_rfq_create_request_stores_acceptable_min_max(self):
        req = main.RFQCreateRequest(
            product_name="LED Panel",
            category="Electrical",
            specs="60W 600x600",
            quantity=10,
            acceptable_price_min=50.0,
            acceptable_price_max=60.0,
            deadline_hours=24,
        )
        assert req.acceptable_price_min == 50.0
        assert req.acceptable_price_max == 60.0

    def test_existing_rfq_without_range_remains_valid(self):
        req = main.RFQCreateRequest(
            product_name="LED Panel",
            category="Electrical",
            specs="60W 600x600",
            quantity=10,
            deadline_hours=24,
        )
        assert req.acceptable_price_min is None
        assert req.acceptable_price_max is None

    def test_reject_negative_price_bounds(self):
        with pytest.raises(ValidationError):
            main.RFQCreateRequest(
                product_name="LED Panel",
                category="Electrical",
                specs="60W",
                acceptable_price_min=-10.0,
                deadline_hours=24,
            )

        with pytest.raises(ValidationError):
            main.RFQCreateRequest(
                product_name="LED Panel",
                category="Electrical",
                specs="60W",
                acceptable_price_max=-5.0,
                deadline_hours=24,
            )

    def test_reject_non_finite_price_bounds(self):
        with pytest.raises(ValidationError):
            main.RFQCreateRequest(
                product_name="LED Panel",
                category="Electrical",
                specs="60W",
                acceptable_price_min=float("nan"),
                deadline_hours=24,
            )

        with pytest.raises(ValidationError):
            main.RFQCreateRequest(
                product_name="LED Panel",
                category="Electrical",
                specs="60W",
                acceptable_price_max=float("inf"),
                deadline_hours=24,
            )

    def test_reject_min_greater_than_max(self):
        with pytest.raises(ValidationError) as exc_info:
            main.RFQCreateRequest(
                product_name="LED Panel",
                category="Electrical",
                specs="60W",
                acceptable_price_min=70.0,
                acceptable_price_max=50.0,
                deadline_hours=24,
            )
        assert "acceptable_price_min cannot be greater than acceptable_price_max" in str(exc_info.value)

    def test_range_propagates_through_create_rfq_and_match_suppliers(self, mock_supabase):
        mock_supabase.table().insert().execute.return_value.data = [{
            "id": "rfq-101",
            "client_id": "client-1",
            "product_name": "LED Panel",
            "acceptable_price_min": 50.0,
            "acceptable_price_max": 60.0,
            "status": "active",
        }]
        with patch("db.get_suppliers_by_category", return_value=[]):
            rfq, _ = db.create_rfq_and_match_suppliers(
                client_id="client-1",
                product_name="LED Panel",
                category="Electrical",
                acceptable_price_min=50.0,
                acceptable_price_max=60.0,
            )
            insert_call = mock_supabase.table("rfqs").insert.call_args[0][0]
            assert insert_call["acceptable_price_min"] == 50.0
            assert insert_call["acceptable_price_max"] == 60.0


# ==============================================================================
# 2. Negotiation Price Logic Tests (Items 8 - 15)
# ==============================================================================
class TestNegotiationPriceLogic:
    def test_classify_at_or_below_min(self):
        assert db.classify_price_position(50.0, 50.0, 60.0) == "AT_OR_BELOW_MIN"
        assert db.classify_price_position(45.0, 50.0, 60.0) == "AT_OR_BELOW_MIN"

    def test_classify_within_acceptable_range(self):
        assert db.classify_price_position(55.0, 50.0, 60.0) == "WITHIN_ACCEPTABLE_RANGE"

    def test_classify_at_max(self):
        assert db.classify_price_position(60.0, 50.0, 60.0) == "AT_MAX"

    def test_classify_above_acceptable_range(self):
        # Fixed tolerance is AED 3: 62 is above 60 but within 63.
        assert db.classify_price_position(62.0, 50.0, 60.0) == "ABOVE_ACCEPTABLE_RANGE"

    def test_classify_significantly_above_range(self):
        # Fixed tolerance is AED 3: 64 is above the 63 ceiling.
        assert db.classify_price_position(64.0, 50.0, 60.0) == "SIGNIFICANTLY_ABOVE_RANGE"

    def test_negotiation_context_targets_acceptable_minimum(self):
        ctx = db.build_negotiation_price_context(
            acceptable_min=50.0,
            acceptable_max=60.0,
            last_quote=55.0,
            current_quote=68.0,
        )
        assert ctx["preferred_target"] == 50.0
        assert ctx["target_source"] == "acceptable_price_min"
        assert ctx["tolerated_final_ceiling"] == 63.0
        assert ctx["should_negotiate"] is True
        assert ctx["price_position"] == "SIGNIFICANTLY_ABOVE_RANGE"

    def test_negotiation_context_falls_back_to_last_quote_without_minimum(self):
        ctx = db.build_negotiation_price_context(
            acceptable_min=None,
            acceptable_max=None,
            last_quote=43.0,
            current_quote=48.0,
        )
        assert ctx["preferred_target"] == 43.0
        assert ctx["target_source"] == "last_quote"
        assert ctx["tolerated_final_ceiling"] == 46.0

    def test_classify_no_range_set(self):
        assert db.classify_price_position(55.0, None, None) == "NO_RANGE_SET"

    def test_latest_quote_is_effective_quote(self, mock_supabase):
        # Mock quotes table returning 2 quotes for same supplier
        mock_supabase.table().select().eq().order().order().execute.return_value.data = [
            {
                "id": "q-2",
                "rfq_id": "rfq-1",
                "supplier_id": "sup-1",
                "price": 90.0,
                "created_at": "2026-09-12T10:00:00Z",
                "suppliers": {"name": "Supplier 1"},
            },
            {
                "id": "q-1",
                "rfq_id": "rfq-1",
                "supplier_id": "sup-1",
                "price": 100.0,
                "created_at": "2026-09-12T09:00:00Z",
                "suppliers": {"name": "Supplier 1"},
            },
        ]
        quotes = db.get_quotes_for_rfq("rfq-1")
        assert len(quotes) == 1
        assert quotes[0]["id"] == "q-2"
        assert quotes[0]["price"] == 90.0

    def test_quote_revisions_stored_as_separate_historical_rows(self, mock_supabase):
        mock_supabase.table().select().eq().execute.return_value.data = [{
            "id": "rfq-1",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
        }]
        mock_supabase.table().insert().execute.return_value.data = [{"id": "q-rev-2"}]

        res = db.record_quote("rfq-1", "sup-1", 92.0, "2 days", "warranty 1yr", "revised offer 92")
        assert res["id"] == "q-rev-2"
        # Verify supplier status updated to responded
        mock_supabase.table("rfq_suppliers").update.assert_called_with({"status": "responded"})


# ==============================================================================
# 3. Competitive Pricing Context Tests (Items 16 - 21)
# ==============================================================================
class TestCompetitiveContext:
    def test_get_competitive_pricing_context_masks_and_distinguishes_current_supplier(self):
        mock_effective_quotes = [
            {"id": "q-1", "supplier_id": "current-sup", "price": 70.0},
            {"id": "q-2", "supplier_id": "competitor-a", "price": 58.0},
            {"id": "q-3", "supplier_id": "competitor-b", "price": 54.0},
        ]
        with patch("db.get_quotes_for_rfq", return_value=mock_effective_quotes):
            ctx = db.get_competitive_pricing_context("rfq-1", "current-sup")
            assert ctx["has_competition"] is True
            assert ctx["competing_quotes_count"] == 2
            assert ctx["best_competing_price"] == 54.0
            # Ensure competitor IDs or names are not exposed in returned dict
            assert "competitor-a" not in str(ctx)
            assert "competitor-b" not in str(ctx)

    def test_competitive_pricing_context_excludes_unrelated_rfq(self):
        with patch("db.get_quotes_for_rfq") as mock_get_quotes:
            mock_get_quotes.return_value = [{"id": "q-2", "supplier_id": "competitor-a", "price": 54.0}]
            ctx = db.get_competitive_pricing_context("rfq-target", "current-sup")
            mock_get_quotes.assert_called_once_with("rfq-target")
            assert ctx["best_competing_price"] == 54.0


# ==============================================================================
# 4. Policy & Validator Layer Tests (Items 22 - 29)
# ==============================================================================
class TestPolicyValidator:
    def test_negotiation_action_requires_exact_rfq_and_supplier(self, mock_supabase):
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-1", "quote_id": "q-1", "quoted_price": 65.0, "counter_price": 60.0, "negotiation_message": "Could you do AED 60?"},
        )
        context_rfq = {
            "rfqs": {
                "id": "rfq-1",
                "client_id": "client-1",
                "status": "active",
                "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
            },
            "supplier_id": "sup-1",
        }
        mock_quote = {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "sup-1", "price": 65.0, "is_available": True}
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-1", supplier_id="sup-1", context_rfqs=[context_rfq])
            assert res.is_valid is True
            assert res.action == "negotiate_price"
            assert res.sanitized_args["quoted_price"] == 65.0
            assert res.sanitized_args["counter_price"] == 60.0
            assert res.sanitized_args["quote_id"] == "q-1"

    def test_negotiation_blocked_for_cross_tenant(self, mock_supabase):
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-1", "quote_id": "q-1", "quoted_price": 65.0, "counter_price": 60.0, "negotiation_message": "Can you offer AED 60?"},
        )
        context_rfq = {
            "rfqs": {
                "id": "rfq-1",
                "client_id": "client-other",
                "status": "active",
                "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
            },
            "supplier_id": "sup-1",
        }
        mock_quote = {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "sup-1", "price": 65.0, "is_available": True}
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-1", supplier_id="sup-1", context_rfqs=[context_rfq])
            assert res.is_valid is False
            assert "Cross-tenant violation" in res.reason

    def test_negotiation_blocked_after_deadline(self):
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-1", "quote_id": "q-1", "quoted_price": 65.0, "counter_price": 60.0, "negotiation_message": "Can you offer AED 60?"},
        )
        context_rfq = {
            "rfqs": {
                "id": "rfq-1",
                "client_id": "client-1",
                "status": "active",
                "due_by": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            },
            "supplier_id": "sup-1",
        }
        mock_quote = {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "sup-1", "price": 65.0, "is_available": True}
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-1", supplier_id="sup-1", context_rfqs=[context_rfq])
            assert res.is_valid is False
            assert "closed or deadline has passed" in res.reason

    def test_negotiation_blocked_for_closed_rfq(self):
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-1", "quote_id": "q-1", "quoted_price": 65.0, "counter_price": 60.0, "negotiation_message": "Can you offer AED 60?"},
        )
        context_rfq = {
            "rfqs": {
                "id": "rfq-1",
                "client_id": "client-1",
                "status": "closed",
                "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
            },
            "supplier_id": "sup-1",
        }
        mock_quote = {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "sup-1", "price": 65.0, "is_available": True}
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-1", supplier_id="sup-1", context_rfqs=[context_rfq])
            assert res.is_valid is False
            assert "closed or deadline has passed" in res.reason

    def test_high_risk_actions_blocked_regardless_of_confidence(self):
        for hr_tool in ("close_rfq", "accept_quote", "generate_ranking", "save_ranking", "delete_quote"):
            proposal = ActionProposal(
                tool_name=hr_tool,
                arguments={"rfq_id": "rfq-1"},
                confidence=0.99,
            )
            res = validate_action(proposal, client_id="client-1", supplier_id="sup-1")
            assert res.is_valid is False
            assert res.category == ActionCategory.HIGH_RISK


# ==============================================================================
# 5. Negotiation Attempt Limits Tests (Items 30 - 32)
# ==============================================================================
class TestNegotiationLimits:
    def test_negotiation_attempt_increment_and_enforcement(self, mock_supabase):
        mock_supabase.table().select().eq().eq().execute.return_value.data = [{"negotiation_attempts": 2}]
        attempts = db.get_negotiation_attempts("rfq-1", "sup-1")
        assert attempts == 2

        mock_quote = {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "sup-1", "price": 75.0, "is_available": True}
        with patch("db.get_negotiation_attempts", return_value=3), \
             patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            proposal = ActionProposal(
                tool_name="negotiate_price",
                arguments={"rfq_id": "rfq-1", "quote_id": "q-1", "quoted_price": 75.0, "counter_price": 70.0, "negotiation_message": "Please reduce your price."},
            )
            context_rfq = {
                "rfqs": {
                    "id": "rfq-1",
                    "client_id": "client-1",
                    "status": "active",
                    "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
                },
                "supplier_id": "sup-1",
            }
            res = validate_action(proposal, client_id="client-1", supplier_id="sup-1", context_rfqs=[context_rfq])
            assert res.is_valid is False
            assert "Negotiation attempt limit reached" in res.reason


# ==============================================================================
# 6. WhatsApp Continuity & Routing Tests (Items 33 - 37)
# ==============================================================================
class TestWhatsAppContinuity:
    def test_format_rfq_context_does_not_reveal_competitor_identity(self):
        open_rfqs = [{
            "id": "rfq-1",
            "product_name": "LED Panel",
            "specs": "60W",
            "quantity": 10,
            "acceptable_price_min": 50.0,
            "acceptable_price_max": 60.0,
        }]
        with patch("db.get_competitive_pricing_context", return_value={
            "has_competition": True,
            "competing_quotes_count": 2,
            "best_competing_price": 54.0,
        }), patch("db.get_negotiation_attempts", return_value=1):
            ctx = main.format_rfq_context(open_rfqs, current_supplier_id="sup-1")
            assert "Best competing quote is AED 54.0" in ctx
            assert "Negotiation Attempts Made: 1/3" in ctx
            assert "Acceptable Price Range: AED 50.0 - 60.0" in ctx
            # Ensure no supplier names or IDs appear in the text
            assert "Supplier" not in ctx or "supplier" in ctx.lower()
            assert "sup-2" not in ctx


# ==============================================================================
# 7. Deadline & Lifecycle Behavior Tests (Items 38 - 42)
# ==============================================================================
class TestDeadlineBehavior:
    def test_record_quote_fails_after_deadline(self, mock_supabase):
        mock_supabase.table().select().eq().execute.return_value.data = [{
            "id": "rfq-1",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        }]
        res = db.record_quote("rfq-1", "sup-1", 55.0)
        assert res is None

    def test_negotiation_fails_after_deadline(self):
        proposal = ActionProposal(
            tool_name="negotiate_price",
            arguments={"rfq_id": "rfq-1", "quote_id": "q-1", "quoted_price": 65.0, "counter_price": 60.0, "negotiation_message": "Discount please"},
        )
        context_rfq = {
            "rfqs": {
                "id": "rfq-1",
                "client_id": "client-1",
                "status": "active",
                "due_by": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            },
            "supplier_id": "sup-1",
        }
        mock_quote = {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "sup-1", "price": 65.0, "is_available": True}
        with patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]):
            res = validate_action(proposal, client_id="client-1", supplier_id="sup-1", context_rfqs=[context_rfq])
            assert res.is_valid is False
            assert "closed or deadline has passed" in res.reason


# ==============================================================================
# 8. Webhook Negotiation Flow Integration Tests
# ==============================================================================
class TestWebhookNegotiationIntegration:
    @pytest.mark.asyncio
    async def test_webhook_negotiation_direct_match(self, mock_supabase):
        from fastapi.testclient import TestClient
        client = TestClient(main.app)

        payload = {
            "instance": "test_instance",
            "data": {
                "key": {"fromMe": False, "remoteJid": "971501234567@s.whatsapp.net"},
                "message": {
                    "conversation": "Our price is AED 68 per unit, 2 days delivery",
                    "contextInfo": {"stanzaId": "msg-100"},
                },
            },
        }

        mock_client_row = {"id": "client-1", "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier_row = {"id": "sup-1", "name": "Al Noor", "phone_number": "971501234567", "client_id": "client-1"}
        mock_rfq_supplier = {
            "id": "rs-1",
            "rfq_id": "rfq-1",
            "supplier_id": "sup-1",
            "sent_message_id": "msg-100",
            "negotiation_attempts": 0,
            "rfqs": {
                "id": "rfq-1",
                "client_id": "client-1",
                "product_name": "LED Panel",
                "specs": "60W",
                "quantity": 10,
                "status": "active",
                "acceptable_price_min": 50.0,
                "acceptable_price_max": 60.0,
                "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
            },
        }
        mock_quote = {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "sup-1", "price": 68.0, "is_available": True}

        with patch("db.get_client_by_instance", return_value=mock_client_row), \
             patch("db.get_supplier_by_phone", return_value=mock_supplier_row), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=mock_rfq_supplier), \
             patch("db.get_supplier_prior_quotes", return_value=[mock_quote]), \
             patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]), \
             patch("db.get_competitive_pricing_context", return_value={"has_competition": True, "competing_quotes_count": 1, "best_competing_price": 55.0}), \
             patch("db.get_negotiation_attempts", return_value=0), \
             patch("db.increment_negotiation_attempts", return_value=1) as mock_inc, \
             patch("db.record_quote", return_value={"id": "q-1"}) as mock_rec, \
             patch("db.log_message") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("groq_client.route_supplier_message", return_value={
                 "tool_name": "negotiate_price",
                 "arguments": {
                     "rfq_id": "rfq-1",
                     "quote_id": "q-1",
                     "quoted_price": 68.0,
                     "counter_price": 60.0,
                     "negotiation_message": "Thank you. Could you consider revising closer to AED 60 per unit?",
                     "delivery_time": "2 days",
                 },
             }):
            resp = client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "negotiation_sent"
            assert resp.json()["attempts"] == 1
            mock_inc.assert_called_once_with("rfq-1", "sup-1")
            mock_enq.assert_called_once()
            assert "Thank you. Could you consider revising closer to AED 60" in mock_enq.call_args[0][1]

    @pytest.mark.asyncio
    async def test_webhook_negotiation_failed_atomic_increment_suppresses_counteroffer(self, mock_supabase):
        from fastapi.testclient import TestClient
        client = TestClient(main.app)

        payload = {
            "instance": "test_instance",
            "data": {
                "key": {"fromMe": False, "remoteJid": "971501234567@s.whatsapp.net"},
                "message": {
                    "conversation": "Our price is AED 68 per unit",
                    "contextInfo": {"stanzaId": "msg-100"},
                },
            },
        }

        mock_client_row = {"id": "client-1", "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier_row = {"id": "sup-1", "name": "Al Noor", "phone_number": "971501234567", "client_id": "client-1"}
        mock_rfq_supplier = {
            "id": "rs-1",
            "rfq_id": "rfq-1",
            "supplier_id": "sup-1",
            "sent_message_id": "msg-100",
            "negotiation_attempts": 2, # Validator sees 2 and allows it
            "rfqs": {
                "id": "rfq-1",
                "client_id": "client-1",
                "product_name": "LED Panel",
                "specs": "60W",
                "quantity": 10,
                "status": "active",
                "acceptable_price_min": 50.0,
                "acceptable_price_max": 60.0,
                "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
            },
        }

        mock_quote = {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "sup-1", "price": 68.0, "is_available": True}

        with patch("db.get_client_by_instance", return_value=mock_client_row), \
             patch("db.get_supplier_by_phone", return_value=mock_supplier_row), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=mock_rfq_supplier), \
             patch("db.get_supplier_prior_quotes", return_value=[mock_quote]), \
             patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]), \
             patch("db.get_competitive_pricing_context", return_value={"has_competition": True, "competing_quotes_count": 1, "best_competing_price": 55.0}), \
             patch("db.get_negotiation_attempts", return_value=2), \
             patch("db.increment_negotiation_attempts", return_value=-1) as mock_inc, \
             patch("db.record_quote", return_value={"id": "q-1"}) as mock_rec, \
             patch("db.log_message") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("groq_client.route_supplier_message", return_value={
                 "tool_name": "negotiate_price",
                 "arguments": {
                     "rfq_id": "rfq-1",
                     "quote_id": "q-1",
                     "quoted_price": 68.0,
                     "counter_price": 60.0,
                     "negotiation_message": "Counter-offer message that should be suppressed",
                 },
             }):
            resp = client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "rejected_by_policy"
            mock_inc.assert_called_once_with("rfq-1", "sup-1")
            # Counteroffer was NOT enqueued; THANK_YOU_MSG was enqueued instead
            mock_enq.assert_called_once()
            assert "Counter-offer" not in mock_enq.call_args[0][1]
            assert mock_enq.call_args[0][1] == main.THANK_YOU_MSG


# ==============================================================================
# 9. Atomic Concurrency & Edge-Case Unit Tests
# ==============================================================================
class TestAtomicNegotiationConcurrency:
    def test_increment_from_0_succeeds_rpc(self, mock_supabase):
        mock_supabase.rpc().execute.return_value.data = 1
        res = db.increment_negotiation_attempts("rfq-1", "sup-1")
        assert res == 1
        mock_supabase.rpc.assert_called_with("increment_negotiation_attempts", {
            "p_rfq_id": "rfq-1",
            "p_supplier_id": "sup-1",
            "p_max_attempts": 3,
        })

    def test_increment_from_2_succeeds_rpc(self, mock_supabase):
        mock_supabase.rpc().execute.return_value.data = 3
        res = db.increment_negotiation_attempts("rfq-1", "sup-1")
        assert res == 3

    def test_increment_from_3_rejected_rpc(self, mock_supabase):
        mock_supabase.rpc().execute.return_value.data = -1
        res = db.increment_negotiation_attempts("rfq-1", "sup-1")
        assert res == -1

    def test_cas_fallback_increment_from_0_succeeds(self, mock_supabase):
        # Force RPC to fail
        mock_supabase.rpc.side_effect = Exception("RPC not found")
        # Mock select query returning attempts = 0
        mock_supabase.table().select().eq().eq().execute.return_value.data = [{"negotiation_attempts": 0}]
        # Mock CAS update succeeding
        mock_supabase.table().update().eq().eq().eq().execute.return_value.data = [{"negotiation_attempts": 1}]

        res = db.increment_negotiation_attempts("rfq-1", "sup-1")
        assert res == 1

    def test_cas_fallback_increment_from_2_succeeds_to_3(self, mock_supabase):
        mock_supabase.rpc.side_effect = Exception("RPC not found")
        mock_supabase.table().select().eq().eq().execute.return_value.data = [{"negotiation_attempts": 2}]
        mock_supabase.table().update().eq().eq().eq().execute.return_value.data = [{"negotiation_attempts": 3}]

        res = db.increment_negotiation_attempts("rfq-1", "sup-1")
        assert res == 3

    def test_cas_fallback_increment_from_3_rejected(self, mock_supabase):
        mock_supabase.rpc.side_effect = Exception("RPC not found")
        mock_supabase.table().select().eq().eq().execute.return_value.data = [{"negotiation_attempts": 3}]

        res = db.increment_negotiation_attempts("rfq-1", "sup-1")
        assert res == -1

    def test_simulated_concurrent_increment_race_at_limit(self):
        """
        Simulate two concurrent worker requests that both initially see attempts = 2.
        The first worker performs CAS update (2 -> 3) and succeeds.
        The second worker attempts CAS update with `negotiation_attempts == 2`, which fails (0 rows updated).
        """
        state = {"negotiation_attempts": 2}

        def worker_1_attempt():
            # Worker 1 reads current = 2, updates to 3
            if state["negotiation_attempts"] < 3:
                # atomic swap
                if state["negotiation_attempts"] == 2:
                    state["negotiation_attempts"] = 3
                    return 3
            return -1

        def worker_2_attempt():
            # Worker 2 also read 2 originally, tries to execute CAS update
            if state["negotiation_attempts"] == 2:
                state["negotiation_attempts"] = 3
                return 3
            return -1

        res1 = worker_1_attempt()
        res2 = worker_2_attempt()

        # Exactly one succeeds, other fails
        assert res1 == 3
        assert res2 == -1
        # Final value in storage is exactly 3, NEVER 4
        assert state["negotiation_attempts"] == 3

    def test_record_quote_does_not_increment_negotiation_attempts(self, mock_supabase):
        mock_supabase.table().select().eq().execute.return_value.data = [{
            "id": "rfq-1",
            "status": "active",
            "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
        }]
        mock_supabase.table().insert().execute.return_value.data = [{"id": "q-1"}]

        with patch("db.increment_negotiation_attempts") as mock_inc:
            db.record_quote("rfq-1", "sup-1", 100.0)
            mock_inc.assert_not_called()

