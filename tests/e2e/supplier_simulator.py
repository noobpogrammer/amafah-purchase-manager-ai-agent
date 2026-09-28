"""
tests/e2e/supplier_simulator.py
Simulates realistic supplier interactions over the real WhatsApp webhook endpoint.
Supports both standalone messages and native WhatsApp replies with stanzaId correlation.
"""

import re
import uuid
from typing import Any, Dict, List, Optional
from fastapi.testclient import TestClient

import db
import groq_client
import main
from tests.e2e.conversation_recorder import ConversationRecorder
from tests.e2e.transport import TestWhatsAppTransport


class SupplierSimulator:
    """
    Simulates a realistic WhatsApp supplier communicating with the procurement AI.
    Posts authentic Evolution API webhook payloads to /webhook/whatsapp and synchronizes state with the ConversationRecorder.
    """

    def __init__(
        self,
        client: TestClient,
        client_id: str,
        supplier_id: str,
        phone_number: str,
        rfq_id: str,
        transport: TestWhatsAppTransport,
        recorder: ConversationRecorder,
        instance_name: str = "default_instance",
    ):
        self.client = client
        self.client_id = client_id
        self.supplier_id = supplier_id
        self.phone_number = phone_number
        self.rfq_id = rfq_id
        self.transport = transport
        self.recorder = recorder
        self.instance_name = instance_name

    def reply(self, text: str) -> Dict[str, Any]:
        """Sends a standalone WhatsApp message from the supplier into the real webhook endpoint."""
        msg_key_id = f"wamid-{uuid.uuid4()}"
        payload = {
            "event": "messages.upsert",
            "instance": self.instance_name,
            "data": {
                "key": {
                    "remoteJid": f"{self.phone_number}@s.whatsapp.net",
                    "fromMe": False,
                    "id": msg_key_id,
                },
                "message": {
                    "conversation": text,
                },
            },
        }

        # Record supplier turn
        self.recorder.record_turn(
            speaker="SUPPLIER",
            message_text=text,
            quoted_msg_id=None,
            state_snapshot=self._capture_state_snapshot(),
        )

        response = self.client.post("/webhook/whatsapp", json=payload)
        assert response.status_code == 200, f"Webhook rejected supplier message: {response.text}"

        # Capture AI outbound response if one was enqueued/sent
        latest_ai_msg = self.transport.get_latest_message()
        if latest_ai_msg and latest_ai_msg.get("phone") == self.phone_number:
            # Check if this AI message was generated in response to this turn
            last_recorded_ai = [t for t in self.recorder.turns if t["speaker"] == "PROCUREMENT AI"]
            if not last_recorded_ai or last_recorded_ai[-1]["message_text"] != latest_ai_msg["message"]:
                self.recorder.record_turn(
                    speaker="PROCUREMENT AI",
                    message_text=latest_ai_msg["message"],
                    quoted_msg_id=None,
                    state_snapshot=self._capture_state_snapshot(),
                    extra_meta={"outbound_id": latest_ai_msg.get("generated_message_id")},
                )

        return response.json()

    def reply_to(self, outbound_msg_id: str, text: str, quoted_text: Optional[str] = None) -> Dict[str, Any]:
        """Sends a native WhatsApp Reply referencing the outbound message's stanzaId."""
        msg_key_id = f"wamid-{uuid.uuid4()}"
        payload = {
            "event": "messages.upsert",
            "instance": self.instance_name,
            "data": {
                "key": {
                    "remoteJid": f"{self.phone_number}@s.whatsapp.net",
                    "fromMe": False,
                    "id": msg_key_id,
                },
                "message": {
                    "extendedTextMessage": {
                        "text": text,
                        "contextInfo": {
                            "stanzaId": outbound_msg_id,
                            "participant": "bot@s.whatsapp.net",
                            "quotedMessage": {
                                "conversation": quoted_text or "RFQ Request",
                            },
                        },
                    },
                },
            },
        }

        # Record supplier turn
        self.recorder.record_turn(
            speaker="SUPPLIER",
            message_text=text,
            quoted_msg_id=outbound_msg_id,
            state_snapshot=self._capture_state_snapshot(),
        )

        response = self.client.post("/webhook/whatsapp", json=payload)
        assert response.status_code == 200, f"Webhook rejected supplier quoted message: {response.text}"

        # Capture AI outbound response
        latest_ai_msg = self.transport.get_latest_message()
        if latest_ai_msg and latest_ai_msg.get("phone") == self.phone_number:
            last_recorded_ai = [t for t in self.recorder.turns if t["speaker"] == "PROCUREMENT AI"]
            if not last_recorded_ai or last_recorded_ai[-1]["message_text"] != latest_ai_msg["message"]:
                self.recorder.record_turn(
                    speaker="PROCUREMENT AI",
                    message_text=latest_ai_msg["message"],
                    quoted_msg_id=None,
                    state_snapshot=self._capture_state_snapshot(),
                    extra_meta={"outbound_id": latest_ai_msg.get("generated_message_id")},
                )

        return response.json()

    def _capture_state_snapshot(self) -> Dict[str, Any]:
        """Fetches the latest structured policy, session, quote, and flag state for the current RFQ."""
        snapshot = {}
        try:
            rfq = db.get_rfq_by_id(self.rfq_id)
            if rfq:
                snapshot["rfq_status"] = rfq.get("status")

            # Get session (active or awaiting_authorization or awaiting_human_review)
            sess = db.get_active_negotiation_session(self.client_id, self.rfq_id, self.supplier_id)
            if not sess:
                # Look up any recent session
                recent = (
                    db.supabase.table("negotiation_sessions")
                    .select("*")
                    .eq("client_id", self.client_id)
                    .eq("rfq_id", self.rfq_id)
                    .eq("supplier_id", self.supplier_id)
                    .order("updated_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if recent.data:
                    sess = recent.data[0]

            if sess:
                snapshot["session_id"] = sess.get("id")
                snapshot["session_status"] = sess.get("status")
                snapshot["attempt_count"] = sess.get("attempt_count")
                snapshot["initial_supplier_offer"] = sess.get("initial_supplier_offer")
                snapshot["previous_supplier_offer"] = sess.get("previous_supplier_offer")
                snapshot["latest_supplier_offer"] = sess.get("latest_supplier_offer")
                snapshot["latest_agent_counter"] = sess.get("latest_agent_counter")
                snapshot["supplier_last_concession"] = sess.get("supplier_last_concession")
                snapshot["supplier_total_concession"] = sess.get("supplier_total_concession")
                snapshot["selected_strategy"] = sess.get("strategy") or sess.get("selected_strategy")

            # Quotes
            quotes = db.get_quotes_for_rfq(self.rfq_id) or []
            supp_quotes = [q for q in quotes if q.get("supplier_id") == self.supplier_id]
            snapshot["quote_count"] = len(supp_quotes)
            if supp_quotes:
                snapshot["latest_quote"] = supp_quotes[-1].get("price")

            # Flags
            flags = (
                db.supabase.table("flagged_for_review")
                .select("*")
                .eq("client_id", self.client_id)
                .eq("rfq_id", self.rfq_id)
                .eq("status", "pending")
                .execute()
            )
            snapshot["pending_human_review_count"] = len(flags.data) if flags.data else 0

        except Exception as e:
            snapshot["capture_error"] = str(e)

        return snapshot


def deterministic_reasoner_extract(message_text: str, context: Any, input_origin: str = "supplier") -> dict:
    """
    Deterministic procurement reasoner used in CI E2E mode to reliably extract quotes,
    delivery terms, quantities, and clarification responses without external LLM network latency.
    """
    text = (message_text or "").strip()
    
    # 1. Match price pattern e.g. "72 AED", "AED 72", "72.50", "can do 55", "rate is 64.60", "49"
    # Matches numbers like 72, 65, 64.60, 55, 52, 49, etc.
    price = None
    price_match = re.search(r'(?:aed|rate|price|do|is|at|for)?\s*(\d+(?:\.\d+)?)\s*(?:aed|per|\/|$|\b)', text, re.IGNORECASE)
    if price_match:
        try:
            val = float(price_match.group(1))
            if val > 0:
                price = val
        except Exception:
            pass

    # Direct digit fallback if not matched
    if price is None:
        nums = re.findall(r'\b\d+(?:\.\d+)?\b', text)
        for n in nums:
            try:
                fn = float(n)
                # Avoid day count as price if "days" is next
                if fn > 0 and f"{n} day" not in text.lower() and f"{n}-day" not in text.lower():
                    price = fn
                    break
            except Exception:
                pass

    # 2. Match delivery days e.g. "2 days", "4-day delivery", "5 days"
    delivery_time = None
    deliv_match = re.search(r'(\d+)\s*-?\s*days?', text, re.IGNORECASE)
    if deliv_match:
        delivery_time = f"{deliv_match.group(1)} days"

    # 3. Match quantity if mentioned e.g. "for 30 pieces", "30 units"
    quantity = None
    qty_match = re.search(r'(\d+)\s*(?:pcs|pieces|units)', text, re.IGNORECASE)
    if qty_match:
        try:
            quantity = int(qty_match.group(1))
        except Exception:
            pass

    # Determine matched RFQ ID from context
    rfq_id = None
    if hasattr(context, "matched_rfq_id") and context.matched_rfq_id:
        rfq_id = context.matched_rfq_id
    elif hasattr(context, "open_rfqs") and context.open_rfqs:
        first_item = context.open_rfqs[0]
        if isinstance(first_item, dict):
            rfq_obj = first_item.get("rfqs", first_item)
            rfq_id = rfq_obj.get("id") if isinstance(rfq_obj, dict) else first_item.get("rfq_id")

    # If price found, record quote
    if price is not None:
        return {
            "tool_name": "record_quote",
            "arguments": {
                "rfq_id": rfq_id,
                "price": price,
                "delivery_time": delivery_time,
                "quantity": quantity,
                "quality_notes": None,
            },
            "reasoning": f"Extracted quote price {price} from message.",
        }

    # If no price found, check for clarification or escalation
    return {
        "tool_name": "request_clarification",
        "arguments": {
            "clarifying_question": "Could you please specify your price in AED for this order?",
            "ambiguous_topic": "price",
        },
        "reasoning": "No price detected in message.",
    }
