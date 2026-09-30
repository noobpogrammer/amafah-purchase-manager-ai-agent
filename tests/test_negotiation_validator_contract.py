"""Contract tests for negotiation-engine / Policy Validator responsibilities.

All tests are deterministic and use no live LLM calls.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import db
from policy_validator import ActionProposal, validate_action


def _rfq_context():
    rfq = {
        "id": "rfq-1",
        "client_id": "client-1",
        "status": "active",
        "due_by": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "acceptable_price_min": 45.0,
        "acceptable_price_max": 48.0,
    }
    return rfq, {
        "id": "rs-1",
        "rfq_id": "rfq-1",
        "supplier_id": "supp-1",
        "status": "responded",
        "rfqs": rfq,
    }


def _proposal(counter_price=47.45):
    return ActionProposal(
        tool_name="negotiate_price",
        arguments={
            "rfq_id": "rfq-1",
            "quote_id": "quote-65",
            "quoted_price": 65.0,
            "counter_price": counter_price,
            "negotiation_message": f"Could you meet us at AED {counter_price:g} per piece?",
        },
        raw_message="I can reduce it to 65 AED.",
    )


def _trusted_quote():
    return {
        "id": "quote-65",
        "rfq_id": "rfq-1",
        "supplier_id": "supp-1",
        "price": 65.0,
        "delivery_time": "2 days",
        "is_available": True,
    }


def _preflight():
    return {
        "should_counter": True,
        "allowed_counter_min": 45.0,
        "allowed_counter_max": 47.45,
        "recommended_anchor": 47.45,
        "selected_strategy": "RECIPROCAL_CONCESSION",
        "reason_code": "SUPPLIER_CONCEDED",
    }


def test_validator_accepts_engine_approved_reciprocal_counter():
    rfq, rfq_supplier = _rfq_context()
    quote = _trusted_quote()

    with patch.object(db, "get_quote_by_id", return_value=quote), \
         patch.object(db, "get_quotes_for_rfq", return_value=[quote]), \
         patch.object(db, "get_negotiation_attempts", return_value=1):
        result = validate_action(
            proposal=_proposal(47.45),
            client_id="client-1",
            supplier_id="supp-1",
            context_rfqs=[rfq_supplier],
            matched_rfq_id="rfq-1",
            input_origin="supplier",
            negotiation_preflight=_preflight(),
        )

    assert result.is_valid is True
    assert result.sanitized_args["counter_price"] == 47.45
    assert result.sanitized_args["allowed_counter_max"] == 47.45
    assert result.sanitized_args["selected_strategy"] == "RECIPROCAL_CONCESSION"


def test_validator_still_blocks_counter_above_buyer_absolute_max():
    rfq, rfq_supplier = _rfq_context()
    quote = _trusted_quote()
    unsafe_preflight = {
        **_preflight(),
        "allowed_counter_max": 49.0,
        "recommended_anchor": 49.0,
    }

    with patch.object(db, "get_quote_by_id", return_value=quote), \
         patch.object(db, "get_quotes_for_rfq", return_value=[quote]), \
         patch.object(db, "get_negotiation_attempts", return_value=1):
        result = validate_action(
            proposal=_proposal(49.0),
            client_id="client-1",
            supplier_id="supp-1",
            context_rfqs=[rfq_supplier],
            matched_rfq_id="rfq-1",
            input_origin="supplier",
            negotiation_preflight=unsafe_preflight,
        )

    assert result.is_valid is False
    assert "acceptable_price_max" in result.reason


def test_validator_fails_closed_when_engine_preflight_missing():
    rfq, rfq_supplier = _rfq_context()
    quote = _trusted_quote()

    with patch.object(db, "get_quote_by_id", return_value=quote), \
         patch.object(db, "get_quotes_for_rfq", return_value=[quote]), \
         patch.object(db, "get_negotiation_attempts", return_value=1):
        result = validate_action(
            proposal=_proposal(47.45),
            client_id="client-1",
            supplier_id="supp-1",
            context_rfqs=[rfq_supplier],
            matched_rfq_id="rfq-1",
            input_origin="supplier",
            negotiation_preflight=None,
        )

    assert result.is_valid is False
    assert "missing a trusted negotiation-engine preflight" in result.reason
