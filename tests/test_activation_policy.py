"""Option C activation policy tests: no real Supabase or WhatsApp I/O."""
import os
import unittest
from unittest.mock import patch
os.environ.setdefault("SUPABASE_URL", "https://localhost.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "phase2-fake-service-role-key")
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

    def test_short_replies_reach_main_ai_only_with_buying_context(self):
        for msg in ("24", "Agreed", "yes", "3 days", "haan theek hai"):
            with self.subTest(message=msg):
                active = self.check(msg, has_rfq=True)
                self.assertTrue(active["allow_autonomous_reply"])
                self.assertEqual(active["engagement_reason"], "interpret_in_existing_procurement_context")
                self.assertFalse(self.check(msg, has_rfq=False)["allow_autonomous_reply"])
                self.assertFalse(self.check(msg, has_rfq=True, paused=True)["allow_autonomous_reply"])

    def test_product_switch_and_conditional_agreement_are_left_to_main_ai(self):
        for msg in ("Now the fans: agreed at 85 each", "Agreed, but only for 100 instead of 50"):
            result = self.check(msg, has_rfq=True)
            self.assertEqual(result["engagement_decision"], "ENGAGE")
            # Interpretation is enabled; this endpoint never grants acceptance.
            self.assertNotIn("approval_id", result)

    def test_empty_message_is_not_activated_by_open_rfqs(self):
        self.assertFalse(self.check("  ", has_rfq=True)["allow_autonomous_reply"])

    def test_pause_overrides_commercial_intent(self):
        result = self.check("Noora Brush AED 15",has_rfq=True,paused=True)
        self.assertFalse(result["allow_autonomous_reply"])
        self.assertEqual(result["engagement_reason"],"client_automation_paused")

if __name__ == "__main__":
    unittest.main()
