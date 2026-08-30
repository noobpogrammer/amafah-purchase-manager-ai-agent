"""
Amafha — WhatsApp RFQ agent webhook.
Evolution API posts incoming supplier messages here. This replaces the
old n8n branching logic with a single agent decision + tool execution.
"""

from fastapi import FastAPI, Request
import db
import groq_client

app = FastAPI()

# For the demo: one client hardcoded. Once multi-tenant onboarding exists,
# this would be looked up from the Evolution API instance name in the webhook payload.
DEMO_CLIENT_ID = "d88c52ad-3d0b-42e9-86f1-b9f70018856b"


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


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    payload = await request.json()

    # Evolution API payload shape — adjust field paths to match your actual webhook format
    key_data = payload.get("data", {}).get("key", {})

    # Ignore outgoing messages sent by ourselves (e.g., our own outgoing RFQs)
    if key_data.get("fromMe", False):
        return {"status": "ignored", "reason": "outgoing message (fromMe)"}

    raw_remote_jid = key_data.get("remoteJid", "")
    sender_phone = normalize_phone(raw_remote_jid)
    message_text = payload.get("data", {}).get("message", {}).get("conversation", "")

    # Non-text message types (like audioMessage, imageMessage, etc.) have an empty conversation field
    # and are intentionally skipped since the agent doesn't process media/audio content yet.
    if not sender_phone or not message_text:
        return {"status": "ignored", "reason": "no message content"}

    supplier = db.get_supplier_by_phone(DEMO_CLIENT_ID, sender_phone)
    if not supplier:
        return {"status": "ignored", "reason": "unknown supplier"}

    db.log_message(DEMO_CLIENT_ID, supplier["id"], "inbound", message_text)

    open_rfqs = db.get_open_rfqs_for_supplier(supplier["id"])
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
        return {"status": "recorded", "rfq_id": args["rfq_id"]}

    elif decision["tool_name"] == "request_clarification":
        args = decision["arguments"]
        db.create_pending_clarification(
            client_id=DEMO_CLIENT_ID,
            supplier_id=supplier["id"],
            candidate_rfq_ids=args["candidate_rfq_ids"],
            raw_message=message_text,
        )
        # TODO: actually send args["clarifying_question"] back via Evolution API
        return {"status": "clarification_needed", "question": args["clarifying_question"]}

    return {"status": "unhandled", "decision": decision}


@app.post("/rfq/{rfq_id}/rank")
async def rank_rfq(rfq_id: str):
    """Trigger the final comparison/ranking step for a closed or reviewable RFQ."""
    quotes = db.get_quotes_for_rfq(rfq_id)
    quotes_summary = "\n".join(
        f"- {q['suppliers']['name']}: ${q['price']}, delivery: {q.get('delivery_time', '-')}, "
        f"notes: {q.get('quality_notes', '-')}"
        for q in quotes
    )
    result = groq_client.rank_quotes(rfq_details=f"RFQ ID: {rfq_id}", quotes_summary=quotes_summary)
    db.save_ranking(rfq_id, result["best_supplier_id"], result["reasoning"], result)
    return result
