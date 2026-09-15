import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Create RFQ — Single Mode', () => {
  test('should toggle between single RFQ mode and bulk CSV mode', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Create RFQ' }).click();
    await expect(page.getByRole('heading', { name: 'Launch Request for Quote (RFQ)' })).toBeVisible();

    // Toggle to Bulk
    await page.getByRole('button', { name: 'Upload Material Requisition' }).click();
    await expect(page.getByRole('button', { name: 'Switch to Single RFQ' })).toBeVisible();
    await expect(page.locator('#bulk-rfq-input')).toBeVisible();

    // Toggle back to Single
    await page.getByRole('button', { name: 'Switch to Single RFQ' }).click();
    await expect(page.getByRole('button', { name: 'Upload Material Requisition' })).toBeVisible();
    await expect(page.locator('input[placeholder*="Copper Water Pipe"]')).toBeVisible();
  });

  test('should submit single RFQ successfully and render matched suppliers card', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Create RFQ' }).click();

    // Fill required fields
    await page.locator('input[placeholder*="Copper Water Pipe"]').fill('Galvanized Steel Pipe 2 Inch');
    await page.locator('input[placeholder*="Type L, ASTM B88"]').fill('Schedule 40, threaded ends, 6 meter lengths');
    await page.locator('input[placeholder="e.g. 50"]').fill('100');
    await page.locator('input[placeholder="e.g. 12.50"]').fill('65.00');
    await page.locator('input[placeholder="e.g. 50.00"]').fill('55.00');
    await page.locator('input[placeholder="e.g. 60.00"]').fill('70.00');

    // Submit
    const submitBtn = page.getByRole('button', { name: 'Submit & Match Suppliers' });
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    // Result card appears
    const resultCard = page.locator('.result-card');
    await expect(resultCard).toBeVisible();
    await expect(resultCard.getByText('RFQ Created & Suppliers Matched')).toBeVisible();
    await expect(resultCard.getByText('Al Noor Hardware & Tools')).toBeVisible();

    // Click Track Live RFQ Status
    await resultCard.getByRole('button', { name: /Track Live RFQ Status/i }).click();
    await expect(page.getByRole('heading', { name: 'RFQs & Live Tracking' })).toBeVisible();
  });

  test('should validate acceptable price min and max constraints', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Create RFQ' }).click();

    await page.locator('input[placeholder*="Copper Water Pipe"]').fill('Brass Fittings 1 Inch');
    await page.locator('input[placeholder*="Type L, ASTM B88"]').fill('Standard compression fittings');

    // Set Min > Max
    await page.locator('input[placeholder="e.g. 50.00"]').fill('80.00');
    await page.locator('input[placeholder="e.g. 60.00"]').fill('40.00');

    await page.getByRole('button', { name: 'Submit & Match Suppliers' }).click();
    await expect(page.locator('.error-alert')).toContainText('Acceptable Price Min cannot be greater than Acceptable Price Max');
  });

  test('should create and auto-select custom category inline', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Create RFQ' }).click();

    const catSelect = page.locator('select.input-field').first();
    await catSelect.selectOption('__CREATE_CUSTOM__');

    const customInput = page.locator('input[placeholder="Type custom category name..."]');
    await expect(customInput).toBeVisible();
    await customInput.fill('Fire & Safety');
    await page.getByRole('button', { name: 'Add' }).click();

    await expect(customInput).not.toBeVisible();
    await expect(page.getByText("Matching suppliers with 'Fire & Safety' tag")).toBeVisible();
  });

  test('should render warning banner and suppliers link when zero suppliers match', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    // Mock create response returning 0 matched suppliers
    await page.route('**/rfq/create', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'warning',
          rfq_id: 'rfq-nomatch-1',
          matched_suppliers_count: 0,
          suppliers: [],
          message: 'No active suppliers were found with category Tools.',
        }),
      });
    });

    await page.getByRole('button', { name: 'Create RFQ' }).click();
    await page.locator('input[placeholder*="Copper Water Pipe"]').fill('Specialized Hydraulic Press');
    await page.locator('input[placeholder*="Type L, ASTM B88"]').fill('50 Ton capacity');
    await page.getByRole('button', { name: 'Submit & Match Suppliers' }).click();

    await expect(page.getByText('No Matching Suppliers Found')).toBeVisible();
    await page.getByRole('button', { name: 'Add Category Suppliers' }).click();
    await expect(page.getByRole('heading', { name: 'Supplier Directory' })).toBeVisible();
  });
});
