import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Supplier Directory', () => {
  test('should open add supplier modal and create new supplier successfully', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Suppliers' }).click();
    await expect(page.getByRole('heading', { name: 'Supplier Directory' })).toBeVisible();

    // Click Add New Supplier
    await page.getByRole('button', { name: 'Add New Supplier' }).click();
    const modal = page.locator('.modal-card');
    await expect(modal).toBeVisible();
    await expect(modal.getByRole('heading', { name: 'Add New Supplier' })).toBeVisible();

    // Fill form
    await modal.locator('input[placeholder*="Al Noor Hardware"]').fill('Apex Fasteners LLC');
    await modal.locator('input[placeholder*="+97150"]').fill('+971509998888');

    // Select categories (toggle Hardware & Tools)
    await modal.locator('.category-toggle-chip').filter({ hasText: 'Tools' }).click();

    // Fill optional notes
    await modal.locator('textarea').fill('Stainless steel nuts, bolts and fasteners supplier');

    // Submit
    await modal.getByRole('button', { name: 'Create Supplier' }).click();
    await expect(modal).not.toBeVisible();

    // Verify added to table
    await expect(page.getByText('Apex Fasteners LLC')).toBeVisible();
  });

  test('should render empty state and allow adding first supplier', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin', { suppliers: [] });
    await page.goto('/');

    await page.getByRole('button', { name: 'Suppliers' }).click();
    await expect(page.getByText('No Suppliers Found')).toBeVisible();

    // Click Add First Supplier
    await page.getByRole('button', { name: 'Add First Supplier' }).click();
    const modal = page.locator('.modal-card');
    await expect(modal).toBeVisible();
    await modal.getByRole('button', { name: 'Cancel' }).click();
    await expect(modal).not.toBeVisible();
  });

  test('should filter suppliers by name or phone number', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();

    const searchInput = page.locator('input[placeholder*="Search by supplier name"]');

    // Search by name
    await searchInput.fill('Noor');
    await expect(page.getByText('Al Noor Hardware & Tools')).toBeVisible();
    await expect(page.getByText('Emirates Electrical Supplies')).not.toBeVisible();

    // Search by phone
    await searchInput.fill('223344');
    await expect(page.getByText('Emirates Electrical Supplies')).toBeVisible();
    await expect(page.getByText('Al Noor Hardware & Tools')).not.toBeVisible();

    // Clear search
    await searchInput.fill('');
    await expect(page.getByText('Al Noor Hardware & Tools')).toBeVisible();
    await expect(page.getByText('Emirates Electrical Supplies')).toBeVisible();
  });

  test('should filter suppliers by category dropdown', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();

    const catSelect = page.locator('.filter-select-wrap select');

    // Filter by Electrical
    await catSelect.selectOption('Electrical');
    await expect(page.getByText('Emirates Electrical Supplies')).toBeVisible();
    await expect(page.getByText('Al Noor Hardware & Tools')).not.toBeVisible();

    // Switch back to All Categories
    await catSelect.selectOption('');
    await expect(page.getByText('Al Noor Hardware & Tools')).toBeVisible();
  });

  test('should open edit modal and update supplier details successfully', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();

    // Click Edit on Al Noor
    const row = page.locator('tr').filter({ hasText: 'Al Noor Hardware & Tools' });
    await row.getByRole('button', { name: 'Edit' }).click();

    const modal = page.locator('.modal-card');
    await expect(modal).toBeVisible();
    await expect(modal.getByRole('heading', { name: 'Edit Supplier' })).toBeVisible();

    // Modify notes
    const notesInput = modal.locator('textarea');
    await notesInput.fill('Updated special discounted terms: 45 days credit');

    // Submit
    await modal.getByRole('button', { name: 'Update Supplier' }).click();
    await expect(modal).not.toBeVisible();
  });

  test('should close modal when clicking X or Cancel buttons', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();

    // Open and close with X
    await page.getByRole('button', { name: 'Add New Supplier' }).click();
    await expect(page.locator('.modal-card')).toBeVisible();
    await page.locator('.modal-header button.btn-icon').click();
    await expect(page.locator('.modal-card')).not.toBeVisible();

    // Open and close with Cancel
    await page.getByRole('button', { name: 'Add New Supplier' }).click();
    await expect(page.locator('.modal-card')).toBeVisible();
    await page.locator('.modal-footer').getByRole('button', { name: 'Cancel' }).click();
    await expect(page.locator('.modal-card')).not.toBeVisible();
  });

  test('should validate required fields before submitting supplier form', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();

    await page.getByRole('button', { name: 'Add New Supplier' }).click();
    const modal = page.locator('.modal-card');

    // Attempt submit with empty name
    await modal.locator('input[placeholder*="Al Noor Hardware"]').fill('');
    await modal.locator('input[placeholder*="+97150"]').fill('');
    await modal.getByRole('button', { name: 'Create Supplier' }).click();

    // Native validation triggers or error alert shown
    await expect(modal).toBeVisible();
  });

  test('should create and select custom category inline', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();

    await page.getByRole('button', { name: 'Add New Supplier' }).click();
    const modal = page.locator('.modal-card');

    // Click + Custom Category
    await modal.getByRole('button', { name: '+ Custom Category' }).click();
    const customInput = modal.locator('input[placeholder="Type custom category name..."]');
    await expect(customInput).toBeVisible();

    // Type custom category and press Enter
    await customInput.fill('Industrial Safety');
    await customInput.press('Enter');

    // Verify chip added and selected
    await expect(modal.locator('.category-toggle-chip.selected').filter({ hasText: 'Industrial Safety' })).toBeVisible();
  });

  test('should cancel inline custom category creation', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();

    await page.getByRole('button', { name: 'Add New Supplier' }).click();
    const modal = page.locator('.modal-card');

    await modal.getByRole('button', { name: '+ Custom Category' }).click();
    await modal.getByRole('button', { name: 'Cancel' }).first().click();
    await expect(modal.getByRole('button', { name: '+ Custom Category' })).toBeVisible();
  });

  test('should handle backend submission failure gracefully in modal', async ({ page }) => {
    page.allowConsoleError(/Duplicate phone number|Failed to create supplier|Failed to load resource|Error saving supplier/);
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');
    await page.getByRole('button', { name: 'Suppliers' }).click();

    // Force supplier POST to 500
    await page.route('**/rest/v1/suppliers*', async (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ message: 'Duplicate phone number constraint violation' }),
        });
      }
      return route.fallback();
    });

    await page.getByRole('button', { name: 'Add New Supplier' }).click();
    const modal = page.locator('.modal-card');
    await modal.locator('input[placeholder*="Al Noor Hardware"]').fill('Duplicate Test Supp');
    await modal.locator('input[placeholder*="+97150"]').fill('+971501112233');
    await modal.getByRole('button', { name: 'Create Supplier' }).click();

    // Verify error is rendered in modal without crash
    await expect(modal.locator('.error-alert')).toBeVisible();
    await expect(modal).toBeVisible();
  });
});
