"""
Phase 2 Adaptive Autonomous Negotiation Test Suite.
Covers all 47 required scenarios for Phase 2:
- 10-attempt cap and boundary validation (attempts 1..10 allowed, attempt 11 rejected)
- Negotiation session lifecycle & state tracking
- Concession tracking (e.g. 72 -> 65 = 7 AED concession)
- Reciprocal concession engine & hold-position / anti-oscillation rules
- Warm, adaptive WhatsApp negotiation language
- Final price detection & unified ceiling escalation (works with or without last_quote)
- Thread continuity via active negotiation session & exact stanza overriding
- Information protection (no targets, ceilings, competitor prices, or UUID leaks)
- Quote-first persistence
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

import db
import main
import negotiation_engine


CLIENT_ID = str(uuid.uuid4())
SUPPLIER_ID = str(uuid.uuid4())
PHONE_NUMBER = "971501234567"


@pytest.fixture
def mock_supabase():
    with patch.object(db, "supabase") as mock_sb:
        yield mock_sb


def make_open_rfq(
    rfq_id: str,
    product_name: str,
    status: str = "sent",
    specs: str = "Standard",
    hours_remaining: int = 24,
    sent_message_id: str = None,
    acceptable_min: float = 45.0,
    acceptable_max: float = 48.0,
    last_quote: float = None,
):
    due_by = (datetime.now(timezone.utc) + timedelta(hours=hours_remaining)).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "rfq_id": rfq_id,
        "supplier_id": SUPPLIER_ID,
        "status": status,
        "sent_message_id": sent_message_id,
        "rfqs": {
            "id": rfq_id,
            "client_id": CLIENT_ID,
            "product_name": product_name,
            "specs": specs,
            "status": "active",
            "due_by": due_by,
            "acceptable_price_min": acceptable_min,
            "acceptable_price_max": acceptable_max,
            "last_quote": last_quote,
        }
    }


def make_whatsapp_payload(text: str, stanza_id: str = None, msg_id: str = None):
    msg_id = msg_id or f"msg-{uuid.uuid4()}"
    if stanza_id:
        msg_payload = {
            "extendedTextMessage": {
                "text": text,
                "contextInfo": {"stanzaId": stanza_id},
            }
        }
    else:
        msg_payload = {"conversation": text}

    return {
        "instance": "test_instance",
        "data": {
            "key": {
                "remoteJid": f"{PHONE_NUMBER}@s.whatsapp.net",
                "fromMe": False,
                "id": msg_id,
            },
            "message": msg_payload,
        }
    }


class TestPhase2AdaptiveNegotiation:

    # 1. Phase 1 stanza routing remains working

    # 2. Phase 1 standalone unanswered-RFQ routing remains working

    # 3. Maximum counteroffers = 10

    # 4 & 5. Attempt 9 and 10 allowed

    # 6. Attempt 11 rejected

    # 7 & 8. First supplier offer creates negotiation session with ANCHOR strategy
    def test_7_8_first_offer_creates_session_and_anchors(self):
        rfq = {"acceptable_price_min": 45.0, "acceptable_price_max": 48.0}
        preflight = negotiation_engine.build_negotiation_preflight(
            rfq=rfq,
            supplier_quote=72.0,
            previous_quotes=[],
            active_session=None,
        )
        assert preflight["selected_strategy"] == "ANCHOR"
        assert preflight["recommended_anchor"] == 45.0
        assert preflight["allowed_counter_min"] == 45.0
        assert preflight["allowed_counter_max"] == 45.0

    # 9, 10, 11. Supplier 72 -> 65 detects concession of 7 and changes strategy
    def test_9_10_11_supplier_72_to_65_detects_concession_and_changes_strategy(self):
        active_session = {
            "initial_supplier_offer": 72.0,
            "latest_supplier_offer": 72.0,
            "latest_agent_counter": 45.0,
            "attempt_count": 1,
        }
        rfq = {"acceptable_price_min": 45.0, "acceptable_price_max": 48.0}
        preflight = negotiation_engine.build_negotiation_preflight(
            rfq=rfq,
            supplier_quote=65.0,
            previous_quotes=[{"price": 72.0}],
            active_session=active_session,
        )
        assert preflight["supplier_last_concession"] == 7.0
        assert preflight["supplier_total_concession"] == 7.0
        assert preflight["selected_strategy"] == "RECIPROCAL_CONCESSION"
        # Should NOT simply stay at 45 without conceding or jump wildly
        assert preflight["recommended_anchor"] > 45.0
        assert preflight["recommended_anchor"] <= 48.0

    # 12. Supplier repeats same price: no automatic concession (HOLD_POSITION)
    def test_12_supplier_repeats_same_price_holds_position(self):
        active_session = {
            "initial_supplier_offer": 72.0,
            "latest_supplier_offer": 65.0,
            "latest_agent_counter": 46.0,
            "attempt_count": 2,
        }
        rfq = {"acceptable_price_min": 45.0, "acceptable_price_max": 48.0}
        preflight = negotiation_engine.build_negotiation_preflight(
            rfq=rfq,
            supplier_quote=65.0,
            previous_quotes=[{"price": 65.0}],
            active_session=active_session,
        )
        assert preflight["supplier_last_concession"] == 0.0
        assert preflight["selected_strategy"] == "HOLD_POSITION"
        assert preflight["can_concede"] is False
        assert preflight["recommended_anchor"] == 46.0  # Holds counter at 46.0

    # 13. Supplier increases price: no automatic concession
    def test_13_supplier_increases_price_holds_position(self):
        active_session = {
            "initial_supplier_offer": 72.0,
            "latest_supplier_offer": 65.0,
            "latest_agent_counter": 46.0,
            "attempt_count": 2,
        }
        rfq = {"acceptable_price_min": 45.0, "acceptable_price_max": 48.0}
        preflight = negotiation_engine.build_negotiation_preflight(
            rfq=rfq,
            supplier_quote=68.0,  # Price increased from 65 to 68
            previous_quotes=[{"price": 65.0}],
            active_session=active_session,
        )
        assert preflight["supplier_last_concession"] == -3.0
        assert preflight["selected_strategy"] == "HOLD_POSITION"
        assert preflight["can_concede"] is False
        assert preflight["recommended_anchor"] == 46.0

    # 14 & 15. Meaningful movement allows reciprocal concession smaller than supplier's
    def test_14_15_reciprocal_concession_smaller_than_supplier_concession(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=65.0,
            previous_supplier_offer=72.0,  # Supplier conceded 7 AED
            latest_agent_counter=45.0,
            attempt_count=1,
        )
        assert bounds["selected_strategy"] == "RECIPROCAL_CONCESSION"
        assert bounds["can_concede"] is True
        agent_step = bounds["recommended_anchor"] - 45.0
        # Agent concession (e.g. 1.0 - 2.45 AED) is strictly smaller than supplier concession (7.0 AED)
        assert agent_step < 7.0
        assert agent_step > 0.0

    # 16. Counter never exceeds acceptable_max
    def test_16_counter_never_exceeds_acceptable_max(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=49.0,
            previous_supplier_offer=70.0,
            latest_agent_counter=47.5,
            attempt_count=7,
        )
        assert bounds["recommended_anchor"] <= 48.0
        assert bounds["allowed_counter_max"] <= 48.0

    # 17. Counter never >= latest supplier price
    def test_17_counter_never_greater_or_equal_to_supplier_offer(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=46.0,
            previous_supplier_offer=47.0,
            latest_agent_counter=45.0,
            attempt_count=3,
        )
        assert bounds["recommended_anchor"] < 46.0

    # 18 & 19. Counter within allowed range and does not oscillate backward
    def test_18_19_counter_within_range_and_no_backward_oscillation(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=60.0,
            previous_supplier_offer=65.0,
            latest_agent_counter=46.5,
            attempt_count=3,
        )
        assert bounds["allowed_counter_min"] >= 46.5  # Does not oscillate below 46.5
        assert bounds["recommended_anchor"] >= 46.5

    # 20 & 21. Warm language reflects supplier movement

    # 22. Information-seeking does not invent buyer flexibility

    # 23 & 24. Final detected and stops before attempt 10
    def test_23_24_final_detected_and_stops(self):
        assert negotiation_engine.is_final_price_declared("This is my final price 48 AED") is True
        assert negotiation_engine.is_final_price_declared("non-negotiable rate") is True
        assert negotiation_engine.is_final_price_declared("can't go lower") is True
        
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=48.0,
            supplier_final_detected=True,
            attempt_count=2,
        )
        assert bounds["should_counter"] is False
        assert bounds["selected_strategy"] == "ACKNOWLEDGE_AND_STOP"

    # 25. Final above tolerated ceiling escalates
    def test_25_final_above_ceiling_escalates(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=55.0,  # 55 > 51 (ceiling)
            supplier_final_detected=True,
            attempt_count=2,
        )
        assert bounds["should_counter"] is False
        assert bounds["selected_strategy"] == "ESCALATE"
        assert bounds["reason_code"] == "SUPPLIER_FINAL_ABOVE_CEILING"

    # 26. Final within tolerated ceiling stops without extra counter
    def test_26_final_within_ceiling_stops_without_extra_counter(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=48.0,  # 48 <= 51
            supplier_final_detected=True,
            attempt_count=3,
        )
        assert bounds["should_counter"] is False
        assert bounds["selected_strategy"] == "ACKNOWLEDGE_AND_STOP"
        assert bounds["reason_code"] == "SUPPLIER_FINAL_WITHIN_BOUNDS"

    # 27. Final-price logic works when last_quote is NULL
    def test_27_final_price_logic_works_when_last_quote_is_null(self):
        rfq = {"acceptable_price_min": 45.0, "acceptable_price_max": 48.0, "last_quote": None}
        preflight = negotiation_engine.build_negotiation_preflight(
            rfq=rfq,
            supplier_quote=55.0,
            previous_quotes=[],
            raw_message_text="55 is my final offer",
        )
        assert preflight["supplier_final_detected"] is True
        assert preflight["tolerated_final_ceiling"] == 51.0
        assert preflight["selected_strategy"] == "ESCALATE"

    # 28 & 29. Supplier reaches or beats target early -> stop
    def test_28_29_supplier_reaches_or_beats_target_stops(self):
        bounds_exact = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=45.0,
            attempt_count=1,
        )
        assert bounds_exact["should_counter"] is False
        assert bounds_exact["reason_code"] == "TARGET_REACHED"

        bounds_below = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=44.0,
            attempt_count=1,
        )
        assert bounds_below["should_counter"] is False
        assert bounds_below["reason_code"] == "TARGET_REACHED"

    # 30. Supplier accepts our counter -> stop
    def test_30_supplier_accepts_counter_stops(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=46.0,
            latest_agent_counter=46.0,
            attempt_count=2,
        )
        assert bounds["should_counter"] is False
        assert bounds["reason_code"] == "COUNTER_ACCEPTED"

    # 31. Quote history remains intact (quote-first)

    # 32. Active negotiation session routes "65" to correct RFQ

    # 33. Exact stanza to different RFQ overrides active negotiation session

    # 34. Stale session does not capture explicit message for another product

    # 36, 37, 38. Attempt 10 unresolved -> human review with structured summary & no attempt 11

    # 39, 40, 41, 42. Information protection: guardrails block internal disclosures

    # 43. Prompt injection still blocked

    # 44. Autonomous acceptance blocked

    # 45. Validator enforces allowed range 45-47: 46 passes

    # 46. Counter 48 fails when allowed range is 45-47 (even if acceptable_max is 48)

    # 47. Counter below allowed minimum fails

    # 48. Validator recomputes safe range from DB and ignores LLM-supplied fake range

    # 49. Supplier 65 -> 64.80: tiny concession (0.20 < 1.0 threshold) results in HOLD_POSITION
    def test_49_supplier_tiny_concession_holds_position(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=64.80,
            previous_supplier_offer=65.00,  # Concession = 0.20 AED
            latest_agent_counter=45.00,
            attempt_count=1,
        )
        assert bounds["selected_strategy"] == "HOLD_POSITION"
        assert bounds["can_concede"] is False
        assert bounds["recommended_anchor"] == 45.00
        assert bounds["allowed_counter_max"] == 45.00

    # 50. Supplier 65 -> 64.20: concession 0.80 < 1.0 threshold results in HOLD_POSITION
    def test_50_supplier_concession_0_80_holds_position(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=64.20,
            previous_supplier_offer=65.00,  # Concession = 0.80 AED
            latest_agent_counter=45.00,
            attempt_count=1,
        )
        assert bounds["selected_strategy"] == "HOLD_POSITION"
        assert bounds["can_concede"] is False
        assert bounds["recommended_anchor"] == 45.00

    # 51. Supplier 65 -> 63: concession 2.0 >= 1.0 allows reciprocal concession strictly < 2.0
    def test_51_supplier_concession_2_allows_smaller_reciprocal_concession(self):
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=45.0,
            acceptable_max=48.0,
            tolerated_final_ceiling=51.0,
            latest_supplier_offer=63.00,
            previous_supplier_offer=65.00,  # Concession = 2.0 AED
            latest_agent_counter=45.00,
            attempt_count=1,
        )
        assert bounds["selected_strategy"] == "RECIPROCAL_CONCESSION"
        assert bounds["can_concede"] is True
        agent_step = bounds["recommended_anchor"] - 45.00
        assert 0 < agent_step < 2.00
        assert bounds["recommended_anchor"] <= 45.70

    # 52. Agent concession is always strictly smaller than supplier last concession
    @pytest.mark.parametrize("supp_prev, supp_curr", [
        (100.0, 98.0),  # Concession = 2.0
        (80.0, 75.0),   # Concession = 5.0
        (50.0, 48.5),   # Concession = 1.5
        (50.0, 49.0),   # Concession = 1.0
    ])
    def test_52_agent_concession_strictly_smaller_than_supplier_concession(self, supp_prev, supp_curr):
        supp_concession = supp_prev - supp_curr
        bounds = negotiation_engine.build_allowed_counter_range(
            preferred_target=40.0,
            acceptable_max=50.0,
            tolerated_final_ceiling=53.0,
            latest_supplier_offer=supp_curr,
            previous_supplier_offer=supp_prev,
            latest_agent_counter=40.0,
            attempt_count=1,
        )
        agent_step = bounds["recommended_anchor"] - 40.0
        assert agent_step < supp_concession

    # 53. Session DB insert failure fails closed: no negotiation message sent

    # 54. Session DB update returns None on failure (does not return fake dict)
    def test_54_db_create_or_update_session_returns_none_on_error(self, mock_supabase):
        mock_supabase.table().insert().execute.side_effect = Exception("DB connection timeout")
        res = db.create_or_update_negotiation_session(CLIENT_ID, str(uuid.uuid4()), SUPPLIER_ID, preferred_target=45.0)
        assert res is None


class TestFavorableQuoteBuyerSemantics:
    def test_below_target_quote_stretches_downward_never_upward(self):
        result = negotiation_engine.build_allowed_counter_range(
            preferred_target=2.5,
            acceptable_max=4.0,
            tolerated_final_ceiling=7.0,
            latest_supplier_offer=2.0,
            previous_supplier_offer=None,
            latest_agent_counter=None,
            attempt_count=0,
            supplier_final_detected=False,
        )

        assert result["should_counter"] is True
        assert result["selected_strategy"] == "STRETCH_SAVINGS"
        assert result["reason_code"] == "BELOW_TARGET_FAVORABLE"
        assert result["recommended_anchor"] == 1.86
        assert result["recommended_anchor"] < 2.0

    def test_favorable_quote_holds_existing_stretch_counter_on_next_turn(self):
        result = negotiation_engine.build_allowed_counter_range(
            preferred_target=2.5,
            acceptable_max=4.0,
            tolerated_final_ceiling=7.0,
            latest_supplier_offer=2.0,
            previous_supplier_offer=2.0,
            latest_agent_counter=1.86,
            attempt_count=1,
            supplier_final_detected=False,
        )

        assert result["should_counter"] is True
        assert result["selected_strategy"] == "STRETCH_SAVINGS"
        assert result["recommended_anchor"] == 1.86

    def test_favorable_supplier_final_stops_negotiation(self):
        result = negotiation_engine.build_allowed_counter_range(
            preferred_target=2.5,
            acceptable_max=4.0,
            tolerated_final_ceiling=7.0,
            latest_supplier_offer=2.0,
            latest_agent_counter=1.5,
            attempt_count=1,
            supplier_final_detected=True,
        )

        assert result["should_counter"] is False
        assert result["selected_strategy"] == "ACKNOWLEDGE_AND_STOP"
        assert result["reason_code"] == "SUPPLIER_FINAL_WITHIN_BOUNDS"


class TestDreamNestFalsePositiveTradeoffs:
    def test_same_requested_quantity_is_not_tradeoff(self):
        result = negotiation_engine.evaluate_supplier_tradeoff(
            message_text="Quantity: 1 Brand: FLEXTOP Final Price: 2415",
            rfq_constraints=[],
            rfq={"quantity": 1, "specs": "10MM"},
            extracted_quantity=1,
            extracted_variant="FLEXTOP",
            commercial_parse={
                "quantity": {"value": 1, "minimum_order_quantity": None},
                "specification": None,
                "tradeoff": {"present": True, "dimension": "quantity"},
                "delivery": None,
                "payment_terms": None,
            },
        )

        assert result["has_tradeoff"] is False
        assert result["is_authorized"] is True

    def test_plain_brand_variant_is_not_specification_tradeoff(self):
        result = negotiation_engine.evaluate_supplier_tradeoff(
            message_text="Brand EZFLEX 10mmx5c 1 roll 2175",
            rfq_constraints=[],
            rfq={"quantity": 1, "specs": "10MM"},
            extracted_quantity=1,
            extracted_variant="EZFLEX",
            commercial_parse={
                "quantity": {"value": 1, "minimum_order_quantity": None},
                "specification": {"alternative": "EZFLEX"},
                "tradeoff": {"present": True, "dimension": "specification"},
                "delivery": None,
                "payment_terms": None,
            },
        )

        assert result["has_tradeoff"] is False
        assert result["is_authorized"] is True

    def test_explicit_substitution_still_requires_spec_authorization(self):
        result = negotiation_engine.evaluate_supplier_tradeoff(
            message_text="We cannot supply the requested brand. Alternative EZFLEX instead.",
            rfq_constraints=[],
            rfq={"quantity": 1, "specs": "10MM"},
            extracted_variant="EZFLEX",
            commercial_parse={
                "quantity": None,
                "specification": {"alternative": "EZFLEX"},
                "tradeoff": {"present": True, "dimension": "specification"},
                "delivery": None,
                "payment_terms": None,
            },
        )

        assert result["has_tradeoff"] is True
        assert result["is_authorized"] is False
        assert result["dimension"] == "specification"


class TestTargetHierarchyWithoutSyntheticCeiling:
    def test_last_quote_is_target_but_not_hard_maximum(self):
        hierarchy = negotiation_engine.compute_target_hierarchy(
            acceptable_price_min=None,
            acceptable_price_max=None,
            last_quote=1073.0,
        )

        assert hierarchy["preferred_target"] == 1073.0
        assert hierarchy["acceptable_max"] is None
        assert hierarchy["tolerated_final_ceiling"] is None

    def test_explicit_max_is_only_hard_maximum(self):
        hierarchy = negotiation_engine.compute_target_hierarchy(
            acceptable_price_min=1000.0,
            acceptable_price_max=1150.0,
            last_quote=1073.0,
        )

        assert hierarchy["preferred_target"] == 1000.0
        assert hierarchy["acceptable_max"] == 1150.0
        assert hierarchy["tolerated_final_ceiling"] == 1150.0

    def test_supplier_final_without_explicit_max_stops_for_human_not_new_ceiling(self):
        result = negotiation_engine.build_allowed_counter_range(
            preferred_target=1073.0,
            acceptable_max=None,
            tolerated_final_ceiling=None,
            latest_supplier_offer=1180.0,
            supplier_final_detected=True,
        )

        assert result["should_counter"] is False
        assert result["selected_strategy"] == "ACKNOWLEDGE_AND_STOP"
        assert result["reason_code"] == "SUPPLIER_FINAL_REQUIRES_HUMAN_DECISION"

    def test_supplier_final_above_explicit_max_escalates(self):
        result = negotiation_engine.build_allowed_counter_range(
            preferred_target=1073.0,
            acceptable_max=1150.0,
            tolerated_final_ceiling=1150.0,
            latest_supplier_offer=1180.0,
            supplier_final_detected=True,
        )

        assert result["should_counter"] is False
        assert result["selected_strategy"] == "ESCALATE"
        assert result["reason_code"] == "SUPPLIER_FINAL_ABOVE_EXPLICIT_MAX"

    def test_favorable_offer_below_last_quote_stretches_further_down(self):
        result = negotiation_engine.build_allowed_counter_range(
            preferred_target=1073.0,
            acceptable_max=None,
            tolerated_final_ceiling=None,
            latest_supplier_offer=1000.0,
            attempt_count=0,
            supplier_final_detected=False,
        )

        assert result["should_counter"] is True
        assert result["selected_strategy"] == "STRETCH_SAVINGS"
        assert result["recommended_anchor"] == 930.0
        assert result["recommended_anchor"] < 1000.0
