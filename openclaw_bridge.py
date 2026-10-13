"""
Authenticated OpenClaw bridge for the procurement backend.

OpenClaw owns supplier conversation flow. This router exposes a small,
deterministic tool surface over the existing procurement logic and Supabase
state. It intentionally does not expose arbitrary SQL or direct WhatsApp send.
"""

from __future__ import annotations

import hmac
import os
import re
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

import db
import negotiation_engine
import groq_client
from supplier_message_planner import validate_multi_rfq_quotes, AmbiguousSupplierQuote


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


def _latest_supplier_bundle_rfqs(client_id: str, supplier_id: str) -> dict:
    """Latest delivered buyer invitation; never infer its RFQs from chat memory."""
    bundles = (db.supabase.table("supplier_message_bundles")
        .select("id,external_message_id,created_at")
        .eq("client_id", client_id).eq("supplier_id", supplier_id)
        .eq("kind", "initial_rfq").eq("status", "sent")
        .order("created_at", desc=True).limit(1).execute())
    if not bundles.data:
        return {"bundle_id": None, "rfq_ids": [], "external_message_id": None}
    bundle = bundles.data[0]
    rows = (db.supabase.table("supplier_message_bundle_items").select("rfq_id")
        .eq("client_id", client_id).eq("supplier_id", supplier_id)
        .eq("bundle_id", bundle["id"]).execute())
    return {"bundle_id": bundle["id"],
            "rfq_ids": list({r["rfq_id"] for r in (rows.data or [])}),
            "external_message_id": bundle.get("external_message_id")}


def _require_current_bundle_rfq(client_id: str, supplier_id: str, rfq_id: str):
    """Fail closed when same supplier has older active duplicate test RFQs.

    Non-bundled legacy suppliers continue through existing authorization.
    """
    bundle = _latest_supplier_bundle_rfqs(client_id, supplier_id)
    if bundle["rfq_ids"] and rfq_id not in bundle["rfq_ids"]:
        raise HTTPException(status_code=409,
            detail="RFQ is not in supplier's latest delivered invitation bundle. Resolve the latest reference or request clarification.")
    return bundle


def _historical_price_benchmarks(client_id: str, rfq: dict) -> list[dict]:
    """Advisory-only cross-supplier comparisons, excluding current RFQ."""
    import re as _re
    def norm(value):
        return " ".join(_re.findall(r"[a-z0-9]+", str(value or "").lower()))
    target_name = norm(rfq.get("product_name"))
    target_specs = norm(rfq.get("specs"))
    if not target_name:
        return []
    prior = (db.supabase.table("rfqs")
        .select("id,product_name,specs,quantity,required_delivery_days,acceptable_price_min,acceptable_price_max,created_at")
        .eq("client_id", client_id).neq("id", rfq["id"])
        .order("created_at", desc=True).limit(150).execute())
    matches = []
    for old in prior.data or []:
        if norm(old.get("product_name")) != target_name:
            continue
        if target_specs and norm(old.get("specs")) != target_specs:
            continue
        if rfq.get("quantity") and old.get("quantity") and int(rfq["quantity"]) != int(old["quantity"]):
            continue
        current_days, old_days = rfq.get("required_delivery_days"), old.get("required_delivery_days")
        if current_days is not None and old_days is not None and int(current_days) != int(old_days):
            continue
        qs = (db.supabase.table("quotes")
              .select("price,supplier_id,created_at,is_accepted")
              .eq("rfq_id", old["id"]).eq("is_available", True)
              .order("created_at", desc=True).limit(5).execute())
        for q in qs.data or []:
            if q.get("price") is not None:
                matches.append({"source_rfq_id": old["id"],
                                "supplier_id": q.get("supplier_id"),
                                "historical_quote_aed": q["price"],
                                "accepted": q.get("is_accepted"),
                                "quoted_at": q.get("created_at"),
                                "benchmark_only": True})
        if len(matches) >= 12:
            break
    return matches[:12]


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
    quote_id: Optional[str] = None
    accepted_by: str = "openclaw"
    approval_id: Optional[str] = None


class ClaimCommercialApprovalRequest(BaseModel):
    client_id: str
    phone_number: str
    message: str

class PrepareOutboundDeliveryRequest(BaseModel):
    client_id: str
    supplier_id: Optional[str] = None
    phone_number: Optional[str] = None
    body: str


class MessageDeliveryRequest(BaseModel):
    client_id: str
    supplier_id: Optional[str] = None
    phone_number: Optional[str] = None
    body: str
    success: bool
    external_message_id: Optional[str] = None
    message_log_id: Optional[str] = None
    related_rfq_id: Optional[str] = None
    error: Optional[str] = None

class ActivationGateRequest(BaseModel):
    client_id: str
    supplier_id: Optional[str] = None
    phone_number: Optional[str] = None
    message: str = ""


class CommercialActionReviewRequest(BaseModel):
    proposed_message: Optional[str] = None
    client_id: str
    supplier_id: str
    rfq_id: Optional[str] = None
    quote_id: Optional[str] = None
    action_type: str
    attempt_number: int = Field(default=1, ge=1, le=2)
    proposal: dict = Field(default_factory=dict)
    assessment: dict = Field(default_factory=dict)
    session_key: Optional[str] = None


class NegotiationContextRequest(BaseModel):
    client_id: str
    supplier_id: str
    rfq_id: str

class RfqContextResolveRequest(BaseModel):
    client_id: str
    supplier_id: str
    message: str
    source_message_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    reply_to_body: Optional[str] = None
    extracted_facts: dict = Field(default_factory=dict)


class RfqMappingValidateRequest(BaseModel):
    client_id: str
    supplier_id: str
    proposed_rfq_id: str
    message: str
    attempt_number: int = Field(default=1, ge=1, le=3)
    reply_to_message_id: Optional[str] = None


class PendingCommercialDraftRequest(BaseModel):
    client_id: str
    supplier_id: str
    raw_message: str
    candidate_rfq_ids: list[str] = Field(default_factory=list)
    extracted_facts: dict = Field(default_factory=dict)
    source_message_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    reply_to_body: Optional[str] = None
    message_kind: Optional[str] = None


class ResolveCommercialDraftRequest(BaseModel):
    client_id: str
    supplier_id: str
    draft_id: str
    rfq_id: str


@router.post("/activation-gate")
def activation_gate(
    req: ActivationGateRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    """Return authoritative procurement state for a supplier before the agent is allowed to engage."""
    _require_openclaw_key(x_openclaw_key)
    supplier = _get_supplier_for_client(req.client_id, req.supplier_id, req.phone_number)

    open_links = db.get_open_rfqs_for_supplier(supplier["id"])
    active_session = db.get_active_negotiation_session_for_supplier(req.client_id, supplier["id"])
    pending_clarification = db.get_pending_clarification_for_supplier(supplier["id"])
    pending_flags_res = (
        db.supabase.table("flagged_for_review")
        .select("id,rfq_id,category,reason,status,created_at")
        .eq("client_id", req.client_id)
        .eq("supplier_id", supplier["id"])
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )
    pending_flags = pending_flags_res.data or []

    has_procurement_state = bool(
        open_links
        or active_session
        or pending_clarification
        or pending_flags
    )

    # Option C: a greeting or personal conversation cannot activate procurement.
    # The model may interpret commercial details *after* this deterministic gate,
    # but it must not override a DENY. Fail closed for ambiguous text.
    message = (req.message or "").strip()
    normalized = " ".join(message.casefold().split())
    greetings = re.compile(
        r"^(?:hi+|hey+|hello+|good (?:morning|afternoon|evening|night)|"
        r"assalam[ -]?ualaikum|salam|salaam|aoa|namaste|kem cho|kem chho|"
        r"su chale|what's up|whats up)[!.? ]*$",
        re.IGNORECASE,
    )
    personal = re.compile(
        r"(?:how (?:is|are) (?:your |the )?(?:family|wife|husband|kids|parents)|"
        r"what are you doing|what're you doing|"
        r"tame(?:in)? su kari rahya|tame su karo cho|"
        r"su kari rahya|kya kar rahe|ghar (?:par|pe)|"
        r"how is your health|how was your weekend)",
        re.IGNORECASE,
    )
    # Commercial intent alone does not grant RFQ-specific authority:
    # the supplier must also have matching live procurement state.
    commercial = re.compile(
        r"(?:\b(?:rfq|quotation|quote|quoted|price|pricing|rate|"
        r"discount|counteroffer|invoice|purchase order|delivery|dispatch|"
        r"shipment|stock|available|availability|quantity|qty|"
        r"per unit|per piece|aed|final offer)\b|"
        r"(?:دَر|قیمت|کوٹیشن|ڈلیوری|سپلائی)|"
        r"(?:ભાવ|કિંમત|કોટેશન|ડિલિવરી))",
        re.IGNORECASE,
    )
    product_names = [
        str((link.get("rfqs") or {}).get("product_name") or "").casefold().strip()
        for link in open_links
    ]
    product_mentioned = any(
        name and len(name) >= 4 and name in normalized for name in product_names
    )
    is_greeting = bool(greetings.fullmatch(normalized))
    is_personal = bool(personal.search(normalized))
    is_commercial = bool(commercial.search(normalized) or product_mentioned)

    paused = db.is_client_automation_paused(req.client_id)
    if paused:
        decision, reason = "IGNORE", "client_automation_paused"
    elif not normalized:
        decision, reason = "IGNORE", "empty_message"
    elif is_greeting:
        decision, reason = "IGNORE", "greeting_wait_for_second_message"
    elif is_personal and not is_commercial:
        decision, reason = "IGNORE", "personal_conversation"
    elif is_commercial and (open_links or active_session or pending_clarification):
        # Human-review flags alone never authorize procurement replies. A repeated
        # out-of-context inquiry must stay silent after its review row is created.
        decision, reason = "ENGAGE", "explicit_procurement_intent_with_context"
    elif is_commercial:
        decision, reason = "HUMAN_REVIEW", "procurement_intent_without_active_context"
    else:
        decision, reason = "IGNORE", "no_clear_procurement_intent"

    if decision == "HUMAN_REVIEW":
        # Persist once per pending exact supplier request; no AI reply is authorized.
        # Failure is surfaced instead of silently claiming that a review was created.
        existing = (
            db.supabase.table("flagged_for_review")
            .select("id")
            .eq("client_id", req.client_id)
            .eq("supplier_id", supplier["id"])
            .eq("category", "procurement_without_rfq")
            .eq("raw_message", message)
            .eq("status", "pending")
            .limit(1)
            .execute()
        )
        if not (existing.data or []):
            db.supabase.table("flagged_for_review").insert({
                "client_id": req.client_id,
                "supplier_id": supplier["id"],
                "rfq_id": None,
                "category": "procurement_without_rfq",
                "reason": "Procurement inquiry without authorized matching RFQ; human review required",
                "raw_message": message,
                "status": "pending",
                "metadata": {"source": "openclaw_activation_gate", "engagement_reason": reason},
            }).execute()

    return {
        "supplier": {
            "id": supplier.get("id"),
            "name": supplier.get("name"),
            "phone_number": supplier.get("phone_number"),
        },
        "has_procurement_state": has_procurement_state,
        "engagement_decision": decision,
        "engagement_reason": reason,
        "allow_autonomous_reply": decision == "ENGAGE",
        "automation_paused": paused,
        "active_rfq_count": len(open_links),
        "active_negotiation": bool(active_session),
        "pending_clarification": bool(pending_clarification),
        "pending_human_attention": bool(pending_flags),
        "active_rfqs": [
            {
                "rfq_id": (row.get("rfqs") or {}).get("id"),
                "product_name": (row.get("rfqs") or {}).get("product_name"),
                "supplier_status": row.get("status"),
                "due_by": (row.get("rfqs") or {}).get("due_by"),
            }
            for row in open_links
        ],
        "pending_flags": pending_flags,
        "message": req.message,
    }


@router.post("/validate-rfq-mapping")
def validate_rfq_mapping(
    req: RfqMappingValidateRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    """Validate a proposed RFQ against fresh supplier-scoped candidates.

    This confirms authorized identity and strong message references. It cannot
    prove semantic equivalence of free-form product descriptions.
    """
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    links = db.get_open_rfqs_for_supplier(req.supplier_id)
    active = [
        row.get("rfqs") or {} for row in links
        if (row.get("rfqs") or {}).get("client_id") == req.client_id
    ]
    active_ids = {row["id"] for row in active if row.get("id")}
    options = [
        {"rfq_id": row["id"], "product_name": row.get("product_name"),
         "specs": row.get("specs"), "quantity": row.get("quantity")}
        for row in active if row.get("id")
    ]
    if req.proposed_rfq_id not in active_ids:
        return {"status": "RETRY" if req.attempt_number < 3 else "CLARIFY",
                "error_code": "RFQ_NOT_ACTIVE_FOR_SUPPLIER",
                "candidates": options, "attempt_number": req.attempt_number}

    if req.reply_to_message_id:
        bundles = (
            db.supabase.table("supplier_message_bundles")
            .select("id").eq("client_id", req.client_id)
            .eq("supplier_id", req.supplier_id)
            .eq("external_message_id", req.reply_to_message_id)
            .eq("status", "sent").limit(1).execute()
        )
        if bundles.data:
            items = (
                db.supabase.table("supplier_message_bundle_items")
                .select("rfq_id").eq("bundle_id", bundles.data[0]["id"])
                .eq("client_id", req.client_id).eq("supplier_id", req.supplier_id)
                .execute()
            )
            allowed_ids = {x["rfq_id"] for x in (items.data or []) if x.get("rfq_id") in active_ids}
            if allowed_ids and req.proposed_rfq_id not in allowed_ids:
                return {"status": "RETRY" if req.attempt_number < 3 else "CLARIFY",
                        "error_code": "REPLY_REFERENCES_DIFFERENT_RFQ",
                        "candidates": [x for x in options if x["rfq_id"] in allowed_ids],
                        "attempt_number": req.attempt_number}
            if len(allowed_ids) == 1:
                return {"status": "VERIFIED", "rfq_id": req.proposed_rfq_id,
                        "evidence": "single_item_whatsapp_bundle"}

    if len(active_ids) == 1:
        return {"status": "VERIFIED", "rfq_id": req.proposed_rfq_id,
                "evidence": "only_active_supplier_rfq"}

    return {"status": "CANDIDATE_VALID", "rfq_id": req.proposed_rfq_id,
            "evidence": "supplier_and_active_status_only",
            "requires_semantic_disambiguation": True,
            "candidates": options}


@router.post("/negotiation-context")
def negotiation_context(
    req: NegotiationContextRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    """Return hard commercial facts; OpenClaw chooses the negotiation strategy and counter."""
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    rfq = _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)
    bundle = _require_current_bundle_rfq(req.client_id, req.supplier_id, req.rfq_id)

    quotes_res = (
        db.supabase.table("quotes")
        .select("id,price,quantity,delivery_time,is_available,is_accepted,created_at,raw_message")
        .eq("rfq_id", req.rfq_id)
        .eq("supplier_id", req.supplier_id)
        .order("created_at")
        .execute()
    )
    active_session = db.get_active_negotiation_session(req.client_id, req.rfq_id, req.supplier_id)
    constraints = db.get_rfq_negotiation_constraints(req.client_id, req.rfq_id)

    return {
        "rfq": {
            "id": rfq.get("id"),
            "product_name": rfq.get("product_name"),
            "specs": rfq.get("specs"),
            "quantity": rfq.get("quantity"),
            "acceptable_price_min": rfq.get("acceptable_price_min"),
            "acceptable_price_max": rfq.get("acceptable_price_max"),
            "required_delivery_days": rfq.get("required_delivery_days"),
            "quantity_flexible": rfq.get("quantity_flexible"),
            "quantity_max": rfq.get("quantity_max"),
            "delivery_flexible": rfq.get("delivery_flexible"),
            "max_delivery_days": rfq.get("max_delivery_days"),
            "status": rfq.get("status"),
            "due_by": rfq.get("due_by"),
        },
        "quotes": quotes_res.data or [],
        "latest_supplier_invitation_bundle": bundle,
        "historical_price_benchmarks": _historical_price_benchmarks(req.client_id, rfq),
        "historical_benchmarks_are_advisory_only": True,
        "acceptance_requires_current_rfq_maximum_or_human_approval": True,
        "active_negotiation_session": active_session,
        "constraints": constraints,
        "max_attempts": 10,
        "guidance": {
            "openclaw_chooses_counter": True,
            "backend_enforces_only_hard_authority": True,
            "do_not_reveal_internal_limits": True,
        },
    }


class SystemOutboundAuthorizationRequest(BaseModel):
    client_id: str
    phone_number: str
    message: str


@router.post("/authorize-system-outbound")
def authorize_system_outbound(
    req: SystemOutboundAuthorizationRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    """Authorize exact, in-flight buyer-issued RFQ invitations.

    This is deliberately independent of agent session keys and message keywords.
    An agent cannot authorize arbitrary replies by naming an RFQ: the outbound
    text must match an already claimed bundle persisted by the backend.
    """
    _require_openclaw_key(x_openclaw_key)
    if not req.message or len(req.message) > 10000:
        return {"authorized": False, "reason": "invalid_message"}
    if db.is_client_automation_paused(req.client_id):
        return {"authorized": False, "reason": "automation_paused"}
    phone = re.sub(r"\D", "", req.phone_number or "")
    if not phone:
        return {"authorized": False, "reason": "invalid_recipient"}
    matches = (
        db.supabase.table("supplier_message_bundles")
        .select("id,supplier_id,client_id")
        .eq("client_id", req.client_id)
        .eq("kind", "initial_rfq")
        .eq("status", "processing")
        .eq("message", req.message)
        .limit(5)
        .execute()
    )
    for bundle in matches.data or []:
        supplier = db.get_supplier_by_id(bundle["supplier_id"], client_id=req.client_id)
        if not supplier:
            continue
        if re.sub(r"\D", "", supplier.get("phone_number") or "") != phone:
            continue
        items = (
            db.supabase.table("supplier_message_bundle_items")
            .select("rfq_id")
            .eq("bundle_id", bundle["id"])
            .eq("status", "processing")
            .limit(8)
            .execute()
        )
        if not items.data:
            continue
        valid = True
        for item in items.data:
            rfq_rows = (
                db.supabase.table("rfqs")
                .select("status,due_by")
                .eq("id", item["rfq_id"])
                .eq("client_id", req.client_id)
                .limit(1)
                .execute()
            )
            rfq = rfq_rows.data[0] if rfq_rows.data else None
            if not rfq or not db.is_rfq_open(rfq):
                valid = False
                break
        if valid:
            return {"authorized": True, "purpose": "initial_rfq", "bundle_id": bundle["id"]}
    return {"authorized": False, "reason": "no_exact_inflight_authorization"}


@router.post("/review-commercial-action")
def review_commercial_action(
    req: CommercialActionReviewRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    """Minimal hard guard for Phase 2 LLM-led commercial decisions.

    The backend does not choose negotiation strategy. It only enforces authority,
    persisted state, tenant/RFQ linkage, stale/finalized state, and explicit bounds.
    """
    _require_openclaw_key(x_openclaw_key)
    supplier = _get_supplier_for_client(req.client_id, req.supplier_id, None)

    action = (req.action_type or "").strip().lower()
    proposal = req.proposal or {}
    if not isinstance(proposal, dict):
        raise HTTPException(status_code=422, detail="proposal must be a JSON object")
    # Reject malformed commercial JSON before evaluating numeric constraints.
    if action in {"counter", "counteroffer", "negotiate"}:
        required = {"price", "quantity", "delivery_days"}
        if set(proposal) != required:
            raise HTTPException(status_code=422, detail="Counter requires exactly price, quantity, delivery_days")
        if (type(proposal["price"]) not in (int, float)
            or type(proposal["quantity"]) is not int
            or type(proposal["delivery_days"]) is not int):
            raise HTTPException(status_code=422, detail="Invalid counter field types")
        if (not __import__("math").isfinite(proposal["price"])
            or proposal["price"] <= 0 or proposal["quantity"] <= 0
            or proposal["delivery_days"] < 0):
            raise HTTPException(status_code=422, detail="Invalid counter values")
    rfq = None
    active_session = None
    if req.rfq_id:
        rfq = _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)
        _require_current_bundle_rfq(req.client_id, req.supplier_id, req.rfq_id)
        active_session = db.get_active_negotiation_session(
            req.client_id,
            req.rfq_id,
            req.supplier_id,
        )
        if rfq.get("status") != "active":
            outcome = "REJECT"
            feedback = f"RFQ is {rfq.get('status')}; no new commercial action is allowed."
            adjusted = None
        elif not db.is_rfq_open(rfq) and not active_session:
            outcome = "REJECT"
            feedback = "The RFQ deadline has passed and there is no existing in-flight negotiation to continue."
            adjusted = None
        else:
            outcome = "APPROVE"
            feedback = "Commercial action is within current authority."
            adjusted = None
    else:
        outcome = "APPROVE"
        feedback = "No RFQ-scoped hard-boundary violation detected."
        adjusted = None

    if outcome == "APPROVE" and action in {"accept", "confirm", "award", "accept_quote"} and rfq and rfq.get("acceptable_price_max") is None:
        outcome, feedback = "ESCALATE", "Current RFQ has no authorized maximum; historical benchmarks cannot approve acceptance."
    # Acceptance must always reference a persisted quote.
    if outcome == "APPROVE" and action in {"accept", "confirm", "award", "accept_quote"}:
        if not req.quote_id:
            outcome = "RETRY"
            feedback = "Acceptance requires the exact persisted quote_id. Persist/select the quote first, then retry."
        else:
            qres = (
                db.supabase.table("quotes")
                .select("*")
                .eq("id", req.quote_id)
                .eq("supplier_id", req.supplier_id)
                .limit(1)
                .execute()
            )
            quote = qres.data[0] if qres.data else None
            if not quote or (req.rfq_id and quote.get("rfq_id") != req.rfq_id):
                outcome = "RETRY"
                feedback = "The proposed acceptance does not reference a persisted quote for this supplier/RFQ."
            elif rfq and rfq.get("acceptable_price_max") is not None and quote.get("price") is not None:
                if float(quote["price"]) > float(rfq["acceptable_price_max"]):
                    outcome = "ESCALATE"
                    feedback = "The persisted quote exceeds the explicit buyer maximum; human authorization is required before acceptance."

    # Counter proposals: OpenClaw chooses the amount. Clamp only explicit hard authority.
    if outcome == "APPROVE" and action in {"counter", "counteroffer", "negotiate"} and rfq:
        proposed_price = proposal.get("price")
        if proposed_price is None:
            outcome = "RETRY"
            feedback = "A counter action requires proposal.price."
        else:
            try:
                proposed_price = float(proposed_price)
            except (TypeError, ValueError):
                outcome = "RETRY"
                feedback = "proposal.price must be numeric."
            else:
                if proposed_price <= 0:
                    outcome = "RETRY"
                    feedback = "Counter price must be greater than zero."
                else:
                    hard_max = rfq.get("acceptable_price_max")
                    if hard_max is not None and proposed_price > float(hard_max):
                        outcome = "ADJUST"
                        adjusted = {**proposal, "price": float(hard_max)}
                        feedback = (
                            f"The negotiation direction may continue, but AED {proposed_price:g} exceeds "
                            f"the explicit buyer maximum. The highest authorized price is AED {float(hard_max):g}. "
                            "Reconsider the strategy using that boundary."
                        )

                    latest_quote_res = (
                        db.supabase.table("quotes")
                        .select("id,price,quantity,delivery_time,created_at")
                        .eq("rfq_id", req.rfq_id)
                        .eq("supplier_id", req.supplier_id)
                        .eq("is_available", True)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                    latest_quote = latest_quote_res.data[0] if latest_quote_res.data else None
                    if outcome == "APPROVE" and not latest_quote:
                        outcome = "RETRY"
                        feedback = (
                            "No persisted supplier quote exists for this RFQ. "
                            "Persist the supplier's current offer first, then propose the counter again."
                        )
                    elif outcome == "APPROVE" and latest_quote.get("price") is not None:
                        if proposed_price > float(latest_quote["price"]):
                            outcome = "RETRY"
                            feedback = (
                                "The proposed buyer counter is above the supplier's latest persisted price. "
                                "Do not negotiate upward against the buyer. Choose another strategy or price."
                            )

    # Quantity changes require explicit flexibility/authorization.
    if outcome == "APPROVE" and rfq and proposal.get("quantity") is not None:
        try:
            q = int(proposal["quantity"])
            requested_q = int(rfq.get("quantity")) if rfq.get("quantity") is not None else None
        except (TypeError, ValueError):
            outcome = "RETRY"
            feedback = "proposal.quantity must be an integer."
        else:
            if requested_q is not None and q != requested_q:
                allowed = bool(rfq.get("quantity_flexible"))
                qmax = rfq.get("quantity_max")
                if not allowed or (qmax is not None and q > int(qmax)):
                    outcome = "ESCALATE"
                    feedback = "The proposed quantity differs from the RFQ and is outside explicit autonomous authority."

    # Delivery extensions require explicit flexibility/authorization.
    if outcome == "APPROVE" and rfq and proposal.get("delivery_days") is not None:
        try:
            days = int(proposal["delivery_days"])
            required = int(rfq.get("required_delivery_days")) if rfq.get("required_delivery_days") is not None else None
        except (TypeError, ValueError):
            outcome = "RETRY"
            feedback = "proposal.delivery_days must be an integer."
        else:
            if required is not None and days > required:
                allowed = bool(rfq.get("delivery_flexible"))
                max_days = rfq.get("max_delivery_days")
                if not allowed or (max_days is not None and days > int(max_days)):
                    outcome = "ESCALATE"
                    feedback = "The proposed delivery extension is outside explicit autonomous authority."

    # Commercial evaluator checks typed action data only, never LLM prose.
    # Hard boundaries above remain authoritative.
    approved_text = None
    if outcome == "APPROVE" and action in {"accept", "confirm", "award", "accept_quote"}:
        if not rfq or not req.quote_id or set(proposal) != {"price", "quantity", "delivery_days"}:
            outcome, feedback = "RETRY", "Acceptance needs an RFQ, persisted quote_id, and exact price/quantity/delivery_days."
        elif (type(proposal["price"]) not in (int, float)
              or type(proposal["quantity"]) is not int
              or type(proposal["delivery_days"]) is not int):
            outcome, feedback = "RETRY", "Acceptance JSON types are invalid."
        else:
            qres = (db.supabase.table("quotes").select("*")
                    .eq("id", req.quote_id).eq("rfq_id", req.rfq_id)
                    .eq("supplier_id", req.supplier_id).limit(1).execute())
            q = (qres.data or [None])[0]
            if not q or not q.get("is_available") or q.get("price") is None:
                outcome, feedback = "REJECT", "Supplier quote is missing or unavailable."
            elif (float(proposal["price"]) != float(q["price"])
                  or int(proposal["quantity"]) != int(q.get("quantity") or rfq["quantity"])
                  or int(proposal["delivery_days"]) != int(rfq.get("required_delivery_days") or proposal["delivery_days"])):
                outcome, feedback = "REJECT", "Acceptance terms do not match persisted quote or required delivery."
            else:
                approved_text = (f"Confirmed — AED {float(proposal['price']):g} per unit "
                                 f"for {proposal['quantity']} pieces, delivery within "
                                 f"{proposal['delivery_days']} days. Thank you!")
    if outcome == "APPROVE":
        if action in {"accept", "confirm", "award", "accept_quote"}:
            pass  # Acceptance checked against the exact persisted quote above.
        elif action in {"counter", "counteroffer", "negotiate"}:
            if not rfq or set(proposal) != {"price", "quantity", "delivery_days"}:
                outcome, feedback = "RETRY", "Counter requires RFQ plus exactly price, quantity, delivery_days."
            elif (isinstance(proposal["price"], bool)
                  or not isinstance(proposal["price"], (int, float))
                  or not isinstance(proposal["quantity"], int)
                  or isinstance(proposal["quantity"], bool)
                  or not isinstance(proposal["delivery_days"], int)
                  or isinstance(proposal["delivery_days"], bool)):
                outcome, feedback = "RETRY", "Counter fields must be numeric price and integer quantity/delivery_days."
            elif proposal["quantity"] <= 0 or proposal["delivery_days"] < 0:
                outcome, feedback = "RETRY", "Invalid quantity or delivery_days."
            else:
                approved_text = (
                    f"Thank you for your quotation. Could you offer AED "
                    f"{float(proposal['price']):g} per unit for {proposal['quantity']} units "
                    f"with delivery within {proposal['delivery_days']} day(s)?"
                )
        elif action in {"ask", "clarify", "request_info", "request_price"} and rfq and not proposal:
            approved_text = "Thank you. Could you confirm your best unit price in AED and estimated delivery time?"
        elif action in {"acknowledge", "acknowledgement"} and rfq and not proposal:
            approved_text = "Thank you. We've received your quotation."
        else:
            outcome, feedback = "REJECT", "Unsupported structured action or missing RFQ; no message authorized."

    # Two model correction attempts maximum. After that, fail safe to human review.
    if outcome in {"ADJUST", "RETRY"} and req.attempt_number >= 2:
        outcome = "ESCALATE"
        feedback = (
            "Two proposal attempts have not produced an authorized commercial action. "
            "Do not keep looping; use a brief context-appropriate holding response only if needed and request human attention."
        )
        adjusted = None

    approval_id = None
    try:
        audit_result = db.supabase.table("commercial_action_intents").insert({
            "client_id": req.client_id,
            "supplier_id": req.supplier_id,
            "rfq_id": req.rfq_id,
            "quote_id": req.quote_id,
            "session_key": req.session_key,
            "action_type": action or "unknown",
            "attempt_number": req.attempt_number,
            "proposal": proposal,
            "guard_snapshot": {
                "assessment": req.assessment,
                "rfq_status": rfq.get("status") if rfq else None,
                "acceptable_price_max": rfq.get("acceptable_price_max") if rfq else None,
            },
            "outcome": outcome,
            "feedback": feedback,
            "adjusted_action": adjusted,
            "approved_message": approved_text if outcome == "APPROVE" else None,
        }).execute()
        if outcome == "APPROVE":
            approval_id = (audit_result.data or [None])[0]["id"]
    except Exception as exc:
        if outcome == "APPROVE":
            raise HTTPException(status_code=503, detail="Could not persist commercial send approval") from exc

    return {
        "outcome": outcome,
        "feedback": feedback,
        "approval_id": approval_id if action in {"accept", "confirm", "award", "accept_quote"} and outcome == "APPROVE" else None,
        "adjusted_action": adjusted,
        "attempt_number": req.attempt_number,
        "hard_guard_only": True,
        "approved_message": approved_text if outcome == "APPROVE" else None,
        "approved_terms": {
            "action_type": action,
            "price": proposal.get("price"),
            "quantity": proposal.get("quantity"),
            "delivery_days": proposal.get("delivery_days"),
        } if outcome == "APPROVE" else None,
        "supplier_id": supplier.get("id"),
        "rfq_id": req.rfq_id,
    }


@router.post("/claim-commercial-outbound")
def claim_commercial_outbound(req: ClaimCommercialApprovalRequest, x_openclaw_key: Optional[str] = Header(default=None)):
    """Atomically consume one exact, live backend authorization before WhatsApp sends."""
    _require_openclaw_key(x_openclaw_key)
    supplier = _get_supplier_for_client(req.client_id, None, req.phone_number)
    res = db.supabase.rpc("claim_commercial_message_approval", {
        "p_client_id": req.client_id,
        "p_supplier_id": supplier["id"],
        "p_body": req.message.strip(),
    }).execute()
    claimed = res.data or []
    if not claimed:
        return {"authorized": False}
    return {"authorized": True, "approval_id": claimed[0]["approval_id"],
            "rfq_id": claimed[0]["rfq_id"]}


@router.post("/resolve-rfq-context")
def resolve_rfq_context(
    req: RfqContextResolveRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    """Resolve one supplier message against multiple open RFQs using strong context first.

    This endpoint deliberately does not force a guess. It returns RESOLVED only
    for strong evidence; otherwise it returns a reduced candidate set with
    evidence for OpenClaw to reason over.
    """
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)

    open_links = db.get_open_rfqs_for_supplier(req.supplier_id)
    candidates = []
    candidate_ids = set()

    for link in open_links:
        rfq = link.get("rfqs") or {}
        if rfq.get("client_id") != req.client_id or not rfq.get("id"):
            continue
        candidate_ids.add(rfq["id"])

        quote_res = (
            db.supabase.table("quotes")
            .select("id,price,quantity,delivery_time,is_available,is_accepted,created_at")
            .eq("rfq_id", rfq["id"])
            .eq("supplier_id", req.supplier_id)
            .order("created_at", desc=True)
            .limit(3)
            .execute()
        )
        quote_rows = quote_res.data or []
        latest_quote = quote_rows[0] if quote_rows else None

        session = db.get_active_negotiation_session(req.client_id, rfq["id"], req.supplier_id)

        recent_res = (
            db.supabase.table("message_log")
            .select("id,direction,body,created_at,external_message_id")
            .eq("client_id", req.client_id)
            .eq("supplier_id", req.supplier_id)
            .eq("related_rfq_id", rfq["id"])
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )

        candidates.append({
            "rfq_id": rfq["id"],
            "product_name": rfq.get("product_name"),
            "specs": rfq.get("specs"),
            "quantity": rfq.get("quantity"),
            "category": rfq.get("category"),
            "due_by": rfq.get("due_by"),
            "supplier_status": link.get("status"),
            "negotiation_attempts": link.get("negotiation_attempts") or 0,
            "has_quote_history": bool(quote_rows),
            "latest_quote": latest_quote,
            "active_negotiation": bool(session),
            "recent_rfq_messages": recent_res.data or [],
        })

    if not candidates:
        return {
            "status": "NO_MATCH",
            "resolved_rfq_id": None,
            "candidates": [],
            "reason": "Supplier has no open delivered RFQ candidates.",
        }

    # Bundled outbound messages have one WhatsApp stanza for many RFQs.
    # Never resolve a reply to the first associated RFQ just because message_log
    # has one related_rfq_id for backwards compatibility.
    if req.reply_to_message_id:
        bundle_lookup = (
            db.supabase.table("supplier_message_bundles")
            .select("id")
            .eq("client_id", req.client_id)
            .eq("supplier_id", req.supplier_id)
            .eq("external_message_id", req.reply_to_message_id)
            .eq("status", "sent")
            .limit(1)
            .execute()
        )
        if bundle_lookup.data:
            bundle_id = bundle_lookup.data[0]["id"]
            bundle_items = (
                db.supabase.table("supplier_message_bundle_items")
                .select("rfq_id")
                .eq("bundle_id", bundle_id)
                .eq("client_id", req.client_id)
                .eq("supplier_id", req.supplier_id)
                .execute()
            )
            matched_ids = {row["rfq_id"] for row in (bundle_items.data or []) if row.get("rfq_id") in candidate_ids}
            if len(matched_ids) == 1:
                only_id = next(iter(matched_ids))
                return {
                    "status": "RESOLVED", "resolved_rfq_id": only_id,
                    "confidence": "very_high",
                    "reason": "Native WhatsApp reply matched a one-item bundle.",
                    "candidates": [x for x in candidates if x["rfq_id"] == only_id],
                }
            return {
                "status": "AMBIGUOUS", "resolved_rfq_id": None,
                "confidence": "low",
                "reason": "Reply references a multi-RFQ supplier bundle; resolve each quoted item explicitly.",
                "candidates": [x for x in candidates if x["rfq_id"] in matched_ids],
                "bundle_id": bundle_id,
            }

    # 1) Native reply / stanza context is strongest when we have the external id.
    if req.reply_to_message_id:
        msg_res = (
            db.supabase.table("message_log")
            .select("id,related_rfq_id,body,external_message_id,external_alt_message_id")
            .eq("client_id", req.client_id)
            .eq("supplier_id", req.supplier_id)
            .or_(
                f"external_message_id.eq.{req.reply_to_message_id},"
                f"external_alt_message_id.eq.{req.reply_to_message_id}"
            )
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if msg_res.data:
            linked = msg_res.data[0].get("related_rfq_id")
            if linked in candidate_ids:
                return {
                    "status": "RESOLVED",
                    "resolved_rfq_id": linked,
                    "confidence": "very_high",
                    "reason": "Native WhatsApp reply context matched a logged RFQ message.",
                    "candidates": [x for x in candidates if x["rfq_id"] == linked],
                }

    # Never select an RFQ from product-name token matching or from old chat
    # memory. Supplier language, aliases, and scripts are interpreted by the
    # model, while the backend validates a proposed candidate separately.
    # Preserve ALL candidates: cutting to 3 silently hides valid concurrent
    # RFQs, especially when the supplier has many open requests.
    if len(candidates) == 1:
        return {
            "status": "RESOLVED",
            "resolved_rfq_id": candidates[0]["rfq_id"],
            "confidence": "high",
            "reason": "Only one authorized open RFQ exists for this supplier.",
            "candidates": candidates,
        }

    return {
        "status": "AMBIGUOUS",
        "resolved_rfq_id": None,
        "confidence": "low",
        "reason": "Multiple open RFQs: use semantic interpretation with fresh candidates and server validation.",
        "candidates": candidates,
        "clarification_guidance": (
            "Infer from the current message and quoted WhatsApp reference. "
            "Submit each proposed active RFQ for backend validation; retry on a "
            "recoverable mapping rejection and clarify only if evidence is insufficient."
        ),
    }


@router.post("/pending-commercial-draft")
def save_pending_commercial_draft(
    req: PendingCommercialDraftRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    """Persist extracted commercial facts before asking an RFQ clarification."""
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)

    clean_ids = []
    for rfq_id in req.candidate_rfq_ids[:8]:
        rfq = db.get_rfq_by_id(rfq_id, client_id=req.client_id)
        if rfq:
            clean_ids.append(rfq_id)

    payload = {
        "client_id": req.client_id,
        "supplier_id": req.supplier_id,
        "source_message_id": req.source_message_id,
        "reply_to_message_id": req.reply_to_message_id,
        "reply_to_body": req.reply_to_body,
        "raw_message": req.raw_message,
        "message_kind": req.message_kind,
        "candidate_rfq_ids": clean_ids,
        "extracted_facts": req.extracted_facts or {},
        "status": "awaiting_rfq_resolution",
        "updated_at": db.datetime.now(db.timezone.utc).isoformat(),
    }

    if req.source_message_id:
        existing = (
            db.supabase.table("pending_commercial_drafts")
            .select("*")
            .eq("client_id", req.client_id)
            .eq("supplier_id", req.supplier_id)
            .eq("source_message_id", req.source_message_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            if existing.data[0].get("status") == "resolved":
                return {"saved": True, "draft": existing.data[0], "already_resolved": True}
            row = (
                db.supabase.table("pending_commercial_drafts")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
            return {"saved": True, "draft": row.data[0] if row.data else existing.data[0]}

    row = db.supabase.table("pending_commercial_drafts").insert(payload).execute()
    if not row.data:
        raise HTTPException(status_code=500, detail="Pending commercial draft could not be persisted.")
    return {"saved": True, "draft": row.data[0]}


@router.post("/resolve-commercial-draft")
def resolve_commercial_draft(
    req: ResolveCommercialDraftRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    """Resolve an existing commercial draft to one RFQ without losing extracted facts."""
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)

    res = (
        db.supabase.table("pending_commercial_drafts")
        .select("*")
        .eq("id", req.draft_id)
        .eq("client_id", req.client_id)
        .eq("supplier_id", req.supplier_id)
        .eq("status", "awaiting_rfq_resolution")
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Pending commercial draft not found.")

    draft = res.data[0]
    candidates = draft.get("candidate_rfq_ids") or []
    if candidates and req.rfq_id not in candidates:
        raise HTTPException(status_code=409, detail="Selected RFQ was not one of the draft's remaining candidates.")

    now_iso = db.datetime.now(db.timezone.utc).isoformat()
    upd = (
        db.supabase.table("pending_commercial_drafts")
        .update({
            "status": "resolved",
            "resolved_rfq_id": req.rfq_id,
            "resolved_at": now_iso,
            "updated_at": now_iso,
        })
        .eq("id", req.draft_id)
        .execute()
    )
    resolved = upd.data[0] if upd.data else {**draft, "status": "resolved", "resolved_rfq_id": req.rfq_id}
    return {
        "resolved": True,
        "rfq_id": req.rfq_id,
        "draft": resolved,
        "extracted_facts": resolved.get("extracted_facts") or {},
        "raw_message": resolved.get("raw_message"),
        "instruction": (
            "Continue from the preserved facts. Do not ask the supplier to repeat price, quantity, "
            "delivery, or other terms already present in extracted_facts."
        ),
    }



class MultiItemSupplierQuoteRequest(BaseModel):
    client_id: str
    supplier_id: str
    message: str
    source_message_id: Optional[str] = None
    reply_to_message_id: Optional[str] = None


@router.post("/parse-multi-item-quotes")
def parse_multi_item_quotes(req: MultiItemSupplierQuoteRequest, x_openclaw_key: Optional[str] = Header(default=None)):
    """Safely map several prices to authorized RFQs; no automatic persistence."""
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    links = [x for x in db.get_open_rfqs_for_supplier(req.supplier_id) if (x.get("rfqs") or {}).get("client_id") == req.client_id]
    bundle_id = None
    if req.reply_to_message_id:
        found = (
            db.supabase.table("supplier_message_bundles").select("id")
            .eq("client_id", req.client_id).eq("supplier_id", req.supplier_id)
            .eq("external_message_id", req.reply_to_message_id)
            .eq("status", "sent").limit(1).execute()
        )
        if found.data:
            bundle_id = found.data[0]["id"]
            number_rows = (
                db.supabase.table("supplier_message_bundle_items")
                .select("rfq_id,item_position")
                .eq("bundle_id", bundle_id)
                .eq("client_id", req.client_id)
                .eq("supplier_id", req.supplier_id)
                .order("item_position").execute()
            )
            item_numbers = {str(row["rfq_id"]): row.get("item_position") for row in (number_rows.data or [])}
            links = [
                {**entry, "rfqs": {
                    **entry["rfqs"],
                    "item_number": item_numbers[str(entry["rfqs"]["id"])],
                    "reference": str(entry["rfqs"]["id"])[:8],
                }}
                for entry in links if str(entry["rfqs"]["id"]) in item_numbers
            ]
    if len(links) < 2:
        return {"status": "SINGLE_RFQ", "items": [], "bundle_id": bundle_id}
    items = (groq_client.parse_multi_rfq_quotes(req.message, links) or {}).get("items") or []
    if not items:
        return {"status": "NO_ITEMS", "items": [], "bundle_id": bundle_id}
    allowed_ids = [str(x["rfqs"]["id"]) for x in links]
    safe_items = []
    unresolved_items = []
    for raw in items:
        try:
            valid = validate_multi_rfq_quotes([raw], allowed_ids)
            item = valid[0]
            safe_items.append({
                **raw, "candidate_rfq_id": item.rfq_id,
                "price": float(item.price),
            })
        except (AmbiguousSupplierQuote, ValueError, TypeError) as exc:
            unresolved_items.append({
                "product_reference": raw.get("product_reference"),
                "price": raw.get("price"),
                "quantity": raw.get("quantity"),
                "delivery_time": raw.get("delivery_time"),
                "reason": str(exc),
            })
    if unresolved_items:
        return {
            "status": "CLARIFICATION_REQUIRED",
            "items": [],
            "resolved_items": safe_items,
            "unresolved_items": unresolved_items,
            "bundle_id": bundle_id,
            "candidate_rfqs": [
                {"rfq_id": row["rfqs"]["id"], "product_name": row["rfqs"].get("product_name"),
                 "item_number": row["rfqs"].get("item_number")}
                for row in links
            ],
            "instruction": (
                "Save the source message and all extracted facts in a pending commercial draft. "
                "Ask one precise clarification for the unresolved line(s). Never repeat pricing "
                "facts already captured. Record the batch only once every line is mapped."
            ),
        }
    distinct_ids = {item["candidate_rfq_id"] for item in safe_items}
    return {
        "status": "MAPPED" if len(distinct_ids) >= 2 else "SINGLE_RFQ",
        "items": safe_items,
        "bundle_id": bundle_id,
        "source_message_id": req.source_message_id,
        "instruction": "Persist the entire mapped list atomically with record-multi-item-quotes before making commercial commitments.",
    }



@router.post("/process-multi-item-message")
def process_multi_item_message(
    req: MultiItemSupplierQuoteRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    """One safe tool call: parse the COMPLETE message and atomically persist it.

    If any item is unresolved, save all known facts for targeted clarification
    and DO NOT persist a misleading partial commercial batch.
    """
    parsed = parse_multi_item_quotes(req, x_openclaw_key)
    if parsed.get("status") == "CLARIFICATION_REQUIRED":
        candidate_ids = [str(c["rfq_id"]) for c in parsed.get("candidate_rfqs") or []]
        saved = save_pending_commercial_draft(
            PendingCommercialDraftRequest(
                client_id=req.client_id,
                supplier_id=req.supplier_id,
                raw_message=req.message,
                candidate_rfq_ids=candidate_ids,
                extracted_facts={
                    "resolved_items": parsed.get("resolved_items") or [],
                    "unresolved_items": parsed.get("unresolved_items") or [],
                    "bundle_id": parsed.get("bundle_id"),
                },
                source_message_id=req.source_message_id,
                reply_to_message_id=req.reply_to_message_id,
                message_kind="multi_rfq_price_list",
            ),
            x_openclaw_key,
        )
        return {**parsed, "draft_id": (saved.get("draft") or {}).get("id")}
    if parsed.get("status") != "MAPPED":
        return parsed
    if not req.source_message_id:
        raise HTTPException(status_code=422, detail="Persist inbound message first and supply its internal message_log UUID.")
    recorded = record_multi_item_quotes(
        RecordMultiItemQuotesRequest(
            client_id=req.client_id,
            supplier_id=req.supplier_id,
            source_message_id=req.source_message_id,
            raw_message=req.message,
            items=parsed["items"],
        ),
        x_openclaw_key,
    )
    return {"status": "MULTI_RFQ_RECORDED", "bundle_id": parsed.get("bundle_id"), "recording": recorded}


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



class RecordMultiItemQuotesRequest(BaseModel):
    client_id: str
    supplier_id: str
    source_message_id: str
    raw_message: str
    items: list[dict] = Field(min_length=2)


@router.post("/record-multi-item-quotes")
def record_multi_item_quotes(req: RecordMultiItemQuotesRequest, x_openclaw_key: Optional[str] = Header(default=None)):
    """Persist verified RFQ-specific variants; reject ambiguous batches before writes.

    Caller must first parse and validate all items. Retried message/RFQ pairs
    return existing rows; no supplier WhatsApp message is sent by this endpoint.
    """
    _require_openclaw_key(x_openclaw_key)
    if db.is_client_automation_paused(req.client_id):
        raise HTTPException(status_code=409, detail="Tenant is in human-only pause mode.")
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    links = [
        x for x in db.get_open_rfqs_for_supplier(req.supplier_id)
        if (x.get("rfqs") or {}).get("client_id") == req.client_id
    ]
    allowed_ids = [str(x["rfqs"]["id"]) for x in links]
    try:
        mapped = validate_multi_rfq_quotes(req.items, allowed_ids)
    except AmbiguousSupplierQuote as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    grouped = {}
    for original, item in zip(req.items, mapped):
        grouped.setdefault(item.rfq_id, []).append({
            "variant_label": item.variant_label,
            "price": float(item.price),
            "delivery_time": item.delivery_time,
            "quantity": item.quantity,
            "quality_notes": original.get("quality_notes"),
            "is_available": True,
        })
    if len(grouped) < 2:
        raise HTTPException(status_code=422, detail="At least two distinct mapped RFQs are required.")
    # Ensure the source is a real inbound database message belonging to this tenant/supplier.
    source = (
        db.supabase.table("message_log").select("id")
        .eq("id", req.source_message_id)
        .eq("client_id", req.client_id)
        .eq("supplier_id", req.supplier_id)
        .eq("direction", "inbound").limit(1).execute()
    )
    if not source.data:
        raise HTTPException(status_code=422, detail="Source message must be a persisted inbound message.")
    # One transaction validates and writes ALL lines, with advisory locking
    # on the persisted inbound source ID to make concurrent retries safe.
    # Each item is authoritative only after the validation above passes.
    payload = {
        "p_client_id": req.client_id,
        "p_supplier_id": req.supplier_id,
        "p_source_message_id": req.source_message_id,
        "p_raw_message": req.raw_message,
        "p_items": [
            {
                "candidate_rfq_id": item.rfq_id,
                "confidence": float(raw["confidence"]),
                "price": float(item.price),
                "quantity": item.quantity,
                "variant_label": item.variant_label,
                "delivery_time": item.delivery_time,
                "quality_notes": raw.get("quality_notes"),
            }
            for raw, item in zip(req.items, mapped)
        ],
    }
    try:
        saved = db.supabase.rpc("record_multi_rfqs_atomic", payload).execute()
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Atomic multi-RFQ quote recording failed.") from exc
    if not saved.data:
        raise HTTPException(status_code=409, detail="No quote records persisted.")
    return saved.data


@router.post("/record-quote")
def record_quote(req: RecordQuoteRequest, x_openclaw_key: Optional[str] = Header(default=None)):
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    linked = _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)
    _require_current_bundle_rfq(req.client_id, req.supplier_id, req.rfq_id)
    if linked.get("status") != "active":
        raise HTTPException(status_code=409, detail="Closed RFQ cannot receive a new supplier quotation.")

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

    if req.related_rfq_id:
        linked = _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.related_rfq_id)
        if linked.get("status") != "active":
            raise HTTPException(status_code=409, detail="Stale or closed RFQ. Resolve current active RFQ before logging.")

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



class SupplierRevisionRequest(BaseModel):
    client_id: str
    supplier_id: str
    rfq_id: str
    new_quote_id: str


@router.post("/mark-quote-revision")
def mark_quote_revision(req: SupplierRevisionRequest, x_openclaw_key: Optional[str] = Header(default=None)):
    """Option B: historical accepted deal remains recorded but becomes invalid."""
    _require_openclaw_key(x_openclaw_key)
    if db.is_client_automation_paused(req.client_id):
        raise HTTPException(status_code=409, detail="Client is paused; human action required.")
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)
    result = db.mark_supplier_quote_revision(req.client_id, req.rfq_id, req.supplier_id, req.new_quote_id)
    if not result:
        raise HTTPException(status_code=409, detail="Could not mark revision pending; check accepted and revised quotes.")
    return result


@router.post("/accept-negotiated-quote")
def accept_negotiated_quote(
    req: AcceptNegotiatedQuoteRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    _require_openclaw_key(x_openclaw_key)
    _get_supplier_for_client(req.client_id, req.supplier_id, None)
    rfq = _get_rfq_and_verify_link(req.client_id, req.supplier_id, req.rfq_id)
    # A separate acceptance endpoint must enforce the same buyer limits as
    # counteroffers; reviewing a counter cannot authorize accepting a deal.
    if not req.approval_id:
        raise HTTPException(status_code=409, detail="Acceptance requires approved commercial review ID.")
    if not req.quote_id:
        raise HTTPException(status_code=422, detail="Acceptance requires an exact persisted quote_id.")
    quote_res = (
        db.supabase.table("quotes").select("id,price,quantity,is_available")
        .eq("id", req.quote_id).eq("rfq_id", req.rfq_id)
        .eq("supplier_id", req.supplier_id).limit(1).execute()
    )
    quote = quote_res.data[0] if quote_res.data else None
    if not quote or not quote.get("is_available") or quote.get("price") is None:
        raise HTTPException(status_code=409, detail="Quote is not available for acceptance.")
    price_cap = rfq.get("acceptable_price_max")
    if price_cap is not None and float(quote["price"]) > float(price_cap):
        raise HTTPException(status_code=409, detail="Quote exceeds buyer-approved maximum; human override required.")
    if req.price is not None and float(req.price) != float(quote["price"]):
        raise HTTPException(status_code=409, detail="Acceptance price differs from persisted quote.")
    authorized_quantity = rfq.get("quantity")
    if authorized_quantity is not None and quote.get("quantity") is not None:
        if int(quote["quantity"]) != int(authorized_quantity):
            raise HTTPException(status_code=409, detail="Quote quantity differs from RFQ; human approval required.")

    claim = db.supabase.rpc("claim_quote_acceptance_approval", {
        "p_approval_id": req.approval_id,
        "p_client_id": req.client_id,
        "p_supplier_id": req.supplier_id,
        "p_rfq_id": req.rfq_id,
        "p_quote_id": req.quote_id,
    }).execute()
    if not claim.data:
        raise HTTPException(status_code=409, detail="Acceptance approval invalid, expired, mismatched, or already consumed.")

    accepted = db.accept_recorded_quote(
        req.client_id,
        req.rfq_id,
        req.supplier_id,
        price=req.price,
        quantity=req.quantity,
        accepted_by=req.accepted_by,
        quote_id=req.quote_id,
    )
    if not accepted:
        raise HTTPException(
            status_code=409,
            detail="Quote cannot be accepted: no matching persisted quote or no valid in-flight negotiation.",
        )
    return {"accepted": True, "quote": accepted}


@router.post("/prepare-outbound-delivery")
def prepare_outbound_delivery(req: PrepareOutboundDeliveryRequest, x_openclaw_key: Optional[str] = Header(default=None)):
    """Bind a pending outgoing WhatsApp turn to an existing row before transport.

    No database rows are created and no commercial authorization decisions occur.
    """
    _require_openclaw_key(x_openclaw_key)
    supplier = _get_supplier_for_client(req.client_id, req.supplier_id, req.phone_number)
    sent_body = req.body or ""
    canonical_body = re.sub(r"^\[(?:Procurment|Procurement) Agent\]\s*", "", sent_body, count=1)
    matches = []
    for body in dict.fromkeys([canonical_body, sent_body]):
        rows = (
            db.supabase.table("message_log").select("id,status,body,created_at")
            .eq("client_id", req.client_id).eq("supplier_id", supplier["id"])
            .eq("direction", "outbound").eq("body", body)
            .in_("status", ["queued", "sending"])
            .order("created_at", desc=True).limit(2).execute()
        ).data or []
        matches.extend(rows)
    unique = {r["id"]: r for r in matches}
    if len(unique) != 1:
        return {"bound": False, "reason": "no_unique_pending_outbound_record"}
    return {"bound": True, "message_log_id": next(iter(unique))}


@router.post("/message-delivery")
def message_delivery(
    req: MessageDeliveryRequest,
    x_openclaw_key: Optional[str] = Header(default=None),
):
    _require_openclaw_key(x_openclaw_key)
    supplier = _get_supplier_for_client(req.client_id, req.supplier_id, req.phone_number)

    # OpenClaw's WhatsApp reply tag is added at the transport layer, after
    # procurement_log_message saved the undecorated text. Normalize ONLY the
    # known tag; never fuzzy-match unrelated commercial messages.
    sent_body = req.body or ""
    canonical_body = re.sub(r"^\[Procurment Agent\]\s*", "", sent_body, count=1)
    canonical_body = re.sub(r"^\[Procurement Agent\]\s*", "", canonical_body, count=1)

    if req.message_log_id:
        found = (
            db.supabase.table("message_log").select("*")
            .eq("id", req.message_log_id)
            .eq("client_id", req.client_id)
            .eq("supplier_id", supplier["id"])
            .eq("direction", "outbound")
            .limit(1).execute()
        )
        row = (found.data or [None])[0]
        if row and row.get("body") not in {canonical_body, sent_body}:
            raise HTTPException(status_code=409, detail="Bound outbound row text differs from delivered message")
    else:
        # Legacy source without a bound row: exact body fallback only.
        row = db.find_latest_message_by_body(
            client_id=req.client_id,
            supplier_id=supplier["id"],
            direction="outbound",
            body=canonical_body,
            related_rfq_id=req.related_rfq_id,
        )
    if not row:
        # Delivery callbacks reconcile an existing message, never invent a
        # standalone sent record (which could conceal a broken outbound log).
        raise HTTPException(status_code=404, detail="No matching outbound message to reconcile")
    row_id = row["id"]
    if req.success:
        if not req.external_message_id:
            raise HTTPException(status_code=422, detail="Successful delivery must include platform message ID")
        if row.get("status") == "sent":
            if row.get("external_message_id") == req.external_message_id:
                return {"updated": True, "message_log_id": row_id, "already_recorded": True}
            raise HTTPException(status_code=409, detail="Message already marked sent with a different platform ID")
        updated = db.mark_message_sent(row_id, external_message_id=req.external_message_id)
    else:
        if row.get("status") == "sent":
            return {"updated": False, "message_log_id": row_id, "reason": "already_sent"}
        updated = db.mark_message_failed(row_id, req.error or "OpenClaw delivery failed")
    if not updated:
        raise HTTPException(status_code=503, detail="Could not persist delivery status")
    return {"updated": True, "message_log_id": row_id}
