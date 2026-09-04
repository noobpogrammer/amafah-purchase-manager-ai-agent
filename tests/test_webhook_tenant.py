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

    # Stub supplier lookup across all clients
    def fake_get_supplier_by_phone_any_client(phone):
        return {"id": "supplier-1", "client_id": "client-123", "phone_number": phone}

    # Stub open rfqs to force the "no_open_rfq" path
    def fake_get_open_rfqs_for_supplier(supplier_id):
        return []

    def fake_log_message(client_id, supplier_id, direction, body, related_rfq_id=None):
        calls.append((client_id, supplier_id, direction, body, related_rfq_id))

    monkeypatch.setattr(main.db, "get_supplier_by_phone_any_client", fake_get_supplier_by_phone_any_client)
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
    # Ensure log_message was called and the client_id was derived from supplier
    assert calls and calls[0][0] == "client-123"
