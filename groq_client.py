"""
Thin wrapper around Groq's chat completions API with tool-calling.
No framework — just direct API calls, so behavior is fully transparent
and debuggable under deadline pressure.
"""

import os
from dotenv import load_dotenv
load_dotenv()
import json
from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])

MODEL = "openai/gpt-oss-120b"  # cheap/fast — good for classification + tool routing

# ------------------------------------------------------------
# Tool definitions (OpenAI-compatible schema, Groq supports this format)
# ------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "record_quote",
            "description": (
                "Record a supplier's quote for a specific RFQ. Use this when the "
                "supplier's message clearly gives a price (and optionally delivery "
                "time / notes) for a product you are confident matches one specific "
                "open RFQ."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rfq_id": {"type": "string", "description": "The matched RFQ's ID"},
                    "price": {"type": "number", "description": "Quoted price per piece"},
                    "delivery_time": {"type": ["string", "null"], "description": "Stated delivery time, if given"},
                    "quality_notes": {"type": ["string", "null"], "description": "Any warranty/quality notes mentioned"},
                },
                "required": ["rfq_id", "price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_clarification",
            "description": (
                "Use this when the supplier's reply is ambiguous — for example, "
                "the supplier has multiple open RFQs and it's unclear which product "
                "the reply refers to, or the message doesn't clearly state a price. "
                "This asks the supplier a clarifying follow-up question focused specifically on "
                "Price, Quality/Warranty, or Delivery Time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate_rfq_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "RFQ IDs this reply might be referring to",
                    },
                    "clarifying_question": {
                        "type": "string",
                        "description": "The question to send back to the supplier, focusing on price, quality/warranty, or delivery time",
                    },
                },
                "required": ["candidate_rfq_ids", "clarifying_question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_supplier_history",
            "description": "Look up a supplier's past quotes and reliability history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "supplier_id": {"type": "string"},
                },
                "required": ["supplier_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Escalate the conversation to a human procurement manager when the message requires "
                "client-specific business knowledge, when supplier intent remains ambiguous/unclear, "
                "or when the supplier gives contradictory information (e.g., quoting a different "
                "price or conflicting details than previously stated for the same RFQ)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "rfq_id": {
                        "type": ["string", "null"],
                        "description": "The matched RFQ ID if known, or candidate RFQ ID",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Detailed explanation of why human intervention is required",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "requires_business_knowledge",
                            "unclear_intent",
                            "contradictory_information",
                            "other",
                        ],
                        "description": "Category of human escalation",
                    },
                },
                "required": ["reason", "category"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are a procurement assistant for a hardware retail business.
You receive WhatsApp replies from suppliers who were sent RFQs (requests for quotes).

Clarifications should focus specifically on Price, Product Quality/Warranty, and Delivery Time — these are the main fields needed from suppliers. Specs and quantity are already fixed by the RFQ.

Your job: read the supplier's message plus the context of their currently open RFQ(s) and prior quotes, and decide the right action by calling exactly one tool:

1. Call record_quote: if the reply clearly and unambiguously gives a price for ONE specific open RFQ.
2. Call request_clarification: if the reply is ambiguous (e.g., multiple open RFQs and unclear which one, or missing key info like price/delivery).
3. Call escalate_to_human: DO NOT request clarification or guess. Call escalate_to_human when:
   - requires_business_knowledge: The message asks for custom credit terms, payment schedules, or business decisions only a human manager knows (e.g., "Can we pay 50% upfront via bank transfer?" or "Can we exchange goods after 30 days?").
   - unclear_intent: The message is gibberish, irrelevant, or intent cannot be safely determined even after reviewing context.
   - contradictory_information: The supplier gives a contradictory quote or term change vs a prior quote for the same RFQ.

CONTRADICTION & PRICE VARIANCE THRESHOLD RULES:
- Small price variance (<= 10%): Price differences of 10% or less from a prior quote for the same RFQ (e.g., previously quoted $50, now states $52 — a 4% change) are treated as minor rounding or currency adjustments — DO NOT escalate. Call record_quote with the new price if clear, or request_clarification if otherwise ambiguous.
- Large price variance (> 10%) or unexplainable term conflict: Price changes > 10% without explanation (e.g., previously quoted $50, now states $85 without explanation), or delivery/warranty terms that conflict with prior statements, are genuine contradictions — call escalate_to_human with category "contradictory_information".
- Explicitly explained changes: If the supplier explicitly explains a price increase or term change (e.g., "Price is now $85 due to raw material cost increase"), it is NOT a contradiction — call record_quote with the new price ($85).
"""


def route_supplier_message(message_text: str, open_rfqs_context: str, prior_quotes_context: str = "") -> dict:
    """
    Sends the supplier's message + their open RFQ context + prior quotes to Groq, gets back
    a tool call decision. Returns the tool name + parsed arguments.
    """
    user_content = f"Supplier's open RFQs:\n{open_rfqs_context}\n\n"
    if prior_quotes_context:
        user_content += f"Supplier's prior quotes for reference:\n{prior_quotes_context}\n\n"
    user_content += f"Supplier's WhatsApp message:\n{message_text}"

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        tools=TOOLS,
        tool_choice="required",  # force it to pick a tool, no free-text drift
    )

    tool_call = response.choices[0].message.tool_calls[0]
    return {
        "tool_name": tool_call.function.name,
        "arguments": json.loads(tool_call.function.arguments),
    }


def resolve_clarification(message_text: str, candidate_rfqs_context: str, previous_message: str, prior_quotes_context: str = "") -> dict:
    """
    Matches a supplier's follow-up reply against candidate RFQs to resolve a pending clarification.
    """
    prompt = f"Candidate RFQs context:\n{candidate_rfqs_context}\n\n"
    if prior_quotes_context:
        prompt += f"Supplier's prior quotes for reference:\n{prior_quotes_context}\n\n"
    prompt += (
        f"Previous supplier message / context:\n{previous_message}\n\n"
        f"Supplier's new follow-up message:\n{message_text}"
    )

    system_msg = (
        "You are a procurement assistant resolving an ambiguous supplier reply. "
        "Focus specifically on extracting Price, Quality/Warranty notes, and Delivery Time.\n\n"
        "1. Call record_quote if the message clarifies which candidate RFQ it refers to along with a price.\n"
        "2. Call request_clarification if essential details (like price) are still missing or ambiguous.\n"
        "3. Call escalate_to_human if the message requires client business knowledge (e.g. credit/payment terms), "
        "if intent is still unclear/gibberish, or if the supplier provides contradictory information vs prior quotes.\n\n"
        "CONTRADICTION & PRICE VARIANCE THRESHOLD RULES:\n"
        "- Small price variance (<= 10%): Price differences of 10% or less from a prior quote for the same RFQ (e.g., previously quoted $50, now states $52 — a 4% change) are treated as minor rounding or currency adjustments — DO NOT escalate; call record_quote with the new price if clear.\n"
        "- Large price variance (> 10%) or unexplainable term conflict: Price changes > 10% without explanation (e.g., previously quoted $50, now states $85 without explanation), or delivery/warranty terms that conflict with prior statements, are genuine contradictions — call escalate_to_human with category 'contradictory_information'.\n"
        "- Explicitly explained changes: If the supplier explicitly explains a price/term change (e.g., 'Price is now $85 due to raw material cost increase'), it is NOT a contradiction — call record_quote with the new price ($85)."
    )
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt},
        ],
        tools=TOOLS,
        tool_choice="required",
    )
    tool_call = response.choices[0].message.tool_calls[0]
    return {
        "tool_name": tool_call.function.name,
        "arguments": json.loads(tool_call.function.arguments),
    }


def rank_quotes(rfq_details: str, quotes_summary: str) -> dict:
    """
    Final comparison step: given all quotes for an RFQ, ask Groq to rank
    suppliers and explain the reasoning. Uses structured JSON output.
    """
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a procurement analyst. Given an RFQ and the quotes "
                    "received, rank suppliers best-to-worst considering price, "
                    "delivery time, and quality/warranty notes. Respond ONLY with "
                    "valid JSON: {\"best_supplier_id\": str, \"reasoning\": str, "
                    "\"ranking\": [{\"supplier_id\": str, \"rank\": int, \"summary\": str}]}"
                ),
            },
            {
                "role": "user",
                "content": f"RFQ:\n{rfq_details}\n\nQuotes received:\n{quotes_summary}",
            },
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)
