import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Procurement Dashboard View', () => {
  test('should navigate to create_rfq tab when clicking Create New RFQ button', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Create New RFQ' }).click();
    await expect(page.getByRole('heading', { name: 'Launch Request for Quote (RFQ)' })).toBeVisible();
  });

  test('should show pending flags banner and navigate to flags view when clicked', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    const alertBanner = page.locator('.alert-banner.warning-banner');
    await expect(alertBanner).toBeVisible();
    await expect(alertBanner).toContainText('Require Human Review');

    await alertBanner.getByRole('button', { name: /Review Items/i }).click();
    await expect(page.getByRole('heading', { name: 'Agent Attention & Human Review' })).toBeVisible();
  });

  test('should navigate to correct views when clicking metric cards', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    const metricCards = [
      { label: 'Active RFQs', heading: 'RFQs & Live Tracking' },
      { label: 'Registered Suppliers', heading: 'Supplier Directory' },
      { label: 'Quotes Received', heading: 'Quotes Comparison & AI Recommendation Report' },
      { label: 'Pending Agent Flags', heading: 'Agent Attention & Human Review' },
    ];

    for (const card of metricCards) {
      // Go to dashboard first
      await page.locator('.brand-logo').click();
      await expect(page.getByRole('heading', { name: 'Procurement Command Center' })).toBeVisible();

      // Click specific metric card
      await page.locator('.metric-card').filter({ hasText: card.label }).click();
      await expect(page.getByRole('heading', { name: card.heading })).toBeVisible();
    }
  });

  test('should navigate to correct views when clicking demo workflow steps', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    const steps = [
      { step: 'Manage Suppliers', heading: 'Supplier Directory' },
      { step: 'Launch RFQ & Auto-Match', heading: 'Launch Request for Quote (RFQ)' },
      { step: 'Track Live Status', heading: 'RFQs & Live Tracking' },
      { step: 'AI Quote Ranking Report', heading: 'Quotes Comparison & AI Recommendation Report' },
    ];

    for (const s of steps) {
      await page.locator('.brand-logo').click();
      await expect(page.getByRole('heading', { name: 'Procurement Command Center' })).toBeVisible();

      await page.locator('.workflow-step').filter({ hasText: s.step }).click();
      await expect(page.getByRole('heading', { name: s.heading })).toBeVisible();
    }
  });

  test('should navigate to conversations view when clicking View All Logs', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'View All Logs' }).click();
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
