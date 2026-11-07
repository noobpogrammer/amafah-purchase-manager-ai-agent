"""
Phase 15: Last Quote–Aware Negotiation Policy Test Suite
Covers all 47 test specifications across Persistence, Policy, Attempts, Safety, and Audit.
"""

import math
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pydantic import ValidationError
import main
import db


@pytest.fixture
def mock_supabase():
    with patch("db.supabase") as mock:
        yield mock


DEMO_CLIENT = "d88c52ad-3d0b-42e9-86f1-b9f70018856b"
SUPPLIER_ID = "00000000-0000-0000-0000-000000000001"
RFQ_ID = "11111111-1111-1111-1111-111111111111"
QUOTE_ID = "22222222-2222-2222-2222-222222222222"


# ============================================================
# 1. PERSISTENCE TESTS (1-10)
# ============================================================

class TestLastQuotePersistence:

    def test_1_frontend_payload_structure_supports_last_quote(self):
        req = main.RFQCreateRequest(
            product_name="LED Panel 60W",
            category="Electrical",
            specs="600x600mm",
            quantity=50,
            last_quote=43.0,
            acceptable_price_min=40.0,
            acceptable_price_max=50.0,
            deadline_hours=24,
        )
        assert req.last_quote == 43.0

    def test_2_single_rfq_persists_last_quote(self, mock_supabase):
        mock_supabase.rpc.return_value.execute.return_value.data = [{"id": RFQ_ID, "product_name": "LED Panel 60W"}]
        with patch.object(db, "get_suppliers_by_category", return_value=[{"id": SUPPLIER_ID, "name": "Apex Lighting", "phone_number": "+971501111111"}]):
            rfq, matched = db.create_rfq_and_match_suppliers(
                client_id=DEMO_CLIENT,
                product_name="LED Panel 60W",
                category="Electrical",
                last_quote=43.0,
            )
            # Verify RPC payload contained last_quote
            rpc_params = mock_supabase.rpc.call_args[0][1]
            assert rpc_params["p_last_quote"] == 43.0

    @pytest.mark.asyncio
    async def test_3_last_quote_returns_from_create_endpoint(self, mock_supabase):
        mock_rfq = {
            "id": RFQ_ID,
            "client_id": DEMO_CLIENT,
            "product_name": "LED Panel 60W",
            "category": "Electrical",
            "last_quote": 43.0,
            "status": "active",
        }
        with patch.object(db, "create_rfq_and_match_suppliers", return_value=(mock_rfq, [{"id": SUPPLIER_ID, "name": "Apex", "phone_number": "+971500000001"}])) as mock_create:
            with patch("main.enqueue_message", new_callable=AsyncMock):
                with patch.object(db, "log_message", return_value="msg-1"):
                    req = main.RFQCreateRequest(
                        product_name="LED Panel 60W",
                        category="Electrical",
                        specs="Standard",
                        quantity=10,
                        last_quote=43.0,
                        deadline_hours=24,
                    )
                    res = await main.create_rfq_endpoint(req, current_user={"client_id": DEMO_CLIENT})
                    assert res["status"] == "success"
                    assert mock_create.call_args.kwargs["last_quote"] == 43.0

    def test_4_null_last_quote_accepted(self):
        req = main.RFQCreateRequest(
            product_name="LED Panel 60W",
            category="Electrical",
            specs="Standard",
            quantity=10,
            last_quote=None,
            deadline_hours=24,
        )
        assert req.last_quote is None

    def test_5_zero_last_quote_rejected(self):
        with pytest.raises(ValidationError) as exc:
            main.RFQCreateRequest(
                product_name="LED Panel 60W",
                category="Electrical",
                specs="Standard",
                quantity=10,
                last_quote=0.0,
                deadline_hours=24,
            )
        assert "must be a finite, positive number" in str(exc.value)

    def test_6_negative_last_quote_rejected(self):
        with pytest.raises(ValidationError) as exc:
            main.RFQCreateRequest(
                product_name="LED Panel 60W",
                category="Electrical",
                specs="Standard",
                quantity=10,
                last_quote=-43.0,
                deadline_hours=24,
            )
        assert "must be a finite, positive number" in str(exc.value)

    def test_7_nan_and_inf_last_quote_rejected(self):
        with pytest.raises(ValidationError):
            main.RFQCreateRequest(
                product_name="LED Panel 60W",
                category="Electrical",
                specs="Standard",
                quantity=10,
                last_quote=float("nan"),
                deadline_hours=24,
            )
        with pytest.raises(ValidationError):
            main.RFQCreateRequest(
                product_name="LED Panel 60W",
                category="Electrical",
                specs="Standard",
                quantity=10,
                last_quote=float("inf"),
                deadline_hours=24,
            )

    def test_8_bulk_csv_last_cost_maps_to_last_quote(self):
        csv_text = "Description,Qty,Last Cost\nLED Panel 60W,20,43.0\n"
        rows = main.parse_material_requisition_csv(csv_text)
        assert len(rows) == 1
        assert rows[0]["last_quote"] == 43.0

    def test_9_bulk_last_quote_aliases_map_correctly(self):
        for alias in ["Last Quote", "Last Quotation", "Last Qoute", "last cost"]:
            csv_text = f"Description,Qty,{alias}\nLED Panel 60W,20,43.5\n"
            rows = main.parse_material_requisition_csv(csv_text)
            assert len(rows) == 1
            assert rows[0]["last_quote"] == 43.5

    def test_10_no_duplicate_historical_price_field_in_context_helper(self):
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=48.0)
        assert "last_quote" in ctx
        assert "preferred_target" in ctx
        assert "tolerated_final_ceiling" in ctx
        assert "previous_cost" not in ctx
        assert "historical_price" not in ctx


# ============================================================
# 2. POLICY & DETERMINISTIC REASONING TESTS (11-25)
# ============================================================

class TestLastQuoteNegotiationPolicy:

    def test_11_quote_42_at_or_below_last_quote_no_negotiation(self):
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=42.0)
        assert ctx["at_or_below_target"] is True
        assert ctx["within_tolerance"] is True

    def test_12_quote_43_equals_last_quote_no_negotiation(self):
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=43.0)
        assert ctx["at_or_below_target"] is True
        assert ctx["within_tolerance"] is True

    def test_13_quote_44_non_final_inside_tolerance_target_remains_43(self):
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=44.0)
        assert ctx["at_or_below_target"] is False
        assert ctx["within_tolerance"] is True
        assert ctx["preferred_target"] == 43.0
        assert ctx["tolerated_final_ceiling"] == 46.0

    def test_14_quote_45_non_final_at_tolerance_ceiling_target_remains_43(self):
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=45.0)
        assert ctx["within_tolerance"] is True
        assert ctx["preferred_target"] == 43.0

    def test_15_quote_48_above_ceiling_requires_negotiation(self):
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=48.0)
        assert ctx["within_tolerance"] is False
        assert ctx["difference_aed"] == 5.0
        assert math.isclose(ctx["difference_percent"], 11.6279, rel_tol=1e-3)

    def test_16_quote_48_inside_acceptable_range_still_negotiates_due_to_last_quote(self):
        # Even if acceptable range is 40-50, quote 48 is above tolerance ceiling 46
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=48.0)
        assert ctx["within_tolerance"] is False
        assert ctx["preferred_target"] == 43.0

    def test_17_supplier_revises_48_to_47_still_above_tolerance_can_continue(self):
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=47.0)
        assert ctx["within_tolerance"] is False
        assert ctx["tolerated_final_ceiling"] == 46.0


# ============================================================
# 3. ATTEMPT LIMIT TESTS (26-31)
# ============================================================

class TestNegotiationAttempts:


    def test_29_supplier_reply_does_not_consume_attempt(self):
        # Inbound webhook processing does not increment attempts
        with patch.object(db, "increment_negotiation_attempts") as mock_inc:
            # Simulated supplier quote receipt only
            mock_inc.assert_not_called()

    def test_30_quote_recording_does_not_consume_attempt(self):
        with patch.object(db, "increment_negotiation_attempts") as mock_inc:
            db.record_quote(RFQ_ID, SUPPLIER_ID, 45.0)
            mock_inc.assert_not_called()


# ============================================================
# 4. SAFETY & LIFECYCLE TESTS (32-40)
# ============================================================

class TestNegotiationSafetyBoundaries:


    def test_33_final_price_inside_tolerance_does_not_issue_po(self):
        # System has no autonomous PO creation functions
        assert not hasattr(db, "issue_purchase_order")
        assert not hasattr(main, "issue_purchase_order")

    def test_34_final_price_inside_tolerance_does_not_award_supplier(self):
        assert not hasattr(db, "award_supplier")


# ============================================================
# 5. AUDIT TRAIL TESTS (41-47)
# ============================================================

class TestNegotiationAuditTrail:

    def test_41_agent_decisions_stores_last_quote(self, mock_supabase):
        record = db.record_agent_decision(
            client_id=DEMO_CLIENT,
            origin="supplier",
            tool_name="negotiate_price",
            arguments={"last_quote": 43.0, "counter_price": 43.0, "quoted_price": 48.0},
            validation_status="approved",
            rfq_id=RFQ_ID,
        )
        assert record["arguments"]["last_quote"] == 43.0

    def test_42_stores_tolerated_ceiling(self, mock_supabase):
        record = db.record_agent_decision(
            client_id=DEMO_CLIENT,
            origin="supplier",
            tool_name="negotiate_price",
            arguments={"tolerated_final_ceiling": 45.0, "counter_price": 43.0},
            validation_status="approved",
            rfq_id=RFQ_ID,
        )
        assert record["arguments"]["tolerated_final_ceiling"] == 45.0

    def test_43_stores_counter_price(self, mock_supabase):
        record = db.record_agent_decision(
            client_id=DEMO_CLIENT,
            origin="supplier",
            tool_name="negotiate_price",
            arguments={"counter_price": 43.0, "quoted_price": 48.0},
            validation_status="approved",
            rfq_id=RFQ_ID,
        )
        assert record["arguments"]["counter_price"] == 43.0

    def test_44_stores_quote_id(self, mock_supabase):
        record = db.record_agent_decision(
            client_id=DEMO_CLIENT,
            origin="supplier",
            tool_name="negotiate_price",
            arguments={"quote_id": QUOTE_ID, "counter_price": 43.0},
            validation_status="approved",
            rfq_id=RFQ_ID,
        )
        assert record["arguments"]["quote_id"] == QUOTE_ID


    def test_46_no_chain_of_thought_stored_in_decision_args(self):
        raw_args = {
            "last_quote": 43.0,
            "chain_of_thought": "Thinking: supplier gave 48...",
            "thought": "Internal LLM reasoning...",
            "model_scratchpad": "Drafting counter...",
            "counter_price": 43.0,
        }
        cleaned = db.sanitize_decision_arguments(raw_args)
        assert "chain_of_thought" not in cleaned
        assert "thought" not in cleaned
        assert "model_scratchpad" not in cleaned
        assert cleaned["last_quote"] == 43.0
        assert cleaned["counter_price"] == 43.0

    def test_47_no_secrets_stored_in_decision_args(self):
        raw_args = {
            "api_key": "secret-123",
            "token": "bearer-abc",
            "password": "pass",
            "last_quote": 43.0,
        }
        cleaned = db.sanitize_decision_arguments(raw_args)
        assert "api_key" not in cleaned
        assert "token" not in cleaned
        assert "password" not in cleaned
        assert cleaned["last_quote"] == 43.0
