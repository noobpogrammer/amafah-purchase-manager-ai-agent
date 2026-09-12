"""
policy_validator.py
Deterministic Policy & Validator Layer between LLM proposed actions and database/outbound execution.

Architecture:
Supplier message -> LLM interpretation -> Structured Action Proposal -> Policy & Validator Layer -> Approved / Rejected action -> Deterministic execution
"""

import math
import os
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

import db
import guardrails


class ActionCategory(str, Enum):
    READ = "READ"
    PROPOSE_COMMUNICATE = "PROPOSE_COMMUNICATE"
    MUTATION = "MUTATION"
    HIGH_RISK = "HIGH_RISK"


class ActionProposal(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = None
    raw_message: Optional[str] = None


class ValidationResult(BaseModel):
    is_valid: bool
    action: str
    category: ActionCategory
    reason: Optional[str] = None
    sanitized_args: Dict[str, Any] = Field(default_factory=dict)


MAX_NEGOTIATION_ATTEMPTS = int(os.environ.get("MAX_NEGOTIATION_ATTEMPTS", 3))

HIGH_RISK_ACTIONS = {
    "close_rfq",
    "accept_quote",
    "generate_ranking",
    "save_ranking",
    "delete_quote",
    "reopen_rfq",
}


def validate_action(
    proposal: ActionProposal,
    client_id: str,
    supplier_id: str,
    context_rfqs: Optional[List[Dict[str, Any]]] = None,
    matched_rfq_id: Optional[str] = None,
) -> ValidationResult:
    """
    Deterministic validation of an LLM action proposal.
    Guarantees that no mutation or high-risk action can execute without passing strict policy rules.
    Confidence scores never override policy constraints.
    """
    tool_name = (proposal.tool_name or "").strip()

    # 1. High-risk actions must NEVER be executed purely by LLM proposal
    if tool_name in HIGH_RISK_ACTIONS:
        return ValidationResult(
            is_valid=False,
            action=tool_name,
            category=ActionCategory.HIGH_RISK,
            reason=f"Action '{tool_name}' is high-risk and cannot be executed autonomously by LLM proposal.",
        )

    # 2. Validate READ actions
    if tool_name == "get_supplier_history":
        req_supplier_id = proposal.arguments.get("supplier_id")
        if req_supplier_id and req_supplier_id != supplier_id:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.READ,
                reason="Cross-supplier history access forbidden.",
            )
        return ValidationResult(
            is_valid=True,
            action=tool_name,
            category=ActionCategory.READ,
            sanitized_args={"supplier_id": supplier_id},
        )

    # 3. Validate MUTATION: record_quote
    if tool_name == "record_quote":
        args = proposal.arguments or {}
        rfq_id = args.get("rfq_id")

        if not rfq_id or not isinstance(rfq_id, str):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.MUTATION,
                reason="Missing or invalid rfq_id in record_quote proposal.",
            )

        # Deterministic stanza lock: proposal cannot switch away from matched RFQ
        if matched_rfq_id and rfq_id != matched_rfq_id:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.MUTATION,
                reason=f"Proposed RFQ '{rfq_id}' does not match deterministically locked RFQ '{matched_rfq_id}'.",
            )

        # Backward compatibility / legacy adapter: normalize single price into variants list
        variants_raw = args.get("variants")
        if variants_raw is None and "price" in args:
            variants_raw = [{
                "variant_label": args.get("variant_label"),
                "price": args.get("price"),
                "delivery_time": args.get("delivery_time"),
                "quality_notes": args.get("quality_notes"),
                "is_available": args.get("is_available", True),
            }]

        if not isinstance(variants_raw, list) or len(variants_raw) < 1:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.MUTATION,
                reason="record_quote requires a non-empty list of variants (1-10 items).",
            )

        if len(variants_raw) > 10:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.MUTATION,
                reason=f"Exceeded maximum variant limit: {len(variants_raw)} variants proposed (max 10 allowed).",
            )

        sanitized_variants = []
        seen_normalized_labels = set()

        for idx, item in enumerate(variants_raw):
            if not isinstance(item, dict):
                return ValidationResult(
                    is_valid=False,
                    action=tool_name,
                    category=ActionCategory.MUTATION,
                    reason=f"Variant at index {idx} must be an object/dict.",
                )

            # 1. Sanitize & validate variant_label
            raw_label = item.get("variant_label")
            label = None
            if raw_label is not None:
                if not isinstance(raw_label, str):
                    raw_label = str(raw_label)
                # Check for raw control characters
                if any(ord(c) < 32 and c not in ('\t', '\n', '\r') for c in raw_label):
                    return ValidationResult(
                        is_valid=False,
                        action=tool_name,
                        category=ActionCategory.MUTATION,
                        reason=f"Variant label '{raw_label}' contains illegal control characters.",
                    )
                label_clean = raw_label.strip()
                if len(label_clean) > 100:
                    return ValidationResult(
                        is_valid=False,
                        action=tool_name,
                        category=ActionCategory.MUTATION,
                        reason=f"Variant label '{label_clean[:30]}...' exceeds maximum length of 100 characters.",
                    )
                if label_clean != "":
                    label = label_clean

            norm_key = (label or "").strip().casefold()
            if norm_key in seen_normalized_labels:
                return ValidationResult(
                    is_valid=False,
                    action=tool_name,
                    category=ActionCategory.MUTATION,
                    reason=f"Duplicate normalized variant label '{label or 'default'}' in single quote batch. Ambiguity rejected.",
                )
            seen_normalized_labels.add(norm_key)

            # 2. Validate is_available
            raw_avail = item.get("is_available")
            if raw_avail is None:
                is_avail = True
            elif isinstance(raw_avail, bool):
                is_avail = raw_avail
            elif isinstance(raw_avail, str) and raw_avail.lower() in ("true", "1"):
                is_avail = True
            elif isinstance(raw_avail, str) and raw_avail.lower() in ("false", "0"):
                is_avail = False
            else:
                return ValidationResult(
                    is_valid=False,
                    action=tool_name,
                    category=ActionCategory.MUTATION,
                    reason=f"Invalid is_available boolean '{raw_avail}' for variant '{label or 'default'}'.",
                )

            # 3. Validate price
            raw_price = item.get("price")
            price_val = None
            if is_avail:
                if raw_price is None:
                    return ValidationResult(
                        is_valid=False,
                        action=tool_name,
                        category=ActionCategory.MUTATION,
                        reason=f"Missing price for available variant '{label or 'default'}'. Available variants require a positive price.",
                    )
                try:
                    price_val = float(raw_price)
                    if math.isnan(price_val) or math.isinf(price_val) or price_val <= 0:
                        return ValidationResult(
                            is_valid=False,
                            action=tool_name,
                            category=ActionCategory.MUTATION,
                            reason=f"Invalid price value '{raw_price}' for variant '{label or 'default'}'. Price must be a positive number.",
                        )
                except (ValueError, TypeError):
                    return ValidationResult(
                        is_valid=False,
                        action=tool_name,
                        category=ActionCategory.MUTATION,
                        reason=f"Non-numeric price '{raw_price}' for variant '{label or 'default'}'.",
                    )
            else:
                # For withdrawn/unavailable variants, price is optional
                if raw_price is not None:
                    try:
                        price_val = float(raw_price)
                        if math.isnan(price_val) or math.isinf(price_val) or price_val <= 0:
                            price_val = None
                    except (ValueError, TypeError):
                        price_val = None

            # 4. Delivery & notes bounded safe strings
            deliv = item.get("delivery_time")
            if deliv is not None and not isinstance(deliv, str):
                deliv = str(deliv)
            if deliv and len(deliv) > 500:
                deliv = deliv[:500]

            notes = item.get("quality_notes")
            if notes is not None and not isinstance(notes, str):
                notes = str(notes)
            if notes and len(notes) > 500:
                notes = notes[:500]

            sanitized_variants.append({
                "variant_label": label,
                "price": price_val,
                "delivery_time": deliv,
                "quality_notes": notes,
                "is_available": is_avail,
            })

        # Validate RFQ against context / DB
        target_rfq = None
        target_rfq_supplier = None

        if context_rfqs:
            for item in context_rfqs:
                rfq_obj = item.get("rfqs") if (isinstance(item, dict) and "rfqs" in item) else item
                if isinstance(rfq_obj, dict) and rfq_obj.get("id") == rfq_id:
                    target_rfq = rfq_obj
                    target_rfq_supplier = item if (isinstance(item, dict) and "rfqs" in item) else None
                    break

        if not target_rfq:
            try:
                rfq_res = db.supabase.table("rfqs").select("*").eq("id", rfq_id).execute()
                if rfq_res.data:
                    target_rfq = rfq_res.data[0]
            except Exception as e:
                logger.debug(f"Direct RFQ lookup failed in validator: {e}")

        if not target_rfq:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.MUTATION,
                reason=f"Target RFQ '{rfq_id}' does not exist.",
            )

        # Tenant isolation check
        rfq_client_id = target_rfq.get("client_id")
        if rfq_client_id and rfq_client_id != client_id:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.MUTATION,
                reason=f"Cross-tenant violation: RFQ '{rfq_id}' does not belong to client '{client_id}'.",
            )

        # Supplier relationship check
        if not target_rfq_supplier:
            try:
                rs_res = (
                    db.supabase.table("rfq_suppliers")
                    .select("*")
                    .eq("rfq_id", rfq_id)
                    .eq("supplier_id", supplier_id)
                    .execute()
                )
                if not rs_res.data:
                    return ValidationResult(
                        is_valid=False,
                        action=tool_name,
                        category=ActionCategory.MUTATION,
                        reason=f"Supplier '{supplier_id}' is not associated with RFQ '{rfq_id}'.",
                    )
                target_rfq_supplier = rs_res.data[0]
            except Exception as e:
                logger.debug(f"Direct supplier-RFQ lookup failed in validator: {e}")

        # Lifecycle & Deadline check
        if not db.is_rfq_open(target_rfq):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.MUTATION,
                reason=f"RFQ '{rfq_id}' is closed or deadline has passed.",
            )

        return ValidationResult(
            is_valid=True,
            action=tool_name,
            category=ActionCategory.MUTATION,
            sanitized_args={
                "rfq_id": rfq_id,
                "variants": sanitized_variants,
                # Legacy compatibility fields
                "price": sanitized_variants[0]["price"] if sanitized_variants else None,
                "delivery_time": sanitized_variants[0].get("delivery_time") if sanitized_variants else None,
                "quality_notes": sanitized_variants[0].get("quality_notes") if sanitized_variants else None,
            },
        )

    # 4. Validate PROPOSE_COMMUNICATE: request_clarification
    if tool_name == "request_clarification":
        args = proposal.arguments or {}
        candidate_ids = args.get("candidate_rfq_ids") or []
        question = args.get("clarifying_question", "")

        if not isinstance(candidate_ids, list) or not candidate_ids:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason="Missing or empty candidate_rfq_ids for clarification request.",
            )

        # Deterministic stanza lock: clarification candidates must strictly be [matched_rfq_id]
        if matched_rfq_id and candidate_ids != [matched_rfq_id]:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason=f"Clarification candidates violate deterministic RFQ match lock (expected '{matched_rfq_id}', got {candidate_ids}).",
            )

        if not question or not isinstance(question, str):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason="Missing clarifying question text.",
            )

        # Output safety guardrail check
        if not guardrails.is_safe_to_send(question):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason="AI generated non-compliant or code response caught by code-level safety guardrail",
            )

        return ValidationResult(
            is_valid=True,
            action=tool_name,
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={
                "candidate_rfq_ids": candidate_ids,
                "clarifying_question": question,
                "extracted_price": args.get("extracted_price"),
                "extracted_delivery": args.get("extracted_delivery"),
                "extracted_notes": args.get("extracted_notes"),
            },
        )

    # 5. Validate PROPOSE_COMMUNICATE: escalate_to_human
    if tool_name == "escalate_to_human":
        args = proposal.arguments or {}
        reason = args.get("reason", "Human review requested by agent")
        category = args.get("category", "other")
        rfq_id = args.get("rfq_id")

        # Deterministic stanza lock: proposal cannot escalate against another RFQ when locked
        if matched_rfq_id:
            if rfq_id and rfq_id != matched_rfq_id:
                return ValidationResult(
                    is_valid=False,
                    action=tool_name,
                    category=ActionCategory.PROPOSE_COMMUNICATE,
                    reason=f"Escalation RFQ '{rfq_id}' does not match deterministically locked RFQ '{matched_rfq_id}'.",
                )
            # If missing or matching, lock to matched_rfq_id
            rfq_id = matched_rfq_id

        allowed_categories = {
            "requires_business_knowledge",
            "unclear_intent",
            "contradictory_information",
            "other",
        }
        if category not in allowed_categories:
            category = "other"

        return ValidationResult(
            is_valid=True,
            action=tool_name,
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={
                "rfq_id": rfq_id,
                "reason": str(reason),
                "category": category,
            },
        )

    # 6. Validate PROPOSE_COMMUNICATE / MUTATION: negotiate_price
    if tool_name == "negotiate_price":
        args = proposal.arguments or {}
        rfq_id = args.get("rfq_id")
        raw_price = args.get("quoted_price") if "quoted_price" in args else args.get("price")
        neg_msg = args.get("negotiation_message")

        if not rfq_id or not isinstance(rfq_id, str):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason="Missing or invalid rfq_id in negotiate_price proposal.",
            )

        # Deterministic stanza lock: proposal cannot switch away from matched RFQ
        if matched_rfq_id and rfq_id != matched_rfq_id:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason=f"Proposed RFQ '{rfq_id}' does not match deterministically locked RFQ '{matched_rfq_id}'.",
            )

        # Validate numeric price
        try:
            price = float(raw_price)
            if math.isnan(price) or math.isinf(price) or price <= 0:
                return ValidationResult(
                    is_valid=False,
                    action=tool_name,
                    category=ActionCategory.PROPOSE_COMMUNICATE,
                    reason=f"Invalid quoted_price value '{raw_price}'. Price must be a positive number.",
                )
        except (ValueError, TypeError):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason=f"Non-numeric quoted_price '{raw_price}' provided for negotiation.",
            )

        # Validate negotiation message
        if not neg_msg or not isinstance(neg_msg, str) or not neg_msg.strip():
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason="Missing or empty negotiation_message.",
            )

        if not guardrails.is_safe_to_send(neg_msg):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason="AI generated non-compliant or unsafe negotiation message caught by guardrails.",
            )

        # Validate RFQ against context / DB
        target_rfq = None
        target_rfq_supplier = None

        if context_rfqs:
            for item in context_rfqs:
                rfq_obj = item.get("rfqs") if (isinstance(item, dict) and "rfqs" in item) else item
                if isinstance(rfq_obj, dict) and rfq_obj.get("id") == rfq_id:
                    target_rfq = rfq_obj
                    target_rfq_supplier = item if (isinstance(item, dict) and "rfqs" in item) else None
                    break

        if not target_rfq:
            try:
                rfq_res = db.supabase.table("rfqs").select("*").eq("id", rfq_id).execute()
                if rfq_res.data:
                    target_rfq = rfq_res.data[0]
            except Exception:
                pass

        if not target_rfq:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason=f"Target RFQ '{rfq_id}' does not exist.",
            )

        # Tenant isolation check
        rfq_client_id = target_rfq.get("client_id")
        if rfq_client_id and rfq_client_id != client_id:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason=f"Cross-tenant violation: RFQ '{rfq_id}' does not belong to client '{client_id}'.",
            )

        # Supplier relationship check
        if not target_rfq_supplier:
            rs_res = (
                db.supabase.table("rfq_suppliers")
                .select("*")
                .eq("rfq_id", rfq_id)
                .eq("supplier_id", supplier_id)
                .execute()
            )
            if not rs_res.data:
                return ValidationResult(
                    is_valid=False,
                    action=tool_name,
                    category=ActionCategory.PROPOSE_COMMUNICATE,
                    reason=f"Supplier '{supplier_id}' is not associated with RFQ '{rfq_id}'.",
                )
            target_rfq_supplier = rs_res.data[0]

        # Lifecycle & Deadline check
        if not db.is_rfq_open(target_rfq):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason=f"RFQ '{rfq_id}' is closed or deadline has passed.",
            )

        # Negotiation attempt limit check
        attempts = db.get_negotiation_attempts(rfq_id, supplier_id)
        if not isinstance(attempts, (int, float)):
            attempts = 0
        if attempts >= MAX_NEGOTIATION_ATTEMPTS:
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason=f"Negotiation attempt limit reached ({attempts}/{MAX_NEGOTIATION_ATTEMPTS}).",
            )

        sanitized_delivery = args.get("delivery_time")
        if sanitized_delivery is not None and not isinstance(sanitized_delivery, str):
            sanitized_delivery = str(sanitized_delivery)

        sanitized_notes = args.get("quality_notes")
        if sanitized_notes is not None and not isinstance(sanitized_notes, str):
            sanitized_notes = str(sanitized_notes)

        return ValidationResult(
            is_valid=True,
            action=tool_name,
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={
                "rfq_id": rfq_id,
                "quoted_price": price,
                "negotiation_message": neg_msg.strip(),
                "delivery_time": sanitized_delivery,
                "quality_notes": sanitized_notes,
            },
        )

    # 7. Validate PROPOSE_COMMUNICATE: send_procurement_message
    if tool_name == "send_procurement_message":
        args = proposal.arguments or {}
        msg = args.get("message")
        rfq_id = args.get("rfq_id")

        if not msg or not isinstance(msg, str) or not msg.strip():
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason="Missing or empty message in send_procurement_message proposal.",
            )

        # Deterministic stanza/operator lock: message cannot target another RFQ
        if matched_rfq_id:
            if rfq_id and rfq_id != matched_rfq_id:
                return ValidationResult(
                    is_valid=False,
                    action=tool_name,
                    category=ActionCategory.PROPOSE_COMMUNICATE,
                    reason=f"Procurement message RFQ '{rfq_id}' does not match deterministically locked RFQ '{matched_rfq_id}'.",
                )
            rfq_id = matched_rfq_id

        # Safety guardrails check
        if not guardrails.is_safe_to_send(msg):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.PROPOSE_COMMUNICATE,
                reason="AI generated non-compliant or unsafe message caught by guardrails.",
            )

        # Validate RFQ against context / DB if provided
        if rfq_id:
            target_rfq = None
            if context_rfqs:
                for item in context_rfqs:
                    rfq_obj = item.get("rfqs") if (isinstance(item, dict) and "rfqs" in item) else item
                    if isinstance(rfq_obj, dict) and rfq_obj.get("id") == rfq_id:
                        target_rfq = rfq_obj
                        break
            if not target_rfq:
                try:
                    rfq_res = db.supabase.table("rfqs").select("*").eq("id", rfq_id).execute()
                    if rfq_res.data:
                        target_rfq = rfq_res.data[0]
                except Exception:
                    pass

            if target_rfq:
                rfq_client_id = target_rfq.get("client_id")
                if rfq_client_id and rfq_client_id != client_id:
                    return ValidationResult(
                        is_valid=False,
                        action=tool_name,
                        category=ActionCategory.PROPOSE_COMMUNICATE,
                        reason=f"Cross-tenant violation: RFQ '{rfq_id}' does not belong to client '{client_id}'.",
                    )

        return ValidationResult(
            is_valid=True,
            action=tool_name,
            category=ActionCategory.PROPOSE_COMMUNICATE,
            sanitized_args={
                "rfq_id": rfq_id,
                "message": msg.strip(),
            },
        )

    # 8. Default reject any unsupported tool
    return ValidationResult(
        is_valid=False,
        action=tool_name,
        category=ActionCategory.MUTATION,
        reason=f"Unrecognized action '{tool_name}'.",
    )
