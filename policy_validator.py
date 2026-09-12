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
        raw_price = args.get("price")

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

        # Validate numeric price
        try:
            price = float(raw_price)
            if math.isnan(price) or math.isinf(price) or price <= 0:
                return ValidationResult(
                    is_valid=False,
                    action=tool_name,
                    category=ActionCategory.MUTATION,
                    reason=f"Invalid price value '{raw_price}'. Price must be a positive number.",
                )
        except (ValueError, TypeError):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.MUTATION,
                reason=f"Non-numeric price '{raw_price}' provided.",
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
            # Query DB directly
            rfq_res = db.supabase.table("rfqs").select("*").eq("id", rfq_id).execute()
            if rfq_res.data:
                target_rfq = rfq_res.data[0]

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

        # Lifecycle & Deadline check
        if not db.is_rfq_open(target_rfq):
            return ValidationResult(
                is_valid=False,
                action=tool_name,
                category=ActionCategory.MUTATION,
                reason=f"RFQ '{rfq_id}' is closed or deadline has passed.",
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
            category=ActionCategory.MUTATION,
            sanitized_args={
                "rfq_id": rfq_id,
                "price": price,
                "delivery_time": sanitized_delivery,
                "quality_notes": sanitized_notes,
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
