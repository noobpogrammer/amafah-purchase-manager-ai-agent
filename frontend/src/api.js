import { supabase, API_URL, authorizedFetch, apiFetch, getCurrentClientId } from './supabaseClient';
export { authorizedFetch, apiFetch };

export function formatRfqDropdownLabel(rfq) {
  if (!rfq) return '';
  const parts = [];

  // Product Name
  const productName = (rfq.product_name || 'Untitled RFQ').trim();
  parts.push(productName);

  // Specs & Quantity segment: e.g. "60W, qty 30" or "60W" or "qty 30"
  const specDetails = [];
  if (rfq.specs && rfq.specs.trim()) {
    specDetails.push(rfq.specs.trim());
  }
  if (rfq.quantity !== null && rfq.quantity !== undefined && rfq.quantity !== '') {
    specDetails.push(`qty ${rfq.quantity}`);
  }
  if (specDetails.length > 0) {
    parts.push(specDetails.join(', '));
  }

  // Date/Time segment: formatted as short date+time (e.g. Sep 2, 5:34 AM)
  if (rfq.created_at) {
    try {
      const d = new Date(rfq.created_at);
      if (!isNaN(d.getTime())) {
        const dateStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        const timeStr = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
        parts.push(`${dateStr}, ${timeStr}`);
      }
    } catch (e) {
      // ignore
    }
  }

  let label = parts.join(' — ');

  // Category segment: e.g. " (Building Materials)"
  if (rfq.category && rfq.category.trim()) {
    label += ` (${rfq.category.trim()})`;
  }

  return label;
}

export async function fetchDashboardMetrics() {
  const clientId = await getCurrentClientId();
  if (!clientId) throw new Error('Missing client_id (not authenticated)');

  const [rfqsRes, suppliersRes, quotesRes, flagsRes, recentMessagesRes] = await Promise.all([
    supabase.from('rfqs').select('id, status, created_at', { count: 'exact' }).eq('client_id', clientId),
    supabase.from('suppliers').select('id', { count: 'exact' }).eq('client_id', clientId).eq('is_active', true),
    supabase.from('quotes').select('id', { count: 'exact' }),
    supabase.from('flagged_for_review').select('id', { count: 'exact' }).eq('client_id', clientId).eq('status', 'pending'),
    supabase.from('message_log').select('*, suppliers(name)').eq('client_id', clientId).order('created_at', { ascending: false }).limit(6),
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
  const clientId = await getCurrentClientId();
  if (!clientId) throw new Error('Missing client_id (not authenticated)');

  const { data, error } = await supabase
    .from('suppliers')
    .select('*')
    .eq('client_id', clientId)
    .order('created_at', { ascending: false });
  if (error) throw error;
  return data;
}

export async function createSupplier(payload) {
  const clientId = await getCurrentClientId();
  if (!clientId) throw new Error('Missing client_id (not authenticated)');

  const { data, error } = await supabase
    .from('suppliers')
    .insert({
      client_id: clientId,
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
  const response = await authorizedFetch('/rfq/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      product_name: payload.product_name,
      category: payload.category,
      specs: payload.specs,
      quantity: payload.quantity ? parseInt(payload.quantity) : null,
      last_quote: payload.last_quote !== undefined && payload.last_quote !== null && payload.last_quote !== '' ? Number(payload.last_quote) : null,
      deadline_hours: payload.deadline_hours ? parseInt(payload.deadline_hours) : 24,
    }),
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to create RFQ');
  }
  return await response.json();
}

export async function bulkCreateRFQs(formData) {
  const response = await authorizedFetch('/rfq/bulk-create', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to upload RFQs');
  }

  return await response.json();
}

export async function fetchRFQs() {
  const clientId = await getCurrentClientId();
  if (!clientId) throw new Error('Missing client_id (not authenticated)');

  const { data, error } = await supabase
    .from('rfqs')
    .select('*, rfq_suppliers(id, status, supplier_id, suppliers(name)), quotes(id)')
    .eq('client_id', clientId)
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
  const response = await authorizedFetch(`/rfq/${rfqId}/rank`, {
    method: 'POST',
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to trigger AI ranking');
  }
  return await response.json();
}

export async function fetchMessages(supplierId = null, rfqId = null) {
  const clientId = await getCurrentClientId();
  if (!clientId) throw new Error('Missing client_id (not authenticated)');

  let query = supabase
    .from('message_log')
    .select('*, suppliers(name, phone_number), rfqs(product_name)')
    .eq('client_id', clientId)
    .order('created_at', { ascending: true });

  if (supplierId) query = query.eq('supplier_id', supplierId);
  if (rfqId) query = query.eq('related_rfq_id', rfqId);

  const { data, error } = await query;
  if (error) throw error;
  return data;
}

export async function fetchFlags() {
  const clientId = await getCurrentClientId();
  if (!clientId) throw new Error('Missing client_id (not authenticated)');

  const { data, error } = await supabase
    .from('flagged_for_review')
    .select('*, suppliers(name, phone_number), rfqs(product_name)')
    .eq('client_id', clientId)
    .order('created_at', { ascending: false });
  if (error) throw error;
  return data;
}

export async function resolveFlag(flagId) {
  const response = await authorizedFetch(`/flags/${flagId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || `Failed to resolve flag: ${response.statusText}`);
  }
  return await response.json();
}

export async function respondToFlag(flagId, responseText, sendToSupplier = true) {
  const response = await authorizedFetch(`/flags/${flagId}/respond`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ response: responseText, send_to_supplier: sendToSupplier }),
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || 'Failed to respond to flag');
  }
  return await response.json();
}


export async function closeRFQ(rfqId, status = 'closed') {
  const response = await authorizedFetch(`/rfq/${rfqId}/close?status=${status}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || `Failed to close RFQ: ${response.statusText}`);
  }
  return await response.json();
}

export async function fetchRFQsAudit() {
  const response = await authorizedFetch('/rfqs/audit');
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || `Failed to fetch RFQ audit: ${response.statusText}`);
  }
  return await response.json();
}

export async function fetchCategories() {
  const clientId = await getCurrentClientId();
  if (!clientId) throw new Error('Missing client_id (not authenticated)');

  const { data, error } = await supabase
    .from('categories')
    .select('name')
    .eq('client_id', clientId)
    .order('created_at', { ascending: true });

  const defaultList = ['Electronics', 'Hardware', 'Plumbing', 'Electrical', 'Tools', 'Building Materials', 'General'];
  let categoryNames = (data || []).map((c) => c.name);

  if (error || !categoryNames.length) {
    categoryNames = [...defaultList];
  }

  // Also collect any categories dynamically from suppliers table if present
  try {
    const suppliersRes = await supabase.from('suppliers').select('category').eq('client_id', clientId);
    if (suppliersRes.data) {
      suppliersRes.data.forEach((s) => {
        if (Array.isArray(s.category)) {
          s.category.forEach((cat) => {
            if (cat && !categoryNames.includes(cat)) {
              categoryNames.push(cat);
            }
          });
        }
      });
    }
  } catch (e) {
    // Ignore error
  }

  defaultList.forEach((d) => {
    if (!categoryNames.includes(d)) categoryNames.push(d);
  });

  return categoryNames;
}

export async function createCustomCategory(categoryName) {
  const cleanName = (categoryName || '').trim();
  if (!cleanName) throw new Error('Category name cannot be empty');

  // Insert into categories table in Supabase
  const clientId = await getCurrentClientId();
  if (!clientId) throw new Error('Missing client_id (not authenticated)');

  const { data, error } = await supabase
    .from('categories')
    .insert({
      client_id: clientId,
      name: cleanName,
    })
    .select();

  if (error && error.code !== '23505') {
    throw error;
  }

  return cleanName;
}

