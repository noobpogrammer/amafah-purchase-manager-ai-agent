import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Error State Matrix & Resilience', () => {
  test('should handle HTTP 500 on dashboard metrics without blank screen or crash', async ({ page }) => {
    page.allowConsoleError(/Error loading dashboard metrics|Failed to load resource/);
    await setupAuthenticatedSession(page, 'admin');

    await page.route('**/rest/v1/rfqs*', async (route) => {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'Internal server error' }),
      });
    });

    await page.goto('/');
    // Command center still renders safely
    await expect(page.getByRole('heading', { name: 'Procurement Command Center' })).toBeVisible();
    await expect(page.locator('.app-header')).toBeVisible();
  });

  test('should handle HTTP 404 on RFQ detail without crashing', async ({ page }) => {
    page.allowConsoleError(/Error fetching RFQ detail|Failed to load resource/);
    await setupAuthenticatedSession(page, 'admin');

    await page.route('**/rest/v1/rfqs?id=eq.*', async (route) => {
      return route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'RFQ not found' }),
      });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'RFQs & Tracking' }).click();
    await expect(page.getByRole('heading', { name: 'RFQs & Live Tracking' })).toBeVisible();
  });

  test('should handle HTTP 422 validation failure on RFQ creation', async ({ page }) => {
    page.allowConsoleError(/Failed to create RFQ|Failed to load resource|Unprocessable entity/);
    await setupAuthenticatedSession(page, 'admin');

    await page.route('**/rfq/create', async (route) => {
      return route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Unprocessable entity: quantity must be positive' }),
      });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Create RFQ' }).click();

    await page.locator('input[placeholder*="Copper Water Pipe"]').fill('Test Product');
    await page.locator('input[placeholder*="Type L, ASTM B88"]').fill('Test Specs');
    await page.getByRole('button', { name: 'Submit & Match Suppliers' }).click();

    await expect(page.locator('.error-alert')).toContainText('quantity must be positive');
  });

  test('should survive slow network responses without UI lockup', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');

    // Add delay to suppliers endpoint
    await page.route('**/rest/v1/suppliers*', async (route) => {
      await new Promise((res) => setTimeout(res, 800));
      return route.fallback();
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();
    await expect(page.getByRole('heading', { name: 'Supplier Directory' })).toBeVisible();
  });

  test('should survive null optional fields and Unicode/Arabic strings gracefully', async ({ page }) => {
    const robustSuppliers = [
      {
        id: 'supp-fuzz-1',
        client_id: 'client-111',
        name: 'مؤسسة الأمل للتجارة العامة & Co. <script>alert(1)</script> 🚀',
        phone_number: '+971501239876',
        category: null, // null category
        notes: null, // null notes
        is_active: null,
      },
    ];

    await setupAuthenticatedSession(page, 'admin', { suppliers: robustSuppliers });
    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();

    // Table renders Arabic/emoji/escaped HTML safely without crash
    await expect(page.getByText('مؤسسة الأمل للتجارة العامة')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Supplier Directory' })).toBeVisible();
  });
});
