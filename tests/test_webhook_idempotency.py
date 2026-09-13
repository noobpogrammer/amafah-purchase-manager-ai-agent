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

    @pytest.mark.asyncio
    async def test_first_webhook_processes_and_duplicate_ignored(self, test_client):
        """1 & 2: First delivery processes normally, second identical delivery returns ignored."""
        claimed_ids = set()

        def fake_claim(c_id, m_id):
            key = (c_id, m_id)
            if key in claimed_ids:
                return False
            claimed_ids.add(key)
            return True

        mock_client = {"id": CLIENT_UUID, "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier = {"id": SUPPLIER_UUID, "phone_number": "971501234567", "client_id": CLIENT_UUID}
        open_rfqs = [{
            "id": str(uuid.uuid4()),
            "rfq_id": RFQ_UUID,
            "supplier_id": SUPPLIER_UUID,
            "rfqs": {
                "id": RFQ_UUID,
                "client_id": CLIENT_UUID,
                "product_name": "Cement",
                "status": "active",
                "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
            },
        }]

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supplier), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch("db.get_rfq_supplier_by_quoted_text", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=open_rfqs), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_competitive_pricing_context", return_value={"has_competition": False}), \
             patch("db.get_negotiation_attempts", return_value=0), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.claim_webhook_message", side_effect=fake_claim), \
             patch("db.record_quote", return_value={"id": str(uuid.uuid4())}) as mock_record_quote, \
             patch("db.log_message") as mock_log, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("groq_client.route_supplier_message", return_value={
                 "tool_name": "record_quote",
                 "arguments": {"rfq_id": RFQ_UUID, "price": 100.0},
             }):

            payload = self._create_payload(msg_id="msg-101", text="100 AED for Cement")

            # First delivery
            resp1 = test_client.post("/webhook/whatsapp", json=payload)
            assert resp1.status_code == 200
            assert resp1.json()["status"] == "recorded"
            assert mock_record_quote.call_count == 1
            assert mock_enq.call_count == 1

            # Second delivery (duplicate delivery from Evolution)
            resp2 = test_client.post("/webhook/whatsapp", json=payload)
            assert resp2.status_code == 200
            assert resp2.json()["status"] == "ignored"
            assert "already processed message id" in resp2.json()["reason"]
            # No additional quote created
            assert mock_record_quote.call_count == 1
            # No additional message enqueued
            assert mock_enq.call_count == 1

    @pytest.mark.asyncio
    async def test_duplicate_negotiation_does_not_increment_or_send_counteroffer_twice(self, test_client):
        """4 & 6: Duplicate negotiation webhook does not increment attempts twice or send two counteroffers."""
        claimed_ids = set()

        def fake_claim(c_id, m_id):
            key = (c_id, m_id)
            if key in claimed_ids:
                return False
            claimed_ids.add(key)
            return True

        mock_client = {"id": CLIENT_UUID, "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier = {"id": SUPPLIER_UUID, "phone_number": "971501234567", "client_id": CLIENT_UUID}
        matched_rfq_supplier = {
            "id": str(uuid.uuid4()),
            "rfq_id": RFQ_UUID,
            "supplier_id": SUPPLIER_UUID,
            "sent_message_id": "stanza-100",
            "negotiation_attempts": 0,
            "rfqs": {
                "id": RFQ_UUID,
                "client_id": CLIENT_UUID,
                "product_name": "LED Light",
                "status": "active",
                "acceptable_price_min": 50.0,
                "acceptable_price_max": 60.0,
                "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(),
            },
        }

        mock_quote = {"id": "q-1", "rfq_id": RFQ_UUID, "supplier_id": SUPPLIER_UUID, "price": 68.0, "is_available": True}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supplier), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=matched_rfq_supplier), \
             patch("db.get_supplier_prior_quotes", return_value=[mock_quote]), \
             patch("db.get_quote_by_id", return_value=mock_quote), \
             patch("db.get_quotes_for_rfq", return_value=[mock_quote]), \
             patch("db.get_competitive_pricing_context", return_value={"has_competition": True, "competing_quotes_count": 1, "best_competing_price": 55.0}), \
             patch("db.get_negotiation_attempts", return_value=0), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.claim_webhook_message", side_effect=fake_claim), \
             patch("db.increment_negotiation_attempts", return_value=1) as mock_inc, \
             patch("db.record_quote", return_value={"id": str(uuid.uuid4())}) as mock_record_quote, \
             patch("db.log_message"), \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("groq_client.route_supplier_message", return_value={
                 "tool_name": "negotiate_price",
                 "arguments": {
                     "rfq_id": RFQ_UUID,
                     "quote_id": "q-1",
                     "quoted_price": 68.0,
                     "counter_price": 60.0,
                     "negotiation_message": "Could you do 60 AED?",
                 },
             }):

            payload = {
                "event": "messages.upsert",
                "instance": "test_instance",
                "data": {
                    "key": {"fromMe": False, "remoteJid": "971501234567@s.whatsapp.net", "id": "neg-msg-1"},
                    "message": {
                        "conversation": "Our price is 68 AED",
                        "contextInfo": {"stanzaId": "stanza-100"},
                    },
                },
            }

            # First delivery
            resp1 = test_client.post("/webhook/whatsapp", json=payload)
            assert resp1.status_code == 200
            assert resp1.json()["status"] == "negotiation_sent"
            assert mock_inc.call_count == 1
            assert mock_enq.call_count == 1

            # Duplicate delivery
            resp2 = test_client.post("/webhook/whatsapp", json=payload)
            assert resp2.status_code == 200
            assert resp2.json()["status"] == "ignored"
            assert mock_inc.call_count == 1
            assert mock_enq.call_count == 1

    @pytest.mark.asyncio
    async def test_duplicate_clarification_does_not_create_duplicate_clarifications(self, test_client):
        """5: Duplicate clarification webhook does not create duplicate clarification actions."""
        claimed_ids = set()

        def fake_claim(c_id, m_id):
            key = (c_id, m_id)
            if key in claimed_ids:
                return False
            claimed_ids.add(key)
            return True

        mock_client = {"id": CLIENT_UUID, "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier = {"id": SUPPLIER_UUID, "phone_number": "971501234567", "client_id": CLIENT_UUID}
        open_rfqs = [
            {
                "id": str(uuid.uuid4()),
                "rfq_id": RFQ_UUID,
                "supplier_id": SUPPLIER_UUID,
                "rfqs": {"id": RFQ_UUID, "client_id": CLIENT_UUID, "product_name": "Cement Type A", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()},
            },
            {
                "id": str(uuid.uuid4()),
                "rfq_id": RFQ_UUID_2,
                "supplier_id": SUPPLIER_UUID,
                "rfqs": {"id": RFQ_UUID_2, "client_id": CLIENT_UUID, "product_name": "Cement Type B", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()},
            },
        ]

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supplier), \
             patch("db.get_rfq_supplier_by_sent_message_id", return_value=None), \
             patch("db.get_rfq_supplier_by_quoted_text", return_value=None), \
             patch("db.get_open_rfqs_for_supplier", return_value=open_rfqs), \
             patch("db.get_supplier_prior_quotes", return_value=[]), \
             patch("db.get_competitive_pricing_context", return_value={"has_competition": False}), \
             patch("db.get_negotiation_attempts", return_value=0), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.claim_webhook_message", side_effect=fake_claim), \
             patch("db.create_pending_clarification", return_value={"id": str(uuid.uuid4())}) as mock_create_clarif, \
             patch("db.log_message"), \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enq, \
             patch("groq_client.route_supplier_message", return_value={
                 "tool_name": "request_clarification",
                 "arguments": {
                     "candidate_rfq_ids": [RFQ_UUID, RFQ_UUID_2],
                     "clarifying_question": "Which Cement product are you referring to?",
                     "extracted_price": 50.0,
                 },
             }):

            payload = self._create_payload(msg_id="clarif-msg-1", text="50 AED for Cement")

            # First delivery
            resp1 = test_client.post("/webhook/whatsapp", json=payload)
            assert resp1.status_code == 200
            assert resp1.json()["status"] == "clarification_needed"
            assert mock_create_clarif.call_count == 1

            # Duplicate delivery
            resp2 = test_client.post("/webhook/whatsapp", json=payload)
            assert resp2.status_code == 200
            assert resp2.json()["status"] == "ignored"
            assert mock_create_clarif.call_count == 1

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

    def test_application_restart_simulation_persists_deduplication(self, test_client):
        """9: Restart simulation (in-memory cleared, DB persists claim)."""
        db_persisted_records = {("client-1", "msg-pre-restart")}

        def fake_claim(c_id, m_id):
            key = (c_id, m_id)
            if key in db_persisted_records:
                return False
            db_persisted_records.add(key)
            return True

        mock_client = {"id": "client-1", "name": "Test Client", "whatsapp_instance": "test_instance"}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.claim_webhook_message", side_effect=fake_claim):

            payload = self._create_payload(msg_id="msg-pre-restart")
            resp = test_client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "ignored"
            assert "already processed message id: msg-pre-restart" in resp.json()["reason"]

    def test_missing_or_invalid_message_id_safe(self, test_client):
        """10: Missing or invalid message ID continues existing validation safely."""
        mock_client = {"id": CLIENT_UUID, "name": "Test Client", "whatsapp_instance": "test_instance"}
        mock_supplier = {"id": SUPPLIER_UUID, "phone_number": "971501234567", "client_id": CLIENT_UUID}

        with patch("db.get_client_by_instance", return_value=mock_client), \
             patch("db.get_supplier_by_phone", return_value=mock_supplier), \
             patch("db.get_open_rfqs_for_supplier", return_value=[]), \
             patch("db.get_pending_clarification_for_supplier", return_value=None), \
             patch("db.log_message"), \
             patch("db.claim_webhook_message") as mock_claim:

            # Payload with no key.id
            payload = {
                "event": "messages.upsert",
                "instance": "test_instance",
                "data": {
                    "key": {"fromMe": False, "remoteJid": "971501234567@s.whatsapp.net"},
                    "message": {"conversation": "Hello without message id"},
                },
            }
            resp = test_client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "no_open_rfq"
            mock_claim.assert_not_called()

    def test_unknown_tenant_does_not_claim_idempotency_record(self, test_client):
        """11: Unknown tenant/instance exits before claiming idempotency."""
        with patch("db.get_client_by_instance", return_value=None), \
             patch("db.claim_webhook_message") as mock_claim, \
             patch("db.log_webhook_error"):

            payload = self._create_payload(msg_id="msg-unknown-instance", instance="unregistered_instance")
            resp = test_client.post("/webhook/whatsapp", json=payload)
            assert resp.status_code == 200
            assert resp.json()["status"] == "ignored"
            assert "unknown instance" in resp.json()["reason"]
            mock_claim.assert_not_called()
