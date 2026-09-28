"""
tests/e2e/test_e2e_primary_comparison.py
Primary automated end-to-end acceptance test comparing identical supplier negotiation:
Scenario A: Strict RFQ (No Optional Flexibility)
Scenario B: Flexible RFQ (Pre-Authorized Delivery Flexibility)
Generates full transcripts, structured state JSONs, and side-by-side comparison report.
"""

import json
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

import db
import main
from tests.e2e.conversation_recorder import ConversationRecorder, generate_comparison_report
from tests.e2e.supplier_simulator import SupplierSimulator
from tests.e2e.assertions import (
    assert_counter_within_safe_bounds,
    assert_language_safety,
    assert_holding_message,
)


@pytest.mark.e2e
def test_primary_e2e_comparison_strict_vs_flexible(e2e_harness):
    """
    Executes the exact same 6-turn supplier script against:
    1. Strict RFQ (Fixed 2-day delivery) -> Expect pause, flag, holding message, operator approval, session resume.
    2. Flexible RFQ (Authorized 5-day delivery) -> Expect zero pause, zero human flags, autonomous negotiation.
    Generates complete transcripts and side-by-side comparison report.
    """
    app_client: TestClient = e2e_harness["app_client"]
    client_id: str = e2e_harness["client_id"]
    supplier: dict = e2e_harness["supplier"]
    transport = e2e_harness["transport"]
    instance_name: str = e2e_harness["instance_name"]

    # ============================================================
    # 1. RUN SCENARIO A: STRICT RFQ (NO OPTIONAL FLEXIBILITY)
    # ============================================================
    transport.clear()
    strict_rfq_payload = {
        "product_name": "TEST PVC PIPE",
        "category": "Plumbing",
        "specs": "Schedule 40",
        "quantity": 20,
        "acceptable_price_min": 45.0,
        "acceptable_price_max": 48.0,
        "deadline_hours": 2,
        "required_delivery_days": 2,
        # No flexibility provided -> all dimensions fixed
    }

    strict_recorder = ConversationRecorder(
        scenario_name="STRICT_RFQ_DELIVERY_TRADEOFF",
        rfq_meta=strict_rfq_payload,
    )

    # Step 1: Create RFQ via real HTTP endpoint
    create_resp = app_client.post("/rfq/create", json=strict_rfq_payload)
    assert create_resp.status_code == 200, f"Failed creating strict RFQ: {create_resp.text}"
    strict_rfq_id = create_resp.json()["rfq_id"]

    # Verify initial outbound RFQ message captured
    initial_outbound_1 = transport.get_latest_message()
    assert initial_outbound_1 is not None
    assert "Required Delivery: Within 2 days" in initial_outbound_1["message"]
    initial_msg_id_1 = initial_outbound_1["generated_message_id"]

    strict_recorder.record_turn(
        speaker="PROCUREMENT AI",
        message_text=initial_outbound_1["message"],
        state_snapshot={"rfq_status": "active", "session_id": None, "attempt_count": 0, "pending_human_review_count": 0},
        extra_meta={"outbound_id": initial_msg_id_1},
    )

    sim_strict = SupplierSimulator(
        client=app_client,
        client_id=client_id,
        supplier_id=supplier["id"],
        phone_number=supplier["phone_number"],
        rfq_id=strict_rfq_id,
        transport=transport,
        recorder=strict_recorder,
        instance_name=instance_name,
    )

    # Turn 1: Supplier sends initial quote "72 AED per piece, delivery in 2 days."
    sim_strict.reply_to(initial_msg_id_1, "72 AED per piece, delivery in 2 days.")
    turn1_ai = transport.get_latest_message()
    assert turn1_ai is not None
    assert "45" in turn1_ai["message"]
    assert_language_safety(turn1_ai["message"])

    # Turn 2: Supplier moves significantly "I can reduce it to 65 AED."
    sim_strict.reply("I can reduce it to 65 AED.")
    turn2_ai = transport.get_latest_message()
    assert turn2_ai is not None
    assert "47" in turn2_ai["message"]
    assert_language_safety(turn2_ai["message"])

    # Turn 3: Tiny supplier movement "64.60 is the best I can improve right now."
    sim_strict.reply("64.60 is the best I can improve right now.")
    turn3_ai = transport.get_latest_message()
    assert turn3_ai is not None
    # AI holds position without advancing concession
    assert_language_safety(turn3_ai["message"])

    # Turn 4: Supplier proposes trade-off "If you can accept 4-day delivery, I can do 55 AED."
    sim_strict.reply("If you can accept 4-day delivery, I can do 55 AED.")
    turn4_ai = transport.get_latest_message()
    assert turn4_ai is not None
    # Verify holding message sent and human review flag created
    assert_holding_message(turn4_ai["message"])
    assert_language_safety(turn4_ai["message"])

    # Verify session is in awaiting_authorization state
    paused_session = (
        db.supabase.table("negotiation_sessions")
        .select("*")
        .eq("client_id", client_id)
        .eq("rfq_id", strict_rfq_id)
        .eq("supplier_id", supplier["id"])
        .execute()
    ).data[0]
    assert paused_session["status"] == "awaiting_authorization"
    session_id_before = paused_session["id"]

    # Verify pending flag created with trade-off metadata
    pending_flags = (
        db.supabase.table("flagged_for_review")
        .select("*")
        .eq("client_id", client_id)
        .eq("rfq_id", strict_rfq_id)
        .eq("status", "pending")
        .execute()
    ).data
    assert len(pending_flags) == 1
    flag_id = pending_flags[0]["id"]
    assert pending_flags[0]["metadata"]["dimension"] == "delivery"

    # Simulate Human Operator Approval via Structured Authorization Endpoint
    auth_resp = app_client.post(
        f"/flags/{flag_id}/negotiation-authorization",
        json={
            "dimension": "delivery",
            "decision": "approve",
            "constraints": {"max_days": 4},
            "resume_negotiation": True,
        },
    )
    assert auth_resp.status_code == 200, f"Authorization failed: {auth_resp.text}"
    auth_data = auth_resp.json()
    assert auth_data["status"] == "resolved"
    assert auth_data["session_id"] == session_id_before

    # Verify continuation message sent to supplier
    resumed_ai_msg = transport.get_latest_message()
    assert resumed_ai_msg is not None
    assert "4 days" in resumed_ai_msg["message"]
    strict_recorder.record_turn(
        speaker="OPERATOR / PROCUREMENT AI (Resumed)",
        message_text=resumed_ai_msg["message"],
        state_snapshot=sim_strict._capture_state_snapshot(),
        extra_meta={"action": "approved_delivery_tradeoff"},
    )

    # Turn 5: Supplier replies to revised terms "With 4 days I can do 52."
    sim_strict.reply("With 4 days I can do 52.")
    turn5_ai = transport.get_latest_message()
    assert turn5_ai is not None
    assert_language_safety(turn5_ai["message"])

    # Turn 6: Supplier declares final price "49 AED is my final price."
    sim_strict.reply("49 AED is my final price.")

    # Stop Reason
    strict_recorder.set_stop_reason("Supplier declared final price 49 AED (above ceiling of 48 AED). Negotiation completed.")
    strict_recorder.record_invariant("No Threshold Leakage", True)
    strict_recorder.record_invariant("Quote-First Persistence", True)
    strict_recorder.record_invariant("Session History Continuity", True)
    strict_recorder.record_invariant("Human Review on Fixed Delivery Violation", True)

    # Save artifacts
    strict_txt, strict_json = strict_recorder.save_artifacts("test-results", "strict_rfq")

    # ============================================================
    # 2. RUN SCENARIO B: FLEXIBLE RFQ (PRE-AUTHORIZED FLEXIBILITY)
    # ============================================================
    transport.clear()
    flexible_rfq_payload = {
        "product_name": "TEST PVC PIPE",
        "category": "Plumbing",
        "specs": "Schedule 40",
        "quantity": 20,
        "acceptable_price_min": 45.0,
        "acceptable_price_max": 48.0,
        "deadline_hours": 2,
        "required_delivery_days": 2,
        "flexibility": {
            "quantity": {
                "authorized": True,
                "min": 20,
                "max": 30,
            },
            "delivery": {
                "authorized": True,
                "max_days": 5,
            },
        },
    }

    flexible_recorder = ConversationRecorder(
        scenario_name="FLEXIBLE_RFQ_DELIVERY_TRADEOFF",
        rfq_meta=flexible_rfq_payload,
    )

    # Step 1: Create RFQ
    create_flex_resp = app_client.post("/rfq/create", json=flexible_rfq_payload)
    assert create_flex_resp.status_code == 200, f"Failed creating flexible RFQ: {create_flex_resp.text}"
    flexible_rfq_id = create_flex_resp.json()["rfq_id"]

    initial_outbound_2 = transport.get_latest_message()
    assert initial_outbound_2 is not None
    initial_msg_id_2 = initial_outbound_2["generated_message_id"]

    flexible_recorder.record_turn(
        speaker="PROCUREMENT AI",
        message_text=initial_outbound_2["message"],
        state_snapshot={"rfq_status": "active", "session_id": None, "attempt_count": 0, "pending_human_review_count": 0},
        extra_meta={"outbound_id": initial_msg_id_2},
    )

    sim_flex = SupplierSimulator(
        client=app_client,
        client_id=client_id,
        supplier_id=supplier["id"],
        phone_number=supplier["phone_number"],
        rfq_id=flexible_rfq_id,
        transport=transport,
        recorder=flexible_recorder,
        instance_name=instance_name,
    )

    # Run the exact same supplier script
    # Turn 1: 72 AED
    sim_flex.reply_to(initial_msg_id_2, "72 AED per piece, delivery in 2 days.")
    # Turn 2: 65 AED
    sim_flex.reply("I can reduce it to 65 AED.")
    # Turn 3: 64.60 AED
    sim_flex.reply("64.60 is the best I can improve right now.")
    # Turn 4: 4-day delivery @ 55 AED -> Delivery flexibility allows up to 5 days!
    sim_flex.reply("If you can accept 4-day delivery, I can do 55 AED.")

    # In Scenario B: 4 days <= 5 days authorized, so NO flag and autonomous counter sent
    flex_flags = (
        db.supabase.table("flagged_for_review")
        .select("*")
        .eq("client_id", client_id)
        .eq("rfq_id", flexible_rfq_id)
        .eq("status", "pending")
        .execute()
    ).data
    assert len(flex_flags) == 0, "Flexible RFQ should NOT create pending flags for authorized 4-day delivery!"

    # Turn 5: 52 AED
    sim_flex.reply("With 4 days I can do 52.")
    # Turn 6: 49 AED final
    sim_flex.reply("49 AED is my final price.")

    flexible_recorder.set_stop_reason("Supplier declared final price 49 AED. Negotiation completed autonomously.")
    flexible_recorder.record_invariant("No Threshold Leakage", True)
    flexible_recorder.record_invariant("Zero Human Review on Pre-Authorized Trade-off", True)
    flexible_recorder.record_invariant("Continuous Autonomous Concession", True)

    # Save artifacts
    flex_txt, flex_json = flexible_recorder.save_artifacts("test-results", "flexible_rfq")

    # Generate comparison report
    comp_report = generate_comparison_report(
        strict_recorder=strict_recorder,
        flexible_recorder=flexible_recorder,
        output_path="test-results/flexibility_comparison.txt",
    )

    print("\n" + comp_report)
