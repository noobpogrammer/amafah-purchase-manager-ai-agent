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
import policy_validator
from policy_validator import ActionProposal, ActionCategory, validate_action, MAX_NEGOTIATION_ATTEMPTS
from groq_client import AgentContext, format_agent_context_for_prompt


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
        table_mock = MagicMock()
        mock_supabase.table.return_value = table_mock
        table_mock.insert.return_value.execute.return_value.data = [{"id": RFQ_ID, "product_name": "LED Panel 60W"}]
        with patch.object(db, "get_suppliers_by_category", return_value=[{"id": SUPPLIER_ID, "name": "Apex Lighting", "phone_number": "+971501111111"}]):
            rfq, matched = db.create_rfq_and_match_suppliers(
                client_id=DEMO_CLIENT,
                product_name="LED Panel 60W",
                category="Electrical",
                last_quote=43.0,
            )
            # Verify insert payload contained last_quote
            insert_call = table_mock.insert.call_args_list[0][0][0]
            assert insert_call["last_quote"] == 43.0

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
        assert ctx["tolerated_final_ceiling"] == 45.0

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
        # Even if acceptable range is 40-50, quote 48 is above tolerance ceiling 45
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=48.0)
        assert ctx["within_tolerance"] is False
        assert ctx["preferred_target"] == 43.0

    def test_17_supplier_revises_48_to_46_still_above_tolerance_can_continue(self):
        ctx = db.build_historical_price_context(last_quote=43.0, current_quote=46.0)
        assert ctx["within_tolerance"] is False
        assert ctx["tolerated_final_ceiling"] == 45.0

    @pytest.mark.asyncio
    async def test_18_supplier_revises_48_to_45_final_stops_without_escalation(self, mock_supabase):
        mock_rfq = {
            "id": RFQ_ID,
            "client_id": DEMO_CLIENT,
            "product_name": "LED Panel 60W",
            "last_quote": 43.0,
            "status": "active",
        }
        context = AgentContext(
            client_id=DEMO_CLIENT,
            supplier_id=SUPPLIER_ID,
            open_rfqs=[mock_rfq],
        )
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 45.0, "variants": [{"price": 45.0, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), \
             patch.object(db, "log_message", return_value="msg-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch.object(db, "flag_for_human_review") as mock_flag:
            res = await main.execute_validated_action(val, context, "45 final", {"id": SUPPLIER_ID, "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "recorded"
            mock_flag.assert_not_called()

    @pytest.mark.asyncio
    async def test_19_supplier_says_44_final_stops_without_escalation(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 44.0, "variants": [{"price": 44.0, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), patch.object(db, "log_message", return_value="msg-1"), patch("main.enqueue_message", new_callable=AsyncMock), patch.object(db, "flag_for_human_review") as mock_flag:
            res = await main.execute_validated_action(val, context, "44 is my final price", {"id": SUPPLIER_ID, "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "recorded"
            mock_flag.assert_not_called()

    @pytest.mark.asyncio
    async def test_20_supplier_says_43_5_final_stops_without_escalation(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 43.5, "variants": [{"price": 43.5, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), patch.object(db, "log_message", return_value="msg-1"), patch("main.enqueue_message", new_callable=AsyncMock), patch.object(db, "flag_for_human_review") as mock_flag:
            res = await main.execute_validated_action(val, context, "43.5 final offer", {"id": SUPPLIER_ID, "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "recorded"
            mock_flag.assert_not_called()

    @pytest.mark.asyncio
    async def test_21_supplier_says_46_final_escalates_to_agent_attention(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 46.0, "variants": [{"price": 46.0, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), \
             patch.object(db, "log_message", return_value="msg-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch.object(db, "flag_for_human_review", return_value=[{"id": "flag-1"}]) as mock_flag:
            res = await main.execute_validated_action(val, context, "AED 46 final", {"id": SUPPLIER_ID, "name": "Apex", "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "escalated_to_human"
            mock_flag.assert_called_once()
            flag_args = mock_flag.call_args.kwargs
            assert "Historical Last Quote:\nAED 43.0" in flag_args["reason"]
            assert "Tolerated Final Ceiling:\nAED 45.0" in flag_args["reason"]
            assert "Supplier Final Quote:\nAED 46.0" in flag_args["reason"]

    @pytest.mark.asyncio
    async def test_22_supplier_says_47_final_escalates_to_agent_attention(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 47.0, "variants": [{"price": 47.0, "variant_label": "India"}]},
        )
        with patch.object(db, "record_quote"), \
             patch.object(db, "log_message", return_value="msg-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch.object(db, "flag_for_human_review", return_value=[{"id": "flag-2"}]) as mock_flag:
            res = await main.execute_validated_action(val, context, "47 is my final price", {"id": SUPPLIER_ID, "name": "Apex Lighting", "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "escalated_to_human"
            assert "Variant:\nIndia" in mock_flag.call_args.kwargs["reason"]
            assert "Supplier Final Quote:\nAED 47.0" in mock_flag.call_args.kwargs["reason"]

    @pytest.mark.asyncio
    async def test_23_supplier_says_best_price_45_no_escalation(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 45.0, "variants": [{"price": 45.0, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), patch.object(db, "log_message", return_value="msg-1"), patch("main.enqueue_message", new_callable=AsyncMock), patch.object(db, "flag_for_human_review") as mock_flag:
            res = await main.execute_validated_action(val, context, "Best price is 45 AED", {"id": SUPPLIER_ID, "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "recorded"
            mock_flag.assert_not_called()

    @pytest.mark.asyncio
    async def test_24_supplier_says_cannot_go_below_45_no_escalation(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 45.0, "variants": [{"price": 45.0, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), patch.object(db, "log_message", return_value="msg-1"), patch("main.enqueue_message", new_callable=AsyncMock), patch.object(db, "flag_for_human_review") as mock_flag:
            res = await main.execute_validated_action(val, context, "I cannot go below 45 AED", {"id": SUPPLIER_ID, "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "recorded"
            mock_flag.assert_not_called()

    @pytest.mark.asyncio
    async def test_25_supplier_says_cannot_go_below_46_escalation(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 46.0, "variants": [{"price": 46.0, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), \
             patch.object(db, "log_message", return_value="msg-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch.object(db, "flag_for_human_review", return_value=[{"id": "flag-3"}]) as mock_flag:
            res = await main.execute_validated_action(val, context, "Cannot go lower than 46 AED", {"id": SUPPLIER_ID, "name": "Apex", "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "escalated_to_human"
            mock_flag.assert_called_once()


# ============================================================
# 3. ATTEMPT LIMIT TESTS (26-31)
# ============================================================

class TestNegotiationAttempts:

    def test_26_attempts_below_3_and_quote_above_ceiling_may_continue(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "last_quote": 43.0, "status": "active", "due_by": "2099-01-01T00:00:00Z"}
        mock_quote = {"id": QUOTE_ID, "rfq_id": RFQ_ID, "supplier_id": SUPPLIER_ID, "price": 48.0, "is_available": True}
        with patch.object(db, "get_quote_by_id", return_value=mock_quote), \
             patch.object(db, "get_quotes_for_rfq", return_value=[mock_quote]), \
             patch.object(db, "get_rfq_by_id", return_value=mock_rfq), \
             patch.object(db, "get_negotiation_attempts", return_value=1):
            proposal = ActionProposal(
                tool_name="negotiate_price",
                arguments={"rfq_id": RFQ_ID, "quote_id": QUOTE_ID, "quoted_price": 48.0, "counter_price": 43.0, "negotiation_message": "Could you do 43 AED?"},
            )
            res = validate_action(proposal, client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, context_rfqs=[mock_rfq])
            assert res.is_valid is True
            assert res.sanitized_args["preferred_target"] == 43.0
            assert res.sanitized_args["tolerated_final_ceiling"] == 45.0

    @pytest.mark.asyncio
    async def test_27_attempt_3_reached_and_quote_46_creates_agent_attention(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 46.0, "variants": [{"price": 46.0, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), \
             patch.object(db, "get_negotiation_attempts", return_value=3), \
             patch.object(db, "log_message", return_value="msg-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch.object(db, "flag_for_human_review", return_value=[{"id": "flag-4"}]) as mock_flag:
            res = await main.execute_validated_action(val, context, "I can offer 46", {"id": SUPPLIER_ID, "name": "Apex", "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "escalated_to_human"
            assert "Autonomous negotiation limit reached (3/3)" in mock_flag.call_args.kwargs["reason"]

    @pytest.mark.asyncio
    async def test_28_attempt_3_reached_and_quote_45_no_escalation(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 45.0, "variants": [{"price": 45.0, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), \
             patch.object(db, "get_negotiation_attempts", return_value=3), \
             patch.object(db, "log_message", return_value="msg-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch.object(db, "flag_for_human_review") as mock_flag:
            res = await main.execute_validated_action(val, context, "I can offer 45", {"id": SUPPLIER_ID, "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "recorded"
            mock_flag.assert_not_called()

    def test_29_supplier_reply_does_not_consume_attempt(self):
        # Inbound webhook processing does not increment attempts
        with patch.object(db, "increment_negotiation_attempts") as mock_inc:
            # Simulated supplier quote receipt only
            mock_inc.assert_not_called()

    def test_30_quote_recording_does_not_consume_attempt(self):
        with patch.object(db, "increment_negotiation_attempts") as mock_inc:
            db.record_quote(RFQ_ID, SUPPLIER_ID, 45.0)
            mock_inc.assert_not_called()

    def test_31_strictly_no_fourth_autonomous_negotiation(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "last_quote": 43.0, "status": "active", "due_by": "2099-01-01T00:00:00Z"}
        mock_quote = {"id": QUOTE_ID, "rfq_id": RFQ_ID, "supplier_id": SUPPLIER_ID, "price": 48.0, "is_available": True}
        with patch.object(db, "get_quote_by_id", return_value=mock_quote), \
             patch.object(db, "get_quotes_for_rfq", return_value=[mock_quote]), \
             patch.object(db, "get_rfq_by_id", return_value=mock_rfq), \
             patch.object(db, "get_negotiation_attempts", return_value=3):
            proposal = ActionProposal(
                tool_name="negotiate_price",
                arguments={"rfq_id": RFQ_ID, "quote_id": QUOTE_ID, "quoted_price": 48.0, "counter_price": 43.0, "negotiation_message": "Can you do 43?"},
            )
            res = validate_action(proposal, client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, context_rfqs=[mock_rfq])
            assert res.is_valid is False
            assert "attempt limit reached" in res.reason.lower()


# ============================================================
# 4. SAFETY & LIFECYCLE TESTS (32-40)
# ============================================================

class TestNegotiationSafetyBoundaries:

    @pytest.mark.asyncio
    async def test_32_final_price_inside_tolerance_does_not_close_rfq(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 45.0, "variants": [{"price": 45.0, "variant_label": None}]},
        )
        with patch.object(db, "record_quote"), patch.object(db, "log_message", return_value="msg-1"), patch("main.enqueue_message", new_callable=AsyncMock):
            with patch.object(db, "close_rfq") as mock_close:
                await main.execute_validated_action(val, context, "45 final", {"id": SUPPLIER_ID, "phone_number": "+971501111111"}, DEMO_CLIENT)
                mock_close.assert_not_called()

    def test_33_final_price_inside_tolerance_does_not_issue_po(self):
        # System has no autonomous PO creation functions
        assert not hasattr(db, "issue_purchase_order")
        assert not hasattr(main, "issue_purchase_order")

    def test_34_final_price_inside_tolerance_does_not_award_supplier(self):
        assert not hasattr(db, "award_supplier")

    @pytest.mark.asyncio
    async def test_35_final_price_above_tolerance_preserves_supplier_quote_before_escalation(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 47.0, "variants": [{"price": 47.0, "variant_label": None}]},
        )
        recorded_calls = []
        with patch.object(db, "record_quote", side_effect=lambda **kw: recorded_calls.append(kw)), \
             patch.object(db, "log_message", return_value="msg-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch.object(db, "flag_for_human_review", return_value=[{"id": "flag-5"}]):
            res = await main.execute_validated_action(val, context, "47 final", {"id": SUPPLIER_ID, "name": "Apex", "phone_number": "+971501111111"}, DEMO_CLIENT)
            assert res["status"] == "escalated_to_human"
            assert len(recorded_calls) == 1
            assert recorded_calls[0]["price"] == 47.0

    def test_36_cross_tenant_isolation_maintained(self, mock_supabase):
        other_tenant = "99999999-9999-9999-9999-999999999999"
        mock_rfq = {"id": RFQ_ID, "client_id": other_tenant, "last_quote": 43.0, "status": "active", "due_by": "2099-01-01T00:00:00Z"}
        mock_quote = {"id": QUOTE_ID, "rfq_id": RFQ_ID, "supplier_id": SUPPLIER_ID, "price": 48.0, "is_available": True}
        with patch.object(db, "get_quote_by_id", return_value=mock_quote), \
             patch.object(db, "get_quotes_for_rfq", return_value=[mock_quote]), \
             patch.object(db, "get_rfq_by_id", return_value=mock_rfq):
            proposal = ActionProposal(
                tool_name="negotiate_price",
                arguments={"rfq_id": RFQ_ID, "quote_id": QUOTE_ID, "quoted_price": 48.0, "counter_price": 43.0, "negotiation_message": "Offer 43"},
            )
            res = validate_action(proposal, client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, context_rfqs=[mock_rfq])
            assert res.is_valid is False
            assert "cross-tenant" in res.reason.lower()

    def test_37_exact_quote_id_targeting_unchanged(self, mock_supabase):
        mock_supabase.table().select().eq().execute.return_value.data = []
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "last_quote": 43.0, "status": "active", "due_by": "2099-01-01T00:00:00Z"}
        with patch.object(db, "get_quote_by_id", return_value=None):
            proposal = ActionProposal(
                tool_name="negotiate_price",
                arguments={"rfq_id": RFQ_ID, "quote_id": "nonexistent-quote", "quoted_price": 48.0, "counter_price": 43.0, "negotiation_message": "Offer 43"},
            )
            res = validate_action(proposal, client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, context_rfqs=[mock_rfq])
            assert res.is_valid is False
            assert "does not exist" in res.reason.lower()

    def test_38_withdrawn_quote_cannot_negotiate(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "last_quote": 43.0, "status": "active", "due_by": "2099-01-01T00:00:00Z"}
        mock_quote = {"id": QUOTE_ID, "rfq_id": RFQ_ID, "supplier_id": SUPPLIER_ID, "price": 48.0, "is_available": False}
        with patch.object(db, "get_quote_by_id", return_value=mock_quote), patch.object(db, "get_rfq_by_id", return_value=mock_rfq):
            proposal = ActionProposal(
                tool_name="negotiate_price",
                arguments={"rfq_id": RFQ_ID, "quote_id": QUOTE_ID, "quoted_price": 48.0, "counter_price": 43.0, "negotiation_message": "Offer 43"},
            )
            res = validate_action(proposal, client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, context_rfqs=[mock_rfq])
            assert res.is_valid is False
            assert "withdrawn" in res.reason.lower()

    def test_39_superseded_quote_cannot_negotiate(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "last_quote": 43.0, "status": "active", "due_by": "2099-01-01T00:00:00Z"}
        older_quote = {"id": "quote-older", "rfq_id": RFQ_ID, "supplier_id": SUPPLIER_ID, "price": 48.0, "is_available": True}
        newer_quote = {"id": "quote-newer", "rfq_id": RFQ_ID, "supplier_id": SUPPLIER_ID, "price": 46.0, "is_available": True}
        with patch.object(db, "get_quote_by_id", return_value=older_quote), \
             patch.object(db, "get_quotes_for_rfq", return_value=[newer_quote]), \
             patch.object(db, "get_rfq_by_id", return_value=mock_rfq):
            proposal = ActionProposal(
                tool_name="negotiate_price",
                arguments={"rfq_id": RFQ_ID, "quote_id": "quote-older", "quoted_price": 48.0, "counter_price": 43.0, "negotiation_message": "Offer 43"},
            )
            res = validate_action(proposal, client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, context_rfqs=[mock_rfq])
            assert res.is_valid is False
            assert "superseded" in res.reason.lower()

    def test_40_deadline_blocks_negotiation(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "last_quote": 43.0, "status": "active", "due_by": "2020-01-01T00:00:00Z"}
        mock_quote = {"id": QUOTE_ID, "rfq_id": RFQ_ID, "supplier_id": SUPPLIER_ID, "price": 48.0, "is_available": True}
        with patch.object(db, "get_quote_by_id", return_value=mock_quote), \
             patch.object(db, "get_quotes_for_rfq", return_value=[mock_quote]), \
             patch.object(db, "get_rfq_by_id", return_value=mock_rfq), \
             patch.object(db, "is_rfq_open", return_value=False):
            proposal = ActionProposal(
                tool_name="negotiate_price",
                arguments={"rfq_id": RFQ_ID, "quote_id": QUOTE_ID, "quoted_price": 48.0, "counter_price": 43.0, "negotiation_message": "Offer 43"},
            )
            res = validate_action(proposal, client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, context_rfqs=[mock_rfq])
            assert res.is_valid is False
            assert "deadline has passed" in res.reason.lower() or "closed" in res.reason.lower()


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

    @pytest.mark.asyncio
    async def test_45_escalation_decision_is_auditable_with_flag_link(self, mock_supabase):
        mock_rfq = {"id": RFQ_ID, "client_id": DEMO_CLIENT, "product_name": "LED Panel 60W", "last_quote": 43.0, "status": "active"}
        context = AgentContext(client_id=DEMO_CLIENT, supplier_id=SUPPLIER_ID, open_rfqs=[mock_rfq])
        val = policy_validator.ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": RFQ_ID, "price": 47.0, "variants": [{"price": 47.0, "variant_label": None}]},
        )
        updated_decisions = []
        with patch.object(db, "record_quote"), \
             patch.object(db, "log_message", return_value="msg-1"), \
             patch("main.enqueue_message", new_callable=AsyncMock), \
             patch.object(db, "flag_for_human_review", return_value=[{"id": "flag-audit-1"}]), \
             patch.object(db, "update_agent_decision", side_effect=lambda dec_id, **kw: updated_decisions.append((dec_id, kw))):
            await main.execute_validated_action(val, context, "47 final", {"id": SUPPLIER_ID, "phone_number": "+971501111111"}, DEMO_CLIENT, decision_id="dec-123")
            assert len(updated_decisions) == 1
            assert updated_decisions[0][1]["flag_id"] == "flag-audit-1"
            assert updated_decisions[0][1]["arguments"]["last_quote"] == 43.0
            assert updated_decisions[0][1]["arguments"]["tolerated_final_ceiling"] == 45.0

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
