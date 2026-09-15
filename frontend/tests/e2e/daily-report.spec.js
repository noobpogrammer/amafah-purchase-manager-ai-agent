import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Daily Procurement Report', () => {
  test('should allow selecting report date', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Daily Report' }).click();
    await expect(page.getByRole('heading', { name: 'Daily Procurement Report', exact: true })).toBeVisible();

    const dateInput = page.locator('#report-date-input');
    await expect(dateInput).toBeVisible();
    await dateInput.fill('2026-09-12');
    await expect(dateInput).toHaveValue('2026-09-12');
  });

  test('should download docx report file on successful submission', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Daily Report' }).click();

    const dateInput = page.locator('#report-date-input');
    await dateInput.fill('2026-09-12');

    // Setup download listener
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /Download Word Document/i }).click();

    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain('.docx');

    await expect(page.locator('.success-banner')).toBeVisible();
    await expect(page.locator('.success-banner')).toContainText('has been generated and downloaded');
  });

  test('should display notice banner when no data exists for date', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Daily Report' }).click();

    // Fill date that returns nodata in mock
    const dateInput = page.locator('#report-date-input');
    await dateInput.fill('2026-01-01');

    // Override route for this date
    await page.route('**/reports/daily?date=2026-01-01', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'no_data', message: 'No RFQs were created on this date' }),
      });
    });

    await page.getByRole('button', { name: /Download Word Document/i }).click();

    await expect(page.locator('.info-banner')).toBeVisible();
    await expect(page.locator('.info-banner')).toContainText('No RFQs were created on this date');
  });

  test('should display error alert when backend report generation fails', async ({ page }) => {
    page.allowConsoleError(/Template generation rendering error|Failed to generate report|Failed to load resource|500/);
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Daily Report' }).click();

    // Force error response
    await page.route('**/reports/daily*', async (route) => {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Template generation rendering error' }),
      });
    });

    await page.getByRole('button', { name: /Download Word Document/i }).click();

    await expect(page.locator('.error-banner')).toBeVisible();
    await expect(page.locator('.error-banner')).toContainText('Template generation rendering error');
  });
});
