import { test, expect, setupUnauthenticatedSession, setupAuthenticatedSession } from './fixtures/test-base.js';

test.describe('Authentication — Password Reset Flow', () => {
  test('should request password reset email', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/forgot-password');

    await expect(page.getByRole('heading', { name: 'Reset your password' })).toBeVisible();

    await page.locator('#forgot-email').fill('user@amafah.com');

    // Intercept reset
    await page.route('**/auth/v1/recover*', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'Recovery email sent' }),
      });
    });

    await page.getByRole('button', { name: 'Send reset email' }).click();
    await expect(page.locator('.msg.success')).toBeVisible();
    await expect(page.locator('.msg.success')).toContainText('Check your email for reset instructions');
  });

  test('should navigate back to sign in from forgot password', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/forgot-password');

    await page.getByRole('button', { name: 'Back to sign in' }).click();
    await expect(page).toHaveURL('/login');
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
  });

  test('should update password successfully', async ({ page }) => {
    // User arrives on reset-password with recovery session
    await setupAuthenticatedSession(page, 'admin');
    await page.goto('/reset-password');

    await expect(page.getByRole('heading', { name: 'Choose a new password' })).toBeVisible();

    await page.locator('#reset-password').fill('newSecurePassword123');

    // Intercept user update
    await page.route('**/auth/v1/user*', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'user-admin-1', email: 'admin@amafah.com' }),
      });
    });

    await page.getByRole('button', { name: 'Set new password' }).click();
    await expect(page).toHaveURL('/');
  });
});
