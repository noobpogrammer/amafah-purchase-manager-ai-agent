import openclaw_bridge
import main


def make_entry(rfq_id: str, status: str = "sent"):
    return {
        "rfq_id": rfq_id,
        "status": status,
        "rfqs": {
            "id": rfq_id,
            "product_name": rfq_id,
            "status": "active",
        },
    }


def test_fallback_routing_intent_distinguishes_new_quote_revision_and_negotiation():
    new_quote = openclaw_bridge._fallback_parse_commercial_message(
        "72 AED per piece, delivery in 2 days"
    )
    revision = openclaw_bridge._fallback_parse_commercial_message(
        "Revised price is 62 AED"
    )
    negotiation = openclaw_bridge._fallback_parse_commercial_message(
        "50 final"
    )
    unknown = openclaw_bridge._fallback_parse_commercial_message(
        "Let me check and get back to you"
    )

    assert new_quote.routing_intent == "NEW_QUOTE"
    assert revision.routing_intent == "REVISION"
    assert negotiation.routing_intent == "NEGOTIATION_REPLY"
    assert unknown.routing_intent == "UNKNOWN"
