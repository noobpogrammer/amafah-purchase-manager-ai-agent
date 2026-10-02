"""
Phase 2: Adaptive Autonomous Negotiation Engine.

Implements:
1. Target hierarchy & price bounds calculation
2. Concession tracking and counterparty behavior classification
3. Structured strategy selection (ANCHOR, HOLD_POSITION, RECIPROCAL_CONCESSION, INFORMATION_SEEKING, FINAL_PUSH, ACKNOWLEDGE_AND_STOP, ESCALATE)
4. Deterministic allowed counter range calculation (safeguards LLM from proposing arbitrary numbers)
5. Robust final price detection
6. Structured preflight preparation
"""

import os
import re
from typing import Dict, Any, Optional, Tuple

MAX_NEGOTIATION_ATTEMPTS = int(os.environ.get("MAX_NEGOTIATION_ATTEMPTS", 10))
PRICE_TOLERANCE_AED = 3.0
MIN_MEANINGFUL_SUPPLIER_CONCESSION_AED = 1.0
FAVORABLE_STRETCH_DISCOUNT = float(os.environ.get("FAVORABLE_STRETCH_DISCOUNT", "0.25"))


FINAL_PRICE_PATTERNS = [
    r"\bfinal\b",
    r"\bbest\s+price\b",
    r"\blowest\s+price\b",
    r"\bfixed\s+price\b",
    r"\blast\s+price\b",
    r"\bnon-?negotiable\b",
    r"\bcan'?t\s+go\s+(lower|below)\b",
    r"\bcannot\s+(reduce|go\s+lower|go\s+below|discount|drop)\b",
    r"\bno\s+more\s+discount\b",
    r"\bthat'?s\s+(my\s+)?final\b",
    r"\bfinal\s+(offer|price|rate|quote)\b",
    r"\blast\s+(offer|rate|quote)\b",
    r"\bfixed\s+rate\b",
    r"\bbottom\s+line\b",
    r"\btake\s+it\s+or\s+leave\s+it\b",
]

FINAL_PRICE_RE = re.compile("|".join(FINAL_PRICE_PATTERNS), re.IGNORECASE)


def is_final_price_declared(text: Optional[str]) -> bool:
    """Detects if supplier message explicitly declares a final/fixed/non-negotiable offer."""
    if not text:
        return False
    return bool(FINAL_PRICE_RE.search(text))


def compute_target_hierarchy(
    acceptable_price_min: Optional[float] = None,
    acceptable_price_max: Optional[float] = None,
    last_quote: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """
    Establishes the deterministic price target hierarchy:
    1. acceptable_price_min = optimum / preferred target
    2. last_quote = fallback target if acceptable_price_min is missing
    3. acceptable_price_max = final fallback target if neither exists
    
    Ceiling: acceptable_price_max + 3.0 (or preferred_target + 3.0)
    """
    preferred_target = None
    if acceptable_price_min is not None and acceptable_price_min > 0:
        preferred_target = float(acceptable_price_min)
    elif last_quote is not None and last_quote > 0:
        preferred_target = float(last_quote)
    elif acceptable_price_max is not None and acceptable_price_max > 0:
        preferred_target = float(acceptable_price_max)

    acc_max = float(acceptable_price_max) if acceptable_price_max is not None and acceptable_price_max > 0 else None

    # Tolerated final ceiling
    if acc_max is not None:
        tolerated_ceiling = acc_max + PRICE_TOLERANCE_AED
    elif preferred_target is not None:
        tolerated_ceiling = preferred_target + PRICE_TOLERANCE_AED
    else:
        tolerated_ceiling = None

    return {
        "preferred_target": preferred_target,
        "acceptable_max": acc_max,
        "tolerated_final_ceiling": tolerated_ceiling,
    }


def classify_supplier_movement(
    initial_offer: float,
    previous_offer: Optional[float],
    latest_offer: float,
    latest_agent_counter: Optional[float] = None,
    is_final_declared: bool = False,
) -> Tuple[str, float, float]:
    """
    Classifies supplier concession behavior:
    Returns (behavior_classification, supplier_last_concession, supplier_total_concession)
    """
    if previous_offer is not None:
        last_concession = round(previous_offer - latest_offer, 2)
    else:
        last_concession = 0.0

    total_concession = round(initial_offer - latest_offer, 2)

    if latest_agent_counter is not None and latest_offer <= latest_agent_counter:
        return "ACCEPTED_COUNTER", last_concession, total_concession

    if is_final_declared:
        return "FINAL_PRICE", last_concession, total_concession

    if previous_offer is None:
        return "INITIAL_OFFER", 0.0, 0.0

    if last_concession < -0.01:
        return "PRICE_INCREASED", last_concession, total_concession
    elif abs(last_concession) <= 0.01:
        return "NO_MOVEMENT", 0.0, total_concession
    elif last_concession < MIN_MEANINGFUL_SUPPLIER_CONCESSION_AED:
        return "MOVED_SLIGHTLY", last_concession, total_concession
    elif last_concession >= 5.0 or (previous_offer and (last_concession / previous_offer) >= 0.08):
        return "MOVED_SIGNIFICANTLY", last_concession, total_concession
    else:
        return "MOVED_SIGNIFICANTLY", last_concession, total_concession


def build_allowed_counter_range(
    preferred_target: Optional[float],
    acceptable_max: Optional[float],
    tolerated_final_ceiling: Optional[float],
    latest_supplier_offer: float,
    previous_supplier_offer: Optional[float] = None,
    latest_agent_counter: Optional[float] = None,
    attempt_count: int = 0,
    max_attempts: int = MAX_NEGOTIATION_ATTEMPTS,
    supplier_final_detected: bool = False,
    no_movement_count: int = 0,
) -> Dict[str, Any]:
    """
    Calculates deterministic bounds for the next autonomous agent counteroffer.
    Enforces all concession principles and guarantees the LLM cannot invent illegal prices.
    """
    # If preferred target is missing, fallback safely
    if preferred_target is None:
        preferred_target = acceptable_max or latest_supplier_offer

    effective_cap = acceptable_max if acceptable_max is not None else preferred_target

    # 1. Supplier explicitly declared a final/non-negotiable price.
    # Stop immediately even when the price is already favorable. Final commercial
    # acceptance remains a human decision elsewhere in the orchestration layer.
    if supplier_final_detected:
        is_above_ceiling = (
            tolerated_final_ceiling is not None and latest_supplier_offer > tolerated_final_ceiling
        )
        return {
            "allowed_counter_min": None,
            "allowed_counter_max": None,
            "recommended_anchor": None,
            "can_concede": False,
            "selected_strategy": "ESCALATE" if is_above_ceiling else "ACKNOWLEDGE_AND_STOP",
            "reason_code": "SUPPLIER_FINAL_ABOVE_CEILING" if is_above_ceiling else "SUPPLIER_FINAL_WITHIN_BOUNDS",
            "should_counter": False,
        }

    # 2. Supplier accepted/met our previous counter.
    if latest_agent_counter is not None and latest_supplier_offer <= latest_agent_counter:
        return {
            "allowed_counter_min": None,
            "allowed_counter_max": None,
            "recommended_anchor": None,
            "can_concede": False,
            "selected_strategy": "ACKNOWLEDGE_AND_STOP",
            "reason_code": "COUNTER_ACCEPTED",
            "should_counter": False,
        }

    # 3. Favorable quote: supplier is already at/below the buyer's preferred target.
    # Never negotiate upward. Instead, make one deterministic "stretch savings"
    # ask below the supplier's own price. On subsequent turns, hold that stretch
    # counter unless the supplier accepts it or explicitly declares final.
    if latest_supplier_offer <= preferred_target:
        if latest_agent_counter is not None and latest_agent_counter < latest_supplier_offer:
            stretch = round(float(latest_agent_counter), 2)
        else:
            pct = min(max(FAVORABLE_STRETCH_DISCOUNT, 0.01), 0.50)
            stretch = round(latest_supplier_offer * (1.0 - pct), 2)
            if stretch <= 0:
                stretch = round(max(0.01, latest_supplier_offer - 0.01), 2)
            if stretch >= latest_supplier_offer:
                stretch = round(max(0.01, latest_supplier_offer - 0.01), 2)

        if stretch <= 0 or stretch >= latest_supplier_offer:
            return {
                "allowed_counter_min": None,
                "allowed_counter_max": None,
                "recommended_anchor": None,
                "can_concede": False,
                "selected_strategy": "ACKNOWLEDGE_AND_STOP",
                "reason_code": "FAVORABLE_PRICE_NO_SAFE_STRETCH",
                "should_counter": False,
            }

        return {
            "allowed_counter_min": stretch,
            "allowed_counter_max": stretch,
            "recommended_anchor": stretch,
            "can_concede": False,
            "selected_strategy": "STRETCH_SAVINGS",
            "reason_code": "BELOW_TARGET_FAVORABLE",
            "should_counter": True,
        }

    # 4. Attempt limit reached (e.g. 10 attempts already used)
    if attempt_count >= max_attempts:
        is_above_ceiling = (
            tolerated_final_ceiling is not None and latest_supplier_offer > tolerated_final_ceiling
        )
        return {
            "allowed_counter_min": None,
            "allowed_counter_max": None,
            "recommended_anchor": None,
            "can_concede": False,
            "selected_strategy": "ESCALATE",
            "reason_code": "ATTEMPT_LIMIT_REACHED",
            "should_counter": False,
        }

    # 5. First counter (ANCHOR)
    if latest_agent_counter is None or attempt_count == 0:
        anchor = preferred_target
        # Counter must be strictly less than supplier offer
        if anchor >= latest_supplier_offer:
            anchor = max(1.0, round(latest_supplier_offer - 1.0, 2))
        return {
            "allowed_counter_min": round(anchor, 2),
            "allowed_counter_max": round(anchor, 2),
            "recommended_anchor": round(anchor, 2),
            "can_concede": False,
            "selected_strategy": "ANCHOR",
            "reason_code": "INITIAL_ANCHOR",
            "should_counter": True,
        }

    # 6. Subsequent counters
    current_counter = float(latest_agent_counter)
    supplier_last_concession = 0.0
    if previous_supplier_offer is not None:
        supplier_last_concession = round(previous_supplier_offer - latest_supplier_offer, 2)

    # Upper hard ceiling: counter must be <= acceptable_max and strictly < latest_supplier_offer
    max_possible_counter = min(effective_cap, latest_supplier_offer - 0.01)
    if max_possible_counter < current_counter:
        max_possible_counter = current_counter

    # A. Supplier increased price, did not move, or conceded less than MIN_MEANINGFUL_SUPPLIER_CONCESSION_AED -> HOLD POSITION
    if supplier_last_concession < MIN_MEANINGFUL_SUPPLIER_CONCESSION_AED:
        strategy = "HOLD_POSITION"
        if no_movement_count >= 2:
            strategy = "INFORMATION_SEEKING"
        reason = "NO_SUPPLIER_MOVEMENT" if supplier_last_concession <= 0.01 else "SUPPLIER_CONCESSION_BELOW_MIN_THRESHOLD"
        return {
            "allowed_counter_min": round(current_counter, 2),
            "allowed_counter_max": round(current_counter, 2),
            "recommended_anchor": round(current_counter, 2),
            "can_concede": False,
            "selected_strategy": strategy,
            "reason_code": reason,
            "should_counter": True,
        }

    # B. Supplier made meaningful concession (>= MIN_MEANINGFUL_SUPPLIER_CONCESSION_AED) -> Controlled reciprocal concession
    # Rule: our concession must strictly be smaller than supplier's concession (0 < agent_concession < supplier_last_concession)
    # Step proportion: ~25% - 35% of supplier concession, capped at remaining gap to acceptable_max
    raw_step = round(supplier_last_concession * 0.35, 2)
    # Ensure agent concession is strictly smaller than supplier concession
    max_step = max(0.01, round(supplier_last_concession - 0.01, 2))
    concession_step = min(raw_step, max_step)
    if concession_step < 0.25 and supplier_last_concession >= 1.0:
        concession_step = min(0.25, max_step)

    new_counter_max = min(max_possible_counter, round(current_counter + concession_step, 2))
    new_counter_min = current_counter  # Never oscillate backward

    # Check if near attempt limit (e.g. attempt >= 8) -> FINAL PUSH
    if attempt_count >= 8:
        strategy = "FINAL_PUSH"
        reason = "NEAR_ATTEMPT_LIMIT_PUSH"
        new_counter_max = min(max_possible_counter, effective_cap)
    else:
        strategy = "RECIPROCAL_CONCESSION"
        reason = "SUPPLIER_CONCEDED"

    recommended = round(new_counter_max, 2)

    return {
        "allowed_counter_min": round(new_counter_min, 2),
        "allowed_counter_max": round(new_counter_max, 2),
        "recommended_anchor": recommended,
        "can_concede": True,
        "selected_strategy": strategy,
        "reason_code": reason,
        "should_counter": True,
    }


def evaluate_supplier_tradeoff(
    message_text: Optional[str],
    rfq_constraints: Optional[list] = None,
    rfq: Optional[Dict[str, Any]] = None,
    extracted_delivery: Optional[str] = None,
    extracted_quantity: Optional[int] = None,
    extracted_variant: Optional[str] = None,
    commercial_parse: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Evaluates whether the supplier proposes a conditional trade-off
    (delivery, quantity, specification, payment) and deterministically verifies
    if it is authorized under rfq_negotiation_constraints.
    """
    if not message_text and not commercial_parse:
        return {"has_tradeoff": False, "is_authorized": True}

    text = message_text or ""
    constraints_by_dim = {c.get("dimension"): c for c in (rfq_constraints or [])}

    if commercial_parse is None and text:
        try:
            import groq_client
            commercial_parse = groq_client.parse_commercial_message(text)
        except Exception:
            commercial_parse = None

    tradeoff = (commercial_parse.get("tradeoff") or {}) if commercial_parse else {}
    has_tradeoff_flag = bool(tradeoff.get("present"))
    tradeoff_dim = tradeoff.get("dimension")
    deliv_info = (commercial_parse.get("delivery") or {}) if commercial_parse else {}
    qty_info = (commercial_parse.get("quantity") or {}) if commercial_parse else {}
    spec_info = (commercial_parse.get("specification") or {}) if commercial_parse else {}
    payment_info = (commercial_parse.get("payment_terms") or {}) if commercial_parse else {}

    # 1. Check Delivery Trade-off
    proposed_days = deliv_info.get("days")
    if proposed_days is None and extracted_delivery:
        m = re.search(r"(\d+)", str(extracted_delivery))
        if m:
            proposed_days = int(m.group(1))

    # Regex fallback if structured parse missed delivery
    if proposed_days is None and text:
        deliv_match = re.search(
            r"\b(?:if\s+(?:you\s+(?:can\s+)?)?(?:accept|allow|take|do)\s+|if\s+delivery\s+is\s+|can\s+reduce\s+(?:price|rate)\s+if\s+(?:delivery\s+is\s+)?|ready\s+in\s+|lead\s+time\s+(?:is\s+|around\s+)?|delivery\s+(?:in\s+|is\s+)?|delivery\s*:?\s*)(\d+)\s*(?:-day|day|days|working\s*days?|business\s*days?|week|weeks)?\b",
            text,
            re.IGNORECASE,
        )
        if deliv_match:
            val = int(deliv_match.group(1))
            if "week" in deliv_match.group(0).lower():
                val = val * 7
            proposed_days = val

    deliv_constraint = constraints_by_dim.get("delivery")
    current_deliv_req = None
    if deliv_constraint and deliv_constraint.get("constraints"):
        current_deliv_req = deliv_constraint["constraints"].get("required_days")
    if current_deliv_req is None and rfq:
        current_deliv_req = rfq.get("required_delivery_days")

    if proposed_days is not None:
        is_authorized_deliv = bool(deliv_constraint and deliv_constraint.get("status") == "authorized")
        max_days = deliv_constraint.get("constraints", {}).get("max_days") if deliv_constraint else None

        is_conditional_or_different = (
            has_tradeoff_flag
            or tradeoff_dim == "delivery"
            or (current_deliv_req is not None and proposed_days > current_deliv_req)
            or (max_days is not None and proposed_days > int(max_days))
        )

        if is_conditional_or_different:
            if is_authorized_deliv:
                if max_days is not None and proposed_days <= int(max_days):
                    return {
                        "has_tradeoff": True,
                        "is_authorized": True,
                        "dimension": "delivery",
                        "current_value": current_deliv_req,
                        "supplier_proposed_value": proposed_days,
                    }
                else:
                    return {
                        "has_tradeoff": True,
                        "is_authorized": False,
                        "dimension": "delivery",
                        "current_value": current_deliv_req,
                        "supplier_proposed_value": proposed_days,
                        "reason": f"Supplier proposed {proposed_days}-day delivery exceeding authorized maximum ({max_days} days).",
                    }
            else:
                if current_deliv_req is not None and proposed_days <= current_deliv_req:
                    pass
                elif current_deliv_req is not None:
                    return {
                        "has_tradeoff": True,
                        "is_authorized": False,
                        "dimension": "delivery",
                        "current_value": current_deliv_req,
                        "supplier_proposed_value": proposed_days,
                        "reason": f"Supplier proposed {proposed_days}-day delivery but delivery is fixed at {current_deliv_req} days.",
                    }
                elif has_tradeoff_flag or tradeoff_dim == "delivery":
                    return {
                        "has_tradeoff": True,
                        "is_authorized": False,
                        "dimension": "delivery",
                        "current_value": None,
                        "supplier_proposed_value": proposed_days,
                        "reason": f"Supplier proposed conditional {proposed_days}-day delivery trade-off requiring buyer authorization.",
                    }

    # 2. Check Quantity Trade-off
    proposed_qty = qty_info.get("minimum_order_quantity") or qty_info.get("value") or extracted_quantity
    if proposed_qty is None and text:
        qty_match = re.search(
            r"\b(?:if\s+you\s+(?:can\s+)?(?:take|order|buy)|moq\s*(?:is|of)?|minimum\s+(?:order|quantity)\s*(?:is|of)?)\s*(\d+)\s*(?:pcs|pieces|units|nos)?\b",
            text,
            re.IGNORECASE,
        )
        if qty_match:
            proposed_qty = int(qty_match.group(1))

    qty_constraint = constraints_by_dim.get("quantity")
    current_qty = rfq.get("quantity") if rfq else 20
    if qty_constraint and qty_constraint.get("constraints"):
        current_qty = qty_constraint["constraints"].get("required") or current_qty

    if proposed_qty is not None and (proposed_qty != current_qty or has_tradeoff_flag or tradeoff_dim == "quantity"):
        if qty_constraint and qty_constraint.get("status") == "authorized":
            q_min = qty_constraint.get("constraints", {}).get("min")
            q_max = qty_constraint.get("constraints", {}).get("max")
            if q_min is not None and q_max is not None and int(q_min) <= proposed_qty <= int(q_max):
                return {
                    "has_tradeoff": True,
                    "is_authorized": True,
                    "dimension": "quantity",
                    "current_value": current_qty,
                    "supplier_proposed_value": proposed_qty,
                }
            else:
                return {
                    "has_tradeoff": True,
                    "is_authorized": False,
                    "dimension": "quantity",
                    "current_value": current_qty,
                    "supplier_proposed_value": proposed_qty,
                    "reason": f"Supplier proposed quantity {proposed_qty} outside authorized range [{q_min}, {q_max}].",
                }
        else:
            return {
                "has_tradeoff": True,
                "is_authorized": False,
                "dimension": "quantity",
                "current_value": current_qty,
                "supplier_proposed_value": proposed_qty,
                "reason": f"Supplier proposed quantity {proposed_qty} but quantity is fixed at {current_qty}.",
            }

    # 3. Check Specification Trade-off
    proposed_spec = spec_info.get("alternative") or extracted_variant
    if not proposed_spec and text:
        spec_match = re.search(r"\b(?:alternative|substitute|option|instead\s+of|brand)\s*:\s*([A-Za-z0-9\s]+)", text, re.IGNORECASE)
        if spec_match:
            proposed_spec = spec_match.group(1).strip()

    if proposed_spec:
        spec_constraint = constraints_by_dim.get("specification")
        current_spec = rfq.get("specs") if rfq else "Standard"
        if spec_constraint and spec_constraint.get("status") == "authorized":
            allowed = spec_constraint.get("constraints", {}).get("allowed_alternatives")
            if allowed and (proposed_spec.lower() in str(allowed).lower() or any(str(a).lower() in proposed_spec.lower() for a in (allowed if isinstance(allowed, list) else [allowed]))):
                return {
                    "has_tradeoff": True,
                    "is_authorized": True,
                    "dimension": "specification",
                    "current_value": current_spec,
                    "supplier_proposed_value": proposed_spec,
                }
            else:
                return {
                    "has_tradeoff": True,
                    "is_authorized": False,
                    "dimension": "specification",
                    "current_value": current_spec,
                    "supplier_proposed_value": proposed_spec,
                    "reason": f"Supplier proposed specification '{proposed_spec}' not in authorized alternatives.",
                }
        else:
            return {
                "has_tradeoff": True,
                "is_authorized": False,
                "dimension": "specification",
                "current_value": current_spec,
                "supplier_proposed_value": proposed_spec,
                "reason": f"Supplier proposed alternative specification '{proposed_spec}' but specifications are fixed.",
            }

    # 4. Check Payment Terms Trade-off
    proposed_payment = payment_info.get("text")
    if proposed_payment or (has_tradeoff_flag and tradeoff_dim in ("payment", "payment_terms")):
        return {
            "has_tradeoff": True,
            "is_authorized": False,
            "dimension": "payment",
            "current_value": "Standard",
            "supplier_proposed_value": proposed_payment or "Non-standard payment terms",
            "reason": f"Supplier proposed non-standard payment terms '{proposed_payment or 'conditional'}' requiring human review.",
        }

    return {"has_tradeoff": False, "is_authorized": True}


def build_negotiation_preflight(
    rfq: Dict[str, Any],
    supplier_quote: float,
    previous_quotes: list,
    active_session: Optional[Dict[str, Any]] = None,
    raw_message_text: Optional[str] = None,
    rfq_constraints: Optional[list] = None,
    extracted_delivery: Optional[str] = None,
    extracted_quantity: Optional[int] = None,
    extracted_variant: Optional[str] = None,
    commercial_parse: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds the internal structured preflight plan before reasoning or countering.
    Never exposed directly to the supplier.
    """
    min_price = rfq.get("acceptable_price_min")
    max_price = rfq.get("acceptable_price_max")
    last_quote_val = rfq.get("last_quote")

    hierarchy = compute_target_hierarchy(min_price, max_price, last_quote_val)
    preferred_target = hierarchy["preferred_target"]
    acceptable_max = hierarchy["acceptable_max"]
    tolerated_ceiling = hierarchy["tolerated_final_ceiling"]

    attempts_used = active_session.get("attempt_count", 0) if active_session else 0
    attempts_remaining = max(0, MAX_NEGOTIATION_ATTEMPTS - attempts_used)

    prev_supplier_offer = None
    if active_session and active_session.get("latest_supplier_offer") is not None:
        prev_supplier_offer = float(active_session["latest_supplier_offer"])
    elif previous_quotes:
        prev_supplier_offer = float(previous_quotes[-1]["price"])

    initial_supplier_offer = (
        float(active_session["initial_supplier_offer"])
        if active_session and active_session.get("initial_supplier_offer") is not None
        else supplier_quote
    )

    prev_agent_counter = (
        float(active_session["latest_agent_counter"])
        if active_session and active_session.get("latest_agent_counter") is not None
        else None
    )

    is_final = is_final_price_declared(raw_message_text)
    if active_session and active_session.get("supplier_final_detected"):
        is_final = True
    if commercial_parse and commercial_parse.get("supplier_final"):
        is_final = True

    no_mov_count = active_session.get("no_movement_count", 0) if active_session else 0

    behavior, last_concession, total_concession = classify_supplier_movement(
        initial_offer=initial_supplier_offer,
        previous_offer=prev_supplier_offer,
        latest_offer=supplier_quote,
        latest_agent_counter=prev_agent_counter,
        is_final_declared=is_final,
    )

    bounds = build_allowed_counter_range(
        preferred_target=preferred_target,
        acceptable_max=acceptable_max,
        tolerated_final_ceiling=tolerated_ceiling,
        latest_supplier_offer=supplier_quote,
        previous_supplier_offer=prev_supplier_offer,
        latest_agent_counter=prev_agent_counter,
        attempt_count=attempts_used,
        max_attempts=MAX_NEGOTIATION_ATTEMPTS,
        supplier_final_detected=is_final,
        no_movement_count=no_mov_count,
    )

    tradeoff_eval = evaluate_supplier_tradeoff(
        message_text=raw_message_text,
        rfq_constraints=rfq_constraints,
        rfq=rfq,
        extracted_delivery=extracted_delivery,
        extracted_quantity=extracted_quantity,
        extracted_variant=extracted_variant,
        commercial_parse=commercial_parse,
    )

    should_counter = bounds["should_counter"]
    if tradeoff_eval.get("has_tradeoff") and not tradeoff_eval.get("is_authorized"):
        should_counter = False

    return {
        "primary_objective": "Negotiate commercially optimal price towards preferred target without exceeding acceptable max",
        "preferred_target": preferred_target,
        "acceptable_max": acceptable_max,
        "tolerated_final_ceiling": tolerated_ceiling,
        "initial_supplier_offer": initial_supplier_offer,
        "previous_supplier_offer": prev_supplier_offer,
        "latest_supplier_offer": supplier_quote,
        "previous_agent_counter": prev_agent_counter,
        "attempts_used": attempts_used,
        "attempts_remaining": attempts_remaining,
        "supplier_last_concession": last_concession,
        "supplier_total_concession": total_concession,
        "supplier_behavior": behavior,
        "supplier_final_detected": is_final,
        "selected_strategy": bounds["selected_strategy"],
        "reason_code": bounds["reason_code"],
        "should_counter": should_counter,
        "allowed_counter_min": bounds["allowed_counter_min"],
        "allowed_counter_max": bounds["allowed_counter_max"],
        "recommended_anchor": bounds["recommended_anchor"],
        "can_concede": bounds["can_concede"],
        "tradeoff_evaluation": tradeoff_eval,
    }
