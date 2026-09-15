/**
 * API & Supabase Route Interception Helpers for Playwright
 */

import {
  mockClient,
  mockAdminProfile,
  mockMemberProfile,
  mockCategories,
  mockSuppliers,
  mockRfqs,
  mockQuotes,
  mockRanking,
  mockFlags,
  mockMessages,
  mockActivityTimeline,
  mockTeamMembers,
  mockInviteTokens,
  mockDeliveryIssues,
} from './data.js';

export async function setupAppMocks(page, options = {}) {
  const {
    userRole = 'admin', // 'admin', 'member', or 'unauthenticated'
    user = userRole === 'admin' ? mockAdminProfile : userRole === 'member' ? mockMemberProfile : null,
    categories = mockCategories,
    suppliers = mockSuppliers,
    rfqs = mockRfqs,
    quotes = mockQuotes,
    ranking = mockRanking,
    flags = mockFlags,
    messages = mockMessages,
    activity = mockActivityTimeline,
    teamMembers = mockTeamMembers,
    inviteTokens = mockInviteTokens,
    customCategories = [],
  } = options;

  let allCategories = [...new Set([...categories, ...customCategories])];
  let currentSuppliers = structuredClone(suppliers);
  let currentRfqs = structuredClone(rfqs);
  let currentFlags = structuredClone(flags);
  let currentMessages = structuredClone(messages);
  let currentTokens = structuredClone(inviteTokens);

  // Intercept all requests
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = req.url();
    const method = req.method();

    // 1. Supabase Auth Endpoints
    if (
      url.includes('/auth/v1/session') ||
      url.includes('/auth/v1/user') ||
      url.includes('/auth/v1/token') ||
      url.includes('/auth/v1/signup') ||
      url.includes('/auth/v1/logout') ||
      url.includes('/auth/v1/recover')
    ) {
      if (url.includes('/auth/v1/logout')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: { 'access-control-allow-origin': '*' },
          body: JSON.stringify({}),
        });
      }
      if (url.includes('/auth/v1/recover')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: { 'access-control-allow-origin': '*' },
          body: JSON.stringify({ message: 'Recovery email sent' }),
        });
      }
      if (url.includes('/auth/v1/signup')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: { 'access-control-allow-origin': '*' },
          body: JSON.stringify({ id: 'user-new', email: 'newuser@amafah.com' }),
        });
      }
      if (url.includes('/auth/v1/user') && (method === 'PUT' || method === 'PATCH')) {
        const postData = req.postDataJSON() || {};
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: { 'access-control-allow-origin': '*' },
          body: JSON.stringify({
            id: user?.id || mockAdminProfile.id,
            email: user?.email || mockAdminProfile.email,
            user_metadata: user?.user_metadata || { client_id: 'client-111', role: 'admin' },
            ...postData,
          }),
        });
      }
      if (!user && url.includes('/auth/v1/token') && method === 'POST') {
        // Unauthenticated login attempt with mock credentials
        const postData = req.postDataJSON() || {};
        if (postData.password === 'wrongpassword') {
          return route.fulfill({
            status: 400,
            contentType: 'application/json',
            headers: { 'access-control-allow-origin': '*' },
            body: JSON.stringify({ error: 'invalid_grant', error_description: 'Invalid login credentials', message: 'Invalid login credentials' }),
          });
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: { 'access-control-allow-origin': '*' },
          body: JSON.stringify({
            access_token: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTEiLCJyb2xlIjoiYXV0aGVudGljYXRlZCJ9.fake_sig',
            token_type: 'bearer',
            expires_in: 3600,
            refresh_token: 'fake-refresh-token',
            user: {
              id: mockAdminProfile.id,
              email: mockAdminProfile.email,
              user_metadata: { client_id: mockAdminProfile.client_id, role: mockAdminProfile.role },
              app_metadata: { client_id: mockAdminProfile.client_id, role: mockAdminProfile.role },
            },
          }),
        });
      }
      if (url.includes('/auth/v1/user') && method === 'GET') {
        const u = user || mockAdminProfile;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: { 'access-control-allow-origin': '*' },
          body: JSON.stringify({
            id: u.id,
            email: u.email,
            user_metadata: { client_id: u.client_id, role: u.role },
            app_metadata: { client_id: u.client_id, role: u.role },
          }),
        });
      }
      if (!user) {
        return route.fulfill({
          status: 400,
          contentType: 'application/json',
          headers: { 'access-control-allow-origin': '*' },
          body: JSON.stringify({ error: 'invalid_grant', error_description: 'Invalid login credentials', message: 'Invalid login credentials' }),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { 'access-control-allow-origin': '*' },
        body: JSON.stringify({
          access_token: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTEiLCJyb2xlIjoiYXV0aGVudGljYXRlZCJ9.fake_sig',
          token_type: 'bearer',
          expires_in: 3600,
          refresh_token: 'fake-refresh-token',
          user: {
            id: user.id,
            email: user.email,
            user_metadata: { client_id: user.client_id, role: user.role },
            app_metadata: { client_id: user.client_id, role: user.role },
          },
        }),
      });
    }

    // 2. Supabase REST RPC Endpoints
    if (url.includes('/rest/v1/rpc/claim_bootstrap_admin') || url.includes('/rest/v1/rpc/accept_invitation')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { 'access-control-allow-origin': '*' },
        body: JSON.stringify(user || mockAdminProfile),
      });
    }

    const getRange = (arr) => (arr.length === 0 ? '*/0' : `0-${arr.length - 1}/${arr.length}`);
    const restHeaders = (arr) => ({
      'content-range': getRange(arr),
      'access-control-expose-headers': 'content-range, Content-Range, content-type',
      'access-control-allow-origin': '*',
    });

    // 3. Supabase REST Tables
    if (url.includes('/rest/v1/profiles')) {
      if (method === 'GET') {
        const isSingle = req.headers()['accept']?.includes('vnd.pgrst.object+json');
        if (url.includes('client_id=eq.')) {
          return route.fulfill({
            status: 200,
            contentType: 'application/json',
            headers: restHeaders(teamMembers),
            body: JSON.stringify(teamMembers),
          });
        }
        const profileObj = user || mockAdminProfile;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: {
            'content-range': '0-0/1',
            'access-control-expose-headers': 'content-range, Content-Range, content-type',
            'access-control-allow-origin': '*',
          },
          body: JSON.stringify(isSingle ? profileObj : [profileObj]),
        });
      }
    }

    if (url.includes('/rest/v1/categories')) {
      if (method === 'POST') {
        const postData = req.postDataJSON() || {};
        const catName = postData.name || 'New Cat';
        if (!allCategories.includes(catName)) {
          allCategories.push(catName);
        }
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          headers: restHeaders(allCategories),
          body: JSON.stringify([{ id: 'cat-new', name: catName, client_id: mockClient.id }]),
        });
      }
      const mappedCats = allCategories.map((c, i) => ({ id: `cat-${i + 1}`, name: c, client_id: mockClient.id }));
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: restHeaders(mappedCats),
        body: JSON.stringify(mappedCats),
      });
    }

    if (url.includes('/rest/v1/suppliers')) {
      if (method === 'POST') {
        const postData = req.postDataJSON() || {};
        const newSupp = {
          id: `supp-new-${Date.now()}`,
          client_id: mockClient.id,
          name: postData.name || 'New Supplier',
          phone_number: postData.phone_number || '+971500000000',
          categories: postData.categories || [],
          payment_terms: postData.payment_terms || null,
          is_active: true,
          created_at: new Date().toISOString(),
        };
        currentSuppliers.unshift(newSupp);
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          headers: restHeaders(currentSuppliers),
          body: JSON.stringify([newSupp]),
        });
      }
      if (method === 'PATCH') {
        const postData = req.postDataJSON() || {};
        const idMatch = url.match(/id=eq\.([^&]+)/);
        const suppId = idMatch ? decodeURIComponent(idMatch[1]) : null;
        if (suppId) {
          const s = currentSuppliers.find((x) => x.id === suppId);
          if (s) Object.assign(s, postData);
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: restHeaders(currentSuppliers),
          body: JSON.stringify(currentSuppliers),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: restHeaders(currentSuppliers),
        body: JSON.stringify(currentSuppliers),
      });
    }

    if (url.includes('/rest/v1/rfqs')) {
      if (method === 'GET') {
        const isSingle = req.headers()['accept']?.includes('vnd.pgrst.object+json');
        const match = url.match(/[?&]id=eq\.([^&]+)/);
        if (match) {
          const rfqId = decodeURIComponent(match[1]);
          const found = currentRfqs.find((r) => r.id === rfqId);
          if (found) {
            return route.fulfill({
              status: 200,
              contentType: 'application/json',
              headers: restHeaders([found]),
              body: JSON.stringify(isSingle ? found : [found]),
            });
          }
          return route.fulfill({
            status: 404,
            contentType: 'application/json',
            body: JSON.stringify({ message: 'Not found' }),
          });
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: restHeaders(currentRfqs),
          body: JSON.stringify(currentRfqs),
        });
      }
    }

    if (url.includes('/rest/v1/rfq_suppliers')) {
      const match = url.match(/[?&]rfq_id=eq\.([^&]+)/);
      const rfqId = match ? decodeURIComponent(match[1]) : null;
      const targetRfq = currentRfqs.find((r) => r.id === rfqId) || currentRfqs[0];
      const items = targetRfq?.rfq_suppliers || [];
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: restHeaders(items),
        body: JSON.stringify(items),
      });
    }

    if (url.includes('/rest/v1/quotes')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: restHeaders(quotes),
        body: JSON.stringify(quotes),
      });
    }

    if (url.includes('/rest/v1/rfq_rankings') || url.includes('/rest/v1/ai_quote_ranking')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: restHeaders(ranking ? [ranking] : []),
        body: JSON.stringify(ranking ? [ranking] : []),
      });
    }

    if (url.includes('/rest/v1/flagged_for_review')) {
      const pendingOnly = url.includes('status=eq.pending');
      const filtered = pendingOnly ? currentFlags.filter((f) => f.status === 'pending') : currentFlags;
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: restHeaders(filtered),
        body: JSON.stringify(filtered),
      });
    }

    if (url.includes('/rest/v1/message_log')) {
      let filtered = [...currentMessages];
      const suppMatch = url.match(/supplier_id=eq\.([^&]+)/);
      if (suppMatch) {
        filtered = filtered.filter((m) => m.supplier_id === decodeURIComponent(suppMatch[1]));
      }
      const rfqMatch = url.match(/related_rfq_id=eq\.([^&]+)/);
      if (rfqMatch) {
        filtered = filtered.filter((m) => m.related_rfq_id === decodeURIComponent(rfqMatch[1]));
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: restHeaders(filtered),
        body: JSON.stringify(filtered),
      });
    }

    if (url.includes('/rest/v1/invite_tokens')) {
      if (method === 'PATCH') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([{ status: 'claimed' }]),
        });
      }
      const isSingle = req.headers()['accept']?.includes('vnd.pgrst.object+json');
      if (isSingle) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(currentTokens[0] || null),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(currentTokens),
      });
    }

    // 4. FastAPI Backend Endpoints
    if (url.includes('/rfq/create')) {
      const postData = req.postDataJSON() || {};
      const newRfq = {
        id: `rfq-created-${Date.now()}`,
        client_id: mockClient.id,
        product_name: postData.product_name,
        category: postData.category,
        specs: postData.specs,
        quantity: postData.quantity,
        deadline_hours: postData.deadline_hours || 24,
        status: 'active',
        created_at: new Date().toISOString(),
        rfq_suppliers: [
          {
            id: `rs-${Date.now()}`,
            supplier_id: 'supp-1',
            status: 'sent',
            sent_at: new Date().toISOString(),
            reminder_count: 0,
            suppliers: { name: 'Al Noor Hardware & Tools', phone: '+971501112233' },
          },
        ],
        quotes: [],
      };
      currentRfqs.unshift(newRfq);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          rfq_id: newRfq.id,
          matched_suppliers_count: 1,
          suppliers: [{ id: 'supp-1', name: 'Al Noor Hardware & Tools', phone: '+971501112233' }],
        }),
      });
    }

    if (url.includes('/rfq/bulk-create')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          created_count: 2,
          rfqs: [
            { rfq_id: 'rfq-bulk-1', product_name: 'Copper Pipe 1/2', matched_suppliers_count: 2 },
            { rfq_id: 'rfq-bulk-2', product_name: 'Brass Ball Valve', matched_suppliers_count: 1 },
          ],
          failed_rows: [],
        }),
      });
    }

    if (url.match(/\/rfq\/[^/]+\/rank/)) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ranked', ranking }),
      });
    }

    if (url.match(/\/rfq\/[^/]+\/close/)) {
      const match = url.match(/\/rfq\/([^/?]+)\/close/);
      const rfqId = match ? match[1] : null;
      const found = currentRfqs.find((r) => r.id === rfqId);
      if (found) found.status = 'closed';
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'closed', rfq_id: rfqId }),
      });
    }

    if (url.match(/\/rfq\/[^/]+\/activity/)) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ activity }),
      });
    }

    if (url.match(/\/flags\/[^/]+\/resolve/)) {
      const match = url.match(/\/flags\/([^/]+)\/resolve/);
      const flagId = match ? match[1] : null;
      const found = currentFlags.find((f) => f.id === flagId);
      if (found) {
        found.status = 'resolved';
        found.resolved_at = new Date().toISOString();
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'resolved', flag_id: flagId }),
      });
    }

    if (url.match(/\/flags\/[^/]+\/respond/)) {
      const match = url.match(/\/flags\/([^/]+)\/respond/);
      const flagId = match ? match[1] : null;
      const found = currentFlags.find((f) => f.id === flagId);
      if (found) {
        found.status = 'resolved';
        found.resolved_at = new Date().toISOString();
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'resolved', flag_id: flagId, action: 'send_procurement_message' }),
      });
    }

    if (url.includes('/reports/daily')) {
      if (url.includes('nodata')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'no_data', message: 'No RFQs found for date' }),
        });
      }
      // Return a simulated docx binary
      const fakeDocxBuffer = Buffer.from('PK\x03\x04MockDocxFileContentForTesting');
      return route.fulfill({
        status: 200,
        contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        headers: {
          'Content-Disposition': 'attachment; filename="Daily_Procurement_Report_2026-09-12.docx"',
        },
        body: fakeDocxBuffer,
      });
    }

    if (url.includes('/admin/invite')) {
      const postData = req.postDataJSON() || {};
      const newTok = {
        id: `token-${Date.now()}`,
        client_id: mockClient.id,
        token: `mock-invite-${Date.now()}`,
        role: postData.role || 'member',
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 7 * 86400000).toISOString(),
        used_at: null,
      };
      currentTokens.unshift(newTok);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'created', token: newTok.token }),
      });
    }

    if (url.includes('/admin/delivery-issues')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockDeliveryIssues),
      });
    }

    if (url.match(/\/invite\/[^/?]+/)) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'valid', client_id: mockClient.id, role: 'member' }),
      });
    }

    // Pass through static assets, HTML, JS, CSS
    return route.continue();
  });
}
