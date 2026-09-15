import { test, expect, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Team Settings & Workspace Invitations', () => {
  test('should select invite role and generate shareable link', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/team');
    await expect(page.getByRole('heading', { name: 'Team & invites' })).toBeVisible();

    const roleSelect = page.locator('#invite-role');
    await expect(roleSelect).toBeVisible();
    await roleSelect.selectOption('admin');

    // Click generate
    await page.getByRole('button', { name: /Create shareable invite link/i }).click();

    // Verify success banner and shareable link box
    await expect(page.locator('.msg.success')).toBeVisible();
    await expect(page.getByText('Shareable Invite Link:')).toBeVisible();

    // Copy link button
    const copyBtn = page.getByRole('button', { name: /Copy link/i });
    await expect(copyBtn).toBeVisible();
    await copyBtn.click();
    await expect(page.getByText('Copied')).toBeVisible();
  });

  test('should render active team members list', async ({ page }) => {
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/team');

    const membersCard = page.locator('.card').filter({ hasText: 'Active team members' });
    await expect(membersCard).toBeVisible();
    await expect(membersCard.getByText('admin@amafah.com')).toBeVisible();
    await expect(membersCard.getByText('purchaser@amafah.com')).toBeVisible();
  });

  test('should render pending invite tokens with copy buttons', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/team');

    const pendingCard = page.locator('.card').filter({ hasText: 'Active invitation links' });
    await expect(pendingCard).toBeVisible();
    await expect(pendingCard.locator('.invite-link-row')).toHaveCount(1);

    // Click copy on token
    const copyBtn = pendingCard.getByRole('button', { name: 'Copy' }).first();
    await expect(copyBtn).toBeVisible();
    await copyBtn.click();
  });

  test('should restrict member role from generating invite links', async ({ page }) => {
    await setupAuthenticatedSession(page, 'member');
    await page.goto('/team');

    await expect(page.locator('.error-alert')).toContainText('You need admin access to generate team invitations');
    await expect(page.getByRole('button', { name: /Create shareable invite link/i })).toBeDisabled();
  });
});
