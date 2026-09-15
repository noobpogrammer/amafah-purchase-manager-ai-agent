import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Navbar & Navigation Controls', () => {
  test('should navigate to dashboard view when clicking brand logo', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    // Switch to suppliers tab first
    await page.getByRole('button', { name: 'Suppliers' }).click();
    await expect(page.getByRole('heading', { name: 'Supplier Directory' })).toBeVisible();

    // Click Brand logo
    await page.locator('.brand-logo').click();
    await expect(page.getByRole('heading', { name: 'Procurement Command Center' })).toBeVisible();
  });

  test('should switch active tab and view when clicking navigation items', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    const tabs = [
      { name: 'Suppliers', heading: 'Supplier Directory', exact: false },
      { name: 'Create RFQ', heading: 'Launch Request for Quote (RFQ)', exact: false },
      { name: 'RFQs & Tracking', heading: 'RFQs & Live Tracking', exact: false },
      { name: 'Quotes & AI Ranking', heading: 'Quotes Comparison & AI Recommendation Report', exact: false },
      { name: 'Daily Report', heading: 'Daily Procurement Report', exact: true },
      { name: 'WhatsApp Logs', heading: 'WhatsApp Audit Logs & Conversation Transcripts', exact: false },
      { name: 'Agent Attention', heading: 'Agent Attention & Human Review', exact: false },
      { name: 'Dashboard', heading: 'Procurement Command Center', exact: false },
    ];

    for (const tab of tabs) {
      const btn = page.locator('.nav-menu').getByRole('button', { name: new RegExp(tab.name, 'i') });
      await btn.click();
      await expect(page.getByRole('heading', { name: tab.heading, exact: tab.exact })).toBeVisible();
      await expect(btn).toHaveClass(/active/);
    }
  });

  test('should render dynamic pending flag badge count for 0, 1, and >1', async ({ page }) => {
    // 1. With 1 pending flag (default mock data has 1 pending flag)
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    const flagBtn = page.locator('.nav-menu').getByRole('button', { name: /Agent Attention/ });
    await expect(flagBtn.locator('.nav-badge')).toHaveText('1');

    // 2. With 0 flags
    await page.route('**/rest/v1/flagged_for_review*', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { 'content-range': '*/0', 'access-control-allow-origin': '*' },
        body: JSON.stringify([]),
      });
    });
    await page.reload();
    await expect(flagBtn.locator('.nav-badge')).not.toBeVisible();

    // 3. With 3 flags
    const multiFlags = [
      { id: 'f1', client_id: 'client-111', status: 'pending', reason: 'r1', raw_message: 'm1' },
      { id: 'f2', client_id: 'client-111', status: 'pending', reason: 'r2', raw_message: 'm2' },
      { id: 'f3', client_id: 'client-111', status: 'pending', reason: 'r3', raw_message: 'm3' },
    ];
    await page.route('**/rest/v1/flagged_for_review*', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        headers: { 'content-range': '0-2/3', 'access-control-allow-origin': '*' },
        body: JSON.stringify(multiFlags),
      });
    });
    await page.reload();
    await expect(flagBtn.locator('.nav-badge')).toHaveText('3');
  });

  test('should navigate to team settings page', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Team' }).click();
    await expect(page).toHaveURL('/team');
    await expect(page.getByRole('heading', { name: 'Team & invites' })).toBeVisible();
  });

  test('should sign out user and redirect to login page', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
  });
});
