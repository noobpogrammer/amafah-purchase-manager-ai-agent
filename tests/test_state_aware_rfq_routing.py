import groq_client
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
    new_quote = groq_client._fallback_parse_commercial_message(
        "72 AED per piece, delivery in 2 days"
    )
    revision = groq_client._fallback_parse_commercial_message(
        "Revised price is 62 AED"
    )
    negotiation = groq_client._fallback_parse_commercial_message(
        "50 final"
    )
    unknown = groq_client._fallback_parse_commercial_message(
        "Let me check and get back to you"
    )

    assert new_quote.routing_intent == "NEW_QUOTE"
    assert revision.routing_intent == "REVISION"
    assert negotiation.routing_intent == "NEGOTIATION_REPLY"
    assert unknown.routing_intent == "UNKNOWN"


def test_new_quote_routes_only_to_unanswered_rfq():
    quoted = make_entry("PVC PIPE", status="responded")
    unanswered = make_entry("COPPER PIPE", status="sent")

    result = main.select_state_aware_routing_candidates(
        routing_intent="NEW_QUOTE",
        all_open_rfqs=[quoted, unanswered],
        unanswered_rfqs=[unanswered],
        responded_rfqs=[quoted],
    )

    assert result["candidates"] == [unanswered]
    assert result["matched_rfq_id"] == "COPPER PIPE"
    assert result["match_source"] == "routing_new_quote"


def test_revision_routes_only_to_already_quoted_rfq():
    quoted = make_entry("PVC PIPE", status="responded")
    unanswered = make_entry("COPPER PIPE", status="sent")

    result = main.select_state_aware_routing_candidates(
        routing_intent="REVISION",
        all_open_rfqs=[quoted, unanswered],
        unanswered_rfqs=[unanswered],
        responded_rfqs=[quoted],
    )

    assert result["candidates"] == [quoted]
    assert result["matched_rfq_id"] == "PVC PIPE"
    assert result["match_source"] == "routing_revision"


def test_negotiation_reply_prefers_active_negotiation_session():
    negotiating = make_entry("PVC PIPE", status="responded")
    unanswered = make_entry("COPPER PIPE", status="sent")

    result = main.select_state_aware_routing_candidates(
        routing_intent="NEGOTIATION_REPLY",
        all_open_rfqs=[negotiating, unanswered],
        unanswered_rfqs=[unanswered],
        responded_rfqs=[negotiating],
        session_entry=negotiating,
        active_session={"rfq_id": "PVC PIPE", "status": "active"},
    )

    assert result["candidates"] == [negotiating]
    assert result["matched_rfq_id"] == "PVC PIPE"
    assert result["match_source"] == "negotiation_session"


def test_unknown_mixed_state_does_not_guess_single_unanswered_rfq():
    quoted = make_entry("PVC PIPE", status="responded")
    unanswered = make_entry("COPPER PIPE", status="sent")

    result = main.select_state_aware_routing_candidates(
        routing_intent="UNKNOWN",
        all_open_rfqs=[quoted, unanswered],
        unanswered_rfqs=[unanswered],
        responded_rfqs=[quoted],
    )

    assert result["candidates"] == [quoted, unanswered]
    assert result["matched_rfq_id"] is None
    assert result["match_source"] is None


def test_multiple_new_quote_candidates_stay_narrowed_to_unanswered_only():
    quoted = make_entry("PVC PIPE", status="responded")
    unanswered_a = make_entry("COPPER PIPE", status="sent")
    unanswered_b = make_entry("LED PANEL", status="sent")

    result = main.select_state_aware_routing_candidates(
        routing_intent="NEW_QUOTE",
        all_open_rfqs=[quoted, unanswered_a, unanswered_b],
        unanswered_rfqs=[unanswered_a, unanswered_b],
        responded_rfqs=[quoted],
    )

    assert result["candidates"] == [unanswered_a, unanswered_b]
    assert result["matched_rfq_id"] is None


def test_revision_conversation_context_can_break_tie_inside_quoted_pool():
    quoted_a = make_entry("PVC PIPE", status="responded")
    quoted_b = make_entry("STEEL PIPE", status="responded")
    unanswered = make_entry("COPPER PIPE", status="sent")

    result = main.select_state_aware_routing_candidates(
        routing_intent="REVISION",
        all_open_rfqs=[quoted_a, quoted_b, unanswered],
        unanswered_rfqs=[unanswered],
        responded_rfqs=[quoted_a, quoted_b],
        conversation_history=[
            {"direction": "outbound", "related_rfq_id": "STEEL PIPE", "body": "Can you improve the price?"}
        ],
    )

    assert result["candidates"] == [quoted_b]
    assert result["matched_rfq_id"] == "STEEL PIPE"
    assert result["match_source"] == "conversation_context"


def test_incompatible_classifier_state_falls_back_to_unknown_instead_of_forcing():
    quoted = make_entry("PVC PIPE", status="responded")

    result = main.select_state_aware_routing_candidates(
        routing_intent="NEW_QUOTE",
        all_open_rfqs=[quoted],
        unanswered_rfqs=[],
        responded_rfqs=[quoted],
    )

    assert result["routing_intent"] == "UNKNOWN"
    assert result["candidates"] == [quoted]
    assert result["matched_rfq_id"] == "PVC PIPE"


def test_terse_reply_follows_recent_active_negotiation_context():
    negotiating = make_entry("PVC PIPE", status="responded")
    unanswered = make_entry("COPPER PIPE", status="sent")

    result = main.select_state_aware_routing_candidates(
        routing_intent="NEW_QUOTE",
        all_open_rfqs=[negotiating, unanswered],
        unanswered_rfqs=[unanswered],
        responded_rfqs=[negotiating],
        session_entry=negotiating,
        active_session={"rfq_id": "PVC PIPE", "status": "active"},
        conversation_history=[
            {
                "direction": "outbound",
                "related_rfq_id": "PVC PIPE",
                "body": "We're still trying to improve the rate. Could you do AED 48 per piece?",
            }
        ],
        incoming_message_text="50",
    )

    assert result["candidates"] == [negotiating]
    assert result["matched_rfq_id"] == "PVC PIPE"
    assert result["match_source"] == "conversation_context"


def test_full_new_quote_does_not_get_stolen_by_old_negotiation_context():
    negotiating = make_entry("PVC PIPE", status="responded")
    unanswered = make_entry("COPPER PIPE", status="sent")

    result = main.select_state_aware_routing_candidates(
        routing_intent="NEW_QUOTE",
        all_open_rfqs=[negotiating, unanswered],
        unanswered_rfqs=[unanswered],
        responded_rfqs=[negotiating],
        session_entry=negotiating,
        active_session={"rfq_id": "PVC PIPE", "status": "active"},
        conversation_history=[
            {
                "direction": "outbound",
                "related_rfq_id": "PVC PIPE",
                "body": "We're still trying to improve the rate. Could you do AED 48 per piece?",
            }
        ],
        incoming_message_text="72 AED per piece, delivery in 2 days",
    )

    assert result["candidates"] == [unanswered]
    assert result["matched_rfq_id"] == "COPPER PIPE"
    assert result["match_source"] == "routing_new_quote"


def test_compact_currency_and_dozen_packaging_trigger_commercial_gate():
    entry = make_entry("NORA BRUSH", status="sent")

    assert main.should_invoke_commercial_parser(
        "we only provide in dozen packaging - 48aed per dozen",
        matched_rfq_supplier=None,
        pending=None,
        active_session=None,
        open_rfqs=[entry],
    ) is True


def test_compact_aed_format_is_procurement_signal():
    entry = make_entry("NORA BRUSH", status="sent")

    assert main.should_invoke_commercial_parser(
        "48aed",
        matched_rfq_supplier=None,
        pending=None,
        active_session=None,
        open_rfqs=[entry],
    ) is True
