"""
Amafha — WhatsApp RFQ agent webhook.
Evolution API posts incoming supplier messages here. This replaces the
old n8n branching logic with a single agent decision + tool execution.
"""

import os
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
import groq_client

scheduler = AsyncIOScheduler()

DEMO_CLIENT_ID = "d88c52ad-3d0b-42e9-86f1-b9f70018856b"
THANK_YOU_MSG = "Thanks for the quote! We'll be in touch if we move forward."

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")


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


def check_deadlines_and_reminders():
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
            send_whatsapp_message(phone, msg)
            db.mark_rfq_supplier_no_response(item["id"])
            check_and_auto_rank(rfq["id"])
        elif percentage >= 90 and reminder_count == 2:
            msg = f"Final reminder — closing the RFQ for '{prod}' soon! Please reply with your quote if available."
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", msg)
            send_whatsapp_message(phone, msg)
            db.update_rfq_supplier_reminder(item["id"], 3)
        elif percentage >= 70 and reminder_count == 1:
            msg = f"Reminder regarding RFQ for '{prod}'. Please send your quote when ready."
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", msg)
            send_whatsapp_message(phone, msg)
            db.update_rfq_supplier_reminder(item["id"], 2)
        elif percentage >= 50 and reminder_count == 0:
            msg = f"Hi! Just checking in on the RFQ for '{prod}'."
            db.log_message(DEMO_CLIENT_ID, supplier["id"], "outbound", msg)
            send_whatsapp_message(phone, msg)
            db.update_rfq_supplier_reminder(item["id"], 1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(check_deadlines_and_reminders, "interval", minutes=15)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


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
            abandon_msg = "Notice: Maximum clarification rounds reached. Flagging quote for manual follow-up."
            print(f"[Supplier {supplier['id']}] {abandon_msg}")
            return {"status": "abandoned", "reason": "max clarification rounds exceeded"}

        candidate_rfq_ids = pending.get("pending_rfq_ids", [])
        candidate_rfqs = db.get_rfqs_by_ids(candidate_rfq_ids)
        rfq_context = format_rfq_context(candidate_rfqs)

        decision = groq_client.resolve_clarification(
            message_text=message_text,
            candidate_rfqs_context=rfq_context,
            previous_message=pending.get("raw_message", "")
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
            send_whatsapp_message(supplier["phone_number"], THANK_YOU_MSG)

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
            send_whatsapp_message(supplier["phone_number"], question)
            return {"status": "clarification_needed", "question": question}

    open_rfqs = db.get_open_rfqs_for_supplier(supplier["id"])

    if not open_rfqs:
        return {
            "status": "no_open_rfq",
            "note": "message received but no active RFQ to match",
        }

    rfq_context = format_rfq_context(open_rfqs)
    decision = groq_client.route_supplier_message(message_text, rfq_context)

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
        send_whatsapp_message(supplier["phone_number"], THANK_YOU_MSG)

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
        send_whatsapp_message(supplier["phone_number"], question)
        return {"status": "clarification_needed", "question": question}

    return {"status": "unhandled", "decision": decision}


@app.post("/rfq/{rfq_id}/rank")
async def rank_rfq_endpoint(rfq_id: str):
    """Trigger the final comparison/ranking step for a closed or reviewable RFQ."""
    return generate_ranking(rfq_id)
