import { test, expect, setupUnauthenticatedSession } from './fixtures/test-base.js';

test.describe('Authentication — SignUp', () => {
  test('should submit signup form and display verification message', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/signup');

    await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible();

    await page.locator('#signup-email').fill('newuser@amafah.com');
    await page.locator('#signup-password').fill('securepass123');
    await page.locator('#signup-confirm').fill('securepass123');

    // Intercept successful signup
    await page.route('**/auth/v1/signup*', async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'user-new', email: 'newuser@amafah.com' }),
      });
    });

    await page.getByRole('button', { name: 'Sign up' }).click();
    await expect(page.locator('.msg.success')).toBeVisible();
    await expect(page.locator('.msg.success')).toContainText('Check your email to verify your account');
  });

  test('should reject mismatched passwords', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/signup');

    await page.locator('#signup-email').fill('newuser@amafah.com');
    await page.locator('#signup-password').fill('pass123');
    await page.locator('#signup-confirm').fill('pass456');

    await page.getByRole('button', { name: 'Sign up' }).click();
    await expect(page.locator('.msg.error')).toBeVisible();
    await expect(page.locator('.msg.error')).toContainText('Passwords do not match');
  });

  test('should navigate to login page', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/signup');

    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL('/login');
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
  });

  test('should redirect to accept-invite when token is in query params', async ({ page }) => {
    await setupUnauthenticatedSession(page);
    await page.goto('/signup?token=test-invite-abc');
    await expect(page).toHaveURL(/\/accept-invite\?token=test-invite-abc/);
  });
});
