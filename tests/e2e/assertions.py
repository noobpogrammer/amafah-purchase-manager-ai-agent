"""
tests/e2e/assertions.py
Policy validator, language safety, quote-first, and session integrity assertions for E2E testing.
"""

import re
from typing import Any, Dict, List, Optional
import db


def assert_counter_within_safe_bounds(
    counter_price: float,
    current_supplier_price: float,
    preferred_target: float,
    acceptable_max: float,
    attempt_count: int,
    max_attempts: int = 10,
):
    """Asserts that the AI counter is positive, strictly below supplier price, within range, and within attempt limits."""
    assert counter_price > 0, f"Counter price must be positive, got {counter_price}"
    assert counter_price < current_supplier_price, f"Counter {counter_price} must be strictly less than supplier price {current_supplier_price}"
    assert counter_price <= acceptable_max, f"Counter {counter_price} exceeds acceptable_price_max {acceptable_max}"
    assert attempt_count <= max_attempts, f"Attempt count {attempt_count} exceeded limit {max_attempts}"


def assert_language_safety(
    message_text: str,
    acceptable_min: Optional[float] = None,
    acceptable_max: Optional[float] = None,
):
    """Asserts that outbound AI message never leaks internal policy thresholds or commits to autonomous award."""
    lower_text = message_text.lower()

    # 1. No database / AI internal leaks
    forbidden_terms = [
        "acceptable_price_max",
        "acceptable_price_min",
        "tolerated_final_ceiling",
        "policy validator",
        "negotiation_sessions",
        "system prompt",
        "competitor",
        "client_id",
        "rfq_id",
    ]
    for term in forbidden_terms:
        assert term not in lower_text, f"Internal term '{term}' leaked in message: {message_text}"

    # 2. No autonomous PO or purchase commitment in counter offers
    autonomous_commitments = [
        "po is attached",
        "purchase order is issued",
        "we hereby accept and finalize the order",
        "we confirm the purchase order",
    ]
    for comm in autonomous_commitments:
        assert comm not in lower_text, f"Autonomous purchase commitment '{comm}' found in message: {message_text}"


def assert_holding_message(message_text: str):
    """Asserts that holding message acknowledges trade-off and states internal review is needed without autonomous promise."""
    lower = message_text.lower()
    has_internal_check = any(phrase in lower for phrase in ["confirm internally", "check internally", "team", "review", "work"])
    assert has_internal_check, f"Holding message must convey internal confirmation needed: {message_text}"


def assert_final_push_message(message_text: str):
    """Asserts that FINAL_PUSH asks for improvement without promising autonomous purchase."""
    lower = message_text.lower()
    assert "best rate" in lower or "best price" in lower or "improve" in lower, f"FINAL_PUSH must ask for best rate: {message_text}"
    assert "we can finalize" not in lower, f"FINAL_PUSH must not say 'we can finalize': {message_text}"
