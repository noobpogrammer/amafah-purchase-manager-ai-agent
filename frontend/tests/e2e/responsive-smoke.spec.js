import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

const viewports = [
  { name: 'Desktop (1440x900)', width: 1440, height: 900 },
  { name: 'Laptop (1280x720)', width: 1280, height: 720 },
  { name: 'Tablet (768x1024)', width: 768, height: 1024 },
  { name: 'Mobile (390x844)', width: 390, height: 844 },
];

test.describe('Responsive Viewports Smoke Checks', () => {
  for (const vp of viewports) {
    test(`should render procurement dashboard and navigation on ${vp.name}`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await setupAuthenticatedSession(page, 'admin');
      await page.goto('/');

      // Header & brand logo visible
      await expect(page.locator('.app-header')).toBeVisible();
      await expect(page.locator('.brand-logo')).toBeVisible();

      // Dashboard content rendered
      await expect(page.getByRole('heading', { name: 'Procurement Command Center' })).toBeVisible();

      // Navigate to Suppliers view
      await page.getByRole('button', { name: 'Suppliers' }).click();
      await expect(page.getByRole('heading', { name: 'Supplier Directory' })).toBeVisible();

      // Open and close Add Supplier modal
      await page.getByRole('button', { name: 'Add New Supplier' }).click();
      await expect(page.locator('.modal-card')).toBeVisible();
      await page.locator('.modal-card').getByRole('button', { name: 'Cancel' }).click();
      await expect(page.locator('.modal-card')).not.toBeVisible();
    });
  }
});
