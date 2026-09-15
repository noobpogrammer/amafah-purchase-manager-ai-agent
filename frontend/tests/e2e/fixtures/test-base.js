/**
 * Base Playwright Test Fixture with Global Crash Guard and Auth helpers
 */

import { test as base, expect } from '@playwright/test';
import { setupAppMocks } from './api-mocks.js';
import { mockAdminProfile, mockMemberProfile } from './data.js';

export const test = base.extend({
  // Global Crash Guard fixture
  page: async ({ page }, applyFixture, testInfo) => {
    const pageErrors = [];
    const consoleErrors = [];
    const allowedConsolePatterns = [];

    // Attach listeners
    page.on('pageerror', (exception) => {
      pageErrors.push(exception.message || String(exception));
    });

    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text();
        if (
          text.includes('ERR_CONNECTION_CLOSED') ||
          text.includes('ERR_ABORTED') ||
          text.includes('favicon.ico') ||
          text.includes('WebSocket')
        ) {
          return;
        }
        const isAllowed = allowedConsolePatterns.some((pattern) =>
          typeof pattern === 'string' ? text.includes(pattern) : pattern.test(text)
        );
        if (!isAllowed) {
          consoleErrors.push(text);
        }
      }
    });

    // Provide helper on page to allow expected errors in specific negative tests
    page.allowConsoleError = (pattern) => {
      allowedConsolePatterns.push(pattern);
    };

    // Provide assert helper
    page.assertNoUnexpectedBrowserErrors = () => {
      expect(pageErrors, 'Unexpected browser pageerror(s) occurred').toEqual([]);
      expect(consoleErrors, 'Unexpected browser console.error(s) occurred').toEqual([]);
    };

    await applyFixture(page);

    // Automatic check at end of test unless test was expected to fail
    if (testInfo.status === testInfo.expectedStatus) {
      page.assertNoUnexpectedBrowserErrors();
    }
  },
});

export { expect };

/**
 * Helper to initialize page with authenticated session
 */
export async function setupAuthenticatedSession(page, role = 'admin', options = {}) {
  const user = role === 'admin' ? mockAdminProfile : mockMemberProfile;

  // Pre-seed localStorage with fake supabase session so getSession returns immediately
  await page.addInitScript((userData) => {
    const sessionPayload = {
      access_token: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyLTEiLCJyb2xlIjoiYXV0aGVudGljYXRlZCJ9.fake_sig',
      token_type: 'bearer',
      expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) + 3600,
      refresh_token: 'fake-refresh-token',
      user: {
        id: userData.id,
        email: userData.email,
        user_metadata: { client_id: userData.client_id, role: userData.role },
        app_metadata: { client_id: userData.client_id, role: userData.role },
      },
    };
    // Match any Supabase project storage key format
    window.localStorage.setItem('sb-ryixfctiffllmklekbwk-auth-token', JSON.stringify(sessionPayload));
    window.localStorage.setItem('sb-localhost-auth-token', JSON.stringify(sessionPayload));
    window.localStorage.setItem('sb-127-0-0-1-auth-token', JSON.stringify(sessionPayload));
  }, user);

  await setupAppMocks(page, { userRole: role, user, ...options });
}

/**
 * Helper to initialize unauthenticated page
 */
export async function setupUnauthenticatedSession(page, options = {}) {
  await setupAppMocks(page, { userRole: 'unauthenticated', user: null, ...options });
}
