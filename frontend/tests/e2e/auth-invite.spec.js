import { test, expect, setupUnauthenticatedSession } from './fixtures/test-base.js';

test.describe('Authentication — Accept Invitation Flow', () => {
  test('should display error when invitation token is missing or invalid', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/accept-invite');

    await expect(page.getByRole('heading', { name: 'Invalid Invitation' })).toBeVisible();
    await expect(page.locator('.msg.error')).toContainText('No invitation token found in this link');

    await page.getByRole('button', { name: 'Back to sign in' }).click();
    await expect(page).toHaveURL('/login');
  });

  test('should validate invitation token and render workspace details', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/accept-invite?token=test-invite-token-abc123');

    await expect(page.getByRole('heading', { name: 'Join Workspace' })).toBeVisible();
    await expect(page.getByText('You have been invited as a member')).toBeVisible();
    await expect(page.locator('#accept-email')).toBeVisible();
    await expect(page.locator('#accept-password')).toBeVisible();
  });

  test('should validate password length and confirmation match', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/accept-invite?token=test-invite-token-abc123');

    await page.locator('#accept-email').fill('invited@amafah.com');
    await page.locator('#accept-password').fill('123');
    await page.locator('#accept-confirm').fill('456');

    // Mismatched
    await page.getByRole('button', { name: /Accept invite/i }).click();
    await expect(page.locator('.msg.error')).toContainText('Passwords do not match');

    // Short length
    await page.locator('#accept-password').fill('12345');
    await page.locator('#accept-confirm').fill('12345');
    await page.getByRole('button', { name: /Accept invite/i }).click();
    await expect(page.locator('.msg.error')).toContainText('Password must be at least 6 characters');
  });

  test('should accept invitation and create account', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/accept-invite?token=test-invite-token-abc123');

    await page.locator('#accept-email').fill('invited@amafah.com');
    await page.locator('#accept-password').fill('securepassword123');
    await page.locator('#accept-confirm').fill('securepassword123');

    // Intercept signup & claim
    await page.route('**/auth/v1/signup*', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'user-invited-1', email: 'invited@amafah.com' }),
      });
    });

    await page.getByRole('button', { name: /Accept invite/i }).click();

    // Verify success state and Go to sign in button
    await expect(page.locator('.msg.success')).toBeVisible();
    await expect(page.locator('.msg.success')).toContainText('Account created');
    await expect(page.getByRole('button', { name: 'Go to sign in' })).toBeVisible();

    await page.getByRole('button', { name: 'Go to sign in' }).click();
    await expect(page).toHaveURL('/login');
  });
});
