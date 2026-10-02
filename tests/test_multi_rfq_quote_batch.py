import pytest
from unittest.mock import AsyncMock, patch

import main
from policy_validator import ValidationResult, ActionCategory


@pytest.mark.asyncio
async def test_multi_rfq_batch_runs_each_rfq_through_normal_executor():
    open_rfqs = [
        {
            "rfq_id": "rfq-a",
            "supplier_id": "supp-1",
            "status": "sent",
            "rfqs": {
                "id": "rfq-a",
                "client_id": "client-1",
                "status": "active",
                "product_name": "FLEXIBLE WIRE 6MMX5CORE",
                "last_quote": 1073,
            },
        },
        {
            "rfq_id": "rfq-b",
            "supplier_id": "supp-1",
            "status": "sent",
            "rfqs": {
                "id": "rfq-b",
                "client_id": "client-1",
                "status": "active",
                "product_name": "FLEXIBLE WIRE 6MMX3CORE",
                "last_quote": 550,
            },
        },
    ]
    items = [
        {
            "candidate_rfq_id": "rfq-a",
            "product_reference": "6mmx5c",
            "price": 1248,
            "variant_label": "EZFLEX",
            "quantity": 1,
            "supplier_final": False,
            "confidence": 0.98,
        },
        {
            "candidate_rfq_id": "rfq-b",
            "product_reference": "6mmx3c",
            "price": 743,
            "variant_label": "EZFLEX",
            "quantity": 2,
            "supplier_final": False,
            "confidence": 0.97,
        },
    ]

    def approve(proposal, **kwargs):
        variants = []
        for item in proposal.arguments["variants"]:
            variants.append({**item, "is_available": True})
        return ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args={"rfq_id": proposal.arguments["rfq_id"], "variants": variants},
        )

    with patch.object(main, "validate_action", side_effect=approve), \
         patch.object(main.db, "get_supplier_prior_quotes", return_value=[]), \
         patch.object(main.db, "get_negotiation_attempts", return_value=0), \
         patch.object(main.db, "get_active_negotiation_session", return_value=None), \
         patch.object(main.db, "record_agent_decision", return_value={"id": "decision-1"}), \
         patch.object(main, "execute_validated_action", new_callable=AsyncMock) as execute:
        execute.side_effect = [
            {"status": "negotiation_sent", "rfq_id": "rfq-a"},
            {"status": "negotiation_sent", "rfq_id": "rfq-b"},
        ]

        result = await main.process_multi_rfq_quote_batch(
            parsed_items=items,
            all_open_rfqs=open_rfqs,
            supplier={"id": "supp-1", "phone_number": "971500000000"},
            client_id="client-1",
            raw_message="Brand EZFLEX\n6mmx5c 1248/-\n6mmx3c 743/-",
            inbound_log_id="inbound-1",
        )

    assert result["status"] == "multi_rfq_quotes_processed"
    assert result["rfq_count"] == 2
    assert execute.await_count == 2

    first_call = execute.await_args_list[0]
    assert first_call.kwargs["quote_raw_override"].startswith("Brand EZFLEX")
    assert first_call.kwargs["suppress_acknowledgement"] is True


@pytest.mark.asyncio
async def test_multi_rfq_batch_refuses_uncertain_mapping():
    result = await main.process_multi_rfq_quote_batch(
        parsed_items=[
            {
                "candidate_rfq_id": "rfq-a",
                "price": 1248,
                "variant_label": "EZFLEX",
                "confidence": 0.98,
            },
            {
                "candidate_rfq_id": None,
                "price": 743,
                "variant_label": "EZFLEX",
                "confidence": 0.4,
            },
        ],
        all_open_rfqs=[],
        supplier={"id": "supp-1", "phone_number": "971500000000"},
        client_id="client-1",
        raw_message="ambiguous batch",
        inbound_log_id="inbound-1",
    )

    assert result is None
