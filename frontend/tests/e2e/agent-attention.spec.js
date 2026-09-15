import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Agent Attention & Human Review', () => {
  test('should input operator guidance into instruction textarea', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: /Agent Attention/ }).click();
    await expect(page.getByRole('heading', { name: 'Agent Attention & Human Review' })).toBeVisible();

    const flagCard = page.locator('.flagged-item-card').first();
    await expect(flagCard).toBeVisible();
    await expect(flagCard.getByText('Supplier requested 50% advance payment')).toBeVisible();

    const textarea = flagCard.locator('textarea');
    await textarea.fill('We agree to 30% advance with balance upon Bill of Lading copy');
    await expect(textarea).toHaveValue('We agree to 30% advance with balance upon Bill of Lading copy');
  });

  test('should toggle send to supplier checkbox', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: /Agent Attention/ }).click();

    const flagCard = page.locator('.flagged-item-card').first();
    const checkbox = flagCard.locator('input[type="checkbox"]');
    await expect(checkbox).toBeChecked();

    // Toggle off
    await checkbox.uncheck();
    await expect(checkbox).not.toBeChecked();

    // Toggle on
    await checkbox.check();
    await expect(checkbox).toBeChecked();
  });

  test('should resolve flag directly with Mark Resolved button', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: /Agent Attention/ }).click();

    const resolveBtn = page.getByRole('button', { name: 'Mark Resolved' });
    await expect(resolveBtn).toBeVisible();
    await resolveBtn.click();

    // Once resolved, list shows empty state or updated history
    await expect(page.getByText('All Agent Escalations Resolved')).toBeVisible();
  });

  test('should send instruction and resolve flag with Send Instruction button', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: /Agent Attention/ }).click();

    const flagCard = page.locator('.flagged-item-card').first();
    const sendBtn = flagCard.getByRole('button', { name: /Send Instruction & Resolve/i });

    // Initially disabled when textarea empty
    await expect(sendBtn).toBeDisabled();

    // Fill instruction
    await flagCard.locator('textarea').fill('Offer 43 AED max');
    await expect(sendBtn).toBeEnabled();

    // Click submit
    await sendBtn.click();
    await expect(page.getByText('All Agent Escalations Resolved')).toBeVisible();
  });

  test('should render resolved escalation history list', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/');

    await page.getByRole('button', { name: /Agent Attention/ }).click();

    const historyCard = page.locator('.card').filter({ hasText: 'Resolved Escalation History' });
    await expect(historyCard).toBeVisible();
    await expect(historyCard.getByText('Emirates Electrical Supplies')).toBeVisible();
    await expect(historyCard.getByText('Supplier gave conflicting lead times')).toBeVisible();
  });
});
