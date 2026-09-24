from fastapi.testclient import TestClient
import importlib.util
import os
import sys

# Load main.py as a module by path so pytest can import it regardless of CWD/module path
spec = importlib.util.spec_from_file_location(
    "main",
    os.path.join(os.path.dirname(__file__), os.pardir, "main.py"),
)
main = importlib.util.module_from_spec(spec)
# Ensure project root is on sys.path so relative imports like `import db` succeed
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
spec.loader.exec_module(main)


def test_webhook_derives_client_id_and_logs(monkeypatch):
    calls = []

    # Stub client lookup by instance
    def fake_get_client_by_instance(instance):
        return {"id": "client-123", "name": "Test Client", "whatsapp_instance": instance}

    # Stub scoped supplier lookup
    def fake_get_supplier_by_phone(client_id, phone):
        return {"id": "supplier-1", "client_id": client_id, "phone_number": phone}

    # Stub open rfqs to force the "no_open_rfq" path
    def fake_get_open_rfqs_for_supplier(supplier_id):
        return []

    def fake_log_message(client_id, supplier_id, direction, body, related_rfq_id=None):
        calls.append((client_id, supplier_id, direction, body, related_rfq_id))

    monkeypatch.setattr(main.db, "get_client_by_instance", fake_get_client_by_instance)
    monkeypatch.setattr(main.db, "get_supplier_by_phone", fake_get_supplier_by_phone)
    monkeypatch.setattr(main.db, "get_open_rfqs_for_supplier", fake_get_open_rfqs_for_supplier)
    monkeypatch.setattr(main.db, "log_message", fake_log_message)
    # Stub other DB helpers that may be invoked during webhook handling to avoid real DB calls
    monkeypatch.setattr(main.db, "get_pending_clarification_for_supplier", lambda supplier_id: None)
    monkeypatch.setattr(main.db, "get_rfq_supplier_by_sent_message_id", lambda supplier_id, sent_id: None)
    monkeypatch.setattr(main.db, "get_rfq_supplier_by_quoted_text", lambda supplier_id, quoted_text: None)
    monkeypatch.setattr(main.db, "get_supplier_prior_quotes", lambda supplier_id, rfq_ids=None: [])

    client = TestClient(main.app)

    payload = {
        "event": "messages.upsert",
        "instance": "test-instance",
        "data": [
            {
                "key": {"remoteJid": "12345@s.whatsapp.net", "id": "msg-1"},
                "message": {"conversation": "Hello"},
            }
        ],
    }

    resp = client.post("/webhook/whatsapp", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "no_open_rfq"
    # Ensure log_message was called and the client_id was derived from instance
    assert calls and calls[0][0] == "client-123"


def test_webhook_disambiguates_same_phone_under_different_clients(monkeypatch):
    """Verifies that the same supplier phone under two clients routes strictly according to the instance."""
    calls = []

    clients_db = {
        "client-a-instance": {"id": "client-A", "name": "Business A", "whatsapp_instance": "client-a-instance"},
        "client-b-instance": {"id": "client-B", "name": "Business B", "whatsapp_instance": "client-b-instance"},
    }
    suppliers_db = {
        ("client-A", "923188012805"): {"id": "supp-A", "client_id": "client-A", "name": "Supplier under A", "phone_number": "923188012805"},
        ("client-B", "923188012805"): {"id": "supp-B", "client_id": "client-B", "name": "Supplier under B", "phone_number": "923188012805"},
    }

    monkeypatch.setattr(main.db, "get_client_by_instance", lambda inst: clients_db.get(inst))
    monkeypatch.setattr(main.db, "get_supplier_by_phone", lambda c_id, ph: suppliers_db.get((c_id, ph)))
    monkeypatch.setattr(main.db, "get_open_rfqs_for_supplier", lambda supp_id: [])
    monkeypatch.setattr(main.db, "log_message", lambda c_id, s_id, d, b, r=None: calls.append((c_id, s_id, d, b)))
    monkeypatch.setattr(main.db, "get_pending_clarification_for_supplier", lambda s: None)
    monkeypatch.setattr(main.db, "get_rfq_supplier_by_sent_message_id", lambda s, m: None)
    monkeypatch.setattr(main.db, "get_rfq_supplier_by_quoted_text", lambda s, q: None)

    client = TestClient(main.app)

    # Inbound message to Client A's instance
    payload_a = {
        "event": "messages.upsert",
        "instance": "client-a-instance",
        "data": [{"key": {"remoteJid": "923188012805@s.whatsapp.net", "id": "msg-a"}, "message": {"conversation": "Quote 50"}}]
    }
    resp_a = client.post("/webhook/whatsapp", json=payload_a)
    assert resp_a.status_code == 200
    assert calls[-1][0] == "client-A"
    assert calls[-1][1] == "supp-A"

    # Inbound message to Client B's instance
    payload_b = {
        "event": "messages.upsert",
        "instance": "client-b-instance",
        "data": [{"key": {"remoteJid": "923188012805@s.whatsapp.net", "id": "msg-b"}, "message": {"conversation": "Quote 45"}}]
    }
    resp_b = client.post("/webhook/whatsapp", json=payload_b)
    assert resp_b.status_code == 200
    assert calls[-1][0] == "client-B"
    assert calls[-1][1] == "supp-B"



def test_webhook_ignores_group_chat_before_supplier_lookup(monkeypatch):
    """Group WhatsApp events must never be treated as supplier messages."""
    monkeypatch.setattr(
        main.db,
        "get_client_by_instance",
        lambda instance: (_ for _ in ()).throw(AssertionError("client lookup should not run for group messages")),
    )

    client = TestClient(main.app)
    payload = {
        "event": "messages.upsert",
        "instance": "test-instance",
        "data": [{
            "key": {
                "fromMe": False,
                "remoteJid": "120363402951903145@g.us",
                "id": "group-msg-1",
            },
            "message": {"conversation": "45 AED per piece"},
        }],
    }

    resp = client.post("/webhook/whatsapp", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored", "reason": "group chat message"}


def test_webhook_resolves_lid_sender_via_remote_jid_alt(monkeypatch):
    """When Evolution sends an opaque @lid, supplier matching uses remoteJidAlt."""
    looked_up = []
    logged = []

    monkeypatch.setattr(
        main.db,
        "get_client_by_instance",
        lambda instance: {"id": "client-123", "name": "Test Client", "whatsapp_instance": instance},
    )

    def fake_get_supplier_by_phone(client_id, phone):
        looked_up.append((client_id, phone))
        return {
            "id": "supplier-1",
            "client_id": client_id,
            "phone_number": phone,
            "name": "Supplier",
        }

    monkeypatch.setattr(main.db, "get_supplier_by_phone", fake_get_supplier_by_phone)
    monkeypatch.setattr(main.db, "get_open_rfqs_for_supplier", lambda supplier_id: [])
    monkeypatch.setattr(main.db, "get_pending_clarification_for_supplier", lambda supplier_id: None)
    monkeypatch.setattr(main.db, "get_rfq_supplier_by_sent_message_id", lambda supplier_id, sent_id: None)
    monkeypatch.setattr(main.db, "get_rfq_supplier_by_quoted_text", lambda supplier_id, quoted_text: None)
    monkeypatch.setattr(main.db, "claim_webhook_message", lambda client_id, message_id: True)
    monkeypatch.setattr(main.db, "complete_webhook_message", lambda client_id, message_id: True)
    monkeypatch.setattr(
        main.db,
        "log_message",
        lambda client_id, supplier_id, direction, body, related_rfq_id=None: logged.append(
            (client_id, supplier_id, direction, body)
        ) or "log-1",
    )

    client = TestClient(main.app)
    payload = {
        "event": "messages.upsert",
        "instance": "test-instance",
        "data": [{
            "key": {
                "fromMe": False,
                "remoteJid": "259519825358937@lid",
                "remoteJidAlt": "923362853198@s.whatsapp.net",
                "id": "lid-msg-1",
            },
            "message": {"conversation": "45 AED per piece, delivery in 2 days."},
        }],
    }

    resp = client.post("/webhook/whatsapp", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_open_rfq"
    assert looked_up == [("client-123", "923362853198")]
    assert logged and logged[0][1] == "supplier-1"
