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


@router.post("/supplier-context", dependencies=[])
def supplier_context(
    req: SupplierContextRequest,
    _auth: None = Header(default=None, alias="X-OpenClaw-Auth-Placeholder"),
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
    rows = db.record_quotes_batch(
        rfq_id=req.rfq_id,
        supplier_id=req.supplier_id,
        variants=variants,
        raw_message=req.raw_message,
        source_message_id=req.source_message_id,
        confidence=req.confidence,
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

    active_session = db.get_active_negotiation_session(
        req.client_id,
        req.rfq_id,
        req.supplier_id,
    )
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
        "attempts_used": preflight.get("attempts_used"),
        "attempts_remaining": preflight.get("attempts_remaining"),
        "preferred_target": preflight.get("preferred_target"),
        "acceptable_max": preflight.get("acceptable_max"),
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

    row = db.log_message(
        client_id=req.client_id,
        supplier_id=req.supplier_id,
        direction=direction,
        body=req.body,
        related_rfq_id=req.related_rfq_id,
        status=req.status,
        event_key=req.event_key,
    )
    return {"logged": bool(row), "message": row}
