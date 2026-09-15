import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('RFQ Admin Activity & Audit Log', () => {
  test('should render Activity & Audit tab for admin and load timeline events', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();
    await expect(page.getByRole('heading', { name: 'RFQs & Live Tracking' })).toBeVisible();

    const activityTabBtn = page.getByRole('button', { name: /Activity & Audit Log/i });
    await expect(activityTabBtn).toBeVisible();
    await activityTabBtn.click();

    // Verify timeline container and header
    await expect(page.getByRole('heading', { name: 'RFQ Activity & Decision Audit Timeline' })).toBeVisible();

    // Verify timeline events rendered
    const timeline = page.locator('.activity-timeline');
    await expect(timeline.locator('.timeline-item')).toHaveCount(3);
    await expect(page.getByText('Outbound RFQ Broadcast Sent')).toBeVisible();
    await expect(page.getByText('Supplier Quote Recorded (Standard Mueller)')).toBeVisible();
    await expect(page.getByText('Autonomous Acceptance Rejected by Policy Validator')).toBeVisible();
  });

  test('should hide Activity & Audit tab for regular member role', async ({ page }) => {
    await setupAuthenticatedSession(page, 'member');
    await page.goto('/');

    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();
    await expect(page.getByRole('heading', { name: 'RFQs & Live Tracking' })).toBeVisible();

    // Member must NOT see the Admin Activity & Audit Log tab
    await expect(page.getByRole('button', { name: /Activity & Audit Log/i })).not.toBeVisible();
  });

  test('should expand and collapse technical JSON details on timeline events', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();
    await page.getByRole('button', { name: /Activity & Audit Log/i }).click();

    // Find first toggle button
    const toggleBtn = page.locator('.timeline-item').first().getByRole('button', { name: /Technical Details/i });
    await expect(toggleBtn).toBeVisible();
    await expect(toggleBtn).toContainText('View Technical Details');

    // Click to expand
    await toggleBtn.click();
    await expect(toggleBtn).toContainText('Hide Technical Details');
    await expect(page.locator('.timeline-item pre').first()).toBeVisible();
    await expect(page.locator('.timeline-item pre').first()).toContainText('delivery_status');

    // Click to collapse
    await toggleBtn.click();
    await expect(toggleBtn).toContainText('View Technical Details');
    await expect(page.locator('.timeline-item pre')).not.toBeVisible();
  });

  test('should refresh activity timeline when clicking Refresh button', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();
    await page.getByRole('button', { name: /Activity & Audit Log/i }).click();

    const refreshBtn = page.locator('.card-header').getByRole('button', { name: /Refresh/i });
    await expect(refreshBtn).toBeVisible();
    await refreshBtn.click();
    await expect(page.locator('.activity-timeline')).toBeVisible();
  });

  test('should handle activity API 403 or 500 error gracefully without crashing', async ({ page }) => {
    page.allowConsoleError(/Admin permission required|Failed to fetch activity|Failed to load resource|Error fetching RFQ activity/);
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();

    // Mock activity endpoint 403
    await page.route('**/rfq/*/activity', async (route) => {
      return route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Admin permission required to access activity timeline' }),
      });
    });

    await page.getByRole('button', { name: /Activity & Audit Log/i }).click();
    await expect(page.locator('.msg.error')).toBeVisible();
    await expect(page.locator('.msg.error')).toContainText('Admin permission required');
  });
});
