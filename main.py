"""
Amafha — WhatsApp RFQ agent webhook.
Evolution API posts incoming supplier messages here. This replaces the
old n8n branching logic with a single agent decision + tool execution.
"""

import asyncio
import csv
import io
import json
import os
import random
import re
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import math
import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
import requests
from starlette.concurrency import run_in_threadpool

import db
import logging
from auth import get_current_user, verify_jwt
import groq_client
from groq_client import AgentContext
import guardrails
from policy_validator import ActionProposal, validate_action, ActionCategory, ValidationResult, MAX_NEGOTIATION_ATTEMPTS
import negotiation_engine

logger = logging.getLogger(__name__)



scheduler = AsyncIOScheduler()

DEMO_CLIENT_ID = "d88c52ad-3d0b-42e9-86f1-b9f70018856b"
THANK_YOU_MSG = "Thanks for the quote! We'll be in touch if we move forward."
UNAVAILABLE_ACK_MSG = "Thanks for letting us know."
HUMAN_ACK_MSG = "Thanks! We'll review your response and get back to you shortly."

MAX_NO_PROGRESS_ATTEMPTS = 2
MAX_TOTAL_CLARIFICATION_TURNS = 5

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")
EVOLUTION_TYPING_DELAY_MS = int(os.getenv("EVOLUTION_TYPING_DELAY_MS", "1200"))

OUTBOUND_MIN_DELAY = float(os.getenv("OUTBOUND_MIN_DELAY", "3.0"))
OUTBOUND_MAX_DELAY = float(os.getenv("OUTBOUND_MAX_DELAY", "8.0"))

outbound_queue: asyncio.Queue = asyncio.Queue()


class PasswordResetRequest(BaseModel):
    password: str = Field(..., min_length=6, max_length=200)


class RFQCreateRequest(BaseModel):
    product_name: str
    category: Optional[str] = None
    categories: Optional[List[str]] = None
    supplier_ids: Optional[List[str]] = None
    targeting_mode: str = "category"
    specs: str
    quantity: Optional[int] = None
    last_quote: Optional[float] = None
    acceptable_price_min: Optional[float] = None
    acceptable_price_max: Optional[float] = None
    deadline_hours: int = Field(..., gt=0)
    required_delivery_days: Optional[int] = None
    flexibility: Optional[dict] = None

    @field_validator("product_name", "specs")
    @classmethod
    def validate_non_empty(cls, v: str, info: ValidationInfo) -> str:
        if v is None or not str(v).strip():
            raise ValueError(f"'{info.field_name}' cannot be empty or blank")
        return str(v).strip()

    @field_validator("deadline_hours", "required_delivery_days")
    @classmethod
    def validate_positive_ints(cls, v: Optional[int], info: ValidationInfo) -> Optional[int]:
        if v is not None:
            if int(v) <= 0:
                raise ValueError(f"'{info.field_name}' must be a positive integer")
            return int(v)
        return v

    @field_validator("acceptable_price_min", "acceptable_price_max", "last_quote")
    @classmethod
    def validate_price_bounds(cls, v: Optional[float], info: ValidationInfo) -> Optional[float]:
        if v is not None:
            try:
                val = float(v)
                if math.isnan(val) or math.isinf(val) or val <= 0:
                    raise ValueError(f"'{info.field_name}' must be a finite, positive number")
                return val
            except (ValueError, TypeError):
                raise ValueError(f"'{info.field_name}' must be a finite, positive number")
        return v

    @model_validator(mode="after")
    def validate_rfq(self) -> "RFQCreateRequest":
        clean_categories = []
        for raw in (self.categories or ([self.category] if self.category else [])):
            clean = str(raw or "").strip()
            if clean and clean not in clean_categories:
                clean_categories.append(clean)
        if not clean_categories:
            raise ValueError("At least one supplier category is required")
        self.categories = clean_categories
        self.category = clean_categories[0]

        mode = (self.targeting_mode or "category").strip().lower()
        if mode not in {"category", "selected"}:
            raise ValueError("targeting_mode must be 'category' or 'selected'")
        self.targeting_mode = mode

        clean_supplier_ids = []
        for raw in (self.supplier_ids or []):
            sid = str(raw or "").strip()
            if sid and sid not in clean_supplier_ids:
                clean_supplier_ids.append(sid)
        self.supplier_ids = clean_supplier_ids
        if mode == "selected" and not clean_supplier_ids:
            raise ValueError("Select at least one supplier when targeting_mode is 'selected'")

        if self.acceptable_price_min is not None and self.acceptable_price_max is not None:
            if self.acceptable_price_min > self.acceptable_price_max:
                raise ValueError("acceptable_price_min cannot be greater than acceptable_price_max")

        if self.flexibility and isinstance(self.flexibility, dict):
            qty_dict = self.flexibility.get("quantity")
            if isinstance(qty_dict, dict) and qty_dict.get("authorized"):
                q_min = qty_dict.get("min")
                q_max = qty_dict.get("max")
                if q_min is not None and int(q_min) <= 0:
                    raise ValueError("quantity min must be a positive integer")
                if q_max is not None and int(q_max) <= 0:
                    raise ValueError("quantity max must be a positive integer")
                if q_min is not None and q_max is not None and int(q_min) > int(q_max):
                    raise ValueError("quantity min cannot be greater than max")
        return self


@app.post("/rfq/create")
async def create_rfq_endpoint(req: RFQCreateRequest, current_user=Depends(get_current_user)):
    """Create an RFQ and send it to category matches or explicitly selected suppliers."""
    client_id = current_user.get("client_id")

    create_kwargs = {
        "client_id": client_id,
        "product_name": req.product_name,
        "category": req.category,
        "categories": req.categories,
        "supplier_ids": req.supplier_ids,
        "targeting_mode": req.targeting_mode,
        "deadline_hours": req.deadline_hours,
        "specs": req.specs,
        "quantity": req.quantity,
        "required_delivery_days": req.required_delivery_days,
        "flexibility": req.flexibility,
        "authorized_by": current_user.get("id"),
    }
    if req.last_quote is not None:
        create_kwargs["last_quote"] = req.last_quote
    if req.acceptable_price_min is not None:
        create_kwargs["acceptable_price_min"] = req.acceptable_price_min
    if req.acceptable_price_max is not None:
        create_kwargs["acceptable_price_max"] = req.acceptable_price_max

    rfq, matched_suppliers = db.create_rfq_and_match_suppliers(**create_kwargs)

    if not matched_suppliers:
        return {
            "status": "no_matching_suppliers",
            "rfq_id": rfq["id"],
            "categories": req.categories,
            "targeting_mode": req.targeting_mode,
            "message": (
                "RFQ created, but none of the selected suppliers are active."
                if req.targeting_mode == "selected"
                else f"RFQ created, but no active suppliers matched any selected category: {', '.join(req.categories or [])}."
            ),
        }

    rfq_msg = build_rfq_invitation_message(
        product_name=req.product_name,
        specs=req.specs,
        quantity=req.quantity,
        deadline_hours=req.deadline_hours,
        required_delivery_days=req.required_delivery_days,
    )

    for supplier in matched_suppliers:
        msg_log_id = db.log_message(client_id, supplier["id"], "outbound", rfq_msg, related_rfq_id=rfq["id"])
        await enqueue_message(
            supplier["phone_number"],
            rfq_msg,
            rfq_id=rfq["id"],
            supplier_id=supplier["id"],
            message_log_id=msg_log_id,
        )

    return {
        "status": "success",
        "rfq_id": rfq["id"],
        "categories": req.categories,
        "targeting_mode": req.targeting_mode,
        "matched_suppliers_count": len(matched_suppliers),
        "suppliers": [{"id": s["id"], "name": s["name"], "phone": s["phone_number"]} for s in matched_suppliers],
    }


@app.post("/rfq/bulk-create")
async def bulk_create_rfq_endpoint(
    request: Request,
    file: UploadFile = File(...),
    category: Optional[str] = Form(default=None),
    deadline_hours: Optional[int] = Form(default=None),
    acceptable_price_min: Optional[float] = Form(default=None),
    acceptable_price_max: Optional[float] = Form(default=None),
    row_categories: Optional[str] = Form(default=None),
    row_updates: Optional[str] = Form(default=None),
    current_user=Depends(get_current_user),
):
    """Uploads a CSV material requisition sheet and creates one RFQ per row."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are supported for bulk RFQ import. Please export to .csv first.")

    raw_bytes = await file.read()
    try:
        csv_text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        csv_text = raw_bytes.decode("latin-1")

    rows = parse_material_requisition_csv(csv_text)
    if not rows:
        raise HTTPException(status_code=400, detail="No valid RFQ rows were found in the uploaded CSV.")

    try:
        overrides = json.loads(row_categories) if row_categories else []
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="row_categories must be valid JSON when provided")

    if isinstance(overrides, dict):
        overrides = [overrides.get(str(i)) for i in range(len(rows))]

    if not isinstance(overrides, list):
        overrides = []

    # Parse optional row-level updates (product_name, quantity, category, deadline_hours, specs, acceptable_price_min, acceptable_price_max, last_quote)
    try:
        updates = json.loads(row_updates) if row_updates else []
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="row_updates must be valid JSON when provided")

    if isinstance(updates, dict):
        updates = [updates.get(str(i)) for i in range(len(rows))]

    if not isinstance(updates, list):
        updates = []

    created_rfqs = []
    failed_rows = []
    matched_summary = []
    default_deadline = deadline_hours if deadline_hours and deadline_hours > 0 else 24

    for idx, row in enumerate(rows):
        row_category = (overrides[idx] if idx < len(overrides) and overrides[idx] not in (None, "") else category or "").strip()
        row_update = updates[idx] if idx < len(updates) else None
        if not row_category:
            failed_rows.append({"row_number": row["row_number"], "reason": "Missing category. Assign a default category or select one in the preview step."})
            continue

        # Apply row-level overrides when provided
        final_product_name = None
        final_quantity = None
        final_specs = None
        final_deadline = default_deadline
        final_category = row_category
        final_min = acceptable_price_min
        final_max = acceptable_price_max
        final_last_quote = None

        if row_update and isinstance(row_update, dict):
            if row_update.get("product_name") not in (None, ""):
                final_product_name = str(row_update.get("product_name")).strip()
            if row_update.get("quantity") not in (None, ""):
                try:
                    final_quantity = int(float(row_update.get("quantity")))
                except Exception:
                    final_quantity = None
            if row_update.get("specs") not in (None, ""):
                final_specs = str(row_update.get("specs")).strip()
            if row_update.get("deadline_hours") not in (None, ""):
                try:
                    dh = int(row_update.get("deadline_hours"))
                    if dh > 0:
                        final_deadline = dh
                except Exception:
                    pass
            if row_update.get("category") not in (None, ""):
                final_category = str(row_update.get("category")).strip()
            if row_update.get("acceptable_price_min") not in (None, ""):
                try:
                    pmin = float(row_update.get("acceptable_price_min"))
                    if not math.isnan(pmin) and not math.isinf(pmin) and pmin > 0:
                        final_min = pmin
                except Exception:
                    pass
            if row_update.get("acceptable_price_max") not in (None, ""):
                try:
                    pmax = float(row_update.get("acceptable_price_max"))
                    if not math.isnan(pmax) and not math.isinf(pmax) and pmax > 0:
                        final_max = pmax
                except Exception:
                    pass
            if row_update.get("last_quote") not in (None, ""):
                try:
                    plq = float(str(row_update.get("last_quote")).replace(",", ""))
                    if not math.isnan(plq) and not math.isinf(plq) and plq > 0:
                        final_last_quote = plq
                except Exception:
                    pass

        if final_last_quote is None and row.get("last_quote") is not None:
            try:
                plq = float(str(row.get("last_quote")).replace(",", ""))
                if not math.isnan(plq) and not math.isinf(plq) and plq > 0:
                    final_last_quote = plq
            except Exception:
                pass

        if final_min is not None and final_max is not None and final_min > final_max:
            final_min, final_max = final_max, final_min

        # Fallback to parsed values when override missing
        if final_product_name in (None, ""):
            final_product_name = normalize_bulk_description(row["product_name"]) or row.get("product_name")
        if final_specs in (None, ""):
            final_specs = row.get("specs")
        if final_quantity is None:
            final_quantity = row.get("quantity")

        if not final_product_name:
            failed_rows.append({"row_number": row["row_number"], "reason": "Missing Description column"})
            continue

        # Tenant is derived exclusively from authenticated user's profile.
        tenant_client_id = current_user.get("client_id")

        create_kwargs = {
            "client_id": tenant_client_id,
            "product_name": final_product_name,
            "category": final_category,
            "deadline_hours": final_deadline,
            "specs": final_specs,
            "quantity": final_quantity,
            "flexibility": None,
            "authorized_by": current_user.get("id"),
        }
        if final_last_quote is not None:
            create_kwargs["last_quote"] = final_last_quote
        if final_min is not None:
            create_kwargs["acceptable_price_min"] = final_min
        if final_max is not None:
            create_kwargs["acceptable_price_max"] = final_max

        rfq, matched_suppliers = db.create_rfq_and_match_suppliers(**create_kwargs)

        if matched_suppliers:
            rfq_msg = build_rfq_invitation_message(
                product_name=final_product_name,
                specs=final_specs,
                quantity=final_quantity,
                deadline_hours=final_deadline,
                required_delivery_days=None,
            )
            for supplier in matched_suppliers:
                phone = supplier.get("phone_number")
                if phone:
                    msg_log_id = db.log_message(tenant_client_id, supplier["id"], "outbound", rfq_msg, related_rfq_id=rfq["id"])
                    await enqueue_message(phone, rfq_msg, rfq_id=rfq["id"], supplier_id=supplier["id"], message_log_id=msg_log_id)

        created_rfqs.append({
            "rfq_id": rfq["id"],
            "product_name": final_product_name,
            "category": final_category,
            "matched_suppliers_count": len(matched_suppliers),
            "matched_suppliers": [{"id": s["id"], "name": s["name"], "phone_number": s.get("phone_number")} for s in matched_suppliers],
        })
        matched_summary.append({"rfq_id": rfq["id"], "matched_suppliers_count": len(matched_suppliers)})

    return {
        "status": "success",
        "created_count": len(created_rfqs),
        "rows_processed": len(rows),
        "rfqs": created_rfqs,
        "matched_suppliers_summary": matched_summary,
        "failed_rows": failed_rows,
    }


class FlagRespondRequest(BaseModel):
    response: str
    send_to_supplier: bool = True


class FinalQuoteDecisionRequest(BaseModel):
    decision: str


@app.get("/rfqs/audit")
async def get_rfq_audit_endpoint(request: Request, current_user=Depends(get_current_user)):
    """Returns RFQs with null category/deadline values or zero matched suppliers; admin-only."""
    # allow admin API key as before for operational tooling
    admin_key = os.getenv("ADMIN_API_KEY", "").strip()
    if not admin_key:
        # require profile role admin
        if current_user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")

    return db.get_incomplete_rfqs_audit(current_user.get("client_id"))


@app.get("/flags")
async def get_flags_endpoint(current_user=Depends(get_current_user)):
    """Lists human review escalations for a client."""
    return db.get_pending_flags(current_user.get("client_id"))


@app.post("/flags/{flag_id}/resolve")
async def resolve_flag_endpoint(flag_id: str, current_user=Depends(get_current_user)):
    """Marks a human escalation flag as resolved (explicit administrative dismissal, strictly tenant-scoped)."""
    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=401, detail="Authentication missing client_id")

    result = db.resolve_flag(flag_id, client_id=client_id)
    if not result:
        raise HTTPException(status_code=404, detail="Flagged item not found")
    return {"status": "resolved", "flag": result}


@app.post("/flags/{flag_id}/final-quote-decision")
async def final_quote_decision_endpoint(
    flag_id: str,
    payload: FinalQuoteDecisionRequest,
    current_user=Depends(get_current_user),
):
    """Resolve a supplier-declared final quote without closing the whole RFQ."""
    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=401, detail="Authentication missing client_id")

    flag = db.get_flag_by_id(flag_id, client_id=client_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Flagged item not found")
    meta = flag.get("metadata") or {}
    if flag.get("category") != "supplier_final_quote_decision" and meta.get("type") != "supplier_final_quote_decision":
        raise HTTPException(status_code=400, detail="Flag is not a final-quote decision")

    decision = (payload.decision or "").strip().lower()
    if decision not in {"keep_for_evaluation", "end_supplier"}:
        raise HTTPException(status_code=400, detail="decision must be keep_for_evaluation or end_supplier")

    rfq_id = flag.get("rfq_id")
    supplier_id = flag.get("supplier_id")
    if not rfq_id or not supplier_id:
        raise HTTPException(status_code=400, detail="Flag is missing RFQ or supplier context")

    # This action is supplier-scoped. It never closes the whole RFQ.
    supplier_status = "responded" if decision == "keep_for_evaluation" else "closed"
    update_res = (
        db.supabase.table("rfq_suppliers")
        .update({"status": supplier_status})
        .eq("rfq_id", rfq_id)
        .eq("supplier_id", supplier_id)
        .execute()
    )
    if not update_res.data:
        raise HTTPException(status_code=404, detail="RFQ supplier relationship not found")

    try:
        active_session = db.get_active_negotiation_session(client_id, rfq_id, supplier_id)
        if active_session and active_session.get("id"):
            db.complete_negotiation_session(
                active_session["id"],
                status="supplier_final" if decision == "keep_for_evaluation" else "closed",
            )
    except Exception as session_err:
        logger.warning("Could not finalize negotiation session for final quote decision: %s", session_err)

    human_response = (
        "Final supplier quote kept for RFQ evaluation."
        if decision == "keep_for_evaluation"
        else "Supplier negotiation ended for this RFQ; final quote remains in quote history."
    )
    resolved = db.resolve_flag(flag_id, client_id=client_id, human_response=human_response)
    if not resolved:
        raise HTTPException(status_code=409, detail="Could not resolve final-quote decision flag")

    return {
        "status": "resolved",
        "decision": decision,
        "rfq_id": rfq_id,
        "supplier_id": supplier_id,
        "supplier_status": supplier_status,
        "quote_id": meta.get("quote_id"),
    }


@app.post("/flags/{flag_id}/respond")
async def respond_to_flag_endpoint(flag_id: str, payload: FlagRespondRequest, current_user=Depends(get_current_user)):
    """
    Phase 9: Operator Instruction Reasoning Pipeline.
    1. Authenticates user and verifies tenant flag ownership.
    2. If send_to_supplier is False, performs administrative resolve without messaging.
    3. If send_to_supplier is True:
       - Atomically claims pending flag (pending -> processing).
       - Derives trusted tenant, supplier, and locked RFQ from flag.
       - Assembles full AgentContext(input_origin="operator").
       - Invokes reason_about_procurement_message(payload.response, context).
       - Validates proposal with Policy Validator (enforcing stanza/operator RFQ lock).
       - Executes validated action and durable message persistence.
       - Marks flag as resolved (processing -> resolved).
       - On reasoning/validation/execution failure, safely releases claim (processing -> pending).
    """
    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=401, detail="Authentication missing client_id")

    # If operator requested administrative resolution without sending to supplier
    if not payload.send_to_supplier:
        updated = db.resolve_flag_with_response(flag_id, payload.response, client_id=client_id)
        if not updated:
            existing = db.get_flag_by_id(flag_id, client_id=client_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Flagged item not found")
        return {"status": "resolved", "flag_id": flag_id, "sent_to_supplier": False}

    # Atomic claim: pending -> processing
    flag = db.claim_flag_for_operator_action(flag_id, client_id)
    if not flag:
        existing = db.get_flag_by_id(flag_id, client_id=client_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Flagged item not found")
        return JSONResponse(
            status_code=409,
            content={"status": "conflict", "detail": f"Flag is already {existing.get('status')}"},
        )

    supplier = flag.get("suppliers")
    if not supplier:
        db.release_flag_claim(flag_id, client_id)
        raise HTTPException(status_code=400, detail="Supplier not associated with flag")

    rfq_id = flag.get("rfq_id")

    try:
        # Load supplier open RFQs and context
        open_rfqs = db.get_open_rfqs_for_supplier(supplier["id"]) or []
        open_rfq_ids = []
        for e in open_rfqs:
            rfq_obj = e.get("rfqs", e) if isinstance(e, dict) else e
            if isinstance(rfq_obj, dict) and rfq_obj.get("id"):
                open_rfq_ids.append(rfq_obj["id"])

        prior_quotes = []
        if open_rfq_ids:
            try:
                prior_quotes = db.get_supplier_prior_quotes(supplier["id"], open_rfq_ids)
            except Exception as e:
                logger.warning(f"Error loading prior quotes: {e}")

        negotiation_attempts = {}
        for r_id in open_rfq_ids:
            try:
                negotiation_attempts[r_id] = db.get_negotiation_attempts(r_id, supplier["id"])
            except Exception as e:
                logger.warning(f"Error loading negotiation attempts for RFQ {r_id}: {e}")
                negotiation_attempts[r_id] = 0

        competitive_context = {}
        for r_id in open_rfq_ids:
            try:
                comp_ctx = db.get_competitive_pricing_context(r_id, supplier["id"])
                if isinstance(comp_ctx, dict) and comp_ctx.get("has_competition"):
                    competitive_context[r_id] = f"Best competing quote is AED {comp_ctx['best_competing_price']} (from {comp_ctx['competing_quotes_count']} other supplier(s))"
            except Exception as e:
                logger.warning(f"Error loading competitive context for RFQ {r_id}: {e}")

        # Fetch pending clarification if any
        pending_clarification = None
        try:
            pending_clarification = db.get_pending_clarification_for_supplier(supplier["id"])
        except Exception as e:
            logger.warning(f"Error loading pending clarification: {e}")

        # Fetch conversation history
        conv_history = []
        try:
            conv_history = db.get_supplier_conversation_history(client_id, supplier["id"], limit=10)
        except Exception as e:
            logger.warning(f"Error loading conversation history: {e}")

        # Build single unified AgentContext with input_origin="operator" and operator flag lock
        operator_active_session = None
        if rfq_id:
            try:
                candidate_session = db.get_active_negotiation_session_for_supplier(client_id, supplier["id"])
                if candidate_session and str(candidate_session.get("rfq_id")) == str(rfq_id):
                    operator_active_session = candidate_session
            except Exception as session_err:
                logger.warning("Could not load operator negotiation session: %s", session_err)

        context = AgentContext(
            client_id=client_id,
            supplier_id=supplier["id"],
            supplier_name=supplier.get("name"),
            supplier_phone=supplier.get("phone_number"),
            input_origin="operator",
            matched_rfq_id=rfq_id,
            match_source="operator_flag" if rfq_id else None,
            open_rfqs=open_rfqs,
            pending_clarification=pending_clarification,
            prior_quotes=prior_quotes,
            negotiation_attempts=negotiation_attempts,
            active_negotiation_session=operator_active_session,
            competitive_context=competitive_context,
            conversation_history=conv_history,
            review_flag_id=flag.get("id"),
            review_reason=flag.get("reason"),
            review_category=flag.get("category"),
            review_raw_message=flag.get("raw_message"),
        )

        # Unified LLM reasoning
        proposal_dict = groq_client.reason_about_procurement_message(
            payload.response,
            context,
            input_origin="operator",
        )
        action_proposal = ActionProposal(
            tool_name=proposal_dict.get("tool_name", ""),
            arguments=proposal_dict.get("arguments", {}),
        )

        # Policy Validator with operator RFQ lock & clarification context
        validation = validate_action(
            proposal=action_proposal,
            client_id=client_id,
            supplier_id=supplier["id"],
            context_rfqs=context.open_rfqs,
            matched_rfq_id=rfq_id,
            pending_clarification=context.pending_clarification,
            input_origin="operator",
        )

        # Record Agent Decision for operator flow
        target_rfq_for_op = action_proposal.arguments.get("rfq_id") or rfq_id
        decision_record = db.record_agent_decision(
            client_id=client_id,
            origin="operator",
            tool_name=action_proposal.tool_name,
            arguments=action_proposal.arguments,
            validation_status="approved",
            validation_reason=None,
            execution_status="pending",
            rfq_id=target_rfq_for_op,
            supplier_id=supplier["id"],
            flag_id=flag_id,
        )
        decision_id = decision_record.get("id") if decision_record else None

        if not validation.is_valid:
            logger.warning(f"[POLICY REJECTION] Operator instruction failed policy: {validation.reason}")
            if decision_id:
                db.update_agent_decision(
                    decision_id,
                    validation_status="rejected",
                    validation_reason=validation.reason,
                    execution_status="not_executed",
                )
            db.release_flag_claim(flag_id, client_id)
            return JSONResponse(
                status_code=422,
                content={
                    "status": "rejected_by_policy",
                    "reason": validation.reason,
                    "proposal": action_proposal.model_dump(),
                },
            )

        # Execute validated action
        exec_result = await execute_validated_action(
            validation=validation,
            context=context,
            raw_message=payload.response,
            supplier=supplier,
            client_id=client_id,
            decision_id=decision_id,
        )

        # Complete flag transition to resolved
        db.complete_flag_operator_action(flag_id, client_id, payload.response)

        return {
            "status": "resolved",
            "flag_id": flag_id,
            "action": validation.action,
            "result": exec_result,
            "sent_to_supplier": True,
        }

    except Exception as e:
        logger.error(f"Error processing operator action for flag {flag_id}: {e}", exc_info=True)
        db.release_flag_claim(flag_id, client_id)
        if "decision_id" in locals() and decision_id:
            db.update_agent_decision(decision_id, execution_status="failed", execution_error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to process operator instruction: {str(e)}")


class NegotiationAuthorizationRequest(BaseModel):
    dimension: str
    decision: str  # "approve" | "reject"
    constraints: Optional[dict] = None
    resume_negotiation: bool = True


@app.post("/flags/{flag_id}/negotiation-authorization")
async def authorize_negotiation_tradeoff_endpoint(
    flag_id: str,
    payload: NegotiationAuthorizationRequest,
    current_user=Depends(get_current_user),
):
    """
    Handles structured human operator decisions for negotiation trade-offs.
    Persists updated negotiation authority, resolves the flag, and resumes negotiation if requested.
    """
    client_id = current_user.get("client_id")
    user_id = current_user.get("id")

    flag = db.get_flag_by_id(flag_id, client_id=client_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Flagged review item not found")

    rfq_id = flag.get("rfq_id")
    supplier_id = flag.get("supplier_id")
    if not rfq_id or not supplier_id:
        raise HTTPException(status_code=400, detail="Flag is missing RFQ or supplier association")

    rfq = db.get_rfq_by_id(rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="Associated RFQ not found")
    if rfq.get("status") != "active":
        raise HTTPException(status_code=400, detail=f"Cannot authorize negotiation on a {rfq.get('status')} RFQ")

    dimension = payload.dimension
    if dimension not in ("price", "quantity", "delivery", "specification"):
        raise HTTPException(status_code=400, detail=f"Invalid dimension '{dimension}'")

    decision = payload.decision.lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Decision must be 'approve' or 'reject'")

    status = "authorized" if decision == "approve" else "fixed"
    persisted_constraints = payload.constraints or {}

    if decision == "approve":
        if dimension == "delivery":
            max_days = persisted_constraints.get("max_days")
            if max_days is None or int(max_days) <= 0:
                raise HTTPException(status_code=400, detail="max_days must be a positive integer for delivery authorization")
        elif dimension == "quantity":
            q_min = persisted_constraints.get("min")
            q_max = persisted_constraints.get("max")
            if q_min is not None and q_max is not None and int(q_min) > int(q_max):
                raise HTTPException(status_code=400, detail="Quantity min cannot be greater than max")
        elif dimension == "specification":
            alt = persisted_constraints.get("allowed_alternatives")
            if not alt or not str(alt).strip():
                raise HTTPException(status_code=400, detail="allowed_alternatives cannot be empty for specification authorization")

    # 1. Persist negotiation authority
    saved_constraint = db.set_rfq_negotiation_constraint(
        client_id=client_id,
        rfq_id=rfq_id,
        dimension=dimension,
        status=status,
        constraints=persisted_constraints,
        source="operator_review",
        authorized_by=user_id,
    )
    if not saved_constraint:
        raise HTTPException(status_code=500, detail="Failed to persist negotiation authority to database")

    # 2. Resume exact paused session (if resume requested)
    outbound_sent = False
    resumed_session = None
    if payload.resume_negotiation:
        flag_meta = flag.get("metadata") or {}
        session_id = flag_meta.get("session_id")

        if session_id:
            resumed_session = db.resume_negotiation_session(
                session_id=str(session_id),
                client_id=client_id,
                rfq_id=rfq_id,
                supplier_id=supplier_id,
            )
        else:
            # Fallback to active session check only if session_id is absent from metadata
            resumed_session = db.get_active_negotiation_session(client_id, rfq_id, supplier_id)

        if not resumed_session:
            # Fail safely: keep flag pending and show error
            raise HTTPException(
                status_code=400,
                detail="Could not safely resume paused negotiation session. Associated session is missing, invalid, or expired.",
            )

    # 3. Resolve the flag only after successful persistence and (if requested) successful session resumption
    db.resolve_flag_with_response(
        flag_id=flag_id,
        human_response=f"Operator {decision}d {dimension} trade-off",
        client_id=client_id,
    )

    # 4. Continue from the supplier's EXISTING conditional offer.
    # Do not ask the supplier to repeat a price already captured in the flag/session.
    if payload.resume_negotiation and resumed_session:
        supplier = flag.get("suppliers") or db.get_supplier_by_id(supplier_id)
        phone = supplier.get("phone_number") if supplier else None
        flag_meta = flag.get("metadata") or {}

        if decision == "approve":
            supplier_price = flag_meta.get("supplier_latest_price")
            if supplier_price is None:
                supplier_price = resumed_session.get("latest_supplier_offer")

            counter_price = None
            strategy = None
            if supplier_price is not None:
                try:
                    supplier_price = float(supplier_price)
                    bounds = negotiation_engine.build_allowed_counter_range(
                        preferred_target=float(resumed_session.get("preferred_target")) if resumed_session.get("preferred_target") is not None else None,
                        acceptable_max=float(resumed_session.get("acceptable_max")) if resumed_session.get("acceptable_max") is not None else None,
                        tolerated_final_ceiling=float(resumed_session.get("tolerated_final_ceiling")) if resumed_session.get("tolerated_final_ceiling") is not None else None,
                        latest_supplier_offer=supplier_price,
                        previous_supplier_offer=float(resumed_session.get("previous_supplier_offer")) if resumed_session.get("previous_supplier_offer") is not None else None,
                        latest_agent_counter=float(resumed_session.get("latest_agent_counter")) if resumed_session.get("latest_agent_counter") is not None else None,
                        attempt_count=int(resumed_session.get("attempt_count") or 0),
                        max_attempts=MAX_NEGOTIATION_ATTEMPTS,
                        supplier_final_detected=bool(resumed_session.get("supplier_final_detected")),
                        no_movement_count=int(resumed_session.get("no_movement_count") or 0),
                    )
                    strategy = bounds.get("selected_strategy")
                    if bounds.get("should_counter") and bounds.get("recommended_anchor") is not None:
                        counter_price = float(bounds["recommended_anchor"])
                except (TypeError, ValueError):
                    logger.exception(
                        "Failed to calculate post-authorization counter for RFQ %s / supplier %s",
                        rfq_id,
                        supplier_id,
                    )

            if dimension == "delivery":
                days = persisted_constraints.get("max_days")
                if counter_price is not None:
                    resume_msg = (
                        f"Thanks, {days}-day delivery works for us. "
                        f"We have noted your AED {supplier_price:g} offer. "
                        f"Could you do AED {counter_price:g} per piece?"
                    )
                else:
                    resume_msg = (
                        f"Thanks, {days}-day delivery works for us. "
                        f"We have noted your AED {supplier_price:g} offer for evaluation."
                        if supplier_price is not None
                        else f"Thanks, {days}-day delivery works for us. We have noted the updated terms."
                    )
            elif dimension == "quantity":
                q_max = persisted_constraints.get("max")
                if counter_price is not None:
                    resume_msg = (
                        f"Thanks, we can work with quantity up to {q_max} units. "
                        f"We have noted your AED {supplier_price:g} offer. "
                        f"Could you do AED {counter_price:g} per piece?"
                    )
                else:
                    resume_msg = (
                        f"Thanks, we can work with quantity up to {q_max} units. "
                        f"We have noted your AED {supplier_price:g} offer for evaluation."
                        if supplier_price is not None
                        else f"Thanks, we can work with quantity up to {q_max} units. We have noted the updated terms."
                    )
            else:
                if counter_price is not None:
                    resume_msg = (
                        f"Thanks, we can consider that alternative. "
                        f"We have noted your AED {supplier_price:g} offer. "
                        f"Could you do AED {counter_price:g} per piece?"
                    )
                else:
                    resume_msg = (
                        f"Thanks, we can consider that alternative. "
                        f"We have noted your AED {supplier_price:g} offer for evaluation."
                        if supplier_price is not None
                        else "Thanks, we can consider that alternative. We have noted the updated terms."
                    )
        else:
            counter_price = None
            strategy = None
            if dimension == "delivery":
                resume_msg = "We need to keep the requested delivery schedule. Is there any further flexibility on the price within that requirement?"
            elif dimension == "quantity":
                qty = rfq.get("quantity") or "requested"
                resume_msg = f"We need to maintain the required quantity of {qty} units. Is there any flexibility on the unit price?"
            else:
                resume_msg = "We need to stick to the requested specifications. Is there any further flexibility on the price?"

        if phone:
            msg_log_id = db.log_message(client_id, supplier_id, "outbound", resume_msg, related_rfq_id=rfq_id)
            if msg_log_id:
                await enqueue_message(phone, resume_msg, rfq_id=rfq_id, supplier_id=supplier_id, message_log_id=msg_log_id)
                outbound_sent = True

                # A post-approval counter is a real negotiation attempt. Preserve the same
                # session and advance it instead of creating a new conversation/session.
                if decision == "approve" and counter_price is not None:
                    previous_counter = resumed_session.get("latest_agent_counter")
                    preferred_target = resumed_session.get("preferred_target")
                    next_attempt = int(resumed_session.get("attempt_count") or 0) + 1
                    db.create_or_update_negotiation_session(
                        client_id=client_id,
                        rfq_id=rfq_id,
                        supplier_id=supplier_id,
                        status="active",
                        previous_agent_counter=previous_counter,
                        latest_agent_counter=counter_price,
                        attempt_count=next_attempt,
                        agent_last_concession=(
                            round(counter_price - float(previous_counter), 2)
                            if previous_counter is not None else 0.0
                        ),
                        agent_total_concession=(
                            round(counter_price - float(preferred_target), 2)
                            if preferred_target is not None else 0.0
                        ),
                        selected_strategy=strategy or resumed_session.get("selected_strategy"),
                        last_outbound_message_id=msg_log_id,
                    )

    return {
        "status": "resolved",
        "flag_id": flag_id,
        "decision": decision,
        "dimension": dimension,
        "constraint": saved_constraint,
        "outbound_sent": outbound_sent,
        "session_id": resumed_session.get("id") if resumed_session else None,
    }


@app.get("/rfq/{rfq_id}/constraints")
async def get_rfq_constraints_endpoint(rfq_id: str, current_user=Depends(get_current_user)):
    """Fetches structured negotiation constraints for an RFQ."""
    client_id = current_user.get("client_id")
    constraints = db.get_rfq_negotiation_constraints(client_id, rfq_id)
    return {"rfq_id": rfq_id, "constraints": constraints}


# ============================================================
# Phase 14: Activity & Delivery Diagnostics Endpoints
# ============================================================

@app.get("/rfq/{rfq_id}/activity")
async def get_rfq_activity_endpoint(rfq_id: str, current_user=Depends(get_current_user)):
    """
    Admin-only endpoint returning structured chronological activity and decision audit timeline for an RFQ.
    Strictly isolated to current_user.client_id; non-admin receives 403, missing/cross-tenant RFQ receives 404.
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required to view activity audit logs")

    client_id = current_user.get("client_id")
    activity = db.get_rfq_activity(rfq_id, client_id)
    if activity is None:
        raise HTTPException(status_code=404, detail=f"RFQ '{rfq_id}' not found")

    return {
        "rfq_id": rfq_id,
        "count": len(activity),
        "activity": activity,
    }


@app.delete("/admin/categories/{category_name}")
async def delete_category_endpoint(
    category_name: str,
    current_user=Depends(get_current_user),
):
    """Admin-only clean category deletion for the current tenant."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="User has no associated client_id")

    result = db.delete_category_clean(client_id, category_name)
    if not result:
        raise HTTPException(status_code=404, detail="Category not found or could not be deleted")

    return {"status": "deleted", **result}


@app.delete("/admin/suppliers/{supplier_id}")
async def delete_supplier_endpoint(
    supplier_id: str,
    current_user=Depends(get_current_user),
):
    """Admin-only soft delete of a supplier contact for the current tenant."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="User has no associated client_id")

    deleted = db.soft_delete_supplier(supplier_id, client_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Supplier not found or already deleted")

    return {
        "status": "deleted",
        "supplier_id": supplier_id,
    }


@app.get("/admin/delivery-issues")
async def get_delivery_issues_endpoint(
    page: int = 1,
    limit: int = 50,
    current_user=Depends(get_current_user)
):
    """
    Admin-only endpoint returning paginated outbound messages with status 'failed' or 'unknown'
    for operational diagnostics.
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required to view delivery diagnostics")

    client_id = current_user.get("client_id")
    return db.get_delivery_issues(client_id, page=page, limit=limit)



@app.post("/rfq/{rfq_id}/rank")
async def rank_rfq_endpoint(rfq_id: str, current_user=Depends(get_current_user)):
    """Trigger the final comparison/ranking step for a closed or reviewable RFQ."""
    # auth ensures user belongs to RFQ's client via RLS when the ranking query runs
    return generate_ranking(rfq_id)


@app.post("/rfq/{rfq_id}/close")
async def close_rfq_endpoint(rfq_id: str, status: str = "closed", current_user=Depends(get_current_user)):
    """Closes or cancels an RFQ."""
    if status not in ("closed", "cancelled"):
        return {"error": "Invalid status. Must be 'closed' or 'cancelled'."}
    result = db.update_rfq_status(rfq_id, status)
    return {"status": status, "rfq": result}


class InviteCreateRequest(BaseModel):
    role: str = "member"
    email: Optional[str] = None


@app.post("/admin/invite")
async def invite_user_endpoint(
    req: InviteCreateRequest,
    current_user=Depends(get_current_user),
):
    """Admin-only endpoint to generate a secure opaque invite link for a team member."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="User has no associated client_id")

    role = req.role.strip().lower() if req.role in ("admin", "member") else "member"

    token_row = db.create_invite_token(
        client_id=client_id,
        role=role,
        created_by=current_user.get("user_id"),
    )

    frontend_url = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
    if not frontend_url:
        frontend_url = "https://powerful-rebirth-production.up.railway.app"

    invite_link = f"{frontend_url}/accept-invite?token={token_row['token']}"

    return {
        "status": "success",
        "token": token_row["token"],
        "invite_link": invite_link,
        "role": role,
        "client_id": client_id,
        "expires_at": token_row.get("expires_at"),
    }


@app.get("/invite/{token}")
async def get_invite_details_endpoint(token: str):
    """Public endpoint to validate an invite token and retrieve target client_id/role."""
    token_row = db.get_invite_token(token)
    if not token_row:
        raise HTTPException(status_code=404, detail="Invitation not found or invalid")

    if token_row.get("used_at"):
        raise HTTPException(status_code=410, detail="This invitation link has already been used")

    expires_at_str = token_row.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > expires_at:
                raise HTTPException(status_code=410, detail="This invitation link has expired")
        except Exception:
            pass

    return {
        "status": "valid",
        "client_id": token_row["client_id"],
        "role": token_row.get("role", "member"),
    }


@app.post("/invite/{token}/claim")
async def claim_invite_token_endpoint(token: str):
    """Public endpoint to mark an invite token as used after successful signup."""
    token_row = db.get_invite_token(token)
    if not token_row:
        raise HTTPException(status_code=404, detail="Invitation not found")
    db.mark_invite_token_used(token)
    return {"status": "claimed"}


def generate_daily_procurement_docx(date_str: str, rfq_data_list: list) -> bytes:
    """Generates a formatted Word (.docx) document summarizing daily RFQ activity."""
    doc = docx.Document()
    doc.add_heading(f"Daily Procurement Report — {date_str}", level=0)

    for item in rfq_data_list:
        rfq = item["rfq"]
        quotes = item["quotes"]
        top_quotes = item.get("top_quotes") or []

        doc.add_heading(f"RFQ: {rfq.get('product_name', 'Unnamed Product')}", level=1)

        p = doc.add_paragraph()
        p.add_run("Category: ").bold = True
        p.add_run(f"{rfq.get('category') or 'General'}   |   ")
        p.add_run("Quantity: ").bold = True
        p.add_run(f"{rfq.get('quantity') if rfq.get('quantity') is not None else 'N/A'}   |   ")
        p.add_run("Specs: ").bold = True
        p.add_run(f"{rfq.get('specs') or 'Standard'}   |   ")
        p.add_run("Deadline: ").bold = True
        p.add_run(f"{rfq.get('deadline_hours', 24)} hour(s)")

        if not quotes:
            doc.add_paragraph("No one responded to this RFQ.")
        else:
            table = doc.add_table(rows=1, cols=6)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER

            hdr_cells = table.rows[0].cells
            hdr_cells[0].text = "Rank"
            hdr_cells[1].text = "Supplier"
            hdr_cells[2].text = "Variant"
            hdr_cells[3].text = "Price"
            hdr_cells[4].text = "Delivery Time"
            hdr_cells[5].text = "Quality / Warranty Notes"

            for cell in hdr_cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.bold = True

            for q_idx, q in enumerate(top_quotes, 1):
                row_cells = table.add_row().cells
                rank_val = q.get("rank") if q.get("rank") is not None else q_idx
                row_cells[0].text = f"#{rank_val}"
                row_cells[1].text = str(q.get("supplier_name") or "Unknown")
                row_cells[2].text = str(q.get("variant_label") or "—")
                row_cells[3].text = f"AED {q.get('price')}" if q.get('price') is not None else "N/A"
                row_cells[4].text = str(q.get("delivery_time") or "Not specified")
                row_cells[5].text = str(q.get("quality_notes") or "Standard")

            if item.get("reasoning"):
                p = doc.add_paragraph()
                p.add_run("AI Reasoning: ").bold = True
                p.add_run(item["reasoning"])

        doc.add_paragraph()

    doc_io = io.BytesIO()
    doc.save(doc_io)
    doc_io.seek(0)
    return doc_io.getvalue()


@app.get("/reports/daily")
async def get_daily_report_endpoint(
    date: str,
    current_user=Depends(get_current_user),
):
    """Admin-only endpoint generating a daily procurement summary report (.docx)."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    try:
        target_date = datetime.strptime(date.strip(), "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="User has no associated client_id")

    start_dt = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc).isoformat()
    end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc).isoformat()

    rfqs = db.get_rfqs_by_date(client_id, start_dt, end_dt)

    if not rfqs:
        return JSONResponse(
            status_code=200,
            content={"status": "no_data", "message": "No RFQs were created on this date."}
        )

    rfq_data_list = []
    for rfq in rfqs:
        quotes = db.get_quotes_for_rfq(rfq["id"]) or []
        top_quotes = []
        reasoning = None

        if quotes:
            ranking_data = None
            existing_ranking = db.get_ranking_for_rfq(rfq["id"])
            if existing_ranking and existing_ranking.get("ranking_json"):
                ranking_data = existing_ranking.get("ranking_json")
                reasoning = ranking_data.get("reasoning") or existing_ranking.get("reasoning")
            else:
                try:
                    ranking_data = generate_ranking(rfq["id"])
                    if ranking_data:
                        reasoning = ranking_data.get("reasoning")
                except Exception as e:
                    print(f"[Daily Report] Failed generating ranking for RFQ {rfq['id']}: {e}")

            rank_map = {}
            if ranking_data and isinstance(ranking_data, dict) and ranking_data.get("ranking"):
                for item in ranking_data["ranking"]:
                    q_id = str(item.get("quote_id")) if item.get("quote_id") else None
                    if q_id:
                        rank_map[q_id] = item
                    elif item.get("supplier_id"):  # backward compatibility with legacy rankings
                        rank_map[str(item.get("supplier_id"))] = item

            if rank_map:
                sorted_quotes = sorted(
                    quotes,
                    key=lambda q: (
                        rank_map.get(str(q.get("id")), {}).get("rank")
                        or rank_map.get(str(q.get("supplier_id")), {}).get("rank", 999)
                    )
                )
            else:
                sorted_quotes = sorted(
                    quotes,
                    key=lambda q: q.get("price") if q.get("price") is not None else float("inf")
                )

            for q in sorted_quotes[:5]:
                rank_item = rank_map.get(str(q.get("id"))) or rank_map.get(str(q.get("supplier_id")), {})
                top_quotes.append({
                    "rank": rank_item.get("rank"),
                    "quote_id": q.get("id"),
                    "supplier_name": q.get("suppliers", {}).get("name") if isinstance(q.get("suppliers"), dict) else "Supplier",
                    "variant_label": q.get("variant_label"),
                    "price": q.get("price"),
                    "delivery_time": q.get("delivery_time"),
                    "quality_notes": q.get("quality_notes"),
                })

        rfq_data_list.append({
            "rfq": rfq,
            "quotes": quotes,
            "top_quotes": top_quotes,
            "reasoning": reasoning,
        })

    doc_bytes = generate_daily_procurement_docx(date.strip(), rfq_data_list)
    filename = f"Daily_Procurement_Report_{date.strip()}.docx"

    return StreamingResponse(
        io.BytesIO(doc_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


