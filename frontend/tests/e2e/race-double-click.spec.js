import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Double-Click & Race Protection', () => {
  test('should disable button or prevent duplicate submission on Create Supplier', async ({ page }) => {
    let callCount = 0;
    await setupAuthenticatedSession(page, 'admin');

    await page.route('**/rest/v1/suppliers*', async (route) => {
      if (route.request().method() === 'POST') {
        callCount++;
        await new Promise((res) => setTimeout(res, 200));
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify([{ id: 'supp-race-1', name: 'Race Test' }]),
        });
      }
      return route.fallback();
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();
    await page.getByRole('button', { name: 'Add New Supplier' }).click();

    const modal = page.locator('.modal-card');
    await modal.locator('input[placeholder*="Al Noor Hardware"]').fill('Race Test');
    await modal.locator('input[placeholder*="+97150"]').fill('+971501119999');

    const submitBtn = modal.getByRole('button', { name: 'Create Supplier' });

    // Click submit
    await submitBtn.click();
    await expect(modal).not.toBeVisible();
    expect(callCount).toBe(1);
  });

  test('should prevent duplicate submission on Single RFQ create', async ({ page }) => {
    let createCount = 0;
    await setupAuthenticatedSession(page, 'admin');

    await page.route('**/rfq/create', async (route) => {
      createCount++;
      await new Promise((res) => setTimeout(res, 200));
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', rfq_id: 'rfq-race-1', matched_suppliers_count: 1, suppliers: [] }),
      });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Create RFQ' }).click();

    await page.locator('input[placeholder*="Copper Water Pipe"]').fill('Race Test Pipe');
    await page.locator('input[placeholder*="Type L, ASTM B88"]').fill('Race Specs');

    const submitBtn = page.getByRole('button', { name: 'Submit & Match Suppliers' });
    await submitBtn.click();

    await expect(page.locator('.result-card')).toBeVisible();
    expect(createCount).toBe(1);
  });

  test('should prevent runaway duplicate calls on Close RFQ', async ({ page }) => {
    let closeCount = 0;
    await setupAuthenticatedSession(page, 'admin');

    await page.route('**/rfq/*/close*', async (route) => {
      closeCount++;
      await new Promise((res) => setTimeout(res, 200));
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'closed' }),
      });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();

    await page.getByRole('button', { name: 'Close RFQ' }).click();
    const yesCloseBtn = page.getByRole('button', { name: 'Yes, Close' });

    await yesCloseBtn.click();
    await expect(page.getByRole('button', { name: 'Close RFQ' })).not.toBeVisible();
    expect(closeCount).toBe(1);
  });
});
