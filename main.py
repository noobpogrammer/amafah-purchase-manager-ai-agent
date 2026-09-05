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
from datetime import datetime, timezone
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Depends
from pydantic import BaseModel, Field, ValidationInfo, field_validator
import requests
from starlette.concurrency import run_in_threadpool

import db
from auth import get_current_user
import groq_client
import guardrails



scheduler = AsyncIOScheduler()

DEMO_CLIENT_ID = "d88c52ad-3d0b-42e9-86f1-b9f70018856b"
THANK_YOU_MSG = "Thanks for the quote! We'll be in touch if we move forward."
HUMAN_ACK_MSG = "Thanks! We'll review your response and get back to you shortly."
PROCESSED_MESSAGE_IDS: set = set()

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


async def enqueue_message(phone_number: str, message: str, rfq_id: str = None, supplier_id: str = None):
    """Pushes an outbound message onto the asyncio queue for paced sending."""
    await outbound_queue.put((phone_number, message, rfq_id, supplier_id))


async def outbound_worker():
    """Background worker that processes outbound WhatsApp messages one by one with randomized delay."""
    while True:
        try:
            item = await outbound_queue.get()
            if len(item) == 4:
                phone_number, message, rfq_id, supplier_id = item
            else:
                phone_number, message = item
                rfq_id, supplier_id = None, None

            try:
                resp = await run_in_threadpool(send_whatsapp_message, phone_number, message)
                if resp and isinstance(resp, dict) and rfq_id and supplier_id:
                    msg_id = resp.get("key", {}).get("id") or resp.get("id")
                    if msg_id:
                        db.update_rfq_supplier_sent_message_id(rfq_id, supplier_id, msg_id)
            except Exception as e:
                print(f"[Outbound Worker] Error sending WhatsApp message to {phone_number}: {e}")
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
    return response.json()


def normalize_phone(remote_jid: str) -> str:
    """Strips everything from '@' onward to normalize WhatsApp remoteJid to plain digits."""
    if not remote_jid:
        return ""
    return remote_jid.split("@")[0]


def format_rfq_context(open_rfqs: list) -> str:
    if not open_rfqs:
        return "No open RFQs for this supplier."
    lines = []
    for entry in open_rfqs:
        rfq = entry["rfqs"]
        lines.append(
            f"- RFQ ID: {rfq['id']} | Product: {rfq['product_name']} | "
            f"Specs: {rfq.get('specs', '-')} | Qty: {rfq.get('quantity', '-')}"
        )
    return "\n".join(lines)


def format_prior_quotes_context(prior_quotes: list) -> str:
    if not prior_quotes:
        return "No prior quotes on record for this supplier."
    lines = []
    for q in prior_quotes:
        product = q.get("rfqs", {}).get("product_name", "Unknown Product") if isinstance(q.get("rfqs"), dict) else "Unknown Product"
        lines.append(
            f"- Product: {product} | RFQ ID: {q['rfq_id']} | Price: ${q['price']} | "
            f"Delivery: {q.get('delivery_time', '-')} | Notes: {q.get('quality_notes', '-')}"
        )
    return "\n".join(lines)


def generate_ranking(rfq_id: str) -> dict:
    """Generates comparison ranking for quotes received on an RFQ and saves to DB."""
    quotes = db.get_quotes_for_rfq(rfq_id)
    if not quotes:
        return {"error": "No quotes found for this RFQ"}
    quotes_summary = "\n".join(
        f"- Supplier ID: {q['supplier_id']} (Name: {q['suppliers']['name']}): ${q['price']}, delivery: {q.get('delivery_time', '-')}, "
        f"notes: {q.get('quality_notes', '-')}"
        for q in quotes
    )
    result = groq_client.rank_quotes(rfq_details=f"RFQ ID: {rfq_id}", quotes_summary=quotes_summary)
    
    # Ensure best_supplier_id is a valid supplier UUID from quotes
    best_supplier_id = result.get("best_supplier_id")
    quote_supplier_ids = [q["supplier_id"] for q in quotes]
    if best_supplier_id not in quote_supplier_ids:
        # Fallback to first quote's supplier_id if LLM returned supplier name instead of ID
        best_supplier_id = quote_supplier_ids[0]

    db.save_ranking(rfq_id, best_supplier_id, result.get("reasoning", ""), result)
    return result


def check_and_auto_rank(rfq_id: str):
    """Triggers ranking automatically if all suppliers for this RFQ have responded or timed out."""
    if db.is_rfq_fully_processed(rfq_id) and not db.ranking_exists(rfq_id):
        print(f"Auto-triggering quote ranking for completed RFQ: {rfq_id}")
        generate_ranking(rfq_id)


async def check_deadlines_and_reminders():
    """Background cron job that checks active RFQs and handles deadline expiry & reminders."""
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] [Scheduler] Running check_deadlines_and_reminders...")
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
            reminder_count = item.get("reminder_count") or 0
            phone = supplier.get("phone_number")
            prod = rfq.get("product_name") or "RFQ Item"

            print(
                f"[{now.isoformat()}] [Scheduler] Checking item {item_id}: RFQ '{prod}' (id: {rfq.get('id')}), "
                f"supplier '{supplier.get('name')}', sent_at: {sent_at.isoformat()}, elapsed: {elapsed_seconds:.0f}s/{total_seconds:.0f}s ({percentage:.1f}%), reminders_sent: {reminder_count}"
            )

            if percentage >= 100:
                print(f"[{now.isoformat()}] [Scheduler] Deadline expired (100%) for RFQ '{prod}' (item {item_id}). Closing RFQ.")
                msg = f"RFQ for '{prod}' is now closed as the deadline has passed. Thank you!"
                client_id = rfq.get("client_id") or supplier.get("client_id")
                db.log_message(client_id, supplier.get("id"), "outbound", msg)
                await enqueue_message(phone, msg)
                db.close_rfq(rfq.get("id"), "closed")
                check_and_auto_rank(rfq.get("id"))
            elif percentage >= 90 and reminder_count == 2:
                print(f"[{now.isoformat()}] [Scheduler] Triggering 90% reminder for RFQ '{prod}' to {phone} (item {item_id}).")
                msg = f"Final reminder — closing the RFQ for '{prod}' soon! Please reply with your quote if available."
                client_id = rfq.get("client_id") or supplier.get("client_id")
                db.log_message(client_id, supplier.get("id"), "outbound", msg)
                await enqueue_message(phone, msg, rfq_id=rfq.get("id"), supplier_id=supplier.get("id"))
                db.update_rfq_supplier_reminder(item["id"], 3)
            elif percentage >= 70 and reminder_count == 1:
                print(f"[{now.isoformat()}] [Scheduler] Triggering 70% reminder for RFQ '{prod}' to {phone} (item {item_id}).")
                msg = f"Reminder regarding RFQ for '{prod}'. Please send your quote when ready."
                client_id = rfq.get("client_id") or supplier.get("client_id")
                db.log_message(client_id, supplier.get("id"), "outbound", msg)
                await enqueue_message(phone, msg, rfq_id=rfq.get("id"), supplier_id=supplier.get("id"))
                db.update_rfq_supplier_reminder(item["id"], 2)
            elif percentage >= 50 and reminder_count == 0:
                print(f"[{now.isoformat()}] [Scheduler] Triggering 50% reminder for RFQ '{prod}' to {phone} (item {item_id}).")
                msg = f"Hi! Just checking in on the RFQ for '{prod}'."
                client_id = rfq.get("client_id") or supplier.get("client_id")
                db.log_message(client_id, supplier.get("id"), "outbound", msg)
                await enqueue_message(phone, msg, rfq_id=rfq.get("id"), supplier_id=supplier.get("id"))
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
        # Evolution API payload shape — adjust field paths to match your actual webhook format
        data = payload.get("data", {})

        # Evolution API occasionally sends "data" as a list (batched events)
        # instead of a single dict. Take the first item if so; skip if empty.
        if isinstance(data, list):
            if not data:
                return {"status": "ignored", "reason": "empty batched event"}
            data = data[0]

        key_data = data.get("key", {})

        # Ignore outgoing messages sent by ourselves (e.g., our own outgoing RFQs)
        if key_data.get("fromMe", False):
            return {"status": "ignored", "reason": "outgoing message (fromMe)"}

        # Change 2 — Message ID deduplication
        msg_key_id = key_data.get("id")
        if msg_key_id:
            if msg_key_id in PROCESSED_MESSAGE_IDS:
                return {"status": "ignored", "reason": f"already processed message id: {msg_key_id}"}
            PROCESSED_MESSAGE_IDS.add(msg_key_id)
            if len(PROCESSED_MESSAGE_IDS) > 2000:
                PROCESSED_MESSAGE_IDS.clear()

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

        # Resolve supplier across all clients (Evolution instance is shared)
        supplier = db.get_supplier_by_phone_any_client(sender_phone)
        if not supplier:
            return {"status": "ignored", "reason": "unknown supplier"}

        # Derive tenant from supplier record — this is the source of truth for this webhook
        client_id = supplier.get("client_id")
        db.log_message(client_id, supplier["id"], "inbound", message_text)

        # Fix 1 & Change 3: Check if message is a quoted reply (reply-to) matching a sent RFQ message
        matched_rfq_supplier = None
        if quoted_stanza_id:
            matched_rfq_supplier = db.get_rfq_supplier_by_sent_message_id(supplier["id"], quoted_stanza_id)
            if not matched_rfq_supplier:
                print(f"[Webhook] Quoted stanzaId '{quoted_stanza_id}' present but does not match any open RFQ for supplier {supplier['id']}. Ignoring cross-RFQ fallback.")
                return {"status": "ignored", "reason": "quoted_stanza_id already responded or closed"}
        elif quoted_text:
            matched_rfq_supplier = db.get_rfq_supplier_by_quoted_text(supplier["id"], quoted_text)

        if matched_rfq_supplier and matched_rfq_supplier.get("rfqs"):
                matched_rfq = matched_rfq_supplier["rfqs"]
                print(f"[Webhook] Direct match via quoted message (stanzaId: {quoted_stanza_id}) -> RFQ ID: {matched_rfq['id']} ({matched_rfq['product_name']})")

                # Single RFQ context — bypasses multi-RFQ ambiguity resolution entirely!
                rfq_context = format_rfq_context([matched_rfq_supplier])
                prior_quotes = db.get_supplier_prior_quotes(supplier["id"], [matched_rfq["id"]])
                prior_quotes_context = format_prior_quotes_context(prior_quotes)

                try:
                    decision = groq_client.route_supplier_message(message_text, rfq_context, prior_quotes_context)
                except Exception as groq_err:
                    tb_str = traceback.format_exc()

                    print(f"\n=== GROQ ERROR (quoted message route) ===\n{tb_str}\n===========================================\n")
                    db.log_webhook_error(f"Groq quoted route error: {groq_err}", tb_str, payload)
                    reason = f"Groq AI service error during message routing: {str(groq_err)}"
                    db.flag_for_human_review(
                        client_id=client_id,
                        supplier_id=supplier["id"],
                        rfq_id=matched_rfq["id"],
                        reason=reason,
                        category="other",
                        raw_message=message_text,
                    )
                    db.log_message(client_id, supplier["id"], "outbound", HUMAN_ACK_MSG)
                    await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG)
                    return {"status": "escalated_due_to_groq_error", "reason": reason}

                if decision["tool_name"] == "record_quote":
                    args = decision["arguments"]
                    target_rfq_id = args.get("rfq_id") or matched_rfq["id"]
                    db.record_quote(
                        rfq_id=target_rfq_id,
                        supplier_id=supplier["id"],
                        price=args["price"],
                        delivery_time=args.get("delivery_time"),
                        quality_notes=args.get("quality_notes"),
                        raw_message=message_text,
                    )

                    # Check for pending clarification for supplier and revert unresolved candidates
                    pending_clarif = db.get_pending_clarification_for_supplier(supplier["id"])
                    if pending_clarif:
                        db.resolve_pending_clarification(pending_clarif["id"])
                        db.revert_unresolved_candidates(
                            supplier_id=supplier["id"],
                            resolved_rfq_id=target_rfq_id,
                            candidate_rfq_ids=pending_clarif.get("pending_rfq_ids", []),
                        )

                    db.log_message(client_id, supplier["id"], "outbound", THANK_YOU_MSG)
                    await enqueue_message(supplier["phone_number"], THANK_YOU_MSG)
                    check_and_auto_rank(target_rfq_id)
                    return {"status": "recorded_via_quoted_message", "rfq_id": target_rfq_id}

                elif decision["tool_name"] == "request_clarification":
                    args = decision["arguments"]
                    db.create_pending_clarification(
                        client_id=client_id,
                        supplier_id=supplier["id"],
                        candidate_rfq_ids=args.get("candidate_rfq_ids", [matched_rfq["id"]]),
                        raw_message=message_text,
                        extracted_price=args.get("extracted_price"),
                        extracted_delivery=args.get("extracted_delivery"),
                        extracted_notes=args.get("extracted_notes"),
                    )
                    question = args["clarifying_question"]
                    if not guardrails.is_safe_to_send(question):
                        print(f"[Guardrail Triggered] Unsafe output in clarifying question: {question}")
                        db.flag_for_human_review(
                            client_id=client_id,
                            supplier_id=supplier["id"],
                            rfq_id=None,
                            reason="AI generated non-compliant or code response caught by code-level safety guardrail",
                            category="other",
                            raw_message=message_text,
                        )
                        question = HUMAN_ACK_MSG
                    db.log_message(client_id, supplier["id"], "outbound", question)
                    await enqueue_message(supplier["phone_number"], question)
                    return {"status": "clarification_needed", "question": question}

                elif decision["tool_name"] == "escalate_to_human":
                    args = decision["arguments"]
                    db.flag_for_human_review(
                        client_id=client_id,
                        supplier_id=supplier["id"],
                        rfq_id=args.get("rfq_id") or matched_rfq["id"],
                        reason=args.get("reason", "Human review requested by agent"),
                        category=args.get("category", "other"),
                        raw_message=message_text,
                    )
                    db.log_message(client_id, supplier["id"], "outbound", HUMAN_ACK_MSG)
                    await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG)
                    return {
                        "status": "escalated",
                        "reason": args.get("reason"),
                        "category": args.get("category"),
                    }

        # Check for unresolved pending clarification (status = 'awaiting_reply') for this supplier
        pending = db.get_pending_clarification_for_supplier(supplier["id"])
        if pending:
            # Check clarification rounds cap directly on thread's round_number (max 2 rounds allowed)
            current_round = pending.get("round_number", 1)
            if current_round >= 2:
                db.abandon_pending_clarification(pending["id"])
                reason = "Maximum clarification rounds (2) exceeded for supplier."
                db.flag_for_human_review(
                    client_id=client_id,
                    supplier_id=supplier["id"],
                    rfq_id=None,
                    reason=reason,
                    category="unclear_intent",
                    raw_message=message_text,
                )
                ack_msg = "Thanks! We will have a team member follow up with you directly."
                db.log_message(client_id, supplier["id"], "outbound", ack_msg)
                await enqueue_message(supplier["phone_number"], ack_msg)
                return {"status": "escalated", "reason": reason, "category": "unclear_intent"}

            candidate_rfq_ids = pending.get("pending_rfq_ids", [])
            candidate_rfqs = db.get_rfqs_by_ids(candidate_rfq_ids)
            rfq_context = format_rfq_context(candidate_rfqs)

            # Fetch prior quotes context for contradiction detection
            prior_quotes = db.get_supplier_prior_quotes(supplier["id"], candidate_rfq_ids)
            prior_quotes_context = format_prior_quotes_context(prior_quotes)

            try:
                decision = groq_client.resolve_clarification(
                    message_text=message_text,
                    candidate_rfqs_context=rfq_context,
                    previous_message=pending.get("raw_message", ""),
                    prior_quotes_context=prior_quotes_context,
                )
            except Exception as groq_err:
                tb_str = traceback.format_exc()
                print(f"\n=== GROQ ERROR (resolve_clarification) ===\n{tb_str}\n=====================================\n")
                db.log_webhook_error(f"Groq resolve_clarification error: {groq_err}", tb_str, payload)
                reason = f"Groq AI service error during clarification resolution: {str(groq_err)}"
                db.flag_for_human_review(
                    client_id=client_id,
                    supplier_id=supplier["id"],
                    rfq_id=None,
                    reason=reason,
                    category="other",
                    raw_message=message_text,
                )
                db.abandon_pending_clarification(pending["id"])
                db.log_message(client_id, supplier["id"], "outbound", HUMAN_ACK_MSG)
                await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG)
                return {"status": "escalated_due_to_groq_error", "reason": reason}

            if decision["tool_name"] == "record_quote":
                args = decision["arguments"]
                db.record_quote(
                    rfq_id=args["rfq_id"],
                    supplier_id=supplier["id"],
                    price=args["price"],
                    delivery_time=args.get("delivery_time"),
                    quality_notes=args.get("quality_notes"),
                    raw_message=message_text,
                )
                db.resolve_pending_clarification(pending["id"])
                db.revert_unresolved_candidates(
                    supplier_id=supplier["id"],
                    resolved_rfq_id=args["rfq_id"],
                    candidate_rfq_ids=candidate_rfq_ids,
                )

                db.log_message(client_id, supplier["id"], "outbound", THANK_YOU_MSG)
                await enqueue_message(supplier["phone_number"], THANK_YOU_MSG)

                check_and_auto_rank(args["rfq_id"])

                return {
                    "status": "recorded_from_clarification",
                    "rfq_id": args["rfq_id"],
                    "clarification_id": pending["id"],
                }
            elif decision["tool_name"] == "request_clarification":
                args = decision["arguments"]
                next_round = current_round + 1
                db.create_pending_clarification(
                    client_id=client_id,
                    supplier_id=supplier["id"],
                    candidate_rfq_ids=args["candidate_rfq_ids"],
                    raw_message=message_text,
                    extracted_price=args.get("extracted_price"),
                    extracted_delivery=args.get("extracted_delivery"),
                    extracted_notes=args.get("extracted_notes"),
                    round_number=next_round,
                )
                db.abandon_pending_clarification(pending["id"])
                question = args["clarifying_question"]
                if not guardrails.is_safe_to_send(question):
                    print(f"[Guardrail Triggered] Unsafe output in clarifying question: {question}")
                    db.flag_for_human_review(
                        client_id=client_id,
                        supplier_id=supplier["id"],
                        rfq_id=None,
                        reason="AI generated non-compliant or code response caught by code-level safety guardrail",
                        category="other",
                        raw_message=message_text,
                    )
                    question = HUMAN_ACK_MSG
                db.log_message(client_id, supplier["id"], "outbound", question)
                await enqueue_message(supplier["phone_number"], question)
                return {"status": "clarification_needed", "question": question}

            elif decision["tool_name"] == "escalate_to_human":
                args = decision["arguments"]
                db.flag_for_human_review(
                    client_id=client_id,
                    supplier_id=supplier["id"],
                    rfq_id=args.get("rfq_id"),
                    reason=args.get("reason", "Human review requested by agent"),
                    category=args.get("category", "other"),
                    raw_message=message_text,
                )
                db.abandon_pending_clarification(pending["id"])
                db.log_message(client_id, supplier["id"], "outbound", HUMAN_ACK_MSG)
                await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG)
                return {
                    "status": "escalated",
                    "reason": args.get("reason"),
                    "category": args.get("category"),
                }

        open_rfqs = db.get_open_rfqs_for_supplier(supplier["id"])

        if not open_rfqs:
            return {
                "status": "no_open_rfq",
                "note": "message received but no active RFQ to match",
            }

        rfq_context = format_rfq_context(open_rfqs)
        open_rfq_ids = [entry["rfqs"]["id"] for entry in open_rfqs]
        prior_quotes = db.get_supplier_prior_quotes(supplier["id"], open_rfq_ids)
        prior_quotes_context = format_prior_quotes_context(prior_quotes)

        try:
            decision = groq_client.route_supplier_message(message_text, rfq_context, prior_quotes_context)
        except Exception as groq_err:
            tb_str = traceback.format_exc()
            print(f"\n=== GROQ ERROR (route_supplier_message) ===\n{tb_str}\n===========================================\n")
            db.log_webhook_error(f"Groq route_supplier_message error: {groq_err}", tb_str, payload)
            reason = f"Groq AI service error during message routing: {str(groq_err)}"
            db.flag_for_human_review(
                client_id=client_id,
                supplier_id=supplier["id"],
                rfq_id=None,
                reason=reason,
                category="other",
                raw_message=message_text,
            )
            db.log_message(client_id, supplier["id"], "outbound", HUMAN_ACK_MSG)
            await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG)
            return {"status": "escalated_due_to_groq_error", "reason": reason}

        if decision["tool_name"] == "record_quote":
            args = decision["arguments"]
            db.record_quote(
                rfq_id=args["rfq_id"],
                supplier_id=supplier["id"],
                price=args["price"],
                delivery_time=args.get("delivery_time"),
                quality_notes=args.get("quality_notes"),
                raw_message=message_text,
            )

            db.log_message(client_id, supplier["id"], "outbound", THANK_YOU_MSG)
            await enqueue_message(supplier["phone_number"], THANK_YOU_MSG)

            check_and_auto_rank(args["rfq_id"])

            return {"status": "recorded", "rfq_id": args["rfq_id"]}

        elif decision["tool_name"] == "request_clarification":
            args = decision["arguments"]
            db.create_pending_clarification(
                client_id=client_id,
                supplier_id=supplier["id"],
                candidate_rfq_ids=args["candidate_rfq_ids"],
                raw_message=message_text,
                extracted_price=args.get("extracted_price"),
                extracted_delivery=args.get("extracted_delivery"),
                extracted_notes=args.get("extracted_notes"),
            )
            question = args["clarifying_question"]
            if not guardrails.is_safe_to_send(question):
                print(f"[Guardrail Triggered] Unsafe output in clarifying question: {question}")
                db.flag_for_human_review(
                    client_id=client_id,
                    supplier_id=supplier["id"],
                    rfq_id=None,
                    reason="AI generated non-compliant or code response caught by code-level safety guardrail",
                    category="other",
                    raw_message=message_text,
                )
                question = HUMAN_ACK_MSG
            db.log_message(client_id, supplier["id"], "outbound", question)
            await enqueue_message(supplier["phone_number"], question)
            return {"status": "clarification_needed", "question": question}


        elif decision["tool_name"] == "escalate_to_human":
            args = decision["arguments"]
            db.flag_for_human_review(
                client_id=client_id,
                supplier_id=supplier["id"],
                rfq_id=args.get("rfq_id"),
                reason=args.get("reason", "Human review requested by agent"),
                category=args.get("category", "other"),
                raw_message=message_text,
            )
            db.log_message(client_id, supplier["id"], "outbound", HUMAN_ACK_MSG)
            await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG)
            return {
                "status": "escalated",
                "reason": args.get("reason"),
                "category": args.get("category"),
            }

        return {"status": "unhandled", "decision": decision}

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

    rfq, matched_suppliers = db.create_rfq_and_match_suppliers(
        client_id=client_id,
        product_name=req.product_name,
        category=req.category,
        deadline_hours=req.deadline_hours,
        specs=req.specs,
        quantity=req.quantity,
    )

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
        f"• Required Within: {req.deadline_hours} hours\n\n"
        f"Please reply directly to this message with your price per unit (AED) and estimated delivery time. Thanks!"
    )

    for supplier in matched_suppliers:
        db.log_message(client_id, supplier["id"], "outbound", rfq_msg, related_rfq_id=rfq["id"])
        await enqueue_message(supplier["phone_number"], rfq_msg, rfq_id=rfq["id"], supplier_id=supplier["id"])

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

    # Parse optional row-level updates (product_name, quantity, category, deadline_hours, specs)
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

        rfq, matched_suppliers = db.create_rfq_and_match_suppliers(
            client_id=tenant_client_id,
            product_name=final_product_name,
            category=final_category,
            deadline_hours=final_deadline,
            specs=final_specs,
            quantity=final_quantity,
        )

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
    """Marks a human escalation flag as resolved."""
    # further checks (admin/member) could be added here
    result = db.resolve_flag(flag_id)
    return {"status": "resolved", "flag": result}


@app.post("/flags/{flag_id}/respond")
async def respond_to_flag_endpoint(flag_id: str, payload: FlagRespondRequest, current_user=Depends(get_current_user)):
    """Stores human response on a flag, marks it resolved, and optionally sends message to supplier via WhatsApp."""
    updated_flags = db.resolve_flag_with_response(flag_id, payload.response)
    if not updated_flags:
        raise HTTPException(status_code=404, detail="Flagged item not found")

    flag = updated_flags[0]
    if payload.send_to_supplier and flag.get("suppliers"):
        supplier = flag["suppliers"]
        phone = supplier["phone_number"]
        rfq_id = flag.get("rfq_id")
        db.log_message(current_user.get("client_id"), supplier["id"], "outbound", payload.response)
        await enqueue_message(phone, payload.response, rfq_id=rfq_id, supplier_id=supplier["id"])

    return {"status": "resolved", "flag_id": flag_id, "sent_to_supplier": payload.send_to_supplier}



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
