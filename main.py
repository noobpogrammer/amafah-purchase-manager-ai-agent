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
from typing import Any, Optional

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
from auth import get_current_user
import groq_client
from groq_client import AgentContext
import guardrails
from policy_validator import ActionProposal, validate_action, ActionCategory, ValidationResult

logger = logging.getLogger(__name__)



scheduler = AsyncIOScheduler()

DEMO_CLIENT_ID = "d88c52ad-3d0b-42e9-86f1-b9f70018856b"
THANK_YOU_MSG = "Thanks for the quote! We'll be in touch if we move forward."
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


class RFQCreateRequest(BaseModel):
    product_name: str
    category: str = Field(..., min_length=1)
    specs: str
    quantity: Optional[int] = None
    last_quote: Optional[float] = None
    acceptable_price_min: Optional[float] = None
    acceptable_price_max: Optional[float] = None
    deadline_hours: int = Field(..., gt=0)

    @field_validator("product_name", "category", "specs")
    @classmethod
    def validate_non_empty(cls, v: str, info: ValidationInfo) -> str:
        if v is None or not str(v).strip():
            raise ValueError(f"'{info.field_name}' cannot be empty or blank")
        return str(v).strip()

    @field_validator("deadline_hours")
    @classmethod
    def validate_deadline_hours(cls, v: int) -> int:
        if v is None or v <= 0:
            raise ValueError("'deadline_hours' must be a positive integer")
        return int(v)

    @field_validator("acceptable_price_min", "acceptable_price_max")
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
    def validate_price_range(self) -> "RFQCreateRequest":
        if self.acceptable_price_min is not None and self.acceptable_price_max is not None:
            if self.acceptable_price_min > self.acceptable_price_max:
                raise ValueError("acceptable_price_min cannot be greater than acceptable_price_max")
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
        last_quote = float(str(last_quote_raw).replace(",", "")) if last_quote_raw not in (None, "") else None
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
    """Pushes an outbound message onto the asyncio queue for paced sending."""
    await outbound_queue.put((phone_number, message, rfq_id, supplier_id, message_log_id))


async def outbound_worker():
    """Background worker that processes outbound WhatsApp messages one by one with randomized delay."""
    while True:
        try:
            item = await outbound_queue.get()
            if len(item) == 5:
                phone_number, message, rfq_id, supplier_id, message_log_id = item
            elif len(item) == 4:
                phone_number, message, rfq_id, supplier_id = item
                message_log_id = None
            else:
                phone_number, message = item[:2]
                rfq_id, supplier_id, message_log_id = None, None, None

            # 1. Atomically claim message (queued -> sending) immediately before send attempt
            if message_log_id:
                try:
                    claimed = db.mark_message_sending(message_log_id)
                except Exception as claim_err:
                    print(f"[Outbound Worker] Claim check error for message {message_log_id}: {claim_err}")
                    claimed = False

                if not claimed:
                    print(f"[Outbound Worker] Skipping message {message_log_id} to {phone_number}: acquisition failed (not in 'queued' state or claim error).")
                    outbound_queue.task_done()
                    continue

            try:
                resp = await run_in_threadpool(send_whatsapp_message, phone_number, message)
                
                # Extract Evolution message ID safely
                msg_id = None
                if resp and isinstance(resp, dict):
                    msg_id = resp.get("key", {}).get("id") or resp.get("id")
                    if msg_id:
                        msg_id = str(msg_id)

                # 2. Transition to 'sent'
                if message_log_id:
                    db.mark_message_sent(message_log_id, evolution_message_id=msg_id)

                # Phase 4 correlation persistence: update rfq_suppliers.sent_message_id
                if msg_id and rfq_id and supplier_id:
                    db.update_rfq_supplier_sent_message_id(rfq_id, supplier_id, msg_id)

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as net_err:
                # Ambiguous transport error: cannot determine if Evolution accepted or dropped it
                clean_msg = f"Transport/timeout error: {type(net_err).__name__} - {str(net_err)}"
                if message_log_id:
                    db.mark_message_unknown(message_log_id, clean_msg)
                print(f"[Outbound Worker] Ambiguous transport error to {phone_number} (marked 'unknown'): {net_err}")
            except requests.exceptions.HTTPError as http_err:
                # Definite API rejection (HTTP 4xx/5xx)
                clean_msg = f"HTTP error {http_err.response.status_code if http_err.response is not None else ''}: {str(http_err)}"
                if message_log_id:
                    db.mark_message_failed(message_log_id, clean_msg)
                print(f"[Outbound Worker] Definite HTTP failure sending to {phone_number} (marked 'failed'): {http_err}")
            except Exception as e:
                # Other failure (e.g. missing config, JSON decode error, or runtime issue)
                clean_msg = f"Send failure: {str(e)}"
                if message_log_id:
                    db.mark_message_failed(message_log_id, clean_msg)
                print(f"[Outbound Worker] Error sending WhatsApp message to {phone_number} (marked 'failed'): {e}")
            finally:
                outbound_queue.task_done()

            # Random jittered delay between outbound messages
            delay = random.uniform(OUTBOUND_MIN_DELAY, OUTBOUND_MAX_DELAY)
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Outbound Worker] Unexpected error in worker loop: {e}")


def send_whatsapp_message(phone_number: str, message: str) -> dict:
    """Send a text message through the configured Evolution API instance."""
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY or not EVOLUTION_INSTANCE:
        raise RuntimeError("Evolution API configuration is missing")

    url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {
        "Content-Type": "application/json",
        "apikey": EVOLUTION_API_KEY,
    }
    payload = {
        "number": phone_number,
        "text": message,
        "delay": EVOLUTION_TYPING_DELAY_MS,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    try:
        return response.json()
    except Exception:
        return {"status": "ok", "raw": response.text}


def normalize_phone(remote_jid: str) -> str:
    """Strips everything from '@' onward and device suffixes to normalize WhatsApp remoteJid to plain digits."""
    if not remote_jid:
        return ""
    user_part = remote_jid.split("@")[0]
    return user_part.split(":")[0]



def format_rfq_context(open_rfqs: list, current_supplier_id: str = None) -> str:
    if not open_rfqs:
        return "No open RFQs for this supplier."
    lines = []
    for entry in open_rfqs:
        rfq = entry.get("rfqs", entry) if isinstance(entry, dict) else entry
        rfq_id = rfq.get("id") if isinstance(rfq, dict) else None
        price_min = rfq.get("acceptable_price_min") if isinstance(rfq, dict) else None
        price_max = rfq.get("acceptable_price_max") if isinstance(rfq, dict) else None

        range_str = "None"
        if price_min is not None and price_max is not None:
            range_str = f"AED {price_min} - {price_max}"
        elif price_min is not None:
            range_str = f"Min AED {price_min}"
        elif price_max is not None:
            range_str = f"Max AED {price_max}"

        comp_str = "None"
        attempts = 0
        if current_supplier_id and rfq_id:
            comp_ctx = db.get_competitive_pricing_context(rfq_id, current_supplier_id)
            if comp_ctx.get("has_competition"):
                comp_str = f"Best competing quote is AED {comp_ctx['best_competing_price']} (from {comp_ctx['competing_quotes_count']} other supplier(s))"
            attempts = db.get_negotiation_attempts(rfq_id, current_supplier_id)

        lines.append(
            f"- RFQ ID: {rfq_id} | Product: {rfq.get('product_name')} | "
            f"Specs: {rfq.get('specs', '-')} | Qty: {rfq.get('quantity', '-')} | "
            f"Acceptable Price Range: {range_str} | "
            f"Competitive Context: {comp_str} | "
            f"Negotiation Attempts Made: {attempts}/3"
        )
    return "\n".join(lines)


def format_prior_quotes_context(prior_quotes: list) -> str:
    if not prior_quotes:
        return "No prior quotes on record for this supplier."
    lines = []
    for q in prior_quotes:
        product = q.get("rfqs", {}).get("product_name", "Unknown Product") if isinstance(q.get("rfqs"), dict) else "Unknown Product"
        lines.append(
            f"- Product: {product} | RFQ ID: {q['rfq_id']} | Price: AED {q['price']} | "
            f"Delivery: {q.get('delivery_time', '-')} | Notes: {q.get('quality_notes', '-')}"
        )
    return "\n".join(lines)


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

    result = groq_client.rank_quotes(rfq_details=f"RFQ ID: {rfq_id}", quotes_summary=quotes_summary)

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

        msg = f"RFQ for '{prod}' is now closed as the deadline has passed. Thank you!"
        all_notifications_confirmed = True

        for item in rfq_suppliers:
            supplier = item.get("suppliers") or {}
            supplier_id = supplier.get("id") or item.get("supplier_id")
            phone = supplier.get("phone_number")
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

    # 3. Mark Finalization Completed
    try:
        marked = db.mark_rfq_finalization_completed(rfq_id)
        if marked:
            print(f"[{now_iso}] [Finalization] RFQ '{prod}' ({rfq_id}) marked finalization_status='completed'.")
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
    # Section C: Supplier Reminders (strictly for active, open RFQs with percentage < 100)
    # =========================================================================
    try:
        active_items = db.get_active_rfq_suppliers_with_deadlines()
        print(f"[{now.isoformat()}] [Scheduler] Found {len(active_items)} active rfq_supplier items pending responses.")
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[{now.isoformat()}] [Scheduler ERROR] Failed to fetch active RFQ suppliers: {e}\n{tb}")
        db.log_webhook_error(str(e), tb, {"job": "check_deadlines_and_reminders", "stage": "fetch_active_items"})
        return

    for item in active_items:
        item_id = item.get("id")
        try:
            rfq = item.get("rfqs") or {}
            supplier = item.get("suppliers") or {}
            sent_at_str = item.get("sent_at")

            # 1. Strictly verify RFQ is active and open (deadline has not elapsed)
            if not db.is_rfq_open(rfq):
                continue

            if not sent_at_str:
                print(f"[{now.isoformat()}] [Scheduler] Skipping item {item_id}: missing sent_at timestamp.")
                continue

            try:
                sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00"))
                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=timezone.utc)
            except Exception as parse_err:
                print(f"[{now.isoformat()}] [Scheduler] Skipping item {item_id}: invalid sent_at format '{sent_at_str}': {parse_err}")
                continue

            deadline_hours = rfq.get("deadline_hours") or 24
            total_seconds = deadline_hours * 3600
            elapsed_seconds = (now - sent_at).total_seconds()
            if total_seconds <= 0:
                continue

            percentage = (elapsed_seconds / total_seconds) * 100
            if percentage >= 100:
                # Never trigger reminders for RFQs that have reached or exceeded deadline
                continue

            reminder_count = item.get("reminder_count") or 0
            phone = supplier.get("phone_number")
            prod = rfq.get("product_name") or "RFQ Item"

            print(
                f"[{now.isoformat()}] [Scheduler] Checking item {item_id}: RFQ '{prod}' (id: {rfq.get('id')}), "
                f"supplier '{supplier.get('name')}', sent_at: {sent_at.isoformat()}, elapsed: {elapsed_seconds:.0f}s/{total_seconds:.0f}s ({percentage:.1f}%), reminders_sent: {reminder_count}"
            )

            # Reminder thresholds for pending suppliers (50%, 70%, 90%)
            if percentage >= 90 and reminder_count == 2:
                print(f"[{now.isoformat()}] [Scheduler] Triggering 90% reminder for RFQ '{prod}' to {phone} (item {item_id}).")
                msg = f"Final reminder — closing the RFQ for '{prod}' soon! Please reply with your quote if available."
                client_id = rfq.get("client_id") or supplier.get("client_id")
                msg_log_id = db.log_message(client_id, supplier.get("id"), "outbound", msg, related_rfq_id=rfq.get("id"))
                await enqueue_message(phone, msg, rfq_id=rfq.get("id"), supplier_id=supplier.get("id"), message_log_id=msg_log_id)
                db.update_rfq_supplier_reminder(item["id"], 3)
            elif percentage >= 70 and reminder_count == 1:
                print(f"[{now.isoformat()}] [Scheduler] Triggering 70% reminder for RFQ '{prod}' to {phone} (item {item_id}).")
                msg = f"Reminder regarding RFQ for '{prod}'. Please send your quote when ready."
                client_id = rfq.get("client_id") or supplier.get("client_id")
                msg_log_id = db.log_message(client_id, supplier.get("id"), "outbound", msg, related_rfq_id=rfq.get("id"))
                await enqueue_message(phone, msg, rfq_id=rfq.get("id"), supplier_id=supplier.get("id"), message_log_id=msg_log_id)
                db.update_rfq_supplier_reminder(item["id"], 2)
            elif percentage >= 50 and reminder_count == 0:
                print(f"[{now.isoformat()}] [Scheduler] Triggering 50% reminder for RFQ '{prod}' to {phone} (item {item_id}).")
                msg = f"Hi! Just checking in on the RFQ for '{prod}'."
                client_id = rfq.get("client_id") or supplier.get("client_id")
                msg_log_id = db.log_message(client_id, supplier.get("id"), "outbound", msg, related_rfq_id=rfq.get("id"))
                await enqueue_message(phone, msg, rfq_id=rfq.get("id"), supplier_id=supplier.get("id"), message_log_id=msg_log_id)
                db.update_rfq_supplier_reminder(item["id"], 1)
        except Exception as item_err:
            tb = traceback.format_exc()
            print(f"[{now.isoformat()}] [Scheduler ERROR] Failed processing item {item_id}: {item_err}\n{tb}")
            db.log_webhook_error(str(item_err), tb, {"job": "check_deadlines_and_reminders", "item_id": item_id})


REQUIRED_ENV_VARS = [
    "GROQ_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "EVOLUTION_API_URL",
    "EVOLUTION_API_KEY",
    "EVOLUTION_INSTANCE",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup validation: verify all required backend env vars exist and are non-empty
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        raise RuntimeError(
            f"Backend configuration error: Missing required environment variable(s): {', '.join(missing)}. "
            f"Please check your root .env file."
        )

    print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] Starting outbound message worker task...")
    worker_task = asyncio.create_task(outbound_worker())

    # Startup recovery: re-enqueue persistent messages with status = 'queued'
    try:
        queued_msgs = db.get_queued_outbound_messages()
        recovered_count = 0
        for qm in queued_msgs:
            msg_id = qm.get("id")
            body = qm.get("body")
            rfq_id = qm.get("related_rfq_id")
            supplier_id = qm.get("supplier_id")
            phone = None
            if qm.get("suppliers") and isinstance(qm.get("suppliers"), dict):
                phone = qm.get("suppliers").get("phone_number")
            elif supplier_id:
                s = db.get_supplier_by_id(supplier_id)
                if s:
                    phone = s.get("phone_number")
            if phone and body:
                await enqueue_message(phone, body, rfq_id=rfq_id, supplier_id=supplier_id, message_log_id=msg_id)
                recovered_count += 1
        if recovered_count > 0:
            print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] Recovered and re-enqueued {recovered_count} queued outbound message(s).")
    except Exception as rec_err:
        print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan ERROR] Failed to recover queued outbound messages: {rec_err}")

    print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] Registering check_deadlines_and_reminders job on AsyncIOScheduler (interval: 15m, next_run: now)...")
    scheduler.add_job(
        check_deadlines_and_reminders,
        "interval",
        minutes=15,
        next_run_time=datetime.now(timezone.utc),
        id="check_deadlines_and_reminders",
        replace_existing=True,
    )
    scheduler.start()
    print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] AsyncIOScheduler started successfully.")
    yield
    print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] Shutting down scheduler and worker task...")
    scheduler.shutdown()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    print(f"[{datetime.now(timezone.utc).isoformat()}] [Lifespan] Outbound worker and scheduler shut down cleanly.")

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(lifespan=lifespan)


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


async def execute_validated_action(
    validation: ValidationResult,
    context: AgentContext,
    raw_message: str,
    supplier: dict,
    client_id: str,
) -> dict:
    """
    Consolidated, deterministic action execution across all inbound message origins:
    1. Executes DB mutations (quotes, clarification state, negotiation attempts, human escalation flags).
    2. Logs outbound messages in message_log.
    3. Enqueues outbound WhatsApp delivery through the paced queue.
    """
    phone_number = supplier.get("phone_number")
    supplier_id = supplier["id"]

    if validation.action == "record_quote":
        args = validation.sanitized_args
        target_rfq_id = args["rfq_id"]
        variants = args.get("variants")
        if not variants:
            variants = [{
                "variant_label": args.get("variant_label"),
                "price": args.get("price"),
                "delivery_time": args.get("delivery_time"),
                "quality_notes": args.get("quality_notes"),
                "is_available": args.get("is_available", True),
            }]

        # Ensure quote provenance reflects supplier's original message, not operator instruction
        quote_raw = context.review_raw_message if (context.input_origin == "operator" and context.review_raw_message) else raw_message

        # Check if existing quotes already match to avoid duplicate records from operator hold commands
        should_record = True
        if context.input_origin == "operator":
            existing_quotes = [q for q in context.prior_quotes if q.get("rfq_id") == target_rfq_id]
            all_match = True
            for v in variants:
                matching = [
                    q for q in existing_quotes
                    if (q.get("variant_label") or "").strip().casefold() == (v.get("variant_label") or "").strip().casefold()
                    and q.get("price") == v.get("price")
                    and q.get("is_available", True) == v.get("is_available", True)
                ]
                if not matching:
                    all_match = False
                    break
            if existing_quotes and all_match:
                should_record = False

        if should_record:
            if len(variants) == 1 and variants[0].get("variant_label") is None and variants[0].get("is_available", True) is True:
                db.record_quote(
                    rfq_id=target_rfq_id,
                    supplier_id=supplier_id,
                    price=variants[0].get("price"),
                    delivery_time=variants[0].get("delivery_time"),
                    quality_notes=variants[0].get("quality_notes"),
                    raw_message=quote_raw,
                )
            else:
                db.record_quotes_batch(
                    rfq_id=target_rfq_id,
                    supplier_id=supplier_id,
                    variants=variants,
                    raw_message=quote_raw,
                    source_message_id=context.source_message_id,
                )

        # Resolve pending clarification if one was active for this supplier
        if context.pending_clarification:
            db.resolve_pending_clarification(context.pending_clarification["id"])
            db.revert_unresolved_candidates(
                supplier_id=supplier_id,
                resolved_rfq_id=target_rfq_id,
                candidate_rfq_ids=context.pending_clarification.get("pending_rfq_ids", []),
            )

        msg_log_id = db.log_message(client_id, supplier_id, "outbound", THANK_YOU_MSG, related_rfq_id=target_rfq_id)
        if not msg_log_id:
            raise RuntimeError("Failed to log outbound thank-you message durably.")
        await enqueue_message(phone_number, THANK_YOU_MSG, rfq_id=target_rfq_id, supplier_id=supplier_id, message_log_id=msg_log_id)

        if context.match_source:
            return {"status": "recorded_via_quoted_message", "rfq_id": target_rfq_id}
        elif context.pending_clarification:
            return {
                "status": "recorded_from_clarification",
                "rfq_id": target_rfq_id,
                "clarification_id": context.pending_clarification["id"],
            }
        else:
            return {"status": "recorded", "rfq_id": target_rfq_id}

    elif validation.action == "negotiate_price":
        args = validation.sanitized_args
        target_rfq_id = args["rfq_id"]
        quote_id = args.get("quote_id")
        quoted_price = args.get("quoted_price")
        counter_price = args.get("counter_price")
        neg_msg = args["negotiation_message"]
        delivery = args.get("delivery_time")
        notes = args.get("quality_notes")
        variant_label = args.get("variant_label")

        attempts = db.increment_negotiation_attempts(target_rfq_id, supplier_id)
        if attempts <= 0:
            if context.pending_clarification:
                db.resolve_pending_clarification(context.pending_clarification["id"])
                db.revert_unresolved_candidates(
                    supplier_id=supplier_id,
                    resolved_rfq_id=target_rfq_id,
                    candidate_rfq_ids=context.pending_clarification.get("pending_rfq_ids", []),
                )
            msg_log_id = db.log_message(client_id, supplier_id, "outbound", THANK_YOU_MSG, related_rfq_id=target_rfq_id)
            if not msg_log_id:
                raise RuntimeError("Failed to log outbound message durably.")
            await enqueue_message(phone_number, THANK_YOU_MSG, rfq_id=target_rfq_id, supplier_id=supplier_id, message_log_id=msg_log_id)
            return {"status": "rejected_by_policy", "reason": "Negotiation attempt limit reached."}

        if context.pending_clarification:
            db.resolve_pending_clarification(context.pending_clarification["id"])
            db.revert_unresolved_candidates(
                supplier_id=supplier_id,
                resolved_rfq_id=target_rfq_id,
                candidate_rfq_ids=context.pending_clarification.get("pending_rfq_ids", []),
            )

        msg_log_id = db.log_message(client_id, supplier_id, "outbound", neg_msg, related_rfq_id=target_rfq_id)
        if not msg_log_id:
            raise RuntimeError("Failed to log outbound negotiation message durably.")
        await enqueue_message(phone_number, neg_msg, rfq_id=target_rfq_id, supplier_id=supplier_id, message_log_id=msg_log_id)
        return {
            "status": "negotiation_sent",
            "rfq_id": target_rfq_id,
            "quote_id": quote_id,
            "quoted_price": quoted_price,
            "counter_price": counter_price,
            "variant_label": variant_label,
            "attempts": attempts,
        }

    elif validation.action == "request_clarification":
        args = validation.sanitized_args
        candidate_ids = args.get("candidate_rfq_ids") or []
        question = args["clarifying_question"]
        norm_question = " ".join((question or "").lower().split())

        if not context.pending_clarification:
            # 1. New clarification session
            round_number = 1
            no_progress_count = 0
            created_id = db.create_pending_clarification(
                client_id=client_id,
                supplier_id=supplier_id,
                candidate_rfq_ids=candidate_ids,
                raw_message=raw_message,
                extracted_price=args.get("extracted_price"),
                extracted_delivery=args.get("extracted_delivery"),
                extracted_notes=args.get("extracted_notes"),
                round_number=round_number,
                no_progress_count=no_progress_count,
                last_question=question,
            )
            if not created_id:
                logger.error("Failed to create initial pending clarification for supplier %s", supplier_id)
                db.flag_for_human_review(
                    client_id=client_id,
                    supplier_id=supplier_id,
                    rfq_id=candidate_ids[0] if candidate_ids else (context.matched_rfq_id or None),
                    reason="Database error: Failed to create pending clarification.",
                    category="other",
                    raw_message=raw_message,
                )
                return {"status": "failed_creation", "reason": "Failed to create pending clarification."}
        else:
            prev_pc = context.pending_clarification
            prev_candidates = set(prev_pc.get("pending_rfq_ids") or [])
            new_candidates = set(candidate_ids)
            prev_round = prev_pc.get("round_number", 1)
            prev_no_prog = prev_pc.get("no_progress_count", 0)
            prev_question = prev_pc.get("last_question") or ""
            norm_prev_question = " ".join((prev_question or "").lower().split())

            next_round = prev_round + 1

            # Check absolute turn ceiling
            if next_round > MAX_TOTAL_CLARIFICATION_TURNS:
                db.abandon_pending_clarification(prev_pc["id"])
                cand_prods = []
                for rfq_id in (candidate_ids or list(prev_candidates)):
                    cand_rfq = next((r.get("rfqs", r) for r in (context.open_rfqs or []) if str(r.get("rfqs", r).get("id")) == str(rfq_id)), None)
                    if cand_rfq and isinstance(cand_rfq, dict):
                        cand_prods.append(cand_rfq.get("product_name") or str(rfq_id))
                    else:
                        cand_prods.append(str(rfq_id))
                prods_str = ", ".join(cand_prods)
                reason = f"Clarification session reached maximum turn limit ({MAX_TOTAL_CLARIFICATION_TURNS} turns). Remaining candidates: {prods_str}"
                db.flag_for_human_review(
                    client_id=client_id,
                    supplier_id=supplier_id,
                    rfq_id=candidate_ids[0] if candidate_ids else (context.matched_rfq_id or None),
                    reason=reason,
                    category="clarification_stalled",
                    raw_message=raw_message,
                )
                return {"status": "escalated_to_human", "reason": reason, "category": "clarification_stalled"}

            # Calculate semantic delta: Progress vs No Progress
            is_narrowed = len(new_candidates) < len(prev_candidates) and new_candidates.issubset(prev_candidates)
            is_same_question = bool(norm_prev_question and norm_question == norm_prev_question)

            if is_narrowed and not is_same_question:
                no_progress_count = 0
            else:
                no_progress_count = prev_no_prog + 1

            if no_progress_count >= MAX_NO_PROGRESS_ATTEMPTS:
                db.abandon_pending_clarification(prev_pc["id"])
                cand_prods = []
                for rfq_id in (candidate_ids or list(prev_candidates)):
                    cand_rfq = next((r.get("rfqs", r) for r in (context.open_rfqs or []) if str(r.get("rfqs", r).get("id")) == str(rfq_id)), None)
                    if cand_rfq and isinstance(cand_rfq, dict):
                        cand_prods.append(cand_rfq.get("product_name") or str(rfq_id))
                    else:
                        cand_prods.append(str(rfq_id))
                prods_str = ", ".join(cand_prods)
                reason = f"Clarification stalled after {no_progress_count} uninformative replies. Remaining candidates: {prods_str}"
                db.flag_for_human_review(
                    client_id=client_id,
                    supplier_id=supplier_id,
                    rfq_id=candidate_ids[0] if candidate_ids else (context.matched_rfq_id or None),
                    reason=reason,
                    category="clarification_stalled",
                    raw_message=raw_message,
                )
                return {"status": "escalated_to_human", "reason": reason, "category": "clarification_stalled"}

            advanced = db.advance_pending_clarification(
                previous_id=prev_pc["id"],
                client_id=client_id,
                supplier_id=supplier_id,
                candidate_rfq_ids=candidate_ids,
                raw_message=raw_message,
                extracted_price=args.get("extracted_price"),
                extracted_delivery=args.get("extracted_delivery"),
                extracted_notes=args.get("extracted_notes"),
                round_number=next_round,
                no_progress_count=no_progress_count,
                last_question=question,
            )
            if not advanced:
                logger.error("Failed to atomically advance pending clarification %s", prev_pc["id"])
                db.flag_for_human_review(
                    client_id=client_id,
                    supplier_id=supplier_id,
                    rfq_id=candidate_ids[0] if candidate_ids else (context.matched_rfq_id or None),
                    reason="Database error: Failed to atomically advance pending clarification.",
                    category="other",
                    raw_message=raw_message,
                )
                return {"status": "failed_advancement", "reason": "Failed to atomically advance pending clarification."}

        single_rfq = candidate_ids[0] if len(candidate_ids) == 1 else (context.matched_rfq_id or None)
        if single_rfq:
            msg_log_id = db.log_message(client_id, supplier_id, "outbound", question, related_rfq_id=single_rfq)
            if not msg_log_id:
                raise RuntimeError("Failed to log outbound clarification message durably.")
            await enqueue_message(phone_number, question, rfq_id=single_rfq, supplier_id=supplier_id, message_log_id=msg_log_id)
        else:
            msg_log_id = db.log_message(client_id, supplier_id, "outbound", question)
            if not msg_log_id:
                raise RuntimeError("Failed to log outbound clarification message durably.")
            await enqueue_message(phone_number, question, message_log_id=msg_log_id)
        return {"status": "clarification_needed", "question": question}

    elif validation.action == "send_procurement_message":
        args = validation.sanitized_args
        msg = args["message"]
        target_rfq_id = args.get("rfq_id") or context.matched_rfq_id
        if target_rfq_id:
            msg_log_id = db.log_message(client_id, supplier_id, "outbound", msg, related_rfq_id=target_rfq_id)
            if not msg_log_id:
                raise RuntimeError("Failed to log outbound procurement message durably.")
            await enqueue_message(phone_number, msg, rfq_id=target_rfq_id, supplier_id=supplier_id, message_log_id=msg_log_id)
        else:
            msg_log_id = db.log_message(client_id, supplier_id, "outbound", msg)
            if not msg_log_id:
                raise RuntimeError("Failed to log outbound procurement message durably.")
            await enqueue_message(phone_number, msg, supplier_id=supplier_id, message_log_id=msg_log_id)
        return {"status": "message_sent", "message": msg, "rfq_id": target_rfq_id}

    elif validation.action == "escalate_to_human":
        args = validation.sanitized_args
        esc_rfq_id = args.get("rfq_id") or context.matched_rfq_id
        reason = args.get("reason", "Human review requested by agent")
        category = args.get("category", "other")

        db.flag_for_human_review(
            client_id=client_id,
            supplier_id=supplier_id,
            rfq_id=esc_rfq_id,
            reason=reason,
            category=category,
            raw_message=raw_message,
        )
        if context.pending_clarification:
            db.abandon_pending_clarification(context.pending_clarification["id"])

        if esc_rfq_id:
            msg_log_id = db.log_message(client_id, supplier_id, "outbound", HUMAN_ACK_MSG, related_rfq_id=esc_rfq_id)
            if not msg_log_id:
                raise RuntimeError("Failed to log outbound human ack message durably.")
            await enqueue_message(phone_number, HUMAN_ACK_MSG, rfq_id=esc_rfq_id, supplier_id=supplier_id, message_log_id=msg_log_id)
        else:
            msg_log_id = db.log_message(client_id, supplier_id, "outbound", HUMAN_ACK_MSG)
            if not msg_log_id:
                raise RuntimeError("Failed to log outbound human ack message durably.")
            await enqueue_message(phone_number, HUMAN_ACK_MSG, message_log_id=msg_log_id)
        return {
            "status": "escalated",
            "reason": reason,
            "category": category,
        }

    return {"status": "unhandled_action", "action": validation.action}


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    payload = {}
    try:
        payload = await request.json()
        # Change 1 — Webhook event filtering: ignore non-upsert events
        event_type = payload.get("event")
        if event_type and event_type != "messages.upsert":
            return {"status": "ignored", "reason": f"non-upsert event: {event_type}"}
    except Exception as parse_err:
        print(f"[Webhook Debug Error] Could not parse request body as JSON: {parse_err}")

    try:
        # Evolution API payload shape
        data = payload.get("data", {})

        if isinstance(data, list):
            if not data:
                return {"status": "ignored", "reason": "empty batched event"}
            data = data[0]

        key_data = data.get("key", {})

        # Ignore outgoing messages sent by ourselves
        if key_data.get("fromMe", False):
            return {"status": "ignored", "reason": "outgoing message (fromMe)"}

        msg_key_id = key_data.get("id")

        raw_remote_jid = key_data.get("remoteJid", "")
        sender_phone = normalize_phone(raw_remote_jid)
        message_text = (
            data.get("message", {}).get("conversation", "")
            or data.get("message", {}).get("extendedTextMessage", {}).get("text", "")
        )
        quoted_stanza_id = (
            data.get("contextInfo", {}).get("stanzaId")
            or data.get("message", {}).get("extendedTextMessage", {}).get("contextInfo", {}).get("stanzaId")
            or data.get("message", {}).get("contextInfo", {}).get("stanzaId")
        )
        quoted_text = (
            data.get("contextInfo", {}).get("quotedMessage", {}).get("conversation", "")
            or data.get("message", {}).get("extendedTextMessage", {}).get("contextInfo", {}).get("quotedMessage", {}).get("conversation", "")
            or data.get("message", {}).get("contextInfo", {}).get("quotedMessage", {}).get("conversation", "")
        )

        if not sender_phone or not message_text:
            return {"status": "ignored", "reason": "no message content"}

        # Resolve tenant/client unambiguously by Evolution API instance name
        instance_name = (
            payload.get("instance")
            or payload.get("instanceName")
            or (data.get("instance") if isinstance(data, dict) else None)
            or (data.get("instanceName") if isinstance(data, dict) else None)
            or EVOLUTION_INSTANCE
        )

        client = db.get_client_by_instance(instance_name) if instance_name else None
        if not client:
            err_msg = f"Unknown or unconfigured instance '{instance_name}' in webhook payload."
            db.log_webhook_error(
                error_message=err_msg,
                traceback_str=f"Instance '{instance_name}' does not match any client in database. Raw payload: {payload}",
                raw_payload=payload,
            )
            print(f"[Webhook] {err_msg}")
            return {"status": "ignored", "reason": f"unknown instance: {instance_name}"}

        client_id = client["id"]

        # Phase 7.1: Durable Webhook Idempotency Claim (atomic at database level)
        if msg_key_id:
            claimed = db.claim_webhook_message(client_id, msg_key_id)
            if not claimed:
                print(f"[Webhook] Duplicate message '{msg_key_id}' for client '{client_id}' already processed. Ignoring.")
                return {"status": "ignored", "reason": f"already processed message id: {msg_key_id}"}

        # Look up supplier scoped strictly to this client
        supplier = db.get_supplier_by_phone(client_id, sender_phone)
        if not supplier:
            err_msg = f"Unknown supplier: sender_phone='{sender_phone}' (raw remoteJid='{raw_remote_jid}') not found for client '{client.get('name')}' (id={client_id}, instance='{instance_name}')."
            db.log_webhook_error(
                error_message=err_msg,
                traceback_str=f"Unmatched incoming WhatsApp message from sender_phone='{sender_phone}' for client {client_id}. Message: {message_text[:500]}",
                raw_payload=payload,
            )
            print(f"[Webhook] {err_msg}")
            return {"status": "ignored", "reason": "unknown supplier"}

        inbound_log_id = db.log_message(client_id, supplier["id"], "inbound", message_text)

        # 1. Deterministic Quoted / Stanza Match
        matched_rfq_supplier = None
        match_source = None
        if quoted_stanza_id:
            matched_rfq_supplier = db.get_rfq_supplier_by_sent_message_id(supplier["id"], quoted_stanza_id)
            if not matched_rfq_supplier:
                print(f"[Webhook] Quoted stanzaId '{quoted_stanza_id}' present but does not match any open RFQ for supplier {supplier['id']}. Ignoring cross-RFQ fallback.")
                return {"status": "ignored", "reason": "quoted_stanza_id already responded or closed"}
            match_source = "exact_stanza"
        elif quoted_text:
            matched_rfq_supplier = db.get_rfq_supplier_by_quoted_text(supplier["id"], quoted_text)
            if matched_rfq_supplier:
                match_source = "quoted_text"

        # 2. Check Pending Clarification
        pending = db.get_pending_clarification_for_supplier(supplier["id"])
        if pending and not isinstance(pending, dict):
            pending = None

        # 3. Retrieve Open RFQs & Candidate RFQs
        open_rfqs = db.get_open_rfqs_for_supplier(supplier["id"]) or []
        if matched_rfq_supplier and not any(
            (e.get("rfqs", {}).get("id") or e.get("id")) == (matched_rfq_supplier.get("rfqs", {}).get("id") or matched_rfq_supplier.get("id"))
            for e in open_rfqs
        ):
            open_rfqs.append(matched_rfq_supplier)

        if pending:
            candidate_ids = pending.get("pending_rfq_ids", [])
            if candidate_ids:
                cand_rfqs = db.get_rfqs_by_ids(candidate_ids)
                for cr in cand_rfqs:
                    cr_id = cr.get("rfqs", {}).get("id") or cr.get("id")
                    if not any((e.get("rfqs", {}).get("id") or e.get("id")) == cr_id for e in open_rfqs):
                        open_rfqs.append(cr)

        if not open_rfqs and not matched_rfq_supplier and not pending:
            return {
                "status": "no_open_rfq",
                "note": "message received but no active RFQ to match",
            }

        # 4. Fetch Prior Quotes, Negotiation Attempts, and Competitive Context
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

        # 5. Fetch Bounded Chronological Conversation History (excluding current inbound turn)
        try:
            conv_history = db.get_supplier_conversation_history(client_id, supplier["id"], limit=10, exclude_message_id=inbound_log_id)
        except Exception as e:
            logger.warning(f"Error loading conversation history: {e}")
            conv_history = []

        matched_rfq_id = None
        if matched_rfq_supplier:
            matched_rfq = matched_rfq_supplier.get("rfqs", matched_rfq_supplier)
            matched_rfq_id = matched_rfq.get("id") if isinstance(matched_rfq, dict) else None

        # 6. Build Single Unified AgentContext
        context = AgentContext(
            client_id=client_id,
            supplier_id=supplier["id"],
            supplier_name=supplier.get("name"),
            supplier_phone=supplier.get("phone_number"),
            input_origin="supplier",
            matched_rfq_id=matched_rfq_id,
            match_source=match_source,
            open_rfqs=open_rfqs,
            pending_clarification=pending,
            prior_quotes=prior_quotes,
            negotiation_attempts=negotiation_attempts,
            competitive_context=competitive_context,
            conversation_history=conv_history,
            source_message_id=inbound_log_id,
        )

        # 7. Unified Reasoning Execution
        try:
            decision = groq_client.reason_about_procurement_message(message_text, context, input_origin="supplier")
        except Exception as groq_err:
            tb_str = traceback.format_exc()
            print(f"\n=== GROQ ERROR (reason_about_procurement_message) ===\n{tb_str}\n======================================================\n")
            db.log_webhook_error(f"Groq reason_about_procurement_message error: {groq_err}", tb_str, payload)
            reason = f"Groq AI service error during message reasoning: {str(groq_err)}"
            db.flag_for_human_review(
                client_id=client_id,
                supplier_id=supplier["id"],
                rfq_id=matched_rfq_id,
                reason=reason,
                category="other",
                raw_message=message_text,
            )
            if pending:
                db.abandon_pending_clarification(pending["id"])
            msg_log_id = db.log_message(client_id, supplier["id"], "outbound", HUMAN_ACK_MSG)
            await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG, message_log_id=msg_log_id)
            return {"status": "escalated_due_to_groq_error", "reason": reason}

        decision_tool = decision.get("tool_name", "")
        decision_args = dict(decision.get("arguments", {}))
        if decision_tool in ("record_quote", "negotiate_price") and not decision_args.get("rfq_id") and matched_rfq_id:
            decision_args["rfq_id"] = matched_rfq_id

        proposal = ActionProposal(
            tool_name=decision_tool,
            arguments=decision_args,
            raw_message=message_text,
        )

        # 8. Policy Validator Execution (with Deterministic Stanza Lock & Semantic Clarification Context)
        validation = validate_action(
            proposal,
            client_id=client_id,
            supplier_id=supplier["id"],
            context_rfqs=open_rfqs,
            matched_rfq_id=context.matched_rfq_id,
            pending_clarification=context.pending_clarification,
            input_origin="supplier",
        )

        if not validation.is_valid:
            print(f"[Policy Validator] Rejected action '{proposal.tool_name}': {validation.reason}")
            db.flag_for_human_review(
                client_id=client_id,
                supplier_id=supplier["id"],
                rfq_id=proposal.arguments.get("rfq_id") or matched_rfq_id,
                reason=f"Policy Validator rejection: {validation.reason}",
                category="other",
                raw_message=message_text,
            )
            if pending:
                db.abandon_pending_clarification(pending["id"])
            if matched_rfq_id:
                msg_log_id = db.log_message(client_id, supplier["id"], "outbound", HUMAN_ACK_MSG, related_rfq_id=matched_rfq_id)
                await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG, rfq_id=matched_rfq_id, supplier_id=supplier["id"], message_log_id=msg_log_id)
            else:
                msg_log_id = db.log_message(client_id, supplier["id"], "outbound", HUMAN_ACK_MSG)
                await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG, message_log_id=msg_log_id)
            return {"status": "rejected_by_policy", "reason": validation.reason}

        # 9. Consolidated Deterministic Execution
        return await execute_validated_action(validation, context, message_text, supplier, client_id)

    except Exception as e:
        tb_str = traceback.format_exc()
        print(f"\n=== WEBHOOK ERROR ===\n{tb_str}\n=====================\n")
        db.log_webhook_error(str(e), tb_str, payload)
        return {"status": "error_logged", "note": "internal error, logged for review"}

    except Exception as e:
        tb_str = traceback.format_exc()
        print(f"\n=== WEBHOOK ERROR ===\n{tb_str}\n=====================\n")
        db.log_webhook_error(str(e), tb_str, payload)
        return {"status": "error_logged", "note": "internal error, logged for review"}


@app.post("/rfq/create")
async def create_rfq_endpoint(req: RFQCreateRequest, current_user=Depends(get_current_user)):
    """Creates a new RFQ, matches active suppliers by category, and enqueues initial WhatsApp RFQs."""
    # Derive client_id from authenticated profile (do not trust client-supplied client_id)
    client_id = current_user.get("client_id")

    create_kwargs = {
        "client_id": client_id,
        "product_name": req.product_name,
        "category": req.category,
        "deadline_hours": req.deadline_hours,
        "specs": req.specs,
        "quantity": req.quantity,
    }
    if req.acceptable_price_min is not None:
        create_kwargs["acceptable_price_min"] = req.acceptable_price_min
    if req.acceptable_price_max is not None:
        create_kwargs["acceptable_price_max"] = req.acceptable_price_max

    rfq, matched_suppliers = db.create_rfq_and_match_suppliers(**create_kwargs)

    if not matched_suppliers:
        return {
            "status": "no_matching_suppliers",
            "rfq_id": rfq["id"],
            "category": req.category,
            "message": f"RFQ created (ID: {rfq['id']}), but no active suppliers matched category '{req.category}'.",
        }

    rfq_msg = (
        f"Hi! This is Amafha Hardware Store.\n"
        f"We're requesting a quote for the following item:\n\n"
        f"• Product: {req.product_name}\n"
        f"• Specs: {req.specs or 'Standard'}\n"
        f"• Quantity: {req.quantity or 'N/A'}\n"
        f"• Quote Required Within: {req.deadline_hours} hour(s)\n\n"
        f"Please reply directly to this message with your price per unit (AED) and estimated delivery time. Thanks!"
    )

    for supplier in matched_suppliers:
        msg_log_id = db.log_message(client_id, supplier["id"], "outbound", rfq_msg, related_rfq_id=rfq["id"])
        await enqueue_message(supplier["phone_number"], rfq_msg, rfq_id=rfq["id"], supplier_id=supplier["id"], message_log_id=msg_log_id)

    return {
        "status": "success",
        "rfq_id": rfq["id"],
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

    # Parse optional row-level updates (product_name, quantity, category, deadline_hours, specs, acceptable_price_min, acceptable_price_max)
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
        }
        if final_min is not None:
            create_kwargs["acceptable_price_min"] = final_min
        if final_max is not None:
            create_kwargs["acceptable_price_max"] = final_max

        rfq, matched_suppliers = db.create_rfq_and_match_suppliers(**create_kwargs)

        if matched_suppliers:
            rfq_msg = (
                f"Hi! This is Amafha Hardware Store.\n"
                f"We're requesting a quote for the following item:\n\n"
                f"• Product: {final_product_name}\n"
                f"• Specs: {final_specs or 'Standard'}\n"
                f"• Quantity: {final_quantity or 'N/A'}\n"
                f"• Quote Required Within: {final_deadline} hour(s)\n\n"
                f"Please reply directly to this message with your price per unit (AED) and estimated delivery time. Thanks!"
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

        if not validation.is_valid:
            logger.warning(f"[POLICY REJECTION] Operator instruction failed policy: {validation.reason}")
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
        raise HTTPException(status_code=500, detail=f"Failed to process operator instruction: {str(e)}")



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


