-- ============================================================
-- Harden create_rfq_with_constraints_rpc Security Permissions
-- Revoke execution from PUBLIC, anon, and authenticated
-- Grant execution strictly to service_role
-- ============================================================

REVOKE EXECUTE ON FUNCTION public.create_rfq_with_constraints_rpc(UUID, TEXT, TEXT, TEXT, INT, NUMERIC, NUMERIC, NUMERIC, INT, INT, JSONB) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.create_rfq_with_constraints_rpc(UUID, TEXT, TEXT, TEXT, INT, NUMERIC, NUMERIC, NUMERIC, INT, INT, JSONB) FROM anon;
REVOKE EXECUTE ON FUNCTION public.create_rfq_with_constraints_rpc(UUID, TEXT, TEXT, TEXT, INT, NUMERIC, NUMERIC, NUMERIC, INT, INT, JSONB) FROM authenticated;

GRANT EXECUTE ON FUNCTION public.create_rfq_with_constraints_rpc(UUID, TEXT, TEXT, TEXT, INT, NUMERIC, NUMERIC, NUMERIC, INT, INT, JSONB) TO service_role;
