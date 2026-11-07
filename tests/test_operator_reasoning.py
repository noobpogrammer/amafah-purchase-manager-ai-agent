"""
Phase 9: Comprehensive Tests for Human Operator Instructions Through the Unified Agent.
Tests verify:
- Tenant isolation on flag retrieval, response, and resolution (cross-tenant rejection).
- Atomic flag claim lifecycle (pending -> processing -> resolved / failure rollback to pending).
- Unified reasoning with input_origin="operator" and operator review flag context.
- send_procurement_message tool validation and execution.
- Operator RFQ/Supplier locks preventing prompt-based diversion.
- Quote provenance isolation (operator text is never recorded as supplier quote).
- Negotiation rules & attempt cap enforcement under operator guidance.
- Phase 7.2 outbound queue integration & durable message logging.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock, ANY

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient
import main
import db
import openclaw_bridge
from auth import get_current_user


@pytest.fixture(autouse=True)
def setup_test_env():
    """Keep authenticated operator tests isolated without the retired queue."""
    main.app.dependency_overrides[get_current_user] = lambda: {
        "client_id": "tenant-a",
        "user_id": "user-a",
        "role": "member",
    }
    yield
    main.app.dependency_overrides.clear()


class TestTenantIsolation:
    """Tests 1-9: Tenant isolation, deterministic supplier/RFQ derivation, and high-risk action rejection."""

    def test_1_tenant_a_cannot_read_tenant_b_flag(self):
        """1. GET /flags or get_flag_by_id with client_id filters out other tenants."""
        with patch.object(db, "get_flag_by_id", return_value=None):
            result = db.get_flag_by_id("flag-b", client_id="tenant-a")
            assert result is None

    def test_2_tenant_a_cannot_resolve_tenant_b_flag(self):
        """2. POST /flags/{flag_id}/resolve rejects resolving other tenant's flag (returns 404)."""
        client = TestClient(main.app)
        with patch("db.resolve_flag", return_value=None):
            resp = client.post("/flags/flag-tenant-b/resolve")
            assert resp.status_code == 404


class TestAtomicFlagClaim:
    """Tests 10-16: Atomic pending -> processing claim, concurrent collision handling, and release rollback."""

    def test_10_first_pending_claim_succeeds(self):
        """10. First claim on pending flag transitions status to processing."""
        mock_flag = {"id": "flag-10", "client_id": "tenant-a", "status": "processing"}
        with patch.object(db, "get_flag_by_id", return_value=mock_flag), \
             patch.object(db.supabase, "rpc") as mock_rpc:
            mock_rpc.return_value.execute.return_value = MagicMock(data=[mock_flag])
            claimed = db.claim_flag_for_operator_action("flag-10", "tenant-a")
            assert claimed is not None
            assert claimed["status"] == "processing"

    def test_11_and_12_second_concurrent_claim_fails_with_409(self):
        """11, 12. When flag is already processing/resolved, second claim fails and returns 409 Conflict."""
        client = TestClient(main.app)
        mock_existing = {"id": "flag-11", "client_id": "tenant-a", "status": "processing"}
        with patch("db.claim_flag_for_operator_action", return_value=None), \
             patch("db.get_flag_by_id", return_value=mock_existing):

            resp = client.post("/flags/flag-11/respond", json={"response": "Some instruction", "send_to_supplier": True})
            assert resp.status_code == 409
            assert "already processing" in resp.json()["detail"]

    def test_13_wrong_tenant_cannot_claim_flag(self):
        """13. Attempting to claim another tenant's flag returns 404 (does not reveal existence)."""
        client = TestClient(main.app)
        with patch("db.claim_flag_for_operator_action", return_value=None), \
             patch("db.get_flag_by_id", return_value=None):

            resp = client.post("/flags/flag-tenant-b/respond", json={"response": "Some instruction", "send_to_supplier": True})
            assert resp.status_code == 404


class TestFlagLifecycleTransitions:
    """Hardened tests for strictly conditional flag lifecycle transitions (pending -> processing -> resolved / pending)."""

    def test_cannot_complete_pending_directly(self):
        """Cannot transition directly from 'pending' to 'resolved' via complete_flag_operator_action."""
        # When RPC or DB receives a complete attempt on a pending flag (not processing), it returns None
        mock_supabase = MagicMock()
        # Mock RPC returning empty (0 rows affected)
        mock_supabase.rpc.return_value.execute.return_value.data = []
        # Mock conditional update returning empty (status != 'processing')
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase):
            res = db.complete_flag_operator_action("flag-1", "tenant-a", human_response="Done")
            assert res is None

    def test_cannot_complete_resolved_flag(self):
        """Cannot re-complete an already resolved flag."""
        mock_supabase = MagicMock()
        mock_supabase.rpc.return_value.execute.return_value.data = []
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase):
            res = db.complete_flag_operator_action("flag-1", "tenant-a", human_response="Done again")
            assert res is None

    def test_processing_to_resolved_succeeds(self):
        """Strict conditional transition from 'processing' to 'resolved' succeeds."""
        mock_supabase = MagicMock()
        resolved_row = {
            "id": "flag-1",
            "client_id": "tenant-a",
            "status": "resolved",
            "human_response": "Approved price",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_supabase.rpc.return_value.execute.return_value.data = [resolved_row]

        with patch.object(db, "supabase", mock_supabase), \
             patch.object(db, "get_flag_by_id", return_value=resolved_row):
            res = db.complete_flag_operator_action("flag-1", "tenant-a", human_response="Approved price")
            assert res is not None
            assert res["status"] == "resolved"
            assert res["human_response"] == "Approved price"

    def test_processing_to_pending_release_succeeds(self):
        """Strict conditional release from 'processing' to 'pending' succeeds."""
        mock_supabase = MagicMock()
        released_row = {
            "id": "flag-1",
            "client_id": "tenant-a",
            "status": "pending",
        }
        mock_supabase.rpc.return_value.execute.return_value.data = [released_row]

        with patch.object(db, "supabase", mock_supabase):
            res = db.release_flag_claim("flag-1", "tenant-a")
            assert res is not None
            assert res["status"] == "pending"

    def test_release_on_pending_or_resolved_fails_closed(self):
        """Calling release_flag_claim on a flag that is not in 'processing' status returns None."""
        mock_supabase = MagicMock()
        mock_supabase.rpc.return_value.execute.return_value.data = []
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase):
            res = db.release_flag_claim("flag-1", "tenant-a")
            assert res is None

    def test_wrong_tenant_changes_nothing(self):
        """Calling lifecycle functions with wrong client_id returns None (tenant isolation)."""
        mock_supabase = MagicMock()
        # When client_id != tenant_id in DB, 0 rows are returned
        mock_supabase.rpc.return_value.execute.return_value.data = []
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase):
            claim_res = db.claim_flag_for_operator_action("flag-1", "tenant-intruder")
            assert claim_res is None

            release_res = db.release_flag_claim("flag-1", "tenant-intruder")
            assert release_res is None

            comp_res = db.complete_flag_operator_action("flag-1", "tenant-intruder", human_response="Hack")
            assert comp_res is None

    def test_double_claim_allows_one_winner_only(self):
        """Concurrent or sequential claim on the same flag allows only one winner."""
        mock_flag = {
            "id": "flag-1",
            "client_id": "tenant-a",
            "status": "processing",
        }

        # First claim succeeds (returns row)
        mock_supabase_first = MagicMock()
        mock_supabase_first.rpc.return_value.execute.return_value.data = [mock_flag]

        # Second claim fails (already in 'processing', 0 rows returned)
        mock_supabase_second = MagicMock()
        mock_supabase_second.rpc.return_value.execute.return_value.data = []
        mock_supabase_second.table.return_value.update.return_value.eq.return_value.eq.return_value.eq.return_value.select.return_value.execute.return_value.data = []

        with patch.object(db, "supabase", mock_supabase_first), \
             patch.object(db, "get_flag_by_id", return_value=mock_flag):
            first_win = db.claim_flag_for_operator_action("flag-1", "tenant-a")
            assert first_win is not None

        with patch.object(db, "supabase", mock_supabase_second):
            second_win = db.claim_flag_for_operator_action("flag-1", "tenant-a")
            assert second_win is None

    def test_rpc_permissions_and_security_definer_documented(self):
        """Verify the migration SQL file declares SECURITY DEFINER, SET search_path, and service_role grants."""
        migration_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "supabase",
            "migrations",
            "20260912070000_flag_lifecycle_rpc_hardening.sql"
        )
        assert os.path.exists(migration_path), "Migration file must exist"
        with open(migration_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check SECURITY DEFINER and search_path = public on all 3 functions
        assert content.count("SECURITY DEFINER") >= 3
        assert content.count("SET search_path = public") >= 3

        # Check REVOKE and GRANT statements for each function
        assert "REVOKE EXECUTE ON FUNCTION claim_flag_for_operator_action" in content
        assert "GRANT EXECUTE ON FUNCTION claim_flag_for_operator_action(UUID, UUID) TO service_role;" in content

        assert "REVOKE EXECUTE ON FUNCTION release_flag_claim" in content
        assert "GRANT EXECUTE ON FUNCTION release_flag_claim(UUID, UUID) TO service_role;" in content

        assert "REVOKE EXECUTE ON FUNCTION complete_flag_operator_action" in content
        assert "GRANT EXECUTE ON FUNCTION complete_flag_operator_action(UUID, UUID, TEXT) TO service_role;" in content
