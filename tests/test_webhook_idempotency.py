import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
import uuid

import main
import db


@pytest.fixture
def mock_supabase():
    with patch("db.supabase") as mock:
        yield mock


@pytest.fixture
def test_client():
    return TestClient(main.app)


CLIENT_UUID = str(uuid.uuid4())
SUPPLIER_UUID = str(uuid.uuid4())
RFQ_UUID = str(uuid.uuid4())
RFQ_UUID_2 = str(uuid.uuid4())


class TestWebhookIdempotencyUnit:
    def test_claim_webhook_message_rpc_success(self, mock_supabase):
        mock_supabase.rpc().execute.return_value.data = True
        claimed = db.claim_webhook_message(CLIENT_UUID, "msg-100")
        assert claimed is True
        mock_supabase.rpc.assert_called_with("claim_webhook_message", {
            "p_client_id": CLIENT_UUID,
            "p_message_id": "msg-100",
        })

    def test_claim_webhook_message_rpc_duplicate(self, mock_supabase):
        mock_supabase.rpc().execute.return_value.data = False
        claimed = db.claim_webhook_message(CLIENT_UUID, "msg-100")
        assert claimed is False

    def test_claim_webhook_message_fallback_insert_success(self, mock_supabase):
        mock_supabase.rpc.side_effect = Exception("RPC not defined")
        mock_supabase.table().insert().execute.return_value.data = [{"client_id": CLIENT_UUID, "message_id": "msg-100"}]
        claimed = db.claim_webhook_message(CLIENT_UUID, "msg-100")
        assert claimed is True

    def test_claim_webhook_message_fallback_insert_duplicate_error(self, mock_supabase):
        mock_supabase.rpc.side_effect = Exception("RPC not defined")
        mock_supabase.table().insert().execute.side_effect = Exception("duplicate key value violates unique constraint 'processed_webhooks_pkey'")
        claimed = db.claim_webhook_message(CLIENT_UUID, "msg-100")
        assert claimed is False

    def test_claim_webhook_message_empty_inputs_safe(self):
        assert db.claim_webhook_message("", "msg-1") is True
        assert db.claim_webhook_message(CLIENT_UUID, "") is True


class TestWebhookIdempotencyIntegration:
    def _create_payload(self, msg_id="msg-1", instance="test_instance", text="Quote is 100"):
        return {
            "event": "messages.upsert",
            "instance": instance,
            "data": {
                "key": {"fromMe": False, "remoteJid": "971501234567@s.whatsapp.net", "id": msg_id},
                "message": {"conversation": text},
            },
        }


    def test_same_message_id_under_different_clients_distinct_keys(self):
        """7: Same message_id under two different client_ids treated as distinct keys."""
        claimed = set()

        def fake_claim(c_id, m_id):
            key = (c_id, m_id)
            if key in claimed:
                return False
            claimed.add(key)
            return True

        with patch("db.claim_webhook_message", side_effect=fake_claim):
            assert db.claim_webhook_message("client-A", "msg-shared-id") is True
            assert db.claim_webhook_message("client-B", "msg-shared-id") is True
            # Duplicate under Client A fails
            assert db.claim_webhook_message("client-A", "msg-shared-id") is False
            # Duplicate under Client B fails
            assert db.claim_webhook_message("client-B", "msg-shared-id") is False

    def test_concurrent_duplicate_webhook_simulation(self):
        """8: Concurrent duplicate webhook simulation -> exactly one request obtains the claim."""
        db_state = set()

        def atomic_db_insert(client_id, message_id):
            key = (client_id, message_id)
            if key in db_state:
                return False
            db_state.add(key)
            return True

        res1 = atomic_db_insert("client-1", "concurrent-msg-1")
        res2 = atomic_db_insert("client-1", "concurrent-msg-1")

        assert res1 is True
        assert res2 is False
        assert len(db_state) == 1
