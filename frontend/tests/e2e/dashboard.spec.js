import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Procurement Dashboard View', () => {
  test('should navigate to create_rfq tab when clicking Create New RFQ button', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('main').getByRole('button', { name: 'Create request' }).click();
    await expect(page.getByRole('heading', { name: 'Launch Request for Quote (RFQ)' })).toBeVisible();
  });

  test('should show pending flags banner and navigate to flags view when clicked', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    const alertBanner = page.locator('.alert-banner.warning-banner');
    await expect(alertBanner).toBeVisible();
    await expect(alertBanner).toContainText('needs your attention');

    await alertBanner.getByRole('button', { name: /Review Items/i }).click();
    await expect(page.getByRole('heading', { name: 'Agent Attention & Human Review' })).toBeVisible();
  });

  test('should navigate to correct views when clicking metric cards', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    const metricCards = [
      { label: 'Active RFQs', heading: 'RFQs & Live Tracking' },
      { label: 'Supplier network', heading: 'Supplier Directory' },
      { label: 'Quotes Received', heading: 'Quotes Comparison & AI Recommendation Report' },
      { label: 'Needs attention', heading: 'Agent Attention & Human Review' },
    ];

    for (const card of metricCards) {
      // Go to dashboard first
      await page.locator('.brand-logo').click();
      await expect(page.getByRole('heading', { name: 'Procurement overview' })).toBeVisible();

      // Click specific metric card
      await page.locator('.metric-card').filter({ hasText: card.label }).click();
      await expect(page.getByRole('heading', { name: card.heading })).toBeVisible();
    }
  });

  test('should navigate to correct views when clicking demo workflow steps', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    const steps = [
      { step: 'Manage your suppliers', heading: 'Supplier Directory' },
      { step: 'Send a buying request', heading: 'Launch Request for Quote (RFQ)' },
      { step: 'Follow each request', heading: 'RFQs & Live Tracking' },
      { step: 'Compare your offers', heading: 'Quotes Comparison & AI Recommendation Report' },
    ];

    for (const s of steps) {
      await page.locator('.brand-logo').click();
      await expect(page.getByRole('heading', { name: 'Procurement overview' })).toBeVisible();

      await page.locator('.workflow-step').filter({ hasText: s.step }).click();
      await expect(page.getByRole('heading', { name: s.heading })).toBeVisible();
    }
  });

  test('should navigate to conversations view when clicking View All Logs', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'View conversations' }).click();
    await expect(page.getByRole('heading', { name: 'WhatsApp Audit Logs & Conversation Transcripts' })).toBeVisible();
  });

  test('should render recent messages list or empty state correctly', async ({ page }) => {
    // 1. With messages
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    const feed = page.locator('.activity-feed');
    await expect(feed.locator('.activity-item')).toHaveCount(2);

    // 2. With zero messages
    await setupAuthenticatedSession(page, 'admin', { messages: [] });
    await page.goto('/');
    await expect(page.getByText('No WhatsApp activity logged yet.')).toBeVisible();
  });
});
