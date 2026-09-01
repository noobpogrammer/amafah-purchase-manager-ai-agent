"""
Amafha — WhatsApp RFQ agent webhook.
Evolution API posts incoming supplier messages here. This replaces the
old n8n branching logic with a single agent decision + tool execution.
"""

import asyncio
import os
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from pydantic import BaseModel
import requests
from starlette.concurrency import run_in_threadpool

import db
import groq_client

scheduler = AsyncIOScheduler()

DEMO_CLIENT_ID = "d88c52ad-3d0b-42e9-86f1-b9f70018856b"
THANK_YOU_MSG = "Thanks for the quote! We'll be in touch if we move forward."
HUMAN_ACK_MSG = "Thanks! We'll review your response and get back to you shortly."

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")
EVOLUTION_TYPING_DELAY_MS = int(os.getenv("EVOLUTION_TYPING_DELAY_MS", "1200"))

OUTBOUND_MIN_DELAY = float(os.getenv("OUTBOUND_MIN_DELAY", "3.0"))
OUTBOUND_MAX_DELAY = float(os.getenv("OUTBOUND_MAX_DELAY", "8.0"))

outbound_queue: asyncio.Queue = asyncio.Queue()


class RFQCreateRequest(BaseModel):
    client_id: str = DEMO_CLIENT_ID
    product_name: str
    category: str
    specs: str | None = None
    quantity: int | None = None
    deadline_hours: int = 24


async def enqueue_message(phone_number: str, message: str):
    """Pushes an outbound message onto the asyncio queue for paced sending."""
    await outbound_queue.put((phone_number, message))


async def outbound_worker():
    """Background worker that processes outbound WhatsApp messages one by one with randomized delay."""
    while True:
        try:
            phone_number, message = await outbound_queue.get()
            try:
                await run_in_threadpool(send_whatsapp_message, phone_number, message)
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
        f"- {q['suppliers']['name']}: ${q['price']}, delivery: {q.get('delivery_time', '-')}, "
        f"notes: {q.get('quality_notes', '-')}"
        for q in quotes
    )
    result = groq_client.rank_quotes(rfq_details=f"RFQ ID: {rfq_id}", quotes_summary=quotes_summary)
    db.save_ranking(rfq_id, result["best_supplier_id"], result["reasoning"], result)
    return result


def check_and_auto_rank(rfq_id: str):
    """Triggers ranking automatically if all suppliers for this RFQ have responded or timed out."""
    if db.is_rfq_fully_processed(rfq_id) and not db.ranking_exists(rfq_id):
        print(f"Auto-triggering quote ranking for completed RFQ: {rfq_id}")
        generate_ranking(rfq_id)


async def check_deadlines_and_reminders():
    """Background scheduled job for 50%, 70%, 90% reminders & deadline expiration."""
    items = db.get_active_rfq_suppliers_with_deadlines()
    now = datetime.now(timezone.utc)
    for item in items:
        rfq = item["rfqs"]
        supplier = item["suppliers"]
        sent_at_str = item.get("sent_at") or rfq.get("created_at")
        if not sent_at_str:
            continue
        sent_at = datetime.fromisoformat(sent_at_str.replace("Z", "+00:00"))
        deadline_hours = rfq.get("deadline_hours") or 24
        total_seconds = deadline_hours * 3600
        elapsed_seconds = (now - sent_at).total_seconds()
        if total_seconds <= 0:
            continue
        percentage = (elapsed_seconds / total_seconds) * 100
        reminder_count = item.get("reminder_count", 0)
        phone = supplier["phone_number"]
        prod = rfq["product_name"]

        if percentage >= 100:
            msg = f"RFQ for '{prod}' is now closed as the deadline has passed. Thank you!"
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", msg)
            await enqueue_message(phone, msg)
            db.mark_rfq_supplier_no_response(item["id"])
            check_and_auto_rank(rfq["id"])
        elif percentage >= 90 and reminder_count == 2:
            msg = f"Final reminder — closing the RFQ for '{prod}' soon! Please reply with your quote if available."
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", msg)
            await enqueue_message(phone, msg)
            db.update_rfq_supplier_reminder(item["id"], 3)
        elif percentage >= 70 and reminder_count == 1:
            msg = f"Reminder regarding RFQ for '{prod}'. Please send your quote when ready."
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", msg)
            await enqueue_message(phone, msg)
            db.update_rfq_supplier_reminder(item["id"], 2)
        elif percentage >= 50 and reminder_count == 0:
            msg = f"Hi! Just checking in on the RFQ for '{prod}'."
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", msg)
            await enqueue_message(phone, msg)
            db.update_rfq_supplier_reminder(item["id"], 1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(outbound_worker())
    scheduler.add_job(check_deadlines_and_reminders, "interval", minutes=15)
    scheduler.start()
    yield
    scheduler.shutdown()
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    payload = await request.json()

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

    raw_remote_jid = key_data.get("remoteJid", "")
    sender_phone = normalize_phone(raw_remote_jid)
    message_text = data.get("message", {}).get("conversation", "")

    # Non-text message types (like audioMessage, imageMessage, etc.) have an empty conversation field
    # and are intentionally skipped since the agent doesn't process media/audio content yet.
    if not sender_phone or not message_text:
        return {"status": "ignored", "reason": "no message content"}

    supplier = db.get_supplier_by_phone(DEMO_CLIENT_ID, sender_phone)
    if not supplier:
        return {"status": "ignored", "reason": "unknown supplier"}

    db.log_message(DEMO_CLIENT_ID, supplier["id"], "inbound", message_text)

    # Check for unresolved pending clarification (status = 'awaiting_reply') for this supplier
    pending = db.get_pending_clarification_for_supplier(supplier["id"])
    if pending:
        # Check clarification rounds cap (max 2 rounds allowed per supplier-pair)
        rounds_count = db.count_clarification_rounds(supplier["id"], DEMO_CLIENT_ID)
        if rounds_count >= 2:
            db.abandon_pending_clarification(pending["id"])
            reason = "Maximum clarification rounds (2) exceeded for supplier."
            db.flag_for_human_review(
                client_id=DEMO_CLIENT_ID,
                supplier_id=supplier["id"],
                rfq_id=None,
                reason=reason,
                category="unclear_intent",
                raw_message=message_text,
            )
            ack_msg = "Thanks! We will have a team member follow up with you directly."
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", ack_msg)
            await enqueue_message(supplier["phone_number"], ack_msg)
            return {"status": "escalated", "reason": reason, "category": "unclear_intent"}

        candidate_rfq_ids = pending.get("pending_rfq_ids", [])
        candidate_rfqs = db.get_rfqs_by_ids(candidate_rfq_ids)
        rfq_context = format_rfq_context(candidate_rfqs)

        # Fetch prior quotes context for contradiction detection
        prior_quotes = db.get_supplier_prior_quotes(supplier["id"], candidate_rfq_ids)
        prior_quotes_context = format_prior_quotes_context(prior_quotes)

        decision = groq_client.resolve_clarification(
            message_text=message_text,
            candidate_rfqs_context=rfq_context,
            previous_message=pending.get("raw_message", ""),
            prior_quotes_context=prior_quotes_context,
        )

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

            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", THANK_YOU_MSG)
            await enqueue_message(supplier["phone_number"], THANK_YOU_MSG)

            check_and_auto_rank(args["rfq_id"])

            return {
                "status": "recorded_from_clarification",
                "rfq_id": args["rfq_id"],
                "clarification_id": pending["id"],
            }
        elif decision["tool_name"] == "request_clarification":
            args = decision["arguments"]
            db.create_pending_clarification(
                client_id=DEMO_CLIENT_ID,
                supplier_id=supplier["id"],
                candidate_rfq_ids=args["candidate_rfq_ids"],
                raw_message=message_text,
            )
            db.abandon_pending_clarification(pending["id"])
            question = args["clarifying_question"]
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", question)
            await enqueue_message(supplier["phone_number"], question)
            return {"status": "clarification_needed", "question": question}

        elif decision["tool_name"] == "escalate_to_human":
            args = decision["arguments"]
            db.flag_for_human_review(
                client_id=DEMO_CLIENT_ID,
                supplier_id=supplier["id"],
                rfq_id=args.get("rfq_id"),
                reason=args.get("reason", "Human review requested by agent"),
                category=args.get("category", "other"),
                raw_message=message_text,
            )
            db.abandon_pending_clarification(pending["id"])
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", HUMAN_ACK_MSG)
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

    decision = groq_client.route_supplier_message(message_text, rfq_context, prior_quotes_context)

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

        db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", THANK_YOU_MSG)
        await enqueue_message(supplier["phone_number"], THANK_YOU_MSG)

        check_and_auto_rank(args["rfq_id"])

        return {"status": "recorded", "rfq_id": args["rfq_id"]}

    elif decision["tool_name"] == "request_clarification":
        args = decision["arguments"]
        db.create_pending_clarification(
            client_id=DEMO_CLIENT_ID,
            supplier_id=supplier["id"],
            candidate_rfq_ids=args["candidate_rfq_ids"],
            raw_message=message_text,
        )
        question = args["clarifying_question"]
        db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", question)
        await enqueue_message(supplier["phone_number"], question)
        return {"status": "clarification_needed", "question": question}

    elif decision["tool_name"] == "escalate_to_human":
        args = decision["arguments"]
        db.flag_for_human_review(
            client_id=DEMO_CLIENT_ID,
            supplier_id=supplier["id"],
            rfq_id=args.get("rfq_id"),
            reason=args.get("reason", "Human review requested by agent"),
            category=args.get("category", "other"),
            raw_message=message_text,
        )
        db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", HUMAN_ACK_MSG)
        await enqueue_message(supplier["phone_number"], HUMAN_ACK_MSG)
        return {
            "status": "escalated",
            "reason": args.get("reason"),
            "category": args.get("category"),
        }

    return {"status": "unhandled", "decision": decision}


@app.post("/rfq/create")
async def create_rfq_endpoint(req: RFQCreateRequest):
    """Creates a new RFQ, matches active suppliers by category, and enqueues initial WhatsApp RFQs."""
    rfq, matched_suppliers = db.create_rfq_and_match_suppliers(
        client_id=req.client_id,
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
        f"Hello! New Request for Quote:\n"
        f"- Product: {req.product_name}\n"
        f"- Specs: {req.specs or 'Standard'}\n"
        f"- Quantity: {req.quantity or 'N/A'}\n"
        f"- Deadline: {req.deadline_hours} hours\n\n"
        f"Please reply with your price per unit and estimated delivery time."
    )

    for supplier in matched_suppliers:
        db.log_message(req.client_id, supplier["id"], "outbound", rfq_msg, related_rfq_id=rfq["id"])
        await enqueue_message(supplier["phone_number"], rfq_msg)

    return {
        "status": "success",
        "rfq_id": rfq["id"],
        "matched_suppliers_count": len(matched_suppliers),
        "suppliers": [{"id": s["id"], "name": s["name"], "phone": s["phone_number"]} for s in matched_suppliers],
    }


@app.get("/flags")
async def get_flags_endpoint(client_id: str = DEMO_CLIENT_ID):
    """Lists pending human review escalations for a client."""
    return db.get_pending_flags(client_id)


@app.post("/rfq/{rfq_id}/rank")
async def rank_rfq_endpoint(rfq_id: str):
    """Trigger the final comparison/ranking step for a closed or reviewable RFQ."""
    return generate_ranking(rfq_id)
