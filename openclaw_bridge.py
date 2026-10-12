"""
Authenticated OpenClaw bridge for the procurement backend.

OpenClaw owns supplier conversation flow. This router exposes a small,
deterministic tool surface over the existing procurement logic and Supabase
state. It intentionally does not expose arbitrary SQL or direct WhatsApp send.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

import db
import negotiation_engine


router = APIRouter(prefix="/openclaw", tags=["openclaw"])


def _require_openclaw_key(x_openclaw_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("OPENCLAW_TOOL_API_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="OPENCLAW_TOOL_API_KEY is not configured on the backend.",
        )
    if not x_openclaw_key or not hmac.compare_digest(x_openclaw_key, expected):
        raise HTTPException(status_code=401, detail="Invalid OpenClaw tool key.")


def _get_supplier_for_client(client_id: str, supplier_id: Optional[str], phone_number: Optional[str]) -> dict:
    supplier = None
    if supplier_id:
        res = (
            db.supabase.table("suppliers")
            .select("*")
            .eq("id", supplier_id)
            .eq("client_id", client_id)
            .limit(1)
            .execute()
        )
        supplier = res.data[0] if res.data else None
    elif phone_number:
        supplier = db.get_supplier_by_phone(client_id, phone_number)

    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found for this client.")
    return supplier


def _get_rfq_and_verify_link(client_id: str, supplier_id: str, rfq_id: str) -> dict:
    rfq = db.get_rfq_by_id(rfq_id, client_id=client_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found for this client.")

    link = (
        db.supabase.table("rfq_suppliers")
        .select("id,status,negotiation_attempts")
        .eq("rfq_id", rfq_id)
        .eq("supplier_id", supplier_id)
        .limit(1)
        .execute()
    )
    if not link.data:
        raise HTTPException(status_code=403, detail="Supplier is not linked to this RFQ.")
    return rfq


class SupplierContextRequest(BaseModel):
    client_id: str
    supplier_id: Optional[str] = None
    phone_number: Optional[str] = None
    history_limit: int = Field(default=20, ge=1, le=50)


class QuoteVariant(BaseModel):
    variant_label: Optional[str] = None
    price: Optional[float] = None
    delivery_time: Optional[str] = None
    quality_notes: Optional[str] = None
    quantity: Optional[int] = None
    is_available: bool = True


class RecordQuoteRequest(BaseModel):
    client_id: str
    supplier_id: str
    rfq_id: str
    variants: list[QuoteVariant]
    raw_message: Optional[str] = None
    source_message_id: Optional[str] = None
    confidence: str = "high"


class MarkUnavailableRequest(BaseModel):
    client_id: str
    supplier_id: str
    rfq_id: str
    raw_message: Optional[str] = None
    source_message_id: Optional[str] = None
    reason: Optional[str] = None


class NegotiationDecisionRequest(BaseModel):
    client_id: str
    supplier_id: str
    rfq_id: str
    supplier_quote: float = Field(gt=0)
    raw_message: Optional[str] = None
    extracted_delivery: Optional[str] = None
    extracted_quantity: Optional[int] = None
    extracted_variant: Optional[str] = None


class HumanAttentionRequest(BaseModel):
    client_id: str
    supplier_id: str
    rfq_id: Optional[str] = None
    reason: str
    category: str = "other"
    raw_message: str = ""
    metadata: dict = Field(default_factory=dict)


class LogMessageRequest(BaseModel):
    client_id: str
    supplier_id: str
    direction: str
    body: str
    related_rfq_id: Optional[str] = None
    status: Optional[str] = None
    event_key: Optional[str] = None
    external_message_id: Optional[str] = None


class AcceptNegotiatedQuoteRequest(BaseModel):
    client_id: str
    supplier_id: str
    rfq_id: str
    price: Optional[float] = Field(default=None, gt=0)
    quantity: Optional[int] = Field(default=None, gt=0)
    accepted_by: str = "openclaw"


class MessageDeliveryRequest(BaseModel):
    client_id: str
    supplier_id: Optional[str] = None
    phone_number: Optional[str] = None
    body: str
    success: bool
    external_message_id: Optional[str] = None
    related_rfq_id: Optional[str] = None
    error: Optional[str] = None


@router.post("/supplier-context", dependencies=[])
def supplier_context(
    req: SupplierContextRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    _require_openclaw_key(x_openclaw_key)
    supplier = _get_supplier_for_client(req.client_id, req.supplier_id, req.phone_number)

    open_links = db.get_open_rfqs_for_supplier(supplier["id"])
    open_rfqs = []
    for link in open_links:
        rfq = link.get("rfqs") or {}
        if rfq.get("client_id") != req.client_id:
            continue
        open_rfqs.append(
            {
                "rfq_id": rfq.get("id"),
                "product_name": rfq.get("product_name"),
                "specs": rfq.get("specs"),
                "quantity": rfq.get("quantity"),
                "category": rfq.get("category"),
                "last_quote": rfq.get("last_quote"),
                "acceptable_price_min": rfq.get("acceptable_price_min"),
                "acceptable_price_max": rfq.get("acceptable_price_max"),
                "required_delivery_days": rfq.get("required_delivery_days"),
                "due_by": rfq.get("due_by"),
                "supplier_status": link.get("status"),
                "negotiation_attempts": link.get("negotiation_attempts") or 0,
            }
        )

    history = db.get_supplier_conversation_history(
        req.client_id,
        supplier["id"],
        limit=req.history_limit,
    )
    active_session = db.get_active_negotiation_session_for_supplier(
        req.client_id,
        supplier["id"],
    )

    return {
        "supplier": {
            "id": supplier.get("id"),
            "name": supplier.get("name"),
            "phone_number": supplier.get("phone_number"),
            "categories": supplier.get("categories"),
            "notes": supplier.get("notes"),
        },
        "automation_paused": db.is_client_automation_paused(req.client_id),
        "active_rfqs": open_rfqs,
        "conversation_history": history,
        "active_negotiation_session": active_session,
    }


@router.post("/record-quote")
def record_quote(req: RecordQuoteRequest, x_openclaw_key: Optional[str] = Header(default=None)):
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)

    variants = [variant.model_dump() for variant in req.variants]
    allow_post_deadline = db.can_record_post_deadline_quote(
        req.client_id,
        req.rfq_id,
        req.supplier_id,
    )
    rows = db.record_quotes_batch(
        rfq_id=req.rfq_id,
        supplier_id=req.supplier_id,
        variants=variants,
        raw_message=req.raw_message,
        source_message_id=req.source_message_id,
        confidence=req.confidence,
        allow_post_deadline=allow_post_deadline,
    )
    if not rows:
        raise HTTPException(status_code=409, detail="Quote was not recorded.")
    return {"recorded": True, "quotes": rows}


@router.post("/mark-unavailable")
def mark_unavailable(req: MarkUnavailableRequest, x_openclaw_key: Optional[str] = Header(default=None)):
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)

    row = db.record_quote(
        rfq_id=req.rfq_id,
        supplier_id=req.supplier_id,
        price=None,
        quality_notes=req.reason or "Supplier reported unavailable / no stock.",
        raw_message=req.raw_message,
        confidence="high",
        is_available=False,
        source_message_id=req.source_message_id,
    )
    if not row:
        raise HTTPException(status_code=409, detail="Unavailable state was not recorded.")
    return {"recorded": True, "quote": row}


@router.post("/negotiation-decision")
def negotiation_decision(
    req: NegotiationDecisionRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    rfq = _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)

    active_session = db.get_active_negotiation_session(
        req.client_id,
        req.rfq_id,
        req.supplier_id,
    )

    # Existing negotiations may finish after due_by. A brand-new negotiation
    # may not begin after the RFQ deadline.
    if not db.is_rfq_open(rfq) and not active_session:
        raise HTTPException(
            status_code=409,
            detail="RFQ deadline passed and no in-flight negotiation exists for this supplier.",
        )

    quote_rows = (
        db.supabase.table("quotes")
        .select("price,created_at")
        .eq("rfq_id", req.rfq_id)
        .eq("supplier_id", req.supplier_id)
        .eq("is_available", True)
        .order("created_at")
        .execute()
    )
    previous_quotes = [
        row for row in (quote_rows.data or [])
        if row.get("price") is not None and float(row["price"]) > 0
    ]

    constraints = db.get_rfq_negotiation_constraints(req.client_id, req.rfq_id)

    preflight = negotiation_engine.build_negotiation_preflight(
        rfq=rfq,
        supplier_quote=req.supplier_quote,
        previous_quotes=previous_quotes,
        active_session=active_session,
        raw_message_text=req.raw_message,
        rfq_constraints=constraints,
        extracted_delivery=req.extracted_delivery,
        extracted_quantity=req.extracted_quantity,
        extracted_variant=req.extracted_variant,
    )

    requires_human = bool(
        preflight.get("supplier_final_detected")
        or (
            preflight.get("tradeoff_evaluation", {}).get("has_tradeoff")
            and not preflight.get("tradeoff_evaluation", {}).get("is_authorized")
        )
        or preflight.get("selected_strategy") == "ESCALATE"
    )

    attempt_count = int(active_session.get("attempt_count") or 0) if active_session else 0
    latest_counter = active_session.get("latest_agent_counter") if active_session else None
    previous_counter = active_session.get("previous_agent_counter") if active_session else None

    if preflight.get("should_counter"):
        attempt_count += 1
        previous_counter = latest_counter
        latest_counter = preflight.get("recommended_anchor")
        db.supabase.table("rfq_suppliers").update(
            {"negotiation_attempts": attempt_count}
        ).eq("rfq_id", req.rfq_id).eq("supplier_id", req.supplier_id).execute()

    session_status = "awaiting_human_review" if requires_human else "active"
    session = db.create_or_update_negotiation_session(
        req.client_id,
        req.rfq_id,
        req.supplier_id,
        status=session_status,
        preferred_target=preflight.get("preferred_target"),
        acceptable_max=preflight.get("acceptable_max"),
        tolerated_final_ceiling=preflight.get("tolerated_final_ceiling"),
        initial_supplier_offer=preflight.get("initial_supplier_offer"),
        previous_supplier_offer=preflight.get("previous_supplier_offer"),
        latest_supplier_offer=preflight.get("latest_supplier_offer"),
        previous_agent_counter=previous_counter,
        latest_agent_counter=latest_counter,
        attempt_count=attempt_count,
        supplier_total_concession=preflight.get("supplier_total_concession") or 0,
        supplier_last_concession=preflight.get("supplier_last_concession") or 0,
        supplier_final_detected=bool(preflight.get("supplier_final_detected")),
        selected_strategy=preflight.get("selected_strategy"),
        strategy_phase=preflight.get("reason_code"),
    )
    if not session:
        raise HTTPException(status_code=500, detail="Negotiation decision could not be persisted.")

    return {
        "action": "counter" if preflight.get("should_counter") else "stop",
        "requires_human": requires_human,
        "counter_price": preflight.get("recommended_anchor") if preflight.get("should_counter") else None,
        "strategy": preflight.get("selected_strategy"),
        "reason_code": preflight.get("reason_code"),
        "allowed_counter_min": preflight.get("allowed_counter_min"),
        "allowed_counter_max": preflight.get("allowed_counter_max"),
        "supplier_final_detected": preflight.get("supplier_final_detected"),
        "tradeoff_evaluation": preflight.get("tradeoff_evaluation"),
        "attempts_used": attempt_count,
        "attempts_remaining": max(0, 10 - attempt_count),
        "preferred_target": preflight.get("preferred_target"),
        "acceptable_max": preflight.get("acceptable_max"),
        "negotiation_session_id": session.get("id"),
        "persisted": True,
    }


@router.post("/human-attention")
def human_attention(
    req: HumanAttentionRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    if req.rfq_id:
        _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)

    rows = db.flag_for_human_review(
        client_id=req.client_id,
        supplier_id=req.supplier_id,
        rfq_id=req.rfq_id,
        reason=req.reason,
        category=req.category,
        raw_message=req.raw_message,
        metadata=req.metadata,
    )
    return {"created": bool(rows), "flags": rows or []}


@router.post("/log-message")
def log_message(req: LogMessageRequest, x_openclaw_key: Optional[str] = Header(default=None)):
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)

    direction = req.direction.strip().lower()
    if direction not in {"inbound", "outbound"}:
        raise HTTPException(status_code=422, detail="direction must be inbound or outbound")

    row_id = db.log_message(
        client_id=req.client_id,
        supplier_id=req.supplier_id,
        direction=direction,
        body=req.body,
        related_rfq_id=req.related_rfq_id,
        status=req.status,
        event_key=req.event_key,
    )
    if row_id and direction == "outbound" and req.external_message_id:
        db.mark_message_sent(row_id, external_message_id=req.external_message_id)
    return {"logged": bool(row_id), "message_id": row_id}


@router.post("/accept-negotiated-quote")
def accept_negotiated_quote(
    req: AcceptNegotiatedQuoteRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)

    accepted = db.accept_recorded_quote(
        req.client_id,
        req.rfq_id,
        req.supplier_id,
        price=req.price,
        quantity=req.quantity,
        accepted_by=req.accepted_by,
    )
    if not accepted:
        raise HTTPException(
            status_code=409,
            detail="Quote cannot be accepted: no matching persisted quote or no valid in-flight negotiation.",
        )
    return {"accepted": True, "quote": accepted}


@router.post("/message-delivery")
def message_delivery(
    req: MessageDeliveryRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    _require_openclaw_key(x_openclaw_key)
    supplier = _get_supplier_for_client(req.client_id, req.supplier_id, req.phone_number)

    row = db.find_latest_message_by_body(
        client_id=req.client_id,
        supplier_id=supplier["id"],
        direction="outbound",
        body=req.body,
        related_rfq_id=req.related_rfq_id,
    )
    if row:
        row_id = row.get("id")
    else:
        row_id = db.log_message(
            client_id=req.client_id,
            supplier_id=supplier["id"],
            direction="outbound",
            body=req.body,
            related_rfq_id=req.related_rfq_id,
            status="sent" if req.success else "failed",
        )

    if row_id:
        if req.success:
            db.mark_message_sent(row_id, external_message_id=req.external_message_id)
        else:
            db.mark_message_failed(row_id, req.error or "OpenClaw delivery failed")

    return {"updated": bool(row_id), "message_log_id": row_id}
