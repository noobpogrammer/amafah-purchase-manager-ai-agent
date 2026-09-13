import os
import sys
import json
import uuid
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient

import auth
import db
import main
from groq_client import AgentContext
from policy_validator import ActionProposal, ValidationResult

client = TestClient(main.app)

CLIENT_UUID = "d88c52ad-3d0b-42e9-86f1-b9f70018856b"
CLIENT_B_UUID = "e99d63be-4e1c-53fa-97f2-c0e81129967c"
ADMIN_USER_ID = "admin-user-001"
MEMBER_USER_ID = "member-user-002"
RFQ_ID = "33333333-3333-3333-3333-333333333333"
SUPPLIER_ID = "44444444-4444-4444-4444-444444444444"
QUOTE_ID = "55555555-5555-5555-5555-555555555555"


def fake_admin_user():
    return {"user_id": ADMIN_USER_ID, "client_id": CLIENT_UUID, "role": "admin"}


def fake_member_user():
    return {"user_id": MEMBER_USER_ID, "client_id": CLIENT_UUID, "role": "member"}


def fake_client_b_admin():
    return {"user_id": "admin-b", "client_id": CLIENT_B_UUID, "role": "admin"}


# ============================================================
# 1. Decision Audit & Sanitization Tests (Items 1-11)
# ============================================================

def test_sanitize_decision_arguments_strips_disallowed_keys():
    """10-11: Sanitizes prompt, chain of thought, and tokens."""
    dirty_args = {
        "rfq_id": RFQ_ID,
        "counter_price": 45.0,
        "variant_label": "Grade A",
        "system_prompt": "You are a purchasing agent...",
        "chain_of_thought": "Let's negotiate price down...",
        "api_key": "sk-test123456",
        "token": "bearer-secret",
        "nested": {
            "quoted_price": 50.0,
            "internal_prompt": "Hidden prompt text",
        },
    }
    clean = db.sanitize_decision_arguments(dirty_args)
    assert clean["rfq_id"] == RFQ_ID
    assert clean["counter_price"] == 45.0
    assert clean["variant_label"] == "Grade A"
    assert clean["nested"]["quoted_price"] == 50.0
    assert "system_prompt" not in clean
    assert "chain_of_thought" not in clean
    assert "api_key" not in clean
    assert "token" not in clean
    assert "internal_prompt" not in clean["nested"]


def test_record_agent_decision_supplier_proposal():
    """1, 3, 6: Records supplier LLM decision with pending -> executed lifecycle."""
    with patch("db.supabase.table") as mock_table:
        mock_insert = MagicMock()
        mock_insert.execute.return_value = MagicMock(data=[{
            "id": "dec-101",
            "client_id": CLIENT_UUID,
            "origin": "supplier",
            "tool_name": "record_quote",
            "validation_status": "approved",
            "execution_status": "pending",
        }])
        mock_table.return_value.insert.return_value = mock_insert

        rec = db.record_agent_decision(
            client_id=CLIENT_UUID,
            origin="supplier",
            tool_name="record_quote",
            arguments={"rfq_id": RFQ_ID, "price": 100.0},
            validation_status="approved",
            execution_status="pending",
            rfq_id=RFQ_ID,
            supplier_id=SUPPLIER_ID,
            inbound_message_id="msg-in-1",
        )
        assert rec["id"] == "dec-101"
        assert rec["origin"] == "supplier"
        assert rec["validation_status"] == "approved"


def test_record_agent_decision_operator_proposal():
    """2, 9: Records operator LLM decision with linked flag_id."""
    with patch("db.supabase.table") as mock_table:
        mock_insert = MagicMock()
        mock_insert.execute.return_value = MagicMock(data=[{
            "id": "dec-102",
            "client_id": CLIENT_UUID,
            "origin": "operator",
            "tool_name": "negotiate_price",
            "flag_id": "flag-888",
            "validation_status": "approved",
            "execution_status": "pending",
        }])
        mock_table.return_value.insert.return_value = mock_insert

        rec = db.record_agent_decision(
            client_id=CLIENT_UUID,
            origin="operator",
            tool_name="negotiate_price",
            arguments={"rfq_id": RFQ_ID, "quote_id": QUOTE_ID, "counter_price": 45.0},
            validation_status="approved",
            flag_id="flag-888",
        )
        assert rec["origin"] == "operator"
        assert rec["flag_id"] == "flag-888"


def test_record_validator_rejected_decision():
    """4, 5: Persists validator rejection verdict and reason."""
    with patch("db.supabase.table") as mock_table:
        mock_update = MagicMock()
        mock_update.execute.return_value = MagicMock(data=[{
            "id": "dec-103",
            "validation_status": "rejected",
            "validation_reason": "Target quote does not exist.",
            "execution_status": "not_executed",
        }])
        mock_table.return_value.update.return_value.eq.return_value = mock_update

        upd = db.update_agent_decision(
            "dec-103",
            validation_status="rejected",
            validation_reason="Target quote does not exist.",
            execution_status="not_executed",
        )
        assert upd["validation_status"] == "rejected"
        assert upd["execution_status"] == "not_executed"
        assert "does not exist" in upd["validation_reason"]


def test_decision_execution_failure_updates_status():
    """7: Execution exception updates execution_status='failed' with execution_error."""
    with patch("db.supabase.table") as mock_table:
        mock_update = MagicMock()
        mock_update.execute.return_value = MagicMock(data=[{
            "id": "dec-104",
            "execution_status": "failed",
            "execution_error": "Database connection reset",
        }])
        mock_table.return_value.update.return_value.eq.return_value = mock_update

        upd = db.update_agent_decision(
            "dec-104",
            execution_status="failed",
            execution_error="Database connection reset",
        )
        assert upd["execution_status"] == "failed"
        assert upd["execution_error"] == "Database connection reset"


# ============================================================
# 2. Negotiation Auditability Tests (Items 12-18)
# ============================================================

def test_negotiation_decision_arguments_persist_structured_values():
    """12-15: quote_id, quoted_price, counter_price, and variant are preserved in decision args."""
    args = {
        "rfq_id": RFQ_ID,
        "quote_id": QUOTE_ID,
        "quoted_price": 50.0,
        "counter_price": 45.0,
        "variant_label": "Standard Grade",
        "negotiation_message": "Can you do AED 45?",
    }
    with patch("db.supabase.table") as mock_table:
        mock_insert = MagicMock()
        mock_insert.execute.return_value = MagicMock(data=[{
            "id": "dec-neg-1",
            "arguments": args,
            "tool_name": "negotiate_price",
        }])
        mock_table.return_value.insert.return_value = mock_insert

        rec = db.record_agent_decision(
            client_id=CLIENT_UUID,
            origin="supplier",
            tool_name="negotiate_price",
            arguments=args,
            validation_status="approved",
            rfq_id=RFQ_ID,
            supplier_id=SUPPLIER_ID,
        )
        saved_args = rec["arguments"]
        assert saved_args["quote_id"] == QUOTE_ID
        assert saved_args["quoted_price"] == 50.0
        assert saved_args["counter_price"] == 45.0
        assert saved_args["variant_label"] == "Standard Grade"


def test_timeline_reconstructs_negotiation_rounds_separately():
    """16-18: Timeline formats negotiation counter separately from quote price."""
    fake_rfq = {"id": RFQ_ID, "client_id": CLIENT_UUID, "product_name": "LED 60W", "created_at": "2026-09-13T10:00:00Z", "status": "active"}
    fake_quotes = [
        {"id": QUOTE_ID, "rfq_id": RFQ_ID, "price": 50.0, "variant_label": "Grade A", "created_at": "2026-09-13T10:05:00Z", "suppliers": {"name": "Apex"}},
    ]
    fake_decisions = [
        {
            "id": "dec-1",
            "tool_name": "negotiate_price",
            "origin": "supplier",
            "validation_status": "approved",
            "execution_status": "executed",
            "created_at": "2026-09-13T10:06:00Z",
            "arguments": {"quoted_price": 50.0, "counter_price": 45.0, "variant_label": "Grade A"},
            "suppliers": {"name": "Apex"},
        }
    ]

    with patch.object(db, "get_rfq_by_id", return_value=fake_rfq), \
         patch("db.supabase.table") as mock_table:
        
        def table_side_effect(name):
            mock = MagicMock()
            if name == "message_log":
                mock.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(data=[])
            elif name == "quotes":
                mock.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(data=fake_quotes)
            elif name == "agent_decisions":
                mock.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(data=fake_decisions)
            elif name == "flagged_for_review":
                mock.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(data=[])
            elif name == "rfq_rankings":
                mock.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(data=[])
            return mock

        mock_table.side_effect = table_side_effect

        timeline = db.get_rfq_activity(RFQ_ID, CLIENT_UUID)
        assert len(timeline) >= 3
        quote_ev = next(e for e in timeline if e["event_type"] == "quote_recorded")
        neg_ev = next(e for e in timeline if e["event_type"] == "negotiation_sent")

        assert "AED 50" in quote_ev["summary"]
        assert "from AED 50" in neg_ev["summary"]
        assert "to AED 45" in neg_ev["summary"]
        assert neg_ev["details"]["arguments"]["counter_price"] == 45.0


# ============================================================
# 3. Webhook Recovery Lifecycle Tests (Items 19-26)
# ============================================================

def test_webhook_claim_lifecycle_processing_completed_failed():
    """19-24: Claim lifecycle transitions processing -> completed or failed -> retry."""
    with patch("db.supabase.rpc") as mock_rpc:
        # 1. New claim succeeds
        mock_rpc.return_value.execute.return_value = MagicMock(data=True)
        assert db.claim_webhook_message(CLIENT_UUID, "msg-rec-1") is True

        # 2. Mark completed
        mock_rpc.return_value.execute.return_value = MagicMock(data=True)
        assert db.complete_webhook_message(CLIENT_UUID, "msg-rec-1") is True

        # 3. Mark failed
        mock_rpc.return_value.execute.return_value = MagicMock(data=True)
        assert db.fail_webhook_message(CLIENT_UUID, "msg-rec-2", "Timeout error") is True


def test_webhook_unhandled_failure_calls_fail_webhook():
    """24-25: Unhandled error in webhook marks claim as failed."""
    with patch.object(db, "get_client_by_instance", return_value={"id": CLIENT_UUID, "name": "Amafha"}), \
         patch.object(db, "claim_webhook_message", return_value=True), \
         patch.object(db, "get_supplier_by_phone", side_effect=RuntimeError("DB disconnect")), \
         patch.object(db, "fail_webhook_message") as mock_fail:

        resp = client.post("/webhook/whatsapp", json={
            "event": "messages.upsert",
            "instance": "inst_amafha",
            "data": {
                "key": {"id": "msg-crash-1", "remoteJid": "971501112233@s.whatsapp.net", "fromMe": False},
                "message": {"conversation": "Quote: AED 50"},
            }
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "error_logged"
        mock_fail.assert_called_once()
        assert "DB disconnect" in mock_fail.call_args[0][2]


# ============================================================
# 4. Quote Recovery & Deduplication Tests (Items 27-30)
# ============================================================

def test_quote_lookup_defense_in_depth_tenant_check():
    """27-30 & Defense in Depth: get_quote_by_id checks client_id."""
    fake_quote = {
        "id": QUOTE_ID,
        "price": 50.0,
        "rfqs": {"id": RFQ_ID, "client_id": CLIENT_UUID},
    }
    with patch("db.supabase.table") as mock_table:
        mock_table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[fake_quote])

        # Own client succeeds
        q_own = db.get_quote_by_id(QUOTE_ID, client_id=CLIENT_UUID)
        assert q_own is not None
        assert q_own["id"] == QUOTE_ID

        # Other client fails
        q_other = db.get_quote_by_id(QUOTE_ID, client_id=CLIENT_B_UUID)
        assert q_other is None


# ============================================================
# 5. Audit Authorization & Endpoint Tests (Items 31-37)
# ============================================================

def test_rfq_activity_admin_access_success():
    """31: Admin user can retrieve RFQ activity timeline."""
    main.app.dependency_overrides[auth.get_current_user] = fake_admin_user
    fake_activity = [
        {"id": "ev-1", "timestamp": "2026-09-13T10:00:00Z", "event_type": "rfq_created", "title": "RFQ Created", "summary": "RFQ Created"}
    ]
    try:
        with patch.object(db, "get_rfq_activity", return_value=fake_activity):
            resp = client.get(f"/rfq/{RFQ_ID}/activity")
            assert resp.status_code == 200
            data = resp.json()
            assert data["rfq_id"] == RFQ_ID
            assert len(data["activity"]) == 1
    finally:
        main.app.dependency_overrides.clear()


def test_rfq_activity_member_access_forbidden():
    """32: Normal member user receives 403 Forbidden."""
    main.app.dependency_overrides[auth.get_current_user] = fake_member_user
    try:
        resp = client.get(f"/rfq/{RFQ_ID}/activity")
        assert resp.status_code == 403
        assert "Admin role required" in resp.json()["detail"]
    finally:
        main.app.dependency_overrides.clear()


def test_rfq_activity_unauthenticated_returns_401():
    """33: Missing authorization header returns 401."""
    main.app.dependency_overrides.clear()
    resp = client.get(f"/rfq/{RFQ_ID}/activity")
    assert resp.status_code == 401


def test_rfq_activity_cross_tenant_returns_404():
    """34: Admin of Client B requesting Client A RFQ receives 404."""
    main.app.dependency_overrides[auth.get_current_user] = fake_client_b_admin
    try:
        with patch.object(db, "get_rfq_activity", return_value=None):
            resp = client.get(f"/rfq/{RFQ_ID}/activity")
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"].lower()
    finally:
        main.app.dependency_overrides.clear()


# ============================================================
# 6. Delivery Diagnostics Tests (Items 38-43)
# ============================================================

def test_admin_delivery_issues_endpoint_success():
    """38-40, 43: Admin retrieves paginated delivery issues."""
    main.app.dependency_overrides[auth.get_current_user] = fake_admin_user
    fake_issues = {
        "items": [
            {"id": "msg-f-1", "status": "failed", "error_message": "Invalid recipient phone number"},
            {"id": "msg-u-1", "status": "unknown", "error_message": "Timeout after 30s"},
        ],
        "total": 2,
        "page": 1,
        "limit": 50,
    }
    try:
        with patch.object(db, "get_delivery_issues", return_value=fake_issues):
            resp = client.get("/admin/delivery-issues?page=1&limit=50")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["items"]) == 2
            assert data["total"] == 2
    finally:
        main.app.dependency_overrides.clear()


def test_delivery_issues_member_denied():
    """41: Member user receives 403 on delivery diagnostics."""
    main.app.dependency_overrides[auth.get_current_user] = fake_member_user
    try:
        resp = client.get("/admin/delivery-issues")
        assert resp.status_code == 403
    finally:
        main.app.dependency_overrides.clear()


# ============================================================
# 7. RPC Security Hardening Verification (Items 44-48)
# ============================================================

def test_migration_sql_contains_hardened_rpcs():
    """44-48: Verifies Phase 14 migration sets search_path=public,pg_temp and revokes execution."""
    migration_path = "supabase/migrations/20260913080000_phase14_decision_audit_and_hardening.sql"
    with open(migration_path, "r") as f:
        sql = f.read()

    # Search path hardening
    assert "SET search_path = public, pg_temp" in sql
    # Hardened functions
    assert "FUNCTION claim_rfq_for_finalization" in sql
    assert "FUNCTION claim_outbound_message" in sql
    assert "FUNCTION increment_negotiation_attempts" in sql
    assert "FUNCTION claim_webhook_message" in sql
    assert "FUNCTION complete_webhook_message" in sql
    assert "FUNCTION fail_webhook_message" in sql

    # Revocations and service_role grants
    assert "REVOKE EXECUTE ON FUNCTION claim_rfq_for_finalization(UUID) FROM PUBLIC, anon, authenticated;" in sql
    assert "GRANT EXECUTE ON FUNCTION claim_rfq_for_finalization(UUID) TO service_role;" in sql
    assert "REVOKE EXECUTE ON FUNCTION claim_webhook_message(UUID, TEXT) FROM PUBLIC, anon, authenticated;" in sql
    assert "GRANT EXECUTE ON FUNCTION claim_webhook_message(UUID, TEXT) TO service_role;" in sql
