import { supabase, DEMO_CLIENT_ID, API_URL } from './supabaseClient';

export async function fetchDashboardMetrics() {
  const [rfqsRes, suppliersRes, quotesRes, flagsRes, recentMessagesRes] = await Promise.all([
    supabase.from('rfqs').select('id, status, created_at', { count: 'exact' }).eq('client_id', DEMO_CLIENT_ID),
    supabase.from('suppliers').select('id', { count: 'exact' }).eq('client_id', DEMO_CLIENT_ID).eq('is_active', true),
    supabase.from('quotes').select('id', { count: 'exact' }),
    supabase.from('flagged_for_review').select('id', { count: 'exact' }).eq('client_id', DEMO_CLIENT_ID).eq('status', 'pending'),
    supabase.from('message_log').select('*, suppliers(name)').eq('client_id', DEMO_CLIENT_ID).order('created_at', { ascending: false }).limit(6),
  ]);

  const activeRfqs = (rfqsRes.data || []).filter(r => r.status === 'active').length;

  return {
    totalRfqs: rfqsRes.count || 0,
    activeRfqs,
    totalSuppliers: suppliersRes.count || 0,
    totalQuotes: quotesRes.count || 0,
    pendingFlags: flagsRes.count || 0,
    recentMessages: recentMessagesRes.data || [],
  };
}

export async function fetchSuppliers() {
  const { data, error } = await supabase
    .from('suppliers')
    .select('*')
    .eq('client_id', DEMO_CLIENT_ID)
    .order('created_at', { ascending: false });
  if (error) throw error;
  return data;
}

export async function createSupplier(payload) {
  const { data, error } = await supabase
    .from('suppliers')
    .insert({
      client_id: DEMO_CLIENT_ID,
      name: payload.name,
      phone_number: payload.phone_number,
      category: payload.category || [],
      notes: payload.notes || '',
      is_active: payload.is_active !== false,
    })
    .select();
  if (error) throw error;
  return data[0];
}

export async function updateSupplier(id, payload) {
  const { data, error } = await supabase
    .from('suppliers')
    .update({
      name: payload.name,
      phone_number: payload.phone_number,
      category: payload.category || [],
      notes: payload.notes,
      is_active: payload.is_active,
    })
    .eq('id', id)
    .select();
  if (error) throw error;
  return data[0];
}

export async function createRFQ(payload) {
  const response = await fetch(`${API_URL}/rfq/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_id: DEMO_CLIENT_ID,
      product_name: payload.product_name,
      category: payload.category,
      specs: payload.specs,
      quantity: payload.quantity ? parseInt(payload.quantity) : null,
      deadline_hours: payload.deadline_hours ? parseInt(payload.deadline_hours) : 24,
    }),
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to create RFQ');
  }
  return await response.json();
}

export async function fetchRFQs() {
  const { data, error } = await supabase
    .from('rfqs')
    .select('*, rfq_suppliers(id, status, supplier_id, suppliers(name)), quotes(id)')
    .eq('client_id', DEMO_CLIENT_ID)
    .order('created_at', { ascending: false });
  if (error) throw error;
  return data;
}

export async function fetchRFQDetail(rfqId) {
  const [rfqRes, suppliersRes, quotesRes, rankingRes] = await Promise.all([
    supabase.from('rfqs').select('*').eq('id', rfqId).single(),
    supabase.from('rfq_suppliers').select('*, suppliers(*)').eq('rfq_id', rfqId),
    supabase.from('quotes').select('*, suppliers(name)').eq('rfq_id', rfqId),
    supabase.from('rfq_rankings').select('*').eq('rfq_id', rfqId).order('created_at', { ascending: false }).limit(1),
  ]);

  if (rfqRes.error) throw rfqRes.error;

  return {
    rfq: rfqRes.data,
    suppliers: suppliersRes.data || [],
    quotes: quotesRes.data || [],
    ranking: rankingRes.data?.[0] || null,
  };
}

export async function triggerAIRanking(rfqId) {
  const response = await fetch(`${API_URL}/rfq/${rfqId}/rank`, {
    method: 'POST',
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to trigger AI ranking');
  }
  return await response.json();
}

export async function fetchMessages(supplierId = null, rfqId = null) {
  let query = supabase
    .from('message_log')
    .select('*, suppliers(name, phone_number), rfqs(product_name)')
    .eq('client_id', DEMO_CLIENT_ID)
    .order('created_at', { ascending: true });

  if (supplierId) query = query.eq('supplier_id', supplierId);
  if (rfqId) query = query.eq('related_rfq_id', rfqId);

  const { data, error } = await query;
  if (error) throw error;
  return data;
}

export async function fetchFlags() {
  const { data, error } = await supabase
    .from('flagged_for_review')
    .select('*, suppliers(name, phone_number), rfqs(product_name)')
    .eq('client_id', DEMO_CLIENT_ID)
    .order('created_at', { ascending: false });
  if (error) throw error;
  return data;
}

export async function resolveFlag(flagId) {
  const response = await fetch(`${API_URL}/flags/${flagId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Failed to resolve flag: ${response.statusText}`);
  }
  return await response.json();
}

export async function closeRFQ(rfqId, status = 'closed') {
  const response = await fetch(`${API_URL}/rfq/${rfqId}/close?status=${status}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Failed to close RFQ: ${response.statusText}`);
  }
  return await response.json();
}
