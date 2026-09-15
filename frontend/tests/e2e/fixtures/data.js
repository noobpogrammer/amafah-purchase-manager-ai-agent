/**
 * Reusable mock data factories for Amafah E2E tests
 */

export const mockClient = {
  id: 'client-111',
  name: 'Amafah Automation General Trading LLC',
  created_at: '2026-01-01T00:00:00Z',
};

export const mockAdminProfile = {
  id: 'user-admin-1',
  client_id: 'client-111',
  role: 'admin',
  email: 'admin@amafah.com',
  created_at: '2026-01-01T00:00:00Z',
};

export const mockMemberProfile = {
  id: 'user-member-1',
  client_id: 'client-111',
  role: 'member',
  email: 'member@amafah.com',
  created_at: '2026-01-02T00:00:00Z',
};

export const mockCategories = [
  'Electronics',
  'Hardware',
  'Plumbing',
  'Electrical',
  'Tools',
  'Building Materials',
  'General',
];

export const mockSuppliers = [
  {
    id: 'supp-1',
    client_id: 'client-111',
    name: 'Al Noor Hardware & Tools',
    phone_number: '+971501112233',
    category: ['Hardware', 'Tools'],
    notes: 'Payment net 30 days. Fast delivery.',
    is_active: true,
    created_at: '2026-01-10T10:00:00Z',
  },
  {
    id: 'supp-2',
    client_id: 'client-111',
    name: 'Emirates Electrical Supplies',
    phone_number: '+971502223344',
    category: ['Electrical', 'Electronics'],
    notes: 'Premium Schneider / ABB distributor',
    is_active: true,
    created_at: '2026-01-11T11:00:00Z',
  },
  {
    id: 'supp-3',
    client_id: 'client-111',
    name: 'Gulf Plumbing Solutions',
    phone_number: '+971503334455',
    category: ['Plumbing', 'Building Materials'],
    notes: 'Pipes and fittings wholesale',
    is_active: false,
    created_at: '2026-01-12T12:00:00Z',
  },
];

export const mockRfqs = [
  {
    id: 'rfq-active-1',
    client_id: 'client-111',
    product_name: 'Copper Water Pipe 1/2 Inch',
    category: 'Plumbing',
    specs: 'Type L, ASTM B88 compliant, 20ft lengths',
    quantity: 50,
    last_quote: 45.0,
    acceptable_price_min: 40.0,
    acceptable_price_max: 48.0,
    deadline_hours: 24,
    status: 'active',
    created_at: '2026-09-12T08:30:00Z',
    rfq_suppliers: [
      {
        id: 'rs-1',
        rfq_id: 'rfq-active-1',
        supplier_id: 'supp-3',
        status: 'responded',
        sent_at: '2026-09-12T08:31:00Z',
        reminder_count: 0,
        suppliers: { name: 'Gulf Plumbing Solutions', phone_number: '+971503334455' },
      },
    ],
    quotes: [{ id: 'quote-1' }],
  },
  {
    id: 'rfq-closed-1',
    client_id: 'client-111',
    product_name: 'Heavy Duty Power Drill 800W',
    category: 'Tools',
    specs: 'Keyless chuck, 2-speed, industrial grade',
    quantity: 10,
    last_quote: 250.0,
    acceptable_price_min: 220.0,
    acceptable_price_max: 260.0,
    deadline_hours: 48,
    status: 'closed',
    created_at: '2026-09-10T14:00:00Z',
    rfq_suppliers: [
      {
        id: 'rs-2',
        rfq_id: 'rfq-closed-1',
        supplier_id: 'supp-1',
        status: 'responded',
        sent_at: '2026-09-10T14:01:00Z',
        reminder_count: 1,
        suppliers: { name: 'Al Noor Hardware & Tools', phone_number: '+971501112233' },
      },
    ],
    quotes: [{ id: 'quote-2' }],
  },
];

export const mockQuotes = [
  {
    id: 'quote-1',
    rfq_id: 'rfq-active-1',
    supplier_id: 'supp-3',
    price: 44.5,
    variant_label: 'Standard Mueller',
    delivery_time: 'Next day delivery',
    quality_notes: 'ASTM B88 certified with MTC',
    raw_message: 'Hi, we can supply 50 pcs Copper Water Pipe 1/2 Inch Mueller at 44.50 AED each, ready for next day dispatch.',
    created_at: '2026-09-12T09:15:00Z',
    suppliers: { name: 'Gulf Plumbing Solutions' },
  },
  {
    id: 'quote-1-var2',
    rfq_id: 'rfq-active-1',
    supplier_id: 'supp-3',
    price: 42.0,
    variant_label: 'Generic Local',
    delivery_time: '2-3 business days',
    quality_notes: 'Standard grade',
    raw_message: 'Alternative generic option available at 42.00 AED.',
    created_at: '2026-09-12T09:16:00Z',
    suppliers: { name: 'Gulf Plumbing Solutions' },
  },
];

export const mockRanking = {
  id: 'rank-1',
  rfq_id: 'rfq-active-1',
  best_quote_id: 'quote-1',
  best_supplier_id: 'supp-3',
  reasoning: 'Gulf Plumbing Solutions offers Mueller certified ASTM pipes at 44.50 AED within acceptable budget ($40-$48) with immediate next-day delivery.',
  ranking_json: {
    best_quote_id: 'quote-1',
    ranking: [
      {
        rank: 1,
        quote_id: 'quote-1',
        supplier_id: 'supp-3',
        supplier_name: 'Gulf Plumbing Solutions',
        variant_label: 'Standard Mueller',
        price: 44.5,
        summary: 'Best balance of brand compliance, warranty, and next-day delivery speed at $44.50 AED.',
      },
      {
        rank: 2,
        quote_id: 'quote-1-var2',
        supplier_id: 'supp-3',
        supplier_name: 'Gulf Plumbing Solutions',
        variant_label: 'Generic Local',
        price: 42.0,
        summary: 'Lower price at $42.00 AED but longer delivery lead time and generic specification.',
      },
    ],
  },
  created_at: '2026-09-12T10:00:00Z',
};

export const mockFlags = [
  {
    id: 'flag-1',
    client_id: 'client-111',
    supplier_id: 'supp-1',
    related_rfq_id: 'rfq-active-1',
    category: 'requires_business_knowledge',
    reason: 'Supplier requested 50% advance payment terms before dispatch.',
    raw_message: 'We can give 42 AED if you pay 50% advance via bank transfer today.',
    status: 'pending',
    created_at: '2026-09-12T11:00:00Z',
    suppliers: { name: 'Al Noor Hardware & Tools', phone_number: '+971501112233' },
    rfqs: { product_name: 'Copper Water Pipe 1/2 Inch' },
  },
  {
    id: 'flag-2',
    client_id: 'client-111',
    supplier_id: 'supp-2',
    related_rfq_id: 'rfq-active-1',
    category: 'contradictory_information',
    reason: 'Supplier gave conflicting lead times in successive messages.',
    raw_message: 'Earlier we said 2 days, now factory says 3 weeks.',
    status: 'resolved',
    created_at: '2026-09-11T09:00:00Z',
    resolved_at: '2026-09-11T10:30:00Z',
    suppliers: { name: 'Emirates Electrical Supplies', phone_number: '+971502223344' },
    rfqs: { product_name: 'Copper Water Pipe 1/2 Inch' },
  },
];

export const mockMessages = [
  {
    id: 'msg-1',
    client_id: 'client-111',
    supplier_id: 'supp-3',
    related_rfq_id: 'rfq-active-1',
    direction: 'outbound',
    body: 'Hello Gulf Plumbing Solutions, please provide your best quotation for 50 pcs Copper Water Pipe 1/2 Inch (Type L, ASTM B88). Response requested within 24 hours.',
    status: 'sent',
    created_at: '2026-09-12T08:31:00Z',
    suppliers: { name: 'Gulf Plumbing Solutions', phone_number: '+971503334455' },
    rfqs: { product_name: 'Copper Water Pipe 1/2 Inch' },
  },
  {
    id: 'msg-2',
    client_id: 'client-111',
    supplier_id: 'supp-3',
    related_rfq_id: 'rfq-active-1',
    direction: 'inbound',
    body: 'Hi, we can supply 50 pcs Copper Water Pipe 1/2 Inch Mueller at 44.50 AED each, ready for next day dispatch.',
    status: 'delivered',
    created_at: '2026-09-12T09:15:00Z',
    suppliers: { name: 'Gulf Plumbing Solutions', phone_number: '+971503334455' },
    rfqs: { product_name: 'Copper Water Pipe 1/2 Inch' },
  },
];

export const mockActivityTimeline = [
  {
    id: 'act-1',
    timestamp: '2026-09-12T08:31:00Z',
    event_type: 'outbound_sent',
    origin: 'system',
    status: 'sent',
    title: 'Outbound RFQ Broadcast Sent',
    summary: 'Sent RFQ invitation to Gulf Plumbing Solutions (+971503334455).',
    supplier_name: 'Gulf Plumbing Solutions',
    details: { rfq_id: 'rfq-active-1', phone: '+971503334455', delivery_status: 'sent' },
  },
  {
    id: 'act-2',
    timestamp: '2026-09-12T09:15:00Z',
    event_type: 'decision_executed',
    origin: 'agent',
    status: 'executed',
    title: 'Supplier Quote Recorded (Standard Mueller)',
    summary: 'Recorded commercial quote of 44.50 AED from Gulf Plumbing Solutions.',
    supplier_name: 'Gulf Plumbing Solutions',
    details: { price: 44.5, variant: 'Standard Mueller', delivery_time: 'Next day' },
  },
  {
    id: 'act-3',
    timestamp: '2026-09-12T09:30:00Z',
    event_type: 'decision_rejected',
    origin: 'policy_validator',
    status: 'rejected',
    title: 'Autonomous Acceptance Rejected by Policy Validator',
    summary: 'Agent attempted autonomous purchase commitment, prohibited by procurement policy.',
    supplier_name: 'Gulf Plumbing Solutions',
    details: { attempted_action: 'accept_quote', rule: 'no_autonomous_acceptance' },
  },
];

export const mockTeamMembers = [
  {
    id: 'user-admin-1',
    client_id: 'client-111',
    role: 'admin',
    email: 'admin@amafah.com',
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'user-member-1',
    client_id: 'client-111',
    role: 'member',
    email: 'purchaser@amafah.com',
    created_at: '2026-02-01T00:00:00Z',
  },
];

export const mockInviteTokens = [
  {
    id: 'token-1',
    client_id: 'client-111',
    token: 'test-invite-token-abc123',
    role: 'member',
    created_at: '2026-09-10T12:00:00Z',
    expires_at: '2026-09-17T12:00:00Z',
    used_at: null,
  },
];

export const mockDeliveryIssues = {
  total: 1,
  page: 1,
  limit: 50,
  issues: [
    {
      id: 'msg-fail-1',
      client_id: 'client-111',
      recipient_phone: '+971509998877',
      status: 'failed',
      error_message: 'WhatsApp number not registered',
      created_at: '2026-09-12T09:00:00Z',
    },
  ],
};
