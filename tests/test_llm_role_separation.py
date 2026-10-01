"""Focused tests for LLM responsibility separation.

These tests mock all model calls. They must not consume live Groq quota.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import groq_client
from groq_client import AgentContext


def _tool_response(name: str, arguments: dict):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name=name,
                                arguments=json.dumps(arguments),
                            )
                        )
                    ]
                )
            )
        ]
    )


def _text_response(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


def _context(*, prior_quotes=None, active_session=None):
    return AgentContext(
        client_id="client-1",
        supplier_id="supplier-1",
        matched_rfq_id="rfq-1",
        open_rfqs=[
            {
                "id": "rs-1",
                "status": "sent",
                "rfqs": {
                    "id": "rfq-1",
                    "product_name": "TEST SCENARIO 4 PIPE",
                    "status": "active",
                    "acceptable_price_min": 45,
                    "acceptable_price_max": 48,
                },
            }
        ],
        prior_quotes=prior_quotes or [],
        active_negotiation_session=active_session,
    )


def test_procurement_reasoner_is_gpt_oss_and_negotiate_tool_hidden_without_quote():
    ctx = _context()
    fake = _tool_response(
        "record_quote",
        {
            "rfq_id": "rfq-1",
            "variants": [
                {
                    "variant_label": None,
                    "price": 72,
                    "delivery_time": "2 days",
                    "quality_notes": None,
                    "is_available": True,
                }
            ],
        },
    )

    with patch.object(groq_client, "_groq_completion", return_value=fake) as completion:
        result = groq_client.reason_about_procurement_message(
            "72 AED per piece, delivery in 2 days.",
            ctx,
            input_origin="supplier",
        )

    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == groq_client.NEGOTIATION_MODEL
    assert kwargs["call_type"] == "procurement_reasoner"
    tool_names = {t["function"]["name"] for t in kwargs["tools"]}
    assert "record_quote" in tool_names
    assert "negotiate_price" not in tool_names
    assert result["tool_name"] == "record_quote"


def test_negotiate_tool_available_only_after_real_persisted_quote_exists():
    ctx = _context(
        prior_quotes=[
            {
                "id": "quote-real-1",
                "rfq_id": "rfq-1",
                "supplier_id": "supplier-1",
                "price": 72,
                "is_available": True,
            }
        ],
        active_session={"status": "active", "rfq_id": "rfq-1"},
    )
    fake = _tool_response(
        "negotiate_price",
        {
            "rfq_id": "rfq-1",
            "quote_id": "quote-real-1",
            "quoted_price": 72,
            "counter_price": 45,
            "negotiation_message": "Counter at AED 45.",
        },
    )

    with patch.object(groq_client, "_groq_completion", return_value=fake) as completion:
        result = groq_client.reason_about_procurement_message(
            "Can you improve the price?",
            ctx,
            input_origin="supplier",
        )

    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == groq_client.NEGOTIATION_MODEL
    assert kwargs["call_type"] == "negotiation_reasoner"
    tool_names = {t["function"]["name"] for t in kwargs["tools"]}
    assert "negotiate_price" in tool_names
    assert result["arguments"]["quote_id"] == "quote-real-1"


def test_operator_reasoning_always_uses_gpt_oss():
    ctx = _context()
    fake = _tool_response(
        "send_procurement_message",
        {"rfq_id": "rfq-1", "message": "We will confirm internally."},
    )

    with patch.object(groq_client, "_groq_completion", return_value=fake) as completion:
        groq_client.reason_about_procurement_message(
            "Tell them we will confirm internally.",
            ctx,
            input_origin="operator",
        )

    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == groq_client.NEGOTIATION_MODEL
    assert kwargs["call_type"] == "operator_reasoner"


def test_qwen_is_final_supplier_message_writer(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("ALLOW_LIVE_LLM_TESTS", raising=False)

    with patch.object(
        groq_client,
        "_groq_completion",
        return_value=_text_response("Thanks for the quote. Could you do AED 45 per piece?"),
    ) as completion:
        msg = groq_client.write_supplier_message(
            purpose="negotiation",
            approved_facts={
                "supplier_price": 72,
                "counter_price": 45,
                "strategy": "ANCHOR",
            },
            draft_message="Could you do AED 45 per piece?",
        )

    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == groq_client.UTILITY_MODEL
    assert kwargs["call_type"] == "supplier_message_writer"
    assert msg == "Thanks for the quote. Could you do AED 45 per piece?"


def test_qwen_writer_fails_closed_if_it_changes_numeric_terms(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("ALLOW_LIVE_LLM_TESTS", raising=False)

    approved = "Thanks for the quote. Could you do AED 45 per piece?"
    with patch.object(
        groq_client,
        "_groq_completion",
        return_value=_text_response("Thanks. Could you do AED 50 per piece?"),
    ):
        msg = groq_client.write_supplier_message(
            purpose="negotiation",
            approved_facts={
                "supplier_price": 72,
                "counter_price": 45,
                "strategy": "ANCHOR",
            },
            draft_message=approved,
        )

    assert msg == approved


def test_pytest_default_never_calls_live_writer(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_llm_role_separation.py::test_case (call)")
    monkeypatch.delenv("ALLOW_LIVE_LLM_TESTS", raising=False)

    with patch.object(groq_client, "_groq_completion") as completion:
        msg = groq_client.write_supplier_message(
            purpose="negotiation",
            approved_facts={"counter_price": 45},
            draft_message="Could you do AED 45?",
        )

    completion.assert_not_called()
    assert msg == "Could you do AED 45?"


def test_quote_absence_is_explicit_in_reasoning_context():
    prompt_context = groq_client.format_agent_context_for_prompt(_context())
    assert "CURRENT EFFECTIVE QUOTES: NONE" in prompt_context
    assert "negotiate_price is not available" in prompt_context
    assert "Never substitute an RFQ ID for a quote ID" in prompt_context


def test_policy_repair_reasoner_uses_validator_feedback_and_returns_corrective_tool():
    ctx = _context(
        prior_quotes=[
            {
                "id": "quote-old-47",
                "rfq_id": "rfq-1",
                "supplier_id": "supplier-1",
                "price": 47,
                "is_available": True,
            }
        ],
        active_session={"status": "active", "rfq_id": "rfq-1"},
    )
    fake = _tool_response(
        "record_quote",
        {
            "rfq_id": "rfq-1",
            "variants": [
                {
                    "variant_label": None,
                    "price": 46,
                    "delivery_time": "3 days",
                    "quality_notes": None,
                    "is_available": True,
                }
            ],
        },
    )

    with patch.object(groq_client, "_groq_completion", return_value=fake) as completion:
        result = groq_client.reason_with_validator_feedback(
            message_text="46 AED per piece, delivery in 3 days",
            context=ctx,
            rejected_tool_name="negotiate_price",
            rejected_arguments={
                "rfq_id": "rfq-1",
                "quote_id": "quote-old-47",
                "quoted_price": 46,
                "counter_price": 45,
            },
            validator_reason="Quoted price '46.0' does not match trusted effective quote price '47.0'.",
            input_origin="supplier",
        )

    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == groq_client.NEGOTIATION_MODEL
    assert kwargs["call_type"] == "policy_repair_reasoner"
    assert "Policy Validator is authoritative" in kwargs["messages"][1]["content"]
    assert "record that quote first with record_quote" in kwargs["messages"][1]["content"]
    assert result["tool_name"] == "record_quote"
    assert result["arguments"]["variants"][0]["price"] == 46
