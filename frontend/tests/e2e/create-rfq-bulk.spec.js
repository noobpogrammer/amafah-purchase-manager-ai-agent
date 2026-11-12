import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Create RFQ — Bulk CSV Mode', () => {
  test('should reject non-CSV file upload with error message', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.locator('.sidebar-create').click();
    await page.getByRole('button', { name: 'Upload Material Requisition' }).click();

    // Upload invalid .pdf file
    await page.setInputFiles('#bulk-rfq-input', {
      name: 'requisition.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('dummy-pdf-content'),
    });

    await expect(page.locator('.error-alert')).toContainText('Please upload a CSV file (.csv)');
  });

  test('should parse valid CSV file and display editable rows', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.locator('.sidebar-create').click();
    await page.getByRole('button', { name: 'Upload Material Requisition' }).click();

    const sampleCsv = `SL #,Description,Qty,Last Cost
1,Copper Water Pipe 1/2 Inch 20ft,50,45.00
2,Brass Ball Valve 1/2 Inch,25,18.50`;

    await page.setInputFiles('#bulk-rfq-input', {
      name: 'material_requisition.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(sampleCsv),
    });

    // Check table rendered with 2 rows
    const table = page.locator('.bulk-compact-list');
    await expect(table).toBeVisible();
    await expect(table.locator('.bulk-item')).toHaveCount(2);

    // Verify row contents
    await expect(table.locator('.bulk-product-title').first()).toHaveText(/Copper Water Pipe/);
    await expect(table.locator('.bulk-product-title').nth(1)).toHaveText(/Brass Ball Valve/);
  });

  test('should toggle row selection and block submission if none selected', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.locator('.sidebar-create').click();
    await page.getByRole('button', { name: 'Upload Material Requisition' }).click();

    const sampleCsv = `SL #,Description,Qty,Last Cost\n1,PVC Conduit Pipe 25mm,100,12.00`;

    await page.setInputFiles('#bulk-rfq-input', {
      name: 'req.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(sampleCsv),
    });

    const checkbox = page.locator('.bulk-item input[type="checkbox"]').first();
    await expect(checkbox).toBeChecked();

    // Uncheck row
    await checkbox.uncheck();
    await expect(checkbox).not.toBeChecked();

    // Try submit
    await expect(page.getByRole('button', { name: 'Create 0 requests' })).toBeDisabled();

    // Recheck row
    await checkbox.check();
    await expect(checkbox).toBeChecked();
  });

  test('should allow editing row fields before bulk submit', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.locator('.sidebar-create').click();
    await page.getByRole('button', { name: 'Upload Material Requisition' }).click();

    const sampleCsv = `SL #,Description,Qty,Last Cost\n1,Galvanized Elbow 90 Deg,40,8.50`;

    await page.setInputFiles('#bulk-rfq-input', {
      name: 'req.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(sampleCsv),
    });

    const table = page.locator('.bulk-compact-list');
    await expect(table).toBeVisible();

    // Edit quantity
    const qtyInput = table.getByLabel('Quantity',{exact:true}).first();
    await qtyInput.fill('75');

    // Edit category dropdown on row
    const rowCategorySelect = table.getByLabel('Category',{exact:true}).first();
    await rowCategorySelect.selectOption('Plumbing');

    // Submit bulk upload
    await page.getByRole('button', { name: 'Create 1 request' }).click();

    // Verify summary card
    const summaryCard = page.locator('.result-card');
    await expect(summaryCard).toBeVisible();
    await expect(summaryCard.getByText('Bulk RFQs Created')).toBeVisible();
    await expect(summaryCard.getByText('Created 1 RFQ')).toBeVisible();
  });
});
