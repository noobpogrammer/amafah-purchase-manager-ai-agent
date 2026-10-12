"""Option C activation policy tests: no real Supabase or WhatsApp I/O."""
import os
import unittest
from unittest.mock import patch
os.environ.setdefault("SUPABASE_URL", "https://localhost.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "phase2-fake-service-role-key")
os.environ.setdefault("GROQ_API_KEY", "phase2-fake-groq-key")
os.environ["OPENCLAW_TOOL_API_KEY"] = "phase2-test-tool-key"
from fastapi import FastAPI
from fastapi.testclient import TestClient
import openclaw_bridge as bridge


class ActivationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(bridge.router)
        cls.client = TestClient(app)

    def check(self, msg, *, has_rfq=False, paused=False):
        links = [{"rfqs": {"id": "rfq-1", "product_name": "Noora Brush"}, "status": "sent"}] if has_rfq else []
        class Result:
            data = []
        with patch.object(bridge, "_get_supplier_for_client", return_value={"id":"s", "name":"Fatema"}), \
             patch.object(bridge.db, "get_open_rfqs_for_supplier", return_value=links), \
             patch.object(bridge.db, "get_active_negotiation_session_for_supplier", return_value=None), \
             patch.object(bridge.db, "get_pending_clarification_for_supplier", return_value=None), \
             patch.object(bridge.db, "is_client_automation_paused", return_value=paused), \
             patch.object(bridge.db.supabase, "table") as table:
            table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = Result()
            response = self.client.post("/openclaw/activation-gate", headers={"X-OpenClaw-Key": "phase2-test-tool-key"}, json={
                "client_id":"11111111-1111-4111-8111-111111111111", "supplier_id":"22222222-2222-4222-8222-222222222222", "message": msg
            })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_greeting_always_silent(self):
        for active in (False, True):
            for msg in ("hi", "Hello!", "Good morning", "Assalamualaikum"):
                result = self.check(msg,has_rfq=active)
                self.assertEqual(result["engagement_decision"],"IGNORE")

    def test_personal_conversation_silent(self):
        self.assertEqual(self.check("Bhai, tame su kari rahya cho?",has_rfq=True)["engagement_decision"],"IGNORE")
        self.assertEqual(self.check("How is your family?",has_rfq=True)["engagement_decision"],"IGNORE")

    def test_commercial_requires_context(self):
        self.assertEqual(self.check("For Noora Brush my final price is AED 15",has_rfq=True)["engagement_decision"],"ENGAGE")
        self.assertEqual(self.check("For Noora Brush my final price is AED 15",has_rfq=False)["engagement_decision"],"HUMAN_REVIEW")

    def test_pause_overrides_commercial_intent(self):
        result = self.check("Noora Brush AED 15",has_rfq=True,paused=True)
        self.assertFalse(result["allow_autonomous_reply"])
        self.assertEqual(result["engagement_reason"],"client_automation_paused")

if __name__ == "__main__":
    unittest.main()
