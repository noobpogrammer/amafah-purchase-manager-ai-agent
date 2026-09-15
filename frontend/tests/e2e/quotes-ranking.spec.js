import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Quotes Comparison & AI Recommendation Report', () => {
  test('should change selected RFQ via dropdown and load comparison data', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Quotes & AI Ranking' }).click();
    await expect(page.getByRole('heading', { name: 'Quotes Comparison & AI Recommendation Report' })).toBeVisible();

    // Select dropdown
    const selectDropdown = page.locator('.rfq-select-dropdown select');
    await expect(selectDropdown).toBeVisible();

    // Change to Heavy Duty Power Drill by value
    await selectDropdown.selectOption('rfq-closed-1');
    await expect(page.locator('.report-title')).toHaveText('Heavy Duty Power Drill 800W');
  });

  test('should render AI recommendation hero card with best offer details', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Quotes & AI Ranking' }).click();

    // Hero banner check
    const hero = page.locator('.ai-recommendation-hero');
    await expect(hero).toBeVisible();
    await expect(hero.getByText('AI Recommended Offer')).toBeVisible();
    await expect(hero.getByText('AI Recommends: Gulf Plumbing Solutions — Standard Mueller')).toBeVisible();
    await expect(hero.getByText('AED 44.5 | Delivery: Next day delivery')).toBeVisible();
  });

  test('should render quotes table with variant tags and best value badge', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Quotes & AI Ranking' }).click();

    const table = page.locator('.data-table');
    await expect(table).toBeVisible();
    await expect(table.locator('tbody tr')).toHaveCount(2);

    // Row 1 is Best Value
    const row1 = table.locator('tbody tr').first();
    await expect(row1.locator('.badge-best')).toContainText('Best Value');
    await expect(row1.getByText('Standard Mueller')).toBeVisible();
    await expect(row1.locator('.price-tag-large')).toHaveText('AED 44.5');

    // Row 2 is Generic Local
    const row2 = table.locator('tbody tr').nth(1);
    await expect(row2.locator('.badge-best')).not.toBeVisible();
    await expect(row2.getByText('Generic Local')).toBeVisible();
  });

  test('should run AI ranking recommendation and display hero banner', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Quotes & AI Ranking' }).click();

    const runBtn = page.getByRole('button', { name: 'Run AI Recommendation' });
    await expect(runBtn).toBeVisible();
    await runBtn.click();

    await expect(page.locator('.ai-recommendation-hero')).toBeVisible();
  });

  test('should render AI comparative breakdown ranking list', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: 'Quotes & AI Ranking' }).click();

    const breakdownList = page.locator('.ranking-breakdown-list');
    await expect(breakdownList).toBeVisible();
    await expect(breakdownList.locator('.ranking-item')).toHaveCount(2);
    await expect(breakdownList.locator('.rank-badge').first()).toHaveText('#1');
    await expect(breakdownList.locator('.rank-badge').nth(1)).toHaveText('#2');
  });
});
