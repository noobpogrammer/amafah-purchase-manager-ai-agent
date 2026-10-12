"""Regression checks for supplier lookup and local human Ignore resolution."""
import os
import unittest
from unittest.mock import patch, MagicMock
os.environ.setdefault("SUPABASE_URL", "https://localhost.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
import db


class DispatchAndIgnoreTests(unittest.TestCase):
    def test_supplier_id_lookups_are_tenant_scoped(self):
        with patch.object(db.supabase, "table") as table:
            query = table.return_value
            query.select.return_value = query
            query.eq.return_value = query
            query.is_.return_value = query
            query.maybe_single.return_value = query
            query.execute.return_value.data = {"id": "supplier-1", "client_id": "client-1"}
            row = db.get_supplier_by_id("supplier-1", client_id="client-1")
            self.assertEqual(row["id"], "supplier-1")
            table.assert_called_once_with("suppliers")
            query.eq.assert_any_call("client_id", "client-1")

    def test_missing_supplier_is_not_reachable(self):
        with patch.object(db.supabase, "table") as table:
            query = table.return_value
            query.select.return_value = query
            query.eq.return_value = query
            query.is_.return_value = query
            query.maybe_single.return_value = query
            query.execute.return_value.data = None
            self.assertIsNone(db.get_supplier_by_id("missing", "tenant"))

if __name__ == "__main__":
    unittest.main()
