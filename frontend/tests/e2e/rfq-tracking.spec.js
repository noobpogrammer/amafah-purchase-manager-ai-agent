import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('RFQs & Live Tracking', () => {
  test('should toggle live polling state', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();
    await expect(page.getByRole('heading', { name: 'RFQs & Live Tracking' })).toBeVisible();

    const pollingBtn = page.getByRole('button', { name: /Live Polling/i });
    await expect(pollingBtn).toContainText('Live Polling ON (5s)');

    // Toggle OFF
    await pollingBtn.click();
    await expect(pollingBtn).toContainText('Live Polling OFF');

    // Toggle ON
    await pollingBtn.click();
    await expect(pollingBtn).toContainText('Live Polling ON (5s)');
  });

  test('should filter RFQs by All, Active, and Closed tabs', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();

    // Default All: 2 items
    const rfqList = page.locator('.rfq-list');
    await expect(rfqList.locator('.rfq-item-card')).toHaveCount(2);

    // Filter Active: 1 item (Copper Water Pipe)
    await page.locator('.segmented-control').getByRole('button', { name: 'Active' }).click();
    await expect(rfqList.locator('.rfq-item-card')).toHaveCount(1);
    await expect(rfqList.getByText('Copper Water Pipe 1/2 Inch')).toBeVisible();

    // Filter Closed: 1 item (Heavy Duty Power Drill)
    await page.locator('.segmented-control').getByRole('button', { name: 'Closed' }).click();
    await expect(rfqList.locator('.rfq-item-card')).toHaveCount(1);
    await expect(rfqList.getByText('Heavy Duty Power Drill 800W')).toBeVisible();
  });

  test('should load RFQ details when clicking sidebar card', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();

    // Click on active RFQ
    await page.locator('.rfq-item-card').filter({ hasText: 'Copper Water Pipe' }).click();

    // Check header specs
    await expect(page.locator('.rfq-product-title')).toHaveText('Copper Water Pipe 1/2 Inch');
    await expect(page.locator('.data-table').getByText('Gulf Plumbing Solutions')).toBeVisible();

    // Check quotes summary
    await expect(page.locator('.quote-summary-card')).toHaveCount(2);
    await expect(page.locator('.quote-summary-card').first().locator('.price-tag')).toHaveText('AED 44.5');
  });

  test('should navigate to Quotes Report view from detail header', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();

    await page.getByRole('button', { name: 'Quotes Report' }).click();
    await expect(page.getByRole('heading', { name: 'Quotes Comparison & AI Recommendation Report' })).toBeVisible();
  });

  test('should trigger AI ranking and refresh data', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();

    const rankBtn = page.getByRole('button', { name: 'Trigger AI Ranking' });
    await expect(rankBtn).toBeVisible();
    await rankBtn.click();

    // Triggers and navigates to quotes_report
    await expect(page.getByRole('heading', { name: 'Quotes Comparison & AI Recommendation Report' })).toBeVisible();
  });

  test('should show confirmation prompt before closing RFQ', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();

    // Click Close RFQ
    await page.getByRole('button', { name: 'Close RFQ' }).click();
    await expect(page.getByText('Close RFQ?')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Yes, Close' })).toBeVisible();

    // Click Cancel
    await page.getByRole('button', { name: 'Cancel' }).click();
    await expect(page.getByRole('button', { name: 'Close RFQ' })).toBeVisible();
  });

  test('should close RFQ when confirming close action', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();

    await page.getByRole('button', { name: 'Close RFQ' }).click();
    await page.getByRole('button', { name: 'Yes, Close' }).click();

    // Once closed, Close RFQ button disappears
    await expect(page.getByRole('button', { name: 'Close RFQ' })).not.toBeVisible();
  });
});
