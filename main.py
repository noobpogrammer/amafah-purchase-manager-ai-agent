"""
Amafha — procurement backend and deterministic negotiation authority.
OpenClaw owns supplier-facing WhatsApp transport and conversational orchestration.
FastAPI owns procurement state, policy, persistence, scheduling, and operator APIs.
"""

import operator_jobs
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
import openclaw_bridge
from negotiation_engine import MAX_NEGOTIATION_ATTEMPTS
import negotiation_engine
from openclaw_bridge import router as openclaw_router
from supplier_message_planner import BundleItem, plan_supplier_bundles, format_supplier_bundle

logger = logging.getLogger(__name__)


scheduler = AsyncIOScheduler()

DEMO_CLIENT_ID = "d88c52ad-3d0b-42e9-86f1-b9f70018856b"


OPENCLAW_BASE_URL = os.getenv("OPENCLAW_BASE_URL", "").rstrip("/")
OPENCLAW_TOOL_API_KEY = os.getenv("OPENCLAW_TOOL_API_KEY", "").strip()


def sync_openclaw_allowlist(phone_number: str, action: str) -> dict:
    """Add/remove a supplier phone from OpenClaw's WhatsApp allowlist.

    Fail closed for supplier mutations: if OpenClaw cannot be synchronized,
    the API raises so the frontend never reports a supplier as ready while
    WhatsApp admission is stale.
    """
    if not OPENCLAW_BASE_URL:
        raise HTTPException(status_code=503, detail="OpenClaw allowlist sync is not configured")
    if not OPENCLAW_TOOL_API_KEY:
        raise HTTPException(status_code=503, detail="OpenClaw tool authentication is not configured")

    try:
        response = requests.post(
            f"{OPENCLAW_BASE_URL}/internal/whatsapp/allowlist",
            headers={
                "Content-Type": "application/json",
                "X-OpenClaw-Key": OPENCLAW_TOOL_API_KEY,
            },
            json={"action": action, "phone_number": phone_number},
            timeout=12,
        )
    except requests.RequestException as exc:
        logger.exception("OpenClaw allowlist sync failed")
        raise HTTPException(status_code=502, detail="Could not synchronize supplier with WhatsApp") from exc

    if not response.ok:
        logger.error("OpenClaw allowlist sync failed status=%s body=%s", response.status_code, response.text[:500])
        raise HTTPException(status_code=502, detail="Could not synchronize supplier with WhatsApp")

    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid response from OpenClaw allowlist service") from exc

OUTBOUND_MIN_DELAY = float(os.getenv("OUTBOUND_MIN_DELAY", "3.0"))
OUTBOUND_MAX_DELAY = float(os.getenv("OUTBOUND_MAX_DELAY", "8.0"))


def send_via_openclaw_whatsapp(phone_number: str, message: str) -> dict:
    """Send a supplier-facing WhatsApp message through the OpenClaw wrapper."""
    if not OPENCLAW_BASE_URL:
        raise RuntimeError("OPENCLAW_BASE_URL is not configured")
    if not OPENCLAW_TOOL_API_KEY:
        raise RuntimeError("OPENCLAW_TOOL_API_KEY is not configured")

    response = requests.post(
        f"{OPENCLAW_BASE_URL}/internal/whatsapp/send",
        headers={
            "Content-Type": "application/json",
            "X-OpenClaw-Key": OPENCLAW_TOOL_API_KEY,
        },
        json={
            "phone_number": phone_number,
            "message": message,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "OpenClaw WhatsApp send failed")
    return payload


def send_scheduled_reminder_via_openclaw(
    phone_number: str,
    *,
    rfq_id: str,
    product_name: str,
    reminder_stage: str,
    suggested_message: str,
) -> dict:
    """Ask the supplier's OpenClaw session whether a scheduled reminder should be sent."""
    if not OPENCLAW_BASE_URL:
        raise RuntimeError("OPENCLAW_BASE_URL is not configured")
    if not OPENCLAW_TOOL_API_KEY:
        raise RuntimeError("OPENCLAW_TOOL_API_KEY is not configured")

    response = requests.post(
        f"{OPENCLAW_BASE_URL}/internal/procurement/reminder",
        headers={
            "Content-Type": "application/json",
            "X-OpenClaw-Key": OPENCLAW_TOOL_API_KEY,
        },
        json={
            "phone_number": phone_number,
            "rfq_id": rfq_id,
            "product_name": product_name,
            "reminder_stage": reminder_stage,
            "suggested_message": suggested_message,
        },
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "OpenClaw reminder check failed")
    return payload


def sync_existing_suppliers_to_openclaw(client_id: str) -> dict:
    """Merge all active suppliers for a tenant into the OpenClaw WhatsApp allowlist."""
    phones = db.list_supplier_phones_for_client(client_id)
    if not phones:
        return {"ok": True, "action": "merge", "allowFrom": []}
    if not OPENCLAW_BASE_URL or not OPENCLAW_TOOL_API_KEY:
        raise RuntimeError("OpenClaw allowlist sync is not configured")
    response = requests.post(
        f"{OPENCLAW_BASE_URL}/internal/whatsapp/allowlist/sync",
        headers={
            "Content-Type": "application/json",
            "X-OpenClaw-Key": OPENCLAW_TOOL_API_KEY,
        },
        json={"phone_numbers": phones},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"OpenClaw supplier backfill failed status={response.status_code} body={response.text[:500]}"
        )
    return response.json()


class SupplierMutationRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    phone_number: str = Field(..., min_length=8, max_length=32)
    category: List[str] = Field(default_factory=list)
    notes: Optional[str] = ""
    is_active: bool = True


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
            elif self.flexibility.get("quantity_flexible"):
                q_min = self.flexibility.get("quantity_min")
                q_max = self.flexibility.get("quantity_max")
                if q_min is not None and int(q_min) <= 0:
                    raise ValueError("quantity_min must be a positive integer")
                if q_max is not None and int(q_max) <= 0:
                    raise ValueError("quantity_max must be a positive integer")
                if q_min is not None and q_max is not None and int(q_min) > int(q_max):
                    raise ValueError("quantity_min cannot be greater than quantity_max")

            deliv_dict = self.flexibility.get("delivery")
            if isinstance(deliv_dict, dict) and deliv_dict.get("authorized"):
                d_max = deliv_dict.get("max_days")
                if d_max is not None and int(d_max) <= 0:
                    raise ValueError("delivery max_days must be a positive integer")
            elif self.flexibility.get("delivery_flexible"):
                d_max = self.flexibility.get("delivery_max_days")
                if d_max is not None and int(d_max) <= 0:
                    raise ValueError("delivery_max_days must be a positive integer")

            spec_dict = self.flexibility.get("specification")
            if isinstance(spec_dict, dict) and spec_dict.get("authorized"):
                alt = spec_dict.get("allowed_alternatives")
                if alt is not None and not str(alt).strip():
                    raise ValueError("specification allowed_alternatives cannot be empty when authorized")
            elif self.flexibility.get("specs_flexible"):
                alt = self.flexibility.get("allowed_alternatives")
                if alt is not None and not str(alt).strip():
                    raise ValueError("allowed_alternatives cannot be empty when authorized")
        return self

def normalize_bulk_description(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\d+\s*(pcs|pc|nos|bag|pkt)\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[,;]\s*$", "", text)
    return text.strip()


def extract_bulk_specs(description: str) -> Optional[str]:
    text = (description or "").strip()
    if not text:
        return None

    patterns = [
        r"\d+\s*X\s*\d+",
        r"\d+(?:\.\d+)?\s*(?:MM|CM|M|W|KW|V|A)",
        r"\d+(?:\.\d+)?\s*(?:x|X)\s*\d+(?:\.\d+)?",
    ]
    matches = []
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            matches.append(match.group(0).strip())

    if not matches:
        return None

    cleaned = []
    for item in matches:
        normalized = re.sub(r"\s+", "", item)
        if normalized not in cleaned:
            cleaned.append(normalized)
    return "/".join(cleaned)


def normalize_csv_key(value: Optional[str]) -> str:
    text = (value or "").lower().strip()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def extract_row_value(row: dict, aliases: list[str]):
    for raw_key, raw_value in row.items():
        normalized = normalize_csv_key(raw_key)
        if normalized in aliases:
            return raw_value
    return None


def normalize_requisition_row(row: dict, row_index: int) -> Optional[dict]:
    if not row:
        return None

    description = extract_row_value(row, ["description", "item description"])
    if description is None or not str(description).strip():
        return None

    description = str(description).strip()
    clean_description = normalize_bulk_description(description)
    qty_raw = extract_row_value(row, ["qty", "quantity"])
    last_quote_raw = extract_row_value(row, ["last cost", "last quote", "last qoute", "last quotation"])

    try:
        quantity = int(float(str(qty_raw).replace(",", "")))
    except (TypeError, ValueError):
        quantity = None

    try:
        if last_quote_raw not in (None, ""):
            lq_val = float(str(last_quote_raw).replace(",", ""))
            if math.isnan(lq_val) or math.isinf(lq_val) or lq_val <= 0:
                last_quote = None
            else:
                last_quote = lq_val
        else:
            last_quote = None
    except (TypeError, ValueError):
        last_quote = None

    product_name = clean_description if clean_description else description
    row_number_value = extract_row_value(row, ["sl", "sl #", "sl no", "sl no.", "sl no "])
    row_number = row_number_value if row_number_value not in (None, "") else row_index

    return {
        "row_number": row_number,
        "product_name": product_name,
        "specs": extract_bulk_specs(clean_description or description),
        "quantity": quantity,
        "last_quote": last_quote,
        "raw_description": description,
    }


def parse_material_requisition_csv(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for row_index, row in enumerate(reader, start=2):
        normalized = normalize_requisition_row(row, row_index)
        if normalized is not None:
            rows.append(normalized)
    return rows


def require_admin_access(request: Request):
    admin_key = os.getenv("ADMIN_API_KEY", "").strip()
    if not admin_key:
        return

    supplied = request.headers.get("x-admin-key") or request.headers.get("authorization", "").replace("Bearer ", "").strip()
    if not supplied or supplied != admin_key:
        raise HTTPException(status_code=401, detail="Admin authentication required")


async def enqueue_message(
    phone_number: str,
    message: str,
    rfq_id: str = None,
    supplier_id: str = None,
    message_log_id: str = None,
):
    """Send a durable supplier-facing WhatsApp message through OpenClaw."""
    if message_log_id:
        try:
            claimed = db.mark_message_sending(message_log_id)
        except Exception as claim_err:
            logger.warning("OpenClaw send claim failed for message %s: %s", message_log_id, claim_err)
            claimed = False
        if not claimed:
            logger.warning("[OpenClaw Outbound] Message %s was not claimable; skipping duplicate send.", message_log_id)
            return False
    try:
        payload = await run_in_threadpool(send_via_openclaw_whatsapp, phone_number, message)
        message_id = payload.get("message_id")
        if message_log_id:
            db.mark_message_sent(message_log_id, external_message_id=message_id)
        # Audit/debug correlation only. OpenClaw/WhatsApp owns native reply threading.
        if message_id and rfq_id and supplier_id:
            db.update_rfq_supplier_sent_message_id(rfq_id, supplier_id, str(message_id))
        logger.info(
            "[OpenClaw Outbound] Sent WhatsApp message to %s (rfq=%s supplier=%s message_log=%s message_id=%s)",
            phone_number, rfq_id, supplier_id, message_log_id, message_id,
        )
        return True
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
        clean_msg = f"OpenClaw transport/timeout error: {type(net_err).__name__} - {str(net_err)}"
        if message_log_id:
            db.mark_message_unknown(message_log_id, clean_msg)
        logger.error("[OpenClaw Outbound] Ambiguous send state to %s: %s", phone_number, net_err)
        return False
    except Exception as send_err:
        clean_msg = f"OpenClaw send failed: {str(send_err)}"
        if message_log_id:
            db.mark_message_failed(message_log_id, clean_msg)
        logger.exception("[OpenClaw Outbound] Failed to send to %s", phone_number)
        return False


def generate_ranking(rfq_id: str) -> dict:
    """Generates comparison ranking for commercial offers (variants) received on an RFQ and saves to DB."""
    quotes = db.get_quotes_for_rfq(rfq_id)
    if not quotes:
        return {"error": "No quotes found for this RFQ"}

    valid_quotes = {str(q["id"]): q for q in quotes}

    offers_lines = []
    for q in quotes:
        supp_name = (
            q.get("suppliers", {}).get("name", "Unknown Supplier")
            if isinstance(q.get("suppliers"), dict)
            else "Unknown Supplier"
        )
        v_label = f", Variant: '{q['variant_label']}'" if q.get("variant_label") else ""
        offers_lines.append(
            f"- Quote ID: {q['id']} | Supplier: {supp_name}{v_label} | Price: AED {q['price']} | "
            f"Delivery: {q.get('delivery_time', '-')} | Notes: {q.get('quality_notes', '-')}"
        )
    quotes_summary = "\n".join(offers_lines)

    result = openclaw_bridge.rank_quotes(rfq_details=f"RFQ ID: {rfq_id}", quotes_summary=quotes_summary)

    # 1. Deterministic Winner Validation
    best_quote_id = result.get("best_quote_id")
    if not best_quote_id or str(best_quote_id) not in valid_quotes:
        # Check if ranking list has a valid quote_id in item 0
        ranking_list = result.get("ranking") or []
        if ranking_list and isinstance(ranking_list, list):
            first_item = ranking_list[0] if isinstance(ranking_list[0], dict) else {}
            first_item_qid = first_item.get("quote_id")
            if first_item_qid and str(first_item_qid) in valid_quotes:
                best_quote_id = str(first_item_qid)
            elif first_item.get("supplier_id"):
                s_id = str(first_item.get("supplier_id"))
                matching_quotes = [q for q in quotes if str(q.get("supplier_id")) == s_id]
                if matching_quotes:
                    best_quote_id = str(matching_quotes[0]["id"])

    if not best_quote_id or str(best_quote_id) not in valid_quotes:
        # Check if best_supplier_id was returned and matches a quote
        legacy_supp_id = result.get("best_supplier_id") or (result.get("best_quote_id") if result.get("best_quote_id") else None)
        if legacy_supp_id:
            matching_quotes = [q for q in quotes if str(q.get("supplier_id")) == str(legacy_supp_id)]
            if matching_quotes:
                best_quote_id = str(matching_quotes[0]["id"])

    # Strict check: Do NOT arbitrarily pick an unrelated quote row if no valid match exists
    if not best_quote_id or str(best_quote_id) not in valid_quotes:
        raise ValueError(
            f"Invalid ranking result: best_quote_id '{best_quote_id}' does not match any valid candidate quote for RFQ {rfq_id}"
        )

    best_quote_id = str(best_quote_id)
    winning_quote = valid_quotes[best_quote_id]
    best_supplier_id = winning_quote.get("supplier_id")

    # 2. Enrich and validate ranking list deterministically
    raw_ranking_items = result.get("ranking") or []
    enriched_ranking = []
    seen_qids = set()

    for item in raw_ranking_items:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("quote_id")) if item.get("quote_id") else None
        if not qid and item.get("supplier_id"):
            s_id = str(item.get("supplier_id"))
            matching_quotes = [q for q in quotes if str(q.get("supplier_id")) == s_id and str(q["id"]) not in seen_qids]
            if matching_quotes:
                qid = str(matching_quotes[0]["id"])

        if qid and qid in valid_quotes and qid not in seen_qids:
            seen_qids.add(qid)
            q_row = valid_quotes[qid]
            supp_name = (
                q_row.get("suppliers", {}).get("name", "Unknown Supplier")
                if isinstance(q_row.get("suppliers"), dict)
                else "Unknown Supplier"
            )
            enriched_ranking.append({
                "rank": int(item.get("rank", len(enriched_ranking) + 1)),
                "quote_id": qid,
                "supplier_id": q_row.get("supplier_id"),
                "supplier_name": supp_name,
                "variant_label": q_row.get("variant_label"),
                "price": q_row.get("price"),
                "delivery_time": q_row.get("delivery_time"),
                "quality_notes": q_row.get("quality_notes"),
                "summary": item.get("summary", ""),
            })

    # Append any valid candidate quotes omitted by LLM
    for qid, q_row in valid_quotes.items():
        if qid not in seen_qids:
            seen_qids.add(qid)
            supp_name = (
                q_row.get("suppliers", {}).get("name", "Unknown Supplier")
                if isinstance(q_row.get("suppliers"), dict)
                else "Unknown Supplier"
            )
            enriched_ranking.append({
                "rank": len(enriched_ranking) + 1,
                "quote_id": qid,
                "supplier_id": q_row.get("supplier_id"),
                "supplier_name": supp_name,
                "variant_label": q_row.get("variant_label"),
                "price": q_row.get("price"),
                "delivery_time": q_row.get("delivery_time"),
                "quality_notes": q_row.get("quality_notes"),
                "summary": f"AED {q_row.get('price')}",
            })

    enriched_ranking.sort(key=lambda x: x["rank"])

    enriched_ranking_json = {
        "best_quote_id": best_quote_id,
        "best_supplier_id": best_supplier_id,
        "reasoning": result.get("reasoning", ""),
        "ranking": enriched_ranking,
    }

    db.save_ranking(
        rfq_id=rfq_id,
        best_supplier_id=best_supplier_id,
        reasoning=result.get("reasoning", ""),
        ranking_json=enriched_ranking_json,
        best_quote_id=best_quote_id,
    )
    return enriched_ranking_json


def check_and_auto_rank(rfq_id: str):
    """Generates ranking for an RFQ if quotes exist and ranking has not been created yet."""
    quotes = db.get_quotes_for_rfq(rfq_id)
    if quotes and not db.ranking_exists(rfq_id):
        print(f"Triggering quote ranking for RFQ: {rfq_id}")
        generate_ranking(rfq_id)


async def finalize_rfq_job(rfq: dict):
    """
    Executes idempotent finalization steps for an RFQ (after atomic claim or during recovery):
    1. Generates and persists AI quote ranking if quotes exist and ranking is not yet saved.
    2. Idempotently creates and enqueues closure notifications for participating suppliers using event_key.
    3. Marks RFQ finalization as 'completed' with finalized_at timestamp.
    """
    rfq_id = rfq.get("id")
    if not rfq_id:
        return
    prod = rfq.get("product_name") or "RFQ Item"
    client_id = rfq.get("client_id")
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Idempotent Ranking
    try:
        quotes = db.get_quotes_for_rfq(rfq_id)
        if quotes:
            if not db.ranking_exists(rfq_id):
                print(f"[{now_iso}] [Finalization] Generating ranking for RFQ '{prod}' (id: {rfq_id})...")
                generate_ranking(rfq_id)
            else:
                print(f"[{now_iso}] [Finalization] Ranking already exists for RFQ {rfq_id}, skipping duplicate generation.")
    except Exception as rank_err:
        tb = traceback.format_exc()
        print(f"[{now_iso}] [Finalization ERROR] Ranking generation failed for RFQ {rfq_id}: {rank_err}\n{tb}")
        db.log_webhook_error(str(rank_err), tb, {"job": "finalize_rfq_job", "stage": "ranking", "rfq_id": rfq_id})
        # Leave in 'processing' state so recovery can retry later
        return

    # 2. Idempotent Closure Notifications
    try:
        rfq_suppliers = rfq.get("rfq_suppliers") or []
        if not rfq_suppliers:
            rfq_suppliers = db.get_rfq_suppliers_for_rfq(rfq_id)

        description = prod
        if rfq.get("specs"):
            description += f" ({rfq['specs']})"
        if rfq.get("quantity") is not None:
            description += f", {rfq['quantity']} units"
        msg = f"The quotation window for '{description}' has ended. We are no longer requesting quotes for this item. Thank you!"
        all_notifications_confirmed = True

        for item in rfq_suppliers:
            supplier = item.get("suppliers") or {}
            supplier_id = supplier.get("id") or item.get("supplier_id")
            phone = supplier.get("phone_number")
            # Expiring a request for quotations must not imply cancellation of
            # an already accepted agreement. Re-read per supplier on recovery.
            if supplier_id and (item.get("status") == "accepted" or db.supplier_has_accepted_quote(rfq_id, supplier_id)):
                logger.info("[Finalization] Suppressed deadline notice for accepted supplier (rfq=%s supplier=%s)", rfq_id, supplier_id)
                continue
            if not phone and supplier_id:
                sup_row = db.get_supplier_by_id(supplier_id)
                if sup_row:
                    phone = sup_row.get("phone_number")
                    if not client_id:
                        client_id = sup_row.get("client_id")

            if phone and supplier_id:
                c_id = client_id or supplier.get("client_id")
                event_key = f"rfq_closed:{rfq_id}:{supplier_id}"
                existing_msg = db.get_message_by_event_key(event_key)
                if not existing_msg:
                    msg_log_id = db.log_message(c_id, supplier_id, "outbound", msg, related_rfq_id=rfq_id, event_key=event_key)
                    if msg_log_id:
                        await enqueue_message(phone, msg, rfq_id=rfq_id, supplier_id=supplier_id, message_log_id=msg_log_id)
                    else:
                        print(f"[{now_iso}] [Finalization ERROR] Failed to establish durable closure message for supplier {supplier_id} (event_key: {event_key}).")
                        all_notifications_confirmed = False
                else:
                    print(f"[{now_iso}] [Finalization] Closure notification already logged for supplier {supplier_id} (event_key: {event_key}), skipping.")

        if not all_notifications_confirmed:
            print(f"[{now_iso}] [Finalization] Incomplete notification persistence for RFQ {rfq_id}. Leaving in 'processing' state.")
            return

    except Exception as notif_err:
        tb = traceback.format_exc()
        print(f"[{now_iso}] [Finalization ERROR] Notification creation failed for RFQ {rfq_id}: {notif_err}\n{tb}")
        db.log_webhook_error(str(notif_err), tb, {"job": "finalize_rfq_job", "stage": "notifications", "rfq_id": rfq_id})
        return

    # 3. Mark Finalization Completed & Expire Active Negotiation Sessions
    try:
        db.expire_negotiation_sessions_for_rfq(rfq_id)
        marked = db.mark_rfq_finalization_completed(rfq_id)
        if marked:
            print(f"[{now_iso}] [Finalization] RFQ '{prod}' ({rfq_id}) marked finalization_status='completed' and active negotiation sessions expired.")
    except Exception as comp_err:
        tb = traceback.format_exc()
        print(f"[{now_iso}] [Finalization ERROR] Failed marking finalization completed for RFQ {rfq_id}: {comp_err}\n{tb}")
        db.log_webhook_error(str(comp_err), tb, {"job": "finalize_rfq_job", "stage": "mark_completed", "rfq_id": rfq_id})


async def check_deadlines_and_reminders():
    """Background cron job that checks active RFQs and handles deadline expiry & reminders."""
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] [Scheduler] Running check_deadlines_and_reminders...")

    # =========================================================================
    # Section A: Acquire Newly Expired Active RFQs
    # =========================================================================
    try:
        expired_rfqs = db.get_active_rfqs_past_deadline()
        print(f"[{now.isoformat()}] [Scheduler] Found {len(expired_rfqs)} active RFQ(s) past deadline.")
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[{now.isoformat()}] [Scheduler ERROR] Failed to fetch expired RFQs: {e}\n{tb}")
        db.log_webhook_error(str(e), tb, {"job": "check_deadlines_and_reminders", "stage": "fetch_expired_rfqs"})
        expired_rfqs = []

    for rfq in expired_rfqs:
        rfq_id = rfq.get("id")
        prod = rfq.get("product_name") or "RFQ Item"
        if db.is_client_automation_paused(rfq.get("client_id")):
            print(f"[{now.isoformat()}] [Scheduler] Skipping finalization for RFQ '{prod}' ({rfq_id}): client automation paused.")
            continue
        if db.has_inflight_negotiation_for_rfq(rfq_id):
            print(
                f"[{now.isoformat()}] [Scheduler] Deferring finalization for RFQ '{prod}' ({rfq_id}): "
                "at least one supplier negotiation is still in flight."
            )
            continue
        try:
            claimed = db.claim_rfq_for_finalization(rfq_id)
            if claimed:
                print(f"[{now.isoformat()}] [Scheduler] Successfully claimed finalization authority for RFQ '{prod}' (id: {rfq_id}).")
                await finalize_rfq_job(rfq)
            else:
                print(f"[{now.isoformat()}] [Scheduler] Skipped RFQ '{prod}' (id: {rfq_id}): authority not acquired (already claimed or inactive).")
        except Exception as rfq_err:
            tb = traceback.format_exc()
            print(f"[{now.isoformat()}] [Scheduler ERROR] Failed processing expired RFQ {rfq_id}: {rfq_err}\n{tb}")
            db.log_webhook_error(str(rfq_err), tb, {"job": "check_deadlines_and_reminders", "rfq_id": rfq_id})

    # =========================================================================
    # Section B: Recover Incomplete Finalizations (status='closed' & finalization_status='processing')
    # =========================================================================
    try:
        processing_rfqs = db.get_rfqs_pending_finalization_recovery()
        if processing_rfqs:
            print(f"[{now.isoformat()}] [Scheduler] Found {len(processing_rfqs)} incomplete RFQ(s) in 'processing' state. Resuming recovery...")
        for prfq in processing_rfqs:
            prfq_id = prfq.get("id")
            if db.is_client_automation_paused(prfq.get("client_id")):
                print(f"[{now.isoformat()}] [Scheduler] Skipping finalization recovery for RFQ {prfq_id}: client automation paused.")
                continue
            try:
                await finalize_rfq_job(prfq)
            except Exception as rec_err:
                tb = traceback.format_exc()
                print(f"[{now.isoformat()}] [Scheduler ERROR] Recovery failed for RFQ {prfq_id}: {rec_err}\n{tb}")
                db.log_webhook_error(str(rec_err), tb, {"job": "check_deadlines_and_reminders", "stage": "recovery", "rfq_id": prfq_id})
    except Exception as proc_err:
        tb = traceback.format_exc()
        print(f"[{now.isoformat()}] [Scheduler ERROR] Failed fetching processing RFQs for recovery: {proc_err}\n{tb}")
        db.log_webhook_error(str(proc_err), tb, {"job": "check_deadlines_and_reminders", "stage": "fetch_processing_rfqs"})

    # =========================================================================
    # Section C: Grouped supplier reminders
    # =========================================================================
    # Collect every due, unanswered item first; consolidate by supplier, then
    # chunk each outbound reminder into <=8 numbered lines.
    try:
        active_items = db.get_active_rfq_suppliers_with_deadlines()
    except Exception as exc:
        db.log_webhook_error(str(exc), traceback.format_exc(), {"job": "grouped_reminders", "stage": "fetch"})
        return

    grouped_reminders = {}
    for item in active_items:
        try:
            rfq = item.get("rfqs") or {}
            supplier = item.get("suppliers") or {}
            client_id = rfq.get("client_id")
            supplier_id = supplier.get("id")
            if not client_id or not supplier_id or db.is_client_automation_paused(client_id) or not db.is_rfq_open(rfq):
                continue
            if not supplier.get("phone_number"):
                continue
            # A negotiation or quote in progress must never be nudged as though
            # the supplier has ignored the RFQ.
            if db.get_active_negotiation_session(client_id, rfq.get("id"), supplier_id):
                continue
            delivered = db.get_initial_rfq_delivery_status(rfq.get("id"), supplier_id)
            if not delivered or delivered.get("status") != "sent" or not delivered.get("sent_at"):
                continue
            sent_at = datetime.fromisoformat(delivered["sent_at"].replace("Z", "+00:00"))
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            percentage = (now - sent_at).total_seconds() * 100 / (max(1, rfq.get("deadline_hours") or 24) * 3600)
            count = item.get("reminder_count") or 0
            stage = "90%" if count == 2 and 90 <= percentage < 100 else (
                "70%" if count == 1 and 70 <= percentage < 100 else (
                    "50%" if count == 0 and 50 <= percentage < 100 else None
                )
            )
            if stage is None:
                continue
            grouped_reminders.setdefault((client_id, supplier_id), {"supplier": supplier, "items": []})["items"].append({
                "link": item, "rfq": rfq, "stage": stage,
                "plan": BundleItem(rfq_id=str(rfq["id"]), product_name=rfq.get("product_name") or "RFQ Item", quantity=rfq.get("quantity")),
            })
        except Exception as item_err:
            db.log_webhook_error(str(item_err), traceback.format_exc(), {"job": "grouped_reminders", "item_id": item.get("id")})

    for (client_id, supplier_id), group in grouped_reminders.items():
        items = group["items"]
        index = {entry["plan"].rfq_id: entry for entry in items}
        for batch in plan_supplier_bundles([entry["plan"] for entry in items]):
            entries = [index[item.rfq_id] for item in batch]
            # Stable event key makes a previously logged attempt fail closed:
            # no unacknowledged WhatsApp send is automatically repeated.
            stage_key = ",".join(sorted(str(e["link"]["id"]) + ":" + str(e["link"].get("reminder_count") or 0) for e in entries))
            import hashlib
            event_key = "reminder_bundle:" + hashlib.sha256((str(client_id) + str(supplier_id) + stage_key).encode()).hexdigest()
            try:
                if db.is_client_automation_paused(client_id):
                    continue
                if db.get_message_by_event_key(event_key):
                    continue
                products = [f"{i}. {item.product_name}" for i, item in enumerate(batch, 1)]
                msg = "Hi! A quick follow-up from Everyware®️ on the outstanding quotes below:\n" + "\n".join(products)
                msg += "\n\nPlease send your AED price per unit and estimated delivery for each item number. Thanks."
                bundle = db.supabase.table("supplier_message_bundles").insert({
                    "client_id": client_id, "supplier_id": supplier_id, "kind": "reminder",
                    "stage": "mixed", "status": "pending", "message": msg,
                }).execute()
                if not bundle.data:
                    raise RuntimeError("Could not persist reminder bundle")
                bundle_id = bundle.data[0]["id"]
                link_rows = [{
                    "bundle_id": bundle_id, "client_id": client_id, "supplier_id": supplier_id,
                    "item_position": position,
                    "rfq_id": e["rfq"]["id"], "rfq_supplier_id": e["link"]["id"],
                    "kind": "reminder", "stage": e["stage"], "status": "pending",
                } for position, e in enumerate(entries, 1)]
                persisted = db.supabase.table("supplier_message_bundle_items").insert(link_rows).execute()
                if len(persisted.data or []) != len(link_rows):
                    raise RuntimeError("Incomplete reminder-bundle item links")
                message_id = db.log_message(client_id, supplier_id, "outbound", msg, related_rfq_id=batch[0].rfq_id, event_key=event_key)
                if not message_id:
                    raise RuntimeError("Could not persist idempotent reminder message")
                db.supabase.table("supplier_message_bundles").update({"status": "processing"}).eq("id", bundle_id).execute()
                if not await enqueue_message(group["supplier"]["phone_number"], msg, supplier_id=supplier_id, message_log_id=message_id):
                    db.supabase.table("supplier_message_bundles").update({
                        "status": "failed", "error_message": "Outbound not confirmed, no automatic retry"
                    }).eq("id", bundle_id).execute()
                    continue
                logged = db.supabase.table("message_log").select("sent_at,external_message_id").eq("id", message_id).limit(1).execute()
                delivery = logged.data[0] if logged.data else {}
                if not delivery.get("sent_at"):
                    raise RuntimeError("Delivered reminder missing durable sent_at")
                db.supabase.table("supplier_message_bundles").update({
                    "status": "sent", "sent_at": delivery["sent_at"],
                    "external_message_id": delivery.get("external_message_id")
                }).eq("id", bundle_id).execute()
                db.supabase.table("supplier_message_bundle_items").update({"status": "sent"}).eq("bundle_id", bundle_id).execute()
                for e in entries:
                    db.update_rfq_supplier_reminder(e["link"]["id"], (e["link"].get("reminder_count") or 0) + 1)
            except Exception as reminder_err:
                logger.exception("Grouped reminder failed for supplier %s: %s", supplier_id, reminder_err)
                db.log_webhook_error(str(reminder_err), traceback.format_exc(), {"job": "grouped_reminders", "supplier_id": supplier_id})


REQUIRED_ENV_VARS = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "OPENCLAW_BASE_URL",
    "OPENCLAW_TOOL_API_KEY",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("PURGE_LEGACY_PROCUREMENT_DATA", "").lower() == "true":
        print(f"[{datetime.now(timezone.utc).isoformat()}] [Cleanup] Purging legacy procurement history...")
        await run_in_threadpool(db.purge_procurement_history)
        print(f"[{datetime.now(timezone.utc).isoformat()}] [Cleanup] Legacy procurement history purged.")

    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Backend configuration error: Missing required environment variable(s): {', '.join(missing)}.")

    try:
        sync_result = await run_in_threadpool(sync_existing_suppliers_to_openclaw, DEMO_CLIENT_ID)
        print(f"[{datetime.now(timezone.utc).isoformat()}] [OpenClaw Sync] Existing suppliers synchronized: {sync_result}")
    except Exception as sync_err:
        print(f"[{datetime.now(timezone.utc).isoformat()}] [OpenClaw Sync ERROR] Existing supplier backfill failed: {sync_err}")

    try:
        queued_msgs = db.get_queued_outbound_messages()
        recovered_count = 0
        for qm in queued_msgs:
            msg_id = qm.get("id")
            body = qm.get("body")
            rfq_id = qm.get("related_rfq_id")
            supplier_id = qm.get("supplier_id")

            recovery = db.evaluate_outbound_recovery(qm)
            if not recovery.get("retry"):
                reason = recovery.get("reason") or "stale_or_irrelevant"
                db.suppress_stale_outbound_message(msg_id, reason)
                print(
                    f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] "
                    f"Suppressed stale queued outbound message {msg_id}: {reason}"
                )
                continue

            phone = None
            if qm.get("suppliers") and isinstance(qm.get("suppliers"), dict):
                phone = qm.get("suppliers").get("phone_number")
            elif supplier_id:
                supplier = db.get_supplier_by_id(supplier_id)
                if supplier:
                    phone = supplier.get("phone_number")

            if not phone or not body:
                db.suppress_stale_outbound_message(msg_id, "missing_phone_or_body")
                continue

            sent = await enqueue_message(
                phone,
                body,
                rfq_id=rfq_id,
                supplier_id=supplier_id,
                message_log_id=msg_id,
            )
            if sent:
                recovered_count += 1
        if recovered_count:
            print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] Recovered {recovered_count} queued OpenClaw outbound message(s).")
    except Exception as rec_err:
        print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan ERROR] Failed queued-message recovery: {rec_err}")

    print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] Starting RFQ deadline/reminder scheduler; WhatsApp delivery is OpenClaw-only.")
    scheduler.add_job(
        flush_pending_initial_rfq_bundles,
        "interval",
        seconds=30,
        max_instances=1,
        coalesce=True,
        id="flush_initial_rfq_bundles",
        replace_existing=True,
    )
    scheduler.add_job(
        check_deadlines_and_reminders,
        "interval",
        minutes=15,
        next_run_time=datetime.now(timezone.utc),
        id="check_deadlines_and_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        operator_jobs.sync_jobs, "interval", seconds=10,
        args=[OPENCLAW_BASE_URL, OPENCLAW_TOOL_API_KEY],
        max_instances=1, coalesce=True, id="operator_instructions", replace_existing=True,
    )
    scheduler.start()
    yield
    print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] Shutting down RFQ scheduler...")
    scheduler.shutdown()

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(lifespan=lifespan)
app.include_router(openclaw_router)


def _cors_origins() -> list[str]:
    origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]
    extra = ",".join(
        part
        for part in (
            os.getenv("CORS_ORIGINS", ""),
            os.getenv("FRONTEND_URL", ""),
        )
        if part
    )
    for raw in extra.split(","):
        origin = raw.strip().rstrip("/")
        if origin and origin not in origins:
            origins.append(origin)
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    # Deployed Railway frontends until CORS_ORIGINS / FRONTEND_URL is set.
    allow_origin_regex=r"https://.*\.up\.railway\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/auth/reset-password")
async def reset_password(payload: PasswordResetRequest, request: Request):
    """Update the authenticated user's password through Supabase Auth.

    This is a server-side fallback for browsers that fail to send the direct
    cross-origin PUT used by supabase-js. The caller's own access token is
    verified and forwarded to Supabase; the service-role key is not used.
    """
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = auth_header.split(None, 1)[1].strip()
    claims = verify_jwt(token)
    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="Token missing sub claim")

    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    public_key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("VITE_SUPABASE_ANON_KEY")
    if not supabase_url or not public_key:
        raise HTTPException(status_code=500, detail="Supabase Auth is not configured")

    def _update_password():
        return requests.put(
            f"{supabase_url}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": public_key,
                "Content-Type": "application/json",
            },
            json={"password": payload.password},
            timeout=15,
        )

    try:
        response = await run_in_threadpool(_update_password)
    except requests.RequestException as exc:
        logger.exception("Supabase password update request failed")
        raise HTTPException(status_code=502, detail="Could not reach authentication service") from exc

    if response.status_code >= 400:
        try:
            error_body = response.json()
        except ValueError:
            error_body = {}
        detail = (
            error_body.get("msg")
            or error_body.get("message")
            or error_body.get("error_description")
            or error_body.get("error")
            or "Password update failed"
        )
        raise HTTPException(status_code=response.status_code, detail=detail)

    return {"status": "password_updated"}


def stage_initial_rfq_for_bundling(client_id: str, supplier_id: str, rfq_id: str) -> None:
    """Queue a new RFQ for a 30-second supplier-level bundling window."""
    link = (
        db.supabase.table("rfq_suppliers").select("id")
        .eq("rfq_id", rfq_id).eq("supplier_id", supplier_id).limit(1).execute()
    )
    if not link.data:
        raise RuntimeError("Cannot queue RFQ: missing supplier relationship.")
    due = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    created = db.supabase.table("supplier_message_bundle_items").insert({
        "client_id": client_id,
        "supplier_id": supplier_id,
        "rfq_id": rfq_id,
        "rfq_supplier_id": link.data[0]["id"],
        "kind": "initial_rfq",
        "status": "pending",
        "available_at": due,
    }).execute()
    if not created.data:
        raise RuntimeError("Could not durably queue RFQ for supplier bundling.")


async def flush_pending_initial_rfq_bundles():
    """Claim due RFQ invitations with a DB row lock and send once per bundle.

    Sending after an uncertain network failure is NEVER retried automatically:
    the durable log and bundle remain marked failed/unknown for manual review.
    """
    cutoff = datetime.now(timezone.utc).isoformat()
    try:
        due = (
            db.supabase.table("supplier_message_bundle_items")
            .select("client_id,supplier_id")
            .eq("kind", "initial_rfq")
            .eq("status", "pending")
            .is_("bundle_id", "null")
            .lte("available_at", cutoff)
            .limit(500)
            .execute()
        )
    except Exception:
        logger.exception("Could not fetch pending initial RFQ bundle queue.")
        return

    pairs = {(row["client_id"], row["supplier_id"]) for row in (due.data or [])}
    for client_id, supplier_id in pairs:
        if db.is_client_automation_paused(client_id):
            continue
        while True:
            bundle_id = None
            try:
                claimed = db.supabase.rpc("claim_initial_supplier_bundle", {
                    "p_client_id": client_id,
                    "p_supplier_id": supplier_id,
                    "p_limit": 8,
                }).execute()
                batch = claimed.data if claimed else None
                if not batch or not batch.get("items"):
                    break
                bundle_id = batch["bundle_id"]
                items = batch["items"]
                supplier = db.get_supplier_by_id(supplier_id, client_id=client_id)
                if not supplier or not supplier.get("phone_number") or db.is_client_automation_paused(client_id):
                    raise RuntimeError("Supplier not reachable or client paused before dispatch")
                plans = [BundleItem(
                    rfq_id=str(item["rfq_id"]),
                    product_name=item["product_name"],
                    quantity=item.get("quantity"),
                    specs=item.get("specs"),
                    deadline_hours=item.get("deadline_hours"),
                    required_delivery_days=item.get("required_delivery_days"),
                ) for item in items]
                msg = format_supplier_bundle(plans)
                db.supabase.table("supplier_message_bundles").update({
                    "message": msg,
                }).eq("id", bundle_id).execute()
                log_id = db.log_message(client_id, supplier_id, "outbound", msg, related_rfq_id=plans[0].rfq_id)
                if not log_id:
                    raise RuntimeError("Failed to durably log grouped RFQ invitation")
                did_send = await enqueue_message(
                    supplier["phone_number"], msg, supplier_id=supplier_id, message_log_id=log_id,
                )
                if not did_send:
                    raise RuntimeError("Outbound not confirmed; do not automatically retry")
                log = db.supabase.table("message_log").select("sent_at,external_message_id").eq("id", log_id).limit(1).execute()
                sent_log = log.data[0] if log.data else {}
                if not sent_log.get("sent_at"):
                    raise RuntimeError("Sent invitation missing durable timestamp")
                db.supabase.table("supplier_message_bundles").update({
                    "status": "sent",
                    "sent_at": sent_log["sent_at"],
                    "external_message_id": sent_log.get("external_message_id"),
                }).eq("id", bundle_id).execute()
                db.supabase.table("supplier_message_bundle_items").update({
                    "status": "sent",
                }).eq("bundle_id", bundle_id).execute()
                if sent_log.get("external_message_id"):
                    for item in plans:
                        db.update_rfq_supplier_sent_message_id(item.rfq_id, supplier_id, sent_log["external_message_id"])
                await asyncio.sleep(random.uniform(OUTBOUND_MIN_DELAY, OUTBOUND_MAX_DELAY))
            except Exception as exc:
                logger.exception("Initial RFQ supplier bundle dispatch failed for %s: %s", supplier_id, exc)
                if bundle_id:
                    try:
                        db.supabase.table("supplier_message_bundles").update({
                            "status": "failed",
                            "error_message": str(exc)[:300],
                        }).eq("id", bundle_id).execute()
                        db.supabase.table("supplier_message_bundle_items").update({
                            "status": "failed",
                        }).eq("bundle_id", bundle_id).execute()
                    except Exception:
                        logger.exception("Could not flag ambiguous supplier bundle delivery")
                break


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

    for supplier in matched_suppliers:
        if db.is_client_automation_paused(client_id):
            continue
        if supplier.get("phone_number"):
            stage_initial_rfq_for_bundling(client_id, supplier["id"], rfq["id"])

    return {
        "status": "success",
        "delivery_status": "queued_for_bundling" if not db.is_client_automation_paused(client_id) else "automation_paused",
        "rfq_id": rfq["id"],
        "categories": req.categories,
        "targeting_mode": req.targeting_mode,
        "matched_suppliers_count": len(matched_suppliers),
        "suppliers": [{"id": s["id"], "name": s["name"], "phone": s["phone_number"]} for s in matched_suppliers],
    }


async def send_bulk_rfq_bundles(client_id: str, supplier_items: dict) -> dict:
    """Persist grouped bulk invitations before sending through OpenClaw.

    Every RFQ is linked to a durable bundle item. A bundle is marked sent only
    after the single WhatsApp send succeeds; failed sends never become delivered.
    """
    sent_count = 0
    failures = []
    for supplier_id, details in supplier_items.items():
        if db.is_client_automation_paused(client_id):
            failures.append({"supplier_id": supplier_id, "reason": "client_automation_paused"})
            continue
        supplier = details["supplier"]
        phone = supplier.get("phone_number")
        if not phone:
            failures.append({"supplier_id": supplier_id, "reason": "missing_phone"})
            continue
        entries = details["items"]
        for batch in plan_supplier_bundles([entry["item"] for entry in entries], max_items=8):
            selected = {item.rfq_id for item in batch}
            grouped_entries = [entry for entry in entries if entry["item"].rfq_id in selected]
            message = format_supplier_bundle(batch)
            # A permanently recorded bundle must precede the network send.
            created = db.supabase.table("supplier_message_bundles").insert({
                "client_id": client_id,
                "supplier_id": supplier_id,
                "kind": "initial_rfq",
                "status": "pending",
                "message": message,
            }).execute()
            if not created.data:
                raise RuntimeError("Could not persist supplier message bundle; refusing WhatsApp send.")
            bundle_id = created.data[0]["id"]
            try:
                linked_items = []
                for item_index, entry in enumerate(grouped_entries, 1):
                    item = entry["item"]
                    link = db.supabase.table("rfq_suppliers").select("id").eq("rfq_id", item.rfq_id).eq("supplier_id", supplier_id).limit(1).execute()
                    if not link.data:
                        raise RuntimeError("RFQ/supplier link missing; refusing WhatsApp send.")
                    linked_items.append({
                        "bundle_id": bundle_id,
                        "client_id": client_id,
                        "supplier_id": supplier_id,
                        "rfq_id": item.rfq_id,
                        "rfq_supplier_id": link.data[0]["id"],
                        "item_position": item_index,
                        "kind": "initial_rfq",
                        "status": "pending",
                    })
                inserted = db.supabase.table("supplier_message_bundle_items").insert(linked_items).execute()
                if len(inserted.data or []) != len(linked_items):
                    raise RuntimeError("Incomplete bundle item persistence; refusing WhatsApp send.")
                log_id = db.log_message(client_id, supplier_id, "outbound", message, related_rfq_id=batch[0].rfq_id)
                if not log_id:
                    raise RuntimeError("Failed to record outbound bundle message.")
                db.supabase.table("supplier_message_bundles").update({"status": "processing"}).eq("id", bundle_id).execute()
                sent = await enqueue_message(phone, message, supplier_id=supplier_id, message_log_id=log_id)
                if not sent:
                    # Delivery may be UNKNOWN after transport timeout; never retry automatically.
                    db.supabase.table("supplier_message_bundles").update({"status": "failed", "error_message": "Outbound not confirmed; check message log before retry."}).eq("id", bundle_id).execute()
                    failures.append({"supplier_id": supplier_id, "bundle_id": bundle_id, "reason": "send_not_confirmed"})
                    continue
                delivery = db.supabase.table("message_log").select("external_message_id,sent_at").eq("id", log_id).limit(1).execute()
                outbound = delivery.data[0] if delivery.data else {}
                if not outbound.get("sent_at"):
                    raise RuntimeError("Sent message lacks durable delivery timestamp.")
                db.supabase.table("supplier_message_bundles").update({
                    "status": "sent",
                    "external_message_id": outbound.get("external_message_id"),
                    "sent_at": outbound["sent_at"],
                }).eq("id", bundle_id).execute()
                db.supabase.table("supplier_message_bundle_items").update({"status": "sent"}).eq("bundle_id", bundle_id).execute()
                if outbound.get("external_message_id"):
                    for item in batch:
                        db.update_rfq_supplier_sent_message_id(item.rfq_id, supplier_id, outbound["external_message_id"])
                sent_count += 1
            except Exception as exc:
                logger.exception("Supplier bundle %s failed safely", bundle_id)
                db.supabase.table("supplier_message_bundles").update({
                    "status": "failed",
                    "error_message": str(exc)[:300],
                }).eq("id", bundle_id).execute()
                failures.append({"supplier_id": supplier_id, "bundle_id": bundle_id, "reason": "processing_error"})
    return {"sent_bundles": sent_count, "failed_bundles": failures}


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
    suppliers_for_bulk_bundles = {}
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
            for supplier in matched_suppliers:
                if supplier.get("phone_number"):
                    group = suppliers_for_bulk_bundles.setdefault(supplier["id"], {"supplier": supplier, "items": []})
                    group["items"].append({"item": BundleItem(
                        rfq_id=str(rfq["id"]),
                        product_name=final_product_name,
                        quantity=final_quantity,
                        specs=final_specs,
                        deadline_hours=final_deadline,
                    )})

        created_rfqs.append({
            "rfq_id": rfq["id"],
            "product_name": final_product_name,
            "category": final_category,
            "matched_suppliers_count": len(matched_suppliers),
            "matched_suppliers": [{"id": s["id"], "name": s["name"], "phone_number": s.get("phone_number")} for s in matched_suppliers],
        })
        matched_summary.append({"rfq_id": rfq["id"], "matched_suppliers_count": len(matched_suppliers)})

    bundle_result = await send_bulk_rfq_bundles(tenant_client_id, suppliers_for_bulk_bundles)

    return {
        "status": "success" if not bundle_result["failed_bundles"] else "partial_delivery",
        "bundle_delivery": bundle_result,
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

    existing = db.get_flag_by_id(flag_id, client_id=client_id)
    if existing and existing.get("status") == "processing":
        job = (existing.get("metadata") or {}).get("operator_instruction") or {}
        if job and job.get("state") not in {"unknown", "failed"}:
            raise HTTPException(status_code=409, detail="Instruction is still processing; check its status before resolving.")
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
    """Apply trusted human guidance to the same supplier OpenClaw WhatsApp session."""
    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=401, detail="Authentication missing client_id")

    if not payload.send_to_supplier or (payload.response or "").strip().casefold() in {
        "ignore", "ignore this message", "dismiss", "dismiss this", "no action", "no action needed", "do not respond",
    }:
        existing = db.get_flag_by_id(flag_id, client_id=client_id)
        if existing and existing.get("status") == "processing" and (existing.get("metadata") or {}).get("operator_instruction"):
            raise HTTPException(status_code=409, detail="An instruction is already processing; resolving cannot cancel it.")

    # An explicit Ignore/Dismiss is an internal operator decision, not a
    # supplier-facing instruction. Never invoke an LLM or WhatsApp for it.
    normalized_action = (payload.response or "").strip().casefold()
    if normalized_action in {
        "ignore", "ignore this message", "dismiss", "dismiss this",
        "no action", "no action needed", "do not respond",
    }:
        updated = db.resolve_flag_with_response(flag_id, payload.response, client_id=client_id)
        if not updated:
            existing = db.get_flag_by_id(flag_id, client_id=client_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Flagged item not found")
        return {"status": "resolved", "flag_id": flag_id, "sent_to_supplier": False}

    if not payload.send_to_supplier:
        updated = db.resolve_flag_with_response(flag_id, payload.response, client_id=client_id)
        if not updated:
            existing = db.get_flag_by_id(flag_id, client_id=client_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Flagged item not found")
        return {"status": "resolved", "flag_id": flag_id, "sent_to_supplier": False}

    flag = db.get_flag_by_id(flag_id, client_id=client_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Flagged item not found")
    expected_job_id = operator_jobs.job_id(client_id, flag_id, payload.response)
    existing_job = (flag.get("metadata") or {}).get("operator_instruction") or {}
    if existing_job.get("id") == expected_job_id:
        return JSONResponse(status_code=200 if flag.get("status") == "resolved" else 202, content={
            "status": flag.get("status"), "flag_id": flag_id, "instruction_status": existing_job.get("state"),
            "job_id": expected_job_id, "sent_to_supplier": False,
        })
    if flag.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Flag is already {flag.get('status')}")
    supplier = flag.get("suppliers") or db.get_supplier_by_id(flag.get("supplier_id"))
    if not supplier or not supplier.get("phone_number"):
        raise HTTPException(status_code=400, detail="Supplier phone is unavailable for this flag")
    try:
        job = operator_jobs.enqueue(flag, payload.response)
    except Exception:
        raise HTTPException(status_code=503, detail="Instruction was not queued; please retry.")
    if not job:
        # A concurrent submit may have won. Re-read without releasing its claim.
        current = db.get_flag_by_id(flag_id, client_id=client_id) or {}
        current_job = (current.get("metadata") or {}).get("operator_instruction") or {}
        if current_job.get("id") != expected_job_id:
            raise HTTPException(status_code=409, detail="Another instruction is already processing")
        job = current_job
    return JSONResponse(status_code=202, content={
        "status": "processing", "flag_id": flag_id, "instruction_status": job["state"],
        "job_id": job["id"], "sent_to_supplier": False,
    })


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


class AutomationPauseRequest(BaseModel):
    paused: bool
    reason: Optional[str] = None


@app.post("/admin/automation/pause")
async def set_automation_pause_endpoint(
    req: AutomationPauseRequest,
    current_user=Depends(get_current_user),
):
    """Pause or resume all automated procurement handling for the current tenant."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="User has no associated client_id")

    row = db.set_client_automation_pause(client_id, req.paused, req.reason)
    if not row:
        raise HTTPException(status_code=500, detail="Could not update automation pause state")
    return {
        "status": "paused" if req.paused else "active",
        "automation_paused": bool(row.get("automation_paused")),
        "automation_paused_at": row.get("automation_paused_at"),
        "automation_pause_reason": row.get("automation_pause_reason"),
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


@app.post("/admin/suppliers")
async def create_supplier_endpoint(
    req: SupplierMutationRequest,
    current_user=Depends(get_current_user),
):
    """Create a supplier and keep OpenClaw WhatsApp admission synchronized."""
    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="User has no associated client_id")

    created_rows = db.create_supplier(
        client_id,
        req.name.strip(),
        req.phone_number.strip(),
        req.category,
        req.notes,
    )
    supplier = created_rows[0] if created_rows else None
    if not supplier:
        raise HTTPException(status_code=500, detail="Could not create supplier")

    try:
        if req.is_active:
            sync_openclaw_allowlist(req.phone_number, "add")
        if req.is_active is False:
            supplier = db.update_supplier(
                supplier["id"], client_id,
                name=req.name.strip(),
                phone_number=req.phone_number.strip(),
                categories=req.category,
                notes=req.notes,
                is_active=False,
            ) or supplier
    except HTTPException:
        # Roll back the just-created contact so UI and WhatsApp access cannot diverge.
        db.soft_delete_supplier(supplier["id"], client_id)
        raise

    return {"status": "created", "supplier": supplier}


@app.put("/admin/suppliers/{supplier_id}")
async def update_supplier_endpoint(
    supplier_id: str,
    req: SupplierMutationRequest,
    current_user=Depends(get_current_user),
):
    """Update supplier details and atomically reconcile the OpenClaw allowlist."""
    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="User has no associated client_id")

    existing = db.get_supplier_by_id(supplier_id)
    if not existing or existing.get("client_id") != client_id or existing.get("deleted_at"):
        raise HTTPException(status_code=404, detail="Supplier not found")

    old_phone = existing.get("phone_number")
    old_active = existing.get("is_active") is not False
    new_phone = req.phone_number.strip()
    new_active = req.is_active is not False

    # Reconcile WhatsApp access first. If this fails, Supabase stays unchanged.
    if old_active and (not new_active or old_phone != new_phone):
        sync_openclaw_allowlist(old_phone, "remove")
    try:
        if new_active and (not old_active or old_phone != new_phone):
            sync_openclaw_allowlist(new_phone, "add")
    except HTTPException:
        if old_active and (not new_active or old_phone != new_phone):
            try:
                sync_openclaw_allowlist(old_phone, "add")
            except Exception:
                logger.exception("Failed restoring old supplier phone to OpenClaw allowlist")
        raise

    updated = db.update_supplier(
        supplier_id,
        client_id,
        name=req.name.strip(),
        phone_number=new_phone,
        categories=req.category,
        notes=req.notes,
        is_active=new_active,
    )
    if not updated:
        # Best-effort rollback of WhatsApp access if database update unexpectedly fails.
        try:
            if new_active and (not old_active or old_phone != new_phone):
                sync_openclaw_allowlist(new_phone, "remove")
            if old_active:
                sync_openclaw_allowlist(old_phone, "add")
        except Exception:
            logger.exception("Failed rolling back OpenClaw allowlist after supplier DB update failure")
        raise HTTPException(status_code=500, detail="Could not update supplier")

    return {"status": "updated", "supplier": updated}


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

    existing = db.get_supplier_by_id(supplier_id)
    if not existing or existing.get("client_id") != client_id or existing.get("deleted_at"):
        raise HTTPException(status_code=404, detail="Supplier not found or already deleted")

    if existing.get("is_active") is not False and existing.get("phone_number"):
        sync_openclaw_allowlist(existing["phone_number"], "remove")

    deleted = db.soft_delete_supplier(supplier_id, client_id)
    if not deleted:
        try:
            if existing.get("is_active") is not False and existing.get("phone_number"):
                sync_openclaw_allowlist(existing["phone_number"], "add")
        except Exception:
            logger.exception("Failed restoring supplier allowlist after delete rollback")
        raise HTTPException(status_code=500, detail="Could not delete supplier")

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
